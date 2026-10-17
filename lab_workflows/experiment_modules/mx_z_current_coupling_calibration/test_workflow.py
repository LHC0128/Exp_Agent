"""本 ID 的真实联合采集、配置与 writer 配假仪器回归。"""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import builtins
import io
import time
import json
import numpy as np
import pytest
import yaml
from sds_acquisition import SDSAcquisition
from ...common import WorkflowCancelled
from ..mx_z_field_calibration import preparation as shared_preparation
from ..mx_z_field_calibration.test_workflow import (
    copy_config, install_rig as install_field_rig, small_params as field_params, assert_safe,
)
from . import workflow, preparation, acquisition
from .models import MxZCurrentCouplingCalibrationParams
from .stages import ACQUISITION, FINISH_ID, RUN_ID, ANALYSIS_KEYS, definition


# 每个 Z 的 SDS 电流窗和每个频点/重采的 R 窗均独立；没有 OFF/ON 配对。
ACQUISITION_CASES = {
    "mx-z-current-coupling-calibration": {"mode": "single", "cases": {
        "order": ["test_joint_six_step_order_slow_disk_bidirectional_and_last_z"],
        "slow_disk": ["test_joint_six_step_order_slow_disk_bidirectional_and_last_z"],
        "cancel": ["test_hf2_or_sds_failure_and_cancel_keep_completed_groups"],
        "acquire_failure": ["test_hf2_or_sds_failure_and_cancel_keep_completed_groups"],
        "restore_failure": ["test_restore_failure_rescues_last_read_payload"],
        "save_failure": ["test_real_writer_failure_and_secondary_errors_preserve_original"],
        "partial_save": ["test_hf2_or_sds_failure_and_cancel_keep_completed_groups",
            "test_restore_failure_rescues_last_read_payload", "test_autorange_second_read_failure_rescues_first_frame"],
        "entrypoints": ["test_full_stepwise_rerun_and_immutable_raw"],
    }, "sources": [
        "lab_workflows/experiment_modules/mx_z_current_coupling_calibration/workflow.py",
        "lab_workflows/experiment_modules/mx_z_current_coupling_calibration/acquisition.py",
        "lab_workflows/experiment_modules/mx_z_current_coupling_calibration/preparation.py",
        "lab_workflows/experiment_modules/mx_z_current_coupling_calibration/excitation.py",
        "lab_workflows/experiment_modules/mx_z_field_calibration/preparation.py",
        "lab_workflows/experiment_modules/mx_z_field_calibration/excitation.py",
        "lab_workflows/experiment_modules/mx_z_field_calibration/acquisition.py",
    ]},
}


def small_params():
    return replace(MxZCurrentCouplingCalibrationParams.from_external(field_params().to_external()),
        sense_resistor_ohm=10, sense_resistor_power_rating_w=2, maximum_current_a=.2,
        sense_scope_sample_rate_sa_s=64000, sense_scope_duration_s=.002,
        sense_scope_initial_scale_v_div=.2, current_settle_time_s=0,
        sense_scope_auto_offset_tolerance_fraction=1, bidirectional_scan=True)


class FakeScope:
    def __init__(self, rig):
        self.rig, self.rate, self.scale, self.offset = rig, 64000, .2, 0
        self.mode, self.status, self.memory = "AUTO", "STOP", "FSRate"

    def connect(self): self.rig.events.append("connect_sds")
    def disconnect(self): self.rig.events.append("disconnect_sds")
    def trigger_run(self): self.status = "RUN"
    def trigger_stop(self): self.status = "STOP"
    def trigger_status(self): return self.status
    def set_trigger_mode(self, mode): self.mode = "AUTO" if mode == "FTRIG" else mode
    def get_trigger_mode(self): return self.mode
    def set_memory_management(self, value): self.memory = value
    def get_memory_management(self): return self.memory
    def set_sampling_rate(self, value): self.rate = value
    def get_sampling_rate(self): return self.rate
    def get_actual_points(self): return 128
    def get_memory_depth(self): return "128"
    def set_channel_scale(self, channel, value): self.scale = value
    def get_channel_scale(self, channel): return self.scale
    def set_channel_offset(self, channel, value): self.offset = value
    def get_channel_offset(self, channel): return self.offset

    def __getattr__(self, name):
        if name.startswith("iter_"):
            raise AttributeError(name)
        if name.startswith("set_"):
            def setter(*args, **kwargs):
                assert self.rig.enabled, "初始 SDS 配置必须在温控开启时"
                self.rig.events.append("scope_configure")
            return setter
        raise AttributeError(name)


