"""Mx Y-RF 扫描轴和带符号幅度映射。"""

from __future__ import annotations

import numpy as np

from .models import MxYRFParams


def build_amplitude_axis(params: MxYRFParams) -> np.ndarray:
    """构造包含零点的对称带符号幅度轴。"""
    return np.linspace(
        params.y_rf_amp_start_vpp,
        params.y_rf_amp_stop_vpp,
        params.y_rf_amp_points,
        dtype=float,
    )


def build_frequency_axis(params: MxYRFParams) -> np.ndarray:
    """构造模式1线性频率轴。"""
    return np.linspace(
        params.frequency_start_hz,
        params.frequency_stop_hz,
        params.frequency_points,
        dtype=float,
    )


def signed_amplitude_hardware(value_vpp: float) -> tuple[float, float, bool]:
    """把逻辑带符号幅度转换为硬件绝对幅度、相位和输出状态。"""
    value = float(value_vpp)
    if np.isclose(value, 0.0, rtol=0.0, atol=1e-15):
        return 0.0, 0.0, False
    return abs(value), 180.0 if value < 0 else 0.0, True

