"""安全收尾只使用实际执行过的 Pump 与响应配置，真实 setter 配假仪器。"""
import pytest
import yaml
from . import workflow
from .test_workflow import FakeSignal, assert_safe, rig, through


@pytest.fixture
def sine_writes(rig, monkeypatch):
    writes = []

    def setup_sine(device, frequency, amplitude, *, channel=1, **kwargs):
        writes.append((device.resource, channel, float(frequency), float(amplitude)))
        return FakeSignal.__getattr__(device, "setup_sine")(
            frequency, amplitude, channel=channel, **kwargs)

    monkeypatch.setattr(FakeSignal, "setup_sine", setup_sine, raising=False)
    return writes


def _carrier(rig):
    config = rig.mapping["Pump_modulation"]
    return rig.generators[config["resource"]], config["channel"]


@pytest.mark.parametrize("pump_executed", [False, True])
def test_apply_without_pump_rerun_finish_preserves_executed_or_initial_baseline(rig, sine_writes, pump_executed):
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A07" if pump_executed else "A05"))
    original_parameters = dict(ctx.run_parameters)
    directory = ctx.directory.root
    sine_writes.clear()
    ctx.update_parameters({"PUMP_CARRIER_FREQUENCY_HZ": 99e6, "PUMP_CARRIER_AMPLITUDE_VPP": 0.15})
    assert not sine_writes
    assert ctx.states["A06"]["status"] in ("stale", "pending")
    ctx.finish()
    device, channel = _carrier(rig)
    expected = (rig.params.pump_carrier_frequency_hz, rig.params.pump_carrier_amplitude_vpp)
    assert (device.frequency[channel], device.amplitude[channel]) == expected
    assert [(frequency, amplitude) for resource, ch, frequency, amplitude in sine_writes
        if resource == device.resource and ch == channel] == [expected]
    assert device.output[channel] is True
    assert ctx.run_parameters == original_parameters
    config = yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf-8"))
    assert config["parameters"] == original_parameters


def test_rerun_pump_then_new_unexecuted_apply_finish_keeps_last_success(rig, sine_writes):
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A07"))
    first = ctx.directory.root
    original_parameters = dict(ctx.run_parameters)
    ctx.update_parameters({"PUMP_CARRIER_FREQUENCY_HZ": 99e6, "PUMP_CARRIER_AMPLITUDE_VPP": 0.15})
    ctx.execute(["A06"])
    executed_parameters = dict(ctx.run_parameters)
    assert ctx.directory.root != first
    device, channel = _carrier(rig)
    assert (device.frequency[channel], device.amplitude[channel]) == (99e6, 0.15)
    ctx.update_parameters({"PUMP_CARRIER_FREQUENCY_HZ": 98e6, "PUMP_CARRIER_AMPLITUDE_VPP": 0.14})
    sine_writes.clear()
    ctx.finish()
    assert (device.frequency[channel], device.amplitude[channel]) == (99e6, 0.15)
    assert [(frequency, amplitude) for resource, ch, frequency, amplitude in sine_writes
        if resource == device.resource and ch == channel] == [(99e6, 0.15)]
    assert ctx.run_parameters == executed_parameters
    assert yaml.safe_load((first / "experiment_config.yaml").read_text(encoding="utf-8"))["parameters"] == original_parameters


def _assert_response(rig, ctx, expected, writes):
    config = rig.mapping["rf_coil"]
    expected_sine = (config["resource"], config["channel"], expected.y_rf_frequency_hz,
        max(expected.frequency_rf_amplitude_vpp, 1e-6))
    assert [item for item in writes if item[:2] == expected_sine[:2]] == [expected_sine]
    hf2 = ctx.devices["hf2"]
    base = hf2.demod_path(expected.demod_idx)
    assert hf2.get_double(base + "/rate") == expected.response_rate_sa_s
    assert hf2.get_double(base + "/timeconstant") == expected.response_time_constant_s
    assert hf2.get_int(base + "/order") == expected.response_demod_order
    assert hf2.get_double(hf2.osc_path(expected.demod_osc_idx) + "/freq") == expected.y_rf_frequency_hz
    assert_safe(rig)


def test_noise_finish_uses_last_response_configuration_not_unexecuted_rf_parameters(rig, sine_writes):
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A10"))
    original_parameters = dict(ctx.run_parameters)
    expected = ctx.params
    ctx.update_parameters({"Y_RF_FREQUENCY_HZ": 11000, "FREQUENCY_RF_AMPLITUDE_VPP": 0.02,
        "DEMOD_IDX": 1, "DEMOD_OSC_IDX": 1, "RESPONSE_RATE_SA_S": 2000,
        "RESPONSE_TIME_CONSTANT_S": 0.002, "RESPONSE_DEMOD_ORDER": 2})
    sine_writes.clear()
    ctx.finish()
    _assert_response(rig, ctx, expected, sine_writes)
    assert not any("/demods/1/" in name for name in ctx.devices["hf2"].nodes)
    assert ctx.run_parameters == original_parameters
    assert yaml.safe_load(ctx.directory.config_path.read_text(encoding="utf-8"))["parameters"] == original_parameters


def test_response_explicit_rerun_updates_configuration_used_by_sealing(rig, sine_writes):
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A07"))
    ctx.update_parameters({"Y_RF_FREQUENCY_HZ": 11000, "RESPONSE_RATE_SA_S": 2000,
        "RESPONSE_TIME_CONSTANT_S": 0.002, "RESPONSE_DEMOD_ORDER": 2})
    ctx.execute(["A07", "A08", "A09", "A10"])
    expected = ctx.params
    sine_writes.clear()
    ctx.execute(["A11"])
    _assert_response(rig, ctx, expected, sine_writes)
    sealed = ctx.directory.config_path.read_bytes()
    ctx.finish()
    assert ctx.directory.config_path.read_bytes() == sealed


def test_noise_read_failure_still_restores_last_successful_response(rig, sine_writes):
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A09"))
    expected = ctx.params
    failure = OSError("模拟噪声读数失败")
    rig.fault_index, rig.acquire_error = len(rig.captures), failure
    with pytest.raises(OSError) as caught:
        ctx.execute(["A10"])
    assert caught.value is failure
    sine_writes.clear()
    ctx.finish(failure)
    _assert_response(rig, ctx, expected, sine_writes)
    assert ctx.state["failure_reason"] == str(failure)
