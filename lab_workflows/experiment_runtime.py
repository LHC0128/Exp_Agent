"""新模式实验子进程的显式参数、进度上报与取消检查。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .common import WorkflowCancelled
from .experiment_params import ExperimentParams

# GUI 以 JSONL 行协议读取子进程进度；行前缀用于与普通 print 日志区分。
PROGRESS_PROTOCOL_ENV = "LAB_PROGRESS_PROTOCOL"
PROGRESS_PREFIX = "__LAB_PROGRESS__ "


def format_eta(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    minutes, second = divmod(total, 60)
    hours, minute = divmod(minutes, 60)
    return f"{hours}:{minute:02d}:{second:02d}" if hours else f"{minutes:02d}:{second:02d}"


def report_runtime_progress(
    stage: str,
    message: str,
    percent: float | None = None,
    *,
    estimated_remaining_seconds: float | None = None,
) -> None:
    """向 GUI 子进程 stdout 上报一条结构化进度事件。

    GUI 启动时通过 LAB_PROGRESS_PROTOCOL=jsonl 要求 JSONL 行协议；
    直接命令行运行时输出带 ETA 的可读文本。
    """
    payload = {
        "stage": stage,
        "message": message,
        "percent": percent,
        "level": "info",
        "data": {"estimated_remaining_seconds": estimated_remaining_seconds},
    }
    if os.environ.get(PROGRESS_PROTOCOL_ENV) == "jsonl":
        print(PROGRESS_PREFIX + json.dumps(payload, ensure_ascii=False), flush=True)
        return
    eta = "" if estimated_remaining_seconds is None else f"（预计剩余 {format_eta(estimated_remaining_seconds)}）"
    percent_text = "" if percent is None else f" {percent:.0f}%"
    print(f"[{stage}]{percent_text} {message}{eta}", flush=True)


def load_runtime_params(params_type: type[ExperimentParams]) -> ExperimentParams:
    raw = os.environ.get("LAB_TYPED_PARAMETERS")
    if raw is None:
        raise RuntimeError("缺少 LAB_TYPED_PARAMETERS；请通过实验定义或薄入口启动")
    values = json.loads(raw)
    if not isinstance(values, dict):
        raise TypeError("LAB_TYPED_PARAMETERS 必须是 JSON 对象")
    params = params_type.from_external(values)
    errors = params.validate()
    if errors:
        raise ValueError("；".join(errors))
    return params


def apply_runtime_params(namespace: dict[str, Any], params: ExperimentParams) -> None:
    """按模型声明的稳定外部键写入旧工作流局部别名。"""
    fixed = dict(namespace.get("FIXED_PARAMS", {}))
    for name, value in params.to_external().items():
        if name.startswith("FIXED_PARAMS."):
            fixed[name.split(".", 1)[1]] = value
        else:
            namespace[name] = value
    if fixed:
        namespace["FIXED_PARAMS"] = fixed


def runtime_run_dir() -> Path:
    raw = os.environ.get("LAB_RUN_DIR")
    if not raw:
        raise RuntimeError("缺少 LAB_RUN_DIR；离线分析必须指定运行目录")
    path = Path(raw).resolve()
    if not path.is_dir():
        raise FileNotFoundError(f"运行目录不存在: {path}")
    return path


def check_cancelled() -> None:
    raw = os.environ.get("LAB_CANCEL_FILE")
    if raw and Path(raw).exists():
        raise WorkflowCancelled("收到 GUI 安全停止请求")
