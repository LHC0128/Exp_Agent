"""本 ID 的真实配置/DAQ/文件写入配假仪器，不访问实验硬件。"""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import json
import os
import sys
import time
import numpy as np
import pytest
import yaml
from lockin_amplifier import DAQResult
from ...common import WorkflowCancelled
from ..mx_y_optimal_control_rf_frequency_response.test_workflow import copy_config, install_rig as install_base, assert_safe
from ..mx_y_rf_sensitivity import acquisition as r_acquisition
from ..mx_y_rf_sensitivity import acquisition_steps as r_steps
from ..mx_z_field_calibration import preparation as connections
from . import workflow, models, preparation, acquisition
from .models import MxZOptimalControlRFParams
from .stages import ACQUISITION, FINISH_ID, definition

ACQUISITION_CASES = {
    "mx-z-optimal-control-rf-sensitivity": {"mode": "single", "cases": {
        "order": ["test_full_order_slow_disk_and_real_modes"],
        "slow_disk": ["test_full_order_slow_disk_and_real_modes"],
        "cancel": ["test_failure_preserves_original_and_partial_data"],
        "acquire_failure": ["test_failure_preserves_original_and_partial_data"],
        "restore_failure": ["test_restore_failure_salvages_current_rxy", "test_residual_restore_error_preserves_acquisition_error_and_safety_failure"],
        "save_failure": ["test_writer_failure_after_restore"],
        "partial_save": ["test_failure_preserves_original_and_partial_data", "test_restore_failure_salvages_current_rxy", "test_residual_restore_error_preserves_acquisition_error_and_safety_failure"],
        "entrypoints": ["test_full_stepwise_rerun_and_unexecuted_draft"],
    }, "sources": ["lab_workflows/experiment_modules/mx_z_optimal_control_rf_sensitivity/workflow.py",
        "lab_workflows/experiment_modules/mx_z_optimal_control_rf_sensitivity/acquisition.py",
        "lab_workflows/experiment_modules/mx_z_optimal_control_rf_sensitivity/excitation.py",
        "lab_workflows/experiment_modules/mx_z_optimal_control_rf_sensitivity/preparation.py"]},
}


def parameters(**changes):
    return replace(MxZOptimalControlRFParams(), corrected_control_source_run="frozen",
        phase_scan_stop_deg=315., phase_scan_step_deg=45., phase_fit_r_squared_min=.85,
        y_rf_amp_points=21, response_duration_s=.032, noise_n_avg=2, noise_rate_sa_s=1000.,
        noise_duration_s=.256, system_settle_s=.001, temp_switch_on_lag_s=0., **changes)


