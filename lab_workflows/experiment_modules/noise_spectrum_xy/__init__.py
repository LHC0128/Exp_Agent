"""XY 正弦控制噪声谱实验。"""

from .models import NoiseSpectrumXYParams

__all__ = ["ADAPTER", "DEFINITION", "NoiseSpectrumXYParams"]


def __getattr__(name):
    """仅注册或启动实验时加载适配器，导入模型和工作流不定位运行目录。"""
    if name in {"ADAPTER", "DEFINITION"}:
        from .definition import ADAPTER, DEFINITION

        return ADAPTER if name == "ADAPTER" else DEFINITION
    raise AttributeError(f"模块 {__name__!r} 没有属性 {name!r}")
