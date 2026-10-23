"""保留在用实验复用的相位算法；归档实验其余实现按需导入。"""

from importlib import import_module
from pathlib import Path

__path__.append(str(Path(__file__).resolve().parents[3] / "old/experiments/lab_workflows/experiment_modules" / __name__.rsplit(".", 1)[-1]))


def __getattr__(name: str):
    if name in {"ADAPTER", "DEFINITION"}:
        return getattr(import_module(".definition", __name__), name)
    if name == "MxKeithley6221OptimalControlRFParams":
        return getattr(import_module(".models", __name__), name)
    raise AttributeError(name)
