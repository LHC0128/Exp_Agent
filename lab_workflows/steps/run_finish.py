"""实验收尾：安全关断、状态恢复与最终完成状态计算的共享步骤。"""

from __future__ import annotations

import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from .safety_shutdown import SafetyShutdownReport


@dataclass(slots=True)
class RunFinishResult:
    """收尾阶段的结果，供工作流写入 experiment_config.yaml。"""

    completion_status: str
    failure_reason: str | None
    shutdown_report: SafetyShutdownReport
    restore_errors: list[str]
    original_exception_pending: bool

    @property
    def cleanup_errors(self) -> list[str]:
        return [*self.shutdown_report.errors, *self.restore_errors]


def finalize_run_safety(
    *,
    shutdown: Callable[[], Any],
    completion_status: str,
    failure_reason: str | None,
    extra_restores: Sequence[tuple[str, Callable[[], Any]]] = (),
    extra_errors: Sequence[str] = (),
) -> RunFinishResult:
    """执行安全关断与状态恢复，并计算最终 completion_status/failure_reason。

    规则：

    - 关断或恢复失败必须体现到 failure_reason 中（先保留原始失败原因，
      再追加清理错误），而不是只打印 warning；
    - 采集正常完成（completed）但恢复失败时，最终状态改为 failed；
    - 原始采集异常或取消时保留原状态与传播中的异常（清理阶段异常不会
      覆盖原始异常，只合并进 failure_reason）；
    - 关断执行本身抛出的异常被捕获并计入清理错误，不会逃逸覆盖原异常。

    调用方在 finally 块中拿到结果后：若 ``completion_status != "completed"``
    且 ``original_exception_pending`` 为 False，应重新抛出包含
    ``failure_reason`` 的异常，确保 GUI 子进程以失败退出。
    """
    original_exception_pending = sys.exc_info()[0] is not None
    try:
        report = shutdown()
    except Exception as exc:
        report = SafetyShutdownReport(
            action_errors=(f"安全关断执行失败: {exc}",),
            disconnect_errors=(),
            preserved_outputs=(),
        )
    restore_errors: list[str] = [str(item) for item in extra_errors]
    for label, action in extra_restores:
        try:
            result = action()
            if result:
                restore_errors.extend(f"{label}: {item}" for item in result)
        except Exception as exc:
            restore_errors.append(f"{label}: {exc}")
    cleanup_errors = [*report.errors, *restore_errors]
    reason_parts: list[str] = []
    if failure_reason:
        reason_parts.append(str(failure_reason))
    if cleanup_errors:
        reason_parts.append("安全恢复失败: " + "；".join(cleanup_errors))
    final_reason = "；".join(reason_parts) if reason_parts else None
    final_status = completion_status
    if completion_status == "completed" and cleanup_errors:
        final_status = "failed"
    return RunFinishResult(
        completion_status=final_status,
        failure_reason=final_reason,
        shutdown_report=report,
        restore_errors=restore_errors,
        original_exception_pending=original_exception_pending,
    )
