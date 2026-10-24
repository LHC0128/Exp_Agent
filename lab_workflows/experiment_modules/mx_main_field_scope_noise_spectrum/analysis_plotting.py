"""保留原科学图、英文标注与 paper PNG/PDF 导出。"""
from __future__ import annotations
from pathlib import Path
import numpy as np
from ...analysis.noise_spectrum_separation import NoiseSeparationResult, lorentzian_vs_control
from ...plotting import (
    COLOR_CYAN, COLOR_GRAY, COLOR_OPTIMAL, COLOR_TRAD, PAPER_WIDE,
    format_axis, new_figure, save_figure, style_legend,
)
from .global_analysis import GlobalNoiseAccuracyResult, GlobalNoiseSeparationResult

PSD_HEATMAP_LOWER_PERCENTILE = 1.0
PSD_HEATMAP_UPPER_PERCENTILE = 99.5
GLOBAL_2D_EXAMPLE_PLOT_COUNT = 4


def _plot_control_axis(
    results_dir: Path,
    main_field_current_ma: np.ndarray,
    control_frequency_hz: np.ndarray,
) -> str:
    filename = "main_field_control_axis.png"
    figure, axis = new_figure()
    axis.plot(
        main_field_current_ma,
        control_frequency_hz / 1000.0,
        "o-",
        color=COLOR_OPTIMAL,
    )
    format_axis(
        axis,
        xlabel="GS200 current (mA)",
        ylabel="Control frequency (kHz)",
    )
    save_figure(figure, results_dir / filename)
    return filename


def _plot_psd_matrix(
    results_dir: Path,
    frequency_axis_hz: np.ndarray,
    control_frequency_hz: np.ndarray,
    psd_matrix: np.ndarray,
    fit_max_hz: float,
) -> str:
    filename = "noise_spectrum_2d.png"
    figure, axis = new_figure(kind="square")
    log_psd = np.log10(np.maximum(psd_matrix, np.finfo(float).tiny))
    display_start_hz = float(control_frequency_hz[0])
    display_stop_hz = float(control_frequency_hz[-1])
    display_frequency_mask = (
        (frequency_axis_hz >= display_start_hz)
        & (frequency_axis_hz <= display_stop_hz)
    )
    displayed = log_psd[:, display_frequency_mask]
    finite_displayed = displayed[np.isfinite(displayed)]
    if finite_displayed.size == 0:
        raise ValueError("PSD 显示频段内没有有限数值")
    color_min, color_max = np.percentile(
        finite_displayed,
        [PSD_HEATMAP_LOWER_PERCENTILE, PSD_HEATMAP_UPPER_PERCENTILE],
    )
    if color_max <= color_min:
        color_max = color_min + 1.0
    image = axis.pcolormesh(
        frequency_axis_hz / 1000.0,
        control_frequency_hz / 1000.0,
        log_psd,
        shading="auto",
        cmap="inferno",
        vmin=float(color_min),
        vmax=float(color_max), rasterized=True)
    diagonal_min = max(float(frequency_axis_hz[0]), display_start_hz)
    diagonal_max = min(float(frequency_axis_hz[-1]), float(control_frequency_hz[-1]))
    axis.plot(
        [diagonal_min / 1000.0, diagonal_max / 1000.0],
        [diagonal_min / 1000.0, diagonal_max / 1000.0],
        color=COLOR_CYAN,
        linestyle="--",
        label="Expected ridge",
    )
    axis.axvline(
        fit_max_hz / 1000.0,
        color="white",
        linestyle=":",
        linewidth=1.2,
        label="Fit limit",
    )
    format_axis(
        axis,
        xlabel="PD PSD frequency (kHz)",
        ylabel="Control frequency (kHz)",
    )
    axis.set_xlim(display_start_hz / 1000.0, display_stop_hz / 1000.0)
    axis.set_ylim(display_start_hz / 1000.0, display_stop_hz / 1000.0)
    style_legend(axis)
    figure.colorbar(
        image,
        ax=axis,
        extend="both",
        label="log10 PSD (V²/Hz)",
    )
    save_figure(figure, results_dir / filename)
    return filename


