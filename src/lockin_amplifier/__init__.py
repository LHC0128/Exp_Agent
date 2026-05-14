"""Zurich Instruments HF2 锁相放大器控制模块."""

from .instrument import HF2Instrument
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

__all__ = [
    "HF2Instrument",
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
