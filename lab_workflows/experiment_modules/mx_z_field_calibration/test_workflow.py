"""本实验真实配置、R 采集与写盘配假仪器的业务回归。"""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import os
import time
import numpy as np
import pytest
import yaml
from lockin_amplifier import HF2Instrument, DAQResult
from ...common import WorkflowCancelled, find_project_root, load_mapping
from ...steps import routed_signal_generator
from ..mx_y_rf_sensitivity import acquisition as r_acquisition
from . import workflow, preparation, excitation, acquisition, _helpers
from .models import MxZFieldCalibrationParams
from .stages import ACQUISITION, FINISH_ID, definition

ACQUISITION_CASES = {
    "mx-z-field-calibration": {"mode": "single", "cases": {
        "order": ["test_real_six_step_order_and_slow_disk"],
        "slow_disk": ["test_real_six_step_order_and_slow_disk"],
        "cancel": ["test_fault_keeps_prior_points_and_preserves_main_field"],
        "acquire_failure": ["test_fault_keeps_prior_points_and_preserves_main_field"],
        "restore_failure": ["test_restore_failure_rescues_payload"],
        "save_failure": ["test_disk_failure_preserves_original_error"],
        "partial_save": ["test_fault_keeps_prior_points_and_preserves_main_field", "test_restore_failure_rescues_payload",
            "test_completed_z_group_and_partial_next_group_survive_failure", "test_acquisition_error_survives_secondary_restore_and_state_save_errors"],
        "entrypoints": ["test_full_stepwise_and_rerun_keep_immutable_runs"],
    }, "sources": ["lab_workflows/experiment_modules/mx_z_field_calibration/workflow.py",
        "lab_workflows/experiment_modules/mx_z_field_calibration/acquisition.py",
        "lab_workflows/experiment_modules/mx_z_field_calibration/preparation.py",
        "lab_workflows/experiment_modules/mx_z_field_calibration/excitation.py"]},
}


def copy_config(root):
    source = find_project_root()
    (root / "params").mkdir(exist_ok=True)
    for name in ("mapping.yaml", "devices.yaml", "safety_limits.yaml"):
        (root / "params" / name).write_bytes((source / "params" / name).read_bytes())
    path = root / "params/mapping.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    for key in ("lockin_r", "lockin_xy"):
        config["mapping"][key]["device_id"] = "hf2_dev18246"
    path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")


class FakeSignal:
    def __init__(self, resource, rig):
        self.resource, self.rig = resource, rig
        self.output, self.amplitude, self.phase, self.frequency, self.dc = {}, {}, {}, {}, {}
        self.clock = "INT"

    def connect(self):
        self.rig.events.append("connect")

    def disconnect(self):
        self.rig.events.append("disconnect")

    def set_ref_clock_source(self, value):
        self.clock = value

    def get_ref_clock_source(self):
        if self.rig.clock_error:
            raise OSError("模拟参考时钟失败")
        return self.clock

    def __getattr__(self, name):
        if name.startswith("iter_"):
            raise AttributeError(name)
        def command(*args, channel=1, **kwargs):
            value = args[0] if args else None
            if name == "get_output":
                return self.output.get(channel, False)
            if name == "get_shape":
                return "DC" if channel in self.dc else "SIN"
            if name in ("set_output", "set_amplitude", "set_phase_adjust", "set_frequency"):
                attribute = {"set_output": "output", "set_amplitude": "amplitude", "set_phase_adjust": "phase", "set_frequency": "frequency"}[name]
                getattr(self, attribute)[channel] = value
                temp = self.rig.mapping["Temp_Switch"]
                if self.resource != temp["resource"] or channel != temp["channel"]:
                    self.rig.events.append("configure")
            if name == "setup_dc":
                self.dc[channel] = float(value)
                if getattr(self.rig, "shutting_down", False) and (self.rig.root / "pause-shutdown").is_file():
                    (self.rig.root / "shutdown-entered").touch()
                    deadline = time.monotonic() + 40
                    while not (self.rig.root / "release-shutdown").is_file():
                        if time.monotonic() > deadline:
                            raise TimeoutError("假仪器等待收尾释放超时")
                        time.sleep(0.01)
                temp = self.rig.mapping["Temp_Switch"]
                if self.resource == temp["resource"] and channel == temp["channel"]:
                    enabled = bool(value)
                    self.rig.events.append("on" if enabled else "off")
                    if enabled and self.rig.restore_error and self.rig.captures:
                        raise OSError("模拟温控恢复失败")
                    self.rig.enabled = enabled
            if name == "setup_sine":
                assert self.rig.enabled, "波形准备必须在温控开启时"
                self.frequency[channel], self.amplitude[channel] = float(args[0]), float(args[1])
                self.rig.events.append("prepare_sine")
                pump = self.rig.mapping["Pump_modulation"]
                if self.resource == pump["resource"] and channel == pump["channel"]:
                    self.rig.pump_writes.append((float(args[0]), float(args[1])))
        return command