def _plot_extracted_spectra(
    results_dir: Path,
    frequency_axis_hz: np.ndarray,
    s_beta: np.ndarray,
    n_s1: np.ndarray,
) -> str:
    filename = "noise_spectra_extracted.png"
    figure, axes = new_figure(nrows=1, ncols=2, kind="wide")
    for axis, values, color, ylabel in (
        (
            axes[0],
            s_beta,
            COLOR_OPTIMAL,
            "$S_\\beta(\\omega)$ (V²/Hz)",
        ),
        (
            axes[1],
            n_s1,
            COLOR_TRAD,
            "$N_{S_1}(\\omega)$ (V²/Hz)",
        ),
    ):
        valid = (
            np.isfinite(frequency_axis_hz)
            & np.isfinite(values)
            & (frequency_axis_hz > 0.0)
            & (values > 0.0)
        )
        axis.loglog(frequency_axis_hz[valid], values[valid], color=color)
        format_axis(axis, xlabel="PSD frequency (Hz)", ylabel=ylabel)
    save_figure(figure, results_dir / filename)
    return filename


def _plot_fit_parameters(
    results_dir: Path,
    frequency_axis_hz: np.ndarray,
    fit: NoiseSeparationResult,
) -> str:
    filename = "lorentzian_fit_parameters.png"
    labels = (
        ("Gamma (Hz)", "Linewidth"),
        ("Amplitude", "S_beta Amplitude"),
        ("Baseline (V²/Hz)", "N_S1 Baseline"),
        ("Offset (Hz)", "Control-Frequency Offset"),
    )
    figure, axes = new_figure(
        figsize=(PAPER_WIDE[0], 2.0 * PAPER_WIDE[1]),
        nrows=2,
        ncols=2,
        sharex=True,
    )
    for index, (ylabel, title) in enumerate(labels):
        axis = axes.flat[index]
        values = fit.parameters[:, index]
        direct = fit.fit_mask & np.isfinite(values)
        interpolated = fit.interpolated_mask & np.isfinite(values)
        axis.plot(
            frequency_axis_hz[direct] / 1000.0,
            values[direct],
            ".",
            color=COLOR_OPTIMAL,
            label="Direct fit",
        )
        axis.plot(
            frequency_axis_hz[interpolated] / 1000.0,
            values[interpolated],
            ".",
            color=COLOR_TRAD,
            label="Interpolated",
        )
        format_axis(axis, ylabel=ylabel)
        axis.set_title(title)
        style_legend(axis)
    for axis in axes[-1]:
        format_axis(axis, xlabel="PSD frequency (kHz)")
    save_figure(figure, results_dir / filename)
    return filename


def _select_example_indices(
    frequency_axis_hz: np.ndarray,
    valid_mask: np.ndarray,
    count: int,
) -> np.ndarray:
    """在有效拟合频率中选取最接近等间隔目标的唯一索引。"""
    valid_indices = np.flatnonzero(valid_mask)
    if valid_indices.size <= count:
        return valid_indices
    valid_frequencies = frequency_axis_hz[valid_indices]
    targets = np.linspace(
        float(valid_frequencies[0]), float(valid_frequencies[-1]), count
    )
    selected: list[int] = []
    for target in targets:
        order = np.argsort(np.abs(valid_frequencies - target))
        choice = next(
            int(valid_indices[position])
            for position in order
            if int(valid_indices[position]) not in selected
        )
        selected.append(choice)
    return np.asarray(sorted(selected), dtype=int)


def _plot_fit_examples(
    results_dir: Path,
    control_frequency_hz: np.ndarray,
    frequency_axis_hz: np.ndarray,
    psd_matrix: np.ndarray,
    fit: NoiseSeparationResult,
    count: int,
    fit_half_width_hz: float,
) -> list[str]:
    direct_mask = fit.fit_mask & np.all(
        np.isfinite(fit.parameters), axis=1
    )
    in_control_range_mask = (
        (frequency_axis_hz >= control_frequency_hz[0])
        & (frequency_axis_hz <= control_frequency_hz[-1])
    )
    centered_mask = direct_mask & in_control_range_mask & (
        frequency_axis_hz - fit_half_width_hz >= control_frequency_hz[0]
    ) & (
        frequency_axis_hz + fit_half_width_hz <= control_frequency_hz[-1]
    )
    valid_mask = (
        centered_mask
        if np.count_nonzero(centered_mask) >= count
        else direct_mask & in_control_range_mask
    )
    selected = _select_example_indices(frequency_axis_hz, valid_mask, count)
    files: list[str] = []
    for sequence, index in enumerate(selected, start=1):
        fixed_frequency_hz = float(frequency_axis_hz[index])
        fit_window = (
            np.abs(control_frequency_hz - fixed_frequency_hz)
            <= fit_half_width_hz
        )
        fit_control = control_frequency_hz[fit_window]
        fit_psd = psd_matrix[fit_window, index]
        dense_control = np.linspace(
            float(fit_control[0]),
            float(fit_control[-1]),
            1000,
        )
        gamma, amplitude, baseline, offset = fit.parameters[index]
        fitted_curve = lorentzian_vs_control(
            dense_control,
            float(gamma),
            float(amplitude),
            float(baseline),
            float(offset),
            fixed_frequency_hz,
        )
        filename = f"fit_example_{sequence:02d}_{fixed_frequency_hz:.3f}Hz.png"
        figure, axis = new_figure()
        axis.plot(
            fit_control / 1000.0,
            fit_psd,
            ".",
            color=COLOR_OPTIMAL,
            label="Measured PSD",
        )
        axis.plot(
            dense_control / 1000.0,
            fitted_curve,
            "-",
            color=COLOR_TRAD,
            label="Four-parameter Lorentzian",
        )
        format_axis(
            axis,
            xlabel="Control frequency (kHz)",
            ylabel="PSD (V²/Hz)",
        )
        style_legend(axis, title=f"PSD frequency: {fixed_frequency_hz / 1000.0:.3f} kHz")
        save_figure(figure, results_dir / filename)
        files.append(filename)
    return files


