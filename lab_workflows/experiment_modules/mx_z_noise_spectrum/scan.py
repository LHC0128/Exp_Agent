"""Mx Z 直流噪声谱扫描轴。"""

from __future__ import annotations

import numpy as np

from .models import MxZNoiseSpectrumParams


def build_scan_axes(params: MxZNoiseSpectrumParams) -> dict[str, np.ndarray]:
    """返回目标失谐、绝对共振频率和严格反算的 Z 电压。"""
    detuning_hz = np.linspace(
        params.target_detuning_start_hz,
        params.target_detuning_stop_hz,
        params.target_detuning_points,
        dtype=float,
    )
    absolute_frequency_hz = (
        params.zero_bias_reference_frequency_hz + detuning_hz
    )
    z_voltage_v = (
        absolute_frequency_hz - params.z_calibration_intercept_hz
    ) / params.z_calibration_hz_per_v
    return {
        "target_detuning_hz": detuning_hz,
        "absolute_resonance_frequency_hz": absolute_frequency_hz,
        "z_voltage_v": z_voltage_v,
    }