def install_rig(root, monkeypatch, params=None, *, progress=False):
    rig = install_field_rig(root, monkeypatch, params or small_params(), progress=progress)
    rig.sense_captures, rig.sense_error_index, rig.sense_error = [], None, None
    rig.sense_voltage, rig.restore_after_sense = None, False
    rig.scope = FakeScope(rig)
    for generator in rig.generators.values():
        original_sine = generator.setup_sine
        def sine(*args, channel=1, _generator=generator, _original=original_sine, **kwargs):
            _generator.dc.pop(channel, None)
            return _original(*args, channel=channel, **kwargs)
        monkeypatch.setattr(generator, "setup_sine", sine)
    monkeypatch.setattr(shared_preparation, "SDSInstrument", lambda resource: rig.scope)
    monkeypatch.setattr(workflow, "find_project_root", lambda: root)
    monkeypatch.setattr(workflow, "check_cancelled", acquisition.r_points.check_cancelled)
    if not progress:
        monkeypatch.setattr(workflow, "report_runtime_progress", lambda *args, **kwargs: None)
    check = acquisition.r_points.check_cancelled

    def wait(seconds):
        if seconds == rig.params.system_settle_s:
            rig.events.append("settle")
        check()

    monkeypatch.setattr(acquisition, "check_cancelled", check)
    monkeypatch.setattr(acquisition, "_sleep", wait)
    monkeypatch.setattr(acquisition, "_uncancellable_sleep", lambda seconds: None)
    real_switch = acquisition.set_temperature_switch

    def switch(device, enabled, **kwargs):
        if enabled and rig.restore_after_sense and rig.sense_captures:
            rig.events.append("restore_attempt")
            raise OSError("模拟 SDS 后温控恢复失败")
        return real_switch(device, enabled, **kwargs)

    monkeypatch.setattr(acquisition, "set_temperature_switch", switch)

    def read_scope(acquirer, channel, *_args, **kwargs):
        assert channel == 3 and rig.scope.status == "STOP" and not rig.enabled
        check()
        index = len(rig.sense_captures)
        rig.sense_captures.append(index)
        rig.events.append("sense")
        if index == rig.sense_error_index:
            raise rig.sense_error or OSError("模拟 SDS 读取失败")
        z = rig.mapping["Z_magnetic_field"]
        bias = rig.generators[z["resource"]].dc[z["channel"]]
        voltage = np.full(128, bias * .1 + .01 if rig.sense_voltage is None else rig.sense_voltage)
        return SimpleNamespace(time=np.arange(128) / 64000, voltage=voltage, preamble_dict={"point_num": 128})

    monkeypatch.setattr(SDSAcquisition, "acquire_channel", read_scope)
    return rig


@pytest.fixture
def rig(tmp_path, monkeypatch):
    copy_config(tmp_path)
    return install_rig(tmp_path, monkeypatch)


def through(last):
    ids = [stage.id for stage in ACQUISITION if stage.id != FINISH_ID]
    return ids[:ids.index(last) + 1]


def run_directory(rig):
    return next((rig.root / "data" / workflow.DATA_TYPE).iterdir())


