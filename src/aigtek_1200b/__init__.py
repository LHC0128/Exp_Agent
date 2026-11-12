"""Aigtek 1200B 放大器基础控制，不包含波形生成或实验编排。"""

from .instrument import (
    Aigtek1200BInstrument,
    AmplifierCommandError,
    CommandReceipt,
    SafetyCheck,
)

__all__ = ["Aigtek1200BInstrument", "AmplifierCommandError", "CommandReceipt", "SafetyCheck"]
