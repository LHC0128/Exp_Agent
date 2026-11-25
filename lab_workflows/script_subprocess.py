"""在子进程中经 ``lab_workflows.script_runner`` 执行 Python 脚本，按行转发输出并协作取消。

旧式实验适配器与 GUI 代跑脚本任务共用本函数。取消只写取消文件：子进程在脚本
循环检查点抛 ``KeyboardInterrupt``、在库层 ``check_cancelled()`` 抛 ``WorkflowCancelled``
（经 ``LAB_CANCEL_FILE``），由脚本自己的 ``finally`` 完成安全收尾。本函数
始终等到子进程退出才返回（输出转发出错时同样先请求取消并等待），不强杀；调用方
在返回后才释放硬件锁，保证释放时仪器已由脚本收尾。
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable, TextIO

from .common import CancellationToken
from .setups import subprocess_environment


def run_script_runner(
    root: Path,
    script: Path,
    *,
    token: str,
    on_line: Callable[[str], None],
    overrides: dict[str, Any] | None = None,
    argv: list[str] | None = None,
    cancellation: CancellationToken | None = None,
    log: TextIO | None = None,
) -> int:
    """执行 ``script`` 并返回子进程退出码。

    ``overrides`` 走旧式实验的大写常量注入；``argv`` 走脚本模式（设置 ``sys.argv``
    与脚本目录导入路径）。``on_line`` 接收每一行非空输出（已去除行尾空白），
    ``log`` 按原样记录全部输出。
    """
    job_dir = root / "tmp" / "gui_jobs"
    job_dir.mkdir(parents=True, exist_ok=True)
    overrides_path = job_dir / f"{token}.json"
    cancel_path = job_dir / f"{token}.cancel"
    command = [
        str(root / "agent_exp_env" / "Scripts" / "python.exe"),
        "-u", "-m", "lab_workflows.script_runner",
        "--script", str(script),
    ]
    if overrides is not None:
        overrides_path.write_text(json.dumps(overrides, ensure_ascii=False), encoding="utf-8")
        command += ["--overrides", str(overrides_path)]
    if argv is not None:
        command += ["--argv-json", json.dumps(list(argv), ensure_ascii=False)]
    command += ["--cancel-file", str(cancel_path)]
    environment = os.environ.copy()
    environment.update({
        # 与 typed 实验一致：库层 check_cancelled()（采集分段、可取消等待）读同一取消文件。
        "LAB_CANCEL_FILE": str(cancel_path),
        "MPLBACKEND": "Agg",
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUNBUFFERED": "1",
    })
    environment = subprocess_environment(environment)
    process = subprocess.Popen(
        command,
        cwd=root,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    def watch_cancel() -> None:
        while cancellation and process.poll() is None:
            if cancellation.wait(0.1):
                cancel_path.touch(exist_ok=True)
                return

    watcher = threading.Thread(target=watch_cancel, daemon=True)
    watcher.start()
    assert process.stdout is not None
    try:
        for line in process.stdout:
            if log is not None:
                log.write(line)
                log.flush()
            message = line.rstrip()
            if message:
                on_line(message)
        return process.wait()
    finally:
        if process.poll() is None:
            # 转发输出出错：子进程仍可能占着仪器，请求安全停止并等它收尾退出。
            cancel_path.touch(exist_ok=True)
            process.wait()
        overrides_path.unlink(missing_ok=True)
        cancel_path.unlink(missing_ok=True)
