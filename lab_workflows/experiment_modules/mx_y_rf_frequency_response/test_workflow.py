"""真实准备、DAQ、温控和写文件配假仪器的回归。"""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import json
import os
import time
import numpy as np
import pytest
import yaml
from lockin_amplifier import HF2Instrument, DAQResult
from ...common import WorkflowCancelled, find_project_root, load_mapping
from ...steps import routed_signal_generator
from ..mx_y_rf_sensitivity import preparation as sensitivity_preparation, acquisition
from . import workflow, preparation, acquisition_steps
from .analysis_core import bloch_response
from .models import MxYRFFrequencyResponseParams
from .stages import ACQUISITION, FINISH_ID, definition

ACQUISITION_CASES = {
    "mx-y-rf-frequency-response": {"mode": "single", "cases": {
        "order": ["test_real_full_order_and_slow_disk"],
        "slow_disk": ["test_real_full_order_and_slow_disk"],
        "cancel": ["test_failure_keeps_prior_points_and_shutdown"],
        "acquire_failure": ["test_failure_keeps_prior_points_and_shutdown"],
        "restore_failure": ["test_restore_failure_rescues_current_payload"],
        "save_failure": ["test_save_failure_is_reported_after_restore"],
        "partial_save": ["test_failure_keeps_prior_points_and_shutdown", "test_restore_failure_rescues_current_payload",
            "test_acquisition_restore_and_config_failure_preserve_original_error"],
        "entrypoints": ["test_full_and_stepwise_share_outputs_and_rerun_isolates_directory"],
    }, "sources": ["lab_workflows/experiment_modules/mx_y_rf_frequency_response/workflow.py",
        "lab_workflows/experiment_modules/mx_y_rf_frequency_response/acquisition_steps.py",
        "lab_workflows/experiment_modules/mx_y_rf_frequency_response/preparation.py"]},
}

F0_HZ = 10000.0
GAMMA_HZ = 300.0
SATURATION_PER_VPP2 = 20000.0


def copy_config(root):
    source = find_project_root()
    (root / "params").mkdir(exist_ok=True)
    for name in ("mapping.yaml", "devices.yaml", "safety_limits.yaml"):
        (root / "params" / name).write_bytes((source / "params" / name).read_bytes())
    path = root / "params/mapping.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    for key in ("lockin_r", "lockin_xy"):
        config["mapping"][key]["device_id"] = "hf2_dev18246"
    path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")


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


