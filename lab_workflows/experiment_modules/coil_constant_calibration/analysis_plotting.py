"""Z 线圈常数标定的论文图；一张图只画一个物理量，PNG/PDF 成对保存。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use(os.environ.get("MPLBACKEND", "Agg"))
import numpy as np

from ...plotting import (COLOR_GRAY, COLOR_OPTIMAL, COLOR_TRAD, PAPER_WIDE, format_axis,
                         new_figure, save_figure, set_plot_style, style_legend)
from ..mx_y_rf_sensitivity.analysis_core import lorentzian_response

set_plot_style("paper")


def _gamma(payload: dict[str, Any]) -> float:
    return float((payload.get("operating_point") or {}).get("gyromagnetic_ratio_hz_per_nt") or 7.0)


def _accepted(curves):
    return [curve for curve in curves if curve["fit"]["success"]]


def plot_frequency_responses(results_dir: Path, curves) -> str:
    plotted = [curve for curve in curves if not curve["near_zero_skipped"] and curve.get("gamma_hz", 0) > 0]
    if not plotted:
        return ""
    columns = 2
    rows = max(1, int(np.ceil(len(plotted) / columns)))
    fig, axes = new_figure(figsize=(PAPER_WIDE[0], 2.6 * rows), nrows=rows, ncols=columns, squeeze=False)
    for axis, curve in zip(axes.flat, plotted, strict=False):
        frequency = np.asarray(curve["_frequency_hz"], dtype=float) / 1e3
        response = np.asarray(curve["_r_mean_v"], dtype=float)
        axis.plot(frequency, response, "o", markersize=3, color=COLOR_OPTIMAL, label="Measured R")
        parameters = curve["fit"].get("parameters")
        if curve["fit"]["success"] and parameters and np.all(np.isfinite(parameters)):
            dense = np.linspace(float(np.min(frequency)), float(np.max(frequency)), 600)
            axis.plot(dense, lorentzian_response(dense * 1e3, *parameters), color=COLOR_TRAD,
                      linestyle="--", label="Sqrt-Lorentzian fit")
        status = "accepted" if curve["fit"]["success"] else "excluded"
        axis.set_title(f"I = {curve['current_ma']:.1f} mA ({curve['scan_direction']}, {status})")
        format_axis(axis, xlabel="Y RF frequency (kHz)", ylabel="Demod R (V)")
        style_legend(axis)
    for axis in axes.flat[len(plotted):]:
        axis.set_visible(False)
    name = "frequency_response_fits.png"
    save_figure(fig, results_dir / name)
    return name


def _fit_label(payload) -> str:
    gamma = _gamma(payload)
    return (f"K = {payload['K_Hz_per_mA']:.4g} Hz/mA\n"
            f"K_B = {payload['K_B_nT_per_mA']:.4g} nT/mA\n"
            f"I0 = {payload['I0_ma']:.4g} mA, R² = {payload['linearity_current']['r_squared']:.5f}")


def plot_calibration(results_dir: Path, curves, payload) -> str:
    accepted = _accepted(curves)
    excluded = [curve for curve in curves if not curve["fit"]["success"] and not curve["near_zero_skipped"]]
    current_all = np.asarray([curve["current_ma"] for curve in accepted], dtype=float)
    center_all = np.asarray([curve["center_hz"] for curve in accepted], dtype=float)
    sigma_all = np.asarray([curve["fit"]["center_uncertainty_hz"] for curve in accepted], dtype=float)
    gamma = _gamma(payload)
    fig, axis = new_figure()
    if accepted:
        axis.errorbar(current_all, center_all / 1e3, yerr=sigma_all / 1e3, fmt="o", capsize=3,
                      color=COLOR_OPTIMAL, label="Accepted centers")
    if excluded:
        axis.plot([curve["current_ma"] for curve in excluded],
                  np.asarray([curve["center_hz"] for curve in excluded]) / 1e3,
                  "x", color=COLOR_GRAY, label="Excluded centers")
    fit = payload.get("linearity_current") or {}
    slope = float(fit.get("slope", np.nan))
    crossing = float(payload.get("I0_ma", np.nan))
    if np.isfinite(slope) and np.isfinite(crossing) and accepted:
        grid = np.linspace(float(np.min(current_all)), float(np.max(current_all)), 500)
        axis.plot(grid, np.abs(slope * (grid - crossing)) / 1e3, color=COLOR_TRAD, linestyle="--",
                  label=_fit_label(payload))
        axis.axvline(crossing, color=COLOR_GRAY, linestyle=":", linewidth=1.0)
        branches = payload.get("branch_fits") or {}
        for name, mask in (("below", current_all < crossing), ("above", current_all > crossing)):
            branch = branches.get(name) or {}
            if branch.get("success") and np.count_nonzero(mask):
                grid_branch = np.linspace(float(np.min(current_all[mask])), float(np.max(current_all[mask])), 200)
                axis.plot(grid_branch, (branch["slope"] * grid_branch + branch["intercept"]) / 1e3,
                          color=COLOR_TRAD, linewidth=0.8)
    axis.axhline(0.0, color=COLOR_GRAY, linestyle=":", linewidth=0.8)
    format_axis(axis, xlabel="Z coil current (mA)", ylabel="Resonance center (kHz)")
    secondary = axis.secondary_yaxis(
        "right", functions=(lambda khz: khz / gamma, lambda field_ut: field_ut * gamma))
    secondary.set_ylabel("Magnetic field (µT)")
    style_legend(axis)
    name = "coil_constant_calibration.png"
    save_figure(fig, results_dir / name)
    return name


def plot_voltage_response(results_dir: Path, curves, payload) -> str:
    accepted = _accepted(curves)
    if not accepted:
        return ""
    voltage = np.asarray([curve["drive_v"] for curve in accepted], dtype=float)
    center = np.asarray([curve["center_hz"] for curve in accepted], dtype=float)
    sigma = np.asarray([curve["fit"]["center_uncertainty_hz"] for curve in accepted], dtype=float)
    gamma = _gamma(payload)
    fig, axis = new_figure()
    axis.errorbar(voltage, center / 1e3, yerr=sigma / 1e3, fmt="o", capsize=3,
                  color=COLOR_OPTIMAL, label="Accepted centers")
    abs_fit = payload.get("linearity_voltage_abs") or {}
    fit = payload.get("linearity_voltage") or {}
    grid = np.linspace(float(np.min(voltage)), float(np.max(voltage)), 400)
    if abs_fit.get("success"):
        axis.plot(grid, np.abs(float(abs_fit["slope"]) * (grid - float(abs_fit["crossing"]))) / 1e3,
                  color=COLOR_TRAD, linestyle="--",
                  label=(f"S = {float(abs_fit['slope']):.4g} Hz/V\n"
                         f"S_B = {float(abs_fit['slope']) / gamma:.4g} nT/V\n"
                         f"R² = {float(abs_fit['r_squared']):.5f}"))
        axis.axvline(float(abs_fit["crossing"]), color=COLOR_GRAY, linestyle=":", linewidth=1.0)
    if abs_fit.get("success") and fit.get("success"):
        for branch in (fit, fit.get("below_branch")):
            if not branch or not branch.get("success"):
                continue
            axis.plot(grid, (float(branch["slope"]) * grid + float(branch["intercept"])) / 1e3,
                      color=COLOR_TRAD, linewidth=0.8)
    format_axis(axis, xlabel="Signal generator drive voltage (V)", ylabel="Resonance center (kHz)")
    secondary = axis.secondary_yaxis(
        "right", functions=(lambda khz: khz / gamma, lambda field_ut: field_ut * gamma))
    secondary.set_ylabel("Magnetic field (µT)")
    style_legend(axis)
    name = "coil_voltage_response.png"
    save_figure(fig, results_dir / name)
    return name


def plot_residuals(results_dir: Path, curves, payload) -> str:
    fit = payload.get("linearity_current") or {}
    if not fit.get("success"):
        return ""
    slope = float(fit["slope"])
    crossing = float(payload["I0_ma"])
    fig, axis = new_figure()
    axis.axhline(0.0, color=COLOR_GRAY, linestyle="--", linewidth=1.0)
    for direction, style in (("forward", "o-"), ("reverse", "s--")):
        points = [curve for curve in _accepted(curves) if curve["scan_direction"] == direction]
        if not points:
            continue
        current = np.asarray([curve["current_ma"] for curve in points], dtype=float)
        residual = (np.asarray([curve["center_hz"] for curve in points], dtype=float)
                    - np.abs(slope * (current - crossing)))
        order = np.argsort(current)
        axis.plot(current[order], residual[order], style, markersize=4,
                  color=COLOR_OPTIMAL if direction == "forward" else COLOR_TRAD,
                  label=direction)
    format_axis(axis, xlabel="Z coil current (mA)", ylabel="Residual (Hz)")
    style_legend(axis)
    name = "coil_constant_residuals.png"
    save_figure(fig, results_dir / name)
    return name


def plot_dc_check(results_dir: Path, dc_check) -> str:
    points = (dc_check or {}).get("points") or []
    if not points:
        return ""
    drive = np.asarray([point["drive_v"] for point in points], dtype=float)
    current = np.asarray([point["current_a"] for point in points], dtype=float) * 1e3
    fig, axis = new_figure()
    axis.plot(drive, current, "o", color=COLOR_OPTIMAL, label="Measured DC current")
    if "k_a_per_v" in dc_check:
        grid = np.linspace(float(np.min(drive)), float(np.max(drive)), 200)
        fitted = (dc_check["k_a_per_v"] * grid + float(dc_check.get("offset_fit_a", 0.0))) * 1e3
        axis.plot(grid, fitted, color=COLOR_TRAD, linestyle="--",
                  label=(f"I = {dc_check['k_a_per_v']:.5g} A/V · V + {float(dc_check.get('offset_fit_a', 0.0)):.3g} A\n"
                         f"R² = {float(dc_check.get('r_squared', np.nan)):.5f}"))
    format_axis(axis, xlabel="Drive voltage (V)", ylabel="Coil current (mA)")
    style_legend(axis)
    name = "dc_iv_check.png"
    save_figure(fig, results_dir / name)
    return name
