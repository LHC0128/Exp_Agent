"""实验步骤的状态快照与异常恢复工具。"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Iterator


@dataclass(slots=True)
class RestoreAction:
    label: str
    callback: Callable[[], None]


class StateGuard:
    def __init__(self) -> None:
        self._actions: list[RestoreAction] = []
        self.restore_errors: list[str] = []

    def add(self, label: str, callback: Callable[[], None]) -> None:
        self._actions.append(RestoreAction(label, callback))

    def restore(self) -> list[str]:
        for action in reversed(self._actions):
            try:
                action.callback()
            except Exception as exc:
                self.restore_errors.append(f"{action.label}: {exc}")
        self._actions.clear()
        return self.restore_errors


@contextmanager
def guarded_state() -> Iterator[StateGuard]:
    guard = StateGuard()
    try:
        yield guard
    finally:
        guard.restore()


def snapshot_generator_output(guard: StateGuard, device: Any, channel: int, label: str) -> None:
    """记录输出状态；可查询时同时记录 DC 电平。"""
    output = device.get_output(channel)
    voltage = None
    if hasattr(device, "get_dc_voltage"):
        try:
            voltage = device.get_dc_voltage(channel)
        except Exception:
            voltage = None

    def restore() -> None:
        if voltage is not None and hasattr(device, "setup_dc"):
            device.setup_dc(voltage, channel=channel)
        device.set_output(output, channel=channel)

    guard.add(label, restore)