def test_joint_six_step_order_slow_disk_bidirectional_and_last_z(rig, monkeypatch):
    original = np.savez
    open_file = builtins.open
    writes = []

    def guarded_open(file, mode="r", *args, **kwargs):
        if isinstance(file, (str, Path)) and any(flag in mode for flag in "wax+"):
            path = Path(file).resolve()
            if path.is_relative_to(rig.root / "data"):
                assert rig.enabled, f"恢复温控前不能写入 {path.name}"
                writes.append(path.name)
        return open_file(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded_open)
    monkeypatch.setattr(io, "open", guarded_open)

    def save(path, **data):
        assert rig.enabled, "恢复温控前不能写盘"
        name = Path(path).name
        if name == "sense_current.npz" or "attempt" in name:
            rig.events.append("save_sense" if name == "sense_current.npz" else "save_r")
            time.sleep(.001)
        return original(path, **data)

    monkeypatch.setattr(np, "savez", save)
    directory = workflow.run(rig.params)
    assert_safe(rig)
    trace = [item for item in rig.events if item in {"off", "settle", "sense", "acquire", "on", "save_sense", "save_r"}]
    expected = []
    for _ in range(5):
        expected.extend(["off", "settle", "sense", "on", "save_sense"])
        expected.extend(["off", "settle", "acquire", "on", "save_r"] * 9)
    assert trace[trace.index("off"): -1] == expected
    assert len(rig.captures) == 45 and len(rig.sense_captures) == 5
    with np.load(directory / "raw/z_scan_index.npz") as index:
        np.testing.assert_array_equal(index["z_bias_v"], [-1, 0, 1, 0, -1])
        np.testing.assert_array_equal(index["scan_direction"], ["forward"] * 3 + ["reverse"] * 2)
    with np.load(directory / "raw/z_004/frequency_scan.npz") as last:
        assert last["scan_direction"] == "reverse" and len(last["frequency_hz"]) == 9
        assert float(last["current_mean_a"]) == pytest.approx(-.009)
    with np.load(directory / "raw/z_004/sense_current.npz") as data:
        assert float(data["actual_rate_sa_s"]) == pytest.approx(64000)
        np.testing.assert_allclose(data["sense_voltage_v"] / rig.params.sense_resistor_ohm, data["current_a"])
    config = yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf-8"))
    assert config["completion_status"] == "completed" and config["raw_format_version"] == 1
    assert config["safety_shutdown"]["completed"]
    state = json.loads((directory / "acquisition_state.json").read_text(encoding="utf-8"))
    assert state["status"] == "completed" and state["steps"]["A10"]["status"] == "completed"
    assert config["temperature_control"]["actual_temperature_c"] is None
    assert rig.tec.targets == [rig.params.temperature_c]
    assert {"experiment_config.yaml", "acquisition_state.json.tmp", "z_scan_index.npz"}.issubset(writes)


@pytest.mark.parametrize("source", ["hf2", "sds"])
@pytest.mark.parametrize("cancel", [False, True])
def test_hf2_or_sds_failure_and_cancel_keep_completed_groups(rig, source, cancel):
    error = WorkflowCancelled("模拟取消") if cancel else OSError("模拟联合读取失败")
    if source == "hf2":
        rig.fault_index, rig.acquire_error = 10, error
    else:
        rig.sense_error_index, rig.sense_error = 1, error
    with pytest.raises(type(error)) as caught:
        workflow.run(rig.params)
    assert caught.value is error
    assert_safe(rig)
    directory = run_directory(rig)
    assert (directory / "raw/z_000/frequency_scan.npz").exists()
    assert (directory / "raw/z_000/sense_current.npz").exists()
    assert not (directory / "raw/z_001/frequency_scan.npz").exists()
    if source == "hf2":
        assert (directory / "raw/z_001/sense_current.npz").exists()
        assert (directory / "raw/z_001/frequency_0000_attempt_00.npz").exists()
    assert not (directory / "raw/z_002").exists()
    assert not (directory / "raw/z_scan_index.npz").exists()


@pytest.mark.parametrize("source", ["hf2", "sds"])
def test_restore_failure_rescues_last_read_payload(rig, source):
    if source == "hf2":
        rig.restore_error = True
    else:
        rig.restore_after_sense = True
    with pytest.raises(OSError, match="温控恢复失败"):
        workflow.run(rig.params)
    directory = run_directory(rig)
    filename = "frequency_0000_attempt_00.npz" if source == "hf2" else "sense_current.npz"
    with np.load(directory / "raw/z_000" / filename) as data:
        assert len(data["r_v"] if source == "hf2" else data["sense_voltage_v"]) >= 8
    if source == "sds":
        assert "restore_attempt" in rig.events and not rig.captures
    config = yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf-8"))
    assert config["completion_status"] == "failed"


@pytest.mark.parametrize("source", ["hf2", "sds"])
def test_real_writer_failure_and_secondary_errors_preserve_original(rig, monkeypatch, source):
    original = np.savez
    error = OSError("模拟真实 writer 失败")

    def save(path, **data):
        assert rig.enabled
        if (source == "sds" and Path(path).name == "sense_current.npz") or (source == "hf2" and "attempt" in str(path)):
            raise error
        return original(path, **data)

    monkeypatch.setattr(np, "savez", save)
    with pytest.raises(OSError) as caught:
        workflow.run(rig.params)
    assert caught.value is error
    assert_safe(rig)