def install_rig(root, monkeypatch, params=None):
    rig = install_base(root, monkeypatch, params or parameters())
    rig.phase_outlier = rig.flat_phase = False
    rig.outlier_phase = 90.
    gs = Mock()
    gs.get_source_function.return_value = "CURRent"
    gs.get_current.return_value = .001
    gs.get_current_range.return_value = .01
    gs.get_current_limit.return_value = .02
    gs.get_output.return_value = True
    rig.gs = gs
    monkeypatch.setattr(connections, "GS200Instrument", lambda resource: gs)
    monkeypatch.setattr(workflow, "find_project_root", lambda: root)
    monkeypatch.setattr(models, "find_project_root", lambda: root)
    monkeypatch.setattr(r_steps, "_sleep", lambda seconds: rig.events.append("settle"))

    def acquire(hf2, config, **kwargs):
        assert not rig.enabled
        rig.events.append("acquire")
        index = len(rig.captures)
        rig.captures.append(list(config.signal_paths))
        if index == rig.fault_index:
            raise rig.acquire_error or WorkflowCancelled("模拟取消")
        kwargs["cancellation_check"]()
        source = rig.mapping["rf_coil"]
        rf = rig.generators[source["resource"]]
        channel = source["channel"]
        phase = float(rf.phase.get(channel, 0.))
        count = max(8, config.grid_cols)
        t = np.arange(count) / kwargs["actual_rate"]
        noise = 1e-6 * np.sin(np.arange(count) * .7)
        if rig.quality_bad:
            noise += np.linspace(-1, 1, count)
        if "sample.x" in config.signal_paths:
            theta = np.deg2rad(phase - 30.)
            z = np.exp(.4j) * (.02 + .003 * np.cos(theta) + 1j * .001 * np.sin(theta))
            if rig.flat_phase:
                z = np.exp(.4j) * .02
            if rig.phase_outlier and phase == rig.outlier_phase and index < 10:
                z += .04
            values = dict(x=z.real + noise, y=z.imag + noise * .7)
            values["r"] = np.hypot(values["x"], values["y"])
        else:
            amplitude = rf.amplitude.get(channel, 0.) if rf.shape.get(channel) == "SIN" else 0.
            signed = amplitude * (1 if np.cos(np.deg2rad(phase - 30.)) >= 0 else -1)
            mean = abs(.001 * signed / (signed ** 2 + .03 ** 2) + .01)
            if config.duration > .1:
                noise += 2e-5 * np.sin(2 * np.pi * 31.25 * t)
            values = dict(r=np.full(count, mean) + noise)
        return [DAQResult(signal_name=name, demod_index=kwargs["demod_idx"],
            values=values[name.rsplit(".", 1)[-1]], time=t) for name in config.signal_paths]
    monkeypatch.setattr(r_acquisition.daq, "acquire_data", acquire)
    return rig


@pytest.fixture
def rig(tmp_path, monkeypatch):
    copy_config(tmp_path)
    return install_rig(tmp_path, monkeypatch)


def through(last):
    ids = [stage.id for stage in ACQUISITION if stage.id != FINISH_ID]
    return ids[:ids.index(last) + 1]


@pytest.mark.parametrize("residual", [False, True])
def test_full_order_slow_disk_and_real_modes(rig, monkeypatch, residual):
    if residual:
        rig.params = replace(rig.params, phase_cal_rf_amplitude_vpp=0.)
    original = np.savez
    def slow(path, **data):
        assert rig.enabled
        if "attempt" in str(getattr(path, "name", path)) or "r_v" in data:
            rig.events.append("save")
            time.sleep(.001)
        original(path, **data)
    monkeypatch.setattr(np, "savez", slow)
    original_write = Path.write_text
    def write(path, *args, **kwargs):
        assert rig.enabled
        return original_write(path, *args, **kwargs)
    monkeypatch.setattr(Path, "write_text", write)
    directory = workflow.run(rig.params)
    assert_safe(rig)
    for index, event in enumerate(rig.events):
        if event == "acquire":
            assert rig.events[index - 2:index] == ["off", "settle"]
            assert rig.events[index + 1:index + 3] == ["on", "save"]
    config = yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf-8"))
    assert config["completion_status"] == "completed" and config["raw_format_version"] == 1
    assert config["temperature_control"]["actual_temperature_c"] is None
    assert config["temperature_control"]["controlled_by_experiment"] is True
    rig.gs.set_current.assert_any_call(.001)
    with np.load(directory / "raw/phase_scan.npz") as data:
        assert data["scanned_phase_deg"].size == 8
        if residual:
            np.testing.assert_array_equal(data["scanned_phase_deg"], np.arange(0, 360, 45))
        else:
            np.testing.assert_array_equal(data["scanned_phase_deg"], [0, 180, 45, 225, 90, 270, 135, 315])
    if residual:
        assert not (directory / "raw/amplitude_scan.npz").exists()
        assert len(rig.captures) == 8
    else:
        assert len(rig.captures) == 9 + 21 + 2
        assert rig.captures[0] == ["sample.r", "sample.x", "sample.y"]
        assert rig.captures[-1] == ["sample.r"]
        with np.load(directory / "raw/amplitude_scan.npz") as data:
            assert data["signed_amplitude_vpp"].size == 21
            assert data["waveform_mode"][10] == "dc"


