"""真实网格、共享 R 采集和写盘配假仪器；不连接实际设备。"""
from dataclasses import replace
import json
import os
from pathlib import Path
import time

import numpy as np
import pytest
import yaml

from ...common import WorkflowCancelled
from ..mx_y_rf_sensitivity import test_workflow as rf_tests
from ..mx_y_rf_sensitivity import acquisition_steps, _helpers
from . import workflow, acquisition, preparation, stages
from .models import MxYRFProbeDetuningOptimizationParams

ACQUISITION_CASES = {
    "mx-y-rf-probe-detuning-optimization": {"mode": "grouped", "cases": {
        "order": ["test_real_grid_order_slow_disk_and_last_row"],
        "slow_disk": ["test_real_grid_order_slow_disk_and_last_row"],
        "cancel": ["test_failure_preserves_prior_records_and_pzt"],
        "acquire_failure": ["test_failure_preserves_prior_records_and_pzt"],
        "restore_failure": ["test_restore_failure_rescues_current_payload"],
        "save_failure": ["test_save_failure_occurs_after_temperature_restore"],
        "partial_save": ["test_failure_preserves_prior_records_and_pzt", "test_restore_failure_rescues_current_payload"],
        "group_change": ["test_real_grid_order_slow_disk_and_last_row"],
        "last_group": ["test_real_grid_order_slow_disk_and_last_row"],
        "entrypoints": ["test_full_and_stepwise_rerun_preserve_science_and_prior_files"],
    }, "sources": [
        "lab_workflows/experiment_modules/mx_y_rf_probe_detuning_optimization/workflow.py",
        "lab_workflows/experiment_modules/mx_y_rf_probe_detuning_optimization/acquisition.py",
        "lab_workflows/experiment_modules/mx_y_rf_probe_detuning_optimization/preparation.py",
        "lab_workflows/experiment_modules/mx_y_rf_probe_detuning_optimization/session.py",
    ]},
}


def parameters():
    return replace(MxYRFProbeDetuningOptimizationParams(), y_rf_amp_points=21,
        noise_n_avg=2, noise_rate_sa_s=1000, noise_duration_s=0.256, response_duration_s=0.016,
        system_settle_s=0.001, temp_switch_on_lag_s=0, y_rf_nt_per_vpp=10,
        pzt_voltage_start_v=40, pzt_voltage_stop_v=50, pzt_voltage_points=2,
        probe_power_start_v=0.2, probe_power_stop_v=0.3, probe_power_points=2, pzt_settle_time_s=0)


class FakeDLC:
    def __init__(self, rig):
        self.rig = rig
        self.pzt, self.amplitude, self.enabled = 60.0, 10.0, True
        self.restoration_error = False
        self.serial_error = False

    def connect(self):
        self.rig.events.append("dlc_connect")

    def disconnect(self):
        self.rig.events.append("dlc_disconnect")

    def get_controller_serial(self):
        return "wrong" if self.serial_error else self.rig.mapping["probe_laser"]["controller_serial"]

    def get_laser_head_serial(self):
        return self.rig.mapping["probe_laser"]["laser_head_serial"]

    def get_system_health_code(self):
        return 0

    get_laser_health_code = get_system_health_code

    def get_system_health(self):
        return "OK"

    get_laser_health = get_system_health

    def get_interlock_open(self):
        return False

    def get_laser_enabled(self):
        return True

    get_emission = get_laser_enabled
    get_laser_emission = get_laser_enabled

    def get_pzt_voltage_v(self):
        return self.pzt

    def get_pzt_voltage_actual_v(self):
        return self.pzt + 0.01

    def get_scan_amplitude_vpp(self):
        return self.amplitude

    def get_scan_enabled(self):
        return self.enabled

    def set_pzt_voltage_v(self, value):
        if value == 60 and self.restoration_error:
            raise OSError("模拟 PZT 恢复失败")
        self.rig.events.append(f"pzt:{value:g}")
        self.pzt = value
        return value

    def set_scan_amplitude_vpp(self, value):
        self.amplitude = value
        return value

    def set_scan_enabled(self, value):
        self.enabled = value
        return value


def install_rig(root, monkeypatch, params=None):
    rig = rf_tests.install_rig(root, monkeypatch, params or parameters())
    rig.dlc = FakeDLC(rig)
    monkeypatch.setattr(preparation, "create_dlc_pro", lambda config: rig.dlc)
    for module in (workflow, acquisition, preparation):
        monkeypatch.setattr(module, "check_cancelled", _helpers.check_cancelled)
    monkeypatch.setattr(workflow, "find_project_root", lambda: root)
    return rig


