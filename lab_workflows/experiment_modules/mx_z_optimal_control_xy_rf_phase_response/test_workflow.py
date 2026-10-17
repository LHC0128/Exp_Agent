"""本项真实配置、RF 相位采集、科学拟合和落盘配假仪器。"""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import json
import os
import time
import numpy as np
import pytest
import yaml
from lockin_amplifier import DAQResult
from ...common import WorkflowCancelled
from ..mx_y_rf_sensitivity import acquisition as r_acquisition
from ..mx_z_optimal_control_rf_sensitivity import preparation as shared_preparation
from ..mx_z_optimal_control_rf_sensitivity.test_workflow import install_rig as install_control
from ..mx_z_optimal_control_xy_leakage_response.test_workflow import copy_config
from . import workflow, preparation, models, acquisition
from .models import MxZOptimalControlXYRFPhaseResponseParams
from .phase import dispersion_phase_response
from .scan import build_axes, iter_serpentine_grid
from .stages import ACQUISITION, FINISH_ID, definition

ACQUISITION_CASES = {
    "mx-z-optimal-control-xy-rf-phase-response": {"mode": "single", "cases": {
        "order": ["test_real_serpentine_paired_phases_and_slow_disk"],
        "slow_disk": ["test_real_serpentine_paired_phases_and_slow_disk"],
        "cancel": ["test_partial_data_and_original_failure"],
        "acquire_failure": ["test_partial_data_and_original_failure"],
        "restore_failure": ["test_restore_failure_salvages_payload"],
        "save_failure": ["test_save_failure_only_after_restore"],
        "partial_save": ["test_partial_data_and_original_failure", "test_original_error_survives_cleanup_and_config_failure"],
        "entrypoints": ["test_full_and_stepwise_same_science_rerun_and_draft_shutdown"],
    }, "sources": ["lab_workflows/experiment_modules/mx_z_optimal_control_xy_rf_phase_response/workflow.py",
        "lab_workflows/experiment_modules/mx_z_optimal_control_xy_rf_phase_response/acquisition.py",
        "lab_workflows/experiment_modules/mx_z_optimal_control_xy_rf_phase_response/excitation.py",
        "lab_workflows/experiment_modules/mx_z_optimal_control_xy_rf_phase_response/preparation.py",
        "lab_workflows/experiment_modules/mx_z_optimal_control_xy_rf_phase_response/phase.py",
        "lab_workflows/experiment_modules/mx_z_optimal_control_xy_rf_phase_response/models.py",
        "lab_workflows/experiment_modules/mx_z_optimal_control_xy_rf_phase_response/scan.py",
        "lab_workflows/experiment_modules/mx_z_optimal_control_xy_rf_phase_response/session.py",
        "lab_workflows/experiment_modules/mx_z_optimal_control_xy_rf_phase_response/stages.py"]},
}


def parameters(root):
    return replace(MxZOptimalControlXYRFPhaseResponseParams(), run_tag="fake_phase", corrected_control_source_run="frozen",
        control_results_root=str(root / "theory"), z_calibration_source_run="calibration", z_aw_output_vpp=.2,
        x_field_start_v=0., x_field_stop_v=0., x_field_points=1,
        y_field_start_v=0., y_field_stop_v=0., y_field_points=1,
        phase_scan_stop_deg=315., phase_scan_step_deg=45., response_duration_s=.016,
        system_settle_s=.001, temp_switch_on_lag_s=0., phase_fit_amplitude_sigma_min=0.)


