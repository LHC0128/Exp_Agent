"""T2 光学 FID 标定新模式模块。"""

from .models import T2CalibrationParams

__all__ = ["ADAPTER", "DEFINITION", "T2CalibrationParams"]


def __getattr__(name):
    # 离线算法导入不解析设备配置；读取实验定义时才构建适配器。
    if name not in ("ADAPTER", "DEFINITION"):
        raise AttributeError(name)
    from .definition import ADAPTER, DEFINITION
    return {"ADAPTER": ADAPTER, "DEFINITION": DEFINITION}[name]
