"""原投影噪声的英文 PSD 与洛伦兹图；使用公共 paper 配置。"""
from __future__ import annotations
from pathlib import Path
from typing import Any
import matplotlib
matplotlib.use("Agg")
import numpy as np
from ...plotting import (COLOR_GRAY, COLOR_OPTIMAL, COLOR_TRAD, PAPER_WIDE, format_axis,
    new_figure, save_figure, style_legend)


def _plot_overview(
    results_dir: Path,
    frequency_hz: np.ndarray,
    off_psd: np.ndarray | None,
    on_psd: np.ndarray,
    fit_psd: np.ndarray,
    predicted_center_hz: float,
    *,
    fit_input_label: str,
) -> str:
    filename = "scope_psd_overview.png"
    positive = frequency_hz > 0.0
    fig, axes = new_figure(
        figsize=(PAPER_WIDE[0], 2.0 * PAPER_WIDE[1]),
        nrows=2,
        ncols=1,
        sharex=True,
    )
    if off_psd is not None:
        axes[0].semilogy(
            frequency_hz[positive] / 1e3,
            off_psd[positive],
            color=COLOR_GRAY,
            label="GS200 OFF",
        )
    axes[0].semilogy(
        frequency_hz[positive] / 1e3,
        on_psd[positive],
        color=COLOR_OPTIMAL,
        label="GS200 ON",
    )
    axes[0].axvline(
        predicted_center_hz / 1e3,
        color=COLOR_TRAD,
        linestyle="--",
        label="Predicted center",
    )
    format_axis(axes[0], ylabel="PSD (V²/Hz)")
    axes[0].set_title("Scope PD Noise Spectra")
    style_legend(axes[0])
    axes[1].plot(
        frequency_hz[positive] / 1e3,
        fit_psd[positive],
        color=COLOR_OPTIMAL,
    )
    axes[1].axhline(0.0, color=COLOR_GRAY)
    axes[1].axvline(
        predicted_center_hz / 1e3,
        color=COLOR_TRAD,
        linestyle="--",
    )
    format_axis(
        axes[1],
        xlabel="Frequency (kHz)",
        ylabel=fit_input_label,
    )
    save_figure(fig, results_dir / filename)
    return filename


def _plot_fit(
    results_dir: Path,
    frequency_hz: np.ndarray,
    off_psd: np.ndarray | None,
    on_psd: np.ndarray,
    fit_psd: np.ndarray,
    fit: dict[str, Any],
    *,
    fit_input_label: str,
) -> str:
    filename = "spin_noise_lorentzian_fit.png"
    mask = np.asarray(fit["fit_mask"], dtype=bool)
    x = frequency_hz[mask]
    fig, axes = new_figure(
        figsize=(PAPER_WIDE[0], 3.0 * PAPER_WIDE[1]),
        nrows=3,
        ncols=1,
        sharex=True,
    )
    if off_psd is not None:
        axes[0].semilogy(
            x / 1e3,
            off_psd[mask],
            color=COLOR_GRAY,
            label="GS200 OFF",
        )
    axes[0].semilogy(
        x / 1e3,
        on_psd[mask],
        color=COLOR_OPTIMAL,
        label="GS200 ON",
    )
    format_axis(axes[0], ylabel="PSD (V²/Hz)")
    axes[0].set_title("Spin-Noise Fit Window")
    style_legend(axes[0])
    axes[1].plot(
        x / 1e3,
        fit_psd[mask],
        ".",
        color=COLOR_OPTIMAL,
        label=fit_input_label,
    )
    if fit.get("success"):
        axes[1].plot(
            np.asarray(fit["frequency_hz"]) / 1e3,
            np.asarray(fit["fitted_psd_v2_hz"]),
            color=COLOR_TRAD,
            label=(
                f"Lorentzian: f0={fit['center_hz']/1e3:.3f} kHz, "
                f"FWHM={fit['fwhm_hz']:.1f} Hz"
            ),
        )
    format_axis(axes[1], ylabel=fit_input_label)
    style_legend(axes[1])
    if fit.get("success"):
        axes[2].plot(
            np.asarray(fit["frequency_hz"]) / 1e3,
            np.asarray(fit["residual_psd_v2_hz"]),
        )
    else:
        axes[2].text(0.5, 0.5, "Fit failed (see analysis report)", ha="center", va="center", transform=axes[2].transAxes)
    axes[2].axhline(0.0, color=COLOR_GRAY)
    format_axis(
        axes[2],
        xlabel="Frequency (kHz)",
        ylabel="Residual (V²/Hz)",
    )
    save_figure(fig, results_dir / filename)
    return filename
