"""R 均值和标准差二维热图；沿用原英文标注与 paper 出图。"""
import numpy as np
from ...plotting import format_axis, new_figure, save_figure, set_plot_style


def _plot_response(results_dir, data, best_index):
    set_plot_style("paper")
    x_axis = np.asarray(data["x_signed_amplitude_vpp"], dtype=float)
    y_axis = np.asarray(data["y_signed_amplitude_vpp"], dtype=float)
    fig, axes = new_figure(nrows=1, ncols=2, kind="wide", constrained_layout=True)
    mean_mesh = axes[0].pcolormesh(x_axis, y_axis, data["r_mean_v"].T, shading="auto", cmap="viridis", rasterized=True)
    axes[0].plot(float(x_axis[best_index[0]]), float(y_axis[best_index[1]]), marker="x", color="white",
        markersize=7, markeredgewidth=1.4, label="Measured minimum")
    format_axis(axes[0], xlabel="X control signed amplitude (Vpp)", ylabel="Y control signed amplitude (Vpp)")
    axes[0].legend(loc="best")
    fig.colorbar(mean_mesh, ax=axes[0]).set_label("Mean R (V)")
    std_mesh = axes[1].pcolormesh(x_axis, y_axis, data["r_std_v"].T, shading="auto", cmap="magma", rasterized=True)
    format_axis(axes[1], xlabel="X control signed amplitude (Vpp)", ylabel="Y control signed amplitude (Vpp)")
    fig.colorbar(std_mesh, ax=axes[1]).set_label("R standard deviation (V)")
    filename = "xy_leakage_response.png"
    save_figure(fig, results_dir / filename)
    return filename
