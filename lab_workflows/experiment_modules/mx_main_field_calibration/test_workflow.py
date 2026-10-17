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
from .models import MxMainFieldCalibrationParams
from .stages import ACQUISITION, FINISH_ID, definition

ACQUISITION_CASES = {
    "mx-main-field-calibration": {"mode": "single", "cases": {
        "order": ["test_real_six_step_order_and_slow_disk"],
        "slow_disk": ["test_real_six_step_order_and_slow_disk"],
        "cancel": ["test_fault_keeps_prior_points_and_restores_gs200"],
        "acquire_failure": ["test_fault_keeps_prior_points_and_restores_gs200"],
        "restore_failure": ["test_restore_failure_rescues_payload"],
        "save_failure": ["test_disk_failure_preserves_original_error"],
        "partial_save": ["test_fault_keeps_prior_points_and_restores_gs200", "test_restore_failure_rescues_payload"],
        "entrypoints": ["test_full_stepwise_and_rerun_keep_immutable_runs"],
    }, "sources": ["lab_workflows/experiment_modules/mx_main_field_calibration/workflow.py",
        "lab_workflows/experiment_modules/mx_main_field_calibration/acquisition.py",
        "lab_workflows/experiment_modules/mx_main_field_calibration/preparation.py",
        "lab_workflows/experiment_modules/mx_main_field_calibration/excitation.py"]},
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
            if name in ("set_output", "set_amplitude", "set_phase_adjust", "set_frequency"):
                attribute = {"set_output": "output", "set_amplitude": "amplitude", "set_phase_adjust": "phase", "set_frequency": "frequency"}[name]
                getattr(self, attribute)[channel] = value
                temp = self.rig.mapping["Temp_Switch"]
                if self.resource != temp["resource"] or channel != temp["channel"]:
                    self.rig.events.append("configure")
            if name == "setup_dc":
                self.dc[channel] = float(value)
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
    return replace(MxMainFieldCalibrationParams(), main_field_start_ma=7, main_field_stop_ma=8,
        main_field_step_ma=0.5, main_field_reference_current_ma=7,
        main_field_reference_frequency_hz=90000, main_field_initial_hz_per_ma=1000,
        frequency_half_width_hz=1000, frequency_step_hz=250, frequency_duration_s=0.016,
        system_settle_s=0.001, temp_switch_on_lag_s=0)


def install_rig(root, monkeypatch, params=None, *, progress=False):
    mapping = load_mapping(root)
    rig = SimpleNamespace(root=root, mapping=mapping, events=[], generators={}, captures=[],
        enabled=True, cancel=False, fault_index=None, acquire_error=None, restore_error=False,
        clock_error=False, bad_quality=False, gs200=FakeGS200(), tec=FakeTEC())
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
        center = 90000 + 1000 * (rig.gs200.current * 1000 - 7)
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


def assert_safe(rig):
    assert rig.enabled
    assert (rig.gs200.source, rig.gs200.current, rig.gs200.output, rig.gs200.range, rig.gs200.limit) == (
        "CURRent", 0.008, True, 0.01, 0.01)
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
            time.sleep(0.001)
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
    assert len(list((directory / "raw").glob("current_*/frequency_*_attempt_00.npz"))) == 27
    with np.load(directory / "raw/main_field_scan_index.npz") as data:
        np.testing.assert_array_equal(data["main_field_current_ma"], [7, 7.5, 8])
    config = yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf-8"))
    assert config["completion_status"] == "completed" and config["main_field_restored"]
    assert config["temperature_control"]["actual_temperature_c"] is None
    assert config["temperature_control"]["target_temperature_c"] == rig.params.temperature_c
    assert rig.tec.targets == [rig.params.temperature_c]


@pytest.mark.parametrize("failure", [WorkflowCancelled("模拟取消"), OSError("模拟采集失败")])
def test_fault_keeps_prior_points_and_restores_gs200(rig, failure):
    rig.fault_index, rig.acquire_error = 1, failure
    with pytest.raises(type(failure)) as caught:
        workflow.run(rig.params)
    assert caught.value is failure
    assert_safe(rig)
    directory = next((rig.root / "data/Mx_Main_Field_Calibration").iterdir())
    assert (directory / "raw/current_000/frequency_0000_attempt_00.npz").is_file()
    assert not (directory / "raw/current_000/frequency_0001_attempt_00.npz").exists()


def test_restore_failure_rescues_payload(rig):
    rig.restore_error = True
    with pytest.raises(OSError, match="温控恢复失败"):
        workflow.run(rig.params)
    directory = next((rig.root / "data/Mx_Main_Field_Calibration").iterdir())
    with np.load(directory / "raw/current_000/frequency_0000_attempt_00.npz") as data:
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
        path = f"raw/current_{current:03d}/frequency_scan.npz"
        with np.load(first / path) as full, np.load(second / path) as split:
            np.testing.assert_array_equal(full["r_mean_v"], split["r_mean_v"])
    with pytest.raises(ValueError, match="缺少前置"):
        ctx.execute(["A08"])
    ctx.execute(["A04", "A05", "A06", "A07", "A08", "A09"])
    assert ctx.directory.root != second
    assert (first / "experiment_config.yaml").read_bytes() == original
    assert (second / "raw/main_field_scan_index.npz").exists()
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
    ctx.update_parameters({"MAIN_FIELD_REFERENCE_FREQUENCY_HZ": 90500, "SYSTEM_SETTLE_S": 0.002})
    impacts = definition()["parameter_impacts"]["acquisition"]
    predicted = set(impacts["MAIN_FIELD_REFERENCE_FREQUENCY_HZ"]) | set(impacts["SYSTEM_SETTLE_S"])
    assert {key for key in completed if ctx.states[key]["status"] == "stale"} == completed & predicted
    ctx.execute(["A07"])
    rig.bad_quality = True
    with pytest.raises(acquisition.RPointQualityError) as caught:
        ctx.execute(["A08", "A09"])
    assert ctx.states["A08"]["status"] == "quality_failed"
    assert ctx.states["A09"]["status"] != "completed"
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
    assert_safe(rig)


def test_finish_without_dependencies_does_not_connect_or_claim_output_state(rig):
    ctx = workflow.Workflow(rig.params)
    ctx.execute([FINISH_ID])
    assert ctx.closed and rig.events == []
    assert all(value == "未知" for value in ctx.snapshot()["outputs"].values())
    assert ctx.directory is None


def test_finish_preserves_last_executed_pump_parameters(rig):
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A07"))
    carrier = rig.mapping["Pump_modulation"]
    device, channel = rig.generators[carrier["resource"]], carrier["channel"]
    applied = (device.frequency[channel], device.amplitude[channel])
    ctx.update_parameters({"PUMP_CARRIER_FREQUENCY_HZ": 99e6, "PUMP_CARRIER_AMPLITUDE_VPP": 0.15})
    assert ctx.states["A06"]["status"] == "stale"
    assert (device.frequency[channel], device.amplitude[channel]) == applied
    ctx.finish()
    assert (device.frequency[channel], device.amplitude[channel]) == applied
    assert device.output[channel] is True


def test_rerun_pump_explicitly_updates_preserved_parameters(rig):
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A07"))
    ctx.update_parameters({"PUMP_CARRIER_FREQUENCY_HZ": 99e6, "PUMP_CARRIER_AMPLITUDE_VPP": 0.15})
    ctx.execute(["A06"])
    carrier = rig.mapping["Pump_modulation"]
    device, channel = rig.generators[carrier["resource"]], carrier["channel"]
    assert (device.frequency[channel], device.amplitude[channel]) == (99e6, 0.15)
    ctx.finish()
    assert (device.frequency[channel], device.amplitude[channel]) == (99e6, 0.15)
    assert device.output[channel] is True


def test_finish_before_first_pump_step_uses_original_session_parameters(rig):
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A05"))
    ctx.update_parameters({"PUMP_CARRIER_FREQUENCY_HZ": 99e6, "PUMP_CARRIER_AMPLITUDE_VPP": 0.15})
    ctx.finish()
    carrier = rig.mapping["Pump_modulation"]
    device, channel = rig.generators[carrier["resource"]], carrier["channel"]
    assert (device.frequency[channel], device.amplitude[channel]) == (
        rig.params.pump_carrier_frequency_hz, rig.params.pump_carrier_amplitude_vpp)
    assert device.output[channel] is True


@pytest.mark.parametrize("cancel", [False, True])
def test_gs200_restores_original_current_mode_range_limit_and_output(rig, cancel):
    rig.gs200.source, rig.gs200.current, rig.gs200.output = "CURRENT", 0.009, False
    rig.gs200.range, rig.gs200.limit = 0.1, 0.02
    original = vars(rig.gs200).copy()
    if cancel:
        rig.fault_index = 1
        with pytest.raises(WorkflowCancelled):
            workflow.run(rig.params)
    else:
        workflow.run(rig.params)
    assert vars(rig.gs200) == original


def test_voltage_source_mode_rejected_before_initial_output_configuration(rig):
    rig.gs200.source = "VOLTage"
    with pytest.raises(RuntimeError, match="必须是电流模式"):
        workflow.run(rig.params)
    assert rig.gs200.source == "VOLTage"
    assert not rig.tec.targets and not rig.captures
    assert "off" not in rig.events


def test_original_acquisition_error_survives_temperature_restore_failure(rig, monkeypatch):
    failure = OSError("原始读数失败")
    rig.fault_index, rig.acquire_error = 1, failure
    original = r_acquisition.daq.acquire_data

    def acquire(*args, **kwargs):
        try:
            return original(*args, **kwargs)
        except OSError:
            rig.restore_error = True
            raise

    monkeypatch.setattr(r_acquisition.daq, "acquire_data", acquire)
    with pytest.raises(OSError) as caught:
        workflow.run(rig.params)
    assert caught.value is failure
    directory = next((rig.root / "data/Mx_Main_Field_Calibration").iterdir())
    assert (directory / "raw/current_000/frequency_0000_attempt_00.npz").is_file()
    config = yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf-8"))
    assert not config["safety_shutdown"]["completed"]
    assert "原始读数失败" in config["failure_reason"] and "温控恢复失败" in config["failure_reason"]
    assert len(rig.captures) == 2


if __name__ == "__main__":
    import json
    from . import session
    root = Path(os.environ["MX_MAIN_FIELD_TEST_ROOT"])
    payload = json.loads(os.environ["LAB_TYPED_PARAMETERS"])
    payload.pop("session_id")
    install_rig(root, pytest.MonkeyPatch(), MxMainFieldCalibrationParams.from_external(payload), progress=True)
    session.main()
