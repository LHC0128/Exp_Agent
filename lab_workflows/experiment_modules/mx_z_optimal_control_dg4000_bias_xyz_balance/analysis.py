"""DG4000 Z 偏置 XYZ 平衡场离线分析。"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import numpy as np
import yaml
from scipy.optimize import least_squares, minimize

from ...experiment_runtime import runtime_run_dir
from ...plotting import (
    COLOR_TRAD,
    PAPER_WIDE,
    format_axis,
    new_figure,
    save_figure,
    set_plot_style,
)


EXPERIMENT_ID = "mx-z-optimal-control-dg4000-bias-xyz-balance"
GAMMA_HZ_PER_NT = 7.0
FIT_MODEL = "complex steady-state Bloch surface with shared XY balance center"


def _builtin(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _builtin(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_builtin(item) for item in value]
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    return value


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"缺少分析输入: {path}")
    with path.open(encoding="utf-8") as stream:
        payload = yaml.safe_load(stream) or {}
    if not isinstance(payload, dict):
        raise TypeError(f"YAML 顶层必须是映射: {path}")
    return payload


def _load_scan(path: Path) -> dict[str, np.ndarray]:
    if not path.is_file():
        raise FileNotFoundError(f"缺少三维扫描数据: {path}")
    with np.load(path, allow_pickle=False) as data:
        required = {
            "x_field_v",
            "y_field_v",
            "z_bias_v",
            "r_mean_v",
            "r_std_v",
            "x_mean_v",
            "y_mean_v",
            "x_std_v",
            "y_std_v",
            "complex_std_v",
            "acquisition_order",
            "actual_rate_sa_s",
        }
        missing = sorted(required.difference(data.files))
        if missing:
            raise ValueError(f"三维扫描数据缺少字段: {', '.join(missing)}")
        return {key: np.asarray(data[key]) for key in data.files}


def _validate_scan(data: dict[str, np.ndarray]) -> None:
    x_axis = np.asarray(data["x_field_v"], dtype=float)
    y_axis = np.asarray(data["y_field_v"], dtype=float)
    z_axis = np.asarray(data["z_bias_v"], dtype=float)
    expected_shape = (z_axis.size, x_axis.size, y_axis.size)
    if min(expected_shape) < 1:
        raise ValueError("XYZ 扫描轴不能为空")
    for key in (
        "r_mean_v",
        "r_std_v",
        "x_mean_v",
        "y_mean_v",
        "x_std_v",
        "y_std_v",
        "complex_std_v",
        "acquisition_order",
    ):
        if np.asarray(data[key]).shape != expected_shape:
            raise ValueError(
                f"{key} 形状应为 {expected_shape}，实际为 "
                f"{np.asarray(data[key]).shape}"
            )
    for name, axis in (("X", x_axis), ("Y", y_axis), ("Z bias", z_axis)):
        if not np.all(np.isfinite(axis)):
            raise ValueError(f"{name} 扫描轴包含 NaN 或无穷值")
    for key in (
        "r_mean_v",
        "r_std_v",
        "x_mean_v",
        "y_mean_v",
        "x_std_v",
        "y_std_v",
        "complex_std_v",
    ):
        if not np.all(np.isfinite(data[key])):
            raise ValueError(f"{key} 包含 NaN 或无穷值")


def select_measured_minimum(
    data: dict[str, np.ndarray],
) -> tuple[int, int, int]:
    """按 R 最小、采集顺序最先选择规范 Z/X/Y 索引。"""
    r_mean = np.asarray(data["r_mean_v"], dtype=float)
    order = np.asarray(data["acquisition_order"], dtype=int)
    minimum = float(np.min(r_mean))
    candidates = np.argwhere(r_mean == minimum)
    return tuple(
        int(value)
        for value in min(
            candidates,
            key=lambda index: int(order[tuple(index)]),
        )
    )


def _complex_surface(
    parameters: np.ndarray,
    x: np.ndarray | float,
    y: np.ndarray | float,
) -> tuple[np.ndarray | float, np.ndarray | float]:
    """二维稳态 Bloch 型复响应面。

    参数依次为输出偏置、XY 平衡中心、两个方向的线宽和 2x2 增益矩阵。
    该模型用于拟合每个 Z 平面的稳态复响应，不把周期最优控制近似成严格的
    Floquet 解。
    """
    ox, oy, x0, y0, wx, wy, gxx, gxy, gyx, gyy = parameters
    u = (np.asarray(x) - x0) / wx
    v = (np.asarray(y) - y0) / wy
    denominator = 1.0 + u * u + v * v
    sx = ox + (gxx * u + gxy * v) / denominator
    sy = oy + (gyx * u + gyy * v) / denominator
    return sx, sy


def _fit_bloch_plane(
    x_axis: np.ndarray,
    y_axis: np.ndarray,
    x_response: np.ndarray,
    y_response: np.ndarray,
) -> dict[str, Any]:
    """对一个 Z 平面的 Demod X/Y 网格进行稳健复数拟合。"""
    x_axis = np.asarray(x_axis, dtype=float)
    y_axis = np.asarray(y_axis, dtype=float)
    sx = np.asarray(x_response, dtype=float)
    sy = np.asarray(y_response, dtype=float)
    expected_shape = (x_axis.size, y_axis.size)
    if sx.shape != expected_shape or sy.shape != expected_shape:
        raise ValueError(
            f"复数响应形状应为 {expected_shape}，实际为 {sx.shape}/{sy.shape}"
        )
    if x_axis.size < 2 or y_axis.size < 2:
        raise ValueError("Bloch 平面拟合至少需要两个 X 点和两个 Y 点")
    if not np.all(np.isfinite(sx)) or not np.all(np.isfinite(sy)):
        raise ValueError("Bloch 平面拟合输入包含 NaN 或无穷值")

    x_grid, y_grid = np.meshgrid(x_axis, y_axis, indexing="ij")
    response = np.hypot(sx, sy)
    min_index = np.unravel_index(int(np.argmin(response)), response.shape)
    x_span = max(float(x_axis[-1] - x_axis[0]), 1e-9)
    y_span = max(float(y_axis[-1] - y_axis[0]), 1e-9)
    x_step = max(float(np.median(np.diff(x_axis))), 1e-9)
    y_step = max(float(np.median(np.diff(y_axis))), 1e-9)
    component_span = max(
        float(max(np.ptp(sx), np.ptp(sy))),
        1e-9,
    )
    lower = np.asarray(
        [
            np.min(sx) - component_span,
            np.min(sy) - component_span,
            x_axis[0],
            y_axis[0],
            0.5 * x_step,
            0.5 * y_step,
            *([-20.0 * component_span] * 4),
        ],
        dtype=float,
    )
    upper = np.asarray(
        [
            np.max(sx) + component_span,
            np.max(sy) + component_span,
            x_axis[-1],
            y_axis[-1],
            2.0 * x_span,
            2.0 * y_span,
            *([20.0 * component_span] * 4),
        ],
        dtype=float,
    )
    initial_centers = (
        (float(x_axis[min_index[0]]), float(y_axis[min_index[1]])),
        (float(np.mean(x_axis)), float(np.mean(y_axis))),
        (
            float(np.clip(-0.004, x_axis[0], x_axis[-1])),
            float(np.clip(-0.0005, y_axis[0], y_axis[-1])),
        ),
    )
    scale = max(0.05 * component_span, 1e-9)
    best = None
    for x0, y0 in initial_centers:
        initial = np.asarray(
            [
                float(np.median(sx)),
                float(np.median(sy)),
                x0,
                y0,
                max(x_span / 3.0, x_step),
                max(y_span / 3.0, y_step),
                component_span,
                0.0,
                0.0,
                component_span,
            ],
            dtype=float,
        )

        def residual(parameters: np.ndarray) -> np.ndarray:
            fit_sx, fit_sy = _complex_surface(parameters, x_grid, y_grid)
            return np.concatenate(
                ((np.asarray(fit_sx) - sx).ravel(), (np.asarray(fit_sy) - sy).ravel())
            )

        fit = least_squares(
            residual,
            initial,
            bounds=(lower, upper),
            loss="soft_l1",
            f_scale=scale,
            x_scale="jac",
            max_nfev=8000,
        )
        if best is None or fit.cost < best.cost:
            best = fit
    if best is None:
        raise RuntimeError("复数响应拟合失败")

    parameters = np.asarray(best.x, dtype=float)
    fit_sx, fit_sy = _complex_surface(parameters, x_grid, y_grid)
    residual_matrix = np.hypot(np.asarray(fit_sx) - sx, np.asarray(fit_sy) - sy)
    ss_res = float(np.sum((np.asarray(fit_sx) - sx) ** 2 + (np.asarray(fit_sy) - sy) ** 2))
    ss_tot = float(np.sum((sx - np.mean(sx)) ** 2 + (sy - np.mean(sy)) ** 2))
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0.0 else np.nan

    def objective(point: np.ndarray) -> float:
        model_sx, model_sy = _complex_surface(parameters, point[0], point[1])
        return float(model_sx * model_sx + model_sy * model_sy)

    model_minimum = minimize(
        objective,
        np.asarray([parameters[2], parameters[3]], dtype=float),
        method="Nelder-Mead",
        options={"xatol": 1e-10, "fatol": 1e-14, "maxiter": 2000},
    )
    model_min_xy = np.asarray(model_minimum.x, dtype=float)
    model_min_xy[0] = np.clip(model_min_xy[0], x_axis[0], x_axis[-1])
    model_min_xy[1] = np.clip(model_min_xy[1], y_axis[0], y_axis[-1])
    model_min_sx, model_min_sy = _complex_surface(
        parameters, model_min_xy[0], model_min_xy[1]
    )
    model_min_index = (
        int(np.argmin(np.abs(x_axis - model_min_xy[0]))),
        int(np.argmin(np.abs(y_axis - model_min_xy[1]))),
    )
    ix0, iy0 = model_min_index
    local_r = response[max(0, ix0 - 1) : ix0 + 2, max(0, iy0 - 1) : iy0 + 2]
    return {
        "parameters": parameters,
        "balance_xy_v": [float(parameters[2]), float(parameters[3])],
        "model_min_xy_v": [float(model_min_xy[0]), float(model_min_xy[1])],
        "model_min_r_v": float(np.hypot(model_min_sx, model_min_sy)),
        "model_min_nearest_grid_xy_v": [
            float(x_axis[ix0]),
            float(y_axis[iy0]),
        ],
        "model_min_nearest_grid_r_v": float(response[model_min_index]),
        "model_min_local_3x3_median_r_v": float(np.median(local_r)),
        "nearest_grid_xy_v": [
            float(x_axis[min_index[0]]),
            float(y_axis[min_index[1]]),
        ],
        "measured_min_r_v": float(response[min_index]),
        "measured_min_indices": [int(min_index[0]), int(min_index[1])],
        "rmse_complex_v": float(np.sqrt(np.mean(residual_matrix**2))),
        "r_squared_complex": float(r_squared),
        "fit_success": bool(best.success),
        "fit_message": str(best.message),
        "residual_matrix_v": residual_matrix,
    }


def _fit_summary_result(
    data: dict[str, np.ndarray],
    config: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """拟合所有完整 Z 平面，并确定 XY 中心和静态 Z 零场。"""
    complete = np.all(np.isfinite(data["r_mean_v"]), axis=(1, 2))
    complete_indices = np.flatnonzero(complete)
    if complete_indices.size == 0:
        raise RuntimeError("没有完整 Z 平面可用于拟合")
    plane_results: list[dict[str, Any]] = []
    x_axis = np.asarray(data["x_field_v"], dtype=float)
    y_axis = np.asarray(data["y_field_v"], dtype=float)
    for z_index in complete_indices:
        fitted = _fit_bloch_plane(
            x_axis,
            y_axis,
            np.asarray(data["x_mean_v"])[z_index],
            np.asarray(data["y_mean_v"])[z_index],
        )
        fitted["z_index"] = int(z_index)
        fitted["z_bias_v"] = float(np.asarray(data["z_bias_v"])[z_index])
        fitted["z_bias_frequency_hz"] = float(
            np.asarray(data["z_bias_frequency_hz"])[z_index]
        )
        fitted["z_bias_absolute_bz_nt"] = float(
            np.asarray(data["z_bias_absolute_bz_nt"])[z_index]
        )
        plane_results.append(fitted)

    centers = np.asarray([item["balance_xy_v"] for item in plane_results], dtype=float)
    robust_center = np.median(centers, axis=0)
    calibration = config.get("z_control_calibration", {})
    slope = float(calibration.get("slope_hz_per_v", np.nan))
    intercept = float(calibration.get("intercept_hz", np.nan))
    z_axis = np.asarray(data["z_bias_v"], dtype=float)
    frequencies = np.asarray(data["z_bias_frequency_hz"], dtype=float)
    if not np.isfinite(slope) or not np.isfinite(intercept):
        if z_axis.size < 2 or not np.all(np.isfinite(frequencies)):
            slope = np.nan
            intercept = np.nan
        else:
            slope = float((frequencies[-1] - frequencies[0]) / (z_axis[-1] - z_axis[0]))
            intercept = float(np.nanmedian(frequencies - slope * z_axis))
    z_zero_v = float(-intercept / slope) if np.isfinite(slope) and slope != 0 else np.nan
    nearest_z_index = (
        int(np.argmin(np.abs(z_axis - z_zero_v))) if np.isfinite(z_zero_v) else None
    )
    fit_summary = {
        "model": FIT_MODEL,
        "plane_count": int(len(plane_results)),
        "complete_z_indices": [int(index) for index in complete_indices],
        "incomplete_z_indices": [
            int(index) for index in np.flatnonzero(~complete)
        ],
        "robust_xy_center_v": [float(value) for value in robust_center],
        "center_std_v": [float(value) for value in np.std(centers, axis=0)],
        "center_range_v": [
            float(value) for value in (np.max(centers, axis=0) - np.min(centers, axis=0))
        ],
        "median_r_squared": float(np.nanmedian([item["r_squared_complex"] for item in plane_results])),
        "min_r_squared": float(np.nanmin([item["r_squared_complex"] for item in plane_results])),
        "max_r_squared": float(np.nanmax([item["r_squared_complex"] for item in plane_results])),
        "median_complex_rmse_v": float(np.nanmedian([item["rmse_complex_v"] for item in plane_results])),
        "min_complex_rmse_v": float(np.nanmin([item["rmse_complex_v"] for item in plane_results])),
        "max_complex_rmse_v": float(np.nanmax([item["rmse_complex_v"] for item in plane_results])),
        "z_static_zero_from_calibration": {
            "z_bias_v": z_zero_v,
            "frequency_hz": 0.0,
            "absolute_bz_nt": 0.0,
            "slope_hz_per_v": slope,
            "intercept_hz": intercept,
            "nearest_measured_z_bias_v": (
                float(z_axis[nearest_z_index]) if nearest_z_index is not None else None
            ),
        },
        "planes": [],
    }
    for item in plane_results:
        fit_summary["planes"].append(
            {
                key: _builtin(value)
                for key, value in item.items()
                if key not in {"residual_matrix_v"}
            }
        )
    return fit_summary, plane_results


def _axis_edges(axis: np.ndarray) -> np.ndarray:
    axis = np.asarray(axis, dtype=float)
    if axis.size == 1:
        half_width = max(abs(float(axis[0])) * 0.05, 1e-6)
        return np.asarray([axis[0] - half_width, axis[0] + half_width])
    midpoint = (axis[:-1] + axis[1:]) / 2.0
    return np.concatenate(
        (
            [axis[0] - (midpoint[0] - axis[0])],
            midpoint,
            [axis[-1] + (axis[-1] - midpoint[-1])],
        )
    )


def _plot_planes(
    results_dir: Path,
    data: dict[str, np.ndarray],
    best_index: tuple[int, int, int],
    plane_results: list[dict[str, Any]] | None = None,
) -> str:
    set_plot_style("paper")
    x_axis = np.asarray(data["x_field_v"], dtype=float)
    y_axis = np.asarray(data["y_field_v"], dtype=float)
    z_axis = np.asarray(data["z_bias_v"], dtype=float)
    r_mean = np.asarray(data["r_mean_v"], dtype=float)
    columns = min(3, int(z_axis.size))
    rows = int(np.ceil(z_axis.size / columns))
    fig, axes = new_figure(
        figsize=(min(PAPER_WIDE[0], 2.35 * columns + 0.8), 2.55 * rows),
        nrows=rows,
        ncols=columns,
        squeeze=False,
        sharex=True,
        sharey=True,
        constrained_layout=True,
    )
    mesh = None
    x_edges = _axis_edges(x_axis)
    y_edges = _axis_edges(y_axis)
    for z_index, axis in enumerate(axes.flat[: z_axis.size]):
        mesh = axis.pcolormesh(
            x_edges,
            y_edges,
            r_mean[z_index].T,
            shading="flat",
            cmap="viridis",
            vmin=float(np.min(r_mean)),
            vmax=float(np.max(r_mean)), rasterized=True)
        if z_index == best_index[0]:
            axis.plot(
                x_axis[best_index[1]],
                y_axis[best_index[2]],
                marker="x",
                color="white",
                markersize=7,
                markeredgewidth=1.4,
            )
        if plane_results is not None:
            fitted = next(
                (item for item in plane_results if int(item["z_index"]) == z_index),
                None,
            )
            if fitted is not None:
                center = fitted["balance_xy_v"]
                axis.plot(
                    center[0],
                    center[1],
                    marker="o",
                    markerfacecolor="none",
                    markeredgecolor="white",
                    markersize=6,
                    markeredgewidth=1.1,
                )
        format_axis(axis, xlabel="X field setting (V)", ylabel="Y field setting (V)")
        axis.set_title(f"Z bias = {z_axis[z_index]:+.6g} V", fontsize=8)
    for axis in axes.flat[z_axis.size :]:
        axis.set_visible(False)
    assert mesh is not None
    colorbar = fig.colorbar(mesh, ax=axes.flat[: z_axis.size].tolist())
    colorbar.set_label("Mean R (V)")
    filename = "xyz_balance_slices.png"
    save_figure(fig, results_dir / filename)
    return filename


def _plot_components(
    results_dir: Path,
    data: dict[str, np.ndarray],
    best_index: tuple[int, int, int],
    fitted_center_v: np.ndarray | None = None,
) -> str:
    set_plot_style("paper")
    x_axis = np.asarray(data["x_field_v"], dtype=float)
    y_axis = np.asarray(data["y_field_v"], dtype=float)
    z_axis = np.asarray(data["z_bias_v"], dtype=float)
    z_index, x_index, y_index = best_index
    fig, axes = new_figure(nrows=2, ncols=2, squeeze=False, constrained_layout=True, width_mm=177.8, height_mm=130)
    for axis, key, title in (
        (axes[0][0], "r_mean_v", "Mean R"),
        (axes[0][1], "x_mean_v", "Mean X"),
        (axes[1][0], "y_mean_v", "Mean Y"),
    ):
        values = np.asarray(data[key], dtype=float)[z_index].T
        bound = max(float(np.nanmax(np.abs(values))), np.finfo(float).eps)
        mesh = axis.pcolormesh(
            _axis_edges(x_axis),
            _axis_edges(y_axis),
            values,
            shading="flat",
            cmap="viridis" if key == "r_mean_v" else "coolwarm",
            vmin=0.0 if key == "r_mean_v" else -bound,
            vmax=bound,
            rasterized=True,
        )
        axis.plot(x_axis[x_index], y_axis[y_index], "kx", markersize=6)
        if fitted_center_v is not None:
            axis.plot(
                fitted_center_v[0],
                fitted_center_v[1],
                marker="o",
                markerfacecolor="none",
                markeredgecolor="black",
                markersize=6,
                markeredgewidth=1.0,
            )
        format_axis(axis, xlabel="X field setting (V)", ylabel="Y field setting (V)")
        axis.set_title(f"{title} at Z bias={z_axis[z_index]:+.4g} V")
        fig.colorbar(mesh, ax=axis).set_label("V")
    axis = axes[1][1]
    axis.plot(z_axis, np.asarray(data["r_mean_v"])[:, x_index, y_index], "o-", label="R")
    axis.plot(z_axis, np.asarray(data["x_mean_v"])[:, x_index, y_index], "o-", label="X")
    axis.plot(z_axis, np.asarray(data["y_mean_v"])[:, x_index, y_index], "o-", label="Y")
    format_axis(axis, xlabel="Z bias (V)", ylabel="Demod output (V)")
    axis.legend(fontsize=8)
    axis.set_title("Components at measured X/Y minimum")
    filename = "xyz_balance_components.png"
    save_figure(fig, results_dir / filename)
    return filename


def _plot_bloch_fit(
    results_dir: Path,
    data: dict[str, np.ndarray],
    plane_results: list[dict[str, Any]],
) -> str:
    """保存拟合中心、残差和局部 R 的诊断图。"""
    set_plot_style("paper")
    z_axis = np.asarray(data["z_bias_v"], dtype=float)
    centers = np.asarray([item["balance_xy_v"] for item in plane_results], dtype=float)
    z_values = np.asarray([item["z_bias_v"] for item in plane_results], dtype=float)
    local_r = np.asarray(
        [item["model_min_local_3x3_median_r_v"] for item in plane_results],
        dtype=float,
    )
    r_squared = np.asarray(
        [item["r_squared_complex"] for item in plane_results], dtype=float
    )
    fig, axes = new_figure(nrows=2, ncols=2, squeeze=False, constrained_layout=True, width_mm=177.8, height_mm=130)
    axes[0][0].plot(z_values, centers[:, 0] * 1e3, "o-", label="Fitted X center")
    axes[0][0].plot(z_values, centers[:, 1] * 1e3, "s-", label="Fitted Y center")
    format_axis(axes[0][0], xlabel="Z bias (V)", ylabel="XY center (mV)")
    axes[0][0].legend(fontsize=8)
    axes[0][1].plot(z_values, local_r * 1e3, "o-", label="Local R")
    quality_axis = axes[0][1].twinx()
    quality_axis.plot(z_values, r_squared, "s--", color=COLOR_TRAD, label="Complex R²")
    format_axis(axes[0][1], xlabel="Z bias (V)", ylabel="Local R (mV)")
    quality_axis.set_ylabel("Complex R² (dimensionless)")
    axes[0][1].legend(loc="upper left", fontsize=8)
    quality_axis.legend(loc="lower right", fontsize=8)
    if plane_results:
        residual_max = max(float(np.nanmax(item["residual_matrix_v"])) * 1e3 for item in (plane_results[0], plane_results[-1]))
        residual_max = max(residual_max, np.finfo(float).eps)
        for panel_index, item in enumerate((plane_results[0], plane_results[-1])):
            residual = np.asarray(item["residual_matrix_v"], dtype=float)
            image = axes[1][panel_index].imshow(
                residual.T * 1e3,
                cmap="magma", vmin=0.0, vmax=residual_max,
                origin="lower",
                aspect="auto",
                extent=[
                    float(data["x_field_v"][0]),
                    float(data["x_field_v"][-1]),
                    float(data["y_field_v"][0]),
                    float(data["y_field_v"][-1]),
                ],
            )
            axes[1][panel_index].set_title(
                f"Z={float(item['z_bias_v']):+.3f} V residual"
            )
            format_axis(
                axes[1][panel_index],
                xlabel="X field (V)",
                ylabel="Y field (V)",
            )
            fig.colorbar(image, ax=axes[1][panel_index]).set_label(
                "Complex residual (mV)"
            )
    filename = "bloch_fit_summary.png"
    save_figure(fig, results_dir / filename)
    return filename


def _write_csv(results_dir: Path, data: dict[str, np.ndarray]) -> str:
    filename = "xyz_balance_grid.csv"
    x_axis = np.asarray(data["x_field_v"], dtype=float)
    y_axis = np.asarray(data["y_field_v"], dtype=float)
    z_axis = np.asarray(data["z_bias_v"], dtype=float)
    order = np.asarray(data["acquisition_order"], dtype=int)
    with (results_dir / filename).open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            (
                "acquisition_index",
                "z_index",
                "x_index",
                "y_index",
                "z_bias_v",
                "z_bias_frequency_hz",
                "z_bias_absolute_bz_nt",
                "z_control_waveform_mean_bz_nt",
                "x_field_v",
                "y_field_v",
                "r_mean_v",
                "x_mean_v",
                "y_mean_v",
                "r_std_v",
                "complex_std_v",
            )
        )
        frequency = np.asarray(data["z_bias_frequency_hz"], dtype=float)
        absolute_bz = np.asarray(data["z_bias_absolute_bz_nt"], dtype=float)
        mean_control_bz = np.asarray(
            data.get(
                "z_control_waveform_mean_bz_nt",
                absolute_bz,
            ),
            dtype=float,
        )
        for z_index, x_index, y_index in np.ndindex(
            np.asarray(data["r_mean_v"]).shape
        ):
            writer.writerow(
                (
                    int(order[z_index, x_index, y_index]),
                    z_index,
                    x_index,
                    y_index,
                    float(z_axis[z_index]),
                    float(frequency[z_index]),
                    float(absolute_bz[z_index]),
                    float(mean_control_bz[z_index]),
                    float(x_axis[x_index]),
                    float(y_axis[y_index]),
                    float(data["r_mean_v"][z_index, x_index, y_index]),
                    float(data["x_mean_v"][z_index, x_index, y_index]),
                    float(data["y_mean_v"][z_index, x_index, y_index]),
                    float(data["r_std_v"][z_index, x_index, y_index]),
                    float(data["complex_std_v"][z_index, x_index, y_index]),
                )
            )
    return filename


def analyze(run_dir: Path) -> dict[str, Any]:
    """汇总三维 R/X/Y 响应并报告稳健复数拟合平衡点。"""
    run_dir = Path(run_dir).resolve()
    results_dir = run_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    config = _load_yaml(run_dir / "experiment_config.yaml")
    data = _load_scan(run_dir / "raw" / "xyz_balance_scan.npz")
    _validate_scan(data)
    best_index = select_measured_minimum(data)
    z_index, x_index, y_index = best_index
    x_axis = np.asarray(data["x_field_v"], dtype=float)
    y_axis = np.asarray(data["y_field_v"], dtype=float)
    z_axis = np.asarray(data["z_bias_v"], dtype=float)
    r_mean = np.asarray(data["r_mean_v"], dtype=float)
    r_std = np.asarray(data["r_std_v"], dtype=float)
    order = np.asarray(data["acquisition_order"], dtype=int)
    calibration = config.get("z_control_calibration", {})
    gamma = float(calibration.get("gamma_hz_per_nt", GAMMA_HZ_PER_NT))
    if "z_bias_frequency_hz" not in data:
        slope = float(calibration.get("slope_hz_per_v", 0.0))
        intercept = float(calibration.get("intercept_hz", 0.0))
        data["z_bias_frequency_hz"] = slope * z_axis
    if "z_bias_absolute_bz_nt" not in data:
        intercept = float(calibration.get("intercept_hz", 0.0))
        data["z_bias_absolute_bz_nt"] = (
            intercept + np.asarray(data["z_bias_frequency_hz"], dtype=float)
        ) / gamma
    if "z_control_waveform_mean_bz_nt" not in data:
        data["z_control_waveform_mean_bz_nt"] = data["z_bias_absolute_bz_nt"]

    bloch_fit, plane_results = _fit_summary_result(data, config)
    robust_center = np.asarray(bloch_fit["robust_xy_center_v"], dtype=float)
    z_zero = bloch_fit["z_static_zero_from_calibration"]
    nearest_z_index = int(np.argmin(np.abs(z_axis - float(z_zero["z_bias_v"]))))
    nearest_plane = next(
        item for item in plane_results if int(item["z_index"]) == nearest_z_index
    )
    nearest_xy_index = (
        int(np.argmin(np.abs(x_axis - robust_center[0]))),
        int(np.argmin(np.abs(y_axis - robust_center[1]))),
    )
    fitted_point = {
        "x_field_v": float(robust_center[0]),
        "y_field_v": float(robust_center[1]),
        "z_bias_v": float(z_zero["z_bias_v"]),
        "z_bias_frequency_hz": 0.0,
        "z_bias_absolute_bz_nt": 0.0,
        "nearest_measured_z_bias_v": float(z_axis[nearest_z_index]),
        "nearest_measured_z_index": nearest_z_index,
        "nearest_measured_xy_v": [
            float(x_axis[nearest_xy_index[0]]),
            float(y_axis[nearest_xy_index[1]]),
        ],
        "nearest_measured_xy_r_v": float(
            np.asarray(data["r_mean_v"])[
                nearest_z_index, nearest_xy_index[0], nearest_xy_index[1]
            ]
        ),
        "nearest_fit_plane_r_squared": float(nearest_plane["r_squared_complex"]),
    }
    plot_file = _plot_planes(results_dir, data, best_index, plane_results)
    component_plot_file = _plot_components(results_dir, data, best_index, robust_center)
    fit_plot_file = _plot_bloch_fit(results_dir, data, plane_results)
    csv_file = _write_csv(results_dir, data)
    npz_file = "balance_results.npz"
    np.savez(
        results_dir / npz_file,
        best_z_index=np.int64(z_index),
        best_x_index=np.int64(x_index),
        best_y_index=np.int64(y_index),
        best_z_bias_v=np.float64(z_axis[z_index]),
        best_z_bias_frequency_hz=np.float64(data["z_bias_frequency_hz"][z_index]),
        best_z_bias_absolute_bz_nt=np.float64(data["z_bias_absolute_bz_nt"][z_index]),
        best_z_control_waveform_mean_bz_nt=np.float64(
            data["z_control_waveform_mean_bz_nt"][z_index]
        ),
        best_x_field_v=np.float64(x_axis[x_index]),
        best_y_field_v=np.float64(y_axis[y_index]),
        best_r_mean_v=np.float64(r_mean[best_index]),
        best_r_std_v=np.float64(r_std[best_index]),
        best_x_mean_v=np.float64(data["x_mean_v"][best_index]),
        best_y_mean_v=np.float64(data["y_mean_v"][best_index]),
        best_acquisition_index=np.int64(order[best_index]),
        fitted_x_field_v=np.float64(robust_center[0]),
        fitted_y_field_v=np.float64(robust_center[1]),
        fitted_z_bias_v=np.float64(z_zero["z_bias_v"]),
        fitted_z_bias_absolute_bz_nt=np.float64(0.0),
    )
    fit_npz_file = "bloch_fit_analysis.npz"
    z_fit_values = np.asarray([item["z_bias_v"] for item in plane_results], dtype=float)
    np.savez(
        results_dir / fit_npz_file,
        z_bias_v=z_fit_values,
        z_bias_absolute_bz_nt=np.asarray(
            [item["z_bias_absolute_bz_nt"] for item in plane_results], dtype=float
        ),
        balance_xy_v=np.asarray(
            [item["balance_xy_v"] for item in plane_results], dtype=float
        ),
        model_min_xy_v=np.asarray(
            [item["model_min_xy_v"] for item in plane_results], dtype=float
        ),
        model_min_r_v=np.asarray(
            [item["model_min_r_v"] for item in plane_results], dtype=float
        ),
        model_min_local_3x3_median_r_v=np.asarray(
            [item["model_min_local_3x3_median_r_v"] for item in plane_results],
            dtype=float,
        ),
        r_squared_complex=np.asarray(
            [item["r_squared_complex"] for item in plane_results], dtype=float
        ),
        rmse_complex_v=np.asarray(
            [item["rmse_complex_v"] for item in plane_results], dtype=float
        ),
        fit_parameters=np.asarray(
            [item["parameters"] for item in plane_results], dtype=float
        ),
        robust_xy_center_v=robust_center,
        z_static_zero_v=np.float64(z_zero["z_bias_v"]),
    )
    warnings = [
        "实测网格 R 最小点仅作为诊断保留，主结果来自 Demod X/Y 复数稳健拟合。",
        "当前拟合是稳态 Bloch 型二维响应面，不是周期最优控制的严格 Bloch/Floquet 解。",
        "偏置到 Bz 的换算只使用静态 K_Z 标定，不能替代线圈动态传递函数。",
        "最佳 XYZ 组合未在扫描结束后额外复测。",
    ]
    for name, index, axis in (("X", x_index, x_axis), ("Y", y_index, y_axis)):
        if axis.size > 1 and index in {0, axis.size - 1}:
            warnings.append(f"{name} 最小点位于扫描边界。")
    if np.isfinite(z_zero["z_bias_v"]) and (
        z_zero["z_bias_v"] < z_axis[0] or z_zero["z_bias_v"] > z_axis[-1]
    ):
        warnings.append("静态标定得到的 Z 零场位于当前扫描区间之外。")
    result = {
        "experiment_id": EXPERIMENT_ID,
        "run_dir": str(run_dir),
        "scan_completed": config.get("completion_status") == "completed",
        "scan_shape_zyx": [int(z_axis.size), int(x_axis.size), int(y_axis.size)],
        "scan_order": "Z bias outer, continuous X/Y serpentine",
        "criterion": "robust complex Demod X/Y Bloch fit",
        "measured_grid_minimum": {
            "z_index": z_index,
            "x_index": x_index,
            "y_index": y_index,
            "z_bias_v": float(z_axis[z_index]),
            "z_bias_frequency_hz": float(data["z_bias_frequency_hz"][z_index]),
            "z_bias_absolute_bz_nt": float(data["z_bias_absolute_bz_nt"][z_index]),
            "z_control_waveform_mean_bz_nt": float(
                data["z_control_waveform_mean_bz_nt"][z_index]
            ),
            "x_field_v": float(x_axis[x_index]),
            "y_field_v": float(y_axis[y_index]),
            "r_mean_v": float(r_mean[best_index]),
            "r_std_v": float(r_std[best_index]),
            "x_mean_v": float(data["x_mean_v"][best_index]),
            "y_mean_v": float(data["y_mean_v"][best_index]),
            "acquisition_index": int(order[best_index]),
            "remeasured": False,
        },
        "response_summary": {
            "minimum_r_v": float(np.min(r_mean)),
            "maximum_r_v": float(np.max(r_mean)),
            "mean_r_v": float(np.mean(r_mean)),
            "mean_point_std_v": float(np.mean(r_std)),
            "actual_rate_sa_s": float(data["actual_rate_sa_s"]),
        },
        "fitted_balance_point": fitted_point,
        "bloch_fit": bloch_fit,
        "control_source": config.get("control_source", {}),
        "applied_control": config.get("applied_control", {}),
        "bz_conversion": {
            "gamma_hz_per_nt": gamma,
            "slope_hz_per_v": float(
                config.get("z_control_calibration", {}).get(
                    "slope_hz_per_v", np.nan
                )
            ),
            "intercept_hz": float(
                config.get("z_control_calibration", {}).get(
                    "intercept_hz", np.nan
                )
            ),
        },
        "interpretation": {
            "bloch_fit_available": True,
            "model": FIT_MODEL,
            "model_scope": "Demod X/Y 复数稳态响应；Z 方向采用静态 K_Z 标定零场",
            "periodic_floquet_fit_available": False,
            "absolute_field_reported": True,
        },
        "warnings": warnings,
        "files": [
            npz_file,
            fit_npz_file,
            "bloch_fit_analysis.yaml",
            "bloch_fit_analysis.json",
            csv_file,
            plot_file,
            component_plot_file,
            fit_plot_file,
        ],
    }
    fit_yaml_file = results_dir / "bloch_fit_analysis.yaml"
    fit_json_file = results_dir / "bloch_fit_analysis.json"
    fit_yaml_file.write_text(
        yaml.safe_dump(_builtin(bloch_fit), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    fit_json_file.write_text(
        json.dumps(_builtin(bloch_fit), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (results_dir / "analysis.yaml").write_text(
        yaml.safe_dump(_builtin(result), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    (results_dir / "analysis.json").write_text(
        json.dumps(_builtin(result), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def main() -> int:
    run_dir = runtime_run_dir()
    if run_dir is None:
        raise RuntimeError("未设置分析运行目录")
    result = analyze(run_dir)
    best = result["fitted_balance_point"]
    print(
        "Mx Z DG4000 偏置 XYZ 平衡场分析完成: "
        f"X={best['x_field_v']:+.6g} V, "
        f"Y={best['y_field_v']:+.6g} V, "
        f"Z bias={best['z_bias_v']:+.6g} V, "
        f"Bz={best['z_bias_absolute_bz_nt']:+.6g} nT"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
