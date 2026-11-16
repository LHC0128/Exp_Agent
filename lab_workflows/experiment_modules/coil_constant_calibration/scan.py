"""采集侧的电流预测、引导中心估计与驱动电压换算；纯函数，不连接仪器。"""
from __future__ import annotations

import numpy as np

from .models import CoilConstantCalibrationParams


def drive_voltage_for_current(params: CoilConstantCalibrationParams, current_ma: float, *,
                              amps_per_drive_volt: float, offset_a: float = 0.0) -> float:
    """按实测直流 I–V 反算驱动电压：I = k·V + I_offset。"""
    if amps_per_drive_volt <= 0:
        raise ValueError("实测 I/V 必须为正")
    return (float(current_ma) * 1e-3 - float(offset_a)) / float(amps_per_drive_volt)


def quick_center_hz(frequency_hz, response_v) -> dict:
    """引导用的稳健中心估计：基线扣除后取半高质心，失败退回离散峰值。

    只用于预测窗口，不是正式拟合；正式中心由离线分析给出。
    """
    frequency = np.asarray(frequency_hz, dtype=float)
    response = np.asarray(response_v, dtype=float)
    valid = np.isfinite(frequency) & np.isfinite(response)
    frequency, response = frequency[valid], response[valid]
    if frequency.size < 3:
        return {"success": False, "center_hz": float("nan"), "method": "insufficient_points"}
    order = np.argsort(frequency)
    frequency, response = frequency[order], response[order]
    edge = max(1, frequency.size // 10)
    baseline = float(np.median(np.r_[response[:edge], response[-edge:]]))
    signal = response - baseline
    peak = int(np.argmax(signal))
    peak_value = float(signal[peak])
    if not np.isfinite(peak_value) or peak_value <= 0:
        return {"success": False, "center_hz": float(frequency[peak]), "method": "non_positive_peak"}
    half = 0.5 * peak_value
    above = signal >= half
    if np.count_nonzero(above) >= 2:
        weights = signal[above]
        center = float(np.sum(frequency[above] * weights) / np.sum(weights))
        method = "half_max_centroid"
    else:
        center = float(frequency[peak])
        method = "discrete_peak"
    return {"success": True, "center_hz": center, "method": method,
            "peak_frequency_hz": float(frequency[peak]), "peak_value_v": peak_value,
            "baseline_v": baseline}
