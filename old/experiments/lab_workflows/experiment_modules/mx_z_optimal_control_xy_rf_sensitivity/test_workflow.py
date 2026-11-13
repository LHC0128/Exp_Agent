"""本 ID 的真实中心校相、蛇形网格、恢复与磁盘写入的无硬件回归。"""
from dataclasses import replace
from pathlib import Path
import hashlib
import json
import os
import sys
import time
import numpy as np
import pytest
import yaml
from lockin_amplifier import DAQResult
from ...common import WorkflowCancelled
from ..mx_z_optimal_control_rf_sensitivity.test_workflow import install_rig as install_control
from ..mx_y_optimal_control_rf_frequency_response.test_workflow import copy_config, assert_safe
from .. import mx_z_control_params
from ..mx_y_rf_sensitivity import acquisition as r_acquisition
from . import workflow, acquisition, preparation
from .models import MxZOptimalControlXYRFSensitivityParams
from .stages import ACQUISITION, FINISH_ID, definition

ACQUISITION_CASES = {
    "mx-z-optimal-control-xy-rf-sensitivity": {"mode": "single", "cases": {
        "order": ["test_real_grid_order_slow_disk_and_offsets"],
        "slow_disk": ["test_real_grid_order_slow_disk_and_offsets"],
        "cancel": ["test_grid_failure_stops_preserves_original_and_partial_data"],
        "acquire_failure": ["test_grid_failure_stops_preserves_original_and_partial_data"],
        "restore_failure": ["test_restore_failure_salvages_current_payload"],
        "save_failure": ["test_save_failure_after_restore_stops_next_point"],
        "partial_save": ["test_grid_failure_stops_preserves_original_and_partial_data", "test_restore_failure_salvages_current_payload"],
        "entrypoints": ["test_complete_stepwise_match_and_rerun_preserves_previous"],
    }, "sources": ["lab_workflows/experiment_modules/mx_z_optimal_control_xy_rf_sensitivity/workflow.py",
        "lab_workflows/experiment_modules/mx_z_optimal_control_xy_rf_sensitivity/preparation.py",
        "lab_workflows/experiment_modules/mx_z_optimal_control_xy_rf_sensitivity/acquisition.py"]},
}


def parameters(**changes):
    values = dict(corrected_control_source_run="frozen", phase_scan_stop_deg=315., phase_scan_step_deg=45.,
        phase_fit_r_squared_min=.85, y_rf_amp_points=21, response_duration_s=.032, noise_n_avg=2,
        noise_rate_sa_s=1000., noise_duration_s=.256, system_settle_s=.001, temp_switch_on_lag_s=0.,
        x_field_start_v=-.01, x_field_stop_v=.01, x_field_points=2,
        y_field_start_v=-.02, y_field_stop_v=.02, y_field_points=2)
    return replace(MxZOptimalControlXYRFSensitivityParams(), **(values | changes))


def install_rig(root, monkeypatch, params=None):
    rig = install_control(root, monkeypatch, params or parameters())
    monkeypatch.setattr(workflow, "find_project_root", lambda: root)
    monkeypatch.setattr(mx_z_control_params, "find_project_root", lambda: root)
    reader = r_acquisition.daq.acquire_data
    def acquire(hf2, config, **kwargs):
        records = reader(hf2, config, **kwargs)
        if config.duration > .1:
            count, rate = len(records[0].values), kwargs["actual_rate"]
            frequency = np.fft.rfftfreq(count, 1 / rate)
            # 给真实 Welch 链提供低频上升、平坦平台与高频上升，保留全部科学门槛。
            target = np.maximum(1, 20 / np.maximum(frequency, 1)) * np.maximum(1, frequency / 300)
            amplitude = target / np.sqrt(1 + (frequency / 318.65) ** 2)
            wave = np.fft.irfft(amplitude * np.exp(1j * np.pi * np.arange(len(frequency))), count) * 1e-4
            return [DAQResult(signal_name=item.signal_name, demod_index=item.demod_index,
                values=np.mean(item.values) + wave, time=item.time) for item in records]
        return records
    monkeypatch.setattr(r_acquisition.daq, "acquire_data", acquire)
    return rig


@pytest.fixture
def rig(tmp_path, monkeypatch):
    copy_config(tmp_path)
    return install_rig(tmp_path, monkeypatch)


def through(last):
    ids = [stage.id for stage in ACQUISITION if stage.id != FINISH_ID]
    return ids[:ids.index(last) + 1]


def read_config(directory):
    return yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf-8"))


def hashes(directory):
    return {path.relative_to(directory).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in directory.rglob("*") if path.is_file()}