class FakeHF2(HF2Instrument):
    def __init__(self, rig, **kwargs):
        super().__init__(**kwargs)
        self.rig, self.nodes = rig, {}

    def connect(self):
        self.rig.events.append("connect")

    def disconnect(self):
        self.rig.events.append("disconnect")

    def sync(self):
        pass

    def set_int(self, path, value):
        self.nodes[path] = value

    def set_double(self, path, value):
        self.nodes[path] = value

    def get_int(self, path):
        return 210000000 if path.endswith("/clockbase") else int(self.nodes.get(path, 0))

    def get_double(self, path):
        return float(self.nodes.get(path, 0))


class FakeGS200:
    def __init__(self):
        self.source, self.current, self.output, self.range, self.limit = "CURRent", 0.008, True, 0.01, 0.01

    def connect(self): pass
    def disconnect(self): pass
    def get_source_function(self): return self.source
    def get_current(self): return self.current
    def get_output(self): return self.output
    def get_current_range(self): return self.range
    def get_current_limit(self): return self.limit
    def set_source_function(self, value): self.source = value
    def set_current(self, value): self.current = value
    def set_output(self, value): self.output = value
    def set_current_range(self, value): self.range = value
    def set_current_limit(self, value): self.limit = value


class FakeTEC:
    def __init__(self):
        self.targets = []

    def connect(self): pass
    def disconnect(self): pass
    def set_target_temperature(self, value, **kwargs): self.targets.append(value)
    def set_enable(self, *args, **kwargs): raise AssertionError("普通采集不能启停 TEC")
    def get_temperature(self, **kwargs): raise AssertionError("普通采集不能等待温度判稳")


def small_params():
    return replace(MxZFieldCalibrationParams(), z_bias_start_v=-1, z_bias_stop_v=1, z_bias_step_v=1,
        zero_bias_center_frequency_hz=90000, z_initial_hz_per_v=1000,
        z_prediction_polarity="positive_increases_frequency", main_magnetic_field_ma=9.3,
        frequency_half_width_hz=1000, frequency_step_hz=250, frequency_duration_s=0.016,
        system_settle_s=0.001, temp_switch_on_lag_s=0)


