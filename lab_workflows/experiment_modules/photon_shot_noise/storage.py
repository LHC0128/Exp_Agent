"""采集与离线分析共用的原子 NPZ 写入，不依赖仪器。"""
from contextlib import suppress
from pathlib import Path
from tempfile import NamedTemporaryFile
import os
import numpy as np


def save_npz(path, **values):
    """写入或替换失败时保留上一份完整文件。"""
    path = Path(path)
    temporary = None
    try:
        with NamedTemporaryFile(dir=path.parent, suffix=".npz", delete=False) as stream:
            temporary = Path(stream.name)
            np.savez(stream, **values)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            with suppress(OSError):
                temporary.unlink(missing_ok=True)
