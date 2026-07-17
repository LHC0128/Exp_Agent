"""Mx Y-RF 灵敏度实验的纯数值分析。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
from scipy import signal
from scipy.optimize import curve_fit

from sensitivity_analysis.fitting import LARMOR_PER_NT


def dispersive_response(x: np.ndarray, amplitude: float, gamma: float, center: float, offset: float) -> np.ndarray:
    return amplitude * (x - center) / ((x - center) ** 2 + gamma**2) + offset


def absolute_dispersive_response(
    x: np.ndarray,
    amplitude: float,
    gamma: float,
    center: float,
    offset: float,
) -> np.ndarray:
    """返回带基线色散线形的绝对值。"""
    return np.abs(
        dispersive_response(x, amplitude, gamma, center, offset)
    )


def lorentzian_response(x: np.ndarray, amplitude: float, gamma: float, center: float, offset: float) -> np.ndarray:
    return offset + amplitude * gamma**2 / ((x - center) ** 2 + gamma**2)


@dataclass(slots=True)
class FitResult:
    method: str
    success: bool
    parameters: tuple[float, float, float, float]
    uncertainties: tuple[float, float, float, float]
    r_squared: float
    rmse: float
    relative_gamma_uncertainty: float
    rejection_reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

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


def _quality(
    x: np.ndarray,
    y: np.ndarray,
    predicted: np.ndarray,
    parameters: np.ndarray,
    covariance: np.ndarray,
    *,
    r_squared_min: float | None,
    relative_gamma_uncertainty_max: float | None,
    require_center_inside: bool,
    minimum_gamma: float,
) -> tuple[float, float, tuple[float, ...], float, tuple[str, ...]]:
    residual = y - predicted
    ss_res = float(np.sum(residual**2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 1e-30 else 0.0
    rmse = float(np.sqrt(ss_res / len(y)))
    uncertainty = np.full(4, np.nan, dtype=float)
    if covariance.shape == (4, 4) and np.all(np.isfinite(covariance)):
        uncertainty = np.sqrt(np.maximum(np.diag(covariance), 0.0))
    gamma = abs(float(parameters[1]))
    relative = float(uncertainty[1] / gamma) if gamma > 0 and np.isfinite(uncertainty[1]) else float("nan")
    reasons: list[str] = []
    if r_squared_min is not None and r_squared < r_squared_min:
        reasons.append(f"R^2={r_squared:.4f} < {r_squared_min:.4f}")
    if gamma <= minimum_gamma:
        reasons.append(f"gamma={gamma:.6g} <= {minimum_gamma:.6g}")
    if (
        relative_gamma_uncertainty_max is not None
        and np.isfinite(relative)
        and relative > relative_gamma_uncertainty_max
    ):
        reasons.append(
            f"gamma 相对不确定度={relative:.2%} > {relative_gamma_uncertainty_max:.2%}"
        )
    if require_center_inside and not float(np.min(x)) < float(parameters[2]) < float(np.max(x)):
        reasons.append("拟合中心不在扫描范围内")
    return r_squared, rmse, tuple(float(item) for item in uncertainty), relative, tuple(reasons)


def fit_dispersive_response(
    x: np.ndarray,
    y: np.ndarray,
    *,
    r_squared_min: float = 0.85,
    relative_gamma_uncertainty_max: float = 0.5,
) -> FitResult:
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    valid = np.isfinite(x) & np.isfinite(y)
    x, y = x[valid], y[valid]
    if x.size < 5:
        return FitResult("dispersive", False, (0.0, 0.0, 0.0, 0.0), (np.nan,) * 4, 0.0, np.nan, np.nan, ("有效点不足 5",))
    span = float(np.ptp(x))
    gamma0 = max(span / 4.0, np.finfo(float).eps)
    amplitude0 = float(np.ptp(y) * gamma0)
    p0 = (amplitude0, gamma0, float(x[np.argmin(np.abs(y - np.median(y)))]), float(np.median(y)))
    try:
        popt, pcov = curve_fit(dispersive_response, x, y, p0=p0, maxfev=30000)
        popt[1] = abs(popt[1])
        predicted = dispersive_response(x, *popt)
        quality = _quality(
            x, y, predicted, popt, pcov,
            r_squared_min=r_squared_min,
            relative_gamma_uncertainty_max=relative_gamma_uncertainty_max,
            require_center_inside=True,
            minimum_gamma=max(span * 1e-9, 1e-15),
        )
        return FitResult("dispersive", not quality[-1], tuple(map(float, popt)), quality[2], quality[0], quality[1], quality[3], quality[4])
    except Exception as exc:
        return FitResult("dispersive", False, (0.0, 0.0, 0.0, 0.0), (np.nan,) * 4, 0.0, np.nan, np.nan, (f"拟合异常: {exc}",))


def fit_absolute_dispersive_response(
    x: np.ndarray,
    y: np.ndarray,
    *,
    r_squared_min: float = 0.85,
    relative_gamma_uncertainty_max: float = 0.5,
) -> FitResult:
    """拟合 R=|A(V-V0)/((V-V0)^2+gamma^2)+C|。"""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    valid = np.isfinite(x) & np.isfinite(y)
    x, y = x[valid], y[valid]
    if x.size < 5:
        return FitResult(
            "absolute_dispersive_r",
            False,
            (0.0, 0.0, 0.0, 0.0),
            (np.nan,) * 4,
            0.0,
            np.nan,
            np.nan,
            ("有效点不足 5",),
        )
    span = float(np.ptp(x))
    step = float(np.median(np.diff(np.sort(x))))
    center0 = float(x[np.argmin(y)])
    gamma0 = max(
        abs(float(x[np.argmax(y)]) - center0),
        span / 4.0,
        step,
    )
    amplitude0 = max(float(np.max(y)), np.finfo(float).eps) * 2.0 * gamma0
    p0 = (amplitude0, gamma0, center0, 0.0)
    lower = (
        0.0,
        max(step * 0.05, np.finfo(float).eps),
        float(np.min(x)),
        -np.inf,
    )
    upper = (
        np.inf,
        span,
        float(np.max(x)),
        np.inf,
    )
    try:
        popt, pcov = curve_fit(
            absolute_dispersive_response,
            x,
            y,
            p0=p0,
            bounds=(lower, upper),
            maxfev=50000,
        )
        predicted = absolute_dispersive_response(x, *popt)
        quality = _quality(
            x,
            y,
            predicted,
            popt,
            pcov,
            r_squared_min=r_squared_min,
            relative_gamma_uncertainty_max=(
                relative_gamma_uncertainty_max
            ),
            require_center_inside=True,
            minimum_gamma=step,
        )
        return FitResult(
            "absolute_dispersive_r",
            not quality[-1],
            tuple(map(float, popt)),
            quality[2],
            quality[0],
            quality[1],
            quality[3],
            quality[4],
        )
    except Exception as exc:
        return FitResult(
            "absolute_dispersive_r",
            False,
            (0.0, 0.0, 0.0, 0.0),
            (np.nan,) * 4,
            0.0,
            np.nan,
            np.nan,
            (f"拟合异常: {exc}",),
        )


def fit_lorentzian_response(
    frequency_hz: np.ndarray,
    response_v: np.ndarray,
    *,
    r_squared_min: float | None = 0.85,
    relative_gamma_uncertainty_max: float | None = 0.5,
) -> FitResult:
    x = np.asarray(frequency_hz, dtype=float)
    y = np.asarray(response_v, dtype=float)
    valid = np.isfinite(x) & np.isfinite(y)
    x, y = x[valid], y[valid]
    if x.size < 5:
        return FitResult("lorentzian", False, (0.0, 0.0, 0.0, 0.0), (np.nan,) * 4, 0.0, np.nan, np.nan, ("有效点不足 5",))
    span = float(np.ptp(x))
    step = float(np.median(np.diff(np.sort(x))))
    edge_count = max(1, x.size // 10)
    baseline = float(np.median(np.r_[y[:edge_count], y[-edge_count:]]))
    peak_idx = int(np.argmax(np.abs(y - baseline)))
    p0 = (float(y[peak_idx] - baseline), max(span / 10.0, step), float(x[peak_idx]), baseline)
    lower = (-np.inf, max(step * 0.05, np.finfo(float).eps), float(np.min(x)), -np.inf)
    upper = (np.inf, span, float(np.max(x)), np.inf)
    try:
        popt, pcov = curve_fit(lorentzian_response, x, y, p0=p0, bounds=(lower, upper), maxfev=30000)
        predicted = lorentzian_response(x, *popt)
        quality = _quality(
            x, y, predicted, popt, pcov,
            r_squared_min=r_squared_min,
            relative_gamma_uncertainty_max=relative_gamma_uncertainty_max,
            require_center_inside=True,
            minimum_gamma=step,
        )
        if popt[1] >= span / 2.0:
            quality = (*quality[:-1], (*quality[-1], "HWHM 不小于扫描跨度的一半"))
        return FitResult("lorentzian", not quality[-1], tuple(map(float, popt)), quality[2], quality[0], quality[1], quality[3], quality[4])
    except Exception as exc:
        return FitResult("lorentzian", False, (0.0, 0.0, 0.0, 0.0), (np.nan,) * 4, 0.0, np.nan, np.nan, (f"拟合异常: {exc}",))


def central_linear_fit(
    x: np.ndarray,
    y: np.ndarray,
    *,
    center: float,
    gamma: float,
    gamma_fraction: float,
) -> dict[str, Any]:
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    mask = np.isfinite(x) & np.isfinite(y) & (np.abs(x - center) <= gamma_fraction * gamma)
    if np.count_nonzero(mask) < 3:
        return {"success": False, "slope": float("nan"), "intercept": float("nan"), "r_squared": float("nan"), "n_points": int(np.count_nonzero(mask)), "mask": mask}
    slope, intercept = np.polyfit(x[mask], y[mask], 1)
    predicted = slope * x[mask] + intercept
    ss_res = float(np.sum((y[mask] - predicted) ** 2))
    ss_tot = float(np.sum((y[mask] - np.mean(y[mask])) ** 2))
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 1e-30 else 0.0
    return {"success": True, "slope": float(slope), "intercept": float(intercept), "r_squared": r_squared, "n_points": int(np.count_nonzero(mask)), "mask": mask}


def central_absolute_linear_fit(
    x: np.ndarray,
    y: np.ndarray,
    *,
    center: float,
    gamma: float,
    gamma_fraction: float,
) -> dict[str, Any]:
    """在中心附近用 |V-V0| 对 R 做单边斜率幅值诊断。"""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    mask = (
        np.isfinite(x)
        & np.isfinite(y)
        & (np.abs(x - center) <= gamma_fraction * gamma)
    )
    if np.count_nonzero(mask) < 3:
        return {
            "success": False,
            "slope": float("nan"),
            "intercept": float("nan"),
            "r_squared": float("nan"),
            "n_points": int(np.count_nonzero(mask)),
            "mask": mask,
        }
    distance = np.abs(x[mask] - center)
    slope, intercept = np.polyfit(distance, y[mask], 1)
    predicted = slope * distance + intercept
    ss_res = float(np.sum((y[mask] - predicted) ** 2))
    ss_tot = float(np.sum((y[mask] - np.mean(y[mask])) ** 2))
    r_squared = (
        1.0 - ss_res / ss_tot
        if ss_tot > 1e-30
        else 0.0
    )
    return {
        "success": True,
        "slope": float(slope),
        "intercept": float(intercept),
        "r_squared": r_squared,
        "n_points": int(np.count_nonzero(mask)),
        "mask": mask,
    }


def average_welch_psd(waveforms: list[np.ndarray], sample_rates: list[float]) -> tuple[np.ndarray, np.ndarray]:
    if not waveforms or len(waveforms) != len(sample_rates):
        raise ValueError("PSD 波形和采样率数量不一致或为空")
    frequencies: np.ndarray | None = None
    spectra: list[np.ndarray] = []
    for waveform, sample_rate in zip(waveforms, sample_rates):
        values = np.asarray(waveform, dtype=float)
        values = values[np.isfinite(values)]
        if values.size < 8:
            raise ValueError("PSD 波形有效点不足 8")
        f_axis, psd = signal.welch(
            values,
            fs=float(sample_rate),
            window="hann",
            nperseg=values.size,
            detrend="constant",
            scaling="density",
        )
        if frequencies is None:
            frequencies = f_axis
        elif frequencies.shape != f_axis.shape or not np.allclose(frequencies, f_axis):
            raise ValueError("不同噪声记录的 PSD 频率轴不一致")
        spectra.append(psd)
    assert frequencies is not None
    return frequencies, np.mean(np.asarray(spectra), axis=0)


def sensitivity_spectrum(
    psd_r_v2_per_hz: np.ndarray,
    frequency_hz: np.ndarray,
    *,
    slope_signal_v_per_vpp: float,
    hwhm_hz: float | None,
    low_freq_skip_hz: float,
    y_rf_nt_per_vpp: float,
) -> dict[str, Any]:
    slope = abs(float(slope_signal_v_per_vpp))
    if slope <= 1e-30:
        raise ValueError("幅度响应斜率过小，无法计算灵敏度")
    raw_vpp = np.sqrt(np.maximum(np.asarray(psd_r_v2_per_hz, dtype=float), 0.0)) / slope
    correction = np.ones_like(raw_vpp)
    if hwhm_hz is not None and hwhm_hz > 0:
        correction = np.sqrt(1.0 + (np.asarray(frequency_hz, dtype=float) / hwhm_hz) ** 2)
    corrected_vpp = raw_vpp * correction
    flat_mask = np.zeros_like(raw_vpp, dtype=bool)
    median_vpp = float("nan")
    if hwhm_hz is not None and hwhm_hz >= low_freq_skip_hz:
        flat_mask = (frequency_hz >= low_freq_skip_hz) & (frequency_hz <= hwhm_hz) & np.isfinite(corrected_vpp)
        if np.any(flat_mask):
            median_vpp = float(np.median(corrected_vpp[flat_mask]))
    result: dict[str, Any] = {
        "raw_vpp_per_sqrt_hz": raw_vpp,
        "corrected_vpp_per_sqrt_hz": corrected_vpp,
        "correction_factor": correction,
        "flat_mask": flat_mask,
        "flat_median_vpp_per_sqrt_hz": median_vpp,
    }
    if y_rf_nt_per_vpp > 0:
        factor = y_rf_nt_per_vpp * 1e6
        result["raw_ft_per_sqrt_hz"] = raw_vpp * factor
        result["corrected_ft_per_sqrt_hz"] = corrected_vpp * factor
        result["flat_median_ft_per_sqrt_hz"] = median_vpp * factor
    return result


def amplitude_gamma_to_hz(gamma_vpp: float, y_rf_nt_per_vpp: float) -> float | None:
    if y_rf_nt_per_vpp <= 0:
        return None
    return float(abs(gamma_vpp) * y_rf_nt_per_vpp * LARMOR_PER_NT)
