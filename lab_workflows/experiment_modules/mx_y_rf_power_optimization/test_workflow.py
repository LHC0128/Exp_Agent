"""光功率网格真实业务配假仪器：时序、故障、重跑和收尾回归。"""
from dataclasses import replace
from pathlib import Path
import json
import os
import time
import numpy as np
import pytest
import yaml
from ...common import WorkflowCancelled
from ..mx_y_rf_sensitivity.test_workflow import copy_config, install_rig, assert_safe
from .models import MxYRFPowerOptimizationParams
from . import workflow, acquisition, preparation, stages

REGRESSION_CASES = {
    "mx-y-rf-power-optimization": {
        "parameters": {
            "sources": ["lab_workflows/experiment_modules/mx_y_rf_power_optimization/models.py",
                        "params/experiments/mx-y-rf-power-optimization.yaml"],
            "tests": ["test_actual_defaults_roundtrip_and_unsafe_yaml_rejected_before_connection"],
        },
    },
}


ACQUISITION_CASES = {
    "mx-y-rf-power-optimization": {"mode": "grouped", "cases": {
        "order": ["test_real_serpentine_order_and_slow_disk"],
        "slow_disk": ["test_real_serpentine_order_and_slow_disk"],
        "group_change": ["test_real_serpentine_order_and_slow_disk"],
        "last_group": ["test_real_serpentine_order_and_slow_disk"],
        "cancel": ["test_failure_keeps_partial_files_and_original_error"],
        "acquire_failure": ["test_failure_keeps_partial_files_and_original_error"],
        "restore_failure": ["test_restore_failure_rescues_current_read"],
        "save_failure": ["test_save_failure_occurs_after_restore"],
        "partial_save": ["test_failure_keeps_partial_files_and_original_error", "test_restore_failure_rescues_current_read"],
        "entrypoints": ["test_full_stepwise_rerun_and_parameter_snapshot"],
    }, "sources": [
        "lab_workflows/experiment_modules/mx_y_rf_power_optimization/workflow.py",
        "lab_workflows/experiment_modules/mx_y_rf_power_optimization/acquisition.py",
        "lab_workflows/experiment_modules/mx_y_rf_power_optimization/preparation.py",
        "lab_workflows/experiment_modules/mx_y_rf_sensitivity/acquisition_steps.py",
        "lab_workflows/experiment_modules/mx_y_rf_sensitivity/preparation.py",
        "lab_workflows/experiment_modules/mx_y_rf_sensitivity/excitation.py",
    ]},
}


def parameters():
    return replace(MxYRFPowerOptimizationParams(), pump_power_points=2, probe_power_points=2,
        y_rf_amp_points=21, noise_n_avg=12, noise_rate_sa_s=1000, noise_duration_s=4.096,
        response_duration_s=0.016, system_settle_s=0.001, temp_switch_on_lag_s=0, y_rf_nt_per_vpp=100)


def install_power_rig(root, patch):
    rig = install_rig(root, patch, parameters())
    patch.setattr(workflow, "find_project_root", lambda: root)
    synchronize = preparation.synchronize_connected_clocks
    patch.setattr(preparation, "synchronize_connected_clocks", lambda *args, **kwargs:
        synchronize(*args, **(kwargs | {"settle_wait": lambda seconds: None})))
    from ..mx_y_rf_sensitivity import acquisition_steps
    patch.setattr(acquisition, "check_cancelled", acquisition_steps.check_cancelled)
    patch.setattr(workflow, "check_cancelled", acquisition_steps.check_cancelled)
    return rig


@pytest.fixture
def rig(tmp_path, monkeypatch):
    copy_config(tmp_path)
    return install_power_rig(tmp_path, monkeypatch)


def through(last="A09"):
    ids = [stage.id for stage in stages.ACQUISITION if stage.id != stages.FINISH_ID]
    return ids[:ids.index(last) + 1]


