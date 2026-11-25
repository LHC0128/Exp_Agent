"""MFLI 整周正弦切线拟合与局部静态斜率线性回归。"""
from __future__ import annotations

import math

import numpy as np

from ..z_chain_common.phase import decompose


def wrap_deg(value):
    return (value + 180.) % 360. - 180.


def best_tangent(phase_deg, values):
    """整周三分量模型的最大模切线；两支等价时取 [0, 180) 的支。"""
    (_, pos, neg), residual = decompose(phase_deg, values)
    product = pos * neg.conjugate()
    best = float((180 - np.angle(product, deg=True)) / 2 % 180)
    phi = np.deg2rad(best)
    tangent = 1j * (pos * np.exp(1j * phi) - neg * np.exp(-1j * phi))
    max_gain = abs(tangent)
    if not np.isfinite(tangent) or max_gain < 1e-6:
        raise ValueError("整周相位响应幅度不足，未找到可靠工作点")
    return best, float(wrap_deg(np.angle(tangent, deg=True) - 90.)), dict(
        tangent_v_per_command_rad=float(max_gain), residual_rms_v=residual)


def _linear_slope(dphi, volts):
    """在给定子集上线性回归斜率；返回虚部斜率。"""
    design = np.stack([np.ones(dphi.size), dphi], axis=1)
    params, _, rank, _ = np.linalg.lstsq(design, volts, rcond=None)
    if rank != 2 or dphi.size < 3:
        raise ValueError("局部相位子集太少，无法估计斜率")
    return float(params[1].imag)


def _optional_linear_slope(dphi, volts, label, warnings):
    try:
        return _linear_slope(dphi, volts)
    except ValueError:
        warnings.append(f"{label}点数或跨度不足，跳过单独线性一致性检验")
        return None


def local_slopes(center, degrees, times_s, values, full_span_deg, center_indices=(0, 2)):
    """细角度局部扫描：用首尾中心点估计线性漂移并扣除，再回归 d(X+iY)/dφ。

    扫描是单方向的，扫描段内时间与角度共线，不能在同一子集里同时回归时间和斜率；
    改用扫描首尾两次中心测量估计漂移，扣除后再分内/外半区检查局部斜率并记为告警。
    """
    dphi = np.deg2rad(wrap_deg(np.asarray(degrees) - center))
    time = np.asarray(times_s) - np.mean(times_s)
    volts = np.asarray(values, dtype=complex)
    first, last = (int(center_indices[0]), int(center_indices[-1]))
    interval = time[last] - time[first]
    drift = (volts[last] - volts[first]) / interval if interval > 0 else 0j
    corrected = volts - drift * time
    design = np.stack([np.ones(dphi.size), dphi], axis=1)
    params, _, rank, _ = np.linalg.lstsq(design, corrected, rcond=None)
    modeled = design @ params
    if rank != 2 or dphi.size < 6:
        raise ValueError("局部相位无法拟合")
    se = math.sqrt(float(np.linalg.inv(design.T @ design)[1, 1] * np.sum((corrected - modeled).imag**2)
                         / (dphi.size - 2)))
    warnings = []
    if abs(dphi[first]) > np.deg2rad(0.75) or abs(dphi[last]) > np.deg2rad(0.75):
        warnings.append("工作点重复中心漂移超过 0.75°")
    floor = np.deg2rad(full_span_deg)
    if np.max(np.abs(dphi)) < 0.98 * floor:
        warnings.append("局部角度展宽低于 98% 名义跨度")
    inner_mask = np.abs(dphi) <= 0.5 * floor
    outer_mask = np.abs(dphi) >= 0.5 * floor
    outer = _optional_linear_slope(dphi[outer_mask], corrected[outer_mask], "局部外半区", warnings)
    inner = _optional_linear_slope(dphi[inner_mask], corrected[inner_mask], "局部内半区", warnings)
    return dict(k0_v_per_rad=float(params[1].imag), k0_se_v_per_rad=se, slope_x_v_per_rad=float(params[1].real),
                outer_v_per_rad=outer, inner_v_per_rad=inner,
                drift_y_v_per_s=float(drift.imag),
                local_model_rms_v=float(np.sqrt(np.mean(np.abs(corrected - modeled)**2))),
                local_phase_offsets_deg=list(map(float, np.rad2deg(dphi))),
                local_warnings=warnings)
