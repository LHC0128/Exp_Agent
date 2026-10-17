"""共同触发与 Z 任意波的安全配置。"""
from __future__ import annotations
from typing import Any
from ...steps import configure_optimal_control_trigger, configure_z_optimal_control_output
from .models import ZAWWaveformScopeCheckParams


def _configure_outputs(
    params: ZAWWaveformScopeCheckParams,
    devices: dict[str, Any],
    channels: dict[str, int],
    theory: Any,
    applied: Any,
) -> None:
    """配置共同触发和 Z 任意波，输出前完成安全校验。"""
    trigger_device = devices["z_control"]
    trigger_channel = channels["trigger"]
    z_channel = channels["z_field"]
    configure_optimal_control_trigger(
        params,
        trigger_device,
        trigger_channel,
        output=False,
    )
    configure_z_optimal_control_output(
        params,
        trigger_device,
        z_channel,
        theory,
        applied,
    )
