"""GUI 代跑 ``scripts/`` 下的连仪器脚本：边界校验、硬件任务与日志。

脚本作为普通硬件任务进入全局 FIFO 队列，与实验互斥；子进程经
``lab_workflows.script_subprocess.run_script_runner`` 执行，取消只写取消文件，由脚本
自己的 ``finally`` 安全收尾，子进程退出后任务才结束并释放硬件锁（不强杀）。
只允许运行目录 HEAD 上已跟踪且未改动的脚本，未发布的代码不能经此连接仪器；
经 ``lab_client`` 调 GUI 的客户端型脚本在预检拒绝（代跑时它自己的空闲检查必然失败）。
接口契约见协同实验模式方案的 CONTRACT-script-jobs。
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from lab_workflows.common import (
    CancellationToken,
    ProgressCallback,
    WorkflowCancelled,
    emit,
    find_project_root,
)
from lab_workflows.script_subprocess import run_script_runner

from .jobs import Job, JobManager

SCRIPTS_DIR = "scripts"
MAX_ARGV = 256
KIND_PREFIX = "script:"
GIT_TIMEOUT_S = 15
CLIENT_SCRIPT_MARK = re.compile(r"\blab_client\b")


class ScriptRejected(ValueError):
    """脚本路径或参数不满足代跑条件。"""


class ScriptRunBody(BaseModel):
    path: str
    argv: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class ResolvedScript:
    relative: str
    path: Path
    commit: str


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=GIT_TIMEOUT_S,
    )


def resolve_script(root: Path, relative: str) -> ResolvedScript:
    """校验 ``relative`` 是 ``scripts/`` 下已发布（HEAD 跟踪且未改动）的 ``.py``。"""
    if not relative or "\\" in relative or ":" in relative or relative.startswith("/"):
        raise ScriptRejected("脚本路径须为相对仓库根、以 / 分隔的路径")
    parts = relative.split("/")
    if any(part in {"..", ".", ""} for part in parts):
        raise ScriptRejected("脚本路径不能含 . 或 .. 段")
    if len(parts) < 2 or parts[0] != SCRIPTS_DIR or not relative.endswith(".py"):
        raise ScriptRejected(f"只能运行 {SCRIPTS_DIR}/ 下的 .py 脚本")
    scripts_root = (root / SCRIPTS_DIR).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(scripts_root):
        raise ScriptRejected(f"脚本解析后不在 {SCRIPTS_DIR}/ 内")
    if not path.is_file():
        raise ScriptRejected(f"脚本不存在：{relative}")
    if _git(root, "cat-file", "-e", f"HEAD:{relative}").returncode != 0:
        raise ScriptRejected(f"脚本未在当前版本（HEAD）中跟踪，先提交并发布：{relative}")
    status = _git(root, "status", "--porcelain", "--", relative)
    if status.returncode != 0:
        raise ScriptRejected(f"无法核对脚本状态：{status.stderr.strip()}")
    if status.stdout.strip():
        raise ScriptRejected(f"脚本与当前版本（HEAD）不一致，未发布的改动不能代跑：{relative}")
    if CLIENT_SCRIPT_MARK.search(path.read_text(encoding="utf-8-sig", errors="replace")):
        # 客户端型脚本经 lab_client 让 GUI 跑实验，代跑时自身占着硬件锁，其 ensure_idle 必然拒绝。
        raise ScriptRejected(f"脚本经 lab_client 调用 GUI，属于客户端脚本，请在开发目录直接运行，不能代跑：{relative}")
    commit =_git(root, "rev-parse", "--short", "HEAD")
    if commit.returncode != 0:
        raise ScriptRejected(f"无法读取当前版本：{commit.stderr.strip()}")
    return ResolvedScript(relative=relative, path=path, commit=commit.stdout.strip())


def validate_argv(argv: list[str]) -> list[str]:
    if len(argv) > MAX_ARGV:
        raise ScriptRejected(f"脚本参数超过 {MAX_ARGV} 个")
    if any("\x00" in item for item in argv):
        raise ScriptRejected("脚本参数不能含空字符")
    return list(argv)


def run_script_job(
    job: Job,
    script: ResolvedScript,
    argv: list[str],
    root: Path,
    *,
    progress: ProgressCallback,
    cancellation: CancellationToken,
) -> dict[str, Any]:
    """在已持有硬件锁的任务线程里执行脚本；返回时子进程已退出。"""
    log_dir = root / "tmp" / "script_jobs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{job.id}.log"
    emit(progress, "start", f"启动 {script.path.name}", None,
         path=script.relative, argv=argv, commit=script.commit, log_path=str(log_path))
    with log_path.open("w", encoding="utf-8") as log:
        code = run_script_runner(
            root,
            script.path,
            token=f"script_{job.id}",
            argv=argv,
            on_line=lambda message: emit(progress, "running", message),
            cancellation=cancellation,
            log=log,
        )
    if cancellation.cancelled:
        raise WorkflowCancelled(f"脚本 {script.path.name} 已在安全检查点停止（日志 {log_path}）")
    if code:
        raise RuntimeError(f"脚本 {script.path.name} 异常结束，退出码 {code}（日志 {log_path}）")
    return {"path": script.relative, "argv": argv, "commit": script.commit,
            "exit_code": code, "log_path": str(log_path)}


def create_router(jobs: JobManager, root: Callable[[], Path] = find_project_root) -> APIRouter:
    router = APIRouter(prefix="/api/scripts")

    def checked(body: ScriptRunBody) -> tuple[Path, ResolvedScript, list[str]]:
        # 开发目录后端先按硬件策略拒绝（409），再做路径校验。
        jobs.check_hardware_policy()
        project = root()
        try:
            return project, resolve_script(project, body.path), validate_argv(body.argv)
        except ScriptRejected as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.post("/preflight")
    def preflight(body: ScriptRunBody):
        _, script, argv = checked(body)
        return {"path": script.relative, "argv": argv, "resolved": str(script.path),
                "commit": script.commit}

    @router.post("/runs")
    def run(body: ScriptRunBody):
        project, script, argv = checked(body)
        job = jobs.create(
            f"{KIND_PREFIX}{script.relative}",
            lambda current: run_script_job(
                current, script, argv, project,
                progress=lambda event: jobs.event(current, event),
                cancellation=current.cancellation,
            ),
        )
        return job.public()

    return router
