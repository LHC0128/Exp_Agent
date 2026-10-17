"""保留三维 R/X/Y 网格与 Bloch 诊断的原科学图和 CSV 格式。"""
import csv
from pathlib import Path
from typing import Any
import numpy as np
from ...plotting import COLOR_TRAD, PAPER_WIDE, format_axis, new_figure, save_figure, set_plot_style

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
