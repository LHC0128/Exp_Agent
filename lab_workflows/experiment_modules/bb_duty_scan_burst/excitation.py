"""连续 AOM 载波与内部触发的有限周期 Pump 门控。"""
from ...common import load_safety_limits, validate_safety_limit


def configure(ctx):
    p, sg, c = ctx.params, ctx.devices["sg"], ctx.channels
    limits = load_safety_limits(ctx.root)
    carrier, gate = c["Pump_modulation"], c["Time_sequence"]
    for value in (p.aom_carrier_amplitude_v, p.aom_carrier_offset_v - p.aom_carrier_amplitude_v / 2,
                  p.aom_carrier_offset_v + p.aom_carrier_amplitude_v / 2):
        validate_safety_limit("Pump_modulation", value, limits)
    sg.set_burst_state(False, channel=carrier)
    sg.set_mod_state(False, channel=carrier)
    sg.apply_wave("SINusoid", channel=carrier, freq=p.aom_carrier_freq_hz,
        amp=p.aom_carrier_amplitude_v, offset=p.aom_carrier_offset_v, phase=0)
    sg.set_output(True, channel=carrier)
    for value in (p.pump_rf_gate_low_v, p.pump_rf_gate_high_v):
        validate_safety_limit("Time_sequence", value, limits)
    validate_safety_limit("PUMP_MOD_DUTY", p.duty_cycle_list[0] * 100, limits)
    sg.set_burst_state(False, channel=gate)
    sg.set_mod_state(False, channel=gate)
    sg.apply_wave("PULSe", channel=gate, freq=p.modulation_frequency_hz,
        amp=p.pump_rf_gate_high_v - p.pump_rf_gate_low_v, offset=(p.pump_rf_gate_high_v + p.pump_rf_gate_low_v) / 2)
    sg.set_pulse_dcycle(100 * p.duty_cycle_list[0], channel=gate)
    sg.set_high_level(p.pump_rf_gate_high_v, channel=gate)
    sg.set_low_level(p.pump_rf_gate_low_v, channel=gate)
    sg.set_burst_mode("TRIGgered", channel=gate)
    sg.set_burst_ncycles(p.burst_cycles, channel=gate)
    sg.set_burst_period(p.burst_burst_period_s, channel=gate)
    sg.set_burst_trigger_source("INTernal", channel=gate)
    sg.set_burst_state(True, channel=gate)
    sg.set_sync_state(True, channel=gate)
    sg.set_sync_polarity("POSitive", channel=gate)
    sg.set_output(True, channel=gate)
    ctx.outputs["Pump"] = "连续载波 + 内触发有限 Burst；SYNC 正极性"


def verify_ready(ctx):
    sg, gate, p = ctx.devices["sg"], ctx.channels["Time_sequence"], ctx.params
    if not sg.get_burst_state(channel=gate) or not sg.get_output(channel=gate):
        raise ValueError("Pump 门控输出/Burst 模式已改变，请重新执行 A05")
    if not str(sg.get_burst_mode(channel=gate)).upper().startswith("TRIG"):
        raise ValueError("Pump Burst 模式已改变，请重新执行 A05")
    if not str(sg.get_burst_trigger_source(channel=gate)).upper().startswith("INT"):
        raise ValueError("Pump Burst 触发源已改变，请重新执行 A05")
    if int(sg.get_burst_ncycles(channel=gate)) != p.burst_cycles:
        raise ValueError("Pump Burst 周期数已改变，请重新执行 A05")
