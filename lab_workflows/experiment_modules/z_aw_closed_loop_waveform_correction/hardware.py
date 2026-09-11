"""闭环输出的幅度基准与仪器回读校验。"""

from __future__ import annotations

import math
from typing import Any

from ...common import validate_safety_limit
from ...z_arbitrary_control import configure_z_optimal_control_output


def configure_verified_output(params: Any, device: Any, channel: int,
                              theory: Any, applied: Any) -> dict[str, Any]:
    """在关闭状态设置 50 Ω/Vpp 基准，回读一致后才开启 Z 输出。"""
    device.set_output(False, channel=channel)
    # 沿用当前输出的 50 Ω 数值基准，并记录实际回读；不据此推断历史标定设置。
    device.set_output_load("50", channel=channel)
    device.set_voltage_unit("VPP", channel=channel)
    validate_safety_limit("Z_magnetic_field", 0.0)
    device.set_offset(0.0, channel=channel)
    configure_z_optimal_control_output(params, device, channel, theory, applied, output=False)
    actual = {
        "amplitude_vpp": float(device.get_amplitude(channel=channel)),
        "offset_v": float(device.get_offset(channel=channel)),
        "frequency_hz": float(device.get_frequency(channel=channel)),
        "voltage_unit": str(device.get_voltage_unit(channel=channel)).strip().upper(),
        "load_ohm": float(device.get_output_load(channel=channel)),
        "command_voltage_reference": "50_ohm",
    }
    load_is_50_ohm = math.isclose(actual["load_ohm"], 50.0, rel_tol=1e-5, abs_tol=1e-6)
    expected = {"amplitude_vpp": applied.amplitude_vpp, "offset_v": applied.offset_v,
                "frequency_hz": theory.repeat_frequency_hz}
    problems = [f"{key}: 请求 {value:.9g}，回读 {actual[key]:.9g}"
                for key, value in expected.items()
                if not math.isclose(actual[key], value, rel_tol=1e-5, abs_tol=1e-6)]
    if actual["voltage_unit"] != "VPP" or not load_is_50_ohm:
        problems.append(f"幅度基准不一致：{actual}")
    device.raise_for_errors()
    if problems:
        raise RuntimeError("Z 输出设置回读不一致：" + "；".join(problems))
    device.set_output(True, channel=channel)
    return actual
