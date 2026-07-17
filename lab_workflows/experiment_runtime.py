"""新模式实验子进程的显式参数与取消检查。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .common import WorkflowCancelled
from .experiment_params import ExperimentParams


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