@pytest.fixture
def rig(tmp_path, monkeypatch):
    rf_tests.copy_config(tmp_path)
    return install_rig(tmp_path, monkeypatch)


def through(last):
    ids = [stage.id for stage in stages.ACQUISITION if stage.id != stages.FINISH_ID]
    return ids[:ids.index(last) + 1]


def run_directory(rig):
    return next((rig.root / "data/Mx_Y_RF_Probe_Detuning_Optimization").iterdir())


def config(path):
    return yaml.safe_load((path / "experiment_config.yaml").read_text(encoding="utf-8"))


def assert_safe(rig):
    rf_tests.assert_safe(rig)
    assert (rig.dlc.pzt, rig.dlc.amplitude, rig.dlc.enabled) == (60.0, 10.0, True)
    for key, value in (("Pump_laser_power", rig.params.pump_laser_power_v), ("Probe_laser_power", rig.params.probe_laser_power_v)):
        mapping = rig.mapping[key]
        device = rig.generators[mapping["resource"]]
        assert device.dc[mapping["channel"]] == value
        assert device.output[mapping["channel"]]


def test_real_grid_order_slow_disk_and_last_row(rig, monkeypatch):
    from unittest.mock import Mock
    tec = Mock(side_effect=AssertionError("本实验不得连接 TEC"))
    monkeypatch.setattr(preparation.rf_preparation, "TECInstrument", tec)
    writer, text_writer = np.savez, Path.write_text

    def save(path, **payload):
        assert rig.enabled, "温控恢复前不能落盘"
        if "attempt" in str(path) or Path(path).name.startswith("noise_"):
            rig.events.append("save")
            time.sleep(0.001)
        writer(path, **payload)

    def write(path, *args, **kwargs):
        assert rig.enabled, "温控关闭窗口不能写配置或扫描清单"
        return text_writer(path, *args, **kwargs)

    monkeypatch.setattr(np, "savez", save)
    monkeypatch.setattr(Path, "write_text", write)
    path = workflow.run(rig.params)
    assert_safe(rig)
    for i, event in enumerate(rig.events):
        if event == "acquire":
            assert rig.events[i - 2:i] == ["off", "settle"]
            assert rig.events[i + 1:i + 3] == ["on", "save"]
    manifest = yaml.safe_load((path / "raw/point_manifest.yaml").read_text(encoding="utf-8"))
    assert manifest["acquisition_order"] == ["pzt_000_probe_000", "pzt_000_probe_001", "pzt_001_probe_001", "pzt_001_probe_000"]
    assert all(point["status"] == "completed" for point in manifest["points"].values())
    assert [event for event in rig.events if event.startswith("pzt:")] == ["pzt:40", "pzt:50", "pzt:60"]
    assert len(rig.captures) == 4 * (21 + 2)
    state = json.loads((path / "acquisition_state.json").read_text(encoding="utf-8"))
    assert state["steps"]["A08"]["status"] == "completed" and state["current_step"] is None
    assert config(path)["raw_format_version"] == 1
    assert config(path)["temperature_control"]["tec_connected"] is False
    tec.assert_not_called()


@pytest.mark.parametrize("failure", [WorkflowCancelled("模拟取消"), OSError("模拟读取失败")])
def test_failure_preserves_prior_records_and_pzt(rig, failure):
    rig.fault_index, rig.acquire_error = 1, failure
    with pytest.raises(type(failure)) as caught:
        workflow.run(rig.params)
    assert caught.value is failure
    assert_safe(rig)
    path = run_directory(rig)
    assert (path / "raw/points/pzt_000_probe_000/amplitude_0000_attempt_00.npz").is_file()
    assert not (path / "raw/points/pzt_000_probe_000/amplitude_0001_attempt_00.npz").exists()
    assert config(path)["completion_status"] == ("cancelled" if isinstance(failure, WorkflowCancelled) else "failed")


def test_restore_failure_rescues_current_payload(rig):
    rig.restore_error = True
    with pytest.raises(OSError, match="温控恢复失败"):
        workflow.run(rig.params)
    path = run_directory(rig)
    with np.load(path / "raw/points/pzt_000_probe_000/amplitude_0000_attempt_00.npz") as data:
        assert len(data["r_v"]) >= 8
    assert len(rig.captures) == 1
    assert not config(path)["safety_shutdown"]["completed"]
    assert rig.dlc.pzt == 60