def install_rig(root, monkeypatch, params=None):
    mapping = load_mapping(root)
    rig = SimpleNamespace(root=root, mapping=mapping, events=[], generators={}, captures=[], enabled=True,
        cancel=False, fault_index=None, acquire_error=None, restore_error=False, clock_error=False)
    rig.params = params or replace(MxYRFFrequencyResponseParams(),
        rf_amplitude_points=3, rf_amplitude_stop_vpp=0.03,
        frequency_points=25, frequency_duration_s=0.016,
        system_settle_s=0.001, temp_switch_on_lag_s=0)
    for config in mapping.values():
        if config["instrument"] == "signal_generator":
            rig.generators.setdefault(config["resource"], FakeSignal(config["resource"], rig))
    monkeypatch.setattr(routed_signal_generator, "create_signal_generator", lambda config: rig.generators[config["resource"]])
    monkeypatch.setattr(sensitivity_preparation, "create_signal_generator", lambda config: rig.generators[config["resource"]])
    monkeypatch.setattr(sensitivity_preparation, "GS200Instrument", lambda resource: Mock())
    monkeypatch.setattr(sensitivity_preparation, "TECInstrument", lambda **kwargs: Mock())
    monkeypatch.setattr(sensitivity_preparation, "create_lockin", lambda config: FakeHF2(rig, device_id=config["device_id"]))
    monkeypatch.setattr(workflow, "find_project_root", lambda: root)
    runtime_check = acquisition_steps.check_cancelled

    def check():
        runtime_check()
        if rig.cancel:
            raise WorkflowCancelled("模拟取消")

    def wait(seconds):
        rig.events.append("settle")
        check()

    for module in (workflow, acquisition_steps, acquisition):
        monkeypatch.setattr(module, "check_cancelled", check)
    monkeypatch.setattr(acquisition_steps, "_sleep", wait)
    monkeypatch.setattr(sensitivity_preparation, "_sleep", lambda seconds: check())
    monkeypatch.setattr(acquisition_steps, "_uncancellable_sleep", lambda seconds: None)
    monkeypatch.setattr(workflow, "report_runtime_progress", lambda *args, **kwargs: None)

    def acquire(hf2, config, **kwargs):
        assert not rig.enabled
        assert config.signal_paths == ["sample.r"], "本实验只能采集 R"
        rig.events.append("acquire")
        index = len(rig.captures)
        rig.captures.append(config.duration)
        if rig.fault_index == index:
            raise rig.acquire_error or WorkflowCancelled("模拟取消")
        kwargs["cancellation_check"]()
        rf_config = rig.mapping["rf_coil"]
        rf = rig.generators[rf_config["resource"]]
        channel = rf_config["channel"]
        frequency = float(rf.frequency[channel])
        amplitude = float(rf.amplitude.get(channel, 0)) if rf.output.get(channel) else 0.0
        saturation = SATURATION_PER_VPP2 * amplitude**2
        mean = float(bloch_response(
            np.float64(frequency), 0.05, GAMMA_HZ, F0_HZ, 0.1, saturation))
        values = np.full(max(8, config.grid_cols), mean)
        if not rf.output.get(channel):
            values = values + np.random.default_rng(index).normal(0, 1e-5, values.size)
        return [DAQResult(signal_name="sample.r", demod_index=kwargs["demod_idx"],
            values=values, time=np.arange(values.size) / kwargs["actual_rate"])]

    monkeypatch.setattr(acquisition.daq, "acquire_data", acquire)
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
    for key in ("rf_coil", "X_magnetic_field", "Z_magnetic_field"):
        config = rig.mapping[key]
        assert rig.generators[config["resource"]].output[config["channel"]] is False
    for key in ("Pump_modulation", "Time_sequence"):
        config = rig.mapping[key]
        assert rig.generators[config["resource"]].output[config["channel"]] is True


def test_real_full_order_and_slow_disk(rig, monkeypatch):
    original = np.savez

    def save(path, **data):
        assert rig.enabled, "恢复温控前不能写文件"
        if "attempt" in str(path):
            rig.events.append("save")
            time.sleep(0.002)
        original(path, **data)

    monkeypatch.setattr(np, "savez", save)
    directory = workflow.run(rig.params)
    assert_safe(rig)
    for index, event in enumerate(rig.events):
        if event == "acquire":
            assert rig.events[index - 3:index] == ["configure", "off", "settle"]
            assert rig.events[index + 1] == "on"
            assert "save" in rig.events[index + 2:index + 5]
    points = rig.params.rf_amplitude_points * rig.params.frequency_points
    assert len(list((directory / "raw").glob("response_*_attempt_00.npz"))) == points
    with np.load(directory / "raw/frequency_response.npz") as data:
        assert data["r_mean_v"].shape == (rig.params.rf_amplitude_points, rig.params.frequency_points)
        assert data["accepted_attempt_index"].tolist() == [[0] * rig.params.frequency_points] * rig.params.rf_amplitude_points
        assert float(data["frequency_hz"][-1]) == rig.params.frequency_stop_hz
        assert float(data["amplitude_vpp"][-1]) == rig.params.rf_amplitude_stop_vpp
        detuning = (data["frequency_hz"] - F0_HZ) / GAMMA_HZ
        saturation = SATURATION_PER_VPP2 * data["amplitude_vpp"][:, None] ** 2
        expected = 0.1 + 0.05 * np.sqrt(1 + detuning**2) / (1 + detuning**2 + saturation)
        np.testing.assert_allclose(data["r_mean_v"], expected, rtol=1e-12, atol=1e-12)
    config = yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf-8"))
    assert config["completion_status"] == "completed"
    assert config["raw_format_version"] == 1
    assert config["device_disconnect"]["tec_disconnected"] is True
    assert config["temperature_control"]["actual_temperature_c"] is None
    assert config["scan_mode"] == "nested_scan"


@pytest.mark.parametrize("failure", [WorkflowCancelled("模拟取消"), OSError("模拟采集失败")])
def test_failure_keeps_prior_points_and_shutdown(rig, failure):
    rig.fault_index, rig.acquire_error = 1, failure
    with pytest.raises(type(failure)) as caught:
        workflow.run(rig.params)
    assert caught.value is failure
    assert_safe(rig)
    directory = next((rig.root / "data/Mx_Y_RF_Frequency_Response").iterdir())
    assert (directory / "raw/response_a000_f0000_attempt_00.npz").is_file()
    assert not (directory / "raw/response_a000_f0001_attempt_00.npz").exists()
    assert not (directory / "raw/frequency_response.npz").exists()


