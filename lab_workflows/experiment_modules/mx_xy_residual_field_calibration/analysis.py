"""Mx XY 剩磁二维校准离线分析。"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from ...experiment_runtime import runtime_run_dir
from ...plotting import (
    COLOR_ORANGE,
    COLOR_OPTIMAL,
    format_axis,
    new_figure,
    save_figure,
    style_legend,
)
from .models import MxXYResidualFieldCalibrationParams


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


def _load_config(run_dir: Path) -> tuple[MxXYResidualFieldCalibrationParams, dict[str, Any]]:
    config_path = run_dir / "experiment_config.yaml"
    if not config_path.is_file():
        raise FileNotFoundError(f"缺少实验配置: {config_path}")
    with config_path.open(encoding="utf-8") as stream:
        config = yaml.safe_load(stream) or {}
    params = MxXYResidualFieldCalibrationParams.from_external(
        config.get("parameters", {}),
        schema_version=int(config.get("schema_version", 1)),
    )
    return params, config


def _capture_summary(path: Path) -> dict[str, Any]:
    with np.load(path, allow_pickle=False) as raw:
        return {
            "path": path,
            "sequence_index": int(raw["sequence_index"]),
            "repeat_index": int(raw["repeat_index"]),
            "x_index": int(raw["x_index"]),
            "y_index": int(raw["y_index"]),
            "x_field_v": float(raw["x_field_v"]),
            "y_field_v": float(raw["y_field_v"]),
            "time_unix_s": float(raw["capture_midpoint_unix_s"]),
            "pd_mean_v": float(raw["pd_mean_v"]),
            "pd_std_v": float(raw["pd_std_v"]),
        }


def _load_captures(raw_dir: Path, pattern: str) -> list[dict[str, Any]]:
    captures = [_capture_summary(path) for path in raw_dir.glob(pattern)]
    captures.sort(key=lambda item: item["sequence_index"])
    if not captures:
        raise FileNotFoundError(f"没有找到原始数据: {raw_dir / pattern}")
    return captures


def _plot_map(
    results_dir: Path,
    x_axis: np.ndarray,
    y_axis: np.ndarray,
    matrix: np.ndarray,
    *,
    title: str,
    colorbar_label: str,
    filename: str,
    best_xy: tuple[float, float] | None = None,
    symmetric: bool = False,
) -> str:
    fig, axis = new_figure(kind="square")
    kwargs: dict[str, Any] = {"shading": "auto"}
    if symmetric:
        bound = float(np.max(np.abs(matrix)))
        if bound > 0.0:
            kwargs.update(cmap="coolwarm", vmin=-bound, vmax=bound)
    image = axis.pcolormesh(x_axis, y_axis, matrix.T, **kwargs)
    if best_xy is not None:
        axis.plot(
            best_xy[0],
            best_xy[1],
            marker="x",
            markersize=8,
            markeredgewidth=1.5,
            color=COLOR_OPTIMAL,
        )
    format_axis(axis, xlabel="X field voltage (V)", ylabel="Y field voltage (V)")
    axis.set_title(title)
    fig.colorbar(image, ax=axis, label=colorbar_label)
    save_figure(fig, results_dir / filename, bbox_inches="tight")
    return filename


def _plot_pd_surface(
    results_dir: Path,
    x_axis: np.ndarray,
    y_axis: np.ndarray,
    mean_matrix: np.ndarray,
    *,
    best_xy: tuple[float, float],
    best_pd_mean_v: float,
) -> str:
    """绘制原始 PD 均值三维曲面。"""
    from matplotlib.lines import Line2D

    x_grid, y_grid = np.meshgrid(x_axis, y_axis, indexing="ij")
    fig, axis = new_figure(kind="wide", subplot_kw={"projection": "3d"})
    surface = axis.plot_surface(
        x_grid,
        y_grid,
        mean_matrix,
        cmap="viridis",
        linewidth=0.2,
        antialiased=True,
        alpha=0.9,
    )
    axis.scatter(
        [best_xy[0]],
        [best_xy[1]],
        [best_pd_mean_v],
        marker="x",
        s=38,
        linewidths=1.5,
        color=COLOR_OPTIMAL,
        depthshade=False,
    )
    axis.set_xlabel("X field voltage (V)")
    axis.set_ylabel("Y field voltage (V)")
    axis.set_zlabel("PD mean voltage (V)")
    axis.set_title("PD Mean Surface")
    axis.view_init(elev=26.0, azim=-135.0)
    axis.set_box_aspect((1.15, 1.0, 0.75))
    fig.colorbar(
        surface,
        ax=axis,
        pad=0.08,
        shrink=0.62,
        label="PD mean voltage (V)",
    )
    style_legend(
        axis,
        handles=[
            Line2D(
                [],
                [],
                color=COLOR_OPTIMAL,
                marker="x",
                linestyle="None",
                label="Best measured point",
            ),
        ],
        loc="upper left",
        fontsize=7.0,
    )
    filename = "pd_mean_surface.png"
    save_figure(fig, results_dir / filename)
    return filename


def _plot_bloch_2d_fit(
    results_dir: Path,
    x_axis: np.ndarray,
    y_axis: np.ndarray,
    measured_matrix: np.ndarray,
    fitted_matrix: np.ndarray,
    *,
    fitted_balance_xy: tuple[float, float],
    fitted_balance_signal_v: float,
) -> str:
    """并列绘制漂移修正测量面和二维 Bloch 拟合面。"""
    x_grid, y_grid = np.meshgrid(x_axis, y_axis, indexing="ij")
    color_min = float(min(np.min(measured_matrix), np.min(fitted_matrix)))
    color_max = float(max(np.max(measured_matrix), np.max(fitted_matrix)))
    fig, axes_grid = new_figure(
        figsize=(8.8, 4.0),
        ncols=2,
        subplot_kw={"projection": "3d"},
    )
    axes = np.asarray(axes_grid).reshape(-1)
    surfaces = []
    for axis, matrix, title in zip(
        axes,
        (measured_matrix, fitted_matrix),
        ("Measured Drift-Corrected Surface", "2D Bloch Fit (Bz Approx. 0)"),
        strict=True,
    ):
        surfaces.append(
            axis.plot_surface(
                x_grid,
                y_grid,
                matrix,
                cmap="viridis",
                vmin=color_min,
                vmax=color_max,
                linewidth=0.15,
                antialiased=True,
            )
        )
        axis.set_xlabel("X field voltage (V)", fontsize=7.0, labelpad=2.0)
        axis.set_ylabel("Y field voltage (V)", fontsize=7.0, labelpad=2.0)
        axis.set_title(title, fontsize=9.0, pad=5.0)
        axis.tick_params(axis="both", which="major", labelsize=6.5, pad=0.5)
        axis.view_init(elev=26.0, azim=-135.0)
        axis.set_box_aspect((1.1, 1.0, 0.72))
    axes[1].scatter(
        [fitted_balance_xy[0]],
        [fitted_balance_xy[1]],
        [fitted_balance_signal_v],
        marker="x",
        s=38,
        linewidths=1.5,
        color=COLOR_ORANGE,
        depthshade=False,
        label="Fitted balance",
    )
    axes[1].plot(
        [fitted_balance_xy[0], fitted_balance_xy[0]],
        [fitted_balance_xy[1], fitted_balance_xy[1]],
        [float(np.min(fitted_matrix)), float(np.max(fitted_matrix))],
        color=COLOR_ORANGE,
        linestyle="--",
        linewidth=1.0,
    )
    style_legend(axes[1], fontsize=6.5)
    fig.subplots_adjust(left=0.02, right=0.88, bottom=0.08, top=0.90, wspace=0.18)
    colorbar_axis = fig.add_axes([0.91, 0.25, 0.012, 0.50])
    colorbar = fig.colorbar(surfaces[-1], cax=colorbar_axis)
    colorbar.ax.tick_params(labelsize=6.5)
    colorbar.set_label("Corrected PD mean (V)", fontsize=7.0)
    filename = "bloch_2d_fit.png"
    save_figure(fig, results_dir / filename, bbox_inches="tight")
    return filename


def _plot_dispersive_fit_metrics(
    results_dir: Path,
    x_axis: np.ndarray,
    fits: list[DispersiveFitResult],
    *,
    best_x_index: int,
) -> str:
    """绘制每个实测 X 的色散幅度、线宽和中心斜率。"""
    amplitude = np.asarray([abs(item.amplitude_v) for item in fits], dtype=float)
    linewidth = np.asarray([item.linewidth_v for item in fits], dtype=float)
    slope = np.asarray([item.central_slope_v_per_v for item in fits], dtype=float)
    center = np.asarray([item.center_y_v for item in fits], dtype=float)
    valid = np.asarray([item.valid_for_selection for item in fits], dtype=bool)
    fig, axes_grid = new_figure(figsize=(7.0, 5.4), nrows=2, ncols=2)
    axes = np.asarray(axes_grid).reshape(-1)
    panels = (
        (amplitude, "Dispersive amplitude (V)", "Amplitude"),
        (linewidth, "Peak separation (V)", "Linewidth"),
        (slope, "Central slope (V/V)", "Central Slope"),
        (center, "Fitted Y center (V)", "Y Balance Center"),
    )
    for axis, (values, ylabel, title) in zip(axes, panels, strict=True):
        axis.plot(x_axis[valid], values[valid], "o-", color=COLOR_OPTIMAL)
        if np.any(~valid):
            axis.plot(x_axis[~valid], values[~valid], "x", color=COLOR_ORANGE, label="Rejected fit")
        axis.plot(
            x_axis[best_x_index],
            values[best_x_index],
            marker="*",
            markersize=8,
            color=COLOR_ORANGE,
            linestyle="None",
            label="Selected X",
        )
        format_axis(axis, xlabel="X field voltage (V)", ylabel=ylabel)
        axis.set_title(title)
    fig.subplots_adjust(wspace=0.35, hspace=0.45)
    style_legend(axes[-1], fontsize=7.0)
    filename = "dispersive_fit_metrics.png"
    save_figure(fig, results_dir / filename, bbox_inches="tight")
    return filename


def _plot_best_dispersive_fit(
    results_dir: Path,
    y_axis: np.ndarray,
    signal_v: np.ndarray,
    fit: DispersiveFitResult,
    *,
    best_x_v: float,
    best_measured_y_v: float,
) -> str:
    """绘制最佳实测 X 下的 Y 色散数据和拟合曲线。"""
    dense_y = np.linspace(float(y_axis[0]), float(y_axis[-1]), 800)
    fig, axis = new_figure(kind="wide")
    axis.plot(y_axis, signal_v, "o", label="Measured drift-corrected mean")
    axis.plot(
        dense_y,
        dispersive_line(
            dense_y,
            fit.offset_v,
            fit.amplitude_v,
            fit.center_y_v,
            fit.gamma_v,
        ),
        color=COLOR_OPTIMAL,
        label="Robust dispersive fit",
    )
    axis.axvline(
        fit.center_y_v,
        color=COLOR_ORANGE,
        linestyle="--",
        linewidth=1.0,
        label="Fitted Y center",
    )
    axis.plot(
        best_measured_y_v,
        dispersive_line(
            np.asarray([best_measured_y_v]),
            fit.offset_v,
            fit.amplitude_v,
            fit.center_y_v,
            fit.gamma_v,
        )[0],
        marker="x",
        markersize=7,
        markeredgewidth=1.3,
        color=COLOR_ORANGE,
        linestyle="None",
        label="Nearest measured Y",
    )
    format_axis(axis, xlabel="Y field voltage (V)", ylabel="Drift-corrected PD mean (V)")
    axis.set_title(f"Best Y Dispersive Fit at X = {best_x_v:.6g} V")
    style_legend(axis, loc="lower right", fontsize=7.0)
    filename = "best_y_dispersive_fit.png"
    save_figure(fig, results_dir / filename, bbox_inches="tight")
    return filename


def analyze(run_dir: Path) -> dict[str, Any]:
    run_dir = Path(run_dir).resolve()
    params, config = _load_config(run_dir)
    raw_dir = run_dir / "raw"
    results_dir = run_dir / "results"
    results_dir.mkdir(exist_ok=True)

    axes_path = raw_dir / "xy_residual_scan_axes.npz"
    if not axes_path.is_file():
        raise FileNotFoundError(f"缺少扫描轴: {axes_path}")
    with np.load(axes_path, allow_pickle=False) as axes:
        x_axis = np.asarray(axes["x_field_v"], dtype=float)
        y_axis = np.asarray(axes["y_field_v"], dtype=float)

    grid_captures = _load_captures(raw_dir, "grid_X*_Y*_R*.npz")

    grouped: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for capture in grid_captures:
        grouped[(capture["x_index"], capture["y_index"])].append(capture)

    shape = (x_axis.size, y_axis.size)
    mean_matrix = np.full(shape, np.nan, dtype=float)
    point_std_matrix = np.full(shape, np.nan, dtype=float)
    time_matrix = np.full(shape, np.nan, dtype=float)
    repeat_count_matrix = np.zeros(shape, dtype=int)
    rows: list[dict[str, Any]] = []
    for x_index in range(x_axis.size):
        for y_index in range(y_axis.size):
            captures = sorted(
                grouped.get((x_index, y_index), []),
                key=lambda item: item["repeat_index"],
            )
            if len(captures) != params.point_repeats:
                raise RuntimeError(
                    f"网格 X{x_index:03d}/Y{y_index:03d} 的重复数 "
                    f"{len(captures)} 与 POINT_REPEATS={params.point_repeats} 不一致"
                )
            point_time = float(np.mean([item["time_unix_s"] for item in captures]))
            point_mean = float(np.mean([item["pd_mean_v"] for item in captures]))
            point_repeat_std = (
                float(np.std([item["pd_mean_v"] for item in captures], ddof=1))
                if len(captures) > 1
                else 0.0
            )
            mean_matrix[x_index, y_index] = point_mean
            point_std_matrix[x_index, y_index] = point_repeat_std
            time_matrix[x_index, y_index] = point_time
            repeat_count_matrix[x_index, y_index] = len(captures)

    if not np.all(np.isfinite(mean_matrix)):
        raise RuntimeError("PD 均值矩阵包含无效值")
    bloch_fit = fit_bloch_2d(
        x_axis,
        y_axis,
        mean_matrix,
        time_matrix_unix_s=time_matrix,
    )
    if not bloch_fit.valid_for_selection:
        raise RuntimeError(f"二维 Bloch 拟合失败: {bloch_fit.message}")
    x_grid, y_grid = np.meshgrid(x_axis, y_axis, indexing="ij")
    bloch_fit_matrix = bloch_2d_surface(
        x_grid,
        y_grid,
        bloch_fit.offset_v,
        bloch_fit.amplitude_v,
        bloch_fit.balance_x_v,
        bloch_fit.balance_y_v,
        bloch_fit.width_x_v,
        bloch_fit.width_y_v,
    )
    fitted_drift_matrix = bloch_fit.linear_drift_v_per_s * (
        time_matrix - bloch_fit.time_origin_unix_s
    )
    drift_corrected_mean_matrix = mean_matrix - fitted_drift_matrix
    bloch_total_fit_matrix = bloch_fit_matrix + fitted_drift_matrix
    bloch_residual_matrix = mean_matrix - bloch_total_fit_matrix
    best_x_index = int(np.argmin(np.abs(x_axis - bloch_fit.balance_x_v)))
    best_y_index = int(np.argmin(np.abs(y_axis - bloch_fit.balance_y_v)))
    best_x_v = float(x_axis[best_x_index])
    best_y_v = float(y_axis[best_y_index])

    dispersive_fits = [
        fit_dispersive_line(y_axis, drift_corrected_mean_matrix[x_index, :])
        for x_index in range(x_axis.size)
    ]
    valid_fit_mask = np.asarray(
        [item.valid_for_selection for item in dispersive_fits], dtype=bool
    )
    fit_csv_name = "y_dispersive_fits.csv"
    fit_rows = []
    for x_index, fit in enumerate(dispersive_fits):
        fit_rows.append(
            {
                "x_index": x_index,
                "x_field_v": float(x_axis[x_index]),
                **asdict(fit),
            }
        )
    with (results_dir / fit_csv_name).open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(fit_rows[0]))
        writer.writeheader()
        writer.writerows(fit_rows)
    fit_slopes = np.asarray(
        [item.central_slope_v_per_v for item in dispersive_fits], dtype=float
    )
    fit_amplitudes = np.asarray(
        [abs(item.amplitude_v) for item in dispersive_fits], dtype=float
    )
    fit_linewidths = np.asarray(
        [item.linewidth_v for item in dispersive_fits], dtype=float
    )
    dispersive_best_x_index: int | None = None
    amplitude_best_x_index: int | None = None
    linewidth_best_x_index: int | None = None
    dispersive_best_fit: DispersiveFitResult | None = None
    dispersive_best_y_index: int | None = None
    metric_extrema_agree = False
    if np.any(valid_fit_mask):
        dispersive_best_x_index = int(
            np.argmax(np.where(valid_fit_mask, fit_slopes, -np.inf))
        )
        amplitude_best_x_index = int(
            np.argmax(np.where(valid_fit_mask, fit_amplitudes, -np.inf))
        )
        linewidth_best_x_index = int(
            np.argmin(np.where(valid_fit_mask, fit_linewidths, np.inf))
        )
        metric_extrema_agree = bool(
            dispersive_best_x_index
            == amplitude_best_x_index
            == linewidth_best_x_index
        )
        dispersive_best_fit = dispersive_fits[dispersive_best_x_index]
        dispersive_best_y_index = int(
            np.argmin(np.abs(y_axis - dispersive_best_fit.center_y_v))
        )

    for x_index in range(x_axis.size):
        for y_index in range(y_axis.size):
            rows.append(
                {
                    "x_index": x_index,
                    "y_index": y_index,
                    "x_field_v": float(x_axis[x_index]),
                    "y_field_v": float(y_axis[y_index]),
                    "capture_time_unix_s": float(time_matrix[x_index, y_index]),
                    "pd_mean_v": float(mean_matrix[x_index, y_index]),
                    "point_repeat_std_v": float(point_std_matrix[x_index, y_index]),
                    "fitted_linear_drift_v": float(
                        fitted_drift_matrix[x_index, y_index]
                    ),
                    "drift_corrected_pd_mean_v": float(
                        drift_corrected_mean_matrix[x_index, y_index]
                    ),
                    "bloch_fitted_pd_mean_v": float(
                        bloch_fit_matrix[x_index, y_index]
                    ),
                    "bloch_total_fitted_pd_mean_v": float(
                        bloch_total_fit_matrix[x_index, y_index]
                    ),
                    "bloch_residual_v": float(
                        bloch_residual_matrix[x_index, y_index]
                    ),
                    "repeat_count": int(repeat_count_matrix[x_index, y_index]),
                }
            )

    csv_name = "xy_residual_calibration.csv"
    with (results_dir / csv_name).open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    npz_name = "calibration_results.npz"
    np.savez(
        results_dir / npz_name,
        x_field_v=x_axis,
        y_field_v=y_axis,
        pd_mean_v=mean_matrix,
        point_repeat_std_v=point_std_matrix,
        acquisition_time_unix_s=time_matrix,
        fitted_linear_drift_v=fitted_drift_matrix,
        drift_corrected_pd_mean_v=drift_corrected_mean_matrix,
        bloch_fit_surface_v=bloch_fit_matrix,
        bloch_total_fit_v=bloch_total_fit_matrix,
        bloch_residual_v=bloch_residual_matrix,
        best_x_index=np.int64(best_x_index),
        best_y_index=np.int64(best_y_index),
        best_x_field_v=np.float64(best_x_v),
        best_y_field_v=np.float64(best_y_v),
        fitted_x_balance_v=np.float64(bloch_fit.balance_x_v),
        fitted_y_balance_v=np.float64(bloch_fit.balance_y_v),
        bloch_offset_v=np.float64(bloch_fit.offset_v),
        bloch_amplitude_v=np.float64(bloch_fit.amplitude_v),
        bloch_width_x_v=np.float64(bloch_fit.width_x_v),
        bloch_width_y_v=np.float64(bloch_fit.width_y_v),
        bloch_linear_drift_v_per_s=np.float64(bloch_fit.linear_drift_v_per_s),
        bloch_time_origin_unix_s=np.float64(bloch_fit.time_origin_unix_s),
        bloch_rmse_v=np.float64(bloch_fit.rmse_v),
        bloch_r_squared=np.float64(bloch_fit.r_squared),
        dispersive_fit_valid=valid_fit_mask,
        dispersive_fit_offset_v=np.asarray([item.offset_v for item in dispersive_fits]),
        dispersive_fit_amplitude_v=np.asarray([item.amplitude_v for item in dispersive_fits]),
        dispersive_fit_center_y_v=np.asarray([item.center_y_v for item in dispersive_fits]),
        dispersive_fit_gamma_v=np.asarray([item.gamma_v for item in dispersive_fits]),
        dispersive_fit_linewidth_v=fit_linewidths,
        dispersive_fit_central_slope_v_per_v=fit_slopes,
        dispersive_fit_rmse_v=np.asarray([item.rmse_v for item in dispersive_fits]),
        dispersive_fit_r_squared=np.asarray([item.r_squared for item in dispersive_fits]),
        dispersive_crosscheck_x_index=np.int64(
            -1 if dispersive_best_x_index is None else dispersive_best_x_index
        ),
        dispersive_crosscheck_y_balance_v=np.float64(
            np.nan
            if dispersive_best_fit is None
            else dispersive_best_fit.center_y_v
        ),
    )

    best_xy = (best_x_v, best_y_v)
    fitted_balance_xy = (bloch_fit.balance_x_v, bloch_fit.balance_y_v)
    plot_files = [
        _plot_map(results_dir, x_axis, y_axis, mean_matrix, title="PD Mean Voltage", colorbar_label="Mean voltage (V)", filename="pd_mean_voltage_map.png", best_xy=best_xy),
        _plot_map(results_dir, x_axis, y_axis, drift_corrected_mean_matrix, title="Drift-Corrected PD Mean", colorbar_label="Corrected mean voltage (V)", filename="pd_drift_corrected_map.png", best_xy=best_xy),
        _plot_pd_surface(
            results_dir,
            x_axis,
            y_axis,
            mean_matrix,
            best_xy=best_xy,
            best_pd_mean_v=float(mean_matrix[best_x_index, best_y_index]),
        ),
        _plot_bloch_2d_fit(
            results_dir,
            x_axis,
            y_axis,
            drift_corrected_mean_matrix,
            bloch_fit_matrix,
            fitted_balance_xy=fitted_balance_xy,
            fitted_balance_signal_v=bloch_fit.offset_v,
        ),
        _plot_map(
            results_dir,
            x_axis,
            y_axis,
            bloch_residual_matrix,
            title="2D Bloch Fit Residual",
            colorbar_label="Residual (V)",
            filename="bloch_2d_residual_map.png",
            best_xy=fitted_balance_xy,
            symmetric=True,
        ),
    ]
    if dispersive_best_x_index is not None and dispersive_best_fit is not None:
        assert dispersive_best_y_index is not None
        plot_files.extend(
            [
                _plot_dispersive_fit_metrics(
                    results_dir,
                    x_axis,
                    dispersive_fits,
                    best_x_index=dispersive_best_x_index,
                ),
                _plot_best_dispersive_fit(
                    results_dir,
                    y_axis,
                    drift_corrected_mean_matrix[dispersive_best_x_index, :],
                    dispersive_best_fit,
                    best_x_v=float(x_axis[dispersive_best_x_index]),
                    best_measured_y_v=float(y_axis[dispersive_best_y_index]),
                ),
            ]
        )

    fig, axis = new_figure(kind="wide")
    flat_times = time_matrix.reshape(-1)
    time_order = np.argsort(flat_times)
    elapsed_time_s = flat_times[time_order] - bloch_fit.time_origin_unix_s
    fitted_drift_v = fitted_drift_matrix.reshape(-1)[time_order]
    axis.plot(elapsed_time_s, fitted_drift_v, color=COLOR_OPTIMAL, label="Fitted linear drift")
    axis.axhline(0.0, color="0.5", linestyle="--", linewidth=0.8)
    format_axis(axis, xlabel="Time from scan midpoint (s)", ylabel="Fitted drift (V)")
    axis.set_title("Fitted Acquisition Drift", fontsize=9.0)
    axis.xaxis.label.set_size(8.0)
    axis.yaxis.label.set_size(8.0)
    axis.tick_params(axis="both", which="major", labelsize=7.0)
    style_legend(axis, fontsize=7.0)
    drift_plot = "fitted_time_drift.png"
    save_figure(fig, results_dir / drift_plot, bbox_inches="tight")
    plot_files.append(drift_plot)

    quality_warnings: list[str] = []
    x_grid_step = float(np.median(np.diff(x_axis)))
    y_grid_step = float(np.median(np.diff(y_axis)))
    if bloch_fit.r_squared < 0.8:
        quality_warnings.append("bloch_r_squared_below_0.8")
    if bloch_fit.balance_x_near_boundary:
        quality_warnings.append("fitted_x_balance_near_scan_boundary")
    if bloch_fit.balance_y_near_boundary:
        quality_warnings.append("fitted_y_balance_near_scan_boundary")
    if bloch_fit.width_x_near_boundary:
        quality_warnings.append("fitted_x_width_near_fit_boundary")
    if bloch_fit.width_y_near_boundary:
        quality_warnings.append("fitted_y_width_near_fit_boundary")
    dispersive_crosscheck_x_v: float | None = None
    dispersive_crosscheck_y_v: float | None = None
    dispersive_x_difference_v: float | None = None
    dispersive_y_difference_v: float | None = None
    if dispersive_best_x_index is None or dispersive_best_fit is None:
        quality_warnings.append("no_valid_per_x_dispersive_crosscheck")
    else:
        dispersive_crosscheck_x_v = float(x_axis[dispersive_best_x_index])
        dispersive_crosscheck_y_v = float(dispersive_best_fit.center_y_v)
        dispersive_x_difference_v = (
            dispersive_crosscheck_x_v - bloch_fit.balance_x_v
        )
        dispersive_y_difference_v = (
            dispersive_crosscheck_y_v - bloch_fit.balance_y_v
        )
        if abs(dispersive_x_difference_v) > x_grid_step:
            quality_warnings.append("bloch_and_dispersive_x_differ_by_more_than_one_step")
        if abs(dispersive_y_difference_v) > y_grid_step:
            quality_warnings.append("bloch_and_dispersive_y_differ_by_more_than_one_step")

    summary = {
        "experiment_id": config.get("experiment_id", "mx-xy-residual-field-calibration"),
        "criterion": "global 2D steady-state Bloch fit of raw PD mean",
        "model": "pd_mean = offset + linear_drift*(t-t0) + amplitude*v/(1+u^2+v^2)",
        "reference_strategy": "none",
        "bz_assumption": "approximately_zero",
        "grid_order": "x_outer_y_forward",
        "fitted_x_balance_v": bloch_fit.balance_x_v,
        "fitted_y_balance_v": bloch_fit.balance_y_v,
        "best_x_field_v": best_x_v,
        "best_y_field_v": best_y_v,
        "best_x_index": int(best_x_index),
        "best_y_index": int(best_y_index),
        "best_grid_definition": "nearest measured point to the fitted 2D Bloch balance",
        "best_pd_mean_v": float(mean_matrix[best_x_index, best_y_index]),
        "best_drift_corrected_pd_mean_v": float(
            drift_corrected_mean_matrix[best_x_index, best_y_index]
        ),
        "bloch_offset_v": bloch_fit.offset_v,
        "bloch_amplitude_v": bloch_fit.amplitude_v,
        "bloch_width_x_v": bloch_fit.width_x_v,
        "bloch_width_y_v": bloch_fit.width_y_v,
        "bloch_linear_drift_v_per_s": bloch_fit.linear_drift_v_per_s,
        "bloch_time_origin_unix_s": bloch_fit.time_origin_unix_s,
        "bloch_fitted_drift_over_scan_v": float(
            np.ptp(fitted_drift_matrix)
        ),
        "bloch_rmse_v": bloch_fit.rmse_v,
        "bloch_r_squared": bloch_fit.r_squared,
        "bloch_balance_x_near_boundary": bloch_fit.balance_x_near_boundary,
        "bloch_balance_y_near_boundary": bloch_fit.balance_y_near_boundary,
        "bloch_width_x_near_boundary": bloch_fit.width_x_near_boundary,
        "bloch_width_y_near_boundary": bloch_fit.width_y_near_boundary,
        "valid_dispersive_fit_count": int(np.count_nonzero(valid_fit_mask)),
        "dispersive_crosscheck_x_field_v": dispersive_crosscheck_x_v,
        "dispersive_crosscheck_y_balance_v": dispersive_crosscheck_y_v,
        "dispersive_crosscheck_x_difference_from_bloch_v": dispersive_x_difference_v,
        "dispersive_crosscheck_y_difference_from_bloch_v": dispersive_y_difference_v,
        "dispersive_amplitude_max_x_field_v": (
            None
            if amplitude_best_x_index is None
            else float(x_axis[amplitude_best_x_index])
        ),
        "dispersive_linewidth_min_x_field_v": (
            None
            if linewidth_best_x_index is None
            else float(x_axis[linewidth_best_x_index])
        ),
        "dispersive_central_slope_max_x_field_v": dispersive_crosscheck_x_v,
        "dispersive_metric_extrema_agree": metric_extrema_agree,
        "grid_shape": [int(x_axis.size), int(y_axis.size)],
        "quality_warnings": quality_warnings,
        "files": [npz_name, csv_name, fit_csv_name, *plot_files],
    }
    with (results_dir / "analysis.yaml").open("w", encoding="utf-8") as stream:
        yaml.safe_dump(summary, stream, allow_unicode=True, sort_keys=False)
    with (results_dir / "analysis.json").open("w", encoding="utf-8") as stream:
        json.dump(summary, stream, ensure_ascii=False, indent=2)
    print(
        "Mx XY 剩磁校准分析完成: "
        f"fitted X={bloch_fit.balance_x_v:.9g} V, "
        f"fitted Y={bloch_fit.balance_y_v:.9g} V, "
        f"R^2={bloch_fit.r_squared:.6g}"
    )
    return summary


def main() -> int:
    analyze(runtime_run_dir())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
