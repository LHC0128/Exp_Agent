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


DISPERSION_MODEL_NAME = "abs(dispersion(B_eff(phi)))"


def dispersion_phase_response(
    phase_deg: np.ndarray | float,
    scale: float,
    resonance_amplitude: float,
    width: float,
    residual_amplitude: float,
    residual_phase_deg: float,
    y_rf_amplitude_vpp: float,
) -> np.ndarray:
    """|色散| 折叠响应：R = |scale*(B-b0)/((B-b0)^2+w^2)|。

    B(phi) = |A e^{i phi} + C e^{i phi_c}| 是 Y RF 矢量与剩磁射频成分的
    合成等效幅度；改变相位即改变等效幅度，响应为色散线形的绝对值。
    """
    relative = np.deg2rad(
        np.asarray(phase_deg, dtype=float) - residual_phase_deg
    )
    effective = np.sqrt(
        y_rf_amplitude_vpp**2
        + residual_amplitude**2
        + 2.0
        * y_rf_amplitude_vpp
        * residual_amplitude
        * np.cos(relative)
    )
    detuning = effective - resonance_amplitude
    return np.abs(scale * detuning / (detuning**2 + width**2))


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
class DispersionPhaseFitResult:
    """|色散| 折叠相位响应模型的拟合结果。

    参数顺序为 (scale, resonance_amplitude, width, residual_amplitude,
    residual_phase_deg)，模型为
    R = |scale*(B-b0)/((B-b0)^2+w^2)|，B(phi) = |A e^{i phi} + C e^{i phi_c}|。
    """

    model: str
    success: bool
    parameters: tuple[float, float, float, float, float]
    uncertainties: tuple[float, float, float, float, float]
    covariance: tuple[tuple[float, ...], ...]
    r_squared: float
    selected_phase_deg: float
    observed_max_phase_deg: float
    peak_to_peak_v: float
    noise_median_v: float
    signal_to_noise: float
    y_rf_amplitude_vpp: float
    rejection_reasons: tuple[str, ...]

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
            "peak_to_peak_v": self.peak_to_peak_v,
            "noise_median_v": self.noise_median_v,
            "signal_to_noise": self.signal_to_noise,
            "y_rf_amplitude_vpp": self.y_rf_amplitude_vpp,
            "rejection_reasons": list(self.rejection_reasons),
        }


