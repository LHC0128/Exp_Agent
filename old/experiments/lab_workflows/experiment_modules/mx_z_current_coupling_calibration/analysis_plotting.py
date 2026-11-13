"""Mx Z 实际电流标定的科学绘图。"""
from pathlib import Path
from typing import Any

import numpy as np

from ...plotting import format_axis, new_figure, save_figure, set_plot_style
from ..mx_z_field_calibration.analysis_plotting import _plot_frequency_responses


def _plot_calibration(results_dir: Path, curves: list[dict[str, Any]], fit: dict[str, Any]) -> str:
    set_plot_style("paper")
    current = np.asarray([curve["current_a"] for curve in curves], dtype=float)
    centers = np.asarray([curve["fit"]["parameters"][2] for curve in curves], dtype=float)
    sigma = np.asarray([curve["fit"]["center_uncertainty_hz"] for curve in curves], dtype=float)
    accepted = np.asarray([curve["fit"]["success"] for curve in curves], dtype=bool)
    figure, axis = new_figure()
    if np.any(accepted):
        axis.errorbar(current[accepted], centers[accepted], yerr=sigma[accepted], fmt="o", capsize=2, label="Accepted centers")
    excluded = ~accepted & np.isfinite(current) & np.isfinite(centers)
    if np.any(excluded):
        axis.plot(current[excluded], centers[excluded], "x", label="Excluded centers")
    if not np.any(accepted):
        axis.text(0.5, 0.95, "No accepted resonance centers", transform=axis.transAxes,
                  ha="center", va="top")
    if fit["success"]:
        grid = np.linspace(float(np.min(current)), float(np.max(current)), 400)
        axis.plot(
            grid,
            fit["slope_hz_per_v"] * grid + fit["intercept_hz"],
            ls="--",
            label=f"Linear fit, R2={fit['r_squared']:.5f}",
        )
    format_axis(axis, xlabel="Measured coil current (A)", ylabel="Resonance center (Hz)")
    axis.grid(True, alpha=0.25)
    if axis.get_legend_handles_labels()[0]:
        axis.legend(loc="best")
    filename = "current_coupling_calibration.png"
    save_figure(figure, results_dir / filename)
    return filename
