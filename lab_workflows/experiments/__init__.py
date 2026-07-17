"""实验契约、脚本兼容适配器和统一注册表。"""

from __future__ import annotations

from .contracts import ExperimentDefinition


def get_experiment(experiment_id: str) -> ExperimentDefinition:
    """延迟加载注册表，避免实验定义导入契约时形成循环依赖。"""
    from .registry import get_experiment as _get_experiment

    return _get_experiment(experiment_id)


def list_experiments() -> list[ExperimentDefinition]:
    """延迟加载注册表，保持原有公共导入接口不变。"""
    from .registry import list_experiments as _list_experiments

    return _list_experiments()

__all__ = ["ExperimentDefinition", "get_experiment", "list_experiments"]
