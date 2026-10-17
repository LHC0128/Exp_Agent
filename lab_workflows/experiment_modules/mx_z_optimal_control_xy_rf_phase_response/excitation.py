"""Y RF 的 DC 包络、下降沿 Burst 与逐相位重新 arm。"""
from typing import Any
from .models import MxZOptimalControlXYRFPhaseResponseParams
from ..mx_z_optimal_control_rf_sensitivity.excitation import _validate_y_rf_envelope
from ...steps import OPTIMAL_CONTROL_BURST_TRIGGER_SLOPE


def _configure_y_rf_at_offset(
    params: MxZOptimalControlXYRFPhaseResponseParams,
    device: Any,
    channel: int,
    offset_v: float,
) -> None:
    _validate_y_rf_envelope(offset_v, params.phase_cal_rf_amplitude_vpp)
    device.set_output(False, channel=channel)
    device.set_burst_state(False, channel=channel)
    device.set_mod_state(False, channel=channel)
    device.setup_sine(
        params.y_rf_frequency_hz,
        params.phase_cal_rf_amplitude_vpp,
        offset=offset_v,
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


def _rearm_y_rf_at_offset(
    params: MxZOptimalControlXYRFPhaseResponseParams,
    device: Any,
    channel: int,
    *,
    offset_v: float,
    phase_deg: float,
) -> None:
    """用 OFF→设置 offset/phase→ON 重新等待下一次共同触发。"""
    _validate_y_rf_envelope(offset_v, params.phase_cal_rf_amplitude_vpp)
    device.set_output(False, channel=channel)
    device.set_burst_phase(float(phase_deg) % 360.0, channel=channel)
    device.set_amplitude(params.phase_cal_rf_amplitude_vpp, channel=channel)
    device.set_offset(float(offset_v), channel=channel)
    device.set_output(True, channel=channel)
