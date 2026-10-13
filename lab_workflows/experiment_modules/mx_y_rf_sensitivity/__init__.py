"""Mx Y 向 RF 场灵敏度实验。"""

from .models import MxYRFParams

__all__ = ["ADAPTER", "DEFINITION", "MxYRFParams"]


def __getattr__(name):
    # 分析算法可独立导入，读取实验定义时才解析仓库默认参数入口。
    if name not in ("ADAPTER", "DEFINITION"):
        raise AttributeError(name)
    from .definition import ADAPTER, DEFINITION
    return {"ADAPTER": ADAPTER, "DEFINITION": DEFINITION}[name]