def assert_powers(rig, params):
    for key, value in (("Pump_laser_power", params.pump_laser_power_v), ("Probe_laser_power", params.probe_laser_power_v)):
        config = rig.mapping[key]
        source = rig.generators[config["resource"]]
        assert source.output[config["channel"]]
        assert source.dc[config["channel"]] == value


def test_real_serpentine_order_and_slow_disk(rig, monkeypatch):
    writer = np.savez
    def save(path, **data):
        assert rig.enabled, "温控未恢复就写盘"
        if "attempt" in str(path) or Path(path).name.startswith("noise_"):
            rig.events.append("save")
            time.sleep(0.001)
        writer(path, **data)
    monkeypatch.setattr(np, "savez", save)
    directory = workflow.run(rig.params)
    assert_safe(rig)
    assert_powers(rig, rig.params)
    for i, event in enumerate(rig.events):
        if event == "acquire":
            assert rig.events[i - 2:i] == ["off", "settle"]
            assert rig.events[i + 1] == "on"
    manifest = yaml.safe_load((directory / "raw/point_manifest.yaml").read_text(encoding="utf-8"))
    assert manifest["acquisition_order"] == ["pump_000_probe_000", "pump_000_probe_001", "pump_001_probe_001", "pump_001_probe_000"]
    assert all(record["status"] == "completed" for record in manifest["points"].values())
    assert len(rig.captures) == 4 * (21 + rig.params.noise_n_avg)
    assert len(list((directory / "raw/points").glob("*/noise_000.npz"))) == 4
    config = yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf-8"))
    assert config["grid_summary"]["completed_point_count"] == 4
    assert config["raw_format_version"] == 1 and config["schema_version"] == 2
    assert config["temperature_control"]["actual_temperature_c"] is None
    state = json.loads((directory / "acquisition_state.json").read_text(encoding="utf-8"))
    assert state["steps"]["A09"]["status"] == "completed"


@pytest.mark.parametrize("cancel", [False, True])
def test_failure_keeps_partial_files_and_original_error(rig, cancel):
    failure = WorkflowCancelled("模拟取消") if cancel else OSError("模拟读取失败")
    rig.fault_index, rig.acquire_error = 2, failure
    ctx = workflow.Workflow(rig.params)
    with pytest.raises(type(failure)) as caught:
        try:
            ctx.execute(through())
        finally:
            ctx.finish(failure)
    assert caught.value is failure
    assert_safe(rig)
    assert_powers(rig, rig.params)
    files = list(ctx.directory.raw.rglob("*attempt*.npz"))
    assert len(files) == 2
    with np.load(files[0]) as data:
        assert data["r_v"].size >= 8
    config = yaml.safe_load(ctx.directory.config_path.read_text(encoding="utf-8"))
    assert config["completion_status"] == ("cancelled" if cancel else "failed")
    assert str(failure) in config["failure_reason"]
    manifest = yaml.safe_load((ctx.directory.raw / "point_manifest.yaml").read_text(encoding="utf-8"))
    assert list(manifest["points"].values())[0]["status"] == "failed"
    assert list(manifest["points"].values())[1]["status"] == "pending"


def test_restore_failure_rescues_current_read(rig, monkeypatch):
    rig.restore_error = True
    writer = np.savez
    writes = []
    def save(path, **data):
        if "attempt" in str(path):
            assert rig.events[-1] == "on", "先尝试恢复温控再抢救内存"
            writes.append(path)
        writer(path, **data)
    monkeypatch.setattr(np, "savez", save)
    ctx = workflow.Workflow(rig.params)
    with pytest.raises(Exception, match="温控恢复失败") as caught:
        ctx.execute(through())
    ctx.finish(caught.value)
    assert len(writes) == 1
    with np.load(writes[0]) as data:
        assert data["r_v"].size >= 8
    assert not ctx.state["safety_shutdown"]["completed"]
    assert ctx.states[stages.FINISH_ID]["status"] == "failed"
    assert_powers(rig, rig.params)


