"""原相位图、逐 X 曲线和非线性参数图；英文标注和 paper 配置。"""
from pathlib import Path
from typing import Any
from matplotlib import cm, colors
from matplotlib.lines import Line2D
import numpy as np
from ...plotting import format_axis, new_figure, save_figure, set_plot_style


def _plot_nonlinear_maps(
    results_dir: Path,
    data: dict[str, np.ndarray],
    fit: dict[str, Any],
) -> str:
    """绘制非线性色散模型的 XY 参数地图。"""
    set_plot_style("paper")
    x_axis = np.asarray(data["x_field_v"], dtype=float)
    y_axis = np.asarray(data["y_field_v"], dtype=float)
    accepted = np.asarray(fit["fit_accepted"], dtype=bool)
    fields = (
        ("selected_phase_deg", "Selected RF phase (deg)", "twilight"),
        ("residual_amplitude_vpp", "Residual RF amplitude (Vpp)", "plasma"),
        ("resonance_amplitude_vpp", "Resonance amplitude (Vpp)", "cividis"),
        ("width_vpp", "Dispersion width (Vpp)", "magma"),
        ("r_squared", "Nonlinear fit R squared", "inferno"),
        ("fit_accepted", "Nonlinear fit accepted", "gray"),
    )
    fig, axes = new_figure(nrows=3, ncols=2, constrained_layout=True, width_mm=177.8, height_mm=165)
    axes = np.asarray(axes, dtype=object).reshape(3, 2)
    for axis, (key, label, cmap) in zip(axes.flat, fields):
        if key == "fit_accepted":
            values = accepted.astype(float)
        elif key == "selected_phase_deg":
            values = np.asarray(fit[key], dtype=float)
        else:
            values = np.asarray(
                fit["parameters"].get(key, fit.get(key)),
                dtype=float,
            )
        if key != "fit_accepted":
            values = np.where(accepted, values, np.nan)
        mesh = axis.pcolormesh(
            x_axis,
            y_axis,
            values.T,
            shading="auto",
            cmap=cmap, rasterized=True,
            vmin=0 if key in {"selected_phase_deg", "fit_accepted"} else None,
            vmax=360 if key == "selected_phase_deg" else (1 if key == "fit_accepted" else None))
        format_axis(
            axis,
            xlabel="X field setting (V)",
            ylabel="Y field / RF offset (V)",
        )
        axis.set_title(label)
        fig.colorbar(mesh, ax=axis, shrink=0.9)
    filename = "xy_rf_phase_response_nonlinear_maps.png"
    save_figure(fig, results_dir / filename)
    return filename


def _plot_maps(results_dir: Path, data: dict[str, np.ndarray]) -> str:
    set_plot_style("paper")
    x_axis = np.asarray(data["x_field_v"], dtype=float)
    y_axis = np.asarray(data["y_field_v"], dtype=float)
    accepted = np.asarray(data["fit_accepted"], dtype=bool)
    fields = (
        ("selected_phase_deg", "Selected RF phase (deg)", "twilight"),
        ("amplitude_v", "Primary amplitude (V)", "plasma"),
        ("baseline_v", "Primary baseline (V)", "viridis"),
        ("r_squared", "Primary fit R squared", "magma"),
        ("fit_accepted", "Fit accepted", "gray"),
    )
    fig, axes = new_figure(nrows=3, ncols=2, constrained_layout=True, width_mm=177.8, height_mm=165)
    axes = np.asarray(axes, dtype=object).reshape(3, 2)
    for axis, (key, label, cmap) in zip(axes.flat, fields):
        values = accepted.astype(float) if key == "fit_accepted" else np.asarray(data[key], dtype=float)
        if key != "fit_accepted":
            values = np.where(accepted, values, np.nan)
        mesh = axis.pcolormesh(x_axis, y_axis, values.T, shading="auto", cmap=cmap, rasterized=True,
            vmin=0 if key in {"selected_phase_deg", "fit_accepted"} else None,
            vmax=360 if key == "selected_phase_deg" else (1 if key == "fit_accepted" else None))
        format_axis(axis, xlabel="X field setting (V)", ylabel="Y field / RF offset (V)")
        axis.set_title(label)
        fig.colorbar(mesh, ax=axis, shrink=0.9)
    axes[2, 1].axis("off")
    filename = "xy_rf_phase_response_maps.png"
    save_figure(fig, results_dir / filename)
    return filename


