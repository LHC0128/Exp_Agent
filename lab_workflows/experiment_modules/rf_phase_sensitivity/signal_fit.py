"""MFLI 整周切线对齐、理论式 (2.2.2) 色散拟合与零频 sqrt-Lorentzian 频响拟合。"""
from __future__ import annotations

import math

import numpy as np
from scipy.optimize import least_squares

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


def eq222_y(phi_rad: np.ndarray, y0: float, k0: float, b_r2: float, phi_c_rad: float, v0: float = 0.0) -> np.ndarray:
    """理论式 (2.2.2) Y(φ) = Y0 + A·v / (1 + v²)，按 (Y0, K0=A·r, b=r², v0, φc) 参数化以兼容弱场 r→0。"""
    s = np.sin(phi_rad - phi_c_rad)
    if b_r2 <= 1e-12 and abs(v0) <= 1e-12:
        return y0 + k0 * s
    r = math.sqrt(max(b_r2, 1e-12))
    v = v0 + r * s
    amp = k0 / r
    return y0 + amp * v / (1.0 + v * v)


def fit_eq222_full_cycle(phi_deg: np.ndarray, y_v: np.ndarray, *, phi_c_init_deg: float = 0.0) -> dict:
    """用带静态微失衡偏置 v0 的理论式 (2.2.2) 拟合 0–360° 整周色散波形。"""
    phi_rad = np.deg2rad(np.asarray(phi_deg, dtype=float))
    y_arr = np.asarray(y_v, dtype=float)
    phi_c0 = math.radians(phi_c_init_deg)
    y0_init = float(np.median(y_arr))
    ptp = max(float(np.ptp(y_arr)), 1e-4)
    # 先在 phi_c 附近确定正确符号：斜率为正或负。
    dphi = np.deg2rad(wrap_deg(np.asarray(phi_deg, dtype=float) - phi_c_init_deg))
    sign_guess = 1.0 if np.sum(dphi * (y_arr - y0_init)) >= 0 else -1.0
    k_init = sign_guess * max(ptp, 0.05)

    def _fit_start(r_init: float):
        def resid(p):
            y0, amp, r, v0, phi_c = p
            v = v0 + r * np.sin(phi_rad - phi_c)
            return y0 + amp * v / (1.0 + v * v) - y_arr

        amp_init = abs(k_init) / max(r_init, 0.05)
        if sign_guess < 0:
            amp_init = -amp_init
        scale = max(ptp, 0.05)
        x0 = np.array([y0_init, amp_init, r_init, 0.0, phi_c0])
        lb = np.array([-20.0 * scale, -100.0 * scale, 1e-3, -3.0, phi_c0 - math.pi])
        ub = np.array([20.0 * scale, 100.0 * scale, 40.0, 3.0, phi_c0 + math.pi])
        return least_squares(resid, x0, bounds=(lb, ub), method="trf", max_nfev=800)

    candidates = [_fit_start(r0) for r0 in (0.05, 2.2)]
    best_res = min(candidates, key=lambda res: float(np.sum(res.fun**2)))
    y0, amp, r, v0, phi_c = map(float, best_res.x)
    rms = float(np.sqrt(np.mean(best_res.fun**2)))
    ratio = float(np.clip(-v0 / r, -0.999999, 0.999999))
    delta = math.asin(ratio)
    phi_z1 = phi_c + delta
    k0_crossing = float(amp * r * math.cos(delta))
    return dict(
        y0=y0,
        amp=amp,
        r=r,
        v0=v0,
        k0_v_per_rad=k0_crossing,
        phi_c_deg=float(np.rad2deg(phi_c) % 360.0),
        phi_z1_deg=float(np.rad2deg(phi_z1) % 360.0),
        rms_v=rms,
    )


