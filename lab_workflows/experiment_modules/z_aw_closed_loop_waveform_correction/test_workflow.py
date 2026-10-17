"""真实闭环业务配假设备；不连接仪器，所有运行均在临时根目录。"""
from contextlib import contextmanager, ExitStack, redirect_stdout
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
import pytest
import yaml
from ...common import WorkflowCancelled
from ..z_aw_waveform_scope_check.test_workflow import ROOT, copy_config as copy_scope_config, fake_hardware as scope_hardware
from . import acquisition, capture, models, preparation, response_filter, workflow
from .stages import ACQUISITION, definition

ACQUISITION_CASES = {
    "z-aw-closed-loop-waveform-correction": {
        "mode": "single",
        "cases": {
            "order": ["test_full_and_staged_methods_use_real_capture_and_keep_outputs_safe"],
            "slow_disk": ["test_slow_save_cancel_keeps_partial_frames_and_restores_before_summary"],
            "cancel": ["test_slow_save_cancel_keeps_partial_frames_and_restores_before_summary"],
            "acquire_failure": ["test_read_and_shutdown_failure_preserve_original_exception"],
            "restore_failure": ["test_read_and_shutdown_failure_preserve_original_exception"],
            "save_failure": ["test_summary_save_failure_is_safe_and_failed"],
            "partial_save": ["test_read_and_shutdown_failure_preserve_original_exception"],
            "entrypoints": ["test_full_and_staged_methods_use_real_capture_and_keep_outputs_safe"],
        },
        "sources": ["lab_workflows/experiment_modules/z_aw_closed_loop_waveform_correction/workflow.py",
                    "lab_workflows/experiment_modules/z_aw_closed_loop_waveform_correction/preparation.py",
                    "lab_workflows/experiment_modules/z_aw_closed_loop_waveform_correction/harmonic_loop.py",
                    "lab_workflows/experiment_modules/z_aw_closed_loop_waveform_correction/export.py"],
    },
}


def copy_config(root):
    copy_scope_config(root)
    directory = root / "data/Mx_Z_Current_Coupling_Calibration/fake_current/results"
    directory.mkdir(parents=True)
    (directory / "analysis.yaml").write_text(yaml.safe_dump(dict(
        experiment_id="mx-z-current-coupling-calibration", success=True,
        K_Z_Hz_per_A=100000, K_Z_Hz_per_Vsense=10000, f_0_at_zero_current_hz=0,
        sense_resistor_ohm=10, linearity={"r_squared": 1},
        curves=[{"z_bias_v": value, "current_a": .01 * value} for value in (-1., 0., 1.)])), encoding="utf-8")
    directory = root / "data/Z_Coil_Current_Frequency_Response/fake_response/results"
    directory.mkdir(parents=True)
    np.savez(directory / "frequency_response.npz", format_version=4,
        output_protocol="coherent_swept_sine_current_response", command_voltage_reference="50_ohm",
        phase_reference="measured_drive_voltage", frequency_hz=[500., 1000., 2000., 4000.],
        transfer_real_a_per_v=np.full(4, .01), transfer_imag_a_per_v=np.zeros(4),
        reliable=np.ones(4, dtype=bool), phase_reliable=np.ones(4, dtype=bool),
        frame_count=np.full(4, 3), valid_frame_count=np.full(4, 3),
        valid_frame_count_rule="exact_frequency_v1", sense_resistor_ohm=10.)


def scan_params(method="time_domain"):
    return models.ZAWClosedLoopWaveformCorrectionParams(control_version="v1", run_tag="fake",
        correction_method=method, current_coupling_calibration_source_run="fake_current",
        current_frequency_response_source_run="fake_response", error_cutoff_hz=4000.,
        max_iterations=3, required_passes=1, target_relative_rms=.03,
        iteration_repeats=3, holdout_repeats=3, final_validation_repeats=3,
        iteration_damping=1., output_settle_s=0.)


@contextmanager
def fake_hardware(root):
    with scope_hardware(root) as hw, ExitStack() as stack:
        for module in (models,):
            stack.enter_context(patch.object(module, "find_project_root", return_value=root))
        for module in (models, response_filter):
            stack.enter_context(patch.object(module, "resolve_control_results_root", return_value=root / "control"))
        stack.enter_context(patch.object(acquisition, "_sleep_cancellable"))
        hw.outputs, hw.events, hw.command = {}, [], np.zeros(64)
        hw.gain, hw.read_count, hw.read_failure, hw.shutdown_failure = .8, 0, False, False

        def output(value, *, channel):
            hw.events.append(("output", channel, value))
            if hw.shutdown_failure and channel == 1 and not value:
                raise OSError("restore broke")
            hw.outputs[channel] = value

        def upload(values, **kwargs):
            hw.command = np.asarray(values) * kwargs["amplitude"] / 2 + kwargs["offset"]
            hw.events.append(("upload",))

        def read_channel(_device, channel, *_args, **_kwargs):
            if channel == 3:
                hw.read_count += 1
                if hw.read_failure and hw.read_count > 1:
                    hw.shutdown_failure = True
                    raise OSError("read broke")
            t = np.arange(-96, 97) / hw.state["rate"]
            phase = t + .5 / hw.state["rate"]
            voltage = hw.gain * .1 * np.interp(phase, np.arange(64) / 64000,
                hw.command, period=.001) if channel == 3 else np.where(t < 0, 5., 0.)
            hw.events.append(("read", channel))
            return SimpleNamespace(time=t, voltage=voltage, preamble_dict={"points": len(t)})

        hw.generator.set_output.side_effect = output
        hw.generator.setup_arbitrary.side_effect = upload
        hw.generator.get_amplitude.return_value = scan_params().z_aw_output_vpp
        hw.generator.get_offset.return_value = 0.
        hw.generator.get_frequency.return_value = 1000.
        hw.generator.get_voltage_unit.return_value = "VPP"
        hw.generator.get_output_load.return_value = 50.
        hw.acquire.side_effect = read_channel
        yield hw


