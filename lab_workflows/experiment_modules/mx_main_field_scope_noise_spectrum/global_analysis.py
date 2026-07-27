"""Mx 主场示波器噪声谱的二维全局分解。"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import signal as scipy_signal
from scipy.optimize import minimize


@dataclass(slots=True)
class GlobalNoiseSeparationResult:
    """二维全局响应、秩一背景和噪声谱分解结果。"""

    frequency_hz: np.ndarray
    control_frequency_hz: np.ndarray
    gamma_hz: float
    control_offset_hz: float
    n_s1: np.ndarray
    c_s_beta: np.ndarray
    peak_contribution: np.ndarray
    background_profile: np.ndarray
    model_matrix: np.ndarray
    response_matrix: np.ndarray
    residual_matrix: np.ndarray
    median_absolute_fractional_residual: np.ndarray
    cv_n_s1_relative_std: np.ndarray
    cv_c_s_beta_relative_std: np.ndarray
    cv_deviance: np.ndarray
    quantitative_valid_mask: np.ndarray
    whittle_deviance_mean: float
    cv_deviance_mean: float
    log_r_squared: float
    optimizer_success: bool
    optimizer_message: str


@dataclass(slots=True)
class GlobalNoiseAccuracyResult:
    """控制频率留出和背景模型敏感性给出的内部精度。"""

    fold_gamma_hz: np.ndarray
    fold_control_offset_hz: np.ndarray
    background_margin_hz: np.ndarray
    margin_gamma_hz: np.ndarray
    margin_control_offset_hz: np.ndarray
    n_s1_systematic_relative_std: np.ndarray
    c_s_beta_systematic_relative_std: np.ndarray
    n_s1_total_relative_uncertainty: np.ndarray
    c_s_beta_total_relative_uncertainty: np.ndarray
    quantitative_valid_mask: np.ndarray
    constant_background_gamma_hz: float
    constant_background_offset_hz: float
    constant_background_whittle_deviance_mean: float
    constant_background_log_r_squared: float


def lorentzian_response_matrix(
    control_frequency_hz: np.ndarray,
    frequency_hz: np.ndarray,
    gamma_hz: float,
    control_offset_hz: float,
) -> np.ndarray:
    """构造公式中的 ``L(omega, Omega_ctrl)`` 二维响应矩阵。"""
    control = np.asarray(control_frequency_hz, dtype=float).reshape(-1, 1)
    frequency = np.asarray(frequency_hz, dtype=float).reshape(1, -1)
    shifted_control = control + float(control_offset_hz)
    gamma_squared = float(gamma_hz) ** 2
    numerator = gamma_squared + frequency**2
    denominator = (
        (gamma_squared - frequency**2 + shifted_control**2) ** 2
        + 4.0 * frequency**2 * gamma_squared
    )
    return numerator / np.maximum(denominator, np.finfo(float).tiny)


def _solve_nonnegative_two_component(
    observed: np.ndarray,
    background: np.ndarray,
    response: np.ndarray,
    weights: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """逐列求解 ``background*N + response*B`` 的二变量 NNLS。"""
    x_background = np.asarray(background, dtype=float).reshape(-1, 1)
    x_response = np.asarray(response, dtype=float)
    weighted_background = weights * x_background
    weighted_response = weights * x_response
    aa = np.sum(weighted_background * x_background, axis=0)
    ab = np.sum(weighted_background * x_response, axis=0)
    bb = np.sum(weighted_response * x_response, axis=0)
    ay = np.sum(weighted_background * observed, axis=0)
    by = np.sum(weighted_response * observed, axis=0)
    yy = np.sum(weights * observed**2, axis=0)
    tiny = np.finfo(float).tiny
    determinant = np.maximum(aa * bb - ab**2, tiny)
    n_value = (ay * bb - ab * by) / determinant
    b_value = (aa * by - ab * ay) / determinant

    invalid = (
        ~np.isfinite(n_value)
        | ~np.isfinite(b_value)
        | (n_value < 0.0)
        | (b_value < 0.0)
    )
    if np.any(invalid):
        n_only = np.maximum(ay / np.maximum(aa, tiny), 0.0)
        b_only = np.maximum(by / np.maximum(bb, tiny), 0.0)
        n_only_error = yy - 2.0 * n_only * ay + n_only**2 * aa
        b_only_error = yy - 2.0 * b_only * by + b_only**2 * bb
        use_n_only = n_only_error <= b_only_error
        n_value[invalid] = np.where(use_n_only[invalid], n_only[invalid], 0.0)
        b_value[invalid] = np.where(use_n_only[invalid], 0.0, b_only[invalid])
    return n_value, b_value


def _fit_linear_components(
    psd_matrix: np.ndarray,
    background_profile: np.ndarray,
    response_matrix: np.ndarray,
    *,
    rows: np.ndarray | None = None,
    iterations: int = 6,
    huber_threshold: float = 2.5,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """以相对残差 IRLS 求非负背景和调制峰贡献。"""
    matrix = np.asarray(psd_matrix, dtype=float)
    background = np.asarray(background_profile, dtype=float)
    response = np.asarray(response_matrix, dtype=float)
    if rows is not None:
        matrix = matrix[rows]
        background = background[rows]
        response = response[rows]
    column_scale = np.maximum(
        np.nanmedian(matrix, axis=0), np.finfo(float).tiny
    )
    observed = matrix / column_scale
    response_peak = np.maximum(np.nanmax(response_matrix, axis=0), np.finfo(float).tiny)
    normalized_response = response / response_peak
    weights = np.ones_like(observed)
    n_scaled = np.ones(observed.shape[1], dtype=float)
    peak_scaled = np.zeros(observed.shape[1], dtype=float)
    for _ in range(iterations):
        n_scaled, peak_scaled = _solve_nonnegative_two_component(
            observed,
            background,
            normalized_response,
            weights,
        )
        model = (
            background[:, None] * n_scaled[None, :]
            + normalized_response * peak_scaled[None, :]
        )
        model = np.maximum(model, np.finfo(float).tiny)
        fractional_residual = (observed - model) / model
        absolute_residual = np.abs(fractional_residual)
        robust_weight = np.minimum(
            1.0,
            huber_threshold / np.maximum(absolute_residual, np.finfo(float).tiny),
        )
        weights = robust_weight / model**2
        weights /= np.maximum(np.mean(weights, axis=0, keepdims=True), np.finfo(float).tiny)
    n_s1 = n_scaled * column_scale
    peak_contribution = peak_scaled * column_scale
    c_s_beta = peak_contribution / response_peak
    return n_s1, c_s_beta, peak_contribution


def _whittle_deviance(observed: np.ndarray, model: np.ndarray) -> np.ndarray:
    """返回忽略常数后的逐点 Gamma/Whittle 偏差。"""
    ratio = np.asarray(observed, dtype=float) / np.maximum(
        np.asarray(model, dtype=float), np.finfo(float).tiny
    )
    ratio = np.clip(ratio, 1.0e-6, 1.0e6)
    return 2.0 * (ratio - np.log(ratio) - 1.0)


def _smooth_positive_profile(values: np.ndarray) -> np.ndarray:
    profile = np.asarray(values, dtype=float).copy()
    valid = np.isfinite(profile) & (profile > 0.0)
    indices = np.arange(profile.size, dtype=float)
    if np.count_nonzero(valid) < 3:
        return np.ones_like(profile)
    profile[~valid] = np.interp(indices[~valid], indices[valid], profile[valid])
    window = min(3, profile.size if profile.size % 2 else profile.size - 1)
    if window >= 5:
        profile = np.exp(
            scipy_signal.savgol_filter(
                np.log(np.maximum(profile, np.finfo(float).tiny)),
                window_length=window,
                polyorder=2,
                mode="interp",
            )
        )
    return np.clip(profile, 0.05, 100.0)


def _initial_background_profile(
    psd_matrix: np.ndarray,
    frequency_hz: np.ndarray,
    control_frequency_hz: np.ndarray,
    margin_hz: float,
) -> np.ndarray:
    off_ridge = (
        np.abs(
            control_frequency_hz[:, None] - frequency_hz[None, :]
        )
        >= margin_hz
    )
    column_baseline = np.nanmedian(np.where(off_ridge, psd_matrix, np.nan), axis=0)
    ratio = psd_matrix / np.maximum(column_baseline[None, :], np.finfo(float).tiny)
    profile = np.nanmedian(np.where(off_ridge, ratio, np.nan), axis=1)
    return _normalize_background_profile(
        _smooth_positive_profile(profile), control_frequency_hz
    )


def _normalize_background_profile(
    profile: np.ndarray, control_frequency_hz: np.ndarray
) -> np.ndarray:
    high_control = control_frequency_hz >= np.quantile(control_frequency_hz, 0.6)
    normalization = float(np.median(profile[high_control]))
    return np.asarray(profile, dtype=float) / max(normalization, np.finfo(float).tiny)


def _update_background_profile(
    psd_matrix: np.ndarray,
    frequency_hz: np.ndarray,
    control_frequency_hz: np.ndarray,
    response_matrix: np.ndarray,
    n_s1: np.ndarray,
    c_s_beta: np.ndarray,
    old_profile: np.ndarray,
    margin_hz: float,
    control_offset_hz: float,
) -> np.ndarray:
    resonance = response_matrix * c_s_beta[None, :]
    background_observed = psd_matrix - resonance
    off_ridge = (
        np.abs(
            control_frequency_hz[:, None]
            + control_offset_hz
            - frequency_hz[None, :]
        )
        >= margin_hz
    )
    ratio = background_observed / np.maximum(n_s1[None, :], np.finfo(float).tiny)
    valid = off_ridge & np.isfinite(ratio) & (ratio > 0.0)
    candidate = np.nanmedian(np.where(valid, ratio, np.nan), axis=1)
    candidate = _normalize_background_profile(
        _smooth_positive_profile(candidate), control_frequency_hz
    )
    damped = np.exp(0.5 * np.log(old_profile) + 0.5 * np.log(candidate))
    return _normalize_background_profile(damped, control_frequency_hz)


def _optimize_global_response(
    psd_matrix: np.ndarray,
    frequency_hz: np.ndarray,
    control_frequency_hz: np.ndarray,
    background_profile: np.ndarray,
    gamma_guess_hz: float,
) -> tuple[float, float, bool, str]:
    control_step = float(np.median(np.diff(control_frequency_hz)))
    gamma_min = max(0.5 * control_step, 10.0)
    gamma_max = min(
        5000.0,
        0.25 * float(control_frequency_hz[-1] - control_frequency_hz[0]),
    )
    offset_bound = min(2500.0, 0.1 * float(control_frequency_hz[-1]))

    def objective(values: np.ndarray) -> float:
        gamma = float(np.exp(values[0]))
        offset = float(values[1] * control_step)
        response = lorentzian_response_matrix(
            control_frequency_hz, frequency_hz, gamma, offset
        )
        n_s1, c_s_beta, _ = _fit_linear_components(
            psd_matrix,
            background_profile,
            response,
            iterations=4,
        )
        model = (
            background_profile[:, None] * n_s1[None, :]
            + response * c_s_beta[None, :]
        )
        deviance = _whittle_deviance(psd_matrix, model)
        return float(np.mean(np.minimum(deviance, 25.0)))

    starts = {
        float(np.clip(gamma_guess_hz, gamma_min, gamma_max)),
        float(np.clip(650.0, gamma_min, gamma_max)),
        float(np.clip(1000.0, gamma_min, gamma_max)),
    }
    candidates = []
    for start in starts:
        optimized = minimize(
            objective,
            x0=np.asarray([np.log(start), 0.0]),
            method="L-BFGS-B",
            bounds=(
                (np.log(gamma_min), np.log(gamma_max)),
                (-offset_bound / control_step, offset_bound / control_step),
            ),
            options={
                "maxiter": 80,
                "ftol": 1.0e-9,
                "eps": np.asarray([1.0e-4, 1.0e-3]),
            },
        )
        candidates.append(optimized)
    best = min(candidates, key=lambda item: float(item.fun))
    return (
        float(np.exp(best.x[0])),
        float(best.x[1] * control_step),
        bool(best.success),
        str(best.message),
    )


def _cross_validate(
    psd_matrix: np.ndarray,
    background_profile: np.ndarray,
    response_matrix: np.ndarray,
    folds: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    n_values = []
    a_values = []
    heldout_deviance_sum = np.zeros(psd_matrix.shape[1], dtype=float)
    heldout_count = np.zeros(psd_matrix.shape[1], dtype=int)
    row_indices = np.arange(psd_matrix.shape[0])
    for fold in range(folds):
        test_rows = row_indices % folds == fold
        train_rows = ~test_rows
        n_s1, c_s_beta, _ = _fit_linear_components(
            psd_matrix,
            background_profile,
            response_matrix,
            rows=train_rows,
        )
        n_values.append(n_s1)
        a_values.append(c_s_beta)
        prediction = (
            background_profile[test_rows, None] * n_s1[None, :]
            + response_matrix[test_rows] * c_s_beta[None, :]
        )
        deviance = _whittle_deviance(psd_matrix[test_rows], prediction)
        heldout_deviance_sum += np.sum(np.minimum(deviance, 25.0), axis=0)
        heldout_count += deviance.shape[0]
    n_matrix = np.asarray(n_values)
    a_matrix = np.asarray(a_values)
    n_relative_std = np.std(n_matrix, axis=0, ddof=1) / np.maximum(
        np.mean(n_matrix, axis=0), np.finfo(float).tiny
    )
    a_relative_std = np.std(a_matrix, axis=0, ddof=1) / np.maximum(
        np.mean(a_matrix, axis=0), np.finfo(float).tiny
    )
    cv_deviance = heldout_deviance_sum / np.maximum(heldout_count, 1)
    return (
        n_relative_std,
        a_relative_std,
        cv_deviance,
        float(np.mean(cv_deviance)),
    )


def fit_global_noise_separation(
    psd_matrix: np.ndarray,
    frequency_hz: np.ndarray,
    control_frequency_hz: np.ndarray,
    *,
    gamma_guess_hz: float = 650.0,
    background_margin_hz: float = 5000.0,
    cv_folds: int = 5,
    alternating_iterations: int = 4,
) -> GlobalNoiseSeparationResult:
    """拟合共同响应参数、秩一控制背景及两条非负噪声谱。"""
    matrix = np.asarray(psd_matrix, dtype=float)
    frequency = np.asarray(frequency_hz, dtype=float).reshape(-1)
    control = np.asarray(control_frequency_hz, dtype=float).reshape(-1)
    if matrix.shape != (control.size, frequency.size):
        raise ValueError("PSD 矩阵形状必须为 (控制频率点数, PSD 频率点数)")
    if control.size < 20 or frequency.size < 20:
        raise ValueError("二维全局拟合至少需要 20 个控制频率点和 20 个 PSD 频率点")
    if np.any(~np.isfinite(matrix)) or np.any(matrix <= 0.0):
        raise ValueError("二维全局拟合要求 PSD 矩阵全部有限且大于 0")
    if np.any(np.diff(control) <= 0.0) or np.any(np.diff(frequency) <= 0.0):
        raise ValueError("控制频率轴和 PSD 频率轴必须严格递增")
    if background_margin_hz <= 0.0:
        raise ValueError("背景脊线排除半宽必须大于 0")
    if cv_folds != 0 and (cv_folds < 2 or cv_folds > control.size // 4):
        raise ValueError("交叉验证折数必须至少为 2，且每折至少保留 4 个控制频率点")

    background = _initial_background_profile(
        matrix, frequency, control, background_margin_hz
    )
    gamma = float(gamma_guess_hz)
    offset = 0.0
    optimizer_success = False
    optimizer_message = ""
    for _ in range(alternating_iterations):
        gamma, offset, optimizer_success, optimizer_message = (
            _optimize_global_response(
                matrix,
                frequency,
                control,
                background,
                gamma,
            )
        )
        response = lorentzian_response_matrix(control, frequency, gamma, offset)
        n_s1, c_s_beta, _ = _fit_linear_components(
            matrix, background, response
        )
        background = _update_background_profile(
            matrix,
            frequency,
            control,
            response,
            n_s1,
            c_s_beta,
            background,
            background_margin_hz,
            offset,
        )

    response = lorentzian_response_matrix(control, frequency, gamma, offset)
    n_s1, c_s_beta, peak_contribution = _fit_linear_components(
        matrix, background, response
    )
    model = (
        background[:, None] * n_s1[None, :]
        + response * c_s_beta[None, :]
    )
    residual = matrix - model
    fractional_residual = residual / np.maximum(model, np.finfo(float).tiny)
    median_fractional_residual = np.median(np.abs(fractional_residual), axis=0)
    deviance = _whittle_deviance(matrix, model)
    if cv_folds:
        cv_n, cv_a, cv_deviance, cv_deviance_mean = _cross_validate(
            matrix,
            background,
            response,
            cv_folds,
        )
    else:
        cv_n = np.full(frequency.size, np.nan)
        cv_a = np.full(frequency.size, np.nan)
        cv_deviance = np.full(frequency.size, np.nan)
        cv_deviance_mean = float("nan")
    control_step = float(np.median(np.diff(control)))
    ridge_covered = (
        (frequency >= control[0] + max(gamma, control_step))
        & (frequency <= control[-1] - max(gamma, control_step))
    )
    quantitative_valid = (
        ridge_covered
        & np.isfinite(n_s1)
        & np.isfinite(c_s_beta)
        & (n_s1 > 0.0)
        & (c_s_beta > 0.0)
        & ((cv_n <= 0.15) if cv_folds else True)
        & ((cv_a <= 0.25) if cv_folds else True)
        & (median_fractional_residual <= 0.35)
        & ((cv_deviance <= 1.0) if cv_folds else True)
    )
    log_observed = np.log(matrix)
    log_model = np.log(np.maximum(model, np.finfo(float).tiny))
    total_log_variation = float(np.sum((log_observed - np.mean(log_observed)) ** 2))
    log_r_squared = (
        1.0 - float(np.sum((log_observed - log_model) ** 2)) / total_log_variation
        if total_log_variation > 0.0
        else float("nan")
    )
    return GlobalNoiseSeparationResult(
        frequency_hz=frequency,
        control_frequency_hz=control,
        gamma_hz=gamma,
        control_offset_hz=offset,
        n_s1=n_s1,
        c_s_beta=c_s_beta,
        peak_contribution=peak_contribution,
        background_profile=background,
        model_matrix=model,
        response_matrix=response,
        residual_matrix=residual,
        median_absolute_fractional_residual=median_fractional_residual,
        cv_n_s1_relative_std=cv_n,
        cv_c_s_beta_relative_std=cv_a,
        cv_deviance=cv_deviance,
        quantitative_valid_mask=quantitative_valid,
        whittle_deviance_mean=float(np.mean(deviance)),
        cv_deviance_mean=cv_deviance_mean,
        log_r_squared=log_r_squared,
        optimizer_success=optimizer_success,
        optimizer_message=optimizer_message,
    )


def project_global_noise_separation(
    psd_matrix: np.ndarray,
    frequency_hz: np.ndarray,
    control_frequency_hz: np.ndarray,
    reference_result: GlobalNoiseSeparationResult,
    *,
    cv_folds: int = 5,
) -> GlobalNoiseSeparationResult:
    """固定核心频段求得的全局量，向更宽频段投影两条噪声谱。"""
    matrix = np.asarray(psd_matrix, dtype=float)
    frequency = np.asarray(frequency_hz, dtype=float).reshape(-1)
    control = np.asarray(control_frequency_hz, dtype=float).reshape(-1)
    if matrix.shape != (control.size, frequency.size):
        raise ValueError("PSD 矩阵形状必须为 (控制频率点数, PSD 频率点数)")
    if np.any(~np.isfinite(matrix)) or np.any(matrix <= 0.0):
        raise ValueError("二维全局投影要求 PSD 矩阵全部有限且大于 0")
    if np.any(np.diff(control) <= 0.0) or np.any(np.diff(frequency) <= 0.0):
        raise ValueError("控制频率轴和 PSD 频率轴必须严格递增")
    if (
        reference_result.control_frequency_hz.shape != control.shape
        or not np.allclose(reference_result.control_frequency_hz, control)
    ):
        raise ValueError("投影与核心拟合必须使用相同的控制频率轴")
    if cv_folds != 0 and (cv_folds < 2 or cv_folds > control.size // 4):
        raise ValueError("交叉验证折数必须至少为 2，且每折至少保留 4 个控制频率点")

    background = np.asarray(reference_result.background_profile, dtype=float)
    response = lorentzian_response_matrix(
        control,
        frequency,
        reference_result.gamma_hz,
        reference_result.control_offset_hz,
    )
    n_s1, c_s_beta, peak_contribution = _fit_linear_components(
        matrix, background, response
    )
    model = background[:, None] * n_s1[None, :] + response * c_s_beta[None, :]
    residual = matrix - model
    fractional_residual = residual / np.maximum(model, np.finfo(float).tiny)
    median_fractional_residual = np.median(np.abs(fractional_residual), axis=0)
    deviance = _whittle_deviance(matrix, model)
    if cv_folds:
        cv_n, cv_a, cv_deviance, cv_deviance_mean = _cross_validate(
            matrix, background, response, cv_folds
        )
    else:
        cv_n = np.full(frequency.size, np.nan)
        cv_a = np.full(frequency.size, np.nan)
        cv_deviance = np.full(frequency.size, np.nan)
        cv_deviance_mean = float("nan")
    control_step = float(np.median(np.diff(control)))
    ridge_covered = (
        frequency >= control[0] + max(reference_result.gamma_hz, control_step)
    ) & (
        frequency <= control[-1] - max(reference_result.gamma_hz, control_step)
    )
    quantitative_valid = (
        ridge_covered
        & np.isfinite(n_s1)
        & np.isfinite(c_s_beta)
        & (n_s1 > 0.0)
        & (c_s_beta > 0.0)
        & ((cv_n <= 0.15) if cv_folds else True)
        & ((cv_a <= 0.25) if cv_folds else True)
        & (median_fractional_residual <= 0.35)
        & ((cv_deviance <= 1.0) if cv_folds else True)
    )
    log_observed = np.log(matrix)
    log_model = np.log(np.maximum(model, np.finfo(float).tiny))
    total_log_variation = float(np.sum((log_observed - np.mean(log_observed)) ** 2))
    log_r_squared = (
        1.0 - float(np.sum((log_observed - log_model) ** 2)) / total_log_variation
        if total_log_variation > 0.0
        else float("nan")
    )
    return GlobalNoiseSeparationResult(
        frequency_hz=frequency,
        control_frequency_hz=control,
        gamma_hz=reference_result.gamma_hz,
        control_offset_hz=reference_result.control_offset_hz,
        n_s1=n_s1,
        c_s_beta=c_s_beta,
        peak_contribution=peak_contribution,
        background_profile=background,
        model_matrix=model,
        response_matrix=response,
        residual_matrix=residual,
        median_absolute_fractional_residual=median_fractional_residual,
        cv_n_s1_relative_std=cv_n,
        cv_c_s_beta_relative_std=cv_a,
        cv_deviance=cv_deviance,
        quantitative_valid_mask=quantitative_valid,
        whittle_deviance_mean=float(np.mean(deviance)),
        cv_deviance_mean=cv_deviance_mean,
        log_r_squared=log_r_squared,
        optimizer_success=reference_result.optimizer_success,
        optimizer_message=reference_result.optimizer_message,
    )


def assess_global_noise_accuracy(
    psd_matrix: np.ndarray,
    frequency_hz: np.ndarray,
    control_frequency_hz: np.ndarray,
    central_result: GlobalNoiseSeparationResult,
    *,
    background_margin_hz: float,
    cv_folds: int = 5,
    projection_psd_matrix: np.ndarray | None = None,
    projection_frequency_hz: np.ndarray | None = None,
    projection_result: GlobalNoiseSeparationResult | None = None,
) -> GlobalNoiseAccuracyResult:
    """评估全局参数留出稳定性与背景脊线掩码系统误差。"""
    matrix = np.asarray(psd_matrix, dtype=float)
    frequency = np.asarray(frequency_hz, dtype=float)
    control = np.asarray(control_frequency_hz, dtype=float)
    projection_items = (
        projection_psd_matrix,
        projection_frequency_hz,
        projection_result,
    )
    if any(item is not None for item in projection_items) and not all(
        item is not None for item in projection_items
    ):
        raise ValueError("投影精度评估必须同时提供 PSD、频率轴和中心投影结果")
    row_indices = np.arange(control.size)
    fold_gamma = []
    fold_offset = []
    for fold in range(cv_folds):
        train = row_indices % cv_folds != fold
        gamma, offset, _, _ = _optimize_global_response(
            matrix[train],
            frequency,
            control[train],
            central_result.background_profile[train],
            central_result.gamma_hz,
        )
        fold_gamma.append(gamma)
        fold_offset.append(offset)

    margins = np.asarray(
        [0.8 * background_margin_hz, background_margin_hz, 1.2 * background_margin_hz],
        dtype=float,
    )
    margin_results = []
    for margin in margins:
        if np.isclose(margin, background_margin_hz):
            margin_results.append(central_result)
        else:
            margin_results.append(
                fit_global_noise_separation(
                    matrix,
                    frequency,
                    control,
                    gamma_guess_hz=central_result.gamma_hz,
                    background_margin_hz=float(margin),
                    cv_folds=0,
                )
            )
    reported_result = projection_result or central_result
    reported_margin_results = margin_results
    if projection_result is not None:
        assert projection_psd_matrix is not None
        assert projection_frequency_hz is not None
        reported_margin_results = [
            project_global_noise_separation(
                projection_psd_matrix,
                projection_frequency_hz,
                control,
                item,
                cv_folds=0,
            )
            for item in margin_results
        ]
    n_stack = np.asarray([item.n_s1 for item in reported_margin_results])
    a_stack = np.asarray([item.c_s_beta for item in reported_margin_results])
    n_systematic = np.std(n_stack, axis=0, ddof=1) / np.maximum(
        np.mean(n_stack, axis=0), np.finfo(float).tiny
    )
    a_systematic = np.std(a_stack, axis=0, ddof=1) / np.maximum(
        np.mean(a_stack, axis=0), np.finfo(float).tiny
    )
    n_total = np.sqrt(reported_result.cv_n_s1_relative_std**2 + n_systematic**2)
    a_total = np.sqrt(reported_result.cv_c_s_beta_relative_std**2 + a_systematic**2)
    valid = (
        reported_result.quantitative_valid_mask
        & (n_total <= 0.15)
        & (a_total <= 0.25)
    )

    constant_background = np.ones(control.size, dtype=float)
    constant_gamma, constant_offset, _, _ = _optimize_global_response(
        matrix,
        frequency,
        control,
        constant_background,
        central_result.gamma_hz,
    )
    constant_response = lorentzian_response_matrix(
        control, frequency, constant_gamma, constant_offset
    )
    constant_n, constant_a, _ = _fit_linear_components(
        matrix, constant_background, constant_response
    )
    constant_model = (
        constant_background[:, None] * constant_n[None, :]
        + constant_response * constant_a[None, :]
    )
    constant_deviance = float(
        np.mean(_whittle_deviance(matrix, constant_model))
    )
    log_observed = np.log(matrix)
    log_constant = np.log(np.maximum(constant_model, np.finfo(float).tiny))
    log_variation = float(np.sum((log_observed - np.mean(log_observed)) ** 2))
    constant_log_r_squared = (
        1.0 - float(np.sum((log_observed - log_constant) ** 2)) / log_variation
        if log_variation > 0.0
        else float("nan")
    )
    return GlobalNoiseAccuracyResult(
        fold_gamma_hz=np.asarray(fold_gamma, dtype=float),
        fold_control_offset_hz=np.asarray(fold_offset, dtype=float),
        background_margin_hz=margins,
        margin_gamma_hz=np.asarray(
            [item.gamma_hz for item in margin_results], dtype=float
        ),
        margin_control_offset_hz=np.asarray(
            [item.control_offset_hz for item in margin_results], dtype=float
        ),
        n_s1_systematic_relative_std=n_systematic,
        c_s_beta_systematic_relative_std=a_systematic,
        n_s1_total_relative_uncertainty=n_total,
        c_s_beta_total_relative_uncertainty=a_total,
        quantitative_valid_mask=valid,
        constant_background_gamma_hz=constant_gamma,
        constant_background_offset_hz=constant_offset,
        constant_background_whittle_deviance_mean=constant_deviance,
        constant_background_log_r_squared=constant_log_r_squared,
    )
