"""Zurich Instruments HF2 / MFLI 的 Python Core 控制接口。"""

from .base import LockinInstrument, lockin_capabilities
from .hf2 import HF2Instrument
from .mfli import MFLIInstrument
from .config import (
    SignalInputConfig,
    OscillatorConfig,
    DemodulatorConfig,
    SignalOutputConfig,
    DAQConfig,
    DAQResult,
    AuxOutConfig,
)
from . import demod
from . import daq
from . import auxout
from .scope import ScopeConfig, ScopeResult, acquire_scope

__all__ = [
    "HF2Instrument",
    "MFLIInstrument",
    "LockinInstrument",
    "lockin_capabilities",
    "ScopeConfig",
    "ScopeResult",
    "acquire_scope",
    "SignalInputConfig",
    "OscillatorConfig",
    "DemodulatorConfig",
    "SignalOutputConfig",
    "DAQConfig",
    "DAQResult",
    "AuxOutConfig",
    "demod",
    "daq",
    "auxout",
]
