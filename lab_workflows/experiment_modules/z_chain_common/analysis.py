"""三分量相位响应的公共最小二乘；不连接仪器、不写文件。"""
from __future__ import annotations

import numpy as np


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