def _plot_phase_curves(results_dir: Path, data: dict[str, np.ndarray]) -> str:
    """绘制所有 XY 偏置下的 RF 相位响应曲线。

    聚合文件按实际触发顺序保存相位；绘图时按相位数值重排，避免把
    180° 配对采集顺序误画成相位曲线。正式拟合拒绝的网格点使用虚线，
    但仍保留原始曲线，便于定位异常相位点。
    """
    set_plot_style("paper")
    x_axis = np.asarray(data["x_field_v"], dtype=float)
    y_axis = np.asarray(data["y_field_v"], dtype=float)
    phase_raw = np.asarray(data["acquisition_phase_deg"], dtype=float)
    phase_order = np.argsort(np.mod(phase_raw, 360.0), kind="stable")
    phase = np.mod(phase_raw[phase_order], 360.0)
    r_mean = np.asarray(data["r_mean_v"], dtype=float)[..., phase_order]
    accepted = np.asarray(data["fit_accepted"], dtype=bool)

    columns = min(3, x_axis.size)
    rows = int(np.ceil(x_axis.size / columns))
    fig, axes = new_figure(nrows=rows, ncols=columns, sharex=True, sharey=True,
                           squeeze=False, constrained_layout=False, width_mm=177.8,
                           height_mm=max(75, 55 * rows))
    y_min = float(np.nanmin(r_mean))
    y_max = float(np.nanmax(r_mean))
    y_padding = max(0.06 * (y_max - y_min), 0.01)
    y_min -= y_padding
    y_max += y_padding
    y_low = float(np.min(y_axis))
    y_high = float(np.max(y_axis))
    if np.isclose(y_low, y_high):
        y_low -= 0.5
        y_high += 0.5
    norm = colors.Normalize(vmin=y_low, vmax=y_high)

    for panel, x_index in enumerate(range(x_axis.size)):
        row, column = divmod(panel, columns)
        axis = axes[row, column]
        for y_index, y_value in enumerate(y_axis):
            axis.plot(
                phase,
                r_mean[x_index, y_index],
                color=cm.viridis(norm(float(y_value))),
                linestyle="-" if accepted[x_index, y_index] else "--",
                marker="o",
                markersize=2.0,
                linewidth=1.1,
                alpha=0.9 if accepted[x_index, y_index] else 0.7,
            )
        rejected_count = int(np.count_nonzero(~accepted[x_index]))
        title = f"X = {x_axis[x_index]:+.2f} V"
        if rejected_count:
            title += f"\nRejected: {rejected_count}"
        axis.set_title(title)
        axis.set_xlim(0.0, 360.0)
        axis.set_ylim(y_min, y_max)
        axis.set_xticks(np.arange(0.0, 361.0, 120.0))
        axis.grid(True, alpha=0.22, linewidth=0.5)
        format_axis(
            axis,
            xlabel="RF phase (deg)" if row == rows - 1 else "",
            ylabel="Demod0 R (V)" if column == 0 else "",
        )

    for axis in axes.flat[x_axis.size:]:
        axis.axis("off")

    fig.subplots_adjust(
        left=0.06,
        right=0.87,
        top=0.95,
        bottom=0.09,
        wspace=0.16,
        hspace=0.28,
    )
    scalar_mappable = cm.ScalarMappable(norm=norm, cmap="viridis")
    scalar_mappable.set_array(y_axis)
    colorbar_axis = fig.add_axes((0.895, 0.16, 0.018, 0.70))
    colorbar = fig.colorbar(
        scalar_mappable,
        cax=colorbar_axis,
    )
    colorbar.set_label("Y offset / RF DC offset (V)")
    colorbar.set_ticks(y_axis)
    colorbar.ax.set_yticklabels([f"{value:+.2f}" for value in y_axis])
    handles = [
        Line2D([0], [0], color="0.25", linestyle="-", linewidth=1.4, label="Fit accepted"),
        Line2D([0], [0], color="0.25", linestyle="--", linewidth=1.4, label="Fit rejected"),
    ]
    fig.legend(
        handles=handles,
        loc="lower center",
        bbox_to_anchor=(0.5, 0.012),
        ncol=2,
        frameon=False,
    )
    fig.suptitle("RF phase response across XY bias", y=0.995)
    filename = "xy_rf_phase_response_curves.png"
    save_figure(fig, results_dir / filename)
    return filename
