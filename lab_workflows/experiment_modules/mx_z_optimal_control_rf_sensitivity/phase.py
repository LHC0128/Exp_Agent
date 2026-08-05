"""Y RF 触发相位扫描的绝对值正弦拟合。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
from scipy.optimize import curve_fit


def inside_absolute_sine(
    phase_deg: np.ndarray | float,
    baseline: float,
    amplitude: float,
    phase_zero_deg: float,
) -> np.ndarray:
    """正式模型 |C + A sin(phi-phi0)|。"""
    phase = np.deg2rad(np.asarray(phase_deg, dtype=float) - phase_zero_deg)
    return np.abs(baseline + amplitude * np.sin(phase))


def outside_absolute_sine(
    phase_deg: np.ndarray | float,
    baseline: float,
    amplitude: float,
    phase_zero_deg: float,
) -> np.ndarray:
    """诊断模型 C + A |sin(phi-phi0)|。"""
    phase = np.deg2rad(np.asarray(phase_deg, dtype=float) - phase_zero_deg)
    return baseline + amplitude * np.abs(np.sin(phase))


@dataclass(frozen=True, slots=True)
class PhaseFitResult:
    """单个相位模型的拟合结果。"""

    model: str
    success: bool
    parameters: tuple[float, float, float]
    uncertainties: tuple[float, float, float]
    covariance: tuple[tuple[float, ...], ...]
    r_squared: float
    selected_phase_deg: float
    observed_max_phase_deg: float
    rejection_reasons: tuple[str, ...]

    @property
    def amplitude(self) -> float:
        return self.parameters[1]

    @property
    def amplitude_uncertainty(self) -> float:
        return self.uncertainties[1]

    def to_dict(self) -> dict[str, object]:
        return {
            "model": self.model,
            "success": self.success,
            "parameters": list(self.parameters),
            "uncertainties": list(self.uncertainties),
            "covariance": [list(row) for row in self.covariance],
            "r_squared": self.r_squared,
            "selected_phase_deg": self.selected_phase_deg,
            "observed_max_phase_deg": self.observed_max_phase_deg,
            "rejection_reasons": list(self.rejection_reasons),
        }


@dataclass(frozen=True, slots=True)
class QuadraturePhaseFitResult:
    """基于相差 180° 复数差分的 Y RF 校相结果。"""

    model: str
    success: bool
    selected_phase_deg: float
    observed_max_phase_deg: float
    baseline_x_v: float
    baseline_y_v: float
    baseline_r_v: float
    baseline_angle_deg: float
    in_phase_amplitude_v: float
    amplitude_uncertainty_v: float
    r_squared: float
    fit_cos_coefficient_v: float
    fit_sin_coefficient_v: float
    fit_covariance: tuple[tuple[float, ...], ...]
    quadrature_cos_coefficient_v: float
    quadrature_sin_coefficient_v: float
    quadrature_at_selected_v: float
    quadrature_fraction: float
    pair_count: int
    rejection_reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "model": self.model,
            "success": self.success,
            "selected_phase_deg": self.selected_phase_deg,
            "observed_max_phase_deg": self.observed_max_phase_deg,
            "baseline_x_v": self.baseline_x_v,
            "baseline_y_v": self.baseline_y_v,
            "baseline_r_v": self.baseline_r_v,
            "baseline_angle_deg": self.baseline_angle_deg,
            "in_phase_amplitude_v": self.in_phase_amplitude_v,
            "amplitude_uncertainty_v": self.amplitude_uncertainty_v,
            "r_squared": self.r_squared,
            "fit_cos_coefficient_v": self.fit_cos_coefficient_v,
            "fit_sin_coefficient_v": self.fit_sin_coefficient_v,
            "fit_covariance": [
                list(row) for row in self.fit_covariance
            ],
            "quadrature_cos_coefficient_v": (
                self.quadrature_cos_coefficient_v
            ),
            "quadrature_sin_coefficient_v": (
                self.quadrature_sin_coefficient_v
            ),
            "quadrature_at_selected_v": self.quadrature_at_selected_v,
            "quadrature_fraction": self.quadrature_fraction,
            "pair_count": self.pair_count,
            "rejection_reasons": list(self.rejection_reasons),
        }


@dataclass(frozen=True, slots=True)
class ComplexPhaseOutlierDetection:
    """完整 X/Y 相位曲线的稳健离群点诊断。"""

    sigma_threshold: float
    noise_multiplier: float
    median_residual_v: float
    robust_sigma_v: float
    median_point_complex_std_v: float
    threshold_v: float
    residual_v: tuple[float, ...]
    outlier_indices: tuple[int, ...]
    outlier_phase_deg: tuple[float, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "model": "complex_first_harmonic_residual",
            "sigma_threshold": self.sigma_threshold,
            "noise_multiplier": self.noise_multiplier,
            "median_residual_v": self.median_residual_v,
            "robust_sigma_v": self.robust_sigma_v,
            "median_point_complex_std_v": (
                self.median_point_complex_std_v
            ),
            "threshold_v": self.threshold_v,
            "residual_v": list(self.residual_v),
            "outlier_indices": list(self.outlier_indices),
            "outlier_phase_deg": list(self.outlier_phase_deg),
        }


def _circular_distance_deg(left: float, right: float) -> float:
    return abs((left - right + 180.0) % 360.0 - 180.0)


def _opposite_phase_pairs(
    phase_deg: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """返回 [0, 180) 半周中的相位及其 +180° 索引。"""
    phase = np.mod(np.asarray(phase_deg, dtype=float).reshape(-1), 360.0)
    if phase.size < 8 or phase.size % 2:
        raise ValueError("正交校相至少需要 4 对相差 180° 的相位点")
    if np.unique(np.round(phase, decimals=9)).size != phase.size:
        raise ValueError("正交校相相位轴包含重复点")
    first_indices = np.flatnonzero(phase < 180.0)
    if first_indices.size * 2 != phase.size:
        raise ValueError("正交校相相位轴必须完整覆盖一个 360° 周期")
    opposite_indices: list[int] = []
    for index in first_indices:
        target = phase[index] + 180.0
        distances = np.asarray(
            [
                _circular_distance_deg(float(value), float(target))
                for value in phase
            ],
            dtype=float,
        )
        opposite = int(np.argmin(distances))
        if distances[opposite] > 1e-7:
            raise ValueError(
                f"相位 {phase[index]:.9g}° 缺少 "
                f"{target % 360.0:.9g}° 配对点"
            )
        opposite_indices.append(opposite)
    if np.unique(opposite_indices).size != first_indices.size:
        raise ValueError("正交校相的 180° 配对点不唯一")
    return first_indices, np.asarray(opposite_indices, dtype=int)


def paired_phase_order(phase_deg: np.ndarray) -> np.ndarray:
    """把完整相位轴重排为 φ、φ+180° 紧邻的采集顺序。"""
    phase = np.asarray(phase_deg, dtype=float).reshape(-1)
    first, opposite = _opposite_phase_pairs(phase)
    order = np.column_stack((first, opposite)).reshape(-1)
    return phase[order]


def detect_complex_phase_outliers(
    phase_deg: np.ndarray,
    x_mean_v: np.ndarray,
    y_mean_v: np.ndarray,
    complex_std_v: np.ndarray,
    *,
    sigma_threshold: float,
    noise_multiplier: float = 10.0,
) -> ComplexPhaseOutlierDetection:
    """用完整复数一阶谐波的稳健残差识别触发错误点。"""
    phase = np.asarray(phase_deg, dtype=float).reshape(-1)
    x_values = np.asarray(x_mean_v, dtype=float).reshape(-1)
    y_values = np.asarray(y_mean_v, dtype=float).reshape(-1)
    point_std = np.asarray(complex_std_v, dtype=float).reshape(-1)
    if not (
        phase.shape
        == x_values.shape
        == y_values.shape
        == point_std.shape
    ):
        raise ValueError("相位离群检测的相位、X、Y、复噪声长度不一致")
    if phase.size < 8:
        raise ValueError("相位离群检测至少需要 8 个点")
    if not (
        np.all(np.isfinite(phase))
        and np.all(np.isfinite(x_values))
        and np.all(np.isfinite(y_values))
        and np.all(np.isfinite(point_std))
    ):
        raise ValueError("相位离群检测包含 NaN 或无穷值")
    if sigma_threshold <= 0.0 or noise_multiplier <= 0.0:
        raise ValueError("相位离群检测阈值必须大于 0")

    radians = np.deg2rad(phase)
    design = np.column_stack(
        (
            np.ones(phase.size, dtype=float),
            np.cos(radians),
            np.sin(radians),
        )
    )
    x_coefficients, _, _, _ = np.linalg.lstsq(
        design,
        x_values,
        rcond=None,
    )
    y_coefficients, _, _, _ = np.linalg.lstsq(
        design,
        y_values,
        rcond=None,
    )
    prediction = design @ x_coefficients + 1j * (
        design @ y_coefficients
    )
    residual = np.abs((x_values + 1j * y_values) - prediction)
    median_residual = float(np.median(residual))
    robust_sigma = float(
        1.4826 * np.median(np.abs(residual - median_residual))
    )
    median_point_std = float(np.median(point_std))
    robust_threshold = (
        median_residual + sigma_threshold * robust_sigma
    )
    noise_threshold = noise_multiplier * median_point_std
    threshold = max(robust_threshold, noise_threshold, 1e-12)
    outlier_indices = np.flatnonzero(residual > threshold)
    return ComplexPhaseOutlierDetection(
        sigma_threshold=float(sigma_threshold),
        noise_multiplier=float(noise_multiplier),
        median_residual_v=median_residual,
        robust_sigma_v=robust_sigma,
        median_point_complex_std_v=median_point_std,
        threshold_v=float(threshold),
        residual_v=tuple(float(value) for value in residual),
        outlier_indices=tuple(int(value) for value in outlier_indices),
        outlier_phase_deg=tuple(
            float(phase[index]) for index in outlier_indices
        ),
    )


def _harmonic_fit(
    phase_deg: np.ndarray,
    values: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    radians = np.deg2rad(np.asarray(phase_deg, dtype=float))
    design = np.column_stack((np.cos(radians), np.sin(radians)))
    coefficients, _, _, _ = np.linalg.lstsq(
        design,
        np.asarray(values, dtype=float),
        rcond=None,
    )
    fitted = design @ coefficients
    residual = np.asarray(values, dtype=float) - fitted
    dof = max(int(values.size) - 2, 1)
    residual_variance = float(np.sum(residual**2)) / dof
    covariance = residual_variance * np.linalg.pinv(design.T @ design)
    total_sum = float(
        np.sum((np.asarray(values, dtype=float) - np.mean(values)) ** 2)
    )
    residual_sum = float(np.sum(residual**2))
    r_squared = (
        1.0 - residual_sum / total_sum
        if total_sum > 0.0
        else float("-inf")
    )
    return coefficients, covariance, fitted, r_squared


def fit_quadrature_phase_scan(
    phase_deg: np.ndarray,
    x_mean_v: np.ndarray,
    y_mean_v: np.ndarray,
    *,
    r_squared_min: float,
    amplitude_sigma_min: float,
) -> tuple[QuadraturePhaseFitResult, dict[str, np.ndarray]]:
    """用相差 180° 的复数差分拟合建设性 Y RF 相位。"""
    phase = np.asarray(phase_deg, dtype=float).reshape(-1)
    x_values = np.asarray(x_mean_v, dtype=float).reshape(-1)
    y_values = np.asarray(y_mean_v, dtype=float).reshape(-1)
    if x_values.shape != phase.shape or y_values.shape != phase.shape:
        raise ValueError("正交校相的相位、X、Y 数组长度不一致")
    if not (
        np.all(np.isfinite(phase))
        and np.all(np.isfinite(x_values))
        and np.all(np.isfinite(y_values))
    ):
        raise ValueError("正交校相包含 NaN 或无穷值")

    first, opposite = _opposite_phase_pairs(phase)
    complex_values = x_values + 1j * y_values
    pair_phase = np.mod(phase[first], 360.0)
    pair_center = (complex_values[first] + complex_values[opposite]) / 2.0
    pair_delta = (complex_values[first] - complex_values[opposite]) / 2.0
    baseline = complex(np.mean(pair_center))
    baseline_r = float(abs(baseline))
    if baseline_r <= 1e-12:
        raise ValueError("正交校相基线复幅度为零，无法定义同相方向")
    rotation = np.exp(-1j * np.angle(baseline))
    rotated_delta = pair_delta * rotation
    in_phase = np.real(rotated_delta)
    quadrature = np.imag(rotated_delta)

    coefficients, covariance, fitted, r_squared = _harmonic_fit(
        pair_phase,
        in_phase,
    )
    quadrature_coefficients, _, quadrature_fitted, _ = _harmonic_fit(
        pair_phase,
        quadrature,
    )
    amplitude = float(np.hypot(*coefficients))
    selected_phase = float(
        np.rad2deg(np.arctan2(coefficients[1], coefficients[0])) % 360.0
    )
    observed_max_phase = float(pair_phase[int(np.argmax(in_phase))])
    if amplitude > 0.0:
        gradient = coefficients / amplitude
        amplitude_variance = float(gradient @ covariance @ gradient)
        amplitude_uncertainty = float(
            np.sqrt(max(amplitude_variance, 0.0))
        )
    else:
        amplitude_uncertainty = float("inf")
    selected_radians = np.deg2rad(selected_phase)
    quadrature_at_selected = float(
        quadrature_coefficients[0] * np.cos(selected_radians)
        + quadrature_coefficients[1] * np.sin(selected_radians)
    )
    quadrature_fraction = (
        abs(quadrature_at_selected) / amplitude
        if amplitude > 0.0
        else float("inf")
    )

    reasons: list[str] = []
    if not np.all(np.isfinite(covariance)):
        reasons.append("正交校相协方差包含非有限值")
    if not np.isfinite(r_squared) or r_squared < r_squared_min:
        reasons.append(
            f"正交同相拟合 R^2={r_squared:.6g} "
            f"低于门槛 {r_squared_min:.6g}"
        )
    if (
        not np.isfinite(amplitude_uncertainty)
        or amplitude_uncertainty <= 0.0
        or amplitude <= amplitude_sigma_min * amplitude_uncertainty
    ):
        reasons.append(
            "正交同相幅度未超过 "
            f"{amplitude_sigma_min:.6g}σ 显著性门槛"
        )

    result = QuadraturePhaseFitResult(
        model="paired_complex_projection",
        success=not reasons,
        selected_phase_deg=selected_phase,
        observed_max_phase_deg=observed_max_phase,
        baseline_x_v=float(np.real(baseline)),
        baseline_y_v=float(np.imag(baseline)),
        baseline_r_v=baseline_r,
        baseline_angle_deg=float(np.rad2deg(np.angle(baseline))),
        in_phase_amplitude_v=amplitude,
        amplitude_uncertainty_v=amplitude_uncertainty,
        r_squared=float(r_squared),
        fit_cos_coefficient_v=float(coefficients[0]),
        fit_sin_coefficient_v=float(coefficients[1]),
        fit_covariance=tuple(
            tuple(float(value) for value in row)
            for row in covariance
        ),
        quadrature_cos_coefficient_v=float(quadrature_coefficients[0]),
        quadrature_sin_coefficient_v=float(quadrature_coefficients[1]),
        quadrature_at_selected_v=quadrature_at_selected,
        quadrature_fraction=float(quadrature_fraction),
        pair_count=int(pair_phase.size),
        rejection_reasons=tuple(reasons),
    )
    arrays = {
        "paired_phase_deg": pair_phase,
        "opposite_phase_deg": np.mod(phase[opposite], 360.0),
        "pair_center_x_v": np.real(pair_center),
        "pair_center_y_v": np.imag(pair_center),
        "pair_delta_x_v": np.real(pair_delta),
        "pair_delta_y_v": np.imag(pair_delta),
        "pair_in_phase_v": in_phase,
        "pair_quadrature_v": quadrature,
        "pair_fit_in_phase_v": fitted,
        "pair_fit_quadrature_v": quadrature_fitted,
    }
    return result, arrays


def _selected_peak(
    model: Callable[..., np.ndarray],
    parameters: tuple[float, float, float],
    observed_max_phase_deg: float,
) -> float:
    baseline, amplitude, phase_zero = parameters
    candidates = np.asarray(
        [(phase_zero + 90.0) % 360.0, (phase_zero + 270.0) % 360.0],
        dtype=float,
    )
    values = model(candidates, baseline, amplitude, phase_zero)
    maximum = float(np.max(values))
    eligible = candidates[
        np.isclose(values, maximum, rtol=1e-10, atol=max(1e-12, abs(maximum) * 1e-10))
    ]
    return float(
        min(
            eligible,
            key=lambda value: _circular_distance_deg(
                float(value), observed_max_phase_deg
            ),
        )
    )


def _fit_model(
    phase_deg: np.ndarray,
    r_mean_v: np.ndarray,
    *,
    model: Callable[..., np.ndarray],
    model_name: str,
    baseline_bounds: tuple[float, float],
    r_squared_min: float | None,
    amplitude_sigma_min: float | None,
) -> PhaseFitResult:
    observed_max_phase = float(phase_deg[int(np.argmax(r_mean_v))] % 360.0)
    data_span = max(float(np.ptp(r_mean_v)), 1e-12)
    data_max = max(float(np.max(np.abs(r_mean_v))), data_span)
    best: tuple[np.ndarray, np.ndarray, float] | None = None
    for phase_guess in np.arange(0.0, 360.0, 30.0):
        try:
            parameters, covariance = curve_fit(
                model,
                phase_deg,
                r_mean_v,
                p0=(
                    float(np.min(r_mean_v)),
                    data_span,
                    float(phase_guess),
                ),
                bounds=(
                    [baseline_bounds[0], 0.0, -720.0],
                    [baseline_bounds[1], data_max * 4.0, 720.0],
                ),
                maxfev=50000,
            )
        except (RuntimeError, ValueError, FloatingPointError):
            continue
        fitted = model(phase_deg, *parameters)
        residual_sum = float(np.sum((r_mean_v - fitted) ** 2))
        if best is None or residual_sum < best[2]:
            best = (parameters, covariance, residual_sum)

    reasons: list[str] = []
    if best is None:
        reasons.append("绝对值正弦拟合未收敛")
        nan3 = (float("nan"),) * 3
        return PhaseFitResult(
            model=model_name,
            success=False,
            parameters=nan3,
            uncertainties=nan3,
            covariance=tuple((float("nan"),) * 3 for _ in range(3)),
            r_squared=float("nan"),
            selected_phase_deg=float("nan"),
            observed_max_phase_deg=observed_max_phase,
            rejection_reasons=tuple(reasons),
        )

    parameters_raw, covariance_raw, residual_sum = best
    parameters = (
        float(parameters_raw[0]),
        float(parameters_raw[1]),
        float(parameters_raw[2] % 360.0),
    )
    covariance_array = np.asarray(covariance_raw, dtype=float)
    uncertainties_array = np.sqrt(np.clip(np.diag(covariance_array), 0.0, None))
    uncertainties = tuple(float(value) for value in uncertainties_array)
    total_sum = float(np.sum((r_mean_v - np.mean(r_mean_v)) ** 2))
    r_squared = 1.0 - residual_sum / total_sum if total_sum > 0 else float("-inf")
    if not np.all(np.isfinite(covariance_array)):
        reasons.append("拟合协方差包含非有限值")
    if r_squared_min is not None and (
        not np.isfinite(r_squared) or r_squared < r_squared_min
    ):
        reasons.append(
            f"R^2={r_squared:.6g} 低于门槛 {r_squared_min:.6g}"
        )
    if amplitude_sigma_min is not None:
        amplitude_uncertainty = uncertainties[1]
        if (
            not np.isfinite(amplitude_uncertainty)
            or amplitude_uncertainty <= 0
            or parameters[1] <= amplitude_sigma_min * amplitude_uncertainty
        ):
            reasons.append(
                "拟合幅度未超过 "
                f"{amplitude_sigma_min:.6g}σ 显著性门槛"
            )
    selected_phase = _selected_peak(
        model,
        parameters,
        observed_max_phase,
    )
    return PhaseFitResult(
        model=model_name,
        success=not reasons,
        parameters=parameters,
        uncertainties=uncertainties,
        covariance=tuple(
            tuple(float(value) for value in row)
            for row in covariance_array
        ),
        r_squared=float(r_squared),
        selected_phase_deg=selected_phase,
        observed_max_phase_deg=observed_max_phase,
        rejection_reasons=tuple(reasons),
    )


def fit_phase_scan(
    phase_deg: np.ndarray,
    r_mean_v: np.ndarray,
    *,
    r_squared_min: float,
    amplitude_sigma_min: float,
) -> tuple[PhaseFitResult, PhaseFitResult]:
    """拟合正式模型和诊断模型，并返回二者结果。"""
    phase = np.asarray(phase_deg, dtype=float).reshape(-1)
    response = np.asarray(r_mean_v, dtype=float).reshape(-1)
    if phase.size < 6 or response.size != phase.size:
        raise ValueError("相位扫描至少需要 6 个等长数据点")
    if not np.all(np.isfinite(phase)) or not np.all(np.isfinite(response)):
        raise ValueError("相位扫描包含 NaN 或无穷值")
    data_max = max(float(np.max(np.abs(response))), 1e-12)
    primary = _fit_model(
        phase,
        response,
        model=inside_absolute_sine,
        model_name="abs(C + A*sin(phi-phi0))",
        baseline_bounds=(-2.0 * data_max, 2.0 * data_max),
        r_squared_min=r_squared_min,
        amplitude_sigma_min=amplitude_sigma_min,
    )
    diagnostic = _fit_model(
        phase,
        response,
        model=outside_absolute_sine,
        model_name="C + A*abs(sin(phi-phi0))",
        baseline_bounds=(0.0, 2.0 * data_max),
        r_squared_min=None,
        amplitude_sigma_min=None,
    )
    return primary, diagnostic
