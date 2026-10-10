"""旧 DG4000 导入入口；具体实现位于 signal_generator.dg4000。"""

from signal_generator.dg4000 import DEFAULT_TIMEOUT, DG4000Instrument
from signal_generator.types import (
    BurstMode, ModType, OutputPolarity, PulseTransition, SweepSpacing,
    SyncPolarity, VoltageUnit, WaveShape,
)

__all__ = [
    "DG4000Instrument",
    "DEFAULT_TIMEOUT",
    "WaveShape",
    "ModType",
    "SweepSpacing",
    "BurstMode",
    "PulseTransition",
    "VoltageUnit",
    "OutputPolarity",
    "SyncPolarity",
]