def test_real_grid_order_slow_disk_and_offsets(rig, monkeypatch):
    save, write = np.savez, Path.write_text
    def slow(path, **data):
        assert rig.enabled
        if "r_v" in data:
            rig.events.append("save")
            time.sleep(.001)
        save(path, **data)
    def text(path, *args, **kwargs):
        assert rig.enabled
        return write(path, *args, **kwargs)
    monkeypatch.setattr(np, "savez", slow)
    monkeypatch.setattr(Path, "write_text", text)
    directory = workflow.run(rig.params)
    assert_safe(rig)
    for i, event in enumerate(rig.events):
        if event == "acquire":
            assert rig.events[i - 2:i] == ["off", "settle"]
            assert rig.events[i + 1:i + 3] == ["on", "save"]
    config = read_config(directory)
    assert config["experiment_id"] == workflow.EXPERIMENT_ID
    assert config["raw_format_version"] == 1 and config["completion_status"] == "completed"
    assert config["main_field_restore"]["success"] and config["safety_shutdown"]["completed"]
    assert config["phase_calibration_reference"]["x_field_v"] == 0.
    assert config["phase_calibration_reference"]["y_field_v"] == 0.
    assert config["temperature_control"]["actual_temperature_c"] is None
    assert config["temperature_control"]["controlled_by_experiment"] is True
    manifest = yaml.safe_load((directory / "raw/point_manifest.yaml").read_text(encoding="utf-8"))
    assert manifest["acquisition_order"] == ["x_000_y_000", "x_000_y_001", "x_001_y_001", "x_001_y_000"]
    for key, record in manifest["points"].items():
        assert record["status"] == "completed"
        point = directory / "raw/points" / key
        with np.load(point / "amplitude_scan.npz") as data:
            np.testing.assert_allclose(data["signed_amplitude_vpp"], np.linspace(-.1, .1, 21))
            assert data["waveform_mode"][10] == "dc"
        with np.load(point / "noise_000.npz") as data:
            assert data["y_rf_dc_v"] == record["y_field_v"]
            assert bool(data["y_rf_output_on"]) == bool(record["y_field_v"])
    assert len(rig.captures) == 9 + 4 * (21 + 2)
    assert sum("sample.x" in capture for capture in rig.captures) == 9


@pytest.mark.parametrize("failure", [WorkflowCancelled("模拟网格取消"), OSError("模拟网格读取失败")])
def test_grid_failure_stops_preserves_original_and_partial_data(rig, failure):
    rig.fault_index, rig.acquire_error = 12, failure
    with pytest.raises(type(failure)) as caught:
        workflow.run(rig.params)
    assert caught.value is failure
    assert_safe(rig)
    directory = next((rig.root / "data" / workflow.DATA_TYPE).iterdir())
    point = directory / "raw/points/x_000_y_000"
    assert len(list(point.glob("*attempt*.npz"))) == 3
    manifest = yaml.safe_load((directory / "raw/point_manifest.yaml").read_text(encoding="utf-8"))
    assert manifest["points"]["x_000_y_000"]["reason"] == str(failure)
    assert manifest["points"]["x_000_y_001"]["status"] == "pending"
    assert read_config(directory)["completion_status"] != "completed"


@pytest.mark.parametrize("also_acquire_failure", [False, True])
def test_restore_failure_salvages_current_payload(rig, also_acquire_failure):
    rig.restore_error, rig.restore_after = True, 11 if also_acquire_failure else 10
    if also_acquire_failure:
        rig.fault_index, rig.acquire_error = 10, ValueError("原始读取错误")
    expected = ValueError if also_acquire_failure else OSError
    with pytest.raises(expected) as caught:
        workflow.run(rig.params)
    directory = next((rig.root / "data" / workflow.DATA_TYPE).iterdir())
    with np.load(directory / "raw/points/x_000_y_000/amplitude_0000_attempt_00.npz") as data:
        assert data["r_v"].size >= 8
    assert len(rig.captures) <= 11
    assert read_config(directory)["completion_status"] == "failed"
    if also_acquire_failure:
        assert caught.value is rig.acquire_error


def test_save_failure_after_restore_stops_next_point(rig, monkeypatch):
    save = np.savez
    def fail(path, **data):
        assert rig.enabled
        if "signed_amplitude_vpp" in data and "r_v" in data:
            raise OSError("模拟磁盘写入失败")
        save(path, **data)
    monkeypatch.setattr(np, "savez", fail)
    with pytest.raises(OSError, match="磁盘写入失败"):
        workflow.run(rig.params)
    assert_safe(rig)
    assert len(rig.captures) == 10