def test_restore_failure_rescues_current_payload(rig):
    rig.restore_error = True
    with pytest.raises(OSError, match="温控恢复失败"):
        workflow.run(rig.params)
    directory = next((rig.root / "data/Mx_Y_RF_Frequency_Response").iterdir())
    with np.load(directory / "raw/response_a000_f0000_attempt_00.npz") as data:
        assert len(data["r_v"]) >= 8
    config = yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf-8"))
    assert config["completion_status"] == "failed"
    assert not config["safety_shutdown"]["completed"]
    assert len(rig.captures) == 1


def test_save_failure_is_reported_after_restore(rig, monkeypatch):
    original = np.savez

    def save(path, **data):
        assert rig.enabled
        if "attempt" in str(path):
            raise OSError("模拟磁盘失败")
        original(path, **data)

    monkeypatch.setattr(np, "savez", save)
    with pytest.raises(OSError, match="磁盘失败"):
        workflow.run(rig.params)
    assert_safe(rig)


def test_full_and_stepwise_share_outputs_and_rerun_isolates_directory(rig):
    first = workflow.run(rig.params)
    original = (first / "experiment_config.yaml").read_bytes()
    rig.captures.clear()
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A07"))
    ctx.execute(["A08", "A09"])
    with np.load(first / "raw/frequency_response.npz") as full, np.load(ctx.directory.root / "raw/frequency_response.npz") as split:
        np.testing.assert_allclose(full["r_mean_v"], split["r_mean_v"])
    second = ctx.directory.root
    with pytest.raises(ValueError, match="缺少前置"):
        ctx.execute(["A09"])
    # 封存后重跑采集需要显式重新准备激励，并使用新目录。
    ctx.execute(["A07", "A08", "A09"])
    assert ctx.directory.root != second
    assert (first / "experiment_config.yaml").read_bytes() == original
    assert (second / "raw/frequency_response.npz").is_file()
    ctx.finish()


def test_missing_dependencies_and_parameter_impacts_match_real_update(rig):
    ctx = workflow.Workflow(rig.params)
    with pytest.raises(ValueError, match="缺少前置"):
        ctx.execute(["A08"])
    assert not rig.events
    ctx.execute(through("A07"))
    before = ctx.snapshot()["steps"]
    ctx.execute(["A01"])
    assert ctx.snapshot()["steps"] == before
    for state in ctx.states.values():
        state["status"] = "completed"
    ctx.update_parameters({"TEMP_SWITCH_ON_LAG_S": 0.5})
    expected = set(definition()["parameter_impacts"]["acquisition"]["TEMP_SWITCH_ON_LAG_S"])
    assert {key for key, state in ctx.states.items() if state["status"] == "stale"} == expected
    ctx.finish()


def test_real_bad_r_points_exhaust_retries_and_block_shutdown_stage(rig, monkeypatch):
    rig.params = replace(rig.params, r_point_max_attempts=2, r_bad_point_std_threshold_v=0.001)
    reader = acquisition.daq.acquire_data
    def noisy_point(*args, **kwargs):
        result = reader(*args, **kwargs)
        result[0].values[:] += np.arange(result[0].values.size) % 2
        return result
    monkeypatch.setattr(acquisition.daq, "acquire_data", noisy_point)
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A09"))
    assert ctx.states["A08"]["status"] == "quality_failed"
    assert ctx.states["A09"]["status"] == "pending"
    assert ctx.snapshot()["outputs"]["Y RF"] == "未知"
    assert ctx.states["A08"]["artifacts"] == [f"raw/response_a000_f0000_attempt_{index:02d}.npz" for index in range(2)]
    assert len(rig.captures) == 2
    for index in range(2):
        with np.load(ctx.directory.raw / f"response_a000_f0000_attempt_{index:02d}.npz") as data:
            assert not data["quality_accepted"] and data["r_std_v"] > 0.001
    with pytest.raises(ValueError, match="缺少前置"):
        ctx.execute(["A09"])
    assert len(rig.captures) == 2
    ctx.finish(ctx.quality_error)
    assert_safe(rig)