def test_save_failure_occurs_after_restore(rig, monkeypatch):
    writer = np.savez
    failure = OSError("模拟磁盘失败")
    def save(path, **data):
        assert rig.enabled
        if "amplitude_0001_attempt" in str(path):
            raise failure
        writer(path, **data)
    monkeypatch.setattr(np, "savez", save)
    ctx = workflow.Workflow(rig.params)
    with pytest.raises(OSError) as caught:
        ctx.execute(through())
    ctx.finish(caught.value)
    assert caught.value is failure
    assert len(list(ctx.directory.raw.rglob("*attempt*.npz"))) == 1
    assert_safe(rig)


def test_full_stepwise_rerun_and_parameter_snapshot(rig):
    full = workflow.run(rig.params)
    ctx = workflow.Workflow(rig.params)
    with pytest.raises(ValueError, match="前置"):
        ctx.execute(["A08"])
    ctx.execute(through("A07"))
    ctx.execute(["A08", "A09"])
    first, frozen = ctx.directory.root, ctx.directory.config_path.read_bytes()
    with np.load(full / "raw/power_grid.npz") as left, np.load(first / "raw/power_grid.npz") as right:
        for key in left.files:
            np.testing.assert_array_equal(left[key], right[key])
    with pytest.raises(ValueError, match="前置"):
        ctx.execute(["A08"])
    ctx.execute(["A07", "A08", "A09"])
    assert ctx.directory.root != first
    assert (first / "experiment_config.yaml").read_bytes() == frozen
    ctx.update_parameters({"FIXED_PARAMS.Pump_laser_power": 0.4, "PUMP_CARRIER_FREQUENCY_HZ": 99e6})
    ctx.finish()
    assert (first / "experiment_config.yaml").read_bytes() == frozen
    assert_powers(rig, rig.params)
    pump = rig.mapping["Pump_modulation"]
    assert rig.generators[pump["resource"]].frequency[pump["channel"]] == rig.params.pump_carrier_frequency_hz


def test_parameter_impacts_preflight_and_analysis_only_draft_do_not_lose_raw(rig):
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A08"))
    path, raw = ctx.directory.root, (ctx.directory.raw / "power_grid.npz").read_bytes()
    ctx.execute(["A01"])
    assert ctx.states["A08"]["status"] == "completed"
    ctx.update_parameters({"LOW_FREQ_SKIP_HZ": 7})
    assert ctx.states["A08"]["status"] == "completed"
    ctx.execute(["A09"])
    assert ctx.directory.root == path
    assert (ctx.directory.raw / "power_grid.npz").read_bytes() == raw
    assert yaml.safe_load(ctx.directory.config_path.read_text(encoding="utf-8"))["parameters"]["LOW_FREQ_SKIP_HZ"] == 3
    ctx.finish()


def test_safety_failure_does_not_skip_laser_restore_or_hide_primary(rig, monkeypatch):
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A07"))
    error = OSError("模拟读数失败")
    def fail(*args, **kwargs):
        raise OSError("模拟辅助场收尾失败")
    monkeypatch.setattr(preparation, "safe_shutdown", fail)
    ctx.finish(error)
    assert_powers(rig, rig.params)
    assert not ctx.state["safety_shutdown"]["completed"]
    assert str(error) in ctx.state["failure_reason"]


def test_clock_failure_closes_outputs_before_disconnect(rig):
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A07"))
    rig.clock_error = True
    with pytest.raises(Exception) as caught:
        ctx.execute(["A03"])
    ctx.finish(caught.value)
    assert_safe(rig)
    assert ctx.closed and "disconnect" in rig.events


