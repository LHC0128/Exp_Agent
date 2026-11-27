"""基带频响的零频 √Lorentzian 归一化与相位灵敏度折算；不连接仪器、不写文件。

A05 的实测增益 |H(f)| = |Y_pk(f)| / phase_pk(f) 以 0 Hz 为中心的 √Lorentzian 线型
H0 / sqrt(1 + (f/fc)²) 拟合，并用零频截距 H0 归一化；A06 的探测器 ASD 在相邻合格频点之间
按对数频率—对数幅值插值折算相位 ASD，另用同一 √Lorentzian 模型做全频段回补。
实现由 rf_phase_sensitivity.analysis_steps 迁出，行为与迁移前一致。
"""
from __future__ import annotations

import math

import numpy as np

from . import signal_fit
from .tone_fit import MAINS_HARMONICS_HZ

CLOSURE_RANGE = (0.8, 1.2)


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        value = float(value)
    except OverflowError:
        return None
    return value if math.isfinite(value) else None


def transfer_bands(configured, accepted):
    """只连接配置表中的相邻合格频点，不用复测替换主测。"""
    bands, band = [], []
    for f in configured:
        if f in accepted:
            band.append(f)
        else:
            if len(band) >= 2:
                bands.append(band)
            band = []
    if len(band) >= 2:
        bands.append(band)
    return bands


def fit_transfer_lorentzian(rows, accepted, k0):
    """按 √Lorentzian 拟合 |H(f)| 并把 H0 归一化增益写回行内。"""
    ordered = sorted(accepted.values(), key=lambda row: row["frequency_hz"])
    if len(ordered) < 2:
        return dict(available=False, reason="有效主测频点不足 2 个，无法拟合 √Lorentzian")
    clean = [row for row in ordered if not any(abs(row["frequency_hz"] - m) <= 0.5 for m in MAINS_HARMONICS_HZ)]
    fit_rows = clean if len(clean) >= 2 else ordered
    freqs = [row["frequency_hz"] for row in fit_rows]
    gains = [row["h_v_per_rad"] for row in fit_rows]
    ses = [row.get("h_fit_se_v_per_rad") for row in fit_rows]
    fit = signal_fit.fit_sqrt_lorentzian(freqs, gains, ses if all(s is not None for s in ses) else None)
    if not fit.get("available"):
        return fit
    h0 = fit["h0_v_per_rad"]
    h0_se = fit.get("h0_se_v_per_rad")
    valid_h0_se = h0_se is not None and math.isfinite(h0_se) and h0_se >= 0
    for row in rows:
        if (h := row.get("h_v_per_rad")) is not None and h > 0:
            gain = _number(h / h0)
            row["normalized_h0_gain"] = gain
            if gain is not None and (h_se := row.get("h_fit_se_v_per_rad")) is not None:
                rel_h = h_se / h0
                rel_h0 = (gain * (h0_se / h0)) if valid_h0_se else 0.0
                row["normalized_h0_gain_se"] = _number(math.hypot(rel_h, rel_h0))
    if k0 is not None and math.isfinite(k0) and abs(k0) > 0:
        fit["h0_to_k0_ratio"] = _number(h0 / abs(k0))
    return fit


def refer_phase_asd(frequency, asd, bands, accepted, lorentzian_fit=None):
    """按相邻硬合格频点的 |H| 做对数频率-对数幅值插值并折算相位 ASD。

    bands 只由硬合格（accepted=True 且 h 为正有限）的相邻频点组成，是否 quality_passed
    不参与筛选：只要频带内至少有 2 个这样的频点就照常输出 phase_asd_* 列与折算曲线，
    质量不足由调用方标 advisory / referral_warnings。
    """
    h = np.full(frequency.shape, np.nan)
    phase = np.full(frequency.shape, np.nan)
    band_ids = np.zeros(frequency.shape, dtype=int)
    metrics = []
    for band_id, band in enumerate(bands, 1):
        indices = np.flatnonzero((frequency >= band[0]) & (frequency <= band[-1]))
        if not indices.size or len(band) < 2:
            continue
        gains = [accepted[f]["h_v_per_rad"] for f in band]
        if any(not (value is not None and math.isfinite(value) and value > 0) for value in gains):
            continue
        h_band = np.exp(np.interp(np.log(frequency[indices]), np.log(band), np.log(gains)))
        with np.errstate(divide="ignore", over="ignore", invalid="ignore"):
            values = asd[indices] / h_band
            keep = np.isfinite(values) & np.isfinite(values * 1e6) & (h_band > 0)
        indices, values = indices[keep], values[keep]
        if not indices.size:
            continue
        h[indices], phase[indices], band_ids[indices] = h_band[keep], values, band_id
        median = float(np.median(values))
        middle = int(np.argmin(abs(frequency[indices] - (band[0] + band[-1]) / 2)))
        metrics.append(dict(transfer_band=band_id, bins=int(indices.size),
                            frequency_min_hz=float(frequency[indices[0]]),
                            frequency_max_hz=float(frequency[indices[-1]]),
                            midpoint_bin_hz=float(frequency[indices[middle]]),
                            midpoint_rad_per_sqrt_hz=float(values[middle]),
                            midpoint_urad_per_sqrt_hz=float(values[middle] * 1e6),
                            midpoint_deg_per_sqrt_hz=float(math.degrees(values[middle])),
                            median_rad_per_sqrt_hz=median, median_urad_per_sqrt_hz=median * 1e6,
                            median_deg_per_sqrt_hz=math.degrees(median)))

    h_lor = np.full(frequency.shape, np.nan)
    phase_lor = np.full(frequency.shape, np.nan)
    lor_summary = dict(available=False, reason="缺少有效的 √Lorentzian 频响拟合")
    if metrics and lorentzian_fit and lorentzian_fit.get("available"):
        h0 = float(lorentzian_fit["h0_v_per_rad"])
        fc = float(lorentzian_fit["fc_hz"])
        if h0 > 0 and fc > 0 and frequency.size:
            with np.errstate(divide="ignore", over="ignore", invalid="ignore"):
                cand_h = h0 / np.sqrt(1.0 + (frequency / fc) ** 2)
                cand_p = asd / cand_h
                valid = np.isfinite(cand_p) & np.isfinite(cand_p * 1e6) & (cand_h > 0)
            if valid.any():
                h_lor[valid] = cand_h[valid]
                phase_lor[valid] = cand_p[valid]
                vals = cand_p[valid]
                med_lor = float(np.median(vals))
                lor_summary = dict(
                    available=True,
                    bins=int(valid.sum()),
                    frequency_min_hz=float(frequency[valid][0]),
                    frequency_max_hz=float(frequency[valid][-1]),
                    h0_v_per_rad=h0,
                    fc_hz=fc,
                    median_rad_per_sqrt_hz=med_lor,
                    median_urad_per_sqrt_hz=med_lor * 1e6,
                )
    return h, phase, band_ids, metrics, h_lor, phase_lor, lor_summary