def test_save_failure_occurs_after_temperature_restore(rig, monkeypatch):
    writer = np.savez

    def save(path, **payload):
        assert rig.enabled
        if "attempt" in str(path):
            raise OSError("模拟磁盘失败")
        writer(path, **payload)

    monkeypatch.setattr(np, "savez", save)
    with pytest.raises(OSError, match="磁盘失败"):
        workflow.run(rig.params)
    assert_safe(rig)
    assert len(rig.captures) == 1


def test_full_and_stepwise_rerun_preserve_science_and_prior_files(rig):
    full = workflow.run(rig.params)
    raw_before = {p.relative_to(full): p.read_bytes() for p in full.rglob("*") if p.is_file()}
    rig.captures.clear()  # 相同假噪声种子，比较完整与分步的科学数组。
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A06"))
    ctx.execute(["A07", "A08"])
    stepwise = ctx.directory.root
    assert stepwise != full
    for key in ("pzt_000_probe_000", "pzt_001_probe_000"):
        for filename in ("amplitude_scan.npz",):
            with np.load(full / "raw/points" / key / filename) as a, np.load(stepwise / "raw/points" / key / filename) as b:
                for name in a.files:
                    np.testing.assert_array_equal(a[name], b[name])
    with pytest.raises(ValueError, match="重新准备"):
        ctx.execute(["A07"])
    ctx.execute(["A06", "A07", "A08"])
    assert ctx.directory.root not in (full, stepwise)
    ctx.finish()
    assert {p.relative_to(full): p.read_bytes() for p in full.rglob("*") if p.is_file()} == raw_before
    assert_safe(rig)


def test_dependency_preflight_and_visible_parameter_ownership(rig):
    ctx = workflow.Workflow(rig.params)
    with pytest.raises(ValueError, match="前置"):
        ctx.execute(["A07"])
    assert "connect" not in rig.events
    ctx.execute(through("A06"))
    before = ctx.snapshot()
    ctx.execute(["A01"])
    assert ctx.states["A06"] == before["steps"]["A06"]
    shown = {name for column in ("acquisition", "analysis") for stage in stages.definition()[column] for name in stage["display_parameters"]}
    assert shown == {field["name"] for field in ctx.params.schema(ctx.root)["fields"]}
    for column in ("acquisition", "analysis"):
        assert stages.definition()["parameter_impacts"][column] == stages.parameter_impacts(getattr(stages, column.upper()))
    acquisition_impacts = stages.parameter_impacts(stages.ACQUISITION)
    for keys in (("RESPONSE_TIME_CONSTANT_S",), ("PZT_SETTLE_TIME_S", "PUMP_CARRIER_AMPLITUDE_VPP")):
        fresh = workflow.Workflow(rig.params)
        fresh.execute(through("A06"))
        old = deepcopy_states = {key: dict(value) for key, value in fresh.states.items()}
        fresh.update_parameters({key: fresh.params.to_external()[key] * 0.9 + (0.01 if key == "PZT_SETTLE_TIME_S" else 0) for key in keys})
        expected = {item for key in keys for item in acquisition_impacts[key]}
        assert {key for key in old if fresh.states[key] != deepcopy_states[key]} == {key for key in expected if old[key]["status"] != "pending"}
        fresh.finish()
    ctx.finish()


@pytest.mark.parametrize("fault", ["identity", "clock", "pzt_restore"])
def test_device_preparation_and_restoration_failure_is_not_success(rig, fault):
    if fault == "identity":
        rig.dlc.serial_error = True
    elif fault == "clock":
        rig.clock_error = True
    else:
        rig.dlc.restoration_error = True
    with pytest.raises((RuntimeError, OSError, ValueError, AssertionError)):
        workflow.run(rig.params)
    assert config(run_directory(rig))["completion_status"] == "failed"
    if fault == "pzt_restore":
        assert not config(run_directory(rig))["safety_shutdown"]["completed"]
    else:
        assert not rig.captures


