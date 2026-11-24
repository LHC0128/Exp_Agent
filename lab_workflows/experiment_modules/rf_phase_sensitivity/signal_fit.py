"""同帧 CH3/CH4 电学相位与静态原子局部斜率的纯计算。"""
from __future__ import annotations

import math

import numpy as np

from ..z_chain_common.phase import decompose


def wrap_deg(value):
    return (value + 180.) % 360. - 180.


def _reference(t, voltage, nominal_hz):
    low, high = np.percentile(voltage, (0.5, 99.5))
    if high - low < 1.0:
        raise ValueError("CH3 共同参考电平跨度不足 1 V")
    levels = low + (high - low) * np.array([0.25, 0.5, 0.75])
    before, after = voltage[:-1], voltage[1:]
    low_out, mid_up, high_in = [np.flatnonzero((before < level) & (after >= level)) + 1 for level in levels]
    previous_low = np.searchsorted(low_out, high_in, side="right") - 1
    good = (previous_low >= 0) & np.r_[True, np.diff(previous_low) != 0] if high_in.size else np.array([], dtype=bool)
    a, b = low_out[previous_low[good]], high_in[good]
    mid = np.searchsorted(mid_up, a)
    j = mid_up[mid[mid < np.searchsorted(mid_up, b, side="right")]]
    if j.size < 16:
        raise ValueError("CH3 完整上升沿过少")
    dt = t[j] - t[j - 1]
    edges = t[j - 1] + (levels[1] - voltage[j - 1]) / (voltage[j] - voltage[j - 1]) * dt
    intervals = np.diff(edges) * nominal_hz
    steps = np.rint(intervals).astype(int)
    if np.any(steps < 1) or np.mean(steps != 1) > 0.01 or np.any(abs(intervals - steps) > 0.15):
        raise ValueError("CH3 上升沿不支持连续载波周期编号")
    cycles = np.r_[0, np.cumsum(steps)]
    centered = cycles - cycles.mean()
    period = np.dot(centered, edges - edges.mean()) / np.dot(centered, centered)
    first = edges.mean() - period * cycles.mean()
    residual = 360 * (edges - first - cycles * period) / period
    if not math.isclose(1 / period, nominal_hz, rel_tol=0.002) or np.max(np.abs(residual)) > 8:
        raise ValueError("CH3 参考频率或沿相位抖动不符合载波标定")
    return float(first), float(1 / period), dict(edge_count=int(j.size), edge_fit_rms_deg=float(np.std(residual)),
        edge_interpolation_bracket_deg=float(360 * np.max(dt) / period))


def _fit(design, values):
    coef, _, rank, singular = np.linalg.lstsq(design, values, rcond=None)
    if rank != design.shape[1] or singular[0] > 100 * singular[-1]:
        raise ValueError("载波/上下边带拟合病态")
    carrier, *side = coef[1::2] - 1j * coef[2::2]
    if abs(carrier) < 0.005:
        raise ValueError("CH4 线圈监视载波不足 5 mV 峰值")
    pm = am = 0j
    if side:
        upper, lower = side[0] / carrier, (side[1] / carrier).conjugate()
        pm, am = (upper - lower) / 1j, upper + lower
    residual = values - design @ coef
    r_squared = float(1. - np.var(residual) / np.var(values))
    return carrier, pm, am, r_squared


def measure_electrical(t, reference_v, coil_v, carrier_hz, modulation_hz=None):
    """以 CH3 拟合上升沿为相位零点，从 CH4 同帧三音分离小角度 PM 与 AM。"""
    t = t - t[0]
    if modulation_hz is not None and t[-1] * modulation_hz < 9.8:
        raise ValueError("同帧覆盖不足约 10 个调制周期")
    first, fc, diagnostics = _reference(t, reference_v, carrier_hz)
    frequencies = (fc,) if modulation_hz is None else (fc, fc + modulation_hz, fc - modulation_hz)
    design = np.empty((t.size, 1 + 2 * len(frequencies)))
    design[:, 0] = 1.
    for i, f in enumerate(frequencies):
        phase = (2 * np.pi * f) * (t - first)
        np.cos(phase, out=design[:, 1 + i * 2])
        np.sin(phase, out=design[:, 2 + i * 2])
    carrier, pm, am, r_squared = _fit(design, coil_v)
    segment_fits = [_fit(design[start:stop], coil_v[start:stop]) for start, stop in
                    zip(np.linspace(0, t.size, 5, dtype=int)[:-1], np.linspace(0, t.size, 5, dtype=int)[1:])]
    offsets = [float(np.angle(c / carrier, deg=True)) for c, _, _, _ in segment_fits]
    phase_span = max(offsets) - min(offsets)
    pm_scatter = max(abs(part - pm) for _, part, _, _ in segment_fits)
    return dict(reference_hz=fc, **diagnostics, carrier_center_deg=float(np.angle(carrier, deg=True)),
                carrier_v_pk=float(abs(carrier)), phase_span_deg=float(phase_span),
                phase_pk_rad=float(abs(pm)), phase_scatter_rad=float(pm_scatter), am_fraction_pk=float(abs(am)),
                pm_complex_rad_pk=[float(pm.real), float(pm.imag)], am_complex_fraction_pk=[float(am.real), float(am.imag)],
                fit_r_squared=r_squared, modulation_hz=modulation_hz,
                stable=bool(phase_span <= 0.5 and r_squared >= 0.85))


