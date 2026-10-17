"""Mx Z 最优控制 XY 偏置噪声谱科研图。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import matplotlib
import numpy as np

from ...plotting import (
    COLOR_OPTIMAL,
    COLOR_TRAD,
    format_axis,
    new_figure,
    save_figure,
    set_plot_style,
    style_legend,
)
from .models import MxZOptimalControlXYNoiseSpectrumParams

matplotlib.use(os.environ.get("MPLBACKEND", "Agg"))


def _plot_heatmap(
    results_dir: Path,
    x_axis: np.ndarray,
    y_axis: np.ndarray,
    matrix: np.ndarray,
    best: dict[str, Any],
) -> str:
    set_plot_style("paper")
    figure, axis = new_figure(kind="square")
    image = axis.pcolormesh(x_axis, y_axis, matrix.T, shading="auto", cmap="viridis", rasterized=True)
    axis.plot(best["x_field_v"], best["y_field_v"], "x", color=COLOR_TRAD, ms=8, mew=1.8, label="Minimum")
    format_axis(axis, xlabel="X DC bias (V)", ylabel="Y DC bias (V)")
    axis.set_title("R ASD median in selected band")
    figure.colorbar(image, ax=axis, label="ASD median (V/$\\sqrt{\\mathrm{Hz}}$)")
    style_legend(axis)
    filename = "noise_median_heatmap.png"
    save_figure(figure, results_dir / filename)
    return filename



def _plot_best_spectrum(
    results_dir: Path,
    frequency: np.ndarray,
    asd: np.ndarray,
    params: MxZOptimalControlXYNoiseSpectrumParams,
    best: dict[str, Any],
) -> str:
    set_plot_style("paper")
    figure, axis = new_figure()
    positive = (frequency > 0) & np.isfinite(asd) & (asd > 0)
    axis.loglog(frequency[positive], asd[positive], color=COLOR_OPTIMAL, label="Best point")
    axis.axvspan(params.low_freq_skip_hz, params.noise_band_max_hz, color="0.8", alpha=0.35, label="Statistics band")
    format_axis(axis, xlabel="Frequency (Hz)", ylabel="R ASD (V/$\\sqrt{\\mathrm{Hz}}$)")
    axis.set_title(f"Best point: X={best['x_field_v']:+.4f} V, Y={best['y_field_v']:+.4f} V")
    style_legend(axis)
    filename = "noise_spectrum_best.png"
    save_figure(figure, results_dir / filename)
    return filename
