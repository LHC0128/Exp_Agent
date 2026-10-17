"""连续 Pump 激励、Y RF 正弦与 R 解调准备。"""
from typing import Any
from lockin_amplifier import DemodulatorConfig, OscillatorConfig, SignalInputConfig, demod
from ...common import validate_safety_limit
from .models import MxMainFieldCalibrationParams

def _set_pump_gate_on(device: Any, channel: int, voltage_v: float) -> None:
    validate_safety_limit("Time_sequence", voltage_v)
    device.set_burst_state(False, channel=channel)
    device.set_mod_state(False, channel=channel)
    device.setup_dc(voltage_v, channel=channel)
    device.set_output(True, channel=channel)


def configure_pump(params, devices, channels):
    pump_rf = devices["pump_rf"]
    validate_safety_limit("Pump_modulation", params.pump_carrier_amplitude_vpp)
    pump_rf.setup_sine(
        params.pump_carrier_frequency_hz,
        params.pump_carrier_amplitude_vpp,
        offset=0.0,
        phase=0.0,
        channel=channels["pump_carrier"],
    )
    pump_rf.set_output(True, channel=channels["pump_carrier"])
    _set_pump_gate_on(pump_rf, channels["pump_gate"], params.pump_gate_voltage_v)



def configure_response(params, devices, channels):
    xy_field = devices["xy_field"]
    validate_safety_limit("rf_coil", params.y_rf_amplitude_vpp)
    xy_field.set_burst_state(False, channel=channels["y_rf"])
    xy_field.set_mod_state(False, channel=channels["y_rf"])
    xy_field.setup_sine(
        params.main_field_reference_frequency_hz,
        params.y_rf_amplitude_vpp,
        offset=0.0,
        phase=0.0,
        channel=channels["y_rf"],
    )
    xy_field.set_output(False, channel=channels["y_rf"])

    hf2 = devices["hf2"]
    demod.configure_signal_input(
        hf2,
        SignalInputConfig(
            input_index=0,
            range=params.hf2_signal_range_v,
            diff=False,
        ),
    )
    demod.configure_oscillator(
        hf2,
        OscillatorConfig(
            osc_index=params.demod_osc_idx,
            frequency=params.main_field_reference_frequency_hz,
        ),
    )
    actual_rate = demod.configure_demodulator(
        hf2,
        DemodulatorConfig(
            demod_index=params.demod_idx,
            enable=True,
            rate=params.response_rate_sa_s,
            input_channel=0,
            osc_select=params.demod_osc_idx,
            harmonic=1,
            time_constant=params.response_time_constant_s,
            order=params.response_demod_order,
            phase=float(hf2.get_double(f"{hf2.demod_path(params.demod_idx)}/phaseshift")),
        ),
     for_acquisition=True)
    return float(actual_rate)