def _plot_global_2d_spectra(
    results_dir: Path,
    fit: GlobalNoiseSeparationResult,
    accuracy: GlobalNoiseAccuracyResult,
) -> str:
    filename = "global_2d_noise_spectra.png"
    figure, axes = new_figure(nrows=1, ncols=2, kind="wide")
    valid = accuracy.quantitative_valid_mask
    items = (
        (
            fit.c_s_beta,
            accuracy.c_s_beta_total_relative_uncertainty,
            COLOR_OPTIMAL,
            "$4G^2S_2^2S_\\beta(\\omega)$ (effective coefficient)",
        ),
        (
            fit.n_s1,
            accuracy.n_s1_total_relative_uncertainty,
            COLOR_TRAD,
            "$N_{S_1}(\\omega)$ (V²/Hz)",
        ),
    )
    for axis, (values, uncertainty, color, ylabel) in zip(axes, items):
        axis.semilogy(
            fit.frequency_hz[~valid],
            values[~valid],
            ".",
            color=COLOR_GRAY,
            alpha=0.5,
            label="Excluded",
        )
        axis.semilogy(
            fit.frequency_hz[valid],
            values[valid],
            color=color,
            label="Quantitative band",
        )
        lower = np.maximum(values * (1.0 - uncertainty), np.finfo(float).tiny)
        upper = values * (1.0 + uncertainty)
        axis.fill_between(
            fit.frequency_hz[valid],
            lower[valid],
            upper[valid],
            color=color,
            alpha=0.2,
            linewidth=0.0,
        )
        axis.set_xlim(0.0, float(fit.frequency_hz[-1]))
        format_axis(axis, xlabel="PSD frequency (Hz)", ylabel=ylabel)
        style_legend(axis)
    save_figure(figure, results_dir / filename)
    return filename


def _plot_global_2d_background(
    results_dir: Path,
    fit: GlobalNoiseSeparationResult,
) -> str:
    filename = "global_2d_control_background.png"
    figure, axis = new_figure()
    axis.semilogy(
        fit.control_frequency_hz / 1000.0,
        fit.background_profile,
        "o-",
        color=COLOR_OPTIMAL,
    )
    axis.axhline(1.0, color=COLOR_GRAY, linestyle="--", label="High-control reference")
    format_axis(
        axis,
        xlabel="Control frequency (kHz)",
        ylabel="Background multiplier H(Omega)",
    )
    style_legend(axis)
    save_figure(figure, results_dir / filename)
    return filename


def _plot_global_2d_residual(
    results_dir: Path,
    fit: GlobalNoiseSeparationResult,
) -> str:
    filename = "global_2d_fractional_residual.png"
    figure, axis = new_figure(kind="square")
    fractional = fit.residual_matrix / np.maximum(
        fit.model_matrix, np.finfo(float).tiny
    )
    image = axis.pcolormesh(
        fit.frequency_hz / 1000.0,
        fit.control_frequency_hz / 1000.0,
        np.ma.masked_invalid(fractional),
        shading="auto",
        cmap="coolwarm",
        vmin=-0.5,
        vmax=0.5, rasterized=True)
    diagonal_min = max(float(fit.frequency_hz[0]), float(fit.control_frequency_hz[0]))
    diagonal_max = min(float(fit.frequency_hz[-1]), float(fit.control_frequency_hz[-1]))
    axis.plot(
        [diagonal_min / 1000.0, diagonal_max / 1000.0],
        [diagonal_min / 1000.0, diagonal_max / 1000.0],
        color="black",
        linestyle="--",
        linewidth=1.0,
        label="Expected ridge",
    )
    format_axis(
        axis,
        xlabel="PSD frequency (kHz)",
        ylabel="Control frequency (kHz)",
    )
    style_legend(axis)
    figure.colorbar(image, ax=axis, label="Fractional residual", extend="both")
    save_figure(figure, results_dir / filename)
    return filename


