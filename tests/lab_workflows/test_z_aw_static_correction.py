"""用假仪器验证静态闭环状态转换、停止和实测结果导出。"""
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock
import hashlib

import numpy as np
import pytest
import yaml

from lab_workflows.common import WorkflowCancelled
from lab_workflows.current_feedback import load_corrected_control_waveform
from lab_workflows.steps.run_directory import RunDirectory
from lab_workflows.experiment_modules.z_aw_closed_loop_waveform_correction import workflow as w
from lab_workflows.experiment_modules.z_aw_closed_loop_waveform_correction import capture as c
from lab_workflows.experiment_modules.z_aw_closed_loop_waveform_correction.analysis import analyze
from lab_workflows.experiment_modules.z_aw_closed_loop_waveform_correction.models import ZAWClosedLoopWaveformCorrectionParams as Params
from lab_workflows.experiment_modules.z_aw_closed_loop_waveform_correction.static_feedback import (
    PreparedFeedback, StaticFit, applied_from_voltage, periodic_lowpass, rms,
)


def fake_run(monkeypatch, tmp_path, errors):
    run_path = tmp_path / "data" / w.DATA_TYPE / "test_run"
    raw, results = run_path / "raw", run_path / "results"
    raw.mkdir(parents=True)
    results.mkdir()
    run_dir = RunDirectory(run_path, raw, results, run_path / "experiment_config.yaml")
    source = tmp_path / "source.yaml"
    source.write_text("success: true\n", encoding="utf-8")
    n = 128
    time = np.arange(n) / 12800
    target = 0.001 * np.sin(2 * np.pi * np.arange(n) / n) + 0.0002
    fit = StaticFit(0.005, 0.00003, 1, np.array([0., 1.]), np.array([0.00003, 0.00503]))
    initial = applied_from_voltage((target - fit.intercept_a) / fit.gain_a_per_v, amplitude_vpp=2, offset_v=0)
    theory = SimpleNamespace(time_s=time, omega_ctrl_hz=target * 1e7, repeat_frequency_hz=100.,
                             waveform_path=source, parameter_path=source,
                             waveform_sha256="t", parameter_sha256="p")
    calibration = SimpleNamespace(sense_resistor_ohm=270., run_name="static", analysis_path=source,
                                   analysis_sha256="c", slope_hz_per_a=1e7)
    prepared = PreparedFeedback(theory, calibration, fit, target, rms(target), initial)
    params = Params(max_iterations=len(errors), iteration_repeats=1, error_cutoff_hz=400,
                    z_aw_output_vpp=2, output_settle_s=0)
    run_dir.update_config(parameters=params.to_external())
    monkeypatch.setattr(w, "find_project_root", lambda: tmp_path)
    monkeypatch.setattr(w, "load_mapping", lambda root: {})
    monkeypatch.setattr(w, "prepare_feedback", lambda *args: prepared)
    monkeypatch.setattr(w, "create_run_directory", lambda *args, **kw: run_dir)
    device, scope, acquirer = Mock(), Mock(), Mock()
    scope.get_sampling_rate.return_value = 12800.
    monkeypatch.setattr(w, "_connect_devices", lambda *args: ({"z_control": device, "scope": scope, "acquirer": acquirer}, {"z_field": 1, "trigger": 2}))
    monkeypatch.setattr(w, "synchronize_connected_clocks", lambda *args, **kw: {})
    monkeypatch.setattr(w, "_scope_config", lambda *args, **kw: (Mock(), {}))
    monkeypatch.setattr(w, "configure_optimal_control_trigger", Mock())
    monkeypatch.setattr(w, "_sleep_cancellable", lambda seconds: None)
    commands = []
    def upload(*args):
        commands.append(args[-1].voltage_v.copy())
        return {"command_voltage_reference": "50_ohm"}
    monkeypatch.setattr(w, "configure_verified_output", upload)
    queue = iter(errors)
    def capture(*args):
        error = next(queue)
        if isinstance(error, Exception):
            raise error
        current = np.tile(target * (1 - error), 3)
        frame = {"time_s": np.arange(-n, 2 * n) / 12800,
                 "measured_voltage_v": current * 270,
                 "trigger_time_s": np.array([-0.00001, 0.00001]),
                 "trigger_voltage_v": np.array([5., 0.])}
        np.savez(args[-1], **frame)
        return frame
    monkeypatch.setattr(w, "capture_with_headroom", capture)
    shutdown = Mock(return_value=SimpleNamespace(errors=(), to_dict=lambda: {"errors": []}))
    monkeypatch.setattr(w, "safe_shutdown", shutdown)
    return params, prepared, run_dir, commands, shutdown