def fit_eq222_local(dphi_rad: np.ndarray, y_v: np.ndarray, *, max_offset_rad: float) -> dict:
    """在工作点附近拟合对称理论式 (2.2.2)：Y(Δφ) = Y0 + (K0/r) · (r sin(Δφ-δ)) / [1 + r² sin²(Δφ-δ)]。"""
    y_arr = np.asarray(y_v, dtype=float)
    x_arr = np.asarray(dphi_rad, dtype=float)
    if x_arr.size < 6:
        raise ValueError("局部相位点数不足，无法拟合")
    design = np.column_stack((np.ones_like(x_arr), x_arr))
    coef, _, rank, _ = np.linalg.lstsq(design, y_arr, rcond=None)
    if rank != 2:
        raise ValueError("局部相位跨度退化，无法拟合")
    y0_init, k0_init = float(coef[0]), float(coef[1])
    k_bound = max(abs(k0_init) * 15.0, float(np.ptp(y_arr)) * 20.0, 1.0)
    y_bound = max(float(np.max(np.abs(y_arr))) * 5.0 + 1.0, 1.0)
    off_bound = max(min(float(max_offset_rad), math.pi / 4.0), 0.05)

    def resid(p):
        y0, k0, b_r2, off = p
        return eq222_y(x_arr, y0, k0, b_r2, off) - y_arr

    best = None
    for b_init in (0.0, 2.0, 9.0):
        x0 = np.array([y0_init, k0_init if abs(k0_init) > 1e-8 else 0.1, b_init, 0.0])
        lb = np.array([-y_bound, -k_bound, 0.0, -off_bound])
        ub = np.array([y_bound, k_bound, 900.0, off_bound])
        res = least_squares(resid, np.clip(x0, lb + 1e-12, ub - 1e-12), bounds=(lb, ub), method="trf", max_nfev=800)
        if best is None or float(np.sum(res.fun**2)) < float(np.sum(best.fun**2)):
            best = res

    y0, k0, b_r2, off = map(float, best.x)
    r = math.sqrt(max(b_r2, 0.0))
    dof = max(y_arr.size - 4, 1)
    sigma2 = float(np.sum(best.fun**2) / dof)
    try:
        cov = sigma2 * np.linalg.pinv(best.jac.T @ best.jac)
        k0_se = float(math.sqrt(max(float(cov[1, 1]), 0.0)))
    except np.linalg.LinAlgError:
        k0_se = 0.0
    rms = float(np.sqrt(np.mean(best.fun**2)))
    return dict(
        y0=y0,
        k0=k0,
        k0_se=k0_se,
        b_r2=b_r2,
        r=r,
        amp=(k0 / r) if r > 1e-6 else float("nan"),
        phi_offset_rad=off,
        rms_v=rms,
    )


def _linear_slope(dphi, volts):
    """在给定子集上线性回归斜率；返回虚部斜率。"""
    design = np.stack([np.ones(dphi.size), dphi], axis=1)
    params, _, rank, _ = np.linalg.lstsq(design, volts, rcond=None)
    if rank != 2 or dphi.size < 3:
        raise ValueError("局部相位子集太少，无法估计斜率")
    return float(params[1].imag)


def _optional_linear_slope(dphi, volts):
    try:
        return _linear_slope(dphi, volts)
    except ValueError:
        return None


