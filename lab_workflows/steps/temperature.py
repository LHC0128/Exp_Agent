"""温控开关和温度稳定公共步骤。"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

from ..common import CancellationToken, ProgressCallback, emit, validate_safety_limit


@dataclass(frozen=True, slots=True)
class TemperatureControlStatus:
    """一次实验的 TEC 控制状态。"""

    target_temperature_c: float
    actual_temperature_c: float | None
    controlled_by_experiment: bool
    control_source: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _report_temperature_warning(
    progress: ProgressCallback | None,
    message: str,
) -> None:
    if progress is None:
        print(f"[警告] {message}")
        return
    emit(progress, "temperature", message, level="warning")


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


def configure_temperature_control(
    tec: Any | None,
    target_c: float,
    *,
    channel: int = 1,
    tolerance_c: float = 1.0,
    stable_reads: int = 3,
    poll_interval_s: float = 5.0,
    timeout_s: float = 1200.0,
    cancellation: CancellationToken | None = None,
    progress: ProgressCallback | None = None,
    stability_waiter: Callable[..., float] | None = None,
) -> TemperatureControlStatus:
    """配置 TEC；设备不可用时交由外部温控软件并继续实验。"""
    validate_safety_limit("temperature", float(target_c))
    if tec is None:
        _report_temperature_warning(
            progress,
            (
                "TEC 未连接，已跳过目标温度设置和稳定等待；"
                f"请确认外部温控软件维持 {target_c:.2f} °C。"
            ),
        )
        return TemperatureControlStatus(
            target_temperature_c=float(target_c),
            actual_temperature_c=None,
            controlled_by_experiment=False,
            control_source="external_software",
        )

    tec.set_target_temperature(float(target_c), channel=channel)
    tec.set_enable(True, channel=channel)
    waiter = stability_waiter or wait_for_temperature_stable
    actual = waiter(
        tec,
        float(target_c),
        channel=channel,
        tolerance_c=tolerance_c,
        stable_reads=stable_reads,
        poll_interval_s=poll_interval_s,
        timeout_s=timeout_s,
        cancellation=cancellation,
        progress=progress,
    )
    return TemperatureControlStatus(
        target_temperature_c=float(target_c),
        actual_temperature_c=float(actual),
        controlled_by_experiment=True,
        control_source="tec103",
    )
