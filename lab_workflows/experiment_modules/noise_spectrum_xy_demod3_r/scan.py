"""Demod3 R 二维扫描的纯计算工具。"""

from __future__ import annotations

import math
from collections.abc import Iterator

import numpy as np

from .models import NoiseSpectrumXYDemod3RParams


def build_scan_axes(params: NoiseSpectrumXYDemod3RParams) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """构造控制频率、包络电压和 Demod3 解调频率轴。"""
    control = np.linspace(params.control_freq_start_hz, params.control_freq_stop_hz, params.control_freq_points)
    envelope = (control - params.xy_ctrl_b_hz) / params.xy_ctrl_k_hz_per_v
    demod3 = np.linspace(params.demod3_freq_start_hz, params.demod3_freq_stop_hz, params.demod3_freq_points)
    return control, envelope, demod3


def iter_frequency_batches(point_count: int, batch_points: int) -> Iterator[range]:
    """按温控恢复批次切分 Demod3 频率索引。"""
    if point_count < 1 or batch_points < 1:
        raise ValueError("扫描点数和批次点数必须为正整数")
    for start in range(0, point_count, batch_points):
        yield range(start, min(point_count, start + batch_points))


def estimate_scan_duration_s(params: NoiseSpectrumXYDemod3RParams) -> float:
    """估计二维扫描时间，不包含升温、校相和通信开销。"""
    measurement = params.control_freq_points * params.demod3_freq_points * (
        params.demod3_freq_settle_time_s + params.demod3_acquisition_duration_s
    )
    recovery_batches = math.ceil(params.demod3_freq_points / params.temp_recovery_batch_points)
    recovery = params.control_freq_points * recovery_batches * params.temp_recovery_time_s
    return float(measurement + recovery)
