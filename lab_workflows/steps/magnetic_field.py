"""磁场输出的共享配置步骤。"""

from __future__ import annotations

from typing import Any

from ..common import validate_safety_limit


def configure_fixed_dc_field(
    device: Any,
    channel: int,
    safety_key: str,
    voltage_v: float,
) -> float:
    """配置固定 DC 磁场；严格零值写入 0 V 后关闭输出。"""
    validated_v = float(validate_safety_limit(safety_key, float(voltage_v)))
    device.set_burst_state(False, channel=channel)
    device.set_mod_state(False, channel=channel)
    device.setup_dc(validated_v, channel=channel)
    device.set_output(validated_v != 0.0, channel=channel)
    return validated_v
