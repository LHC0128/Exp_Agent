"""Pump 外部门控、固定占空比改频与 Demod0 R 准备。"""
import math
from lockin_amplifier import SignalInputConfig, OscillatorConfig, DemodulatorConfig, demod
from ...common import validate_safety_limit


def set_gate_frequency(params, gate, channel, frequency):
    """只更新脉冲频率与脉宽，读取实际值以拒绝仪器钳位。"""
    validate_safety_limit("PUMP_MOD_DUTY", params.pump_mod_duty)
    width = params.pump_mod_duty / 100 / frequency
    gate.set_frequency(frequency, channel=channel)
    gate.set_pulse_width(width, channel=channel)
    actual_frequency = gate.get_frequency(channel=channel)
    actual_width = gate.get_pulse_width(channel=channel)
    if not math.isclose(actual_frequency, frequency, rel_tol=1e-6) or not math.isclose(actual_width, width, rel_tol=0.01, abs_tol=1e-9):
        raise RuntimeError(f"门控回读不匹配：请求 {frequency} Hz / {width} s，实际 {actual_frequency} Hz / {actual_width} s")


def set_demod_frequency(hf2, frequency):
    demod.configure_oscillator(hf2, OscillatorConfig(osc_index=0, frequency=frequency))
    if not math.isclose(hf2.get_double(f"{hf2.osc_path(0)}/freq"), frequency, rel_tol=1e-6):
        raise RuntimeError("HF2 解调频率回读与门控频率不匹配")


def configure_pump(params, devices, channels):
    carrier, carrier_channel = devices["Pump_modulation"], channels["Pump_modulation"]
    gate, gate_channel = devices["Time_sequence"], channels["Time_sequence"]
    validate_safety_limit("Pump_modulation", params.pump_carrier_amplitude_vpp)
    carrier.set_output(False, channel=carrier_channel)
    carrier.set_burst_state(False, channel=carrier_channel)
    carrier.set_mod_state(False, channel=carrier_channel)
    carrier.setup_sine(freq=100e6, amplitude=params.pump_carrier_amplitude_vpp, offset=0, phase=0, channel=carrier_channel)
    carrier.set_output(False, channel=carrier_channel)
    carrier.set_burst_mode("GATed", channel=carrier_channel)
    carrier.set_burst_trigger_source("EXTernal", channel=carrier_channel)
    carrier.set_burst_phase(0.0, channel=carrier_channel)
    carrier.set_burst_state(True, channel=carrier_channel)

    initial_frequency = params.predicted_center(params.z_bias_start_v)
    validate_safety_limit("Time_sequence", 0.0)
    validate_safety_limit("Time_sequence", 5.0)
    validate_safety_limit("PUMP_MOD_DUTY", params.pump_mod_duty)
    gate.set_output(False, channel=gate_channel)
    gate.set_burst_state(False, channel=gate_channel)
    gate.set_mod_state(False, channel=gate_channel)
    gate.setup_pulse(freq=initial_frequency, amplitude=5, offset=2.5,
                     width=params.pump_mod_duty / 100 / initial_frequency, channel=gate_channel)
    gate.set_pulse_delay(0, channel=gate_channel)
    gate.set_pulse_leading("MINimum", channel=gate_channel)
    gate.set_pulse_trailing("MINimum", channel=gate_channel)
    # 在开启门控之前验证首频点，后续每点同样回读。
    set_gate_frequency(params, gate, gate_channel, initial_frequency)
    gate.set_output(True, channel=gate_channel)
    carrier.set_output(True, channel=carrier_channel)



def configure_response(params, devices):
    initial_frequency = params.predicted_center(params.z_bias_start_v)
    hf2 = devices["hf2"]
    demod.configure_signal_input(hf2, SignalInputConfig(input_index=0, range=params.hf2_signal_range_v, diff=False))
    set_demod_frequency(hf2, initial_frequency)
    phase = hf2.get_double(f"{hf2.demod_path(0)}/phaseshift")
    actual_rate = demod.configure_demodulator(hf2, DemodulatorConfig(
        demod_index=0, enable=True, rate=params.response_rate_sa_s, input_channel=0,
        osc_select=0, harmonic=1, time_constant=params.response_time_constant_s,
        order=params.response_demod_order, phase=phase,
    ), for_acquisition=True)
    if actual_rate * params.frequency_duration_s < 8:
        raise RuntimeError("HF2 实际采样率下每频点不足 8 个样本")
    snapshot = {
        "input": {"range": hf2.get_double(f"{hf2.sig_in_path(0)}/range"),
                  **{name: hf2.get_int(f"{hf2.sig_in_path(0)}/{name}") for name in ("ac", "diff", "imp50")}},
        "demod": {**{name: hf2.get_double(f"{hf2.demod_path(0)}/{name}") for name in ("rate", "timeconstant", "phaseshift")},
                  **{name: hf2.get_int(f"{hf2.demod_path(0)}/{name}") for name in ("order", "oscselect", "harmonic", "enable", "adcselect")}},
        "oscillator_frequency_hz": hf2.get_double(f"{hf2.osc_path(0)}/freq"),
    }
    return actual_rate, snapshot
