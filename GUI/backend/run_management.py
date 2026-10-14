"""历史运行的目录校验、名称和独立星标元数据。"""

from datetime import datetime
import os
from pathlib import Path
import re
import stat

from fastapi import HTTPException

from lab_workflows.experiment_modules.noise_spectrum_xy.state_io import read_state, write_state


def split_run_name(run_id: str) -> tuple[str, str]:
    match = re.match(r"^(\d{4}_\d{4}(?:\d{2})?)_(.+)$", run_id)
    return (match[1], match[2]) if match else ("", run_id)


def checked_run_path(root: Path, data_type: str, run_id: str, *, recursive=False) -> Path:
    if (not run_id or run_id in {".", ".."} or run_id.endswith((".", " "))
            or any(c in run_id for c in '/\\:\x00')):
        raise HTTPException(400, "运行 ID 必须是单个目录名")
    data_root = root / "data"
    base = data_root / data_type
    candidate = base / run_id
    if base.resolve().parent != data_root.resolve() or candidate.resolve().parent != base.resolve():
        raise HTTPException(400, "运行目录超出允许范围")
    pending = [data_root, base, candidate]
    try:
        while pending:
            entry = pending.pop()
            info = entry.lstat()
            if entry.is_symlink() or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                raise HTTPException(400, "含链接或联接点的运行目录不能修改")
            if recursive and entry != base and entry != data_root and entry.is_dir():
                pending.extend(entry.iterdir())
        if not candidate.is_dir():
            raise HTTPException(404, "运行目录不存在")
        for name in ("run_metadata.json", "run_metadata.json.tmp"):
            entry = candidate / name
            if os.path.lexists(entry):
                info = entry.lstat()
                if entry.is_symlink() or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                    raise HTTPException(400, "星标元数据不能使用链接或联接点")
    except FileNotFoundError as exc:
        raise HTTPException(404, "运行目录不存在") from exc
    except OSError as exc:
        raise HTTPException(409, f"运行目录无法访问，请刷新后重试: {exc}") from exc
    return candidate.resolve()


def read_starred(directory: Path) -> bool:
    path = directory / "run_metadata.json"
    try:
        if path.is_symlink() or path.resolve().parent != directory.resolve():
            return False
        metadata = read_state(path)
        return isinstance(metadata, dict) and metadata.get("starred") is True
    except (OSError, ValueError):
        return False


def save_starred(directory: Path, starred: bool) -> None:
    try:
        write_state(directory / "run_metadata.json", {"starred": starred})
    except OSError as exc:
        raise HTTPException(409, f"星标保存失败，请重试: {exc}") from exc


def rename_directory(directory: Path, name: str, config: dict) -> Path:
    if (not name.strip() or len(name) > 80 or name.endswith((".", " "))
            or any(ord(c) < 32 or ord(c) == 127 or c in '<>:"/\\|?*' for c in name)):
        raise HTTPException(400, "名称须为 1–80 字，不能含 Windows 非法字符或以点、空格结尾")
    prefix, _ = split_run_name(directory.name)
    if not prefix:
        timestamp = config.get("timestamp", "")
        if isinstance(timestamp, str) and re.fullmatch(r"\d{4}_\d{4}(?:\d{2})?", timestamp):
            prefix = timestamp
        else:
            try:
                prefix = datetime.fromisoformat(config["started_at"]).strftime("%m%d_%H%M%S")
            except (KeyError, TypeError, ValueError) as exc:
                raise HTTPException(400, "缺少有效的原始运行时间，不能重命名") from exc
    target = directory.with_name(f"{prefix}_{name}")
    if target.name == directory.name:
        return directory
    if target.resolve().parent != directory.parent or os.path.lexists(target):
        raise HTTPException(409, "目标运行目录已存在或超出允许范围，不能覆盖")
    try:
        directory.rename(target)
    except OSError as exc:
        raise HTTPException(409, f"重命名失败，原目录保留，请重试: {exc}") from exc
    return target
