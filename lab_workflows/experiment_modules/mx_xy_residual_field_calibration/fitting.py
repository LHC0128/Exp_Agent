"""原二维 Bloch/线性漂移及逐 X 色散科学算法，保持原初值与质量规则。"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np


@dataclass(frozen=True, slots=True)
class DispersiveFitResult:
    """固定 X 下 Y 色散线的稳健拟合结果。"""

    success: bool
    valid_for_selection: bool
    offset_v: float
    amplitude_v: float
    center_y_v: float
    gamma_v: float
    linewidth_v: float
    central_slope_v_per_v: float
    rmse_v: float
    r_squared: float
    message: str


@dataclass(frozen=True, slots=True)
class Bloch2DFitResult:
    """Bz 近似为零时二维稳态 Bloch 模型的全局拟合结果。"""

    success: bool
    valid_for_selection: bool
    offset_v: float
    amplitude_v: float
    balance_x_v: float
    balance_y_v: float
    width_x_v: float
    width_y_v: float
    linear_drift_v_per_s: float
    time_origin_unix_s: float
    rmse_v: float
    r_squared: float
    balance_x_near_boundary: bool
    balance_y_near_boundary: bool
    width_x_near_boundary: bool
    width_y_near_boundary: bool
    message: str


def bloch_2d_surface(
    x_v: np.ndarray,
    y_v: np.ndarray,
    offset_v: float,
    amplitude_v: float,
    balance_x_v: float,
    balance_y_v: float,
    width_x_v: float,
    width_y_v: float,
) -> np.ndarray:
    """返回 Pump 沿 Z、Probe 测量 Px 且 Bz≈0 的稳态二维 Bloch 响应。"""
    normalized_x = (np.asarray(x_v, dtype=float) - float(balance_x_v)) / float(
        width_x_v
    )
    normalized_y = (np.asarray(y_v, dtype=float) - float(balance_y_v)) / float(
        width_y_v
    )
    return float(offset_v) + float(amplitude_v) * normalized_y / (
        1.0 + normalized_x**2 + normalized_y**2
    )


def fit_bloch_2d(
    x_axis: np.ndarray,
    y_axis: np.ndarray,
    signal_matrix: np.ndarray,
    *,
    time_matrix_unix_s: np.ndarray | None = None,
) -> Bloch2DFitResult:
    """用多初值稳健最小二乘拟合二维 Bloch 响应和可选线性漂移。"""
    from scipy.optimize import least_squares

    x_axis = np.asarray(x_axis, dtype=float).reshape(-1)
    y_axis = np.asarray(y_axis, dtype=float).reshape(-1)
    signal_matrix = np.asarray(signal_matrix, dtype=float)
    if x_axis.size < 3 or y_axis.size < 7:
        raise ValueError("二维 Bloch 拟合至少需要 3 个 X 点和 7 个 Y 点")
    if signal_matrix.shape != (x_axis.size, y_axis.size):
        raise ValueError("二维 Bloch 拟合矩阵形状与 X/Y 扫描轴不一致")
    if not np.all(np.isfinite(signal_matrix)):
        raise ValueError("二维 Bloch 拟合输入包含非有限值")
    if not np.all(np.diff(x_axis) > 0.0) or not np.all(np.diff(y_axis) > 0.0):
        raise ValueError("二维 Bloch 拟合要求 X/Y 扫描轴严格递增")

    drift_enabled = time_matrix_unix_s is not None
    if drift_enabled:
        time_matrix_unix_s = np.asarray(time_matrix_unix_s, dtype=float)
        if time_matrix_unix_s.shape != signal_matrix.shape:
            raise ValueError("采集时间矩阵形状与二维信号矩阵不一致")
        if not np.all(np.isfinite(time_matrix_unix_s)):
            raise ValueError("采集时间矩阵包含非有限值")
        time_origin_unix_s = float(np.mean(time_matrix_unix_s))
        centered_time_s = time_matrix_unix_s - time_origin_unix_s
        time_span_s = float(np.ptp(time_matrix_unix_s))
        if time_span_s <= 0.0:
            raise ValueError("启用线性漂移拟合时采集时间必须覆盖非零时间范围")
    else:
        time_origin_unix_s = 0.0
        centered_time_s = np.zeros_like(signal_matrix)
        time_span_s = 0.0

    x_grid, y_grid = np.meshgrid(x_axis, y_axis, indexing="ij")
    x_min, x_max = float(x_axis[0]), float(x_axis[-1])
    y_min, y_max = float(y_axis[0]), float(y_axis[-1])
    x_span, y_span = x_max - x_min, y_max - y_min
    x_step = float(np.median(np.diff(x_axis)))
    y_step = float(np.median(np.diff(y_axis)))
    signal_min = float(np.min(signal_matrix))
    signal_max = float(np.max(signal_matrix))
    signal_span = signal_max - signal_min
    if signal_span <= np.finfo(float).eps:
        return Bloch2DFitResult(
            success=False,
            valid_for_selection=False,
            offset_v=float(np.mean(signal_matrix)),
            amplitude_v=0.0,
            balance_x_v=float(x_axis[x_axis.size // 2]),
            balance_y_v=float(y_axis[y_axis.size // 2]),
            width_x_v=np.nan,
            width_y_v=np.nan,
            linear_drift_v_per_s=0.0,
            time_origin_unix_s=time_origin_unix_s,
            rmse_v=0.0,
            r_squared=np.nan,
            balance_x_near_boundary=False,
            balance_y_near_boundary=False,
            width_x_near_boundary=False,
            width_y_near_boundary=False,
            message="signal_is_constant",
        )

    width_x_min = max(0.5 * x_step, x_span * 1.0e-6)
    width_y_min = max(0.5 * y_step, y_span * 1.0e-6)
    width_x_max = 2.0 * x_span
    width_y_max = 2.0 * y_span
    offset_limit_low = signal_min - 2.0 * signal_span
    offset_limit_high = signal_max + 2.0 * signal_span
    amplitude_limit = max(20.0 * signal_span, 1.0e-12)
    lower = np.asarray(
        [
            offset_limit_low,
            -amplitude_limit,
            x_min,
            y_min,
            width_x_min,
            width_y_min,
        ]
    )
    upper = np.asarray(
        [
            offset_limit_high,
            amplitude_limit,
            x_max,
            y_max,
            width_x_max,
            width_y_max,
        ]
    )
    if drift_enabled:
        drift_limit = max(10.0 * signal_span / time_span_s, 1.0e-15)
        lower = np.append(lower, -drift_limit)
        upper = np.append(upper, drift_limit)
        centered_flat = centered_time_s.reshape(-1)
        signal_flat = signal_matrix.reshape(-1)
        drift_guess = float(
            np.dot(centered_flat, signal_flat - np.mean(signal_flat))
            / np.dot(centered_flat, centered_flat)
        )
        drift_guess = float(np.clip(drift_guess, -0.9 * drift_limit, 0.9 * drift_limit))
    else:
        drift_guess = 0.0

    row_peak_to_peak = np.ptp(signal_matrix, axis=1)
    strongest_row_index = int(np.argmax(row_peak_to_peak))
    strongest_row = signal_matrix[strongest_row_index]
    row_gradient = np.gradient(strongest_row, y_axis)
    gradient_y_index = int(np.argmax(np.abs(row_gradient)))
    edge_offset = float(
        np.mean(
            [
                np.mean(signal_matrix[:, 0]),
                np.mean(signal_matrix[:, -1]),
            ]
        )
    )
    zero_y_index = int(np.argmin(np.abs(strongest_row - edge_offset)))
    x_candidates = np.unique(
        np.clip(
            [
                x_axis[strongest_row_index],
                x_axis[x_axis.size // 2],
                x_axis[int(np.argmax(np.max(np.abs(signal_matrix - edge_offset), axis=1)))],
            ],
            x_min,
            x_max,
        )
    )
    y_candidates = np.unique(
        np.clip(
            [
                y_axis[gradient_y_index],
                y_axis[zero_y_index],
                y_axis[y_axis.size // 2],
            ],
            y_min,
            y_max,
        )
    )
    width_pairs = (
        (x_span / 6.0, y_span / 6.0),
        (x_span / 3.0, y_span / 3.0),
        (x_span / 2.0, y_span / 2.0),
        (x_span / 4.0, y_span / 2.0),
        (x_span / 2.0, y_span / 4.0),
    )
    f_scale = max(0.05 * signal_span, np.finfo(float).eps)
    best: tuple[float, Any] | None = None
    for balance_x_guess in x_candidates:
        for balance_y_guess in y_candidates:
            local_y_index = int(np.argmin(np.abs(y_axis - balance_y_guess)))
            amplitude_sign = 1.0 if row_gradient[local_y_index] >= 0.0 else -1.0
            for width_x_guess, width_y_guess in width_pairs:
                initial = np.asarray(
                    [
                        edge_offset,
                        amplitude_sign * signal_span,
                        balance_x_guess,
                        balance_y_guess,
                        np.clip(width_x_guess, width_x_min, width_x_max),
                        np.clip(width_y_guess, width_y_min, width_y_max),
                    ],
                    dtype=float,
                )
                if drift_enabled:
                    initial = np.append(initial, drift_guess)

                def residual_function(values: np.ndarray) -> np.ndarray:
                    fitted_values = bloch_2d_surface(x_grid, y_grid, *values[:6])
                    if drift_enabled:
                        fitted_values = fitted_values + values[6] * centered_time_s
                    return (fitted_values - signal_matrix).reshape(-1)

                fit = least_squares(
                    residual_function,
                    initial,
                    bounds=(lower, upper),
                    loss="soft_l1",
                    f_scale=f_scale,
                    x_scale="jac",
                    max_nfev=5000,
                )
                if best is None or float(fit.cost) < best[0]:
                    best = (float(fit.cost), fit)

    if best is None:
        raise RuntimeError("二维 Bloch 拟合没有执行任何有效初值")
    fit = best[1]
    spatial_values = tuple(map(float, fit.x[:6]))
    linear_drift_v_per_s = float(fit.x[6]) if drift_enabled else 0.0
    fitted = bloch_2d_surface(x_grid, y_grid, *spatial_values)
    fitted = fitted + linear_drift_v_per_s * centered_time_s
    residual = fitted - signal_matrix
    rmse = float(np.sqrt(np.mean(residual**2)))
    ss_res = float(np.sum(residual**2))
    ss_total = float(np.sum((signal_matrix - np.mean(signal_matrix)) ** 2))
    r_squared = 1.0 - ss_res / ss_total if ss_total > 0.0 else np.nan
    (
        offset_v,
        amplitude_v,
        balance_x_v,
        balance_y_v,
        width_x_v,
        width_y_v,
    ) = spatial_values
    balance_x_near_boundary = bool(
        balance_x_v <= x_min + 0.5 * x_step or balance_x_v >= x_max - 0.5 * x_step
    )
    balance_y_near_boundary = bool(
        balance_y_v <= y_min + 0.5 * y_step or balance_y_v >= y_max - 0.5 * y_step
    )
    width_x_near_boundary = bool(
        width_x_v <= 1.05 * width_x_min or width_x_v >= 0.95 * width_x_max
    )
    width_y_near_boundary = bool(
        width_y_v <= 1.05 * width_y_min or width_y_v >= 0.95 * width_y_max
    )
    valid_for_selection = bool(
        fit.success
        and np.all(np.isfinite(fit.x))
        and abs(amplitude_v) > np.finfo(float).eps
        and np.isfinite(r_squared)
    )
    return Bloch2DFitResult(
        success=bool(fit.success),
        valid_for_selection=valid_for_selection,
        offset_v=offset_v,
        amplitude_v=amplitude_v,
        balance_x_v=balance_x_v,
        balance_y_v=balance_y_v,
        width_x_v=width_x_v,
        width_y_v=width_y_v,
        linear_drift_v_per_s=linear_drift_v_per_s,
        time_origin_unix_s=time_origin_unix_s,
        rmse_v=rmse,
        r_squared=float(r_squared),
        balance_x_near_boundary=balance_x_near_boundary,
        balance_y_near_boundary=balance_y_near_boundary,
        width_x_near_boundary=width_x_near_boundary,
        width_y_near_boundary=width_y_near_boundary,
        message=str(fit.message),
    )


def dispersive_line(
    y_v: np.ndarray,
    offset_v: float,
    amplitude_v: float,
    center_y_v: float,
    gamma_v: float,
) -> np.ndarray:
    """返回幅度归一化的色散线，正负峰位于中心两侧 gamma 处。"""
    delta_v = np.asarray(y_v, dtype=float) - float(center_y_v)
    gamma_v = float(gamma_v)
    return float(offset_v) + float(amplitude_v) * (
        2.0 * gamma_v * delta_v / (delta_v**2 + gamma_v**2)
    )


def fit_dispersive_line(y_v: np.ndarray, signal_v: np.ndarray) -> DispersiveFitResult:
    """用多初值稳健最小二乘拟合单条 Y 色散线。"""
    from scipy.optimize import least_squares

    y_v = np.asarray(y_v, dtype=float).reshape(-1)
    signal_v = np.asarray(signal_v, dtype=float).reshape(-1)
    if y_v.size != signal_v.size or y_v.size < 7:
        raise ValueError("色散拟合至少需要 7 个一一对应的 Y 扫描点")
    if not np.all(np.isfinite(y_v)) or not np.all(np.isfinite(signal_v)):
        raise ValueError("色散拟合输入包含非有限值")
    if not np.all(np.diff(y_v) > 0.0):
        raise ValueError("色散拟合要求 Y 扫描轴严格递增")

    y_min = float(y_v[0])
    y_max = float(y_v[-1])
    y_span = y_max - y_min
    y_step = float(np.median(np.diff(y_v)))
    signal_min = float(np.min(signal_v))
    signal_max = float(np.max(signal_v))
    signal_span = signal_max - signal_min
    if signal_span <= np.finfo(float).eps:
        return DispersiveFitResult(
            False,
            False,
            float(np.mean(signal_v)),
            0.0,
            float(y_v[y_v.size // 2]),
            np.nan,
            np.nan,
            np.nan,
            0.0,
            np.nan,
            "signal_is_constant",
        )

    endpoint_offset = float(np.mean([signal_v[0], signal_v[-1]]))
    gradient = np.gradient(signal_v, y_v)
    gradient_index = int(np.argmax(np.abs(gradient)))
    center_candidates = np.unique(
        np.clip(
            [
                y_v[gradient_index],
                y_v[int(np.argmin(np.abs(signal_v - endpoint_offset)))],
                0.5 * (y_min + y_max),
            ],
            y_min,
            y_max,
        )
    )
    gamma_min = max(0.25 * y_step, y_span * 1.0e-6)
    gamma_max = 2.0 * y_span
    gamma_candidates = np.unique(
        np.clip([2.0 * y_step, y_span / 8.0, y_span / 4.0], gamma_min, gamma_max)
    )
    offset_lower = signal_min - 2.0 * signal_span
    offset_upper = signal_max + 2.0 * signal_span
    amplitude_limit = max(10.0 * signal_span, 1.0e-12)
    bounds = (
        np.asarray([offset_lower, -amplitude_limit, y_min, gamma_min]),
        np.asarray([offset_upper, amplitude_limit, y_max, gamma_max]),
    )
    f_scale = max(0.05 * signal_span, np.finfo(float).eps)
    best: tuple[float, Any] | None = None
    for center_guess in center_candidates:
        local_gradient = float(gradient[int(np.argmin(np.abs(y_v - center_guess)))])
        amplitude_sign = 1.0 if local_gradient >= 0.0 else -1.0
        for gamma_guess in gamma_candidates:
            initial = np.asarray(
                [
                    endpoint_offset,
                    amplitude_sign * 0.5 * signal_span,
                    center_guess,
                    gamma_guess,
                ],
                dtype=float,
            )
            fit = least_squares(
                lambda values: dispersive_line(y_v, *values) - signal_v,
                initial,
                bounds=bounds,
                loss="soft_l1",
                f_scale=f_scale,
                x_scale="jac",
                max_nfev=5000,
            )
            residual = dispersive_line(y_v, *fit.x) - signal_v
            rmse = float(np.sqrt(np.mean(residual**2)))
            if best is None or rmse < best[0]:
                best = (rmse, fit)

    if best is None:
        raise RuntimeError("色散拟合没有执行任何有效初值")
    rmse, fit = best
    offset_v, amplitude_v, center_y_v, gamma_v = map(float, fit.x)
    residual = dispersive_line(y_v, *fit.x) - signal_v
    ss_res = float(np.sum(residual**2))
    ss_total = float(np.sum((signal_v - np.mean(signal_v)) ** 2))
    r_squared = 1.0 - ss_res / ss_total if ss_total > 0.0 else np.nan
    boundary_margin = 0.5 * y_step
    valid_for_selection = bool(
        fit.success
        and np.all(np.isfinite(fit.x))
        and y_min + boundary_margin <= center_y_v <= y_max - boundary_margin
        and 0.5 * y_step <= gamma_v <= y_span
        and abs(amplitude_v) > np.finfo(float).eps
        and np.isfinite(r_squared)
        and r_squared >= 0.5
    )
    return DispersiveFitResult(
        success=bool(fit.success),
        valid_for_selection=valid_for_selection,
        offset_v=offset_v,
        amplitude_v=amplitude_v,
        center_y_v=center_y_v,
        gamma_v=gamma_v,
        linewidth_v=2.0 * gamma_v,
        central_slope_v_per_v=2.0 * abs(amplitude_v) / gamma_v,
        rmse_v=rmse,
        r_squared=float(r_squared),
        message=str(fit.message),
    )
