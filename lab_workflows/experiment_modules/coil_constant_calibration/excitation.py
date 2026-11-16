"""Pump 连续载波、Y RF/R 解调准备与 Z 线圈驱动设置；导入不连接仪器。"""
from __future__ import annotations

from typing import Any

from lockin_amplifier import DemodulatorConfig, OscillatorConfig, SignalInputConfig, demod

from ...common import validate_safety_limit
from ...steps.coil_drive_chain import set_dc
from .run_format import SOURCE_KEY


def set_pump_gate_on(device: Any, channel: int, voltage_v: float) -> None:
    validate_safety_limit("Time_sequence", voltage_v)
    device.set_burst_state(False, channel=channel)
    device.set_mod_state(False, channel=channel)
    device.setup_dc(voltage_v, channel=channel)
    device.set_output(True, channel=channel)


def configure_pump(ctx) -> None:
    """连续 100 MHz 载波 + 常开门控。"""
    p, ch = ctx.params, ctx.channels
    pump_rf = ctx.devices["pump_rf"]
    validate_safety_limit("Pump_modulation", p.pump_carrier_amplitude_vpp)
    pump_rf.setup_sine(p.pump_carrier_frequency_hz, p.pump_carrier_amplitude_vpp,
                       offset=0.0, phase=0.0, channel=ch["pump_carrier"])
    pump_rf.set_output(True, channel=ch["pump_carrier"])
    set_pump_gate_on(pump_rf, ch["pump_gate"], p.pump_gate_voltage_v)
    ctx.outputs["Pump"] = "100 MHz 载波 ON，门控 5 V 常开"


def configure_response(ctx) -> float:
    """Y RF 正弦与锁相 R 解调准备；返回实际解调率。"""
    p, ch = ctx.params, ctx.channels
    hf2 = ctx.devices["hf2"]
    xy = ctx.devices["xy_field"]
    initial = float(p.main_field_center_hz)
    validate_safety_limit("rf_coil", p.y_rf_amplitude_vpp)
    xy.set_burst_state(False, channel=ch["y_rf"])
    xy.set_mod_state(False, channel=ch["y_rf"])
    xy.setup_sine(initial, p.y_rf_amplitude_vpp, offset=0.0, phase=0.0, channel=ch["y_rf"])
    xy.set_output(False, channel=ch["y_rf"])
    demod.configure_signal_input(
        hf2, SignalInputConfig(input_index=0, range=p.hf2_signal_range_v, diff=False))
    demod.configure_oscillator(hf2, OscillatorConfig(osc_index=p.demod_osc_idx, frequency=initial))
    actual_rate = demod.configure_demodulator(
        hf2,
        DemodulatorConfig(
            demod_index=p.demod_idx, enable=True, rate=p.response_rate_sa_s, input_channel=0,
            osc_select=p.demod_osc_idx, harmonic=1, time_constant=p.response_time_constant_s,
            order=p.response_demod_order,
            phase=float(hf2.get_double(f"{hf2.demod_path(p.demod_idx)}/phaseshift")),
        ),
        for_acquisition=True)
    ctx.outputs["Y RF"] = f"正弦 {initial:.6g} Hz，{p.y_rf_amplitude_vpp:g} Vpp（输出 OFF，待扫描）"
    return float(actual_rate)


def set_y_rf_frequency(ctx, frequency_hz: float) -> None:
    """设置 Y RF 频率并同步锁相振荡器；每频点调用。"""
    p, ch = ctx.params, ctx.channels
    xy, hf2 = ctx.devices["xy_field"], ctx.devices["hf2"]
    validate_safety_limit("rf_coil", p.y_rf_amplitude_vpp)
    xy.set_frequency(float(frequency_hz), channel=ch["y_rf"])
    xy.set_phase_adjust(0.0, channel=ch["y_rf"])
    xy.set_amplitude(p.y_rf_amplitude_vpp, channel=ch["y_rf"])
    xy.set_output(True, channel=ch["y_rf"])
    demod.configure_oscillator(
        hf2, OscillatorConfig(osc_index=p.demod_osc_idx, frequency=float(frequency_hz)))


def stop_y_rf(ctx) -> None:
    xy = ctx.devices.get("xy_field")
    if xy is not None and "y_rf" in ctx.channels:
        xy.set_output(False, channel=ctx.channels["y_rf"])


def set_z_current_drive(ctx, volts: float) -> float:
    """写 Z 通道 DC 电平（功放输入），先过 Z_magnetic_field 安全限值。"""
    set_dc(ctx, volts, SOURCE_KEY)
    return float(volts)