def install_rig(root, monkeypatch, params=None, *, progress=False):
    mapping = load_mapping(root)
    rig = SimpleNamespace(root=root, mapping=mapping, events=[], generators={}, captures=[],
        enabled=True, cancel=False, fault_index=None, acquire_error=None, restore_error=False,
        clock_error=False, bad_quality=False, pump_writes=[], gs200=FakeGS200(), tec=FakeTEC())
    rig.params = params or small_params()
    for config in mapping.values():
        if config["instrument"] == "signal_generator":
            rig.generators.setdefault(config["resource"], FakeSignal(config["resource"], rig))
    monkeypatch.setattr(routed_signal_generator, "create_signal_generator", lambda config: rig.generators[config["resource"]])
    monkeypatch.setattr(preparation, "create_signal_generator", lambda config: rig.generators[config["resource"]])
    monkeypatch.setattr(preparation, "GS200Instrument", lambda resource: rig.gs200)
    monkeypatch.setattr(preparation, "TECInstrument", lambda **kwargs: rig.tec)
    monkeypatch.setattr(preparation, "create_lockin", lambda config: FakeHF2(rig, device_id=config["device_id"]))
    monkeypatch.setattr(workflow, "find_project_root", lambda: root)
    runtime_check = _helpers.check_cancelled

    def check():
        runtime_check()
        if rig.cancel:
            raise WorkflowCancelled("模拟取消")

    def wait(seconds):
        rig.events.append("settle")
        check()

    for module in (workflow, acquisition, r_acquisition, _helpers):
        monkeypatch.setattr(module, "check_cancelled", check)
    monkeypatch.setattr(acquisition, "_sleep", wait)
    monkeypatch.setattr(preparation, "_sleep", lambda seconds: check())
    monkeypatch.setattr(acquisition, "_uncancellable_sleep", lambda seconds: None)
    if not progress:
        monkeypatch.setattr(workflow, "report_runtime_progress", lambda *args, **kwargs: None)

    def acquire(hf2, config, **kwargs):
        assert not rig.enabled
        assert config.signal_paths == ["sample.r"]
        rig.events.append("acquire")
        index = len(rig.captures)
        rig.captures.append(config.duration)
        if rig.fault_index == index:
            raise rig.acquire_error or WorkflowCancelled("模拟取消")
        kwargs["cancellation_check"]()
        rf_config = mapping["rf_coil"]
        frequency = rig.generators[rf_config["resource"]].frequency[rf_config["channel"]]
        z_config = mapping["Z_magnetic_field"]
        center = 90000 + 1000 * rig.generators[z_config["resource"]].dc[z_config["channel"]]
        mean = 0.1 + 0.5 * 300**2 / ((frequency - center)**2 + 300**2)
        values = np.full(max(8, config.grid_cols), mean)
        if rig.bad_quality:
            values[::2] += 0.1
        return [DAQResult(signal_name="sample.r", demod_index=kwargs["demod_idx"],
            values=values, time=np.arange(values.size) / kwargs["actual_rate"])]

    monkeypatch.setattr(r_acquisition.daq, "acquire_data", acquire)
    return rig


@pytest.fixture
def rig(tmp_path, monkeypatch):
    copy_config(tmp_path)
    return install_rig(tmp_path, monkeypatch)


def through(last):
    ids = [stage.id for stage in ACQUISITION if stage.id != FINISH_ID]
    return ids[:ids.index(last) + 1]


def assert_safe(rig, *, configured=True):
    assert rig.enabled
    assert (rig.gs200.source, rig.gs200.current, rig.gs200.output, rig.gs200.range, rig.gs200.limit) == (
        "CURRent", rig.params.main_magnetic_field_ma / 1000 if configured else 0.008, True, 0.01, 0.01)
    for key in ("rf_coil", "X_magnetic_field", "Z_magnetic_field"):
        config = rig.mapping[key]
        assert rig.generators[config["resource"]].output[config["channel"]] is False
    temp = rig.mapping["Temp_Switch"]
    assert rig.generators[temp["resource"]].output[temp["channel"]] is True
    for key in ("Pump_modulation", "Time_sequence"):
        config = rig.mapping[key]
        assert rig.generators[config["resource"]].output[config["channel"]] is True


