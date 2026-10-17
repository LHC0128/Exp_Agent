"""Mx 高主场 Z 磁场频率标定离线分析。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import matplotlib
import numpy as np

from ...plotting import (
    COLOR_GRAY,
    COLOR_OPTIMAL,
    COLOR_TRAD,
    PAPER_WIDE,
    format_axis,
    new_figure,
    save_figure,
    set_plot_style,
    style_legend,
)
from ..mx_y_rf_sensitivity.analysis_core import lorentzian_response

matplotlib.use(os.environ.get("MPLBACKEND", "Agg"))



def _plot_frequency_responses(
    results_dir: Path, curves: list[dict[str, Any]],
    frequency_label: str = "Y RF frequency (kHz)",
) -> str:
    columns = 2
    rows = int(np.ceil(len(curves) / columns))
    fig, axes = new_figure(
        figsize=(PAPER_WIDE[0], 2.7 * rows),
        nrows=rows,
        ncols=columns,
        squeeze=False,
    )
    for axis, curve in zip(axes.flat, curves, strict=False):
        frequency = curve["frequency_hz"]
        response = curve["r_mean_v"]
        fit = curve["fit"]
        axis.plot(
            frequency / 1e3,
            response,
            "o",
            color=COLOR_OPTIMAL,
            label="Measured R",
        )
        if np.all(np.isfinite(fit["parameters"])):
            dense = np.linspace(float(frequency.min()), float(frequency.max()), 800)
            axis.plot(
                dense / 1e3,
                lorentzian_response(dense, *fit["parameters"]),
                color=COLOR_TRAD,
                linestyle="--",
                label="Lorentzian fit",
            )
        status = "accepted" if fit["success"] else "excluded"
        axis.set_title(
            f"Z bias = {curve['z_bias_v']:+.1f} V ({status})"
        )
        format_axis(
            axis,
            xlabel=frequency_label,
            ylabel="Demod R (V)",
        )
        style_legend(axis)
    for axis in axes.flat[len(curves):]:
        axis.set_visible(False)
    filename = "frequency_response_fits.png"
    save_figure(fig, results_dir / filename)
    return filename

def _plot_calibration(
    results_dir: Path,
    curves: list[dict[str, Any]],
    linear: dict[str, Any],
) -> tuple[str, str]:
    z_all = np.asarray([curve["z_bias_v"] for curve in curves], dtype=float)
    center_all = np.asarray([curve["fit"]["parameters"][2] for curve in curves], dtype=float)
    sigma_all = np.asarray([curve["fit"]["center_uncertainty_hz"] for curve in curves], dtype=float)
    accepted = np.asarray([curve["fit"]["success"] for curve in curves], dtype=bool)

    fig, axis = new_figure()
    if np.any(accepted):
        axis.errorbar(
            z_all[accepted],
            center_all[accepted] / 1e3,
            yerr=sigma_all[accepted] / 1e3,
            fmt="o",
            capsize=3,
            color=COLOR_OPTIMAL,
            label="Accepted centers",
        )
    if np.any(~accepted & np.isfinite(center_all)):
        axis.plot(
            z_all[~accepted],
            center_all[~accepted] / 1e3,
            "x",
            color=COLOR_GRAY,
            label="Excluded centers",
        )
    if linear["success"]:
        grid = np.linspace(float(z_all.min()), float(z_all.max()), 400)
        fitted = linear["slope_hz_per_v"] * grid + linear["intercept_hz"]
        axis.plot(
            grid,
            fitted / 1e3,
            color=COLOR_TRAD,
            linestyle="--",
            label=(
                f"Linear fit: K={linear['slope_hz_per_v']:.3f} Hz/V, "
                f"R²={linear['r_squared']:.5f}"
            ),
        )
    format_axis(
        axis,
        xlabel="Z DC bias (V)",
        ylabel="Resonance center (kHz)",
    )
    style_legend(axis)
    calibration_name = "z_frequency_calibration.png"
    save_figure(fig, results_dir / calibration_name)

    fig, axis = new_figure()
    if linear["success"] and np.any(accepted):
        residual = np.asarray(linear["residual_hz"], dtype=float)
        axis.axhline(0.0, color=COLOR_GRAY, linestyle="--")
        axis.plot(z_all[accepted], residual, "o-", color=COLOR_OPTIMAL)
    format_axis(
        axis,
        xlabel="Z DC bias (V)",
        ylabel="Linear-fit residual (Hz)",
    )
    residual_name = "z_frequency_residuals.png"
    save_figure(fig, results_dir / residual_name)
    return calibration_name, residual_name
