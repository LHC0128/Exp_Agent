"""探测链路频率响应标定实验模块。"""

__all__ = ["DEFINITION"]


def __getattr__(name):
    # 保持原公开入口；离线模块导入不提前构造适配器。
    if name != "DEFINITION":
        raise AttributeError(name)
    from .definition import DEFINITION
    return DEFINITION
