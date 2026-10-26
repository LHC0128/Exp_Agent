"""Mx Y 最优控制 RF 频率响应实验。"""
from .models import MxYOptimalControlRFFrequencyResponseParams
__all__ = ["ADAPTER", "DEFINITION", "MxYOptimalControlRFFrequencyResponseParams"]


def __getattr__(name):
    # 离线导入不构造适配器；保留原公开对象和模型导入。
    if name not in ("ADAPTER", "DEFINITION"):
        raise AttributeError(name)
    from .definition import ADAPTER, DEFINITION
    return {"ADAPTER": ADAPTER, "DEFINITION": DEFINITION}[name]
