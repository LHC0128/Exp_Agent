"""温控开关和温度稳定公共步骤。"""

from __future__ import annotations

import math
import time
from typing import Any

from ..common import CancellationToken, ProgressCallback, emit, validate_safety_limit


def set_temperature_switch(
    device: Any,
    enabled: bool,
    *,
    channel: int = 2,
    on_voltage: float = 5.0,
    off_voltage: float = 0.0,
    settle_time: float = 0.0,
    cancellation: CancellationToken | None = None,
) -> float:
    """用 DC 5/0 V 表示温控开关开/关，始终保持物理输出 ON。"""
    voltage = on_voltage if enabled else off_voltage
    validate_safety_limit("Temp_Switch", float(voltage))
    device.setup_dc(float(voltage), channel=channel)
    device.set_output(True, channel=channel)
    deadline = time.monotonic() + max(0.0, settle_time)
    while time.monotonic() < deadline:
        if cancellation:
            cancellation.raise_if_cancelled()
        time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
    return float(voltage)


def wait_for_temperature_stable(
    tec: Any,
    target_c: float,
    *,
    channel: int = 1,
    tolerance_c: float = 1.0,
    stable_reads: int = 3,
    poll_interval_s: float = 5.0,
    timeout_s: float = 1200.0,
    cancellation: CancellationToken | None = None,
    progress: ProgressCallback | None = None,
) -> float:
    validate_safety_limit("temperature", float(target_c))
    stable_reads = max(1, int(stable_reads))
    stable_count = 0
    started = time.monotonic()
    while True:
        if cancellation:
            cancellation.raise_if_cancelled()
        value = float(tec.get_temperature(channel=channel))
        if not math.isfinite(value):
            raise RuntimeError("TEC 返回的温度读数无效")
        delta = abs(value - target_c)
        stable_count = stable_count + 1 if delta <= tolerance_c else 0
        emit(
            progress,
            "temperature",
            f"温度 {value:.2f} °C，目标 {target_c:.2f} °C，稳定 {stable_count}/{stable_reads}",
        )
        if stable_count >= stable_reads:
            return value
        if time.monotonic() - started >= timeout_s:
            raise TimeoutError(
                f"温度在 {timeout_s:.0f}s 内未稳定至 {target_c:.2f}±{tolerance_c:.2f} °C"
            )
        deadline = time.monotonic() + poll_interval_s
        while time.monotonic() < deadline:
            if cancellation:
                cancellation.raise_if_cancelled()
            time.sleep(min(0.1, deadline - time.monotonic()))