@pytest.mark.parametrize("failure", [WorkflowCancelled("模拟取消"), OSError("模拟采集失败")])
def test_failure_preserves_original_and_partial_data(rig, failure):
    rig.fault_index, rig.acquire_error = 2, failure
    with pytest.raises(type(failure)) as caught:
        workflow.run(rig.params)
    assert caught.value is failure
    assert_safe(rig)
    directory = next((rig.root / "data" / workflow.DATA_TYPE).iterdir())
    assert len(list((directory / "raw").glob("*attempt*.npz"))) == 2
    assert not (directory / "raw/amplitude_scan.npz").exists()


def test_restore_failure_salvages_current_rxy(rig):
    rig.restore_error = True
    with pytest.raises(OSError, match="温控恢复失败"):
        workflow.run(rig.params)
    directory = next((rig.root / "data" / workflow.DATA_TYPE).iterdir())
    with np.load(directory / "raw/phase_dummy_attempt_00.npz") as data:
        assert data["r_v"].size >= 8 and data["x_v"].size >= 8
    assert "on" in rig.events[rig.events.index("acquire") + 1:]
    config = yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf-8"))
    assert config["completion_status"] == "failed"


def test_writer_failure_after_restore(rig, monkeypatch):
    original = np.savez
    def fail(path, **data):
        assert rig.enabled
        if "attempt" in str(getattr(path, "name", path)):
            raise OSError("磁盘故障")
        original(path, **data)
    monkeypatch.setattr(np, "savez", fail)
    with pytest.raises(OSError, match="磁盘故障"):
        workflow.run(rig.params)
    assert_safe(rig)
    assert len(rig.captures) == 1


def test_full_stepwise_rerun_and_unexecuted_draft(rig):
    full = workflow.run(rig.params)
    ctx = workflow.Workflow(rig.params)
    for stage in ACQUISITION:
        if stage.id != FINISH_ID:
            ctx.execute([stage.id])
    for name in ("phase_scan.npz", "amplitude_scan.npz", "noise_000.npz"):
        with np.load(full / "raw" / name) as left, np.load(ctx.directory.raw / name) as right:
            for key in left.files:
                np.testing.assert_array_equal(left[key], right[key])
    previous = ctx.directory.root
    before = {str(p.relative_to(previous)): (p.read_bytes(), p.stat().st_mtime_ns) for p in previous.rglob("*") if p.is_file()}
    ctx.update_parameters({"PUMP_CARRIER_FREQUENCY_HZ": 99000000.})
    assert ctx.shutdown_parameters.pump_carrier_frequency_hz != 99000000.
    ctx.execute(["A03", "A04", "A05", "A06", "A07"])
    assert ctx.directory.root != previous
    assert before == {str(p.relative_to(previous)): (p.read_bytes(), p.stat().st_mtime_ns) for p in previous.rglob("*") if p.is_file()}
    ctx.update_parameters({"PUMP_CARRIER_FREQUENCY_HZ": 98000000., "RESPONSE_TIME_CONSTANT_S": .002})
    ctx.finish()
    source = rig.mapping["Pump_modulation"]
    assert rig.generators[source["resource"]].frequency[source["channel"]] == 99000000.
    assert_safe(rig)


def test_quality_dependency_clock_source_and_parameter_ownership(rig):
    ctx = workflow.Workflow(rig.params)
    with pytest.raises(ValueError, match="缺少前置"):
        ctx.execute(["A04"])
    assert not rig.events
    visible = {f["name"] for f in rig.params.schema()["fields"]}
    owned = {key for column in ("acquisition", "analysis") for step in definition()[column] for key in step["display_parameters"]}
    assert visible <= owned
    ctx.execute(through("A03"))
    rig.quality_bad = True
    ctx.execute(["A04", "A05"])
    assert ctx.states["A04"]["status"] == "quality_failed"
    assert ctx.states["A05"]["status"] != "completed"
    ctx.finish()
    assert_safe(rig)
    empty = workflow.Workflow(rig.params)
    empty.execute([FINISH_ID])
    assert empty.closed and empty.directory is None


