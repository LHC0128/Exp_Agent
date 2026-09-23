"""Mx Z 最优控制 XYZ 平衡场离线分析。"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from ...experiment_runtime import runtime_run_dir
from ...plotting import (
    COLOR_GREEN,
    COLOR_OPTIMAL,
    COLOR_ORANGE,
    COLOR_TRAD,
    PAPER_WIDE,
    format_axis,
    new_figure,
    save_figure,
    set_plot_style,
    style_legend,
)
from .vshape import (
    detect_grid_outliers,
    fit_complex_linear_mod,
    fit_vshape_1d,
)


EXPERIMENT_ID = "mx-z-optimal-control-xyz-balance"


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
            "z_field_ma",
            "r_mean_v",
            "r_std_v",
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
    z_axis = np.asarray(data["z_field_ma"], dtype=float)
    expected_shape = (z_axis.size, x_axis.size, y_axis.size)
    if min(expected_shape) < 1:
        raise ValueError("XYZ 扫描轴不能为空")
    for key in ("r_mean_v", "r_std_v", "acquisition_order"):
        if np.asarray(data[key]).shape != expected_shape:
            raise ValueError(
                f"{key} 形状应为 {expected_shape}，实际为 "
                f"{np.asarray(data[key]).shape}"
            )
    for name, axis in (("X", x_axis), ("Y", y_axis), ("Z", z_axis)):
        if not np.all(np.isfinite(axis)):
            raise ValueError(f"{name} 扫描轴包含 NaN 或无穷值")
    for key in ("r_mean_v", "r_std_v"):
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


def _axis_step(axis: np.ndarray) -> float | None:
    """返回扫描轴步长；单点轴返回 None。"""
    axis = np.asarray(axis, dtype=float)
    if axis.size < 2:
        return None
    return float(np.median(np.diff(axis)))


def _fit_summary(fit: dict[str, Any]) -> dict[str, Any]:
    """剔除内部掩码后的拟合摘要，便于写入 YAML/JSON。"""
    return {key: value for key, value in fit.items() if key != "final_mask"}


def _model_curve(fit: dict[str, Any], dense: np.ndarray) -> np.ndarray:
    """按拟合模式生成模型曲线。"""
    if fit.get("mode") == "complex_linear_mod":
        a = float(fit["a"])
        b = float(fit["b"])
        c = float(fit["c"])
        d = float(fit["d"])
        e0 = float(fit.get("e0", 0.0))
        return np.sqrt((a * dense + c) ** 2 + (b * dense + d) ** 2) + e0
    return np.abs(fit["k"] * (dense - fit["s0"])) + fit["e"]


def _plot_linear_fit(
    results_dir: Path,
    data: dict[str, np.ndarray],
    work_z_index: int,
    per_layer: list[dict[str, Any]],
    x0_values: np.ndarray,
    y0_values: np.ndarray,
) -> str:
    """绘制工作点层的两条切片拟合与零点随 z 漂移图。"""
    set_plot_style("paper")
    x_axis = np.asarray(data["x_field_v"], dtype=float)
    y_axis = np.asarray(data["y_field_v"], dtype=float)
    z_axis = np.asarray(data["z_field_ma"], dtype=float)
    r_mean = np.asarray(data["r_mean_v"], dtype=float)
    layer = per_layer[work_z_index]
    fig, axes = new_figure(nrows=2, ncols=2, squeeze=False, constrained_layout=True, width_mm=177.8, height_mm=130)
    # 左上：X 切片
    ax = axes[0][0]
    y_cut = float(layer["slice_x_at_y_v"])
    y_cut_index = int(np.argmin(np.abs(y_axis - y_cut)))
    ax.plot(
        x_axis,
        r_mean[work_z_index, :, y_cut_index],
        "o",
        color=COLOR_OPTIMAL,
        label="Data",
    )
    x_fit = layer["x_fit"]
    if x_fit["success"]:
        dense = np.linspace(float(x_axis.min()), float(x_axis.max()), 200)
        ax.plot(
            dense,
            _model_curve(x_fit, dense),
            "--",
            color=COLOR_TRAD,
            label="Fit",
        )
        ax.axvline(x_fit["s0"], color=COLOR_ORANGE, ls=":", alpha=0.6)
    format_axis(
        ax,
        xlabel="X field setting (V)",
        ylabel="Mean R (V)",
    )
    ax.set_title(f"X slice at Y={y_cut:+.4g} V (Z={z_axis[work_z_index]:+.4g} mA)")
    style_legend(ax, fontsize=7.0)
    # 右上：Y 切片
    ax = axes[0][1]
    x_cut = float(layer["slice_y_at_x_v"])
    x_cut_index = int(np.argmin(np.abs(x_axis - x_cut)))
    ax.plot(
        y_axis,
        r_mean[work_z_index, x_cut_index, :],
        "o",
        color=COLOR_OPTIMAL,
        label="Data",
    )
    y_fit = layer["y_fit"]
    if y_fit["success"]:
        dense = np.linspace(float(y_axis.min()), float(y_axis.max()), 200)
        ax.plot(
            dense,
            _model_curve(y_fit, dense),
            "--",
            color=COLOR_TRAD,
            label="Fit",
        )
        ax.axvline(y_fit["s0"], color=COLOR_ORANGE, ls=":", alpha=0.6)
    format_axis(
        ax,
        xlabel="Y field setting (V)",
        ylabel="Mean R (V)",
    )
    ax.set_title(f"Y slice at X={x_cut:+.4g} V (Z={z_axis[work_z_index]:+.4g} mA)")
    style_legend(ax, fontsize=7.0)
    # 左下：x0 vs z
    ax = axes[1][0]
    finite = np.isfinite(x0_values)
    if int(finite.sum()) >= 2:
        ax.plot(
            z_axis[finite],
            x0_values[finite],
            "o",
            color=COLOR_OPTIMAL,
            label="Fitted X zero",
        )
        if int(finite.sum()) >= 3:
            coefficients = np.polyfit(z_axis[finite], x0_values[finite], 1)
            dense = np.linspace(float(z_axis.min()), float(z_axis.max()), 100)
            ax.plot(
                dense,
                np.polyval(coefficients, dense),
                "--",
                color=COLOR_GREEN,
                label=f"Slope {coefficients[0]:+.3g} V/mA",
            )
    format_axis(ax, xlabel="Z field setting (mA)", ylabel="Fitted X zero (V)")
    style_legend(ax, fontsize=7.0)
    # 右下：y0 vs z
    ax = axes[1][1]
    finite = np.isfinite(y0_values)
    if int(finite.sum()) >= 2:
        ax.plot(
            z_axis[finite],
            y0_values[finite],
            "o",
            color=COLOR_OPTIMAL,
            label="Fitted Y zero",
        )
        if int(finite.sum()) >= 3:
            coefficients = np.polyfit(z_axis[finite], y0_values[finite], 1)
            dense = np.linspace(float(z_axis.min()), float(z_axis.max()), 100)
            ax.plot(
                dense,
                np.polyval(coefficients, dense),
                "--",
                color=COLOR_GREEN,
                label=f"Slope {coefficients[0]:+.3g} V/mA",
            )
    format_axis(ax, xlabel="Z field setting (mA)", ylabel="Fitted Y zero (V)")
    style_legend(ax, fontsize=7.0)
    filename = "xyz_balance_linear_fit.png"
    save_figure(fig, results_dir / filename)
    return filename


def analyze_xyz_linear_fit(
    data: dict[str, np.ndarray],
    results_dir: Path,
    *,
    coupling_min_r_squared: float = 0.5,
) -> dict[str, Any]:
    """沿每层网格最小点的行/列做一维 V 形拟合，输出零点、耦合与建议。

    共享给 Mx Z 与 Mx Keithley 6221 两个 XYZ 平衡实验的分析器使用。
    返回可并入 analysis.yaml 的片段，含 ``linear_fit`` 与 ``files`` 键。
    """
    x_axis = np.asarray(data["x_field_v"], dtype=float)
    y_axis = np.asarray(data["y_field_v"], dtype=float)
    z_axis = np.asarray(data["z_field_ma"], dtype=float)
    r_mean = np.asarray(data["r_mean_v"], dtype=float)

    outlier_mask = detect_grid_outliers(r_mean)
    outlier_positions = [
        {
            "z_field_ma": float(z_axis[zi]),
            "x_field_v": float(x_axis[xi]),
            "y_field_v": float(y_axis[yi]),
            "r_mean_v": float(r_mean[zi, xi, yi]),
        }
        for zi, xi, yi in np.argwhere(outlier_mask)
    ]
    clean_r = np.where(outlier_mask, np.inf, r_mean)

    per_layer: list[dict[str, Any]] = []
    x0_values = np.full(z_axis.size, np.nan)
    y0_values = np.full(z_axis.size, np.nan)
    layer_e = np.full(z_axis.size, np.nan)
    for zi in range(z_axis.size):
        plane = clean_r[zi]
        flat_index = int(np.argmin(plane))
        xi_layer, yi_layer = np.unravel_index(flat_index, plane.shape)
        # 复线性模拟合（含复相位干涉的 |J*s+C| 形），失败时回退实 V 形
        x_fit = fit_complex_linear_mod(x_axis, r_mean[zi, :, yi_layer])
        if not x_fit["success"]:
            x_fit = fit_vshape_1d(x_axis, r_mean[zi, :, yi_layer])
        y_fit = fit_complex_linear_mod(y_axis, r_mean[zi, xi_layer, :])
        if not y_fit["success"]:
            y_fit = fit_vshape_1d(y_axis, r_mean[zi, xi_layer, :])
        e_value = float(np.min(plane))
        layer_e[zi] = e_value
        if x_fit["success"]:
            x0_values[zi] = float(x_fit["s0"])
        if y_fit["success"]:
            y0_values[zi] = float(y_fit["s0"])
        per_layer.append(
            {
                "z_field_ma": float(z_axis[zi]),
                "slice_x_at_y_v": float(y_axis[yi_layer]),
                "slice_y_at_x_v": float(x_axis[xi_layer]),
                "e_v": e_value,
                "x_fit": _fit_summary(x_fit),
                "y_fit": _fit_summary(y_fit),
            }
        )

    coupling: dict[str, Any] = {
        "x0_slope_v_per_ma": None,
        "x0_intercept_v": None,
        "y0_slope_v_per_ma": None,
        "y0_intercept_v": None,
        "n_layers": 0,
        "min_fit_r_squared": float(coupling_min_r_squared),
    }
    valid_x = np.asarray(
        [
            bool(np.isfinite(x0_values[index]))
            and float(
                per_layer[index]["x_fit"].get("r_squared", np.nan)
            )
            >= coupling["min_fit_r_squared"]
            for index in range(z_axis.size)
        ]
    )
    valid_y = np.asarray(
        [
            bool(np.isfinite(y0_values[index]))
            and float(
                per_layer[index]["y_fit"].get("r_squared", np.nan)
            )
            >= coupling["min_fit_r_squared"]
            for index in range(z_axis.size)
        ]
    )
    coupling["n_layers"] = int(min(valid_x.sum(), valid_y.sum()))
    if int(valid_x.sum()) >= 3:
        coefficients = np.polyfit(z_axis[valid_x], x0_values[valid_x], 1)
        coupling["x0_slope_v_per_ma"] = float(coefficients[0])
        coupling["x0_intercept_v"] = float(coefficients[1])
    if int(valid_y.sum()) >= 3:
        coefficients = np.polyfit(z_axis[valid_y], y0_values[valid_y], 1)
        coupling["y0_slope_v_per_ma"] = float(coefficients[0])
        coupling["y0_intercept_v"] = float(coefficients[1])

    work_z_index = int(np.argmin(layer_e))
    z_layer_fit: dict[str, Any] = {
        "method": "argmin",
        "vertex_z_ma": None,
        "curvature": None,
    }
    if z_axis.size >= 3 and np.all(np.isfinite(layer_e)):
        coefficients = np.polyfit(z_axis, layer_e, 2)
        curvature = float(coefficients[0])
        vertex = float(-coefficients[1] / (2.0 * coefficients[0]))
        span = float(np.ptp(z_axis))
        if (
            curvature > 1e-12
            and float(z_axis.min()) - span <= vertex <= float(z_axis.max()) + span
        ):
            nearest = int(np.argmin(np.abs(z_axis - vertex)))
            if np.isfinite(x0_values[nearest]) and np.isfinite(y0_values[nearest]):
                work_z_index = nearest
                z_layer_fit = {
                    "method": "quadratic_vertex",
                    "vertex_z_ma": vertex,
                    "curvature": curvature,
                }
    work_z = float(
        z_layer_fit["vertex_z_ma"]
        if z_layer_fit["method"] == "quadratic_vertex"
        else z_axis[work_z_index]
    )
    work_x = float(x0_values[work_z_index])
    work_y = float(y0_values[work_z_index])
    x_fallback = not np.isfinite(work_x)
    y_fallback = not np.isfinite(work_y)
    if x_fallback:
        work_x = float(
            np.unravel_index(
                int(np.argmin(clean_r[work_z_index])),
                clean_r[work_z_index].shape,
            )[0]
        )
        work_x = float(x_axis[int(np.argmin(np.abs(x_axis - work_x)))])
    if y_fallback:
        work_y = float(
            np.unravel_index(
                int(np.argmin(clean_r[work_z_index])),
                clean_r[work_z_index].shape,
            )[1]
        )
        work_y = float(y_axis[int(np.argmin(np.abs(y_axis - work_y)))])
    extrapolated = (
        (not x_fallback and not bool(
            float(x_axis.min()) <= work_x <= float(x_axis.max())
        ))
        or (not y_fallback and not bool(
            float(y_axis.min()) <= work_y <= float(y_axis.max())
        ))
    )

    grid_best = select_measured_minimum(data)
    grid_x = float(x_axis[grid_best[1]])
    grid_y = float(y_axis[grid_best[2]])
    grid_z = float(z_axis[grid_best[0]])
    step_x = _axis_step(x_axis)
    step_y = _axis_step(y_axis)
    grid_comparison = {
        "dx_v": None if not np.isfinite(work_x) else float(work_x - grid_x),
        "dy_v": None if not np.isfinite(work_y) else float(work_y - grid_y),
        "dz_ma": float(work_z - grid_z),
        "x_within_one_step": (
            None
            if step_x is None or not np.isfinite(work_x)
            else bool(abs(float(work_x - grid_x)) <= step_x)
        ),
        "y_within_one_step": (
            None
            if step_y is None or not np.isfinite(work_y)
            else bool(abs(float(work_y - grid_y)) <= step_y)
        ),
    }

    near_edge = False
    if step_x is not None and np.isfinite(work_x):
        near_edge = near_edge or bool(
            min(abs(work_x - float(x_axis.min())), abs(work_x - float(x_axis.max())))
            < step_x
        )
    if step_y is not None and np.isfinite(work_y):
        near_edge = near_edge or bool(
            min(abs(work_y - float(y_axis.min())), abs(work_y - float(y_axis.max())))
            < step_y
        )
    suggestion = None
    if extrapolated or near_edge or x_fallback or y_fallback:
        suggestion = {
            "reason": (
                "fitted zero is outside or within one step of the scan "
                "boundary"
            ),
            "suggested_center": {
                "x_field_v": None if not np.isfinite(work_x) else work_x,
                "y_field_v": None if not np.isfinite(work_y) else work_y,
                "z_field_ma": work_z,
            },
            "note": (
                "re-run the scan centered on the fitted zero and widen the "
                "range until the minimum lies strictly inside the window"
            ),
        }

    fit_warnings: list[str] = []
    if x_fallback or y_fallback:
        fit_warnings.append(
            "一维 V 形拟合在工作点层失败，拟合工作点已回退到该层网格最小点。"
        )
    if grid_comparison["x_within_one_step"] is False or (
        grid_comparison["y_within_one_step"] is False
    ):
        fit_warnings.append(
            "拟合工作点与网格最小点偏差超过一个扫描步长，"
            "建议以拟合零点为中心复测。"
        )
    if extrapolated:
        fit_warnings.append(
            "拟合零点位于扫描窗口之外，工作点为外推结果，需要扩大扫描范围复测。"
        )
    if int(outlier_mask.sum()) > 0:
        fit_warnings.append(
            f"检测到 {int(outlier_mask.sum())} 个邻域孤立点"
            "（瞬态触发/相位毛刺或窄谷结构），已从层最小点判定中排除，"
            "位置见 balance_linear_fit.npz 的 outlier_mask。"
        )

    fitted_workpoint = {
        "x_field_v": None if not np.isfinite(work_x) else work_x,
        "y_field_v": None if not np.isfinite(work_y) else work_y,
        "z_field_ma": work_z,
        "x_from_fit": not x_fallback,
        "y_from_fit": not y_fallback,
        "extrapolated": bool(extrapolated),
    }

    plot_file = _plot_linear_fit(
        results_dir,
        data,
        work_z_index,
        per_layer,
        x0_values,
        y0_values,
    )
    npz_file = "balance_linear_fit.npz"
    np.savez(
        results_dir / npz_file,
        z_field_ma=z_axis,
        x_field_v=x_axis,
        y_field_v=y_axis,
        x0_v=x0_values,
        y0_v=y0_values,
        layer_e_v=layer_e,
        outlier_mask=outlier_mask,
        workpoint_x_field_v=np.float64(work_x),
        workpoint_y_field_v=np.float64(work_y),
        workpoint_z_field_ma=np.float64(work_z),
        x0_slope_v_per_ma=(
            np.float64(np.nan)
            if coupling["x0_slope_v_per_ma"] is None
            else np.float64(coupling["x0_slope_v_per_ma"])
        ),
        y0_slope_v_per_ma=(
            np.float64(np.nan)
            if coupling["y0_slope_v_per_ma"] is None
            else np.float64(coupling["y0_slope_v_per_ma"])
        ),
    )

    return {
        "linear_fit": {
            "method": "per_layer_1d_vshape",
            "model": "R = |k (s - s0)| + e along 1-D slices through each layer minimum",
            "outlier_detection": {
                "method": "neighborhood_median_deviation_3mad",
                "count": int(outlier_mask.sum()),
                "positions": outlier_positions,
            },
            "per_layer": per_layer,
            "coupling": coupling,
            "z_layer_fit": z_layer_fit,
            "fitted_workpoint": fitted_workpoint,
            "grid_comparison": grid_comparison,
            "next_scan_suggestion": suggestion,
        },
        "linear_fit_warnings": fit_warnings,
        "linear_fit_files": [npz_file, plot_file],
    }



def _plot_xy_planes(
    results_dir: Path,
    data: dict[str, np.ndarray],
    best_index: tuple[int, int, int],
) -> str:
    set_plot_style("paper")
    best_z_index, best_x_index, best_y_index = best_index
    x_axis = np.asarray(data["x_field_v"], dtype=float)
    y_axis = np.asarray(data["y_field_v"], dtype=float)
    z_axis = np.asarray(data["z_field_ma"], dtype=float)
    r_mean = np.asarray(data["r_mean_v"], dtype=float)
    vmin = float(np.min(r_mean))
    vmax = float(np.max(r_mean))

    columns = min(3, int(z_axis.size))
    rows = int(np.ceil(z_axis.size / columns))
    figure_width = min(PAPER_WIDE[0], 2.25 * columns + 0.75)
    fig, axes = new_figure(
        figsize=(figure_width, 2.4 * rows),
        nrows=rows,
        ncols=columns,
        squeeze=False,
        sharex=True,
        sharey=True,
        constrained_layout=True,
    )
    x_edges = _axis_edges(x_axis)
    y_edges = _axis_edges(y_axis)
    mesh = None
    plane_axes = []
    for z_index, axis in enumerate(axes.flat[: z_axis.size]):
        plane_axes.append(axis)
        mesh = axis.pcolormesh(
            x_edges,
            y_edges,
            r_mean[z_index, :, :].T,
            shading="flat",
            cmap="viridis",
            vmin=vmin,
            vmax=vmax, rasterized=True)
        if z_index == best_z_index:
            axis.plot(
                float(x_axis[best_x_index]),
                float(y_axis[best_y_index]),
                marker="x",
                color="white",
                markersize=7,
                markeredgewidth=1.4,
                label="Measured minimum",
            )
        format_axis(
            axis,
            xlabel="X field setting (V)",
            ylabel="Y field setting (V)",
        )
        axis.set_title(
            f"Z current = {z_axis[z_index]:+.6g} mA",
            fontsize=8,
        )
    for axis in axes.flat[z_axis.size :]:
        axis.set_visible(False)
    assert mesh is not None
    colorbar = fig.colorbar(mesh, ax=plane_axes)
    colorbar.set_label("Mean R (V)")
    filename = "xyz_balance_slices.png"
    save_figure(fig, results_dir / filename)
    return filename


def _write_csv(results_dir: Path, data: dict[str, np.ndarray]) -> str:
    filename = "xyz_balance_grid.csv"
    x_axis = np.asarray(data["x_field_v"], dtype=float)
    y_axis = np.asarray(data["y_field_v"], dtype=float)
    z_axis = np.asarray(data["z_field_ma"], dtype=float)
    r_mean = np.asarray(data["r_mean_v"], dtype=float)
    r_std = np.asarray(data["r_std_v"], dtype=float)
    order = np.asarray(data["acquisition_order"], dtype=int)
    with (results_dir / filename).open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            (
                "acquisition_index",
                "z_index",
                "x_index",
                "y_index",
                "z_field_ma",
                "x_field_v",
                "y_field_v",
                "r_mean_v",
                "r_std_v",
            )
        )
        for z_index, x_index, y_index in np.ndindex(r_mean.shape):
            writer.writerow(
                (
                    int(order[z_index, x_index, y_index]),
                    z_index,
                    x_index,
                    y_index,
                    float(z_axis[z_index]),
                    float(x_axis[x_index]),
                    float(y_axis[y_index]),
                    float(r_mean[z_index, x_index, y_index]),
                    float(r_std[z_index, x_index, y_index]),
                )
            )
    return filename


def analyze(run_dir: Path) -> dict[str, Any]:
    """汇总三维 R 响应并报告实测网格最小点。"""
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
    z_axis = np.asarray(data["z_field_ma"], dtype=float)
    r_mean = np.asarray(data["r_mean_v"], dtype=float)
    r_std = np.asarray(data["r_std_v"], dtype=float)
    order = np.asarray(data["acquisition_order"], dtype=int)

    warnings = [
        "最佳 XYZ 组合未在扫描结束后额外复测。",
        "结果是 X/Y 电压与 GS200 电流设定值，未换算为绝对磁场。",
    ]
    for name, index, axis in (
        ("X", x_index, x_axis),
        ("Y", y_index, y_axis),
        ("Z", z_index, z_axis),
    ):
        if axis.size > 1 and index in {0, axis.size - 1}:
            warnings.append(f"{name} 最小点位于扫描边界。")

    linear_analysis = analyze_xyz_linear_fit(data, results_dir)
    warnings.extend(linear_analysis.get("linear_fit_warnings", []))

    plot_file = _plot_xy_planes(results_dir, data, best_index)
    csv_file = _write_csv(results_dir, data)
    npz_file = "balance_results.npz"
    np.savez(
        results_dir / npz_file,
        best_z_index=np.int64(z_index),
        best_x_index=np.int64(x_index),
        best_y_index=np.int64(y_index),
        best_z_field_ma=np.float64(z_axis[z_index]),
        best_x_field_v=np.float64(x_axis[x_index]),
        best_y_field_v=np.float64(y_axis[y_index]),
        best_r_mean_v=np.float64(r_mean[best_index]),
        best_r_std_v=np.float64(r_std[best_index]),
        best_acquisition_index=np.int64(order[best_index]),
    )

    result = {
        "experiment_id": EXPERIMENT_ID,
        "run_dir": str(run_dir),
        "scan_completed": config.get("completion_status") == "completed",
        "scan_shape_zyx": [
            int(z_axis.size),
            int(x_axis.size),
            int(y_axis.size),
        ],
        "scan_order": "Z outer, continuous X/Y serpentine",
        "criterion": "minimum measured arithmetic mean of HF2 Demod0 R",
        "measured_grid_minimum": {
            "z_index": z_index,
            "x_index": x_index,
            "y_index": y_index,
            "z_field_ma": float(z_axis[z_index]),
            "x_field_v": float(x_axis[x_index]),
            "y_field_v": float(y_axis[y_index]),
            "r_mean_v": float(r_mean[best_index]),
            "r_std_v": float(r_std[best_index]),
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
        "control_source": config.get("control_source", {}),
        "applied_control": config.get("applied_control", {}),
        "linear_fit": linear_analysis.get("linear_fit", {}),
        "interpretation": {
            "continuous_fit_performed": True,
            "fit_method": "per_layer_1d_vshape",
            "field_calibration_performed": False,
            "absolute_field_reported": False,
        },
        "warnings": warnings,
        "files": [
            npz_file,
            csv_file,
            plot_file,
            *linear_analysis.get("linear_fit_files", []),
        ],
    }
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
    best = result["measured_grid_minimum"]
    print(
        "Mx Z 最优控制 XYZ 平衡场分析完成: "
        f"X={best['x_field_v']:+.6g} V, "
        f"Y={best['y_field_v']:+.6g} V, "
        f"Z={best['z_field_ma']:+.6g} mA"
    )
    fitted = result.get("linear_fit", {}).get("fitted_workpoint", {})
    if fitted:
        print(
            "V 形拟合工作点: "
            f"X={fitted['x_field_v']:+.6g} V, "
            f"Y={fitted['y_field_v']:+.6g} V, "
            f"Z={fitted['z_field_ma']:+.6g} mA"
            f"（外推: {fitted.get('extrapolated')}）"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