def test_real_bad_r_point_is_saved_and_only_safe_quality_failure_continues(rig, monkeypatch):
    from ..mx_y_rf_sensitivity import acquisition as r_acquisition
    reader = r_acquisition.daq.acquire_data
    def noisy_first(*args, **kwargs):
        result = reader(*args, **kwargs)
        if len(rig.captures) == 1:
            result[0].values[:] += np.linspace(0, 1, result[0].values.size)
        return result
    monkeypatch.setattr(r_acquisition.daq, "acquire_data", noisy_first)
    rig.params = replace(rig.params, r_point_max_attempts=1)
    directory = workflow.run(rig.params)
    manifest = yaml.safe_load((directory / "raw/point_manifest.yaml").read_text(encoding="utf-8"))
    records = list(manifest["points"].values())
    assert records[0]["status"] == "invalid_quality"
    assert all(record["status"] == "completed" for record in records[1:])
    with np.load(directory / "raw/points/pump_000_probe_000/amplitude_0000_attempt_00.npz") as data:
        assert not data["quality_accepted"]
    assert_safe(rig)


def test_manifest_replace_failure_keeps_previous_complete_file(tmp_path, monkeypatch):
    path = tmp_path / "manifest.yaml"
    acquisition._write_manifest(path, {"points": {"old": "complete"}})
    previous = path.read_bytes()
    replace_file = Path.replace
    def fail(source, target):
        if source.name == "manifest.yaml.tmp":
            raise PermissionError("模拟 Windows 文件占用")
        return replace_file(source, target)
    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(PermissionError):
        acquisition._write_manifest(path, {"points": {"new": "running"}})
    assert path.read_bytes() == previous
    assert not path.with_suffix(".yaml.tmp").exists()


def test_visible_parameters_and_impacts_use_actual_schema():
    from .definition import ADAPTER
    fields = {f["name"] for f in ADAPTER.schema()["fields"]}
    flow = stages.definition()
    displayed = {key for stage in flow["acquisition"] + flow["analysis"] for key in stage["display_parameters"]}
    assert fields <= displayed
    assert flow["parameter_impacts"]["acquisition"]["SYSTEM_SETTLE_S"] == ["A08", "A09"]
    assert flow["parameter_impacts"]["analysis"]["R_BAD_POINT_STD_THRESHOLD_V"] == ["B02", "B03"]


def test_actual_defaults_roundtrip_and_unsafe_yaml_rejected_before_connection(tmp_path):
    root = Path(__file__).resolve().parents[3]
    params = MxYRFPowerOptimizationParams.from_yaml(root / "params/experiments/mx-y-rf-power-optimization.yaml")
    assert params.validate(root) == []
    saved = tmp_path / "defaults.yaml"
    params.save_yaml(saved)
    assert MxYRFPowerOptimizationParams.from_yaml(saved).to_external() == params.to_external()
    unsafe = replace(params, y_rf_amp_start_vpp=-3.0, y_rf_amp_stop_vpp=3.0)
    unsafe.save_yaml(saved)
    rejected = MxYRFPowerOptimizationParams.from_yaml(saved)
    with pytest.raises(ValueError, match="rf_coil|安全"):
        preparation.preflight(rejected, root)


if __name__ == "__main__":
    with pytest.MonkeyPatch.context() as patch:
        install_power_rig(Path(os.environ["MX_Y_RF_POWER_TEST_ROOT"]), patch)
        if gate_name := os.environ.get("MX_Y_RF_POWER_SHUTDOWN_GATE"):
            gate = Path(gate_name)
            shutdown = preparation.shutdown
            def gated_shutdown(*args, **kwargs):
                gate.with_suffix(".started").touch()
                deadline = time.monotonic() + 30
                while not gate.exists():
                    if time.monotonic() > deadline:
                        raise TimeoutError("测试未释放安全收尾")
                    time.sleep(0.01)
                result = shutdown(*args, **kwargs)
                gate.with_suffix(".finished").write_text(json.dumps(result), encoding="utf-8")
                return result
            patch.setattr(preparation, "shutdown", gated_shutdown)
        payload = json.loads(os.environ.get("LAB_TYPED_PARAMETERS", "{}"))
        if "session_id" in payload:
            from .session import main
        else:
            from .workflow import main
        main()
