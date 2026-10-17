"""Z 控制与带 DC 偏置 Y RF 的外触发输出。"""
from __future__ import annotations
from typing import Any
from ...common import validate_safety_limit
from ...steps import configure_fixed_dc_field, OPTIMAL_CONTROL_BURST_TRIGGER_SLOPE
from .models import MxZOptimalControlRFParams

def _configure_y_rf_output(
    params: MxZOptimalControlRFParams,
    device: Any,
    channel: int,
) -> None:
    amplitude_vpp = float(params.phase_cal_rf_amplitude_vpp)
    _validate_y_rf_envelope(params.y_rf_offset_v, amplitude_vpp)
    if amplitude_vpp == 0.0:
        configure_fixed_dc_field(
            device,
            channel,
            "Y_magnetic_field",
            params.y_rf_offset_v,
        )
        return
    device.set_output(False, channel=channel)
    device.set_burst_state(False, channel=channel)
    device.set_mod_state(False, channel=channel)
    device.setup_sine(
        params.y_rf_frequency_hz,
        amplitude_vpp,
        offset=params.y_rf_offset_v,
        phase=0.0,
        channel=channel,
    )
    device.set_burst_state(True, channel=channel)
    device.set_burst_mode("INFinity", channel=channel)
    device.set_burst_trigger_source("EXTernal", channel=channel)
    device.set_burst_trigger_slope(
        OPTIMAL_CONTROL_BURST_TRIGGER_SLOPE,
        channel=channel,
    )
    device.set_burst_phase(0.0, channel=channel)
    device.set_output(False, channel=channel)


def _validate_y_rf_envelope(offset_v: float, amplitude_vpp: float) -> None:
    """校验 Y RF 幅度及叠加 DC 偏置后的完整输出包络。"""
    amplitude = abs(float(amplitude_vpp))
    offset = float(validate_safety_limit("Y_magnetic_field", float(offset_v)))
    validate_safety_limit("rf_coil", amplitude)
    validate_safety_limit("Y_magnetic_field", offset - amplitude / 2.0)
    validate_safety_limit("Y_magnetic_field", offset + amplitude / 2.0)


def _restore_y_rf_burst_sine(
    device: Any,
    channel: int,
    *,
    frequency_hz: float,
    amplitude_vpp: float,
    offset_v: float,
    phase_deg: float,
) -> None:
    """从纯 DC 零点恢复带偏置的外触发 Burst 正弦。"""
    _validate_y_rf_envelope(offset_v, amplitude_vpp)
    device.set_output(False, channel=channel)
    device.set_burst_state(False, channel=channel)
    device.set_mod_state(False, channel=channel)
    device.setup_sine(
        float(frequency_hz),
        abs(float(amplitude_vpp)),
        offset=float(offset_v),
        phase=0.0,
        channel=channel,
    )
    device.set_burst_state(True, channel=channel)
    device.set_burst_mode("INFinity", channel=channel)
    device.set_burst_trigger_source("EXTernal", channel=channel)
    device.set_burst_trigger_slope(
        OPTIMAL_CONTROL_BURST_TRIGGER_SLOPE,
        channel=channel,
    )
    device.set_burst_phase(float(phase_deg) % 360.0, channel=channel)
    device.set_output(False, channel=channel)


def _rearm_y_rf(
    device: Any,
    channel: int,
    *,
    amplitude_vpp: float,
    phase_deg: float,
    frequency_hz: float | None = None,
    offset_v: float = 0.0,
    waveform_state: dict[str, str] | None = None,
) -> dict[str, Any]:
    """设置 Y RF；零幅度切为纯 DC，非零点用 OFF→ON 等待触发。"""
    amplitude = abs(float(amplitude_vpp))
    phase = float(phase_deg) % 360.0
    _validate_y_rf_envelope(offset_v, amplitude)
    device.set_output(False, channel=channel)
    if amplitude == 0.0:
        configure_fixed_dc_field(
            device,
            channel,
            "Y_magnetic_field",
            offset_v,
        )
        if waveform_state is not None:
            waveform_state["mode"] = "dc"
        return {
            "hardware_amplitude_vpp": 0.0,
            "hardware_phase_deg": phase,
            "dc_offset_v": float(offset_v),
            "waveform_mode": "dc",
            "output_on": bool(offset_v != 0.0),
        }
    if waveform_state is not None and waveform_state.get("mode") == "dc":
        if frequency_hz is None:
            raise ValueError("从 Y RF 纯 DC 恢复正弦时必须提供 frequency_hz")
        _restore_y_rf_burst_sine(
            device,
            channel,
            frequency_hz=frequency_hz,
            amplitude_vpp=amplitude,
            offset_v=offset_v,
            phase_deg=phase,
        )
    else:
        device.set_burst_phase(phase, channel=channel)
        device.set_amplitude(amplitude, channel=channel)
    device.set_output(True, channel=channel)
    if waveform_state is not None:
        waveform_state["mode"] = "burst_sine"
    return {
        "hardware_amplitude_vpp": amplitude,
        "hardware_phase_deg": phase,
        "dc_offset_v": float(offset_v),
        "waveform_mode": "burst_sine",
        "output_on": True,
    }


def _rearm_control(
    device: Any,
    channel: int,
    *,
    phase_deg: float,
) -> dict[str, Any]:
    """设置 Z 控制触发相位，并用 OFF→ON 重新等待外部触发。"""
    phase = float(phase_deg) % 360.0
    device.set_output(False, channel=channel)
    device.set_burst_phase(phase, channel=channel)
    device.set_output(True, channel=channel)
    return {
        "control_hardware_phase_deg": phase,
        "control_output_on": True,
    }