@pytest.fixture
def rig(tmp_path):
    copy_config(tmp_path)
    with fake_hardware(tmp_path) as hw:
        yield tmp_path, hw


@pytest.mark.parametrize("method", ["time_domain", "response_filtered_feedback", "harmonic_jacobian"])
def test_full_and_staged_methods_use_real_capture_and_keep_outputs_safe(rig, method):
    root, hw = rig
    params = scan_params(method)
    full = workflow.run(params, project_root=root)
    ctx = workflow.Workflow(params, project_root=root)
    try:
        ctx.execute(["A01", "A02", "A03", "A04", "A05"])
        assert ctx.states["A05"]["status"] == "completed", ctx.snapshot()
        first, config_bytes = ctx.run_dir, (ctx.run_dir / "experiment_config.yaml").read_bytes()
        with np.load(full / "results/corrected_control_waveform.npz") as a, np.load(
                first / "results/corrected_control_waveform.npz") as b:
            for key in ("time_s", "target_current_a", "measured_current_a", "voltage_v"):
                np.testing.assert_allclose(a[key], b[key], atol=1e-15, rtol=1e-12)
            assert int(a["format_version"]) == (4 if method == "harmonic_jacobian" else 3)
            assert float(a["relative_rms_error"]) <= params.target_relative_rms
            if method == "harmonic_jacobian":
                assert bool(a["final_reload_validated"])
        assert hw.outputs[1] is False and hw.outputs[2] is True
        assert (first / "raw/iteration_summary.yaml").is_file()
        ctx.execute(["A05"])
        assert ctx.run_dir != first
        assert (first / "experiment_config.yaml").read_bytes() == config_bytes
        ctx.update_parameters({"CONTROL_SCALE": .9})
    finally:
        ctx.finish()
    assert yaml.safe_load((first / "experiment_config.yaml").read_text(encoding="utf-8"))["parameters"] == params.to_external()
    assert hw.state["status"] == "STOP"


def test_missing_prerequisites_no_hardware_and_parameter_impacts_match(rig):
    root, hw = rig
    ctx = workflow.Workflow(scan_params(), project_root=root)
    with pytest.raises(ValueError, match="缺少前置"):
        ctx.execute(["A05"])
    hw.generator.connect.assert_not_called()
    flow = definition()
    displayed = {key for stage in flow["acquisition"] for key in stage["display_parameters"]}
    assert displayed == set(ctx.params.to_external(include_hidden=False))
    for changes in ({"RUN_TAG": "next"}, {"SCOPE_HEADROOM_FACTOR": 2.}, {"CONTROL_SCALE": .9}):
        ctx.states = {stage.id: {"status": "completed", "error": ""} for stage in ACQUISITION}
        ctx.update_parameters(changes)
        expected = set().union(*(set(flow["parameter_impacts"]["acquisition"][key]) for key in changes))
        assert {key for key, value in ctx.states.items() if value["status"] == "stale"} == expected
    ctx.finish()


def test_slow_save_cancel_keeps_partial_frames_and_restores_before_summary(rig):
    root, hw = rig
    cancelled = False
    save, write = np.savez, Path.write_text

    def slow_save(path, *args, **kwargs):
        nonlocal cancelled
        result = save(path, *args, **kwargs)
        time.sleep(.001)
        if Path(path).name == "scope_capture_000.npz":
            cancelled = True
        return result

    def check():
        if cancelled:
            raise WorkflowCancelled("cancel after saved frame")

    def summary(path, *args, **kwargs):
        if path.name == "iteration_summary.yaml":
            assert hw.outputs[1] is False
            hw.events.append(("summary",))
        return write(path, *args, **kwargs)

    with patch.object(np, "savez", side_effect=slow_save), patch.object(capture, "check_cancelled", side_effect=check), patch.object(Path, "write_text", autospec=True, side_effect=summary):
        with pytest.raises(WorkflowCancelled, match="cancel after"):
            workflow.run(scan_params(), project_root=root)
    paths = list((root / "data" / workflow.DATA_TYPE).glob("*/raw/iteration_000/scope_capture_000.npz"))
    assert len(paths) == 1
    config = yaml.safe_load((paths[0].parents[2] / "experiment_config.yaml").read_text(encoding="utf-8"))
    assert config["completion_status"] == "cancelled" and hw.events[-1] != ("upload",)


