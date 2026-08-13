"""Mx Keithley 6221 主场标定扫描轴。"""

from __future__ import annotations

import numpy as np

from .models import MxKeithley6221MainFieldCalibrationParams


def build_current_axis(params: MxKeithley6221MainFieldCalibrationParams) -> np.ndarray:
    count = int(round((params.keithley_current_stop_ma - params.keithley_current_start_ma) / params.keithley_current_step_ma))
    return np.linspace(params.keithley_current_start_ma, params.keithley_current_stop_ma, count + 1, dtype=float)


def predicted_center_hz(params: MxKeithley6221MainFieldCalibrationParams, current_ma: float) -> float:
    return float(
        params.keithley_reference_frequency_hz
        + params.keithley_initial_hz_per_ma * (float(current_ma) - params.keithley_reference_current_ma)
    )


def build_frequency_axis(params: MxKeithley6221MainFieldCalibrationParams, current_ma: float) -> np.ndarray:
    center = predicted_center_hz(params, current_ma)
    count = int(round(2.0 * params.frequency_half_width_hz / params.frequency_step_hz))
    return np.linspace(center - params.frequency_half_width_hz, center + params.frequency_half_width_hz, count + 1, dtype=float)