def _plot_global_2d_accuracy(
    results_dir: Path,
    fit: GlobalNoiseSeparationResult,
    accuracy: GlobalNoiseAccuracyResult,
) -> str:
    filename = "global_2d_accuracy.png"
    figure, axes = new_figure(nrows=1, ncols=2, kind="wide")
    axes[0].semilogy(
        fit.frequency_hz / 1000.0,
        accuracy.c_s_beta_total_relative_uncertainty * 100.0,
        color=COLOR_OPTIMAL,
        label="$4G^2S_2^2S_\\beta$",
    )
    axes[0].semilogy(
        fit.frequency_hz / 1000.0,
        accuracy.n_s1_total_relative_uncertainty * 100.0,
        color=COLOR_TRAD,
        label="$N_{S_1}$",
    )
    axes[0].axhline(25.0, color=COLOR_GRAY, linestyle="--")
    format_axis(
        axes[0],
        xlabel="PSD frequency (kHz)",
        ylabel="Internal relative uncertainty (%)",
    )
    style_legend(axes[0])
    axes[1].semilogy(
        fit.frequency_hz / 1000.0,
        fit.median_absolute_fractional_residual,
        color=COLOR_OPTIMAL,
        label="Fit residual",
    )
    axes[1].semilogy(
        fit.frequency_hz / 1000.0,
        fit.cv_deviance,
        color=COLOR_TRAD,
        label="Held-out deviance",
    )
    format_axis(
        axes[1],
        xlabel="PSD frequency (kHz)",
        ylabel="Residual metric",
    )
    style_legend(axes[1])
    save_figure(figure, results_dir / filename)
    return filename


def _plot_global_2d_examples(
    results_dir: Path,
    psd_matrix: np.ndarray,
    fit: GlobalNoiseSeparationResult,
) -> str:
    filename = "global_2d_fit_examples.png"
    in_control_range_mask = (
        (fit.frequency_hz >= fit.control_frequency_hz[0])
        & (fit.frequency_hz <= fit.control_frequency_hz[-1])
    )
    indices = _select_example_indices(
        fit.frequency_hz,
        in_control_range_mask,
        GLOBAL_2D_EXAMPLE_PLOT_COUNT,
    )
    if indices.size == 0:
        raise ValueError(
            "二维全局拟合结果在控制频率范围内没有可用于示例图的 PSD 频率"
        )
    figure, axes = new_figure(
        figsize=(PAPER_WIDE[0], float(indices.size) * PAPER_WIDE[1]),
        nrows=int(indices.size),
        ncols=1,
        sharex=True,
    )
    axes_array = np.atleast_1d(axes)
    for axis, index in zip(axes_array, indices):
        background = fit.background_profile * fit.n_s1[index]
        modulated = fit.response_matrix[:, index] * fit.c_s_beta[index]
        axis.semilogy(
            fit.control_frequency_hz / 1000.0,
            psd_matrix[:, index],
            ".",
            color=COLOR_GRAY,
            label="Measured PSD",
        )
        axis.semilogy(
            fit.control_frequency_hz / 1000.0,
            fit.model_matrix[:, index],
            color=COLOR_OPTIMAL,
            label="Global model",
        )
        axis.semilogy(
            fit.control_frequency_hz / 1000.0,
            background,
            "--",
            color=COLOR_TRAD,
            label="Control-dependent background",
        )
        axis.semilogy(
            fit.control_frequency_hz / 1000.0,
            np.maximum(modulated, np.finfo(float).tiny),
            ":",
            color=COLOR_CYAN,
            label="Modulated-noise contribution",
        )
        format_axis(axis, ylabel="PSD (V²/Hz)")
        axis.set_title(f"PSD frequency: {fit.frequency_hz[index] / 1000.0:.3f} kHz")
        style_legend(axis)
    format_axis(axes_array[-1], xlabel="Control frequency (kHz)")
    save_figure(figure, results_dir / filename)
    return filename