@dataclass(frozen=True, slots=True)
class DispersionPhaseOutlierDetection:
    """基于非线性色散拟合残差的跨相位异常检测结果。"""

    model: str
    sigma_threshold: float
    noise_multiplier: float
    fit_success: bool
    fit_r_squared: float
    fit_rejection_reasons: tuple[str, ...]
    median_residual_v: float
    robust_sigma_v: float
    median_point_std_v: float
    threshold_v: float
    residual_v: tuple[float, ...]
    outlier_indices: tuple[int, ...]
    outlier_phase_deg: tuple[float, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "model": self.model,
            "sigma_threshold": self.sigma_threshold,
            "noise_multiplier": self.noise_multiplier,
            "fit_success": self.fit_success,
            "fit_r_squared": self.fit_r_squared,
            "fit_rejection_reasons": list(self.fit_rejection_reasons),
            "median_residual_v": self.median_residual_v,
            "robust_sigma_v": self.robust_sigma_v,
            "median_point_std_v": self.median_point_std_v,
            "threshold_v": self.threshold_v,
            "residual_v": list(self.residual_v),
            "outlier_indices": list(self.outlier_indices),
            "outlier_phase_deg": list(self.outlier_phase_deg),
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
    data_mean = float(np.mean(r_mean_v))
    data_min = float(np.min(r_mean_v))
    # |C + A sin| 在 A<C（不触零）与 A>C（触零）两种形状下均有局部极小，
    # 单一起点族会被尖点卡住；用多组 (基线, 幅度) 起点网格加线性谐波估计兜底。
    starts: list[tuple[float, float, float]] = []
    for phase_guess in np.arange(0.0, 360.0, 30.0):
        for baseline_guess, amplitude_guess in (
            (data_min, data_span),
            (data_mean, data_span / 2.0),
            (data_mean, data_span),
            (data_min, data_span / 2.0),
        ):
            starts.append(
                (
                    baseline_guess,
                    amplitude_guess,
                    float(phase_guess),
                )
            )
    radians = np.deg2rad(np.asarray(phase_deg, dtype=float))
    design = np.column_stack(
        (
            np.ones(phase_deg.size, dtype=float),
            np.cos(radians),
            np.sin(radians),
        )
    )
    try:
        linear, _, _, _ = np.linalg.lstsq(design, r_mean_v, rcond=None)
        harmonic_amplitude = float(np.hypot(linear[1], linear[2]))
        harmonic_phase = float(
            np.rad2deg(np.arctan2(linear[2], linear[1]))
        )
        for harmonic_guess in (harmonic_phase, harmonic_phase + 180.0):
            starts.append((float(linear[0]), harmonic_amplitude, harmonic_guess))
    except np.linalg.LinAlgError:
        pass

    best: tuple[np.ndarray, np.ndarray, float] | None = None
    for start in starts:
        try:
            parameters, covariance = curve_fit(
                model,
                phase_deg,
                r_mean_v,
                p0=start,
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


def fit_dispersion_phase_scan(
    phase_deg: np.ndarray,
    r_mean_v: np.ndarray,
    r_std_v: np.ndarray,
    *,
    y_rf_amplitude_vpp: float,
    r_squared_min: float,
    amplitude_sigma_min: float,
    max_starts: int | None = None,
) -> DispersionPhaseFitResult:
    """用 |色散| 折叠模型拟合 R 对 Y RF 相位的响应。

    正式模型：R = |scale*(B-b0)/((B-b0)^2+w^2)|，
    B(phi) = |A e^{i phi} + C e^{i phi_c}|，A 固定为校相 Y RF 幅度。
    多起点非线性拟合，选残差最小解；选中相位取拟合曲线峰值中
    距离观测最大相位最近的峰。
    """
    phase = np.asarray(phase_deg, dtype=float).reshape(-1)
    response = np.asarray(r_mean_v, dtype=float).reshape(-1)
    noise = np.asarray(r_std_v, dtype=float).reshape(-1)
    if phase.size < 6 or response.size != phase.size or noise.size != phase.size:
        raise ValueError("|色散| 相位拟合至少需要 6 个等长的相位、R、R 噪声点")
    if not (
        np.all(np.isfinite(phase))
        and np.all(np.isfinite(response))
        and np.all(np.isfinite(noise))
    ):
        raise ValueError("|色散| 相位拟合包含 NaN 或无穷值")
    if not np.isfinite(y_rf_amplitude_vpp) or y_rf_amplitude_vpp <= 0.0:
        raise ValueError("|色散| 相位拟合的校相 Y RF 幅度必须为有限正值")
    if np.any(noise < 0.0):
        raise ValueError("|色散| 相位拟合的点噪声必须非负")

    observed_max_phase = float(phase[int(np.argmax(response))] % 360.0)
    data_span = max(float(np.ptp(response)), 1e-12)
    data_max = max(float(np.max(np.abs(response))), data_span)
    noise_median = float(np.median(noise))
    amplitude_a = float(y_rf_amplitude_vpp)

    # 多起点网格：剩磁幅度、工作点、线宽、剩磁相位分别覆盖常见情形。
    starts: list[tuple[float, float, float, float, float]] = []
    for residual_phase_guess in np.arange(0.0, 360.0, 30.0):
        for residual_guess in (
            0.2 * amplitude_a,
            0.5 * amplitude_a,
            amplitude_a,
            2.0 * amplitude_a,
        ):
            for resonance_guess in (0.5 * amplitude_a, amplitude_a):
                for width_guess in (
                    0.05 * amplitude_a,
                    0.2 * amplitude_a,
                    0.5 * amplitude_a,
                ):
                    starts.append(
                        (
                            data_max * width_guess,
                            resonance_guess,
                            width_guess,
                            residual_guess,
                            float(residual_phase_guess),
                        )
                    )

    scale_upper = 10.0 * amplitude_a * data_max
    amplitude_upper = 5.0 * amplitude_a
    bounds = (
        [1e-12, 1e-12, 1e-12, 0.0, -720.0],
        [scale_upper, amplitude_upper, 3.0 * amplitude_a, amplitude_upper, 720.0],
    )
    if max_starts is not None:
        if max_starts < 1:
            raise ValueError("|色散| 相位拟合的最大起点数必须大于 0")
        if max_starts < len(starts):
            # 保留整个剩磁相位范围，避免简单截断只覆盖少数相位起点。
            indices = np.unique(
                np.rint(
                    np.linspace(0, len(starts) - 1, int(max_starts))
                ).astype(int)
            )
            starts = [starts[int(index)] for index in indices]

    best: tuple[np.ndarray, np.ndarray, float] | None = None
    for start in starts:
        try:
            parameters, covariance = curve_fit(
                lambda phi, s, b0, w, c, pc: dispersion_phase_response(
                    phi, s, b0, w, c, pc, amplitude_a
                ),
                phase,
                response,
                p0=start,
                bounds=bounds,
                maxfev=200000,
            )
        except (RuntimeError, ValueError, FloatingPointError):
            continue
        fitted = dispersion_phase_response(phase, *parameters, amplitude_a)
        residual_sum = float(np.sum((response - fitted) ** 2))
        if best is None or residual_sum < best[2]:
            best = (parameters, covariance, residual_sum)

    nan5 = (float("nan"),) * 5
    reasons: list[str] = []
    if best is None:
        reasons.append("|色散| 相位拟合未收敛")
        return DispersionPhaseFitResult(
            model=DISPERSION_MODEL_NAME,
            success=False,
            parameters=nan5,
            uncertainties=nan5,
            covariance=tuple((float("nan"),) * 5 for _ in range(5)),
            r_squared=float("nan"),
            selected_phase_deg=float("nan"),
            observed_max_phase_deg=observed_max_phase,
            peak_to_peak_v=float("nan"),
            noise_median_v=noise_median,
            signal_to_noise=float("nan"),
            y_rf_amplitude_vpp=amplitude_a,
            rejection_reasons=tuple(reasons),
        )

    parameters_raw, covariance_raw, residual_sum = best
    parameters = tuple(
        float(value) if index != 4 else float(value) % 360.0
        for index, value in enumerate(parameters_raw)
    )
    covariance_array = np.asarray(covariance_raw, dtype=float)
    uncertainties = tuple(
        float(value)
        for value in np.sqrt(np.clip(np.diag(covariance_array), 0.0, None))
    )
    total_sum = float(np.sum((response - np.mean(response)) ** 2))
    r_squared = (
        1.0 - residual_sum / total_sum
        if total_sum > 0.0
        else float("-inf")
    )
    fitted = dispersion_phase_response(phase, *parameters, amplitude_a)
    peak_to_peak = float(np.ptp(fitted))
    signal_to_noise = (
        peak_to_peak / noise_median
        if noise_median > 0.0
        else float("inf")
    )

    if not np.all(np.isfinite(covariance_array)):
        reasons.append("|色散| 拟合协方差包含非有限值")
    if not np.isfinite(r_squared) or r_squared < r_squared_min:
        reasons.append(
            f"|色散| 拟合 R^2={r_squared:.6g} 低于门槛 {r_squared_min:.6g}"
        )
    if (
        not np.isfinite(signal_to_noise)
        or signal_to_noise < amplitude_sigma_min
    ):
        reasons.append(
            f"拟合响应峰谷差 {peak_to_peak:.6g} V 与中位点噪声 "
            f"{noise_median:.6g} V 的比值 {signal_to_noise:.6g} "
            f"低于 {amplitude_sigma_min:.6g}σ 门槛"
        )

    # 选中相位：拟合曲线的局部峰值中取距离观测最大相位最近的一个。
    dense = np.linspace(0.0, 360.0, 1441)
    curve = dispersion_phase_response(dense, *parameters, amplitude_a)
    peak_mask = (
        (curve[1:-1] > curve[:-2]) & (curve[1:-1] >= curve[2:])
    )
    peaks = dense[1:-1][peak_mask]
    if peaks.size == 0:
        peaks = np.asarray([dense[int(np.argmax(curve))]], dtype=float)
    selected_phase = float(
        min(
            peaks,
            key=lambda value: _circular_distance_deg(
                float(value), observed_max_phase
            ),
        )
    )
    return DispersionPhaseFitResult(
        model=DISPERSION_MODEL_NAME,
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
        peak_to_peak_v=peak_to_peak,
        noise_median_v=noise_median,
        signal_to_noise=float(signal_to_noise),
        y_rf_amplitude_vpp=amplitude_a,
        rejection_reasons=tuple(reasons),
    )


def detect_dispersion_phase_outliers(
    phase_deg: np.ndarray,
    r_mean_v: np.ndarray,
    r_std_v: np.ndarray,
    *,
    y_rf_amplitude_vpp: float,
    sigma_threshold: float,
    noise_multiplier: float = 10.0,
    max_starts: int | None = 24,
) -> DispersionPhaseOutlierDetection:
    """用非线性色散模型残差识别相位异常点。

    拟合门槛在此阶段关闭，只用拟合曲线建立稳健 MAD 阈值；最终是否接受
    网格点仍由 ``fit_dispersion_phase_scan`` 的完整门槛决定。
    """
    phase = np.asarray(phase_deg, dtype=float).reshape(-1)
    response = np.asarray(r_mean_v, dtype=float).reshape(-1)
    noise = np.asarray(r_std_v, dtype=float).reshape(-1)
    if not (phase.shape == response.shape == noise.shape):
        raise ValueError("非线性色散离群检测的相位、R 均值、R 噪声长度不一致")
    if phase.size < 8:
        raise ValueError("非线性色散离群检测至少需要 8 个点")
    if not (
        np.all(np.isfinite(phase))
        and np.all(np.isfinite(response))
        and np.all(np.isfinite(noise))
    ):
        raise ValueError("非线性色散离群检测包含 NaN 或无穷值")
    if np.any(noise < 0.0):
        raise ValueError("非线性色散离群检测的单点噪声不能为负")
    if sigma_threshold <= 0.0 or noise_multiplier <= 0.0:
        raise ValueError("非线性色散离群检测阈值必须大于 0")

    active = np.ones(phase.size, dtype=bool)
    outlier_indices: list[int] = []
    fit = None
    residual = np.zeros_like(response)
    threshold = 1e-12
    median_residual = 0.0
    robust_sigma = 0.0
    median_point_std = float(np.median(noise))
    # 逐次剔除最大稳健残差并重新拟合，避免一个严重坏点把非线性模型
    # 拉向自身、从而掩盖第二个坏点。至少保留 6 个点供五参数模型拟合。
    for _ in range(max(1, phase.size - 6)):
        fit = fit_dispersion_phase_scan(
            phase[active],
            response[active],
            noise[active],
            y_rf_amplitude_vpp=y_rf_amplitude_vpp,
            r_squared_min=float("-inf"),
            amplitude_sigma_min=0.0,
            max_starts=max_starts,
        )
        if not np.all(np.isfinite(fit.parameters)):
            # 拟合完全不收敛时不把整条曲线误判为异常，交由最终拟合报告失败。
            residual = np.zeros_like(response)
            break
        fitted = dispersion_phase_response(
            phase,
            *fit.parameters,
            y_rf_amplitude_vpp,
        )
        residual = np.abs(response - fitted)
        active_residual = residual[active]
        median_residual = float(np.median(active_residual))
        robust_sigma = float(
            1.4826
            * np.median(np.abs(active_residual - median_residual))
        )
        threshold = max(
            median_residual + sigma_threshold * robust_sigma,
            noise_multiplier * median_point_std,
            1e-12,
        )
        candidates = np.flatnonzero(active & (residual > threshold))
        if candidates.size == 0:
            break
        worst = int(candidates[np.argmax(residual[candidates])])
        outlier_indices.append(worst)
        active[worst] = False

    if fit is None:
        fit = fit_dispersion_phase_scan(
            phase,
            response,
            noise,
            y_rf_amplitude_vpp=y_rf_amplitude_vpp,
            r_squared_min=float("-inf"),
            amplitude_sigma_min=0.0,
            max_starts=max_starts,
        )
    # 参数退化时，非线性模型可能通过改变线宽/尺度吸收单个坏点。
    # 此时仅把一阶谐波检测作为保守兜底；正常情况下异常判定仍来自
    # 非线性色散拟合残差，且两种检测结果会合并而不会减少重测点。
    if not outlier_indices:
        from ..mx_z_optimal_control_rf_sensitivity.phase import (
            detect_r_phase_outliers,
        )

        harmonic = detect_r_phase_outliers(
            phase,
            response,
            noise,
            sigma_threshold=sigma_threshold,
            noise_multiplier=noise_multiplier,
        )
        outlier_indices.extend(
            int(index) for index in harmonic.outlier_indices
        )
        if outlier_indices:
            residual = np.maximum(
                residual,
                np.asarray(harmonic.residual_v, dtype=float),
            )
    return DispersionPhaseOutlierDetection(
        model=DISPERSION_MODEL_NAME,
        sigma_threshold=float(sigma_threshold),
        noise_multiplier=float(noise_multiplier),
        fit_success=bool(np.all(np.isfinite(fit.parameters))),
        fit_r_squared=float(fit.r_squared),
        fit_rejection_reasons=tuple(fit.rejection_reasons),
        median_residual_v=median_residual,
        robust_sigma_v=robust_sigma,
        median_point_std_v=median_point_std,
        threshold_v=float(threshold),
        residual_v=tuple(float(value) for value in residual),
        outlier_indices=tuple(int(value) for value in outlier_indices),
        outlier_phase_deg=tuple(
            float(phase[index]) for index in outlier_indices
        ),
    )
