"""逐层一维 V 形/复线性模拟合、耦合与工作点诊断图。"""

from pathlib import Path
from typing import Any

import numpy as np

from ...plotting import (
    COLOR_GREEN,
    COLOR_OPTIMAL,
    COLOR_ORANGE,
    COLOR_TRAD,
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


def _axis_step(axis: np.ndarray) -> float | None:
    """返回扫描轴步长；单点轴返回 None。"""
    axis = np.asarray(axis, dtype=float)
    if axis.size < 2:
        return None
    return float(np.median(np.diff(axis)))


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