def test_clock_failure_stops_before_capture(rig):
    rig.clock_error = True
    with pytest.raises(RuntimeError, match="时钟|参考"):
        workflow.run(rig.params)
    assert not rig.captures and rig.enabled


def test_failed_workpoint_does_not_apply_draft_during_shutdown(rig):
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A03"))
    baseline = ctx.shutdown_parameters
    ctx.update_parameters({"PUMP_CARRIER_FREQUENCY_HZ": 99e6})
    rig.clock_error = True
    with pytest.raises(RuntimeError) as caught:
        ctx.execute(["A03"])
    assert ctx.shutdown_parameters is baseline
    ctx.finish(caught.value)
    source = rig.mapping["Pump_modulation"]
    assert rig.generators[source["resource"]].frequency[source["channel"]] == baseline.pump_carrier_frequency_hz


def test_residual_restore_error_preserves_acquisition_error_and_safety_failure(rig, monkeypatch):
    rig.params = replace(rig.params, phase_cal_rf_amplitude_vpp=0.)
    failure = OSError("原始读数失败")
    rig.fault_index, rig.acquire_error = 1, failure
    rearm = acquisition._rearm_control
    def restore(*args, **kwargs):
        if len(rig.captures) >= 2 and kwargs["phase_deg"] == rig.params.control_burst_phase_deg:
            raise OSError("相位恢复失败")
        return rearm(*args, **kwargs)
    monkeypatch.setattr(acquisition, "_rearm_control", restore)
    monkeypatch.setattr(workflow, "_rearm_control", restore)
    with pytest.raises(OSError) as caught:
        workflow.run(rig.params)
    assert caught.value is failure
    directory = next((rig.root / "data" / workflow.DATA_TYPE).iterdir())
    config = yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf-8"))
    assert config["acquisition_cleanup_notes"]
    assert config["safety_shutdown"]["completed"] is False
    assert "相位" in str(config["safety_shutdown"]["errors"])
    assert (directory / "raw/phase_0000_attempt_00.npz").is_file()


def test_frozen_content_change_rejected_before_more_hardware(rig):
    ctx = workflow.Workflow(rig.params)
    ctx.execute(["A01"])
    source = rig.root / "data/Z_AW_Closed_Loop_Waveform_Correction/frozen/results/corrected_control_waveform.npz"
    with np.load(source) as data:
        values = {k: data[k] for k in data.files}
    values["normalized"] = -values["normalized"]
    values["voltage_v"] = -values["voltage_v"]
    np.savez(source, **values)
    with pytest.raises(ValueError, match="已改变"):
        ctx.execute(["A02"])
    assert not rig.events
    ctx.finish()


def test_full_run_rechecks_source_between_freeze_and_connect(rig, monkeypatch):
    load = preparation.load_source
    def replace_after_load(params, root):
        control, applied = load(params, root)
        path = control.waveform_path
        with np.load(path) as data:
            values = {key: data[key] for key in data.files}
        values["normalized"] = -values["normalized"]
        values["voltage_v"] = -values["voltage_v"]
        np.savez(path, **values)
        return control, applied
    monkeypatch.setattr(preparation, "load_source", replace_after_load)
    with pytest.raises(ValueError, match="已改变"):
        workflow.run(rig.params)
    assert not rig.events


