"""Mx 主磁场控制噪声谱扫描轴。"""

from __future__ import annotations

import numpy as np

from .models import MxMainFieldNoiseSpectrumParams


def build_scan_axes(
    params: MxMainFieldNoiseSpectrumParams,
) -> dict[str, np.ndarray]:
    """返回正控制频率、负失谐、共振频率和 GS200 电流。"""
    control_frequency_hz = np.linspace(
        params.control_frequency_start_hz,
        params.control_frequency_stop_hz,
        params.control_frequency_points,
        dtype=float,
    )
    signed_detuning_hz = -control_frequency_hz
    resonance_frequency_hz = (
        params.hf2_reference_frequency_hz - control_frequency_hz
    )
    main_field_current_ma = (
        resonance_frequency_hz - params.main_field_calibration_intercept_hz
    ) / params.main_field_calibration_hz_per_ma
    return {
        "control_frequency_hz": control_frequency_hz,
        "signed_detuning_hz": signed_detuning_hz,
        "resonance_frequency_hz": resonance_frequency_hz,
        "main_field_current_ma": main_field_current_ma,
    }
