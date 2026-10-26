"""Z 控制与带 DC 偏置 Y RF 的外触发输出。"""
from __future__ import annotations
from typing import Any
from ...steps import configure_fixed_dc_field, OPTIMAL_CONTROL_BURST_TRIGGER_SLOPE
from ...steps import mx_z_optimal_control as _shared_y_rf
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
    return _shared_y_rf.validate_y_rf_envelope(offset_v, amplitude_vpp)


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
    return _shared_y_rf.restore_y_rf_burst_sine(
        device, channel, frequency_hz=frequency_hz, amplitude_vpp=amplitude_vpp,
        offset_v=offset_v, phase_deg=phase_deg)


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
    return _shared_y_rf.rearm_y_rf(
        device, channel, amplitude_vpp=amplitude_vpp, phase_deg=phase_deg,
        frequency_hz=frequency_hz, offset_v=offset_v, waveform_state=waveform_state)


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
