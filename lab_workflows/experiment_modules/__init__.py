"""显式参数模型驱动的正式实验模块。"""

from pathlib import Path

# 保留历史 Python 导入路径；只有显式导入归档模块时才加载其实现。
__path__.append(str(Path(__file__).resolve().parents[2] / "old/experiments/lab_workflows/experiment_modules"))
