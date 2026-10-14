"""从采集旁文件读取实际采样率，不依赖仪器驱动。"""

import math
from pathlib import Path

import numpy as np


def load_acquisition_rate(path, fallback: float) -> float:
    """旧数据使用保存的速率，新波形优先使用逐段采集快照。"""
    path = Path(path)
    metadata = path.with_name(path.stem + "_acquisition.npz")
    rate = float(fallback)
    if metadata.exists():
        with np.load(metadata, allow_pickle=False) as data:
            rate = float(data["actual_rate_Sa_s"])
    if not math.isfinite(rate) or rate <= 0:
        raise ValueError("采集记录缺少有效的实际采样率")
    return rate