def test_real_six_step_order_and_slow_disk(rig, monkeypatch):
    original = np.savez

    def save(path, **data):
        assert rig.enabled, "恢复温控前不能写盘"
        if "attempt" in str(path):
            rig.events.append("save")
            time.sleep(0.02)
        original(path, **data)

    monkeypatch.setattr(np, "savez", save)
    directory = workflow.run(rig.params)
    assert_safe(rig)
    for index, event in enumerate(rig.events):
        if event == "acquire":
            assert rig.events[index - 2:index] == ["off", "settle"]
            assert rig.events[index - 3] == "configure"
            assert rig.events[index + 1:index + 3] == ["on", "save"]
    assert len(rig.captures) == 27
    assert len(list((directory / "raw").glob("z_*/frequency_*_attempt_00.npz"))) == 27
    with np.load(directory / "raw/z_scan_index.npz") as data:
        np.testing.assert_array_equal(data["z_bias_v"], [-1, 0, 1])
    with np.load(directory / "raw/z_002/frequency_scan.npz") as last:
        assert float(last["z_bias_v"]) == 1 and len(last["frequency_hz"]) == 9
        assert all(str(name).startswith("frequency_") for name in last["accepted_file"])
    config = yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf-8"))
    assert config["completion_status"] == "completed" and config["safety_shutdown"]["completed"]
    assert config["temperature_control"]["actual_temperature_c"] is None
    assert config["temperature_control"]["target_temperature_c"] == rig.params.temperature_c
    assert rig.tec.targets == [rig.params.temperature_c]


@pytest.mark.parametrize("failure", [WorkflowCancelled("模拟取消"), OSError("模拟采集失败")])
def test_fault_keeps_prior_points_and_preserves_main_field(rig, failure):
    rig.fault_index, rig.acquire_error = 1, failure
    with pytest.raises(type(failure)) as caught:
        workflow.run(rig.params)
    assert caught.value is failure
    assert_safe(rig)
    directory = next((rig.root / "data/Mx_Z_Field_Calibration").iterdir())
    assert (directory / "raw/z_000/frequency_0000_attempt_00.npz").is_file()
    assert not (directory / "raw/z_000/frequency_0001_attempt_00.npz").exists()


def test_completed_z_group_and_partial_next_group_survive_failure(rig):
    rig.fault_index, rig.acquire_error = 10, OSError("下一 Z 频扫中断")
    with pytest.raises(OSError, match="下一 Z 频扫中断"):
        workflow.run(rig.params)
    assert_safe(rig)
    directory = next((rig.root / "data/Mx_Z_Field_Calibration").iterdir())
    with np.load(directory / "raw/z_000/frequency_scan.npz") as first:
        assert len(first["frequency_hz"]) == 9 and float(first["z_bias_v"]) == -1
    assert (directory / "raw/z_001/frequency_0000_attempt_00.npz").is_file()
    assert not (directory / "raw/z_001/frequency_scan.npz").exists()
    assert not (directory / "raw/z_002").exists()
    assert not (directory / "raw/z_scan_index.npz").exists()


def test_acquisition_error_survives_secondary_restore_and_state_save_errors(rig, monkeypatch):
    original = OSError("原始采集错误")
    rig.fault_index, rig.acquire_error = 1, original
    class RestoreFailure:
        def __bool__(self):
            return len(rig.captures) >= 2
    rig.restore_error = RestoreFailure()
    save = workflow.Workflow.save
    def fail_save(ctx):
        if ctx.state.get("failure_reason"):
            raise OSError("次级配置写盘错误")
        return save(ctx)
    monkeypatch.setattr(workflow.Workflow, "save", fail_save)
    with pytest.raises(OSError) as caught:
        workflow.run(rig.params)
    assert caught.value is original
    assert len(rig.captures) == 2
    directory = next((rig.root / "data/Mx_Z_Field_Calibration").iterdir())
    assert (directory / "raw/z_000/frequency_0000_attempt_00.npz").is_file()


def test_restore_failure_rescues_payload(rig):
    rig.restore_error = True
    with pytest.raises(OSError, match="温控恢复失败"):
        workflow.run(rig.params)
    directory = next((rig.root / "data/Mx_Z_Field_Calibration").iterdir())
    with np.load(directory / "raw/z_000/frequency_0000_attempt_00.npz") as data:
        assert len(data["r_v"]) >= 8
    config = yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf-8"))
    assert config["completion_status"] == "failed" and not config["safety_shutdown"]["completed"]
    assert len(rig.captures) == 1