def test_parameter_v5_migration_new_key_priority_and_yaml_roundtrip():
    values = parameters().to_external()
    values.pop("SYSTEM_SETTLE_S")
    migrated = MxZOptimalControlRFParams.from_external(values | {"RESPONSE_SETTLE_TIME_S": .3, "TEMP_SWITCH_OFF_LEAD_S": .5}, schema_version=5)
    assert migrated.system_settle_s == .5
    explicit = MxZOptimalControlRFParams.from_external(values | {"SYSTEM_SETTLE_S": .2, "RESPONSE_SETTLE_TIME_S": .8}, schema_version=5)
    assert explicit.system_settle_s == .2
    assert MxZOptimalControlRFParams.from_external(yaml.safe_load(yaml.safe_dump(explicit.to_external()))).to_external() == explicit.to_external()


def test_cross_phase_retry_and_quadrature_quality_block(rig):
    rig.phase_outlier = True
    rig.outlier_phase = 80.
    rig.params = replace(rig.params, phase_scan_stop_deg=340., phase_scan_step_deg=20.)
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A04"))
    payload = ctx.state["phase_calibration"]
    assert payload["cross_phase_reacquisition"]["initial_detection"]["outlier_phase_deg"]
    assert not payload["cross_phase_reacquisition"]["final_detection"]["outlier_phase_deg"]
    assert list(ctx.directory.raw.glob("phase_*_cross_retry_attempt_*.npz"))
    ctx.finish()
    rig.phase_outlier, rig.flat_phase = False, True
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A05"))
    assert ctx.states["A04"]["status"] == "quality_failed"
    assert ctx.states["A05"]["status"] != "completed"
    assert (ctx.directory.results / "phase_calibration.yaml").is_file()
    assert (ctx.directory.results / "phase_calibration.png").is_file()
    ctx.finish()


def test_noise_rf_metadata_and_dc_transition(rig):
    rig.params = replace(rig.params, noise_rf_enabled=True, noise_rf_amplitude_vpp=.002)
    directory = workflow.run(rig.params)
    config = yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf-8"))
    assert config["noise_rf"]["y_rf_enabled"] is True
    with np.load(directory / "raw/noise_000.npz") as data:
        assert data["y_rf_amplitude_vpp"] == .002
        assert data["y_rf_dc_v"] == rig.params.y_rf_offset_v
    assert_safe(rig)


def main():
    """仅测试显式启动本模块时注入假仪器，生产 session 不加载此入口。"""
    from ...experiment_runtime import load_runtime_params
    from ..noise_spectrum_xy.session import serve
    root = Path(os.environ["MX_Z_RF_TEST_ROOT"])
    payload = json.loads(os.environ["LAB_TYPED_PARAMETERS"])
    session_id = payload.pop("session_id")
    os.environ["LAB_TYPED_PARAMETERS"] = json.dumps(payload)
    monkeypatch = pytest.MonkeyPatch()
    install_rig(root, monkeypatch)
    ctx = workflow.Workflow(load_runtime_params(MxZOptimalControlRFParams), session_id=session_id)
    if gate_path := os.environ.get("MX_Z_RF_TEST_SHUTDOWN_GATE"):
        gate = Path(gate_path)
        shutdown = ctx._shutdown
        def blocked_shutdown(**kwargs):
            gate.with_suffix(".started").touch()
            deadline = time.monotonic() + 25
            while not gate.exists():
                if time.monotonic() > deadline:
                    raise TimeoutError("测试未释放收尾")
                time.sleep(.02)
            result = shutdown(**kwargs)
            gate.with_suffix(".finished").write_text(json.dumps(dict(
                pump_frequency_hz=ctx.shutdown_parameters.pump_carrier_frequency_hz,
                response_rate_sa_s=ctx.response_parameters.response_rate_sa_s,
                errors=result)), encoding="utf-8")
            return result
        ctx._shutdown = blocked_shutdown
    try:
        serve(ctx, sys.stdin, stages=ACQUISITION, validate=workflow.validate_selection, finish_id=FINISH_ID)
    finally:
        monkeypatch.undo()


if __name__ == "__main__":
    main()
