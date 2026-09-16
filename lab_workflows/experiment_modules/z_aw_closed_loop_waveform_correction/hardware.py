"""闭环输出的幅度基准与仪器回读校验；共享步骤位于 z_arbitrary_control。"""

from __future__ import annotations

from typing import Any

from ...common import validate_safety_limit
from ...z_arbitrary_control import configure_aw_external_burst, verify_aw_output_settings


def configure_verified_output(params: Any, device: Any, channel: int,
                              theory: Any, applied: Any) -> dict[str, Any]:
    """在关闭状态设置 50 Ω/Vpp 基准，回读一致后才开启 Z 输出。"""
    device.set_output(False, channel=channel)
    device.set_output_load("50", channel=channel)
    device.set_voltage_unit("VPP", channel=channel)
    validate_safety_limit("Z_magnetic_field", 0.0)
    device.set_offset(0.0, channel=channel)
    configure_aw_external_burst(
        device, channel, applied, theory.repeat_frequency_hz,
        burst_phase_deg=params.control_burst_phase_deg, output=False,
    )
    actual = verify_aw_output_settings(device, channel, applied, theory.repeat_frequency_hz)
    device.set_output(True, channel=channel)
    return actual
