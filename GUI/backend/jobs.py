"""本地单任务管理与事件记录。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from threading import Lock, Thread
from typing import Any, Callable
from uuid import uuid4

from lab_workflows.common import CancellationToken, ProgressEvent, WorkflowCancelled


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


class JobManager:
    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}
        self.hardware_lock = Lock()
        self.jobs_lock = Lock()

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
        acquired = hardware_required and self.hardware_lock.acquire(blocking=False)
        if hardware_required and not acquired:
            job.status = "failed"
            job.error = "其他硬件任务正在运行"
            job.message = job.error
            job.finished_at = datetime.now().isoformat(timespec="seconds")
            return
        try:
            job.status = "running"
            job.started_at = datetime.now().isoformat(timespec="seconds")
            self.event(job, ProgressEvent("start", "任务开始", 0))
            job.result = runner(job)
            job.status = "completed"
            job.stage = "complete"
            job.percent = 100
            job.message = "任务完成"
        except WorkflowCancelled as exc:
            job.status = "cancelled"
            job.message = str(exc)
        except Exception as exc:
            job.status = "failed"
            job.error = str(exc)
            job.message = str(exc)
        finally:
            job.finished_at = datetime.now().isoformat(timespec="seconds")
            if acquired:
                self.hardware_lock.release()

    def event(self, job: Job, event: ProgressEvent) -> None:
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
        try:
            return self.jobs[job_id]
        except KeyError as exc:
            raise KeyError(f"未知任务: {job_id}") from exc

    def cancel(self, job_id: str) -> Job:
        job = self.get(job_id)
        if job.status not in {"queued", "running"}:
            raise ValueError("任务已经结束，无法取消")
        job.cancellation.cancel()
        job.message = "已请求安全停止"
        return job


manager = JobManager()