def test_quality_exhaustion_skips_only_one_xy_point(rig, monkeypatch):
    acquire = acquisition._acquire_point
    def point(params, value, *args):
        rig.quality_bad = value["key"] == "x_000_y_000"
        return acquire(params, value, *args)
    monkeypatch.setattr(acquisition, "_acquire_point", point)
    directory = workflow.run(rig.params)
    manifest = yaml.safe_load((directory / "raw/point_manifest.yaml").read_text(encoding="utf-8"))
    assert manifest["points"]["x_000_y_000"]["status"] == "invalid_quality"
    assert sum(record["status"] == "completed" for record in manifest["points"].values()) == 3
    assert len(list((directory / "raw/points/x_000_y_000").glob("*attempt*.npz"))) == 3
    assert_safe(rig)


def test_complete_stepwise_match_and_rerun_preserves_previous(rig):
    full = workflow.run(rig.params)
    ctx = workflow.Workflow(rig.params)
    for step in ACQUISITION:
        if step.id != FINISH_ID:
            ctx.execute([step.id])
    for relative in ("phase_scan.npz", "points/x_000_y_000/amplitude_scan.npz", "points/x_001_y_000/noise_000.npz"):
        with np.load(full / "raw" / relative) as left, np.load(ctx.directory.raw / relative) as right:
            for key in left.files:
                np.testing.assert_array_equal(left[key], right[key])
    previous, before = ctx.directory.root, hashes(ctx.directory.root)
    ctx.execute(["A03", "A04", "A05", "A06"])
    assert ctx.directory.root != previous and hashes(previous) == before
    ctx.finish()
    assert_safe(rig)


def test_preconditions_quality_and_visible_parameter_ownership(rig):
    ctx = workflow.Workflow(rig.params)
    with pytest.raises(ValueError, match="缺少前置"):
        ctx.execute(["A05"])
    assert not rig.events
    visible = {field["name"] for field in rig.params.schema()["fields"]}
    owned = {key for column in ("acquisition", "analysis") for stage in definition()[column] for key in stage["display_parameters"]}
    assert visible <= owned
    ctx.execute(through("A03"))
    rig.flat_phase = True
    ctx.execute(["A04", "A05"])
    assert ctx.states["A04"]["status"] == "quality_failed"
    assert ctx.states["A05"]["status"] != "completed"
    ctx.finish()
    empty = workflow.Workflow(rig.params)
    empty.execute([FINISH_ID])
    assert empty.closed and empty.directory is None


def test_unexecuted_parameter_draft_cannot_change_shutdown(rig):
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A05"))
    baseline = ctx.shutdown_parameters
    ctx.update_parameters({"PUMP_CARRIER_FREQUENCY_HZ": 99e6, "RESPONSE_RATE_SA_S": 2000.})
    ctx.finish()
    source = rig.mapping["Pump_modulation"]
    assert rig.generators[source["resource"]].frequency[source["channel"]] == baseline.pump_carrier_frequency_hz
    assert ctx.response_parameters.response_rate_sa_s == baseline.response_rate_sa_s
    assert_safe(rig)


def test_source_drift_and_clock_failure_reject_before_capture(rig):
    ctx = workflow.Workflow(rig.params)
    ctx.execute(["A01"])
    path = rig.root / "data/Z_AW_Closed_Loop_Waveform_Correction/frozen/results/corrected_control_waveform.npz"
    path.write_bytes(path.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="来源文件已改变"):
        ctx.execute(["A02"])
    assert not rig.events
    ctx.finish()
    rig.clock_error = True
    with pytest.raises(RuntimeError, match="时钟|参考"):
        workflow.run(rig.params)
    assert not rig.captures and rig.enabled


def test_manifest_atomic_replace_keeps_previous_on_failure(tmp_path, monkeypatch):
    path = tmp_path / "point_manifest.yaml"
    acquisition._write_manifest(path, {"value": "previous"})
    before = path.read_bytes()
    def fail(*args):
        raise PermissionError("模拟替换失败")
    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(PermissionError):
        acquisition._write_manifest(path, {"value": "next"})
    assert path.read_bytes() == before