def test_disk_failure_preserves_original_error(rig, monkeypatch):
    original = np.savez
    error = OSError("模拟磁盘失败")

    def save(path, **data):
        assert rig.enabled
        if "attempt" in str(path):
            raise error
        original(path, **data)

    monkeypatch.setattr(np, "savez", save)
    with pytest.raises(OSError) as caught:
        workflow.run(rig.params)
    assert caught.value is error
    assert_safe(rig)


def test_full_stepwise_and_rerun_keep_immutable_runs(rig):
    first = workflow.run(rig.params)
    original = (first / "experiment_config.yaml").read_bytes()
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A07"))
    ctx.execute(["A08", "A09"])
    second = ctx.directory.root
    for current in range(3):
        path = f"raw/z_{current:03d}/frequency_scan.npz"
        with np.load(first / path) as full, np.load(second / path) as split:
            np.testing.assert_array_equal(full["r_mean_v"], split["r_mean_v"])
    with pytest.raises(ValueError, match="缺少前置"):
        ctx.execute(["A08"])
    ctx.execute(["A07", "A08", "A09"])
    assert ctx.directory.root != second
    assert (first / "experiment_config.yaml").read_bytes() == original
    assert (second / "raw/z_scan_index.npz").exists()
    ctx.finish()
    assert_safe(rig)


def test_dependencies_impacts_quality_and_preflight(rig):
    ctx = workflow.Workflow(rig.params)
    with pytest.raises(ValueError, match="缺少前置"):
        ctx.execute(["A08"])
    assert rig.events == []
    ctx.execute(through("A07"))
    before = ctx.snapshot()["steps"]
    ctx.execute(["A01"])
    assert ctx.snapshot()["steps"] == before
    completed = {stage.id for stage in ACQUISITION if ctx.states[stage.id]["status"] == "completed"}
    ctx.update_parameters({"ZERO_BIAS_CENTER_FREQUENCY_HZ": 90500, "SYSTEM_SETTLE_S": 0.002})
    impacts = definition()["parameter_impacts"]["acquisition"]
    predicted = set(impacts["ZERO_BIAS_CENTER_FREQUENCY_HZ"]) | set(impacts["SYSTEM_SETTLE_S"])
    assert {key for key in completed if ctx.states[key]["status"] == "stale"} == completed & predicted
    ctx.execute(["A07"])
    rig.bad_quality = True
    with pytest.raises(acquisition.RPointQualityError) as caught:
        ctx.execute(["A08", "A09"])
    assert ctx.states["A08"]["status"] == "quality_failed"
    assert ctx.states["A09"]["status"] != "completed"
    rejected = list(ctx.directory.raw.glob("z_000/frequency_0000_attempt_*.npz"))
    assert len(rejected) == rig.params.r_point_max_attempts
    for path in rejected:
        with np.load(path) as data:
            assert not data["quality_accepted"]
    ctx.finish(caught.value)
    assert_safe(rig)


def test_clock_failure_keeps_original_error_and_restores(rig):
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A02"))
    rig.clock_error = True
    with pytest.raises(Exception, match="时钟") as caught:
        ctx.execute(["A03"])
    ctx.finish(caught.value)
    assert "disconnect" in rig.events
    assert_safe(rig, configured=False)


def test_applied_pump_parameters_require_explicit_step_before_preserved_output_changes(rig):
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A07"))
    config = rig.mapping["Pump_modulation"]
    generator = rig.generators[config["resource"]]
    previous = generator.amplitude[config["channel"]]
    ctx.update_parameters({"PUMP_CARRIER_FREQUENCY_HZ": 80e6, "PUMP_CARRIER_AMPLITUDE_VPP": 0.1})
    assert ctx.states["A06"]["status"] == "stale"
    assert rig.pump_writes == [(rig.params.pump_carrier_frequency_hz, previous)]
    ctx.finish()
    assert generator.amplitude[config["channel"]] == previous
    assert generator.frequency[config["channel"]] == rig.params.pump_carrier_frequency_hz
    assert rig.pump_writes == [(rig.params.pump_carrier_frequency_hz, previous)] * 2
    assert_safe(rig)


