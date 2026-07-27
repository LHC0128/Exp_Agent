"""GS200 主磁场状态快照与恢复步骤。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..common import validate_safety_limit


@dataclass(frozen=True, slots=True)
class MainFieldState:
    """运行前 GS200 电流源状态。"""

    source_function: str
    current_a: float
    output_on: bool
    current_range_a: float
    current_limit_a: float


def snapshot_main_field_state(gs200: Any) -> MainFieldState:
    """读取并校验 GS200 电流模式的完整运行前状态。"""
    source_function = str(gs200.get_source_function()).strip()
    if not source_function.upper().startswith("CURR"):
        raise RuntimeError(
            f"GS200 运行前源模式为 {source_function!r}，必须是电流模式"
        )
    current_a = float(gs200.get_current())
    validate_safety_limit("main_magnetic_field", current_a * 1000.0)
    return MainFieldState(
        source_function=source_function,
        current_a=current_a,
        output_on=bool(gs200.get_output()),
        current_range_a=float(gs200.get_current_range()),
        current_limit_a=float(gs200.get_current_limit()),
    )


def restore_main_field_state(gs200: Any, state: MainFieldState) -> None:
    """先关闭输出恢复设定，再恢复运行前输出开关。"""
    current_ma = state.current_a * 1000.0
    validate_safety_limit("main_magnetic_field", current_ma)
    gs200.set_output(False)
    gs200.set_source_function(state.source_function)
    gs200.set_current_limit(state.current_limit_a)
    gs200.set_current(state.current_a)
    gs200.set_current_range(state.current_range_a)
    gs200.set_output(state.output_on)
