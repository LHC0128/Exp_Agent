"""Mx Z 最优控制 XYZ 平衡场实验。"""

from .models import MxZOptimalControlXYZBalanceParams

__all__ = ["ADAPTER", "DEFINITION", "MxZOptimalControlXYZBalanceParams"]


def __getattr__(name):
    # 离线导入不构造适配器；保留原公开对象和模型导入。
    if name not in ("ADAPTER", "DEFINITION"):
        raise AttributeError(name)
    from .definition import ADAPTER, DEFINITION
    return {"ADAPTER": ADAPTER, "DEFINITION": DEFINITION}[name]
