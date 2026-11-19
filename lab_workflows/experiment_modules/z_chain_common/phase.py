"""射频相位轴与相位响应分量的公共纯计算；不连接仪器、不写文件。"""
from __future__ import annotations

import math

import numpy as np

# 角度一律按这个精度取整，避免 0.1+0.2 式的浮点尾数进 CSV 与摘要。
ROUND_DIGITS = 9


def phase_grid(start_deg: float, stop_deg: float, step_deg: float, *,
               include_stop: bool = True) -> list[float]:
    """角度网格（度）。

    ``include_stop=True`` 时含两端，步进除不尽时补上终点；``include_stop=False`` 时
    只取 ``start + k·step < stop`` 的点，用于整周扫描避免 0° 与 360° 重复。
    """
    for name, value in (("Y_RF_PHASE_START_DEG", start_deg), ("Y_RF_PHASE_STOP_DEG", stop_deg),
                        ("Y_RF_PHASE_STEP_DEG", step_deg)):
        if not math.isfinite(value):
            raise ValueError(f"{name} 必须是有限数")
    if step_deg <= 0:
        raise ValueError("Y_RF_PHASE_STEP_DEG 必须为正")
    if stop_deg < start_deg:
        raise ValueError("Y_RF_PHASE_STOP_DEG 不能小于 Y_RF_PHASE_START_DEG")
    count = int(math.floor((stop_deg - start_deg) / step_deg + 1e-9))
    last = count + 1 if include_stop else count
    angles = [round(start_deg + index * step_deg, ROUND_DIGITS) for index in range(last)]
    if include_stop and angles[-1] < stop_deg - 1e-9:
        angles.append(round(stop_deg, ROUND_DIGITS))
    if not angles:
        raise ValueError("射频相位角度列表为空，请检查起点、终点与步进")
    return angles


def harmonic_matrix(phases_deg):
    """分解矩阵：列依次为 1、e^{iφ}、e^{−iφ}（φ 为写入 DG 的 burst 相位）。"""
    phi = np.radians(np.asarray(phases_deg, dtype=float))
    return np.stack([np.ones_like(phi), np.exp(1j * phi), np.exp(-1j * phi)], axis=1).astype(complex)


def decompose(phases_deg, values):
    """最小二乘解 (c0, c+1, c−1) 与残差 RMS；相位数为 3 时残差恒为 0。"""
    matrix = harmonic_matrix(phases_deg)
    values = np.asarray(values, dtype=complex)
    coefficients, *_ = np.linalg.lstsq(matrix, values, rcond=None)
    residual = values - matrix @ coefficients
    return coefficients, float(np.sqrt(np.mean(np.abs(residual) ** 2)))
