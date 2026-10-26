"""静磁场灵敏度 typed workflow。"""

from .models import StaticSensitivityParams

__all__ = ["ADAPTER", "DEFINITION", "StaticSensitivityParams"]


def __getattr__(name):
    # 离线模块导入不构造适配器，访问实验定义时才解析项目配置。
    if name not in ("ADAPTER", "DEFINITION"):
        raise AttributeError(name)
    from .definition import ADAPTER, DEFINITION
    return {"ADAPTER": ADAPTER, "DEFINITION": DEFINITION}[name]
