"""Z RF 触发相位扫描诊断图；拟合失败时仍绘制实测曲线。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import matplotlib
import numpy as np

from ...plotting import (
    COLOR_GRAY,
    COLOR_GREEN,
    COLOR_OPTIMAL,
    COLOR_PURPLE,
    COLOR_TRAD,
    format_axis,
    new_figure,
    save_figure,
    set_plot_style,
    style_legend,
)

matplotlib.use(os.environ.get("MPLBACKEND", "Agg"))


def _r_squared_label(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "n/a"
    return f"{number:.3f}" if np.isfinite(number) else "n/a"


def plot_z_phase_scan(raw_dir: Path, results_dir: Path, payload: dict[str, Any]) -> str:
    """上幅绘制 R/X/Y 随 Z RF Burst 相位的变化，下幅绘制成对正交拟合。

    ``payload`` 提供 ``quadrature_fit`` 与 ``selected_phase_deg`` 时绘制
    拟合与选定相位；缺失（拟合失败）时仅绘制实测曲线。
    """
    with np.load(Path(raw_dir) / "z_phase_scan.npz") as data:
        phase_deg = np.asarray(data["scanned_phase_deg"], dtype=float)
        r_mean_v = np.asarray(data["r_mean_v"], dtype=float)
        r_std_v = np.asarray(data["r_std_v"], dtype=float)
        x_mean_v = np.asarray(data["x_mean_v"], dtype=float)
        x_std_v = np.asarray(data["x_std_v"], dtype=float)
        y_mean_v = np.asarray(data["y_mean_v"], dtype=float)
        y_std_v = np.asarray(data["y_std_v"], dtype=float)

    fit = payload.get("quadrature_fit", {})
    selected = payload.get("selected_phase_deg", float("nan"))

    set_plot_style("paper")
    fig, axes = new_figure(nrows=2, figsize=(8.0, 7.2), sharex=False)
    ax_signal, ax_pair = axes

    order = np.argsort(np.mod(phase_deg, 360.0))
    phase_sorted = np.mod(phase_deg[order], 360.0)
    ax_signal.errorbar(phase_sorted, r_mean_v[order], yerr=r_std_v[order], fmt="^", color=COLOR_GREEN, capsize=2, label="Demod R")
    ax_signal.errorbar(phase_sorted, x_mean_v[order], yerr=x_std_v[order], fmt="o", color=COLOR_OPTIMAL, capsize=2, label="Demod X")
    ax_signal.errorbar(phase_sorted, y_mean_v[order], yerr=y_std_v[order], fmt="s", color=COLOR_PURPLE, capsize=2, label="Demod Y")
    format_axis(ax_signal, xlabel="Z RF burst phase (deg)", ylabel="RF demodulator mean (V)")
    ax_signal.set_xlim(0.0, 360.0)
    style_legend(ax_signal)

    if fit:
        with np.load(Path(raw_dir) / "z_phase_scan.npz") as data:
            pair_phase_deg = np.asarray(data["paired_phase_deg"], dtype=float)
            pair_in_phase_v = np.asarray(data["pair_in_phase_v"], dtype=float)
            pair_quadrature_v = np.asarray(data["pair_quadrature_v"], dtype=float)
        dense = np.linspace(0.0, 360.0, 1441)
        radians = np.deg2rad(dense)
        in_phase_fit = float(fit["fit_cos_coefficient_v"]) * np.cos(radians) + float(fit["fit_sin_coefficient_v"]) * np.sin(radians)
        quadrature_fit = float(fit["quadrature_cos_coefficient_v"]) * np.cos(radians) + float(fit["quadrature_sin_coefficient_v"]) * np.sin(radians)
        ax_pair.plot(pair_phase_deg, pair_in_phase_v, "o", color=COLOR_OPTIMAL, label="Paired in-phase")
        ax_pair.plot(pair_phase_deg, pair_quadrature_v, "s", color=COLOR_PURPLE, label="Paired quadrature")
        ax_pair.plot(dense, in_phase_fit, "--", color=COLOR_TRAD, label=f"In-phase fit ($R^2$={_r_squared_label(fit.get('r_squared'))})")
        ax_pair.plot(dense, quadrature_fit, ":", color=COLOR_PURPLE, label="Quadrature fit")
        ax_pair.axhline(0.0, color=COLOR_GRAY, lw=0.8)
        if np.isfinite(float(selected)):
            status = "Selected" if payload.get("success") is not False else "Candidate"
            ax_pair.axvline(float(selected), color=COLOR_GRAY, ls="-.", label=f"{status}: {float(selected):.2f} deg")
        format_axis(ax_pair, xlabel="Z RF burst phase (deg)", ylabel="Baseline-referenced response (V)")
        ax_pair.set_xlim(0.0, 360.0)
        style_legend(ax_pair)
    else:
        ax_pair.text(0.5, 0.5, "Quadrature fit unavailable", ha="center", va="center", transform=ax_pair.transAxes, color=COLOR_GRAY)
        format_axis(ax_pair, xlabel="Z RF burst phase (deg)", ylabel="Baseline-referenced response (V)")
        ax_pair.set_xlim(0.0, 360.0)

    filename = "z_phase_scan.png"
    save_figure(fig, Path(results_dir) / filename)
    return filename