def test_all_visible_parameters_have_editors_and_impacts_are_shared(rig):
    flow = definition()
    visible = {field["name"] for field in rig.params.schema(rig.root)["fields"]}
    shown = {key for stage in flow["acquisition"] + flow["analysis"] for key in stage["display_parameters"]}
    assert shown == visible
    assert not any("校相" in stage["title"] for stage in flow["acquisition"])
    ctx = workflow.Workflow(rig.params)
    for state in ctx.states.values():
        state["status"] = "completed"
    ctx.update_parameters({"FREQUENCY_DURATION_S": 0.032, "RF_AMPLITUDE_POINTS": 5})
    expected = set(flow["parameter_impacts"]["acquisition"]["FREQUENCY_DURATION_S"]) | set(
        flow["parameter_impacts"]["acquisition"]["RF_AMPLITUDE_POINTS"])
    assert {key for key, state in ctx.states.items() if state["status"] == "stale"} == expected
    ctx.finish()


def test_clock_failure_releases_connections_after_safe_outputs(rig):
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A07"))
    rig.clock_error = True
    failure = None
    try:
        ctx.execute(["A03"])
    except Exception as exc:
        failure = exc
    assert failure is not None
    ctx.finish(failure)
    assert_safe(rig)
    assert "disconnect" in rig.events
    assert ctx.states[FINISH_ID]["status"] == "completed"


def test_failed_pump_reconfiguration_restores_last_success_and_keeps_run_snapshots(rig, monkeypatch):
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A07"))
    first, original = ctx.directory.config_path, dict(ctx.run_parameters)
    expected = ctx.pump_parameters
    ctx.update_parameters({"PUMP_CARRIER_FREQUENCY_HZ": 99e6, "PUMP_CARRIER_AMPLITUDE_VPP": 0.15})
    device, channel = ctx.devices["pump_rf"], ctx.channels["pump_carrier"]
    setter = device.set_output
    failure = OSError("模拟新载波写入后开启失败")

    def fail_once(value, *, channel=1):
        if channel == ctx.channels["pump_carrier"] and value and device.frequency[channel] == 99e6:
            raise failure
        return setter(value, channel=channel)

    monkeypatch.setattr(device, "set_output", fail_once)
    with pytest.raises(OSError) as caught:
        ctx.execute(["A06"])
    assert caught.value is failure
    assert device.frequency[channel] == 99e6
    assert ctx.pump_parameters is expected
    attempted = dict(ctx.run_parameters)
    ctx.finish(failure)
    assert (device.frequency[channel], device.amplitude[channel]) == (
        expected.pump_carrier_frequency_hz, expected.pump_carrier_amplitude_vpp)
    assert_safe(rig)
    assert ctx.run_parameters == attempted
    assert yaml.safe_load(first.read_text(encoding="utf-8"))["parameters"] == original
    assert yaml.safe_load(ctx.directory.config_path.read_text(encoding="utf-8"))["parameters"] == attempted


def test_apply_parameters_does_not_touch_hardware(rig, monkeypatch):
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A07"))
    events = list(rig.events)
    state = ctx.state
    ctx.update_parameters({"FREQUENCY_POINTS": 31})
    assert rig.events == events
    assert ctx.state == state
    ctx.finish()


def test_analysis_only_draft_does_not_create_empty_round_on_sealing(rig):
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A08"))
    assert ctx.snapshot()["outputs"]["Y RF"] == "ON"
    directory, parameters = ctx.directory.root, dict(ctx.run_parameters)
    ctx.update_parameters({"FIT_R_SQUARED_MIN": 0.25})
    ctx.execute(["A09"])
    assert ctx.directory.root == directory
    assert (directory / "raw/frequency_response.npz").is_file()
    config = yaml.safe_load(ctx.directory.config_path.read_text(encoding="utf-8"))
    assert config["parameters"] == parameters
    assert config["completion_status"] == "completed"
    assert ctx.states["A07"]["status"] == ctx.states["A08"]["status"] == "stale"
    assert ctx.snapshot()["outputs"]["Y RF"] == "OFF"
    ctx.finish()


def test_finish_has_no_prerequisites_and_does_not_connect(rig):
    ctx = workflow.Workflow(rig.params)
    ctx.execute([FINISH_ID])
    assert ctx.closed and ctx.states[FINISH_ID]["status"] == "completed"
    assert not rig.events and ctx.directory is None


