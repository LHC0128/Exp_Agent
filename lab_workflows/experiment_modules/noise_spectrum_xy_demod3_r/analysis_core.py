"""Demod3 平均 R 矩阵的纯离线分析函数。"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import curve_fit
from scipy.signal import find_peaks

from lab_workflows.experiment_modules.noise_spectrum_xy.calibration import (
    RobustLinearCalibration,
    fit_robust_linear_calibration,
)


@dataclass(frozen=True, slots=True)
class RMatrixAnalysis:
    """R 矩阵标定和逐列拟合结果。"""

    calibration: RobustLinearCalibration
    ridge_envelope_v: np.ndarray
    ridge_frequency_hz: np.ndarray
    calibrated_control_frequency_hz: np.ndarray
    fit_parameters: np.ndarray
    fit_errors: np.ndarray
    fit_mask: np.ndarray


def find_ridge_points(
    r_matrix_v: np.ndarray,
    envelope_v: np.ndarray,
    demod3_frequency_hz: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """逐 Demod3 频率列寻找沿控制包络轴的最显著 R 峰。"""
    matrix = np.asarray(r_matrix_v, dtype=float)
    envelope = np.asarray(envelope_v, dtype=float).reshape(-1)
    frequencies = np.asarray(demod3_frequency_hz, dtype=float).reshape(-1)
    if matrix.shape != (len(envelope), len(frequencies)):
        raise ValueError("R 矩阵形状与扫描轴不一致")

    ridge_envelope: list[float] = []
    ridge_frequency: list[float] = []
    for column_idx, frequency in enumerate(frequencies):
        column = matrix[:, column_idx]
        finite = np.isfinite(column) & np.isfinite(envelope)
        if np.count_nonzero(finite) < 6:
            continue
        valid_indices = np.flatnonzero(finite)
        valid_values = column[finite]
        peaks, props = find_peaks(valid_values, prominence=0)
        if len(peaks):
            selected = int(peaks[int(np.argmax(props["prominences"]))])
        else:
            selected = int(np.argmax(valid_values))
        ridge_envelope.append(float(envelope[valid_indices[selected]]))
        ridge_frequency.append(float(frequency))
    return np.asarray(ridge_envelope), np.asarray(ridge_frequency)


def lorentzian_r_response(
    omega_ctrl_hz: np.ndarray,
    gamma_hz: float,
    fit_amplitude: float,
    r_baseline_v: float,
    frequency_offset_hz: float,
    fixed_frequency_hz: float,
) -> np.ndarray:
    """以平均 R 代替 PSD 幅值的洛伦兹响应模型。"""
    w = float(fixed_frequency_hz)
    shifted = np.asarray(omega_ctrl_hz, dtype=float) + frequency_offset_hz
    numerator = gamma_hz**2 + w**2
    denominator = (
        (gamma_hz**2 - w**2 + shifted**2) ** 2
        + 4.0 * w**2 * gamma_hz**2
    )
    return r_baseline_v + fit_amplitude * numerator / denominator


def _fit_profiles(
    r_matrix_v: np.ndarray,
    control_frequency_hz: np.ndarray,
    demod3_frequency_hz: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """在每个 Demod3 频率列上拟合 R 随控制频率的响应。"""
    matrix = np.asarray(r_matrix_v, dtype=float)
    control = np.asarray(control_frequency_hz, dtype=float)
    frequencies = np.asarray(demod3_frequency_hz, dtype=float)
    parameters = np.full((len(frequencies), 4), np.nan, dtype=float)
    errors = np.full_like(parameters, np.nan)
    fit_mask = np.zeros(len(frequencies), dtype=bool)
    scan_span = max(float(np.ptp(control)), 1.0)

    for idx, frequency in enumerate(frequencies):
        values = matrix[:, idx]
        finite = np.isfinite(values) & np.isfinite(control)
        if np.count_nonzero(finite) < 10:
            continue
        x_data = control[finite]
        y_data = values[finite]
        baseline = max(0.0, float(np.nanmedian(y_data)))
        height = float(np.nanmax(y_data) - baseline)
        if not np.isfinite(height) or height <= 0:
            continue
        gamma_guess = min(300.0, scan_span / 4.0)
        amplitude_guess = height * (
            gamma_guess**4 + 4.0 * gamma_guess**2 * frequency**2
        ) / max(gamma_guess**2 + frequency**2, np.finfo(float).tiny)
        try:
            popt, covariance = curve_fit(
                lambda x, gamma, amplitude, base, offset: lorentzian_r_response(
                    x, gamma, amplitude, base, offset, frequency
                ),
                x_data,
                y_data,
                p0=[gamma_guess, max(amplitude_guess, 0.0), baseline, 0.0],
                bounds=(
                    [1e-9, 0.0, 0.0, -scan_span],
                    [scan_span * 2.0, np.inf, np.inf, scan_span],
                ),
                maxfev=10000,
            )
        except (RuntimeError, ValueError, FloatingPointError):
            continue
        parameters[idx] = popt
        diagonal = np.diag(covariance)
        errors[idx] = np.sqrt(np.maximum(diagonal, 0.0))
        fit_mask[idx] = True
    return parameters, errors, fit_mask


def analyze_r_matrix(
    r_matrix_v: np.ndarray,
    envelope_v: np.ndarray,
    demod3_frequency_hz: np.ndarray,
) -> RMatrixAnalysis:
    """完成 R 脊线标定和逐列洛伦兹拟合。"""
    ridge_envelope, ridge_frequency = find_ridge_points(
        r_matrix_v,
        envelope_v,
        demod3_frequency_hz,
    )
    calibration = fit_robust_linear_calibration(
        ridge_envelope,
        ridge_frequency,
        min_inliers=6,
    )
    calibrated_control = (
        calibration.slope_hz_per_v * np.asarray(envelope_v, dtype=float)
        + calibration.intercept_hz
    )
    parameters, errors, fit_mask = _fit_profiles(
        r_matrix_v,
        calibrated_control,
        demod3_frequency_hz,
    )
    return RMatrixAnalysis(
        calibration=calibration,
        ridge_envelope_v=ridge_envelope,
        ridge_frequency_hz=ridge_frequency,
        calibrated_control_frequency_hz=calibrated_control,
        fit_parameters=parameters,
        fit_errors=errors,
        fit_mask=fit_mask,
    )
