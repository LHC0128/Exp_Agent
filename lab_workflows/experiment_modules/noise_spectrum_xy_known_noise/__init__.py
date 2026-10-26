"""XY 控制已知可控噪声谱实验。"""

from .models import NoiseSpectrumXYKnownNoiseParams

__all__ = ["ADAPTER", "DEFINITION", "NoiseSpectrumXYKnownNoiseParams"]


def __getattr__(name):
    # 离线导入不构造适配器；保留原公开对象和模型导入。
    if name not in ("ADAPTER", "DEFINITION"):
        raise AttributeError(name)
    from .definition import ADAPTER, DEFINITION
    return {"ADAPTER": ADAPTER, "DEFINITION": DEFINITION}[name]