def test_unexecuted_parameter_apply_does_not_change_safe_baseline(rig):
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A07"))
    parameters_before = config(ctx.directory.root)["parameters"]
    ctx.update_parameters({"FIXED_PARAMS.Pump_laser_power": 0.25, "FIXED_PARAMS.Probe_laser_power": 0.35,
        "PUMP_CARRIER_AMPLITUDE_VPP": 0.1, "PUMP_CARRIER_FREQUENCY_HZ": 99e6, "RESPONSE_RATE_SA_S": 2000})
    ctx.finish()
    assert_safe(rig)
    assert config(ctx.directory.root)["parameters"] == parameters_before
    pump = rig.mapping["Pump_modulation"]
    assert rig.generators[pump["resource"]].frequency[pump["channel"]] == rig.params.pump_carrier_frequency_hz


def test_model_old_waits_single_probe_and_saved_defaults_are_preserved(rig):
    values = rig.params.to_external()
    values.pop("SYSTEM_SETTLE_S")
    params = MxYRFProbeDetuningOptimizationParams.from_external(values | {"RESPONSE_SETTLE_TIME_S": 0.5, "TEMP_SWITCH_OFF_LEAD_S": 0.2}, schema_version=1)
    assert params.system_settle_s == 0.5
    explicit = MxYRFProbeDetuningOptimizationParams.from_external(values | {"SYSTEM_SETTLE_S": 0.7, "RESPONSE_SETTLE_TIME_S": 0.5}, schema_version=1)
    assert explicit.system_settle_s == 0.7
    single = replace(rig.params, probe_power_start_v=0.2, probe_power_stop_v=0.2, probe_power_points=1)
    assert not single.validate(rig.root)
    assert replace(single, probe_power_stop_v=0.3).validate(rig.root)


def test_real_r_quality_retry_exhaustion_preserves_attempts_and_continues_safely(rig, monkeypatch):
    reader = acquisition_steps.acquire_r

    def noisy(*args, **kwargs):
        payload = reader(*args, **kwargs)
        if len(rig.captures) <= rig.params.r_point_max_attempts:
            payload["r"] = np.linspace(0, 1, len(payload["r"]))
        return payload

    monkeypatch.setattr(acquisition_steps, "acquire_r", noisy)
    path = workflow.run(rig.params)
    manifest = yaml.safe_load((path / "raw/point_manifest.yaml").read_text(encoding="utf-8"))
    assert manifest["points"]["pzt_000_probe_000"]["status"] == "invalid_quality"
    assert sum(record["status"] == "completed" for record in manifest["points"].values()) == 3
    assert len(list((path / "raw/points/pzt_000_probe_000").glob("amplitude_0000_attempt_*.npz"))) == rig.params.r_point_max_attempts
    assert_safe(rig)


@pytest.mark.parametrize("failure", [WorkflowCancelled("噪声取消"), OSError("噪声读取失败")])
def test_noise_second_segment_failure_keeps_response_and_prior_noise(rig, failure):
    rig.fault_index = rig.params.y_rf_amp_points + 1
    rig.acquire_error = failure
    with pytest.raises(type(failure)) as caught:
        workflow.run(rig.params)
    assert caught.value is failure
    point = run_directory(rig) / "raw/points/pzt_000_probe_000"
    assert (point / "amplitude_scan.npz").is_file()
    assert (point / "noise_000.npz").is_file()
    assert not (point / "noise_001.npz").exists()
    assert_safe(rig)


def test_mapping_drift_rejected_and_tec_mapping_is_not_required(rig, monkeypatch):
    path = rig.root / "params/mapping.yaml"
    mapping = yaml.safe_load(path.read_text(encoding="utf-8"))
    mapping["mapping"].pop("temperature")
    path.write_text(yaml.safe_dump(mapping, allow_unicode=True), encoding="utf-8")
    from .definition import ADAPTER
    monkeypatch.setattr(ADAPTER, "root", rig.root)
    assert ADAPTER.preflight(rig.params.to_external()) == []
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A06"))
    mapping["mapping"]["probe_laser"]["label"] = "已修改映射修订的 Probe"
    path.write_text(yaml.safe_dump(mapping, allow_unicode=True), encoding="utf-8")
    with pytest.raises(RuntimeError, match="已改变"):
        ctx.execute(["A07"])
    ctx.finish()
    assert_safe(rig)


if __name__ == "__main__":
    from .session import main
    with pytest.MonkeyPatch.context() as patch:
        root = Path(os.environ["PROBE_DETUNING_TEST_ROOT"])
        payload = json.loads(os.environ.get("LAB_TYPED_PARAMETERS", "{}"))
        params = MxYRFProbeDetuningOptimizationParams.from_external({key: value for key, value in payload.items() if key != "session_id"})
        install_rig(root, patch, params)
        main()