def test_read_and_shutdown_failure_preserve_original_exception(rig):
    root, hw = rig
    hw.read_failure = True
    with pytest.raises(OSError, match="read broke"):
        workflow.run(scan_params(), project_root=root)
    base = root / "data" / workflow.DATA_TYPE
    run = next(base.iterdir())
    assert (run / "raw/iteration_000/scope_capture_000.npz").is_file()
    config = yaml.safe_load((run / "experiment_config.yaml").read_text(encoding="utf-8"))
    assert config["completion_status"] == "failed"
    assert "read broke" in config["failure_reason"] and "restore broke" in config["failure_reason"]


def test_summary_save_failure_is_safe_and_failed(rig):
    root, hw = rig
    write = Path.write_text

    def fail(path, *args, **kwargs):
        if path.name == "iteration_summary.yaml":
            assert hw.outputs[1] is False
            raise OSError("disk full")
        return write(path, *args, **kwargs)

    with patch.object(Path, "write_text", autospec=True, side_effect=fail):
        with pytest.raises(OSError, match="disk full"):
            workflow.run(replace(scan_params(), max_iterations=1), project_root=root)
    config = yaml.safe_load(next((root / "data" / workflow.DATA_TYPE).glob("*/experiment_config.yaml")).read_text(encoding="utf-8"))
    assert config["completion_status"] == "failed"
    assert hw.outputs[1] is False


def test_source_drift_scope_mode_and_clock_failure_stop_before_upload(rig):
    root, hw = rig
    ctx = workflow.Workflow(scan_params(), project_root=root)
    try:
        ctx.execute(["A01", "A02", "A03", "A04"])
        hw.state["source"] = "C1"
        with pytest.raises(ValueError, match="模式已改变"):
            ctx.execute(["A05"])
        ctx.execute(["A04"])
        path = ctx.prepared.theory.waveform_path
        path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        with pytest.raises(ValueError, match="来源已改变"):
            ctx.execute(["A05"])
        config = yaml.safe_load((ctx.run_dir / "experiment_config.yaml").read_text(encoding="utf-8"))
        assert config["completion_status"] == "failed" and "来源已改变" in config["failure_reason"]
        hw.generator.setup_arbitrary.assert_not_called()
    finally:
        ctx.finish()


def test_quality_failure_keeps_attempts_allows_explicit_retry_and_never_freezes_unvalidated_command(rig):
    root, hw = rig
    ctx = workflow.Workflow(scan_params("harmonic_jacobian"), project_root=root)
    try:
        hw.gain = .001
        ctx.execute(["A01", "A02", "A03", "A04", "A05"])
        assert ctx.states["A05"]["status"] == "quality_failed"
        failed = ctx.run_dir
        assert list((failed / "raw").rglob("scope_capture_*_attempt_00.npz"))
        assert not (failed / "results/corrected_control_waveform.npz").exists()
        hw.gain = .8
        ctx.execute(["A05"])
        assert ctx.states["A05"]["status"] == "completed"
        assert ctx.run_dir != failed
    finally:
        ctx.finish()


def test_clock_readback_and_partial_connection_failure_cleanup_without_upload(rig):
    root, hw = rig
    hw.generator.get_ref_clock_source.return_value = "INT"
    with pytest.raises(Exception):
        workflow.run(scan_params(), project_root=root)
    hw.generator.setup_arbitrary.assert_not_called()
    hw.generator.disconnect.assert_called_once()
    hw.generator.reset_mock()
    hw.generator.get_ref_clock_source.return_value = "EXT"
    hw.scope.connect.side_effect = OSError("scope connection broke")
    with pytest.raises(OSError, match="scope connection broke"):
        workflow.run(scan_params(), project_root=root)
    hw.generator.disconnect.assert_called_once()


def test_published_parameter_roundtrip_and_old_new_key_precedence():
    params = scan_params()
    saved = params.to_external()
    assert models.ZAWClosedLoopWaveformCorrectionParams.from_external(saved, schema_version=13).to_external() == saved
    old = saved | {"TIME_DOMAIN_CUTOFF_HZ": 2000., "ERROR_CUTOFF_HZ": 4000.}
    old.pop("CORRECTION_METHOD")
    migrated = models.ZAWClosedLoopWaveformCorrectionParams.from_external(old, schema_version=10)
    assert migrated.correction_method == "time_domain" and migrated.error_cutoff_hz == 4000.
    assert models.ZAWClosedLoopWaveformCorrectionParams.schema_version == 13


def main():
    root = Path(os.environ["Z_CLOSED_TEST_ROOT"])
    from .session import serve
    payload = json.loads(os.environ["LAB_TYPED_PARAMETERS"])
    identity = payload.pop("session_id")
    with fake_hardware(root), redirect_stdout(sys.__stdout__):
        serve(workflow.Workflow(models.ZAWClosedLoopWaveformCorrectionParams.from_external(payload),
            project_root=root, session_id=identity), sys.stdin)


if __name__ == "__main__":
    main()