def best_tangent(phase_deg, values):
    """整周三分量模型的最大模切线；两支等价时取 [0, 180) 的支。"""
    (_, pos, neg), residual = decompose(phase_deg, values)
    product = pos * neg.conjugate()
    best = float((180 - np.angle(product, deg=True)) / 2 % 180)
    phi = np.deg2rad(best)
    tangent = 1j * (pos * np.exp(1j * phi) - neg * np.exp(-1j * phi))
    max_gain = abs(tangent)
    if not np.isfinite(tangent) or max_gain < 1e-6 or residual > 0.15 * max_gain:
        raise ValueError("整周相位响应幅度不足或模型残差过大，未找到可靠工作点")
    return best, float(wrap_deg(np.angle(tangent, deg=True) - 90.)), dict(
        tangent_v_per_command_rad=float(max_gain), residual_rms_v=residual)


_LEGACY_LAYOUT = dict(center_indices=(0, 3, 6), outer_pairs=((2, 1),), inner_pairs=((4, 5),))


def local_slopes(center, degrees, times_s, values, full_span_deg, layout=None):
    """同轮对称角度测 d(X+iY)/dφ；内外档用成对差分，跨对平均抵消线性时间漂移。

    ``layout`` 指定哪几个点是重复中心、哪些索引组成内/外档的 (正偏移, 负偏移) 对。
    默认沿用旧的 7 点顺序；新的采集顺序把内外档各测两次，成对差分后平均，
    线性漂移在一次斜率中自动抵消，不再只靠回归时间项。
    """
    dphi = np.deg2rad(wrap_deg(np.asarray(degrees) - center))
    time = np.asarray(times_s) - np.mean(times_s)
    volts = np.asarray(values, dtype=complex)
    layout = dict(_LEGACY_LAYOUT if layout is None else layout)
    design = np.stack([np.ones(dphi.size), dphi, time], axis=1)
    params, _, rank, _ = np.linalg.lstsq(design, volts, rcond=None)
    slope = params[1]
    modeled = design @ params
    if rank != 3 or dphi.size <= 3:
        raise ValueError("局部相位与时间漂移无法独立估计")
    se = math.sqrt(float(np.linalg.inv(design.T @ design)[1, 1] * np.sum((volts - modeled).imag**2) / (dphi.size - 3)))
    if np.max(np.abs(dphi[list(layout["center_indices"])])) > np.deg2rad(0.75):
        raise ValueError("Burst 工作点重复中心漂移超过 0.75°")

    def pair_slope(positive, negative):
        return float((volts[positive].imag - volts[negative].imag) / (dphi[positive] - dphi[negative]))

    outer_pairs = [pair_slope(*pair) for pair in layout["outer_pairs"]]
    inner_pairs = [pair_slope(*pair) for pair in layout["inner_pairs"]]
    floor = np.deg2rad(full_span_deg)
    outer_span = min(abs(dphi[positive] - dphi[negative]) for positive, negative in layout["outer_pairs"])
    inner_span = min(abs(dphi[positive] - dphi[negative]) for positive, negative in layout["inner_pairs"])
    if outer_span < 1.4 * floor or inner_span < 0.65 * floor:
        raise ValueError("CH4 实测局部内外档相位展宽不足")
    return dict(k0_v_per_rad=float(slope.imag), k0_se_v_per_rad=se, slope_x_v_per_rad=float(slope.real),
                outer_v_per_rad=float(np.mean(outer_pairs)), inner_v_per_rad=float(np.mean(inner_pairs)),
                outer_pairs_v_per_rad=outer_pairs, inner_pairs_v_per_rad=inner_pairs,
                drift_y_v_per_s=float(params[2].imag),
                local_model_rms_v=float(np.sqrt(np.mean(np.abs(volts - modeled)**2))),
                local_phase_offsets_deg=list(map(float, np.rad2deg(dphi))))
