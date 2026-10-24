"""原 R 矩阵、稳健标定与四参数曲线；标注保持英文。"""
import os
import matplotlib
import numpy as np
matplotlib.use(os.environ.get("MPLBACKEND", "Agg"))
from ...plotting import (
    COLOR_GRAY, COLOR_OPTIMAL, COLOR_TRAD, PAPER_WIDE, format_axis, new_figure,
    save_figure, set_plot_style, style_legend,
)
from .analysis_steps import output_path


def render(run_dir, data):
    set_plot_style("paper")
    with np.load(run_dir / "results/calibration.npz", allow_pickle=False) as source:
        cal = {key: source[key] for key in source.files}
    with np.load(run_dir / "results/r_fit_params.npz", allow_pickle=False) as source:
        parameters = source["parameters"]
    frequencies, envelope = data["frequencies"], data["envelope"]
    control = cal["calibrated_control_frequency_Hz"]
    fig, ax = new_figure(kind="square")
    image = ax.imshow(data["matrix"], aspect="auto", origin="lower",
        extent=[float(frequencies[0]), float(frequencies[-1]), float(control[0]), float(control[-1])], cmap="viridis")
    format_axis(ax, xlabel="Demod3 frequency (Hz)", ylabel="Calibrated control frequency (Hz)")
    fig.colorbar(image, ax=ax, label="Mean R (V)")
    save_figure(fig, output_path(run_dir, "results/demod3_r_matrix.png"))

    fig, ax = new_figure()
    inlier = cal["inlier_mask"]
    ax.scatter(cal["ridge_envelope_V"][~inlier], cal["ridge_frequency_Hz"][~inlier],
        color=COLOR_TRAD, label="Rejected ridge points")
    ax.scatter(cal["ridge_envelope_V"][inlier], cal["ridge_frequency_Hz"][inlier],
        color=COLOR_OPTIMAL, label="Calibration ridge points")
    line = np.linspace(float(np.min(envelope)), float(np.max(envelope)), 300)
    ax.plot(line, cal["slope_Hz_per_V"] * line + cal["intercept_Hz"], color=COLOR_GRAY, label="Robust linear fit")
    format_axis(ax, xlabel="DirectAW envelope (V)", ylabel="Ridge frequency (Hz)")
    style_legend(ax)
    save_figure(fig, output_path(run_dir, "results/calibration.png"))

    fig, axes = new_figure(figsize=(PAPER_WIDE[0], 2 * PAPER_WIDE[1]), nrows=2, ncols=2, sharex=True)
    for ax, (index, label) in zip(axes.flat, enumerate(
            ("Linewidth (Hz)", "Fit amplitude", "R baseline (V)", "Frequency offset (Hz)")), strict=True):
        ax.plot(frequencies, parameters[:, index], ".-", color=COLOR_OPTIMAL)
        format_axis(ax, ylabel=label)
    for ax in axes[-1]:
        format_axis(ax, xlabel="Demod3 frequency (Hz)")
    save_figure(fig, output_path(run_dir, "results/r_spectra.png"))