def local_slopes(center, degrees, times_s, values, full_span_deg, center_indices=(0, 2)):
    """细角度局部扫描：用首尾中心点扣除线性漂移，再用理论式 (2.2.2) 拟合工作点斜率 K0。"""
    dphi = np.deg2rad(wrap_deg(np.asarray(degrees) - center))
    time = np.asarray(times_s) - np.mean(times_s)
    volts = np.asarray(values, dtype=complex)
    first, last = (int(center_indices[0]), int(center_indices[-1]))
    interval = time[last] - time[first]
    drift = (volts[last] - volts[first]) / interval if interval > 0 else 0j
    corrected = volts - drift * time

    floor = np.deg2rad(full_span_deg)
    fit = fit_eq222_local(dphi, corrected.imag, max_offset_rad=floor)

    # X 正交残留继续沿用直线斜率估计，检验解调轴仅沿 Y 方向。
    x_design = np.stack([np.ones(dphi.size), dphi], axis=1)
    x_params, _, rank, _ = np.linalg.lstsq(x_design, corrected.real, rcond=None)
    if rank != 2:
        raise ValueError("局部相位无法拟合")

    warnings = []
    if abs(dphi[first]) > np.deg2rad(0.75) or abs(dphi[last]) > np.deg2rad(0.75):
        warnings.append("工作点重复中心漂移超过 0.75°")
    if np.max(np.abs(dphi)) < 0.98 * floor:
        warnings.append("局部角度展宽低于 98% 名义跨度")
    inner_mask = np.abs(dphi) <= 0.5 * floor
    outer_mask = np.abs(dphi) >= 0.5 * floor
    return dict(
        k0_v_per_rad=fit["k0"],
        k0_se_v_per_rad=fit["k0_se"],
        slope_x_v_per_rad=float(x_params[1]),
        outer_v_per_rad=_optional_linear_slope(dphi[outer_mask], corrected[outer_mask]),
        inner_v_per_rad=_optional_linear_slope(dphi[inner_mask], corrected[inner_mask]),
        drift_y_v_per_s=float(drift.imag),
        local_model_rms_v=fit["rms_v"],
        local_model_mode="eq_2_2_2",
        eq222_y0_v=fit["y0"],
        eq222_r=fit["r"],
        eq222_phi_offset_deg=float(np.rad2deg(fit["phi_offset_rad"])),
        local_phase_offsets_deg=list(map(float, np.rad2deg(dphi))),
        local_warnings=warnings,
    )


def fit_sqrt_lorentzian(frequencies_hz, gains_v_per_rad, se_v_per_rad=None) -> dict:
    """以 0 Hz 为中心拟合 |H(f)| = H0 / sqrt(1 + (f/fc)²)，用于零频归一化与全频回补。"""
    freqs = np.asarray(frequencies_hz, dtype=float)
    gains = np.asarray(gains_v_per_rad, dtype=float)
    valid = np.isfinite(freqs) & (freqs > 0) & np.isfinite(gains) & (gains > 0)
    if np.sum(valid) < 2:
        return dict(available=False, reason="有效主测频点不足 2 个，无法拟合 √Lorentzian")
    freqs, gains = freqs[valid], gains[valid]
    if se_v_per_rad is not None:
        weights = np.asarray(se_v_per_rad, dtype=float)[valid]
        sigma = np.where(np.isfinite(weights) & (weights > 0), weights, np.maximum(np.max(gains) * 1e-3, 1e-9))
    else:
        sigma = np.full_like(gains, max(float(np.max(gains)) * 1e-3, 1e-9))

    order = np.argsort(freqs)
    freqs, gains, sigma = freqs[order], gains[order], sigma[order]

    def resid(p):
        h0, fc = p
        return (h0 / np.sqrt(1.0 + (freqs / fc) ** 2) - gains) / sigma

    h0_init = float(gains[0]) * 1.05
    best = None
    for fc_init in (40.0, 100.0, 250.0):
        res = least_squares(resid, x0=np.array([h0_init, fc_init]), bounds=([1e-6, 0.5], [1e4, 1e5]),
                            method="trf", max_nfev=600)
        if best is None or float(np.sum(res.fun**2)) < float(np.sum(best.fun**2)):
            best = res

    h0, fc = float(best.x[0]), float(best.x[1])
    if fc >= 9e4:
        return dict(available=False, reason="频响在测频段内无明显滚降，√Lorentzian 拐角频率未收敛")
    dof = max(freqs.size - 2, 1)
    chi2_red = float(np.sum(best.fun**2) / dof)
    try:
        cov = np.linalg.pinv(best.jac.T @ best.jac) * max(chi2_red, 1.0)
        h0_se = float(math.sqrt(max(float(cov[0, 0]), 0.0)))
        fc_se = float(math.sqrt(max(float(cov[1, 1]), 0.0)))
    except np.linalg.LinAlgError:
        h0_se = fc_se = float("nan")
    pred = h0 / np.sqrt(1.0 + (freqs / fc) ** 2)
    return dict(
        available=True,
        h0_v_per_rad=h0,
        h0_se_v_per_rad=h0_se,
        fc_hz=fc,
        fc_se_hz=fc_se,
        chi2_red=chi2_red,
        rms_v_per_rad=float(np.sqrt(np.mean((pred - gains) ** 2))),
        reason="",
    )
