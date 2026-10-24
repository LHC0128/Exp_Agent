"""按当前映射配置连续 AOM 载波与固定 TTL 的内部触发 Burst。"""
import math
from .preparation import checked


def configure(ctx):
    p, sg, ch = ctx.params, ctx.devices["sg"], ctx.channels
    carrier, gate = ch["Pump_modulation"], ch["Time_sequence"]
    checked(ctx, "Pump_modulation", p.aom_carrier_amplitude_v,
            p.aom_carrier_offset_v-p.aom_carrier_amplitude_v/2, p.aom_carrier_offset_v+p.aom_carrier_amplitude_v/2)
    sg.set_burst_state(False, channel=carrier); sg.set_mod_state(False, channel=carrier)
    sg.apply_wave("SINusoid", channel=carrier, freq=p.aom_carrier_freq_hz,
        amp=p.aom_carrier_amplitude_v, offset=p.aom_carrier_offset_v, phase=0)
    sg.set_output(True, channel=carrier)
    checked(ctx, "Time_sequence", p.pump_rf_gate_low_v, p.pump_rf_gate_high_v)
    checked(ctx, "PUMP_MOD_DUTY", p.fixed_duty_cycle)
    sg.set_burst_state(False, channel=gate); sg.set_mod_state(False, channel=gate)
    sg.apply_wave("PULSe", channel=gate, freq=p.modulation_frequency_hz,
        amp=p.pump_rf_gate_high_v-p.pump_rf_gate_low_v, offset=(p.pump_rf_gate_high_v+p.pump_rf_gate_low_v)/2)
    sg.set_pulse_dcycle(p.fixed_duty_cycle, channel=gate)
    sg.set_high_level(p.pump_rf_gate_high_v, channel=gate); sg.set_low_level(p.pump_rf_gate_low_v, channel=gate)
    sg.set_burst_mode("TRIGgered", channel=gate); sg.set_burst_ncycles(p.burst_cycles, channel=gate)
    sg.set_burst_period(p.burst_burst_period_s, channel=gate); sg.set_burst_trigger_source("INTernal", channel=gate)
    sg.set_burst_state(True, channel=gate)
    sg.set_sync_state(True, channel=gate); sg.set_sync_polarity("POSitive", channel=gate)
    sg.set_output(True, channel=gate)
    ctx.outputs.update(AOM="连续载波 + ON", 时序="输出/Burst/SYNC 保持开启")
    ctx.state["burst_ready"] = dict(ctx.state["burst_pump"])


def verify_burst(ctx):
    sg, p, gate = ctx.devices["sg"], ctx.params, ctx.channels["Time_sequence"]
    mode, source = str(sg.get_burst_mode(channel=gate)).upper(), str(sg.get_burst_trigger_source(channel=gate)).upper()
    if (not sg.get_output(channel=gate) or not sg.get_burst_state(channel=gate)
            or mode not in ("TRIG", "TRIGGERED") or source not in ("INT", "INTERNAL")
            or float(sg.get_burst_ncycles(channel=gate)) != p.burst_cycles
            or not math.isclose(float(sg.get_burst_period(channel=gate)), p.burst_burst_period_s, rel_tol=1e-6)):
        raise ValueError("Burst 模式已改变，请显式重跑 A05–A06，不自动改写输出")
