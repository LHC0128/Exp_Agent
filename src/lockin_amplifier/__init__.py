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
from .acquisition import (AcquisitionRequest, AcquisitionPlan, select_acquisition,
                          acquisition_module, nearest_scope_rate, result_rate, acquisition_metadata,
                          save_acquisition_metadata, load_acquisition_rate)

__all__ = [
    "HF2Instrument",
    "MFLIInstrument",
    "LockinInstrument",
    "lockin_capabilities",
    "ScopeConfig",
    "ScopeResult",
    "acquire_scope",
    "AcquisitionRequest", "AcquisitionPlan", "select_acquisition", "acquisition_module",
    "nearest_scope_rate", "result_rate", "acquisition_metadata",
    "save_acquisition_metadata", "load_acquisition_rate",
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