def test_acquisition_error_survives_secondary_restore_and_state_write_errors(rig, monkeypatch):
    error = OSError("原始 HF2 读取失败")
    rig.fault_index, rig.acquire_error = 1, error
    class RestoreAfterFirst:
        def __bool__(self): return len(rig.captures) >= 2
    rig.restore_error = RestoreAfterFirst()
    original = workflow.Workflow.save
    def save(ctx):
        if ctx.state.get("failure_reason"):
            raise OSError("次级状态写盘失败")
        return original(ctx)
    monkeypatch.setattr(workflow.Workflow, "save", save)
    with pytest.raises(OSError) as caught:
        workflow.run(rig.params)
    assert caught.value is error
    assert (run_directory(rig) / "raw/z_000/frequency_0000_attempt_00.npz").exists()


def test_autorange_second_read_failure_rescues_first_frame(rig):
    rig.params = replace(rig.params, sense_scope_auto_offset_tolerance_fraction=.01)
    rig.sense_error_index = 1
    with pytest.raises(OSError, match="SDS 读取失败"):
        workflow.run(rig.params)
    assert_safe(rig)
    with np.load(run_directory(rig) / "raw/z_000/sense_current.npz") as data:
        assert len(data["sense_voltage_v"]) == 128 and not data["quality_accepted"]
        assert "SDS 读取失败" in str(data["acquisition_error"])
    assert not rig.captures


def test_full_stepwise_rerun_and_immutable_raw(rig):
    first = workflow.run(rig.params)
    config = (first / "experiment_config.yaml").read_bytes()
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A08"))
    ctx.execute([RUN_ID, "A10"])
    second = ctx.directory.root
    for index in range(5):
        for name, key in (("sense_current.npz", "current_a"), ("frequency_scan.npz", "r_mean_v")):
            path = f"raw/z_{index:03d}/{name}"
            with np.load(first / path) as full, np.load(second / path) as split:
                np.testing.assert_array_equal(full[key], split[key])
    with pytest.raises(ValueError, match="缺少前置"):
        ctx.execute([RUN_ID])
    ctx.execute(["A07", "A08", RUN_ID, "A10"])
    assert ctx.directory.root != second
    assert (first / "experiment_config.yaml").read_bytes() == config
    assert (second / "raw/z_scan_index.npz").exists()
    ctx.finish()
    assert_safe(rig)


def test_dependencies_parameter_impacts_read_only_preflight_and_quality(rig):
    ctx = workflow.Workflow(rig.params)
    with pytest.raises(ValueError, match="缺少前置"):
        ctx.execute([RUN_ID])
    assert rig.events == []
    ctx.execute(through("A08"))
    before = ctx.snapshot()["steps"]
    ctx.execute(["A01"])
    assert ctx.snapshot()["steps"] == before
    ctx.update_parameters({"SENSE_RESISTOR_TOLERANCE_PERCENT": .1})
    assert ctx.snapshot()["steps"] == before
    flow = definition()
    display = {name for stage in flow["acquisition"] for name in stage["display_parameters"]}
    assert display == set(rig.params.to_external(include_hidden=False)) - set(ANALYSIS_KEYS)
    completed = {key for key, item in ctx.states.items() if item["status"] == "completed"}
    ctx.update_parameters({"ZERO_BIAS_CENTER_FREQUENCY_HZ": 90500, "SYSTEM_SETTLE_S": .002})
    expected = set(flow["parameter_impacts"]["acquisition"]["ZERO_BIAS_CENTER_FREQUENCY_HZ"]) | set(flow["parameter_impacts"]["acquisition"]["SYSTEM_SETTLE_S"])
    assert {key for key, item in ctx.states.items() if item["status"] == "stale"} == completed & expected
    ctx.execute(["A07", "A08"])
    rig.bad_quality = True
    with pytest.raises(acquisition.RPointQualityError) as caught:
        ctx.execute([RUN_ID, "A10"])
    assert ctx.states[RUN_ID]["status"] == "quality_failed" and ctx.states["A10"]["status"] != "completed"
    assert len(list(ctx.directory.raw.glob("z_000/frequency_0000_attempt_*.npz"))) == rig.params.r_point_max_attempts
    ctx.finish(caught.value)


