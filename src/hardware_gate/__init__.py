"""硬件连接门控：只有运行目录允许打开真实仪器通信。

判定依据是驱动代码所在的仓库根，而不是当前工作目录：两套目录各自的
``agent_exp_env`` 都以可编辑安装指向本目录代码，因此从开发目录环境启动的
任何后端、实验子进程、脚本或 Notebook 都得到开发目录的根。

仓库根下存在 ``.lab_runtime`` 且内容等于该根的绝对路径时才允许连接；
标记复制到其他目录后路径不符，即失效。标记不进 git。
"""

from __future__ import annotations

import os
from pathlib import Path

MARKER_NAME = ".lab_runtime"


class HardwareAccessDenied(PermissionError):
    """当前代码目录不允许连接真实仪器。"""


def runtime_root() -> Path:
    """驱动代码所在的仓库根（``<根>/src/hardware_gate/__init__.py``）。"""
    return Path(__file__).resolve().parents[2]


def _normalized(path: str | Path) -> str:
    return os.path.normcase(os.path.normpath(str(path).strip()))


def denial_reason(root: Path | None = None) -> str | None:
    """返回不允许连接的原因；允许时返回 None。"""
    root = (root or runtime_root()).resolve()
    marker = root / MARKER_NAME
    if not marker.is_file():
        return f"{root} 不是运行目录（缺少 {MARKER_NAME}），开发目录禁止连接真实仪器"
    recorded = marker.read_text(encoding="utf-8-sig").strip()
    if _normalized(recorded) != _normalized(root):
        return (f"{marker} 记录的路径 {recorded!r} 与所在目录不符，"
                "标记无效，禁止连接真实仪器")
    return None


def hardware_allowed() -> bool:
    return denial_reason() is None


def require_hardware_access(target: str) -> None:
    """打开真实通信前调用；不允许时抛出 HardwareAccessDenied。"""
    reason = denial_reason()
    if reason is not None:
        raise HardwareAccessDenied(f"拒绝连接 {target}：{reason}")