def test_rerun_pump_step_updates_the_preserved_output_used_by_finish(rig):
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A07"))
    ctx.update_parameters({"PUMP_CARRIER_FREQUENCY_HZ": 80e6, "PUMP_CARRIER_AMPLITUDE_VPP": 0.1})
    ctx.execute(["A06"])
    assert rig.pump_writes == [(rig.params.pump_carrier_frequency_hz, rig.params.pump_carrier_amplitude_vpp),
        (80e6, 0.1)]
    ctx.finish()
    assert rig.pump_writes[-1] == (80e6, 0.1)
    assert len(rig.pump_writes) == 3
    assert_safe(rig)


@pytest.mark.parametrize("first_attempt", [False, True])
def test_failed_pump_step_restores_the_last_successful_full_configuration(rig, monkeypatch, first_attempt):
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A05" if first_attempt else "A07"))
    previous = ctx.directory.root
    snapshot = (previous / "experiment_config.yaml").read_bytes()
    ctx.update_parameters({"PUMP_CARRIER_FREQUENCY_HZ": 80e6, "PUMP_CARRIER_AMPLITUDE_VPP": 0.1})
    error = OSError("Pump 门控设置失败")
    def fail_gate(*args, **kwargs):
        raise error
    monkeypatch.setattr(excitation, "_set_pump_gate_on", fail_gate)
    with pytest.raises(OSError) as caught:
        ctx.execute(["A06"])
    assert caught.value is error and ctx.states["A06"]["status"] == "failed"
    assert rig.pump_writes[-1] == (80e6, 0.1)
    ctx.finish(error)
    assert rig.pump_writes[-1] == (rig.params.pump_carrier_frequency_hz, rig.params.pump_carrier_amplitude_vpp)
    pump = rig.mapping["Pump_modulation"]
    generator = rig.generators[pump["resource"]]
    assert generator.frequency[pump["channel"]] == rig.params.pump_carrier_frequency_hz
    assert generator.amplitude[pump["channel"]] == rig.params.pump_carrier_amplitude_vpp
    assert (previous / "experiment_config.yaml").read_bytes() != snapshot
    assert yaml.safe_load((previous / "experiment_config.yaml").read_text(encoding="utf-8"))["parameters"] == rig.params.to_external()
    assert_safe(rig)


def test_invalid_apply_keeps_revision_values_states_and_actual_hardware(rig):
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A07"))
    before, writes = ctx.snapshot(), list(rig.pump_writes)
    with pytest.raises(ValueError):
        ctx.update_parameters({"ZERO_BIAS_CENTER_FREQUENCY_HZ": 0, "Z_INITIAL_HZ_PER_V": 1000})
    assert ctx.snapshot() == before and rig.pump_writes == writes
    ctx.finish()


def test_finish_without_dependencies_does_not_connect_or_claim_output_state(rig):
    ctx = workflow.Workflow(rig.params)
    ctx.execute([FINISH_ID])
    assert ctx.closed and rig.events == []
    assert all(value == "未知" for value in ctx.snapshot()["outputs"].values())
    assert ctx.directory is None


if __name__ == "__main__":
    import json
    from . import session
    root = Path(os.environ["MX_Z_FIELD_TEST_ROOT"])
    payload = json.loads(os.environ["LAB_TYPED_PARAMETERS"])
    payload.pop("session_id")
    patcher = pytest.MonkeyPatch()
    rig = install_rig(root, patcher, MxZFieldCalibrationParams.from_external(payload), progress=True)
    shutdown = preparation.safe_shutdown
    def tracked_shutdown(*args, **kwargs):
        rig.shutting_down = True
        try:
            return shutdown(*args, **kwargs)
        finally:
            rig.shutting_down = False
    patcher.setattr(preparation, "safe_shutdown", tracked_shutdown)
    session.main()
