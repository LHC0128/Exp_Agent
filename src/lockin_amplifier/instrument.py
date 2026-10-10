"""锁相放大器的旧导入入口；实现分别位于 base、hf2 和 mfli。"""

from .base import LockinInstrument, lockin_capabilities, _zi
from .hf2 import HF2Instrument
from .mfli import MFLIInstrument

__all__ = ["HF2Instrument", "MFLIInstrument", "LockinInstrument", "lockin_capabilities"]
