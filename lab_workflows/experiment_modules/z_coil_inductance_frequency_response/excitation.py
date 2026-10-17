"""Z 内置正弦和外部下降沿 Burst 的配置。"""
from typing import Any
from ...common import validate_safety_limit
from ...steps import configure_optimal_control_trigger
from .models import ZCoilInductanceFrequencyResponseParams

Z_BURST_TRIGGER_SLOPE = "NEGative"

def _configure_sine(
    device: Any,
    channel: int,
    params: ZCoilInductanceFrequencyResponseParams,
    frequency_hz: float,
    *,
    output: bool,
    initialize: bool = True,
) -> None:
    """初始化或切换 Z 正弦，保持外部触发 Burst 配置。"""
    minimum = params.drive_offset_v - params.drive_amplitude_vpp / 2.0
    maximum = params.drive_offset_v + params.drive_amplitude_vpp / 2.0
    validate_safety_limit("Z_magnetic_field", minimum)
    validate_safety_limit("Z_magnetic_field", maximum)
    device.set_output(False, channel=channel)
    if initialize:
        device.set_burst_state(False, channel=channel)
        device.set_mod_state(False, channel=channel)
        device.setup_sine(
            float(frequency_hz),
            float(params.drive_amplitude_vpp),
            offset=float(params.drive_offset_v),
            phase=0.0,
            channel=channel,
        )
    device.set_frequency(float(frequency_hz), channel=channel)
    device.set_amplitude(float(params.drive_amplitude_vpp), channel=channel)
    device.set_offset(float(params.drive_offset_v), channel=channel)
    device.set_phase_adjust(0.0, channel=channel)
    if initialize:
        device.set_burst_mode("INFinity", channel=channel)
        device.set_burst_phase(0.0, channel=channel)
        device.set_burst_trigger_source("EXTernal", channel=channel)
        device.set_burst_trigger_slope(Z_BURST_TRIGGER_SLOPE, channel=channel)
        device.set_burst_state(True, channel=channel)
    device.set_output(bool(output), channel=channel)
