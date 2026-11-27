"""多温控分段噪声的 Welch 功率谱；不连接仪器、不写文件。

每段独立温控窗口录得的连续波形分别计算 Welch PSD，再按段数等权平均，不跨窗口拼接波形。
实现由 rf_phase_sensitivity.analysis_steps 迁出，行为与迁移前一致。
"""
from __future__ import annotations

import math

import numpy as np
from scipy.signal import welch

from .tone_fit import sample_grid

MIN_NOISE_HZ = 1.0
MIN_WELCH_SEGMENTS = 2


def _waveform(values, time_s):
    y = np.asarray(values, dtype=float)
    if y.shape != time_s.shape or not np.isfinite(y).all():
        raise ValueError("XY 波形非有限或长度与时间轴不一致")
    return y


def welch_spectrum(time_s, y_v, rate_sa_s, segment_s):
    """单段波形的单边 PSD；短帧只提供如实标明分辨率的探测器诊断谱。"""
    t, rate = sample_grid(time_s, rate_sa_s)
    y = _waveform(y_v, t)
    # 请求分辨率为 1 / segment_s，短帧只提供如实标明分辨率的探测器诊断谱。
    requested = max(8, math.ceil(rate * segment_s))
    nperseg = min(requested, y.size)
    noverlap = nperseg // 2
    nsegments = 1 + (y.size - nperseg) // (nperseg - noverlap)
    frequency, psd = welch(y, fs=rate, window="hann", nperseg=nperseg, noverlap=noverlap,
                           detrend="linear", return_onesided=True, scaling="density")
    keep = frequency >= MIN_NOISE_HZ
    if not keep.any() or not np.isfinite(psd).all() or (psd < 0).any():
        raise ValueError("没有有效的 1 Hz 以上噪声谱")
    details = dict(rate_sa_s=rate, samples=int(y.size), duration_s=float(t[-1] + 1.0 / rate),
                   nperseg=nperseg, noverlap=noverlap,
                   overlap_fraction=noverlap / nperseg, segments=nsegments, window="hann", detrend="linear",
                   one_sided=True, segment_s=nperseg / rate, requested_segment_s=segment_s,
                   resolution_hz=rate / nperseg, resolution_met=nperseg == requested)
    return frequency[keep], psd[keep], details


def segment_average(frames, segment_s):
    """对多个独立温控窗口录得的连续段分别计算 Welch PSD，并按段数加权（等长即等权）平均。"""
    if not frames:
        raise ValueError("缺少可计算 Welch PSD 的噪声分段")
    freq_ref = None
    nperseg_ref = None
    weighted_psd = None
    first_details = None
    total_samples = 0
    total_segments = 0
    total_duration_s = 0.0
    per_frame_segments = []
    resolution_met = True
    for time_s, y_v, rate_sa_s in frames:
        freq, psd, details = welch_spectrum(time_s, y_v, rate_sa_s, segment_s)
        if freq_ref is None:
            freq_ref = freq
            nperseg_ref = details["nperseg"]
            weighted_psd = psd * details["segments"]
            first_details = details
        else:
            if details["nperseg"] != nperseg_ref or not np.allclose(freq, freq_ref, rtol=1e-9, atol=1e-9):
                raise ValueError("各噪声分段的实际采样率或 Welch 频率网格不一致，拒绝拼接平均")
            weighted_psd = weighted_psd + psd * details["segments"]
        total_samples += details["samples"]
        total_segments += details["segments"]
        total_duration_s += details["duration_s"]
        per_frame_segments.append(details["segments"])
        resolution_met = resolution_met and bool(details["resolution_met"])
    combined = dict(
        first_details,
        samples=int(total_samples),
        duration_s=float(total_duration_s),
        segment_duration_s=float(first_details["duration_s"]),
        segments=int(total_segments),
        acquisition_segments=len(frames),
        per_frame_segments=per_frame_segments,
        resolution_met=resolution_met,
    )
    return freq_ref, weighted_psd / total_segments, combined
