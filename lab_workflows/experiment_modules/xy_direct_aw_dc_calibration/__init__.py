"""实验公开定义惰性加载，显式目录离线分析不依赖当前工作目录。"""
from .models import XYDirectAWDCCalibrationParams

__all__ = ["ADAPTER", "DEFINITION", "XYDirectAWDCCalibrationParams"]


def __getattr__(name):
    if name in ("ADAPTER", "DEFINITION"):
        from . import definition
        return getattr(definition, name)
    raise AttributeError(name)
