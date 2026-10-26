"""Z 线圈电感效应频率响应实验模块。"""

from .models import ZCoilInductanceFrequencyResponseParams

__all__ = [
    "ADAPTER",
    "DEFINITION",
    "ZCoilInductanceFrequencyResponseParams",
]


def __getattr__(name):
    # 离线导入不构造适配器；保留原公开对象和模型导入。
    if name not in ("ADAPTER", "DEFINITION"):
        raise AttributeError(name)
    from .definition import ADAPTER, DEFINITION
    return {"ADAPTER": ADAPTER, "DEFINITION": DEFINITION}[name]