def install_rig(root, monkeypatch, params=None):
    rig = install_control(root, monkeypatch, params or parameters(root))
    monkeypatch.setattr(workflow, "find_project_root", lambda: root)
    monkeypatch.setattr(models, "find_project_root", lambda: root)
    rig.coordinates = []
    def acquire(hf2, config, **kwargs):
        assert not rig.enabled
        assert config.signal_paths == ["sample.r"]
        rig.events.append("acquire")
        index = len(rig.captures)
        rig.captures.append(index)
        if index == rig.fault_index:
            raise rig.acquire_error or WorkflowCancelled("模拟取消")
        kwargs["cancellation_check"]()
        source = rig.mapping["rf_coil"]
        rf, channel = rig.generators[source["resource"]], source["channel"]
        phase = float(rf.phase[channel])
        x = rig.mapping["X_magnetic_field"]
        x_v = rig.generators[x["resource"]].dc[x["channel"]]
        rig.coordinates.append((x_v, phase))
        count = max(8, config.grid_cols)
        noise = 1e-6 * np.sin(np.arange(count) * .7)
        if rig.quality_bad:
            noise += np.linspace(-1, 1, count)
        mean = dispersion_phase_response(phase, .0002, .02, .007, .012, 30., rig.params.phase_cal_rf_amplitude_vpp)
        return [DAQResult(signal_name="sample.r", demod_index=kwargs["demod_idx"], values=np.full(count, mean) + noise,
            time=np.arange(count) / kwargs["actual_rate"])]
    monkeypatch.setattr(r_acquisition.daq, "acquire_data", acquire)
    return rig


@pytest.fixture
def rig(tmp_path, monkeypatch):
    copy_config(tmp_path)
    return install_rig(tmp_path, monkeypatch)


def through(last):
    ids = [stage.id for stage in ACQUISITION if stage.id != FINISH_ID]
    return ids[:ids.index(last) + 1]


def assert_safe(rig):
    assert rig.enabled
    for key in ("X_magnetic_field", "rf_coil"):
        source = rig.mapping[key]
        assert rig.generators[source["resource"]].output[source["channel"]] is False
    for key in ("Z_magnetic_field", "Time_sequence_2"):
        source = rig.mapping[key]
        assert rig.generators[source["resource"]].output[source["channel"]] is True
    rig.gs.set_current.assert_called_with(.001)
    rig.gs.set_output.assert_called_with(True)


def test_real_serpentine_paired_phases_and_slow_disk(rig, monkeypatch):
    rig.params = replace(rig.params, x_field_start_v=-.01, x_field_stop_v=.01, x_field_points=2,
        y_field_start_v=-.01, y_field_stop_v=.01, y_field_points=2)
    save = np.savez
    def slow(path, **data):
        assert rig.enabled
        if "attempt" in str(path):
            rig.events.append("save")
            time.sleep(.001)
        save(path, **data)
    monkeypatch.setattr(np, "savez", slow)
    directory = workflow.run(rig.params)
    assert_safe(rig)
    for index, event in enumerate(rig.events):
        if event == "acquire":
            assert rig.events[index - 2:index] == ["off", "settle"]
            assert rig.events[index + 1:index + 3] == ["on", "save"]
    assert len(rig.captures) == 32
    x, y, configured, phase = build_axes(rig.params)
    np.testing.assert_array_equal(phase, [0, 180, 45, 225, 90, 270, 135, 315])
    with np.load(directory / "raw/xy_rf_phase_response_scan.npz") as data:
        np.testing.assert_array_equal(data["acquisition_order"], [[0, 1], [3, 2]])
        assert data["r_mean_v"].shape == (2, 2, 8)
    for _, xi, yi, xv, yv in iter_serpentine_grid(rig.params):
        for pi, phi in enumerate(phase):
            with np.load(directory / f"raw/grid_X{xi:03}_Y{yi:03}_phase_{pi:03}_attempt_00.npz") as data:
                assert float(data["y_rf_dc_offset_v"]) == yv
                assert float(data["y_rf_burst_phase_deg"]) == phi
    config = yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf-8"))
    assert config["raw_format_version"] == 1 and config["completion_status"] == "completed"
    assert config["temperature_control"]["actual_temperature_c"] is None
    assert config["main_field_restore"]["success"] and config["grid_points"] == 4
    assert rig.aw_settings[-1] == (.2, 0.)


@pytest.mark.parametrize("failure", [WorkflowCancelled("模拟取消"), OSError("模拟采集失败")])
def test_partial_data_and_original_failure(rig, failure):
    rig.fault_index, rig.acquire_error = 1, failure
    with pytest.raises(type(failure)) as caught:
        workflow.run(rig.params)
    assert caught.value is failure
    assert_safe(rig)
    directory = next((rig.root / "data" / workflow.DATA_TYPE).iterdir())
    assert (directory / "raw/grid_X000_Y000_phase_000_attempt_00.npz").is_file()
    assert not (directory / "raw/xy_rf_phase_response_scan.npz").exists()


