"""Mx Y RF Probe 光功率与 PZT 失谐优化实验。"""

from .models import MxYRFProbeDetuningOptimizationParams

__all__ = [
    "ADAPTER",
    "DEFINITION",
    "MxYRFProbeDetuningOptimizationParams",
]


def __getattr__(name):
    # 离线导入不构造适配器；保留原公开对象和模型导入。
    if name not in ("ADAPTER", "DEFINITION"):
        raise AttributeError(name)
    from .definition import ADAPTER, DEFINITION
    return {"ADAPTER": ADAPTER, "DEFINITION": DEFINITION}[name]