def test_theory_source_freeze_and_calibration_drift_use_real_contract(rig):
    source = rig.root / "theory/v1"
    (source / "waveforms").mkdir(parents=True)
    (source / "parameters").mkdir()
    time_axis = np.arange(16) / 1600.
    np.savetxt(source / "waveforms/optimal_control_waveform.csv",
        np.column_stack((time_axis, 50 * np.sin(2 * np.pi * 100 * time_axis))), delimiter=",")
    (source / "parameters/optimal_control_params.csv").write_text("# Output version,v1\nf_rf_Hz,100\n", encoding="utf-8")
    calibration = rig.root / "data/Mx_Z_Field_Calibration/calibration/results/analysis.yaml"
    calibration.parent.mkdir(parents=True)
    payload = dict(experiment_id="mx-z-field-calibration", success=True, K_Z_Hz_per_V=1000.,
        f_0V_Hz=12000., linear_fit={"r_squared": .999})
    calibration.write_text(yaml.safe_dump(payload), encoding="utf-8")
    params = replace(rig.params, control_waveform_source="theory", control_results_root=str(source.parent),
        control_version="v1", z_calibration_source_run="calibration", z_aw_output_vpp=.2)
    ctx = workflow.Workflow(params)
    ctx.execute(through("A03"))
    assert ctx.sources[0] is None
    assert (ctx.directory.raw / "source_optimal_control_waveform.csv").is_file()
    assert ctx.state["control_source"]["mode"] == "theory"
    assert rig.aw_settings[-1] == (.2, 0.)
    payload["f_0V_Hz"] += 1
    calibration.write_text(yaml.safe_dump(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="来源文件已改变"):
        ctx.execute(["A04"])
    assert not rig.captures
    ctx.finish()
    assert_safe(rig)


def test_copied_source_hash_and_zero_calibration_preflight(rig):
    for key in ("phase_cal_rf_amplitude_vpp", "y_rf_nt_per_vpp"):
        with pytest.raises(ValueError, match="必须大于 0"):
            workflow.Workflow(replace(rig.params, **{key: 0.}))
    source = preparation._load_control_sources(rig.params, rig.root)
    path = source[0].waveform_path
    path.write_bytes(path.read_bytes() + b"changed")
    raw = rig.root / "copied"
    raw.mkdir()
    with pytest.raises(ValueError, match="冻结内容不一致"):
        preparation.save_source(raw, source)
    assert not rig.events


def test_shutdown_failure_is_not_safe_completion(rig, monkeypatch):
    ctx = workflow.Workflow(parameters(x_field_start_v=0., x_field_stop_v=0., x_field_points=1,
        y_field_start_v=0., y_field_stop_v=0., y_field_points=1))
    ctx.execute(through("A05"))
    signal = ctx.devices["xy_field"]
    original = signal.set_output
    def fail(value, *, channel=1):
        if not value and channel == ctx.channels["y_rf"]:
            raise OSError("模拟 RF 关闭失败")
        return original(value, channel=channel)
    monkeypatch.setattr(signal, "set_output", fail)
    with pytest.raises(RuntimeError, match="安全收尾失败"):
        ctx.execute(["A06"])
    with pytest.raises(RuntimeError, match="安全收尾失败"):
        ctx.finish()
    config = read_config(ctx.directory.root)
    assert config["completion_status"] == "failed"
    assert config["safety_shutdown"]["completed"] is False
    assert config["main_field_restore"]["success"]


def test_analysis_only_parameter_update_keeps_grid_before_sealing(rig):
    ctx = workflow.Workflow(parameters(x_field_start_v=0., x_field_stop_v=0., x_field_points=1,
        y_field_start_v=0., y_field_stop_v=0., y_field_points=1))
    ctx.execute(through("A05"))
    previous = ctx.directory.root
    before = hashes(previous / "raw")
    ctx.update_parameters({"Y_RF_NT_PER_VPP": 2000.})
    assert ctx.states["A05"]["status"] == "completed"
    ctx.execute(["A06"])
    assert ctx.directory.root != previous
    assert hashes(previous / "raw") == before == hashes(ctx.directory.raw)
    config = read_config(ctx.directory.root)
    assert config["parameters"]["Y_RF_NT_PER_VPP"] == 2000.
    assert config["grid_summary"]["completed_point_count"] == 1
    assert config["completion_status"] == "completed"
    ctx.finish()


def main():
    """测试子进程显式注入假仪器；生产 session 不导入本模块。"""
    from ...experiment_runtime import load_runtime_params
    from ..noise_spectrum_xy.session import serve
    root = Path(os.environ["MX_Z_XY_RF_TEST_ROOT"])
    payload = json.loads(os.environ["LAB_TYPED_PARAMETERS"])
    session_id = payload.pop("session_id")
    os.environ["LAB_TYPED_PARAMETERS"] = json.dumps(payload)
    monkeypatch = pytest.MonkeyPatch()
    install_rig(root, monkeypatch)
    ctx = workflow.Workflow(load_runtime_params(MxZOptimalControlXYRFSensitivityParams), session_id=session_id)
    if gate_path := os.environ.get("MX_Z_XY_RF_TEST_SHUTDOWN_GATE"):
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
