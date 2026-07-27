"""Mx 主磁场示波器噪声谱扫描轴。"""

from __future__ import annotations

import numpy as np

from .models import MxMainFieldScopeNoiseSpectrumParams


def build_scan_axes(
    params: MxMainFieldScopeNoiseSpectrumParams,
) -> dict[str, np.ndarray]:
    """返回目标 Larmor 控制频率和对应的 GS200 电流。"""
    control_frequency_hz = np.linspace(
        params.control_frequency_start_hz,
        params.control_frequency_stop_hz,
        params.control_frequency_points,
        dtype=float,
    )
    main_field_current_ma = (
        control_frequency_hz - params.main_field_calibration_intercept_hz
    ) / params.main_field_calibration_hz_per_ma
    return {
        "control_frequency_hz": control_frequency_hz,
        "larmor_frequency_hz": control_frequency_hz.copy(),
        "main_field_current_ma": main_field_current_ma,
    }
