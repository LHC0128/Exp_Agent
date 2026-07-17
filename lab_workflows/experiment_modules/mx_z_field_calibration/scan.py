"""Mx 高主场 Z 标定扫描轴。"""

from __future__ import annotations

import numpy as np

from .models import MxZFieldCalibrationParams


def polarity_sign(params: MxZFieldCalibrationParams) -> float:
    if params.z_prediction_polarity == "positive_increases_frequency":
        return 1.0
    if params.z_prediction_polarity == "positive_decreases_frequency":
        return -1.0
    raise ValueError("Z_PREDICTION_POLARITY 必须明确选择")


def build_z_axis(params: MxZFieldCalibrationParams) -> np.ndarray:
    count = int(round((params.z_bias_stop_v - params.z_bias_start_v) / params.z_bias_step_v))
    return np.linspace(params.z_bias_start_v, params.z_bias_stop_v, count + 1, dtype=float)


def predicted_center_hz(params: MxZFieldCalibrationParams, z_bias_v: float) -> float:
    return float(
        params.zero_bias_center_frequency_hz
        + polarity_sign(params) * params.z_initial_hz_per_v * float(z_bias_v)
    )


def build_frequency_axis(params: MxZFieldCalibrationParams, z_bias_v: float) -> np.ndarray:
    center = predicted_center_hz(params, z_bias_v)
    count = int(round(2.0 * params.frequency_half_width_hz / params.frequency_step_hz))
    return np.linspace(
        center - params.frequency_half_width_hz,
        center + params.frequency_half_width_hz,
        count + 1,
        dtype=float,
    )