def test_restore_failure_salvages_payload(rig):
    rig.restore_error = True
    with pytest.raises(OSError, match="温控恢复失败"):
        workflow.run(rig.params)
    directory = next((rig.root / "data" / workflow.DATA_TYPE).iterdir())
    with np.load(directory / "raw/grid_X000_Y000_phase_000_attempt_00.npz") as data:
        assert data["r_v"].size >= 8
    assert rig.events.index("on", rig.events.index("acquire")) > rig.events.index("acquire")
    assert len(rig.captures) == 1


def test_save_failure_only_after_restore(rig, monkeypatch):
    save = np.savez
    def broken(path, **data):
        assert rig.enabled
        if "attempt" in str(path):
            raise OSError("磁盘故障")
        save(path, **data)
    monkeypatch.setattr(np, "savez", broken)
    with pytest.raises(OSError, match="磁盘故障"):
        workflow.run(rig.params)
    assert_safe(rig)
    assert len(rig.captures) == 1


def test_original_error_survives_cleanup_and_config_failure(rig, monkeypatch):
    from ...steps.run_directory import RunDirectory
    save = RunDirectory.update_config
    failure = OSError("原始采集异常")
    rig.fault_index, rig.acquire_error, rig.restore_error, rig.restore_after = 1, failure, True, 2
    def broken(self, **updates):
        if updates.get("failure_reason"):
            raise OSError("次要配置写失败")
        return save(self, **updates)
    monkeypatch.setattr(RunDirectory, "update_config", broken)
    with pytest.raises(OSError) as caught:
        workflow.run(rig.params)
    assert caught.value is failure
    directory = next((rig.root / "data" / workflow.DATA_TYPE).iterdir())
    assert (directory / "raw/grid_X000_Y000_phase_000_attempt_00.npz").is_file()


def test_dependency_quality_gate_and_end_without_prerequisites(rig):
    ctx = workflow.Workflow(rig.params)
    with pytest.raises(ValueError, match="缺少前置"):
        ctx.execute(["A04"])
    assert not rig.events
    ctx.execute(through("A03"))
    rig.quality_bad = True
    ctx.execute(["A04", "A05"])
    assert ctx.states["A04"]["status"] == "quality_failed" and ctx.states["A05"]["status"] == "pending"
    assert len(list(ctx.directory.raw.glob("*attempt*.npz"))) == rig.params.r_point_max_attempts
    ctx.finish()
    empty = workflow.Workflow(rig.params)
    empty.execute([FINISH_ID])
    assert empty.directory is None and empty.closed


def test_full_and_stepwise_same_science_rerun_and_draft_shutdown(rig):
    full = workflow.run(rig.params)
    ctx = workflow.Workflow(rig.params)
    for stage in ACQUISITION:
        if stage.id != FINISH_ID:
            ctx.execute([stage.id])
    with np.load(full / "raw/xy_rf_phase_response_scan.npz") as left, np.load(ctx.directory.raw / "xy_rf_phase_response_scan.npz") as right:
        for key in ("r_mean_v", "r_std_v", "fit_accepted", "acquisition_phase_deg", "amplitude_v"):
            np.testing.assert_array_equal(left[key], right[key])
    original, before = ctx.directory.root, (ctx.directory.root / "experiment_config.yaml").read_bytes()
    ctx.update_parameters({"PHASE_FIT_R_SQUARED_MIN": .9})
    assert ctx.states["A04"]["status"] == "stale" and ctx.states["A03"]["status"] == "stale"
    ctx.execute(["A03", "A04", "A05"])
    assert ctx.directory.root != original
    assert (original / "experiment_config.yaml").read_bytes() == before
    applied = ctx.shutdown_parameters
    ctx.update_parameters({"PUMP_CARRIER_FREQUENCY_HZ": 99e6})
    assert ctx.shutdown_parameters is applied
    ctx.finish()
    assert_safe(rig)
    ctx.devices["tec"].set_enable.assert_not_called()
    ctx.devices["tec"].get_temperature.assert_not_called()


