"""Z DC 命令验证、连续 Pump 与 Y RF/R 解调准备。"""
from __future__ import annotations
from typing import Any
from lockin_amplifier import DemodulatorConfig, OscillatorConfig, SignalInputConfig, demod
from ...common import validate_safety_limit
from .models import MxZFieldCalibrationParams
from .scan import initial_rf_frequency_hz

def _set_pump_gate_on(device: Any, channel: int, voltage_v: float) -> None:
    validate_safety_limit("Time_sequence", voltage_v)
    device.set_burst_state(False, channel=channel)
    device.set_mod_state(False, channel=channel)
    device.setup_dc(voltage_v, channel=channel)
    device.set_output(True, channel=channel)

def _set_z_dc_bias(
    device: Any,
    channel: int,
    voltage_v: float,
    *,
    output: bool,
) -> float:
    """设置 Z DC 命令值，并验证波形模式和输出开关状态。"""
    target = float(validate_safety_limit("Z_magnetic_field", voltage_v))
    try:
        device.setup_dc(target, channel=channel)
        device.set_output(output, channel=channel)
        shape = str(device.get_shape(channel=channel)).strip().strip('"').upper()
        output_on = bool(device.get_output(channel=channel))
    except Exception:
        try:
            device.set_output(False, channel=channel)
        except Exception:
            pass
        raise
    if shape != "DC" or output_on is not bool(output):
        device.set_output(False, channel=channel)
        raise RuntimeError(
            "Z DC 配置验证失败："
            f"波形={shape!r}，输出={'ON' if output_on else 'OFF'}"
        )
    return target


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
    initial_frequency_hz = initial_rf_frequency_hz(params)
    xy_field = devices["xy_field"]
    validate_safety_limit("rf_coil", params.y_rf_amplitude_vpp)
    xy_field.set_burst_state(False, channel=channels["y_rf"])
    xy_field.set_mod_state(False, channel=channels["y_rf"])
    xy_field.setup_sine(
        initial_frequency_hz,
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
            frequency=initial_frequency_hz,
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
            phase=float(
                hf2.get_double(f"{hf2.demod_path(params.demod_idx)}/phaseshift")
            ),
        ),
     for_acquisition=True)
    return float(actual_rate)
