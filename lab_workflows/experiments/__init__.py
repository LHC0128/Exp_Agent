"""实验契约、脚本兼容适配器和统一注册表。"""

from .contracts import ExperimentDefinition
from .registry import get_experiment, list_experiments

__all__ = ["ExperimentDefinition", "get_experiment", "list_experiments"]
