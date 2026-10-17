"""Mx Y RF 频率响应与饱和度诊断图；使用公共 paper 绘图样式。"""
from pathlib import Path
from typing import Any
import os
import matplotlib
import numpy as np
from ...plotting import (
    COLOR_GRAY, COLOR_OPTIMAL, COLOR_TRAD, PAPER_WIDE, blues_gradient,
    format_axis, new_figure, save_figure, set_plot_style, style_legend,
)
from .analysis_core import bloch_response

matplotlib.use(os.environ.get("MPLBACKEND", "Agg"))


def _plot_frequency_response(
    *,
    results_dir: Path,
    frequency_hz: np.ndarray,
    amplitude_vpp: np.ndarray,
    r_mean_v: np.ndarray,
    fit_mask: np.ndarray,
    fit_parameters: np.ndarray,
) -> None:
    """叠加绘制各幅度曲线及其布洛赫拟合。"""
    set_plot_style("paper")
    fig, ax = new_figure(figsize=PAPER_WIDE)
    colors = blues_gradient(int(amplitude_vpp.size))
    dense_frequency = np.linspace(
        float(np.nanmin(frequency_hz)),
        float(np.nanmax(frequency_hz)),
        1000,
    )
    for index, amplitude in enumerate(amplitude_vpp):
        mask = fit_mask[index]
        color = colors[index] if amplitude_vpp.size > 1 else COLOR_OPTIMAL
        label = f"{amplitude:.3g} Vpp"
        ax.plot(
            frequency_hz[mask],
            r_mean_v[index][mask],
            "o",
            color=color,
            markersize=3,
            label=label if amplitude_vpp.size > 1 else None,
        )
        parameters = fit_parameters[index]
        if np.all(np.isfinite(parameters)):
            ax.plot(
                dense_frequency,
                bloch_response(dense_frequency, *parameters),
                "-",
                color=color,
                lw=1.2,
            )
    if amplitude_vpp.size == 1 and np.all(np.isfinite(fit_parameters[0])):
        amplitude, gamma, center, _, saturation = fit_parameters[0]
        summary = (
            f"f$_0$={center:.0f} Hz, $\\gamma$={gamma:.0f} Hz, "
            f"S={saturation:.2f}"
            + (" (split)" if saturation > 1.0 else "")
        )
        ax.text(
            0.03,
            0.95,
            summary,
            transform=ax.transAxes,
            va="top",
            fontsize=8,
        )
    format_axis(
        ax,
        xlabel="Y RF frequency (Hz)",
        ylabel="Demod R (V)",
    )
    if amplitude_vpp.size > 1:
        style_legend(ax, fontsize=7, ncol=2)
    save_figure(fig, results_dir / "frequency_response.png")


def _plot_saturation_scaling(
    *,
    results_dir: Path,
    amplitude_vpp: np.ndarray,
    saturation: np.ndarray,
    scaling: dict[str, Any],
) -> None:
    """绘制 S 与幅度平方的线性诊断图。"""
    set_plot_style("paper")
    fig, ax = new_figure()
    squared = amplitude_vpp**2
    ax.plot(
        squared,
        saturation,
        "o",
        color=COLOR_OPTIMAL,
        label="S vs amplitude$^2$",
    )
    if scaling["success"]:
        slope = scaling["slope_per_vpp2"]
        intercept = scaling["intercept"]
        dense = np.linspace(float(np.min(squared)), float(np.max(squared)), 100)
        ax.plot(
            dense,
            slope * dense + intercept,
            "--",
            color=COLOR_TRAD,
            label=(
                "Linear fit "
                f"(slope={slope:.3g}, $R^2$={scaling['r_squared']:.3f})"
            ),
        )
        ax.axhline(
            1.0,
            color=COLOR_GRAY,
            ls=":",
            alpha=0.5,
            label="Split threshold S=1",
        )
    format_axis(
        ax,
        xlabel="Amplitude squared (V$_{pp}^2$)",
        ylabel="Saturation S",
    )
    style_legend(ax, fontsize=7.5)
    save_figure(fig, results_dir / "s_vs_amplitude.png")
