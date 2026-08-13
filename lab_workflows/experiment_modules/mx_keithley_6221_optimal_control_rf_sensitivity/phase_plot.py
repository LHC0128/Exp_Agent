"""Y RF 触发相位校准诊断图。"""

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
    figure_size,
    format_axis,
    new_figure,
    save_figure,
    set_plot_style,
    style_legend,
)
from .phase import inside_absolute_sine, outside_absolute_sine

matplotlib.use(os.environ.get("MPLBACKEND", "Agg"))


def _finite_parameters(fit: dict[str, Any]) -> tuple[float, float, float] | None:
    values = np.asarray(fit.get("parameters", ()), dtype=float).reshape(-1)
    if values.size != 3 or not np.all(np.isfinite(values)):
        return None
    return tuple(float(value) for value in values)


def _r_squared_label(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "n/a"
    return f"{number:.3f}" if np.isfinite(number) else "n/a"


def _plot_quadrature_phase_calibration(
    raw_dir: Path,
    results_dir: Path,
    payload: dict[str, Any],
) -> str:
    """绘制同步 X/Y 与基线参考的成对同相/正交响应。"""
    with np.load(Path(raw_dir) / "phase_scan.npz") as data:
        phase_deg = np.asarray(data["scanned_phase_deg"], dtype=float)
        r_mean_v = np.asarray(data["r_mean_v"], dtype=float)
        r_std_v = np.asarray(data["r_std_v"], dtype=float)
        x_mean_v = np.asarray(data["x_mean_v"], dtype=float)
        x_std_v = np.asarray(data["x_std_v"], dtype=float)
        y_mean_v = np.asarray(data["y_mean_v"], dtype=float)
        y_std_v = np.asarray(data["y_std_v"], dtype=float)
        pair_phase_deg = np.asarray(data["paired_phase_deg"], dtype=float)
        pair_in_phase_v = np.asarray(
            data["pair_in_phase_v"],
            dtype=float,
        )
        pair_quadrature_v = np.asarray(
            data["pair_quadrature_v"],
            dtype=float,
        )
        reacquired = (
            np.asarray(data["cross_phase_reacquired"], dtype=bool)
            if "cross_phase_reacquired" in data
            else np.zeros(phase_deg.size, dtype=bool)
        )

    fit = payload.get("quadrature_fit", {})
    cos_coefficient = float(fit["fit_cos_coefficient_v"])
    sin_coefficient = float(fit["fit_sin_coefficient_v"])
    q_cos_coefficient = float(fit["quadrature_cos_coefficient_v"])
    q_sin_coefficient = float(fit["quadrature_sin_coefficient_v"])
    selected = float(fit["selected_phase_deg"])
    dense = np.linspace(0.0, 360.0, 1441)
    radians = np.deg2rad(dense)
    in_phase_fit = (
        cos_coefficient * np.cos(radians)
        + sin_coefficient * np.sin(radians)
    )
    quadrature_fit = (
        q_cos_coefficient * np.cos(radians)
        + q_sin_coefficient * np.sin(radians)
    )

    set_plot_style("paper")
    width, height = figure_size("wide")
    fig, axes = new_figure(
        nrows=2,
        figsize=(width, height * 1.55),
        sharex=False,
    )
    ax_xy, ax_pair = axes
    order = np.argsort(np.mod(phase_deg, 360.0))
    ax_xy.errorbar(
        np.mod(phase_deg[order], 360.0),
        r_mean_v[order],
        yerr=r_std_v[order],
        fmt="^",
        color=COLOR_GREEN,
        capsize=2,
        label="Demod R",
    )
    ax_xy.errorbar(
        np.mod(phase_deg[order], 360.0),
        x_mean_v[order],
        yerr=x_std_v[order],
        fmt="o",
        color=COLOR_OPTIMAL,
        capsize=2,
        label="Demod X",
    )
    ax_xy.errorbar(
        np.mod(phase_deg[order], 360.0),
        y_mean_v[order],
        yerr=y_std_v[order],
        fmt="s",
        color=COLOR_PURPLE,
        capsize=2,
        label="Demod Y",
    )
    if np.any(reacquired):
        retry_phase = np.mod(phase_deg[reacquired], 360.0)
        ax_xy.scatter(
            retry_phase,
            r_mean_v[reacquired],
            s=72,
            marker="^",
            facecolors="none",
            edgecolors=COLOR_GRAY,
            linewidths=1.2,
            zorder=4,
        )
        ax_xy.scatter(
            retry_phase,
            x_mean_v[reacquired],
            s=72,
            facecolors="none",
            edgecolors=COLOR_GRAY,
            linewidths=1.2,
            label="Reacquired phase",
            zorder=4,
        )
        ax_xy.scatter(
            retry_phase,
            y_mean_v[reacquired],
            s=72,
            marker="s",
            facecolors="none",
            edgecolors=COLOR_GRAY,
            linewidths=1.2,
            zorder=4,
        )
    format_axis(
        ax_xy,
        xlabel="Y RF burst phase (deg)",
        ylabel="Demod mean (V)",
    )
    ax_xy.set_xlim(0.0, 360.0)
    style_legend(ax_xy)

    ax_pair.plot(
        pair_phase_deg,
        pair_in_phase_v,
        "o",
        color=COLOR_OPTIMAL,
        label="Paired in-phase",
    )
    ax_pair.plot(
        pair_phase_deg,
        pair_quadrature_v,
        "s",
        color=COLOR_PURPLE,
        label="Paired quadrature",
    )
    ax_pair.plot(
        dense,
        in_phase_fit,
        "--",
        color=COLOR_TRAD,
        label=(
            "In-phase fit "
            f"($R^2$={_r_squared_label(fit.get('r_squared'))})"
        ),
    )
    ax_pair.plot(
        dense,
        quadrature_fit,
        ":",
        color=COLOR_PURPLE,
        label="Quadrature fit",
    )
    ax_pair.axhline(0.0, color=COLOR_GRAY, lw=0.8)
    if np.isfinite(selected):
        status = "Selected" if payload.get("fit_accepted") is True else "Candidate"
        ax_pair.axvline(
            selected,
            color=COLOR_GRAY,
            ls="-.",
            label=f"{status}: {selected:.2f} deg",
        )
    format_axis(
        ax_pair,
        xlabel="Y RF burst phase (deg)",
        ylabel="Baseline-referenced response (V)",
    )
    ax_pair.set_xlim(0.0, 360.0)
    style_legend(ax_pair)
    filename = "phase_calibration.png"
    save_figure(fig, Path(results_dir) / filename)
    return filename


def plot_phase_calibration(
    raw_dir: Path,
    results_dir: Path,
    payload: dict[str, Any],
) -> str:
    """保存相位扫描诊断图；拟合失败时仍保留实测数据。"""
    if payload.get("mode") == "y_rf_quadrature_phase_calibration":
        return _plot_quadrature_phase_calibration(
            raw_dir,
            results_dir,
            payload,
        )

    with np.load(Path(raw_dir) / "phase_scan.npz") as data:
        if "scanned_phase_deg" in data:
            phase_deg = np.asarray(data["scanned_phase_deg"], dtype=float)
        elif "control_burst_phase_deg" in data:
            phase_deg = np.asarray(
                data["control_burst_phase_deg"],
                dtype=float,
            )
        else:
            phase_deg = np.asarray(
                data["y_rf_burst_phase_deg"],
                dtype=float,
            )
        r_mean_v = np.asarray(data["r_mean_v"], dtype=float)
        r_std_v = np.asarray(data["r_std_v"], dtype=float)

    primary = payload.get("primary_fit", {})
    diagnostic = payload.get("diagnostic_fit", {})
    primary_parameters = _finite_parameters(primary)
    diagnostic_parameters = _finite_parameters(diagnostic)
    dense = np.linspace(0.0, 360.0, 1441)

    set_plot_style("paper")
    fig, ax = new_figure()
    valid_error = np.all(np.isfinite(r_std_v)) and np.all(r_std_v >= 0.0)
    ax.errorbar(
        phase_deg,
        r_mean_v,
        yerr=r_std_v if valid_error else None,
        fmt="o",
        color=COLOR_OPTIMAL,
        capsize=2,
        label="Measured mean R",
    )

    calibration_succeeded = payload.get("fit_accepted") is True
    if "fit_accepted" not in payload:
        calibration_succeeded = payload.get("success") is True
    if primary_parameters is not None:
        status = "Primary fit" if calibration_succeeded else "Primary fit rejected"
        ax.plot(
            dense,
            inside_absolute_sine(dense, *primary_parameters),
            "--",
            color=COLOR_TRAD,
            label=(
                f"{status} "
                f"($R^2$={_r_squared_label(primary.get('r_squared'))})"
            ),
        )
    else:
        ax.plot([], [], "--", color=COLOR_TRAD, label="Primary fit unavailable")

    if diagnostic_parameters is not None:
        ax.plot(
            dense,
            outside_absolute_sine(dense, *diagnostic_parameters),
            ":",
            color=COLOR_PURPLE,
            label=(
                "Diagnostic fit "
                f"($R^2$={_r_squared_label(diagnostic.get('r_squared'))})"
            ),
        )

    try:
        selected = float(
            payload.get(
                "selected_phase_deg",
                payload.get("selected_y_rf_phase_deg", float("nan")),
            )
        )
    except (TypeError, ValueError):
        selected = float("nan")
    if np.isfinite(selected):
        selection_label = "Selected" if calibration_succeeded else "Candidate"
        ax.axvline(
            selected,
            color=COLOR_GRAY,
            ls="-.",
            label=f"{selection_label}: {selected:.2f} deg",
        )

    residual_mode = payload.get("mode") == "residual_control_phase_scan"
    format_axis(
        ax,
        xlabel=(
            "Z control burst phase (deg)"
            if residual_mode
            else "Y RF burst phase (deg)"
        ),
        ylabel="Demod R (V)",
    )
    ax.set_xlim(0.0, 360.0)
    style_legend(ax)
    filename = "phase_calibration.png"
    save_figure(fig, Path(results_dir) / filename)
    return filename
