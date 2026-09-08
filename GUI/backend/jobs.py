"""本地单任务管理与事件记录。"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime
from threading import Condition, Lock, RLock, Thread
from typing import Any, Callable
from uuid import uuid4

from lab_workflows.common import CancellationToken, ProgressEvent, WorkflowCancelled


TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
DEFAULT_MAX_TERMINAL_JOBS = 100


def _json_safe(value: Any) -> Any:
    """将任务结果中的 NumPy 等第三方标量转换为 JSON 原生类型。"""
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "tolist"):
        return _json_safe(value.tolist())
    if hasattr(value, "item"):
        return _json_safe(value.item())
    return value


@dataclass
class Job:
    id: str
    kind: str
    status: str = "queued"
    stage: str = "queued"
    percent: float | None = 0
    message: str = "等待执行"
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    started_at: str | None = None
    finished_at: str | None = None
    result: Any = None
    error: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    cancellation: CancellationToken = field(default_factory=CancellationToken, repr=False)
    finished_order: int | None = field(default=None, repr=False)

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "stage": self.stage,
            "percent": self.percent,
            "message": self.message,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "result": _json_safe(self.result),
            "error": self.error,
            "events": _json_safe(self.events),
        }

    def summary(self) -> dict[str, Any]:
        """不含 events/result 的轻量摘要，供任务列表使用。"""
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "stage": self.stage,
            "percent": self.percent,
            "message": self.message,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


class JobManager:
    def __init__(self, max_terminal_jobs: int = DEFAULT_MAX_TERMINAL_JOBS) -> None:
        if max_terminal_jobs < 0:
            raise ValueError("max_terminal_jobs 不能小于 0")
        self.jobs: dict[str, Job] = {}
        self.hardware_lock = Lock()
        self.jobs_lock = RLock()
        self._hardware_queue: deque[Job] = deque()
        self._hardware_condition = Condition()
        self.max_terminal_jobs = max_terminal_jobs
        self._finished_sequence = 0

    def create(
        self,
        kind: str,
        runner: Callable[[Job], Any],
        *,
        hardware_required: bool = True,
    ) -> Job:
        job = Job(id=uuid4().hex, kind=kind)
        with self.jobs_lock:
            self.jobs[job.id] = job
        Thread(
            target=self._run,
            args=(job, runner, hardware_required),
            daemon=True,
        ).start()
        return job

    def _run(
        self,
        job: Job,
        runner: Callable[[Job], Any],
        hardware_required: bool,
    ) -> None:
        acquired = False
        if hardware_required:
            if not self._wait_for_hardware(job):
                return
            acquired = True
        status = "completed"
        message = "任务完成"
        error = None
        result: Any = None
        stage = "complete"
        percent: float | None = 100
        try:
            with self.jobs_lock:
                job.status = "running"
                job.started_at = datetime.now().isoformat(timespec="seconds")
            self.event(job, ProgressEvent("start", "任务开始", 0))
            result = runner(job)
        except WorkflowCancelled as exc:
            status = "cancelled"
            message = str(exc)
            stage = job.stage
            percent = job.percent
        except Exception as exc:
            status = "failed"
            error = str(exc)
            message = str(exc)
            stage = job.stage
            percent = job.percent
        finally:
            if acquired:
                self._release_hardware()
        self._finish(
            job,
            status=status,
            message=message,
            result=result,
            error=error,
            stage=stage,
            percent=percent,
        )

    def _wait_for_hardware(self, job: Job) -> bool:
        """硬件任务按 FIFO 排队等待硬件锁；排队期间取消返回 False。"""
        with self._hardware_condition:
            self._hardware_queue.append(job)
            self.event(job, ProgressEvent("queued", "等待硬件空闲", 0))
            while True:
                if job.cancellation.cancelled:
                    self._hardware_queue.remove(job)
                    self._finish(
                        job,
                        status="cancelled",
                        message="排队期间已取消",
                        stage=job.stage,
                        percent=job.percent,
                    )
                    return False
                if self._hardware_queue[0] is job and self.hardware_lock.acquire(blocking=False):
                    self._hardware_queue.popleft()
                    self._hardware_condition.notify_all()
                    return True
                self._hardware_condition.wait(timeout=0.25)

    def _release_hardware(self) -> None:
        """释放硬件锁并唤醒排队的下一个硬件任务。"""
        self.hardware_lock.release()
        with self._hardware_condition:
            self._hardware_condition.notify_all()

    def queued_hardware_count(self) -> int:
        with self._hardware_condition:
            return len(self._hardware_queue)

    def try_acquire_short_hardware(self, *, timeout: float = 0.0) -> bool:
        """短时仪器操作尝试获取硬件锁，且不绕过排队的硬件任务。

        与硬件任务共享同一把锁和同一队列条件：只要还有排队任务，
        短时操作就必须让位（等待排队任务消化，或按 timeout 返回
        False），避免长任务排队后仍被短时接口插队。返回 False 表示
        硬件正忙（长任务运行中或已有排队任务），调用方应返回
        hardware_busy 冲突；成功时保证不会同时持有锁并返回 False。
        """
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self._hardware_condition:
            while True:
                if not self._hardware_queue:
                    return self.hardware_lock.acquire(blocking=False)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._hardware_condition.wait(timeout=min(0.25, remaining))

    def _finish(
        self,
        job: Job,
        *,
        status: str,
        message: str,
        result: Any = None,
        error: str | None = None,
        stage: str | None = None,
        percent: float | None = None,
    ) -> None:
        """原子地写入终态，并淘汰最旧的已结束任务。"""
        with self.jobs_lock:
            job.status = status
            job.message = message
            job.result = result
            job.error = error
            if stage is not None:
                job.stage = stage
            if percent is not None:
                job.percent = percent
            job.finished_at = datetime.now().isoformat(timespec="seconds")
            self._finished_sequence += 1
            job.finished_order = self._finished_sequence
            self._prune_terminal_locked()

    def _prune_terminal_locked(self) -> None:
        finished = sorted(
            (
                job
                for job in self.jobs.values()
                if job.status in TERMINAL_STATUSES and job.finished_order is not None
            ),
            key=lambda item: item.finished_order or 0,
        )
        excess = len(finished) - self.max_terminal_jobs
        for old_job in finished[:max(0, excess)]:
            self.jobs.pop(old_job.id, None)

    def event(self, job: Job, event: ProgressEvent) -> None:
        with self.jobs_lock:
            payload = {
                "index": len(job.events),
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                **asdict(event),
            }
            job.events.append(payload)
            job.stage = event.stage
            job.message = event.message
            if event.percent is not None:
                job.percent = event.percent

    def get(self, job_id: str) -> Job:
        with self.jobs_lock:
            try:
                return self.jobs[job_id]
            except KeyError as exc:
                raise KeyError(f"未知任务: {job_id}") from exc

    def list_public(self) -> list[dict[str, Any]]:
        """返回从新到旧的一致任务快照。"""
        with self.jobs_lock:
            return [job.public() for job in reversed(list(self.jobs.values()))]

    def list_summaries(self) -> list[dict[str, Any]]:
        """返回从新到旧的任务摘要，不含事件与结果。"""
        with self.jobs_lock:
            return [job.summary() for job in reversed(list(self.jobs.values()))]

    def has_active_kind(self, kind: str) -> bool:
        with self.jobs_lock:
            return any(
                job.kind == kind and job.status in {"queued", "running"}
                for job in self.jobs.values()
            )

    def stream_state(
        self,
        job: Job,
        start_index: int,
    ) -> tuple[list[dict[str, Any]], str]:
        """为 SSE 复制指定位置之后的事件和当前状态。"""
        with self.jobs_lock:
            return [dict(event) for event in job.events[start_index:]], job.status

    def cancel(self, job_id: str) -> Job:
        with self.jobs_lock:
            job = self.get(job_id)
            if job.status not in {"queued", "running"}:
                raise ValueError("任务已经结束，无法取消")
            job.cancellation.cancel()
            job.message = "已请求安全停止"
            return job


manager = JobManager()
