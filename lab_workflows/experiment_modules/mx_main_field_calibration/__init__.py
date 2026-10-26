"""Mx 主磁场频率标定实验。"""

from .models import MxMainFieldCalibrationParams

__all__ = ["ADAPTER", "DEFINITION", "MxMainFieldCalibrationParams"]


def __getattr__(name):
    # 离线算法导入不解析设备配置；读取实验定义时才构建适配器。
    if name not in ("ADAPTER", "DEFINITION"):
        raise AttributeError(name)
    from .definition import ADAPTER, DEFINITION
    return {"ADAPTER": ADAPTER, "DEFINITION": DEFINITION}[name]
