"""实际电流波形绘图，沿用原双面板并统一 paper 样式。"""
import numpy as np
from ...plotting import format_axis, new_figure, save_figure, set_plot_style
from ..z_aw_waveform_scope_check.analysis_steps import _zscore_rows


def plot_current(results_dir, time_s, measured, reference):
    set_plot_style("paper")
    figure, axes = new_figure(nrows=2, ncols=1, kind="wide", constrained_layout=True)
    time_ms = time_s * 1e3
    axes[0].plot(time_ms, reference[0], label="Target current")
    axes[0].plot(time_ms, np.mean(measured, axis=0), label="Measured current")
    format_axis(axes[0], xlabel="Time (ms)", ylabel="Current (A)")
    axes[0].grid(True, alpha=0.25)
    axes[0].legend(loc="best")
    normalized_measured = _zscore_rows(measured)
    normalized_reference = _zscore_rows(reference)
    axes[1].plot(time_ms, np.mean(normalized_measured, axis=0), label="Measured normalized")
    axes[1].plot(time_ms, normalized_reference[0], label="Target normalized")
    format_axis(axes[1], xlabel="Time (ms)", ylabel="Z-score current")
    axes[1].grid(True, alpha=0.25)
    axes[1].legend(loc="best")
    save_figure(figure, results_dir / "current_waveform_similarity.png")