def test_worsening_continues_from_latest_feedback_and_exports_measured_best(monkeypatch, tmp_path):
    params, prepared, run_dir, commands, shutdown = fake_run(monkeypatch, tmp_path, [0.2, 0.4, 0.1])
    w.run(params)
    assert len(commands) == 3
    np.testing.assert_allclose(commands[2] - commands[1], params.iteration_damping *
                               periodic_lowpass(0.4 * prepared.target_current_a, 1 / 12800, 400) / 0.005, atol=1e-14)
    with np.load(run_dir.results / "corrected_control_waveform.npz") as data:
        np.testing.assert_array_equal(data["voltage_v"], commands[2])
        assert int(data["best_iteration"]) == 2
        assert "frequency_response_run" not in data.files
    loaded = load_corrected_control_waveform(tmp_path, "test_run")
    np.testing.assert_array_equal(loaded.voltage_v, commands[2])
    assert loaded.frequency_response_run == ""
    assert yaml.safe_load(run_dir.config_path.read_text(encoding="utf-8"))['target_reached'] is False
    assert not (run_dir.raw / "iteration_002" / "update.npz").exists()
    shutdown.assert_called_once()


def test_success_requires_consecutive_passes_and_resets_on_noise(monkeypatch, tmp_path):
    params, _, run_dir, commands, _ = fake_run(monkeypatch, tmp_path, [0.01, 0.01, 0.1, 0.01, 0.01, 0.01, 0.2])
    w.run(params)
    assert len(commands) == 6
    assert yaml.safe_load(run_dir.config_path.read_text(encoding="utf-8"))['target_reached'] is True


@pytest.mark.parametrize("failure,status", [(RuntimeError("采集失败"), "failed"), (WorkflowCancelled("取消"), "cancelled")])
def test_failure_saves_completed_best_and_shuts_down(monkeypatch, tmp_path, failure, status):
    params, _, run_dir, commands, shutdown = fake_run(monkeypatch, tmp_path, [0.2, failure])
    with pytest.raises(type(failure)):
        w.run(params)
    config = yaml.safe_load(run_dir.config_path.read_text(encoding="utf-8"))
    assert config["completion_status"] == status
    assert config["best_iteration"] == 0
    with np.load(run_dir.results / "corrected_control_waveform.npz") as data:
        np.testing.assert_array_equal(data["voltage_v"], commands[0])
    shutdown.assert_called_once()


def test_no_valid_round_does_not_export_command(monkeypatch, tmp_path):
    params, _, run_dir, _, shutdown = fake_run(monkeypatch, tmp_path, [RuntimeError("无帧")])
    with pytest.raises(RuntimeError):
        w.run(params)
    assert not (run_dir.results / "corrected_control_waveform.npz").exists()
    assert analyze(run_dir.root)["success"] is False
    shutdown.assert_called_once()


def test_voltage_violation_saves_candidate_without_upload(monkeypatch, tmp_path):
    params, _, run_dir, commands, _ = fake_run(monkeypatch, tmp_path, [100., 0.2])
    w.run(params)
    assert len(commands) == 1
    assert (run_dir.raw / "iteration_000" / "update.npz").exists()
    assert yaml.safe_load(run_dir.config_path.read_text(encoding="utf-8"))["stop_reason"] == "下一轮命令越界"


def test_reanalysis_does_not_shift_or_filter_saved_waveform(monkeypatch, tmp_path):
    params, _, run_dir, _, _ = fake_run(monkeypatch, tmp_path, [0.2, 0.4])
    w.run(params)
    protected = [*run_dir.raw.rglob("*.npz"), run_dir.results / "corrected_control_waveform.npz"]
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in protected}
    report = analyze(run_dir.root)
    assert report["best_iteration"] == 0
    assert report["best_relative_rms_error"] == pytest.approx(0.2)
    assert report["independent_validation"] is False
    assert before == {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in protected}


