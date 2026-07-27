"""按控制频率轴分离可控与不可控噪声的共享数值模型。"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.interpolate import interp1d
from scipy.optimize import curve_fit


@dataclass(slots=True)
class NoiseSeparationResult:
    """二维 Lorentzian 拟合及插值结果。"""

    parameters: np.ndarray
    uncertainties: np.ndarray
    fit_mask: np.ndarray
    interpolated_mask: np.ndarray
    s_beta: np.ndarray
    n_s1: np.ndarray


def lorentzian_vs_control(
    control_frequency_hz: np.ndarray | float,
    gamma_hz: float,
    amplitude: float,
    baseline: float,
    offset_hz: float,
    fixed_frequency_hz: float,
) -> np.ndarray:
    """返回固定分析频率下随控制频率变化的 PSD。"""
    control = np.asarray(control_frequency_hz, dtype=float) + float(offset_hz)
    frequency = float(fixed_frequency_hz)
    numerator = gamma_hz**2 + frequency**2
    denominator = (
        (gamma_hz**2 - frequency**2 + control**2) ** 2
        + 4.0 * frequency**2 * gamma_hz**2
    )
    return baseline + amplitude * numerator / denominator


def estimate_amplitude_guess(
    peak_height: float,
    gamma_guess_hz: float,
    fixed_frequency_hz: float,
) -> float:
    """从峰高反推 Lorentzian 振幅初值。"""
    denominator = gamma_guess_hz**2 + fixed_frequency_hz**2
    return float(peak_height) * (
        gamma_guess_hz**4
        + 4.0 * gamma_guess_hz**2 * fixed_frequency_hz**2
    ) / denominator


def fit_noise_separation(
    psd_matrix: np.ndarray,
    frequency_axis_hz: np.ndarray,
    control_frequency_axis_hz: np.ndarray,
    *,
    peak_margin_hz: float = 10000.0,
    fit_half_width_hz: float | None = None,
    gamma_guess_hz: float = 300.0,
    minimum_points: int = 10,
    fit_axis_margin_fraction: float = 0.5,
) -> NoiseSeparationResult:
    """逐频率列拟合并返回 ``S_beta`` 与 ``N_S1``。

    ``psd_matrix`` 的第一维对应控制频率，第二维对应 PSD 频率。
    算法与原 XY 控制噪声谱一致：先做 2σ 峰筛选，再拟合四参数
    Lorentzian；脊线附近的失败点使用线性插值补齐。指定
    ``fit_half_width_hz`` 时，只使用固定 PSD 频率中心附近的控制频率点拟合。
    """
    matrix = np.asarray(psd_matrix, dtype=float)
    frequency_axis = np.asarray(frequency_axis_hz, dtype=float).reshape(-1)
    control_axis = np.asarray(control_frequency_axis_hz, dtype=float).reshape(-1)
    if matrix.ndim != 2:
        raise ValueError("PSD 矩阵必须是二维数组")
    if matrix.shape != (control_axis.size, frequency_axis.size):
        raise ValueError(
            "PSD 矩阵形状必须为 (控制点数, 频率点数)，"
            f"实际为 {matrix.shape}"
        )
    if control_axis.size < minimum_points:
        raise ValueError(f"控制轴有效点数不足 {minimum_points}")
    if np.any(~np.isfinite(control_axis)) or np.any(np.diff(control_axis) <= 0):
        raise ValueError("控制频率轴必须有限且严格递增")
    if peak_margin_hz <= 0 or gamma_guess_hz <= 0:
        raise ValueError("拟合峰窗口和线宽初值必须大于 0")
    if fit_half_width_hz is not None and fit_half_width_hz <= 0:
        raise ValueError("拟合频带半宽必须大于 0")

    parameters = np.full((frequency_axis.size, 4), np.nan, dtype=float)
    uncertainties = np.full_like(parameters, np.nan)
    fit_mask = np.zeros(frequency_axis.size, dtype=bool)
    control_step_hz = float(np.median(np.diff(control_axis)))
    minimum_gamma_hz = 0.5 * control_step_hz

    for index, fixed_frequency in enumerate(frequency_axis):
        psd_slice = matrix[:, index]
        valid = np.isfinite(psd_slice) & np.isfinite(control_axis)
        fit_valid = valid.copy()
        if fit_half_width_hz is not None:
            fit_valid &= (
                np.abs(control_axis - fixed_frequency) <= fit_half_width_hz
            )
        if np.count_nonzero(fit_valid) < minimum_points:
            continue
        peak_valid = fit_valid & (
            np.abs(control_axis - fixed_frequency) <= peak_margin_hz
        )
        if not np.any(peak_valid):
            continue
        median_value = float(np.nanmedian(psd_slice[fit_valid]))
        std_value = float(np.nanstd(psd_slice[fit_valid]))
        maximum_value = float(np.nanmax(psd_slice[peak_valid]))
        if not np.isfinite(std_value) or maximum_value - median_value <= 2.0 * std_value:
            continue

        amplitude_guess = estimate_amplitude_guess(
            maximum_value - median_value,
            gamma_guess_hz,
            float(fixed_frequency),
        )
        fit_scale = 1.0
        if fit_half_width_hz is not None:
            fit_scale = max(
                float(np.nanmax(np.abs(psd_slice[fit_valid]))),
                np.finfo(float).tiny,
            )
        curve_fit_kwargs: dict[str, object] = {}
        gamma_start_hz = gamma_guess_hz
        if fit_half_width_hz is not None:
            gamma_start_hz = float(
                np.clip(
                    gamma_guess_hz,
                    1.1 * minimum_gamma_hz,
                    0.9 * fit_half_width_hz,
                )
            )
            curve_fit_kwargs["bounds"] = (
                [
                    minimum_gamma_hz,
                    0.0,
                    0.0,
                    -fit_half_width_hz,
                ],
                [
                    fit_half_width_hz,
                    np.inf,
                    np.inf,
                    fit_half_width_hz,
                ],
            )
            curve_fit_kwargs["x_scale"] = "jac"
        try:
            fitted, covariance = curve_fit(
                lambda axis, gamma, amplitude, baseline, offset: (
                    lorentzian_vs_control(
                        axis,
                        gamma,
                        amplitude,
                        baseline,
                        offset,
                        float(fixed_frequency),
                    )
                ),
                control_axis[fit_valid],
                psd_slice[fit_valid] / fit_scale,
                p0=[
                    gamma_start_hz,
                    amplitude_guess / fit_scale,
                    median_value / fit_scale,
                    0.0,
                ],
                maxfev=5000,
                **curve_fit_kwargs,
            )
        except (RuntimeError, ValueError, FloatingPointError):
            continue
        if fit_half_width_hz is not None and (
            abs(float(fitted[3])) >= 0.95 * fit_half_width_hz
            or float(fitted[0]) >= 0.95 * fit_half_width_hz
            or float(fitted[0]) <= 1.01 * minimum_gamma_hz
        ):
            continue
        if fit_scale != 1.0:
            fitted[1:3] *= fit_scale
            scaling = np.diag([1.0, fit_scale, fit_scale, 1.0])
            covariance = scaling @ covariance @ scaling
        parameters[index] = [
            abs(float(fitted[0])),
            abs(float(fitted[1])),
            float(fitted[2]),
            float(fitted[3]),
        ]
        uncertainties[index] = np.sqrt(np.diag(covariance))
        fit_mask[index] = True

    interpolated_mask = np.zeros(frequency_axis.size, dtype=bool)
    fit_count = int(np.count_nonzero(fit_mask))
    if 0 < fit_count < frequency_axis.size:
        lower = max(
            0.0,
            float(control_axis[0]) * (1.0 - fit_axis_margin_fraction),
        )
        upper = float(control_axis[-1]) * (1.0 + fit_axis_margin_fraction)
        near_ridge = (frequency_axis >= lower) & (frequency_axis <= upper)
        source_mask = fit_mask & near_ridge
        if np.count_nonzero(source_mask) > 2:
            source_indices = np.flatnonzero(source_mask)
            fill_indices = np.flatnonzero(~fit_mask & near_ridge)
            for parameter_index in range(parameters.shape[1]):
                interpolation = interp1d(
                    source_indices,
                    parameters[source_mask, parameter_index],
                    kind="linear",
                    fill_value="extrapolate",
                )
                parameters[fill_indices, parameter_index] = interpolation(fill_indices)
            interpolated_mask[fill_indices] = True

    return NoiseSeparationResult(
        parameters=parameters,
        uncertainties=uncertainties,
        fit_mask=fit_mask,
        interpolated_mask=interpolated_mask,
        s_beta=parameters[:, 1].copy(),
        n_s1=parameters[:, 2].copy(),
    )
