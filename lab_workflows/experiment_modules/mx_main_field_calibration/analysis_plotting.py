"""主场标定论文图，保持既有文件名与科学标注。"""
from pathlib import Path
from typing import Any
import os
import matplotlib
matplotlib.use(os.environ.get("MPLBACKEND", "Agg"))
import numpy as np
from ...plotting import (COLOR_GRAY, COLOR_OPTIMAL, COLOR_TRAD, PAPER_WIDE,
    format_axis, new_figure, save_figure, set_plot_style, style_legend)
from ..mx_y_rf_sensitivity.analysis_core import lorentzian_response

def _plot_frequency_responses(
    results_dir: Path, curves: list[dict[str, Any]]
) -> str:
    columns = 2
    rows = max(1, int(np.ceil(len(curves) / columns)))
    fig, axes = new_figure(
        figsize=(PAPER_WIDE[0], 2.7 * rows),
        nrows=rows,
        ncols=columns,
        squeeze=False,
    )
    for axis, curve in zip(axes.flat, curves, strict=False):
        frequency = np.asarray(curve["frequency_hz"], dtype=float)
        response = np.asarray(curve["r_mean_v"], dtype=float)
        fit = curve["fit"]
        axis.plot(
            frequency / 1e3,
            response,
            "o",
            color=COLOR_OPTIMAL,
            label="Measured R",
        )
        if np.all(np.isfinite(fit["parameters"])):
            dense = np.linspace(float(np.nanmin(frequency)), float(np.nanmax(frequency)), 800)
            axis.plot(
                dense / 1e3,
                lorentzian_response(dense, *fit["parameters"]),
                color=COLOR_TRAD,
                linestyle="--",
                label="Lorentzian fit",
            )
        status = "accepted" if fit["success"] else "excluded"
        axis.set_title(
            f"Main-field current = {curve['main_field_current_ma']:.3f} mA ({status})"
        )
        format_axis(
            axis,
            xlabel="Y RF frequency (kHz)",
            ylabel="Demod R (V)",
        )
        style_legend(axis)
    for axis in axes.flat[len(curves) :]:
        axis.set_visible(False)
    filename = "frequency_response_fits.png"
    save_figure(fig, results_dir / filename)
    return filename


def _calibration_fit_label(
    linear: dict[str, Any], gyromagnetic_ratio_hz_per_nt: float
) -> str:
    """生成同时包含频率与磁场截距的英文拟合图例。"""
    slope_hz_per_ma = float(linear["slope_hz_per_ma"])
    intercept_hz = float(linear["intercept_hz"])
    return (
        f"Linear fit: Kf={slope_hz_per_ma:.3f} Hz/mA, "
        f"f0={intercept_hz:.3f} Hz\n"
        f"KB={slope_hz_per_ma / gyromagnetic_ratio_hz_per_nt:.3f} nT/mA, "
        f"B0={intercept_hz / gyromagnetic_ratio_hz_per_nt:.3f} nT, "
        f"R²={linear['r_squared']:.5f}"
    )


def _plot_calibration(
    results_dir: Path,
    curves: list[dict[str, Any]],
    linear: dict[str, Any],
    gyromagnetic_ratio_hz_per_nt: float,
) -> tuple[str, str]:
    current_all = np.asarray(
        [curve["main_field_current_ma"] for curve in curves], dtype=float
    )
    center_all = np.asarray(
        [curve["fit"]["parameters"][2] for curve in curves], dtype=float
    )
    sigma_all = np.asarray(
        [curve["fit"]["center_uncertainty_hz"] for curve in curves], dtype=float
    )
    accepted = np.asarray([curve["fit"]["success"] for curve in curves], dtype=bool)

    fig, axis = new_figure()
    if np.any(accepted):
        axis.errorbar(
            current_all[accepted],
            center_all[accepted] / 1e3,
            yerr=sigma_all[accepted] / 1e3,
            fmt="o",
            capsize=3,
            color=COLOR_OPTIMAL,
            label="Accepted centers",
        )
    if np.any(~accepted & np.isfinite(center_all)):
        axis.plot(
            current_all[~accepted],
            center_all[~accepted] / 1e3,
            "x",
            color=COLOR_GRAY,
            label="Excluded centers",
        )
    if linear["success"]:
        grid = np.linspace(float(current_all.min()), float(current_all.max()), 400)
        fitted = linear["slope_hz_per_ma"] * grid + linear["intercept_hz"]
        axis.plot(
            grid,
            fitted / 1e3,
            color=COLOR_TRAD,
            linestyle="--",
            label=_calibration_fit_label(linear, gyromagnetic_ratio_hz_per_nt),
        )
    format_axis(
        axis,
        xlabel="GS200 current (mA)",
        ylabel="Resonance center (kHz)",
    )
    style_legend(axis)
    secondary = axis.secondary_yaxis(
        "right",
        functions=(
            lambda frequency_khz: frequency_khz / gyromagnetic_ratio_hz_per_nt,
            lambda magnetic_field_ut: magnetic_field_ut * gyromagnetic_ratio_hz_per_nt,
        ),
    )
    secondary.set_ylabel("Magnetic field (µT)")
    calibration_name = "main_field_frequency_calibration.png"
    save_figure(fig, results_dir / calibration_name)

    fig, axis = new_figure()
    if linear["success"] and np.any(accepted):
        residual = np.asarray(linear["residual_hz"], dtype=float)
        axis.axhline(0.0, color=COLOR_GRAY, linestyle="--")
        axis.plot(
            current_all[accepted],
            residual,
            "o-",
            color=COLOR_OPTIMAL,
        )
    format_axis(
        axis,
        xlabel="GS200 current (mA)",
        ylabel="Linear-fit residual (Hz)",
    )
    residual_name = "main_field_frequency_residuals.png"
    save_figure(fig, results_dir / residual_name)
    return calibration_name, residual_name
