"""原剩磁标定科研图；只读取已完成的数值分析，不连接硬件。"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any
import numpy as np
from ...plotting import COLOR_ORANGE, COLOR_OPTIMAL, format_axis, new_figure, save_figure, style_legend, set_plot_style
from .fitting import Bloch2DFitResult, DispersiveFitResult, dispersive_line


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
    image = axis.pcolormesh(x_axis, y_axis, matrix.T, **kwargs, rasterized=True)
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
    save_figure(fig, results_dir / filename)
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
    fig, axes_grid = new_figure(ncols=2, subplot_kw={"projection": "3d"}, width_mm=177.8, height_mm=70)
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
        axis.set_title(title, fontsize=8, pad=5.0)
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
    save_figure(fig, results_dir / filename)
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
    save_figure(fig, results_dir / filename)
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
    save_figure(fig, results_dir / filename)
    return filename


def render(run_dir):
    from .analysis_steps import contained, write_reports
    results_dir = contained(Path(run_dir) / "results", run_dir)
    saved = json.loads(contained(results_dir / "xy_residual_fit.json", run_dir).read_text(encoding="utf8"))
    summary = saved["summary"]
    bloch_fit = Bloch2DFitResult(**saved["bloch_fit"])
    dispersive_fits = [DispersiveFitResult(**value) for value in saved["dispersive_fits"]]
    with np.load(contained(results_dir / "calibration_results.npz", run_dir), allow_pickle=False) as values:
        x_axis, y_axis = values["x_field_v"], values["y_field_v"]
        mean_matrix = values["pd_mean_v"]
        drift_corrected_mean_matrix = values["drift_corrected_pd_mean_v"]
        bloch_fit_matrix = values["bloch_fit_surface_v"]
        bloch_residual_matrix = values["bloch_residual_v"]
        time_matrix = values["acquisition_time_unix_s"]
        fitted_drift_matrix = values["fitted_linear_drift_v"]
        best_x_index, best_y_index = int(values["best_x_index"]), int(values["best_y_index"])
        best_x_v, best_y_v = float(values["best_x_field_v"]), float(values["best_y_field_v"])
        index = int(values["dispersive_crosscheck_x_index"])
    dispersive_best_x_index = None if index < 0 else index
    dispersive_best_fit = None if index < 0 else dispersive_fits[index]
    dispersive_best_y_index = None if index < 0 else int(np.argmin(np.abs(y_axis - dispersive_best_fit.center_y_v)))
    set_plot_style("paper")
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
    axis.set_title("Fitted Acquisition Drift", fontsize=8)
    axis.xaxis.label.set_size(8.0)
    axis.yaxis.label.set_size(8.0)
    axis.tick_params(axis="both", which="major", labelsize=7.0)
    style_legend(axis, fontsize=7.0)
    drift_plot = "fitted_time_drift.png"
    save_figure(fig, results_dir / drift_plot)
    plot_files.append(drift_plot)

    summary["files"] = [*summary["files"], *plot_files]
    write_reports(results_dir, summary)
    return summary
