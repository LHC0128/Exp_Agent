"""共享工作流的配置、安全校验、进度与取消工具。"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import os
from pathlib import Path
from threading import Event
from typing import Any, Callable

import yaml

import hardware_gate


def find_project_root(start: Path | None = None) -> Path:
    """从任意子目录向上定位仓库根目录。"""
    current = (start or Path.cwd()).resolve()
    while current.parent != current:
        if (current / "params" / "mapping.yaml").exists():
            return current
        current = current.parent
    raise FileNotFoundError("无法定位包含 params/mapping.yaml 的项目根目录")


DATA_ROOT_ENV = "LAB_DATA_ROOT"


class DataRootNotConfigured(RuntimeError):
    """开发目录未配置数据根（.lab_dev.yaml data_root → LAB_DATA_ROOT），拒绝读写数据。"""


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left.resolve())) == os.path.normcase(str(right.resolve()))


def data_root(root: Path | None = None) -> Path:
    """实验数据根目录。

    运行目录（有效 .lab_runtime）与其他项目根（如测试临时根、按运行目录推出的根）为 ``<根>/data``；
    开发目录本身没有 data/，数据根取启动器传入的 LAB_DATA_ROOT，未配置时拒绝，
    不在开发目录生成 data/。运行目录忽略 LAB_DATA_ROOT。
    """
    root = Path(root) if root is not None else find_project_root()
    if not _same_path(root, hardware_gate.runtime_root()) or hardware_gate.hardware_allowed():
        return root / "data"
    configured = os.environ.get(DATA_ROOT_ENV, "").strip()
    if not configured:
        raise DataRootNotConfigured(
            f"开发目录 {root} 未配置数据根：在 .lab_dev.yaml 写 data_root 并经 GUI 启动器启动"
            f"（环境变量 {DATA_ROOT_ENV}）")
    return Path(configured)


def load_yaml(path: Path) -> dict[str, Any]:
    # libyaml 的 CSafeLoader 与 SafeLoader 语义相同、约快 7 倍；无 libyaml 时回退纯 Python。
    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    with path.open(encoding="utf-8") as stream:
        return yaml.load(stream, Loader=loader) or {}


def load_mapping(root: Path | None = None) -> dict[str, dict[str, Any]]:
    from .instrument_config import resolve_mapping

    return resolve_mapping(root)


def load_safety_limits(root: Path | None = None) -> dict[str, dict[str, Any]]:
    root = root or find_project_root()
    return load_yaml(root / "params" / "safety_limits.yaml").get(
        "safety_limits", {}
    )


def validate_safety_limit(
    name: str,
    value: float,
    limits: dict[str, dict[str, Any]] | None = None,
) -> float:
    """先拒绝非有限数值，再检查物理量是否落在仓库安全限值内。"""
    if not math.isfinite(value):
        raise ValueError(f"[安全拦截] {name}={value} 必须是有限数值")
    limits = limits or load_safety_limits()
    rule = limits.get(name)
    if not rule:
        return value
    low, high = rule.get("min"), rule.get("max")
    if low is not None and value < low:
        raise ValueError(f"[安全拦截] {name}={value} 低于下限 {low}")
    if high is not None and value > high:
        raise ValueError(f"[安全拦截] {name}={value} 高于上限 {high}")
    return value


@dataclass(slots=True)
class ProgressEvent:
    stage: str
    message: str
    percent: float | None = None
    level: str = "info"
    data: dict[str, Any] = field(default_factory=dict)


ProgressCallback = Callable[[ProgressEvent], None]


class WorkflowCancelled(RuntimeError):
    """工作流在安全检查点收到取消请求。"""


class CancellationToken:
    def __init__(self) -> None:
        self._event = Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise WorkflowCancelled("任务已取消")

    def wait(self, timeout: float | None = None) -> bool:
        """等待取消请求，供子进程监视线程使用。"""
        return self._event.wait(timeout)


def emit(
    callback: ProgressCallback | None,
    stage: str,
    message: str,
    percent: float | None = None,
    level: str = "info",
    **data: Any,
) -> None:
    if callback:
        callback(ProgressEvent(stage, message, percent, level, data))