@pytest.mark.parametrize("mode", ["corrected_run", "theory"])
def test_source_content_freeze_invalidates_before_hardware(rig, mode):
    ctx = workflow.Workflow(replace(rig.params, control_waveform_source=mode))
    ctx.execute(["A01"])
    path = (rig.root / "data/Z_AW_Closed_Loop_Waveform_Correction/frozen/results/corrected_control_waveform.npz"
        if mode == "corrected_run" else rig.root / "theory/v1/waveforms/optimal_control_waveform.csv")
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="控制来源文件已改变"):
        ctx.execute(["A02"])
    assert ctx.states["A01"]["status"] == "stale" and not rig.events
    ctx.finish()


def test_manifest_parameter_ownership_and_real_preview(rig):
    fields = {field["name"] for field in rig.params.schema()["fields"]}
    manifest = definition()
    assert fields <= {key for stage in manifest["acquisition"] for key in stage["display_parameters"]}
    ctx = workflow.Workflow(rig.params)
    ctx.states = {key: {"status": "completed", "error": ""} for key in ctx.states}
    changed = {"PHASE_FIT_R_SQUARED_MIN", "Y_FIELD_POINTS"}
    predicted = set().union(*(set(manifest["parameter_impacts"]["acquisition"][key]) for key in changed))
    ctx.update_parameters({"PHASE_FIT_R_SQUARED_MIN": .9, "Y_FIELD_POINTS": 2, "Y_FIELD_STOP_V": .01})
    assert {key for key, state in ctx.states.items() if state["status"] == "stale"} == predicted
    ctx.finish()


def test_six_phase_points_rejected_before_connection(rig):
    with pytest.raises(ValueError, match="至少 8"):
        workflow.Workflow(replace(rig.params, phase_scan_stop_deg=300., phase_scan_step_deg=60.))
    assert not rig.events


def test_single_outlier_reacquire_preserves_original_file_and_grid_quality(rig, monkeypatch):
    calls = []
    def detect(phase, mean, noise, **kwargs):
        index = (2,) if not calls else ()
        calls.append(True)
        return SimpleNamespace(outlier_indices=index, residual_v=np.ones(len(phase)), threshold_v=.1,
            to_dict=lambda: {"outlier_phase_deg": [float(phase[i]) for i in index]})
    monkeypatch.setattr(acquisition, "detect_dispersion_phase_outliers", detect)
    directory = workflow.run(rig.params)
    assert len(rig.captures) == 9
    assert (directory / "raw/grid_X000_Y000_phase_002_attempt_00.npz").is_file()
    assert (directory / "raw/grid_X000_Y000_phase_002_cross_retry_attempt_00.npz").is_file()
    with np.load(directory / "raw/xy_rf_phase_response_scan.npz") as data:
        assert np.count_nonzero(data["cross_phase_reacquired"]) == 1
        assert str(data["accepted_file"][0, 0, 2]).endswith("cross_retry_attempt_00.npz")
    assert_safe(rig)


if __name__ == "__main__":
    from pytest import MonkeyPatch
    from .session import main
    root = Path(os.environ["MX_Z_XY_PHASE_TEST_ROOT"])
    patch = MonkeyPatch()
    rig = install_rig(root, patch)
    if name := os.environ.get("MX_Z_XY_PHASE_TEST_SHUTDOWN_GATE"):
        gate, shutdown = Path(name), shared_preparation.safe_shutdown
        def gated(devices, channels, params, **kwargs):
            gate.with_suffix(".started").write_text("收尾开始", encoding="utf-8")
            deadline = time.monotonic() + 30
            while not gate.exists():
                if time.monotonic() > deadline:
                    raise TimeoutError("测试没有释放收尾")
                time.sleep(.01)
            report = shutdown(devices, channels, params, **kwargs)
            pump = rig.mapping["Pump_modulation"]
            gate.with_suffix(".finished").write_text(json.dumps({"pump_frequency_hz": rig.generators[pump["resource"]].frequency[pump["channel"]],
                "errors": report.errors}), encoding="utf-8")
            return report
        patch.setattr(shared_preparation, "safe_shutdown", gated)
    main()
