"""连续 Pump 激励、带符号 Y RF 与 R 解调模式配置。"""
from __future__ import annotations
from typing import Any
from lockin_amplifier import DemodulatorConfig, OscillatorConfig, SignalInputConfig, demod
from ...common import validate_safety_limit
from .models import MxYRFParams
from .scan import signed_amplitude_hardware

def _set_pump_gate_on(device: Any, channel: int, voltage_v: float) -> None:
    """保持通道既有阻抗，用通用 DC 接口保持 Pump 门控常开。"""
    validate_safety_limit("Time_sequence", voltage_v)
    device.set_burst_state(False, channel=channel)
    device.set_mod_state(False, channel=channel)
    device.setup_dc(voltage_v, channel=channel)
    device.set_output(True, channel=channel)

def _configure_y_rf_sine_source(
    params: MxYRFParams,
    rf: Any,
    channel: int,
) -> None:
    """把 Y RF 通道恢复为幅度扫描所需的正弦波状态。"""
    initial_amplitude = max(
        params.frequency_rf_amplitude_vpp,
        1e-6,
    )
    validate_safety_limit("rf_coil", initial_amplitude)
    rf.set_burst_state(False, channel=channel)
    rf.set_mod_state(False, channel=channel)
    rf.setup_sine(
        params.y_rf_frequency_hz,
        initial_amplitude,
        offset=0.0,
        phase=0.0,
        channel=channel,
    )
    rf.set_output(False, channel=channel)

def _configure_response_demodulator(
    params: MxYRFParams,
    hf2: Any,
) -> float:
    """恢复幅度响应采集所需的 HF2 Demod0 配置并返回实际速率。"""
    demod.configure_oscillator(
        hf2,
        OscillatorConfig(
            osc_index=params.demod_osc_idx,
            frequency=params.y_rf_frequency_hz,
        ),
    )
    return float(
        demod.configure_demodulator(
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
                    hf2.get_double(
                        f"{hf2.demod_path(params.demod_idx)}/phaseshift"
                    )
                ),
            ),
         for_acquisition=True)
    )

def _restore_response_acquisition_state(
    params: MxYRFParams,
    devices: dict[str, Any],
    channels: dict[str, int],
) -> float:
    """在温控开启时完成响应采集配置。"""
    _configure_y_rf_sine_source(params, devices['xy_field'], channels['y_rf'])
    return float(_configure_response_demodulator(params, devices['hf2']))

def _set_signed_rf(device: Any, channel: int, signed_amplitude_vpp: float, *, output_zero: bool = False) -> dict[str, Any]:
    amplitude, phase, output_on = signed_amplitude_hardware(signed_amplitude_vpp)
    validate_safety_limit("rf_coil", amplitude)
    if output_on:
        device.set_phase_adjust(phase, channel=channel)
        device.set_amplitude(amplitude, channel=channel)
        device.set_output(True, channel=channel)
    else:
        device.set_output(bool(output_zero), channel=channel)
    return {"hardware_amplitude_vpp": amplitude, "hardware_phase_deg": phase, "output_on": bool(output_on or output_zero)}

def _set_y_rf_zero_off(rf: Any, channel: int) -> None:
    """噪声采集前将 Y RF 安全切换为 0 V DC 且关闭输出。"""
    validate_safety_limit("rf_coil", 0.0)
    rf.set_output(False, channel=channel)
    rf.setup_dc(0.0, channel=channel)
    rf.set_output(False, channel=channel)

def _set_y_rf_zero_off_with_temperature_sequence(
    params: MxYRFParams,
    devices: dict[str, Any],
    channels: dict[str, int],
) -> None:
    """按标准温控时序把 Y RF 恢复为零输出。"""
    if devices.get("temp_switch") is None or "temp_switch" not in channels:
        _set_y_rf_zero_off(devices["xy_field"], channels["y_rf"])
        return
    _set_y_rf_zero_off(devices['xy_field'], channels['y_rf'])

def restore_mx_y_rf_laser_powers(
    devices: dict[str, Any],
    channels: dict[str, int],
    *,
    pump_power_v: float,
    probe_power_v: float,
) -> None:
    """恢复 Pump/Probe DC 基准功率并保持两路输出开启。"""
    laser = devices.get("laser")
    if laser is None:
        return
    validate_safety_limit("Pump_laser_power", pump_power_v)
    validate_safety_limit("Probe_laser_power", probe_power_v)
    laser.setup_dc(pump_power_v, channel=channels["pump_laser"])
    laser.set_output(True, channel=channels["pump_laser"])
    laser.setup_dc(probe_power_v, channel=channels["probe_laser"])
    laser.set_output(True, channel=channels["probe_laser"])


def configure_pump(params, devices, channels):
    """保持连续 Pump 载波和常开门控，不加入相位校准。"""
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
    _set_pump_gate_on(
        pump_rf,
        channels["pump_gate"],
        params.pump_gate_voltage_v,
    )




def configure_response(params, devices, channels):
    """只配置 R 解调，不改变已有解调相位。"""
    demod.configure_signal_input(devices["hf2"], SignalInputConfig(
        input_index=0, range=params.hf2_signal_range_v, diff=False))
    return _restore_response_acquisition_state(params, devices, channels)
