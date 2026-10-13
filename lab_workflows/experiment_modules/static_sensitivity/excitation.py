"""静磁场实验的 Pump 载波与外部门控。"""

from ...common import validate_safety_limit


def configure_pump(ctx):
    params = ctx.params
    pump = ctx.devices["dg_mod"]
    carrier, gate = ctx.channels["Pump_modulation"], ctx.channels["Time_sequence"]
    validate_safety_limit("Pump_modulation", params.pump_mod_amplitude)
    for voltage in (-params.pump_mod_amplitude / 2, params.pump_mod_amplitude / 2):
        validate_safety_limit("Pump_modulation", voltage)
    pump.set_burst_state(False, channel=carrier)
    pump.set_mod_state(False, channel=carrier)
    pump.setup_sine(freq=100e6, amplitude=params.pump_mod_amplitude, offset=0.0, phase=0.0, channel=carrier)
    pump.set_output(False, channel=carrier)
    pump.set_burst_mode("GATed", channel=carrier)
    pump.set_burst_trigger_source("EXTernal", channel=carrier)
    pump.set_burst_phase(0.0, channel=carrier)
    pump.set_burst_state(True, channel=carrier)
    validate_safety_limit("PUMP_MOD_DUTY", params.pump_mod_duty)
    for voltage in (params.rf_gate_offset - params.rf_gate_amplitude / 2, params.rf_gate_offset + params.rf_gate_amplitude / 2):
        validate_safety_limit("Time_sequence", voltage)
    pump.set_burst_state(False, channel=gate)
    pump.set_mod_state(False, channel=gate)
    pump.setup_pulse(freq=params.pump_mod_freq, amplitude=params.rf_gate_amplitude,
                     offset=params.rf_gate_offset, width=params.pump_mod_duty / 100 / params.pump_mod_freq, channel=gate)
    pump.set_pulse_delay(params.rf_gate_delay, channel=gate)
    pump.set_pulse_leading("MINimum", channel=gate)
    pump.set_pulse_trailing("MINimum", channel=gate)
    pump.set_output(True, channel=carrier)