def test_sds_quality_failure_retains_diagnostic_and_blocks_hf2(rig):
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A08"))
    ctx.update_parameters({"MAXIMUM_CURRENT_A": .001})
    with pytest.raises(acquisition.SensePointQualityError) as caught:
        ctx.execute([RUN_ID, "A10"])
    assert ctx.states[RUN_ID]["status"] == "quality_failed" and not rig.captures
    assert ctx.outputs["Y RF"] == "未知（采集中断）"
    with np.load(ctx.directory.raw / "z_000/sense_current.npz") as data:
        assert not data["quality_accepted"] and data["current_a"].size == 128
    with pytest.raises(ValueError, match="缺少前置"):
        ctx.execute(["A10"])
    ctx.finish(caught.value)


@pytest.mark.parametrize("rerun", [False, True])
def test_finish_uses_most_recent_executed_pump_values(rig, rerun):
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A08"))
    previous = rig.pump_writes[-1]
    ctx.update_parameters({"PUMP_CARRIER_AMPLITUDE_VPP": .1})
    assert rig.pump_writes == [previous]
    old_directory = ctx.directory.root
    saved = json.loads((old_directory / "acquisition_state.json").read_text(encoding="utf-8"))
    assert saved["parameters"]["PUMP_CARRIER_AMPLITUDE_VPP"] == previous[1]
    if rerun:
        ctx.execute(["A06"])
    ctx.finish()
    assert rig.pump_writes[-1] == (previous[0], .1 if rerun else previous[1])


def test_finish_without_predecessor_and_legacy_exports(rig):
    from ..mx_z_field_calibration.workflow import run_calibration
    assert workflow.run_calibration is run_calibration
    ctx = workflow.Workflow(rig.params)
    ctx.execute([FINISH_ID])
    assert ctx.closed and ctx.directory is None and rig.events == []
    assert all(value == "未知" for value in ctx.outputs.values())


@pytest.mark.parametrize("source", ["rf", "sds"])
def test_changed_real_acquisition_mode_requires_explicit_preparation(rig, source):
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A08"))
    if source == "rf":
        rf = rig.mapping["rf_coil"]
        rig.generators[rf["resource"]].setup_dc(0, channel=rf["channel"])
    else:
        rig.scope.mode = "SINGle"
    with pytest.raises(ValueError, match="模式已改变"):
        ctx.execute([RUN_ID])
    assert not rig.captures and not rig.sense_captures
    stale = "A07" if source == "rf" else "A08"
    assert ctx.states[stale]["status"] == "stale"
    with pytest.raises(ValueError, match="缺少前置"):
        ctx.execute([RUN_ID])
    ctx.finish()


def test_finish_state_save_failure_closes_after_real_safety_restore(rig, monkeypatch):
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A08"))
    error = OSError("模拟结束状态保存失败")
    def save():
        raise error
    monkeypatch.setattr(ctx, "save", save)
    def publish(*args, **kwargs):
        raise OSError("次级会话状态保存失败")
    monkeypatch.setattr(ctx, "publish", publish)
    with pytest.raises(OSError) as caught:
        ctx.finish()
    assert caught.value is error and ctx.closed
    assert ctx.states[FINISH_ID]["status"] == "failed"
    assert_safe(rig)


def test_clock_failure_restores_and_disconnects_without_changing_main_field(rig):
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A02"))
    rig.clock_error = True
    with pytest.raises(Exception, match="时钟") as caught:
        ctx.execute(["A03"])
    ctx.finish(caught.value)
    assert "disconnect_sds" in rig.events
    assert_safe(rig, configured=False)


def test_legacy_wait_keys_preserve_external_parameters():
    old = {"FREQUENCY_SETTLE_TIME_S": .2, "TEMP_SWITCH_OFF_LEAD_S": .3,
        "TEMPERATURE_TOLERANCE_C": .5}
    params = MxZCurrentCouplingCalibrationParams.from_external(old, schema_version=1)
    assert params.system_settle_s == .3
    assert "TEMPERATURE_TOLERANCE_C" not in params.to_external()
    explicit = MxZCurrentCouplingCalibrationParams.from_external(old | {"SYSTEM_SETTLE_S": .1}, schema_version=1)
    assert explicit.system_settle_s == .1
    assert MxZCurrentCouplingCalibrationParams.from_external(explicit.to_external()).to_external() == explicit.to_external()
