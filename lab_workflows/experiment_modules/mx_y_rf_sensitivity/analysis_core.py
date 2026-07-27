"""Mx Y-RF 灵敏度实验的纯数值分析。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
from scipy import signal
from scipy.optimize import curve_fit
from scipy.stats import theilslopes

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


_FLAT_BIN_COUNTS = (30, 40, 50, 60)
_FLAT_OBSERVATION_FACTORS = (1.5, 2.0, 2.5)
_FLAT_MEDIAN_CV_MAX = 0.03
_FLAT_MEDIAN_SPAN_MAX = 0.08
_FLAT_DRIFT_MAX = 0.20
_FLAT_RELATIVE_MAD_MAX = 0.15
_FLAT_RISE_SIGMA_MIN = 3.0


def _fit_nonnegative_hinges(
    design: np.ndarray,
    values: np.ndarray,
) -> tuple[float, np.ndarray, float]:
    """拟合截距和两个非负铰链斜率，并用 Huber 损失评分。"""
    best: tuple[float, np.ndarray, float] | None = None
    for columns in ((0, 1, 2), (0, 1), (0, 2), (0,)):
        coefficients = np.zeros(3, dtype=float)
        coefficients[list(columns)] = np.linalg.lstsq(
            design[:, columns],
            values,
            rcond=None,
        )[0]
        if coefficients[1] < -1e-12 or coefficients[2] < -1e-12:
            continue
        residual = values - design @ coefficients
        centered = residual - np.median(residual)
        scale = (
            1.4826 * float(np.median(np.abs(centered)))
            + np.finfo(float).eps
        )
        threshold = 1.5 * scale
        absolute = np.abs(centered)
        loss = float(
            np.mean(
                np.where(
                    absolute <= threshold,
                    0.5 * absolute**2,
                    threshold * (absolute - 0.5 * threshold),
                )
            )
        )
        if best is None or loss < best[0]:
            best = (loss, coefficients, scale)
    if best is None:
        raise RuntimeError("平坦段铰链模型无法得到非负斜率")
    return best


def _detect_flat_band_variant(
    frequency_hz: np.ndarray,
    sensitivity: np.ndarray,
    *,
    hwhm_hz: float,
    minimum_frequency_hz: float,
    bin_count: int,
    observation_factor: float,
) -> dict[str, float] | None:
    observation_max_hz = min(
        float(np.max(frequency_hz)),
        observation_factor * hwhm_hz,
    )
    valid = (
        np.isfinite(frequency_hz)
        & np.isfinite(sensitivity)
        & (sensitivity > 0)
        & (frequency_hz >= minimum_frequency_hz)
        & (frequency_hz <= observation_max_hz)
    )
    frequency = frequency_hz[valid]
    values = sensitivity[valid]
    if frequency.size < 24 or float(np.ptp(frequency)) <= 0:
        return None

    log_values = np.log(values)
    edges = np.linspace(
        float(np.min(frequency)),
        float(np.max(frequency)),
        bin_count + 1,
    )
    binned_frequency: list[float] = []
    binned_log_values: list[float] = []
    for index, (lower, upper) in enumerate(zip(edges[:-1], edges[1:])):
        mask = (frequency >= lower) & (
            (frequency < upper)
            if index < bin_count - 1
            else (frequency <= upper)
        )
        if np.count_nonzero(mask) < 3:
            continue
        binned_frequency.append(float(np.median(frequency[mask])))
        binned_log_values.append(float(np.median(log_values[mask])))

    x = np.asarray(binned_frequency, dtype=float)
    z = np.asarray(binned_log_values, dtype=float)
    n_bins = x.size
    minimum_flat_bins = max(8, int(round(0.18 * n_bins)))
    minimum_high_bins = max(4, int(round(0.10 * n_bins)))
    if n_bins < minimum_flat_bins + minimum_high_bins:
        return None

    best: tuple[float, int, int, np.ndarray, float] | None = None
    for low_index in range(
        0,
        n_bins - minimum_flat_bins - minimum_high_bins + 1,
    ):
        for high_index in range(
            low_index + minimum_flat_bins - 1,
            n_bins - minimum_high_bins,
        ):
            design = np.column_stack(
                (
                    np.ones(n_bins),
                    np.maximum(0.0, x[low_index] - x),
                    np.maximum(0.0, x - x[high_index]),
                )
            )
            loss, coefficients, scale = _fit_nonnegative_hinges(
                design,
                z,
            )
            candidate = (
                loss,
                low_index,
                high_index,
                coefficients,
                scale,
            )
            if best is None or candidate[0] < best[0]:
                best = candidate
    if best is None:
        return None

    _, low_index, high_index, coefficients, scale = best
    low_hz = (
        float(x[low_index])
        if coefficients[1] * (x[low_index] - x[0]) > scale
        else float(np.min(frequency))
    )
    high_hz = float(x[high_index])
    flat = (frequency >= low_hz) & (frequency <= high_hz)
    flat_bins = (x >= low_hz) & (x <= high_hz)
    if np.count_nonzero(flat) < 8 or np.count_nonzero(flat_bins) < 3:
        return None

    flat_x = x[flat_bins]
    flat_z = z[flat_bins]
    slope = float(theilslopes(flat_z, flat_x)[0])
    drift = abs(
        float(
            np.expm1(
                np.clip(slope * float(np.ptp(flat_x)), -50.0, 50.0)
            )
        )
    )
    log_mad = 1.4826 * float(
        np.median(np.abs(flat_z - np.median(flat_z)))
    )
    relative_mad = float(np.expm1(min(log_mad, 50.0)))
    rise_sigma = float(
        coefficients[2] * (x[-1] - high_hz) / scale
    )
    return {
        "low_hz": low_hz,
        "high_hz": high_hz,
        "median": float(np.median(values[flat])),
        "drift": drift,
        "relative_mad": relative_mad,
        "rise_sigma": rise_sigma,
    }


def detect_flat_sensitivity_band(
    frequency_hz: np.ndarray,
    sensitivity: np.ndarray,
    *,
    hwhm_hz: float | None,
    minimum_frequency_hz: float,
) -> dict[str, Any]:
    """自动识别连续平坦段，并验证不同分析设置下的中位数稳定性。"""
    frequency = np.asarray(frequency_hz, dtype=float)
    values = np.asarray(sensitivity, dtype=float)
    empty_mask = np.zeros_like(values, dtype=bool)
    empty = {
        "success": False,
        "reason": "",
        "band_hz": np.asarray([np.nan, np.nan], dtype=float),
        "mask": empty_mask,
        "median": float("nan"),
        "candidate_count": 0,
        "median_cv": float("nan"),
        "median_p10_p90_span": float("nan"),
        "drift": float("nan"),
        "relative_mad": float("nan"),
        "rise_sigma": float("nan"),
        "low_boundary_p10_p90_hz": np.asarray(
            [np.nan, np.nan],
            dtype=float,
        ),
        "high_boundary_p10_p90_hz": np.asarray(
            [np.nan, np.nan],
            dtype=float,
        ),
    }
    if frequency.shape != values.shape:
        raise ValueError("灵敏度与频率轴形状不一致")
    if hwhm_hz is None or not np.isfinite(hwhm_hz) or hwhm_hz <= 0:
        return {**empty, "reason": "缺少有效 HWHM，无法确定平台搜索尺度"}

    candidates = [
        candidate
        for bin_count in _FLAT_BIN_COUNTS
        for observation_factor in _FLAT_OBSERVATION_FACTORS
        if (
            candidate := _detect_flat_band_variant(
                frequency,
                values,
                hwhm_hz=float(hwhm_hz),
                minimum_frequency_hz=float(minimum_frequency_hz),
                bin_count=bin_count,
                observation_factor=observation_factor,
            )
        )
        is not None
    ]
    if len(candidates) < 6:
        return {
            **empty,
            "reason": "有效平台候选少于 6 组",
            "candidate_count": len(candidates),
        }

    medians = np.asarray(
        [item["median"] for item in candidates],
        dtype=float,
    )
    median_of_medians = float(np.median(medians))
    median_cv = (
        1.4826
        * float(np.median(np.abs(medians - median_of_medians)))
        / median_of_medians
    )
    median_span = float(
        (np.percentile(medians, 90) - np.percentile(medians, 10))
        / median_of_medians
    )
    low_boundaries = np.asarray(
        [item["low_hz"] for item in candidates],
        dtype=float,
    )
    high_boundaries = np.asarray(
        [item["high_hz"] for item in candidates],
        dtype=float,
    )
    drift = float(np.median([item["drift"] for item in candidates]))
    relative_mad = float(
        np.median([item["relative_mad"] for item in candidates])
    )
    rise_sigma = float(
        np.median([item["rise_sigma"] for item in candidates])
    )

    failures: list[str] = []
    if median_cv > _FLAT_MEDIAN_CV_MAX:
        failures.append(
            f"中位数稳健 CV={median_cv:.1%}>{_FLAT_MEDIAN_CV_MAX:.1%}"
        )
    if median_span > _FLAT_MEDIAN_SPAN_MAX:
        failures.append(
            "中位数 P10-P90 跨度="
            f"{median_span:.1%}>{_FLAT_MEDIAN_SPAN_MAX:.1%}"
        )
    if drift > _FLAT_DRIFT_MAX:
        failures.append(
            f"平台漂移={drift:.1%}>{_FLAT_DRIFT_MAX:.1%}"
        )
    if relative_mad > _FLAT_RELATIVE_MAD_MAX:
        failures.append(
            "平台相对 MAD="
            f"{relative_mad:.1%}>{_FLAT_RELATIVE_MAD_MAX:.1%}"
        )
    if rise_sigma < _FLAT_RISE_SIGMA_MIN:
        failures.append(
            f"高频上升证据={rise_sigma:.1f}σ<{_FLAT_RISE_SIGMA_MIN:.1f}σ"
        )

    low_hz = float(np.median(low_boundaries))
    high_hz = float(np.median(high_boundaries))
    flat_mask = (
        np.isfinite(frequency)
        & np.isfinite(values)
        & (values > 0)
        & (frequency >= low_hz)
        & (frequency <= high_hz)
    )
    if not np.any(flat_mask):
        failures.append("自动平台区间内没有有效频谱点")
    success = not failures
    return {
        "success": success,
        "reason": "" if success else "；".join(failures),
        "band_hz": np.asarray([low_hz, high_hz], dtype=float),
        "mask": flat_mask if success else empty_mask,
        "median": (
            float(np.median(values[flat_mask]))
            if success
            else float("nan")
        ),
        "candidate_count": len(candidates),
        "median_cv": median_cv,
        "median_p10_p90_span": median_span,
        "drift": drift,
        "relative_mad": relative_mad,
        "rise_sigma": rise_sigma,
        "low_boundary_p10_p90_hz": np.percentile(
            low_boundaries,
            [10, 90],
        ),
        "high_boundary_p10_p90_hz": np.percentile(
            high_boundaries,
            [10, 90],
        ),
    }


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
    flat = detect_flat_sensitivity_band(
        np.asarray(frequency_hz, dtype=float),
        corrected_vpp,
        hwhm_hz=hwhm_hz,
        minimum_frequency_hz=low_freq_skip_hz,
    )
    median_vpp = float(flat["median"])
    result: dict[str, Any] = {
        "raw_vpp_per_sqrt_hz": raw_vpp,
        "corrected_vpp_per_sqrt_hz": corrected_vpp,
        "correction_factor": correction,
        "flat_mask": flat["mask"],
        "flat_band_hz": flat["band_hz"],
        "flat_median_vpp_per_sqrt_hz": median_vpp,
        "flat_detection_success": np.bool_(flat["success"]),
        "flat_detection_reason": np.str_(flat["reason"]),
        "flat_detection_candidate_count": np.int64(
            flat["candidate_count"]
        ),
        "flat_detection_median_cv": np.float64(flat["median_cv"]),
        "flat_detection_median_p10_p90_span": np.float64(
            flat["median_p10_p90_span"]
        ),
        "flat_detection_drift": np.float64(flat["drift"]),
        "flat_detection_relative_mad": np.float64(
            flat["relative_mad"]
        ),
        "flat_detection_rise_sigma": np.float64(flat["rise_sigma"]),
        "flat_detection_low_boundary_p10_p90_hz": flat[
            "low_boundary_p10_p90_hz"
        ],
        "flat_detection_high_boundary_p10_p90_hz": flat[
            "high_boundary_p10_p90_hz"
        ],
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
