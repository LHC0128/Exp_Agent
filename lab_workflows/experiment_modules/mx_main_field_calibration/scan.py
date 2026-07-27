"""Mx 主磁场标定扫描轴。"""

from __future__ import annotations

import numpy as np

from .models import MxMainFieldCalibrationParams


def build_main_field_axis(params: MxMainFieldCalibrationParams) -> np.ndarray:
    count = int(
        round(
            (params.main_field_stop_ma - params.main_field_start_ma)
            / params.main_field_step_ma
        )
    )
    return np.linspace(
        params.main_field_start_ma,
        params.main_field_stop_ma,
        count + 1,
        dtype=float,
    )


def predicted_center_hz(
    params: MxMainFieldCalibrationParams, current_ma: float
) -> float:
    return float(
        params.main_field_reference_frequency_hz
        + params.main_field_initial_hz_per_ma
        * (float(current_ma) - params.main_field_reference_current_ma)
    )


def build_frequency_axis(
    params: MxMainFieldCalibrationParams, current_ma: float
) -> np.ndarray:
    center = predicted_center_hz(params, current_ma)
    count = int(
        round(2.0 * params.frequency_half_width_hz / params.frequency_step_hz)
    )
    return np.linspace(
        center - params.frequency_half_width_hz,
        center + params.frequency_half_width_hz,
        count + 1,
        dtype=float,
    )