def test_rejected_attempt_is_saved_before_real_retry_accepts(rig, monkeypatch):
    reader = acquisition.daq.acquire_data

    def first_attempt_bad(*args, **kwargs):
        result = reader(*args, **kwargs)
        if len(rig.captures) == 1:
            result[0].values[:] += np.arange(result[0].values.size) % 2
        return result

    monkeypatch.setattr(acquisition.daq, "acquire_data", first_attempt_bad)
    directory = workflow.run(rig.params)
    with np.load(directory / "raw/frequency_response.npz", allow_pickle=True) as data:
        assert data["accepted_attempt_index"][0, 0] == 1
        assert data["accepted_file"][0, 0] == "response_a000_f0000_attempt_01.npz"
    for attempt in (0, 1):
        with np.load(directory / f"raw/response_a000_f0000_attempt_{attempt:02d}.npz") as data:
            assert bool(data["quality_accepted"]) is (attempt == 1)
    assert_safe(rig)


def test_acquisition_restore_and_config_failure_preserve_original_error(rig, monkeypatch):
    from ...steps.run_directory import RunDirectory
    primary = OSError("原始采集错误")
    rig.fault_index, rig.acquire_error = 1, primary
    reader, writer = acquisition.daq.acquire_data, RunDirectory.update_config

    def acquire(*args, **kwargs):
        if len(rig.captures) == 1:
            rig.restore_error = True
        return reader(*args, **kwargs)

    def save_config(directory, **values):
        if len(rig.captures) >= 2:
            raise OSError("额外配置保存错误")
        return writer(directory, **values)

    monkeypatch.setattr(acquisition.daq, "acquire_data", acquire)
    monkeypatch.setattr(RunDirectory, "update_config", save_config)
    ctx = workflow.Workflow(rig.params)
    with pytest.raises(OSError) as caught:
        workflow.run(rig.params, workflow_type=lambda *args, **kwargs: ctx)
    assert caught.value is primary
    assert ctx.closed and not ctx.state["safety_shutdown"]["completed"]
    assert ctx.state["state_save_error"] == "额外配置保存错误"
    assert (ctx.directory.raw / "response_a000_f0000_attempt_00.npz").is_file()
    assert not (ctx.directory.raw / "frequency_response.npz").exists()


def test_tec_disconnect_failure_is_reported_without_claiming_disconnected(rig):
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A07"))
    ctx.devices["tec"].disconnect.side_effect = OSError("TEC 断开失败")
    with pytest.raises(RuntimeError, match="安全收尾失败"):
        ctx.finish()
    assert ctx.closed and ctx.states[FINISH_ID]["status"] == "failed"
    assert ctx.state["device_disconnect"]["tec_disconnected"] is False
    assert ctx.state["device_disconnect"]["errors"]
    config = yaml.safe_load(ctx.directory.config_path.read_text(encoding="utf-8"))
    assert config["device_disconnect"] == ctx.state["device_disconnect"]


if __name__ == "__main__":
    from .session import main
    with pytest.MonkeyPatch.context() as patch:
        root = Path(os.environ["MX_Y_RF_FREQ_RESP_TEST_ROOT"])
        progress = workflow.report_runtime_progress
        install_rig(root, patch)
        patch.setattr(workflow, "report_runtime_progress", progress)
        if gate_name := os.environ.get("MX_Y_RF_FREQ_RESP_SHUTDOWN_GATE"):
            gate = Path(gate_name)
            shutdown = preparation.safe_shutdown

            def gated_shutdown(devices, channels, params, **kwargs):
                gate.with_suffix(".started").write_text("收尾已开始", encoding="utf-8")
                deadline = time.monotonic() + 30
                while not gate.exists():
                    if time.monotonic() > deadline:
                        raise TimeoutError("测试未释放安全收尾")
                    time.sleep(0.01)
                report = shutdown(devices, channels, params, **kwargs)
                gate.with_suffix(".finished").write_text(json.dumps({
                    "pump_frequency_hz": devices["pump_rf"].frequency[channels["pump_carrier"]],
                    "completed": report.completed}), encoding="utf-8")
                return report

            patch.setattr(preparation, "safe_shutdown", gated_shutdown)
        main()
