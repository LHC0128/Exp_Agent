"""Mx Y RF 频率响应的布洛赫稳态统一线形模型与拟合。

物理模型（旋波近似、均匀展宽）：

    主场沿 Z（Larmor 频率 f0），Y 向 RF 场幅度 B1，驱动失谐 Δ = 2π(f0-f)。
    在绕 Z 旋转的坐标系中，稳态布洛赫解给出 Demod R 幅度

        R(f) = C + A * sqrt(1 + x^2) / (1 + x^2 + S)，
        x = (f - f0) / gamma，gamma = 1/(2π T2)，S = (γ B1 / 2)^2 T1 T2。

    S→0 时为单峰（小失谐下近似 Lorentzian，HWHM=gamma）；
    S>1 时中心为局部极小，双峰位于 f0 ± gamma*sqrt(S-1)。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy.optimize import curve_fit

from ..mx_y_rf_sensitivity.analysis_core import fit_lorentzian_response


def bloch_response(
    x: np.ndarray,
    amplitude: float,
    gamma: float,
    center: float,
    offset: float,
    saturation: float,
) -> np.ndarray:
    """统一布洛赫稳态线形 R(f)=C+A·√(1+x²)/(1+x²+S)。"""
    x = np.asarray(x, dtype=float)
    gamma = abs(float(gamma))
    scaled = (x - float(center)) / gamma
    numerator = np.sqrt(1.0 + scaled**2)
    denominator = 1.0 + scaled**2 + max(float(saturation), 0.0)
    return float(offset) + float(amplitude) * numerator / denominator


@dataclass(slots=True)
class BlochFitResult:
    """5 参数布洛赫线形拟合结果与派生量。"""

    success: bool
    parameters: tuple[float, float, float, float, float]
    uncertainties: tuple[float, float, float, float, float]
    r_squared: float
    rmse: float
    relative_gamma_uncertainty: float
    rejection_reasons: tuple[str, ...]

    @property
    def amplitude(self) -> float:
        return self.parameters[0]

    @property
    def gamma(self) -> float:
        return self.parameters[1]

    @property
    def center(self) -> float:
        return self.parameters[2]

    @property
    def offset(self) -> float:
        return self.parameters[3]

    @property
    def saturation(self) -> float:
        return self.parameters[4]

    @property
    def split(self) -> bool:
        """S>1 时为 Rabi 劈裂双峰。"""
        return self.saturation > 1.0

    @property
    def peak_positions_hz(self) -> tuple[float, float] | None:
        """双峰峰位；未劈裂时返回 None。"""
        if not self.split:
            return None
        delta = self.gamma * math.sqrt(self.saturation - 1.0)
        return (self.center - delta, self.center + delta)

    @property
    def valley_response(self) -> float:
        """中心处（劈裂时为谷底）的模型值。"""
        return self.offset + self.amplitude / (1.0 + self.saturation)

    @property
    def effective_rabi_hz(self) -> float:
        """γ√S，等效 Rabi 频率（Hz）。"""
        return self.gamma * math.sqrt(self.saturation)

    def to_dict(self) -> dict[str, Any]:
        peaks = self.peak_positions_hz
        return {
            "success": self.success,
            "model": "C + A*sqrt(1+((f-f0)/gamma)^2)/(1+((f-f0)/gamma)^2+S)",
            "parameters": {
                "amplitude_v": self.amplitude,
                "gamma_hz": self.gamma,
                "center_hz": self.center,
                "offset_v": self.offset,
                "saturation": self.saturation,
            },
            "uncertainties": {
                "amplitude_v": float(self.uncertainties[0]),
                "gamma_hz": float(self.uncertainties[1]),
                "center_hz": float(self.uncertainties[2]),
                "offset_v": float(self.uncertainties[3]),
                "saturation": float(self.uncertainties[4]),
            },
            "r_squared": self.r_squared,
            "rmse_v": self.rmse,
            "relative_gamma_uncertainty": self.relative_gamma_uncertainty,
            "hwhm_hz": self.gamma,
            "split": self.split,
            "peak_positions_hz": list(peaks) if peaks is not None else None,
            "valley_response_v": self.valley_response,
            "effective_rabi_hz": self.effective_rabi_hz,
            "rejection_reasons": list(self.rejection_reasons),
        }


def _attempt_fit(
    x: np.ndarray,
    y: np.ndarray,
    p0: tuple[float, float, float, float, float],
    lower: tuple[float, float, float, float, float],
    upper: tuple[float, float, float, float, float],
) -> tuple[np.ndarray, np.ndarray, float] | None:
    try:
        popt, pcov = curve_fit(
            bloch_response,
            x,
            y,
            p0=p0,
            bounds=(lower, upper),
            maxfev=50000,
        )
        predicted = bloch_response(x, *popt)
        residual = y - predicted
        ss_res = float(np.sum(residual**2))
        return popt, pcov, ss_res
    except Exception:
        return None


def _sqrt_lorentzian(x: np.ndarray, amplitude: float, gamma: float, center: float, offset: float) -> np.ndarray:
    """S=0 固定时的弱驱动线形。"""
    return bloch_response(x, amplitude, gamma, center, offset, 0.0)


def _attempt_sqrt_lorentzian_fit(
    x: np.ndarray,
    y: np.ndarray,
    guess: tuple[float, float, float, float],
    lower: tuple[float, float, float, float],
    upper: tuple[float, float, float, float],
) -> tuple[np.ndarray, np.ndarray, float] | None:
    try:
        popt, pcov = curve_fit(
            _sqrt_lorentzian,
            x,
            y,
            p0=guess,
            bounds=(lower, upper),
            maxfev=50000,
        )
        predicted = _sqrt_lorentzian(x, *popt)
        residual = y - predicted
        ss_res = float(np.sum(residual**2))
        return popt, pcov, ss_res
    except Exception:
        return None


def _estimate_split_guess(
    x: np.ndarray,
    y: np.ndarray,
    *,
    step: float,
    span: float,
) -> tuple[float, float, float] | None:
    """从平滑数据的两峰间距与谷深反演估计 center、gamma 和 S。

    谷深 dip = 1 - valley/peak，理论上有 dip = 1 - 2*sqrt(S)/(1+S)，
    与基线无关，因此对窄扫描范围同样适用。
    """
    order = np.argsort(x)
    xs = x[order]
    ys = y[order]
    window = max(3, min(21, xs.size // 10))
    if window % 2 == 0:
        window += 1
    smoothed = np.convolve(ys, np.ones(window) / window, mode="same")
    edge = max(1, xs.size // 10)
    baseline = float(np.median(np.r_[ys[:edge], ys[-edge:]]))
    centered = smoothed - baseline
    first_idx = int(np.argmax(centered))
    guard = window
    second_idx = first_idx
    second_value = float("-inf")
    for side in (
        np.arange(0, max(0, first_idx - guard)),
        np.arange(min(xs.size, first_idx + guard + 1), xs.size),
    ):
        if side.size == 0:
            continue
        local = int(np.argmax(centered[side]))
        index = int(side[local])
        if float(centered[index]) > second_value:
            second_value = float(centered[index])
            second_idx = index
    if second_idx == first_idx:
        return None
    if float(centered[second_idx]) < 0.5 * float(centered[first_idx]):
        return None
    left_idx, right_idx = sorted((first_idx, second_idx))
    valley = float(np.min(centered[left_idx : right_idx + 1]))
    peak = 0.5 * float(centered[left_idx] + centered[right_idx])
    if peak <= 0.0 or valley >= peak:
        return None
    dip = 1.0 - valley / peak
    if dip <= 0.02:
        return None
    dip = min(dip, 0.6)
    t = (1.0 + math.sqrt(max(2.0 * dip - dip**2, 0.0))) / (1.0 - dip)
    saturation0 = t**2
    if saturation0 <= 1.05:
        return None
    gamma0 = (xs[right_idx] - xs[left_idx]) / (
        2.0 * math.sqrt(saturation0 - 1.0)
    )
    gamma0 = float(np.clip(gamma0, step * 0.05, span))
    center0 = float(
        np.clip(0.5 * (xs[left_idx] + xs[right_idx]), xs[0], xs[-1])
    )
    return center0, gamma0, saturation0


def fit_bloch_response(
    frequency_hz: np.ndarray,
    response_v: np.ndarray,
    *,
    r_squared_min: float | None = 0.85,
    relative_gamma_uncertainty_max: float | None = 0.5,
) -> BlochFitResult:
    """两步拟合统一布洛赫线形。

    弱驱动区 S 与 gamma 在 S≈0 附近简并，直接做 5 参数拟合会滑向
    gamma 偏小的局部解。因此先固定 S=0 拟合 4 参数 sqrt-Lorentzian，
    只有 5 参数拟合明确给出 S>1（Rabi 劈裂）时才采用强驱动解。
    """
    empty = BlochFitResult(
        False,
        (0.0, 0.0, 0.0, 0.0, 0.0),
        (np.nan,) * 5,
        0.0,
        np.nan,
        np.nan,
        ("拟合未执行",),
    )
    x = np.asarray(frequency_hz, dtype=float)
    y = np.asarray(response_v, dtype=float)
    valid = np.isfinite(x) & np.isfinite(y)
    x, y = x[valid], y[valid]
    if x.size < 6:
        return BlochFitResult(
            False,
            (0.0, 0.0, 0.0, 0.0, 0.0),
            (np.nan,) * 5,
            0.0,
            np.nan,
            np.nan,
            ("有效点不足 6",),
        )

    span = float(np.ptp(x))
    step = float(np.median(np.diff(np.sort(x))))
    lorentz = fit_lorentzian_response(x, y, r_squared_min=None, relative_gamma_uncertainty_max=None)
    amplitude0 = abs(float(lorentz.amplitude))
    gamma0 = float(np.clip(lorentz.gamma, step * 0.05, span))
    center0 = float(np.clip(lorentz.center, float(np.min(x)), float(np.max(x))))
    offset0 = max(float(lorentz.offset), 0.0)
    lower4 = (
        0.0,
        max(step * 0.05, np.finfo(float).eps),
        float(np.min(x)),
        0.0,
    )
    upper4 = (
        np.inf,
        span,
        float(np.max(x)),
        np.inf,
    )
    four = _attempt_sqrt_lorentzian_fit(
        x,
        y,
        (amplitude0, gamma0, center0, offset0),
        lower4,
        upper4,
    )
    guess5 = four[0] if four is not None else (amplitude0, gamma0, center0, offset0)
    lower5 = (*lower4, 0.0)
    upper5 = (*upper4, np.inf)
    split_guess = _estimate_split_guess(x, y, step=step, span=span)
    bases = [guess5, (amplitude0, gamma0, center0, offset0)]
    initial_points: list[tuple[float, float, float, float, float]] = []
    for base in bases:
        initial_points.extend(
            (*base, saturation0) for saturation0 in (0.0, 1.2, 4.0, 10.0)
        )
    if split_guess is not None:
        center_guess, gamma_guess, saturation_guess = split_guess
        for base in bases:
            for saturation0 in (
                saturation_guess,
                saturation_guess * 0.5,
                saturation_guess * 1.5,
            ):
                initial_points.append(
                    (
                        base[0],
                        gamma_guess,
                        center_guess,
                        base[3],
                        saturation0,
                    )
                )
    candidates: list[tuple[np.ndarray, np.ndarray, float]] = []
    for initial in initial_points:
        outcome = _attempt_fit(x, y, initial, lower5, upper5)
        if outcome is not None:
            candidates.append(outcome)
    five = min(candidates, key=lambda item: item[2]) if candidates else None

    if five is not None and float(five[0][4]) > 1.0:
        popt, pcov = five[0], five[1]
    elif four is not None:
        popt = np.asarray((*four[0], 0.0), dtype=float)
        pcov = np.full((5, 5), np.nan, dtype=float)
        pcov[:4, :4] = four[1]
    elif five is not None:
        popt, pcov = five[0], five[1]
    else:
        return BlochFitResult(
            False,
            (0.0, 0.0, 0.0, 0.0, 0.0),
            (np.nan,) * 5,
            0.0,
            np.nan,
            np.nan,
            ("所有初值拟合均异常",),
        )

    predicted = bloch_response(x, *popt)
    residual = y - predicted
    ss_res = float(np.sum(residual**2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 1e-30 else 0.0
    rmse = float(np.sqrt(ss_res / len(y)))
    uncertainty = np.full(5, np.nan, dtype=float)
    if pcov.shape == (5, 5) and np.all(np.isfinite(pcov)):
        uncertainty = np.sqrt(np.maximum(np.diag(pcov), 0.0))
    gamma = abs(float(popt[1]))
    relative = (
        float(uncertainty[1] / gamma)
        if gamma > 0 and np.isfinite(uncertainty[1])
        else float("nan")
    )
    reasons: list[str] = []
    if r_squared_min is not None and r_squared < r_squared_min:
        reasons.append(f"R^2={r_squared:.4f} < {r_squared_min:.4f}")
    if (
        relative_gamma_uncertainty_max is not None
        and np.isfinite(relative)
        and relative > relative_gamma_uncertainty_max
    ):
        reasons.append(
            f"gamma 相对不确定度={relative:.2%} > {relative_gamma_uncertainty_max:.2%}"
        )
    if gamma >= span / 2.0:
        reasons.append("gamma 不小于扫描跨度的一半")
    return BlochFitResult(
        not reasons,
        tuple(map(float, popt)),
        tuple(float(item) for item in uncertainty),
        r_squared,
        rmse,
        relative,
        tuple(reasons),
    )


def fit_saturation_scaling(
    amplitude_vpp: np.ndarray,
    saturation: np.ndarray,
) -> dict[str, Any]:
    """对 S 与幅度平方做线性回归（物理上 S ∝ B1^2 ∝ Vpp^2）。"""
    amplitude = np.asarray(amplitude_vpp, dtype=float)
    values = np.asarray(saturation, dtype=float)
    mask = np.isfinite(amplitude) & np.isfinite(values) & (amplitude >= 0)
    amplitude, values = amplitude[mask], values[mask]
    if amplitude.size < 2 or np.ptp(amplitude) <= 0:
        return {
            "success": False,
            "slope_per_vpp2": float("nan"),
            "intercept": float("nan"),
            "r_squared": float("nan"),
            "n_points": int(amplitude.size),
        }
    squared = amplitude**2
    slope, intercept = np.polyfit(squared, values, 1)
    predicted = slope * squared + intercept
    ss_res = float(np.sum((values - predicted) ** 2))
    ss_tot = float(np.sum((values - np.mean(values)) ** 2))
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 1e-30 else 0.0
    return {
        "success": True,
        "slope_per_vpp2": float(slope),
        "intercept": float(intercept),
        "r_squared": r_squared,
        "n_points": int(amplitude.size),
    }