def test_static_loop_converges_with_gain_error_and_global_delay():
    from lab_workflows.experiment_modules.z_aw_closed_loop_waveform_correction.static_feedback import align_cycle
    phase = 2 * np.pi * np.arange(512) / 512
    target = 0.002 * np.sin(phase) + 0.0004 * np.cos(3 * phase) + 0.0001
    g, b = 0.005, 0.00003
    command = (target - b) / g
    for _ in range(100):
        measured = np.roll(0.75 * g * command + b, 23)
        aligned, _ = align_cycle(measured, target)
        error = target - aligned
        command += 0.1 * periodic_lowpass(error, 1 / 51200, 400) / g
    assert rms(error) / rms(target) < 0.0002


def test_scope_retries_with_more_headroom_and_saves_every_attempt(monkeypatch, tmp_path):
    params = Params(error_cutoff_hz=1000)
    time = np.arange(100) / 10000
    frame = {"time_s": time, "measured_voltage_v": np.linspace(-0.9, 0.9, 100),
             "trigger_time_s": time, "trigger_voltage_v": np.where(time < 0.005, 5., 0.),
             "scale_used_v_div": 0.25, "offset_used_v": 0.}
    frames = iter([frame, {**frame, "scale_used_v_div": 0.5}])
    monkeypatch.setattr(c, "capture_frame", lambda *args: next(frames))
    scope = Mock()
    result = c.capture_with_headroom({"scope": scope}, Mock(), params, tmp_path / "frame.npz")
    scope.set_channel_scale.assert_called_once_with(3, 0.5)
    assert result["scale_used_v_div"] == 0.5
    assert len(list(tmp_path.glob("*attempt*.npz"))) == 2


def test_invalid_frame_saved_before_rejection(monkeypatch, tmp_path):
    time = np.arange(100) / 10000
    frame = {"time_s": time, "measured_voltage_v": np.full(100, np.nan),
             "trigger_time_s": time, "trigger_voltage_v": np.zeros(100),
             "scale_used_v_div": 0.5, "offset_used_v": 0.}
    monkeypatch.setattr(c, "capture_frame", lambda *args: frame)
    with pytest.raises(ValueError, match="非有限"):
        c.capture_with_headroom({"scope": Mock()}, Mock(), Params(), tmp_path / "frame.npz")
    assert (tmp_path / "frame_attempt_00.npz").exists()


def test_legacy_analysis_keeps_historical_metric_and_waveform(monkeypatch, tmp_path):
    params, prepared, run_dir, _, _ = fake_run(monkeypatch, tmp_path, [0.2])
    w.run(params)
    config = {"parameters": {"TARGET_SHAPE_NRMSE": 0.03, "Z_AW_OUTPUT_VPP": 2},
              "best_iteration": 0, "current_frequency_response": {"learning_mode": "full_time_domain"},
              "final_command_measured": True}
    run_dir.config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    summary = [{"iteration": 0, "shape_nrmse": 0.2, "holdout": {"shape_nrmse": 0.2}}]
    (run_dir.results / "iteration_summary.yaml").write_text(yaml.safe_dump(summary), encoding="utf-8")
    waveform = run_dir.results / "corrected_control_waveform.npz"
    before = waveform.read_bytes()
    report = analyze(run_dir.root)
    assert report["analysis_version"] == 3
    assert report["target_reached"] is False
    assert report["best_iteration"] == 0
    assert waveform.read_bytes() == before


def test_voltage_and_nyquist_validation_happen_before_alignment():
    time = np.arange(100) / 10000
    frame = {"time_s": time, "measured_voltage_v": np.zeros(100),
             "trigger_time_s": time, "trigger_voltage_v": np.zeros(100),
             "scale_used_v_div": 0.5, "offset_used_v": 0.}
    with pytest.raises(ValueError, match="奈奎斯特"):
        c.validate_frame(frame, Params(error_cutoff_hz=6000))
    frame["measured_voltage_v"][-1] = 2.01
    with pytest.raises(ValueError, match="量程"):
        c.validate_frame(frame, Params(error_cutoff_hz=1000))
