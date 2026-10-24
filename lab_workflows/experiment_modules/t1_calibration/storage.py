"""原子 NPZ 保存；正常写盘失败时保留单独抢救文件。"""
from pathlib import Path
import numpy as np


def save_npz(path, **values):
    path = Path(path)
    temporary = path.with_suffix(".npz.tmp")
    try:
        with temporary.open("wb") as stream:
            np.savez(stream, **values)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def save_shot(path, **values):
    try:
        save_npz(path, **values)
    except Exception as error:
        path = Path(path)
        try:
            save_npz(path.with_name("recovery_"+path.name), **values)
        except Exception as rescue:
            if hasattr(error, "add_note"):
                error.add_note(f"抢救保存亦失败：{rescue}")
        raise
