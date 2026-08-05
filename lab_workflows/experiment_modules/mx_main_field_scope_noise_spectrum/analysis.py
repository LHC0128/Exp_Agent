"""Mx 主磁场示波器噪声谱离线分析。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import matplotlib
import numpy as np
import yaml
from scipy import signal as scipy_signal

from ...analysis.noise_spectrum_separation import (
    NoiseSeparationResult,
    fit_noise_separation,
    lorentzian_vs_control,
)
from ...experiment_runtime import runtime_run_dir
from ...plotting import (
    COLOR_CYAN,
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
from .global_analysis import (
    GlobalNoiseAccuracyResult,
    GlobalNoiseSeparationResult,
    assess_global_noise_accuracy,
    fit_global_noise_separation,
    project_global_noise_separation,
)
from .models import MxMainFieldScopeNoiseSpectrumParams

matplotlib.use(os.environ.get("MPLBACKEND", "Agg"))

PSD_HEATMAP_LOWER_PERCENTILE = 1.0
PSD_HEATMAP_UPPER_PERCENTILE = 99.5
GLOBAL_2D_EXAMPLE_PLOT_COUNT = 4


def _builtin(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _builtin(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_builtin(item) for item in value]
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    return value


def _load_params(
    run_dir: Path,
) -> tuple[MxMainFieldScopeNoiseSpectrumParams, dict[str, Any]]:
    config_path = run_dir / "experiment_config.yaml"
    if not config_path.exists():
        raise FileNotFoundError(f"未找到实验配置: {config_path}")
    with config_path.open(encoding="utf-8") as stream:
        config = yaml.safe_load(stream) or {}
    params = MxMainFieldScopeNoiseSpectrumParams.from_external(
        config.get("parameters", {}),
        schema_version=int(config.get("schema_version", 1)),
    )
    errors = params.validate_model()
    if errors:
        raise ValueError("；".join(errors))
    return params, config


def _load_waveforms(raw_dir: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted(raw_dir.glob("waveform_I*.npz")):
        with np.load(path) as data:
            time_s = np.asarray(data["time_s"], dtype=float).reshape(-1)
            voltage_v = np.asarray(data["voltage_v"], dtype=float).reshape(-1)
            if time_s.size != voltage_v.size or time_s.size < 2:
                raise ValueError(f"示波器时间轴与波形长度无效: {path}")
            sample_interval_s = float(np.median(np.diff(time_s)))
            if not np.isfinite(sample_interval_s) or sample_interval_s <= 0.0:
                raise ValueError(f"示波器时间轴采样间隔无效: {path}")
            calculated_rate_sa_s = 1.0 / sample_interval_s
            saved_rate_sa_s = float(data["actual_rate_sa_s"])
            if not np.isclose(
                calculated_rate_sa_s,
                saved_rate_sa_s,
                rtol=1e-6,
                atol=1e-6,
            ):
                raise ValueError(f"保存采样率与真实时间轴不一致: {path}")
            records.append(
                {
                    "path": path,
                    "point_index": int(data["point_index"]),
                    "control_frequency_hz": float(data["control_frequency_hz"]),
                    "larmor_frequency_hz": float(data["larmor_frequency_hz"]),
                    "main_field_current_ma": float(data["main_field_current_ma"]),
                    "actual_rate_sa_s": calculated_rate_sa_s,
                    "scale_used_v_div": float(data["scale_used_v_div"]),
                    "voltage_v": voltage_v,
                }
            )
    if len(records) < 10:
        raise ValueError(
            f"至少需要 10 个完整主场示波器点，当前仅发现 {len(records)} 个"
        )
    records.sort(key=lambda item: int(item["point_index"]))
    return records


def _compute_psd_matrix(
    records: list[dict[str, Any]], nperseg: int
) -> tuple[np.ndarray, np.ndarray]:
    """使用各波形真实采样率计算 Welch PSD，并要求频率网格一致。"""
    rows: list[np.ndarray] = []
    frequency_axis: np.ndarray | None = None
    for record in records:
        waveform = np.asarray(record["voltage_v"], dtype=float)
        waveform = waveform[np.isfinite(waveform)]
        if waveform.size < 8:
            raise ValueError(f"PD 有效样本不足 8: {record['path']}")
        point_nperseg = min(int(nperseg), waveform.size)
        frequency, psd = scipy_signal.welch(
            waveform,
            fs=float(record["actual_rate_sa_s"]),
            nperseg=point_nperseg,
            noverlap=None,
            scaling="density",
        )
        if frequency_axis is None:
            frequency_axis = frequency
        elif frequency.shape != frequency_axis.shape or not np.allclose(
            frequency, frequency_axis, rtol=1e-8, atol=1e-8
        ):
            raise ValueError("各主场点的真实采样率或 Welch 频率轴不一致")
        rows.append(psd)
    assert frequency_axis is not None
    return frequency_axis, np.asarray(rows, dtype=float)


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
        vmax=float(color_max),
    )
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
        np.clip(fractional, -0.5, 0.5),
        shading="auto",
        cmap="coolwarm",
        vmin=-0.5,
        vmax=0.5,
    )
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
    figure.colorbar(image, ax=axis, label="Fractional residual")
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


def analyze(run_dir: Path) -> dict[str, Any]:
    """只读取指定运行目录中的 PD 原始波形并写入 ``results/``。"""
    set_plot_style("paper")
    run_dir = Path(run_dir).resolve()
    params, config = _load_params(run_dir)
    raw_dir = run_dir / "raw"
    results_dir = run_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    records = _load_waveforms(raw_dir)

    point_indices = np.asarray([record["point_index"] for record in records], dtype=int)
    saved_control_hz = np.asarray(
        [record["control_frequency_hz"] for record in records], dtype=float
    )
    saved_larmor_hz = np.asarray(
        [record["larmor_frequency_hz"] for record in records], dtype=float
    )
    main_field_current_ma = np.asarray(
        [record["main_field_current_ma"] for record in records], dtype=float
    )
    actual_rate_sa_s = np.asarray(
        [record["actual_rate_sa_s"] for record in records], dtype=float
    )
    scale_used_v_div = np.asarray(
        [record["scale_used_v_div"] for record in records], dtype=float
    )
    calibrated_control_hz = (
        params.main_field_calibration_hz_per_ma * main_field_current_ma
        + params.main_field_calibration_intercept_hz
    )
    if not np.allclose(calibrated_control_hz, saved_control_hz, rtol=0.0, atol=1e-6):
        raise ValueError("原始数据中的主场电流不满足保存的外推标定式")
    if not np.allclose(saved_larmor_hz, saved_control_hz, rtol=0.0, atol=1e-9):
        raise ValueError("原始数据中的 Larmor 频率与控制频率不一致")
    if np.any(np.diff(calibrated_control_hz) <= 0.0):
        raise ValueError("主场控制频率轴必须严格递增")

    frequency_axis_hz, psd_matrix = _compute_psd_matrix(
        records, params.welch_nperseg
    )
    np.savez(
        results_dir / "psd_matrix.npz",
        psd_matrix=psd_matrix,
        frequency_axis_hz=frequency_axis_hz,
        control_frequency_hz=calibrated_control_hz,
        larmor_frequency_hz=calibrated_control_hz,
        main_field_current_ma=main_field_current_ma,
        point_index=point_indices,
        actual_rate_sa_s=actual_rate_sa_s,
        scale_used_v_div=scale_used_v_div,
        welch_nperseg=np.int64(params.welch_nperseg),
    )

    fit_frequency_mask = (
        (frequency_axis_hz > 0.0)
        & (frequency_axis_hz <= params.control_frequency_stop_hz)
    )
    fit_frequency_axis_hz = frequency_axis_hz[fit_frequency_mask]
    fit_psd_matrix = psd_matrix[:, fit_frequency_mask]
    if fit_frequency_axis_hz.size < 3:
        raise ValueError("0 Hz 以上且不超过控制频率上限的 PSD 频率点不足 3 个")
    fit = fit_noise_separation(
        fit_psd_matrix,
        fit_frequency_axis_hz,
        calibrated_control_hz,
        peak_margin_hz=params.fit_peak_margin_hz,
        fit_half_width_hz=params.fit_half_width_hz,
        gamma_guess_hz=params.fit_gamma_guess_hz,
    )
    np.savez(
        results_dir / "popt_fit.npz",
        popt=fit.parameters,
        perr=fit.uncertainties,
        fit_mask=fit.fit_mask,
        interpolated_mask=fit.interpolated_mask,
        frequency_axis_hz=fit_frequency_axis_hz,
        param_names=np.asarray(["gamma", "Amp", "D", "dw"], dtype=str),
        fit_control_half_width_hz=np.float64(params.fit_half_width_hz),
    )
    np.savez(
        results_dir / "noise_spectra.npz",
        S_beta=fit.s_beta,
        N_S1=fit.n_s1,
        freq_axis=fit_frequency_axis_hz,
        omega_ctrl=calibrated_control_hz,
        main_field_current_ma=main_field_current_ma,
        fit_mask=fit.fit_mask,
        interpolated_mask=fit.interpolated_mask,
        fit_control_half_width_hz=np.float64(params.fit_half_width_hz),
    )
    np.savetxt(
        results_dir / "noise_spectra.csv",
        np.column_stack(
            [
                fit_frequency_axis_hz,
                fit.s_beta,
                fit.n_s1,
                fit.fit_mask.astype(int),
                fit.interpolated_mask.astype(int),
            ]
        ),
        delimiter=",",
        header=(
            "frequency_Hz,S_beta_controllable_V2_per_Hz,"
            "N_S1_uncontrollable_V2_per_Hz,direct_fit,interpolated"
        ),
        comments="",
    )
    np.savetxt(
        results_dir / "lorentzian_fit_parameters.csv",
        np.column_stack(
            [
                fit_frequency_axis_hz,
                fit.parameters,
                fit.uncertainties,
                fit.fit_mask.astype(int),
                fit.interpolated_mask.astype(int),
            ]
        ),
        delimiter=",",
        header=(
            "frequency_Hz,gamma_Hz,Amp,D,dw_Hz,gamma_err,Amp_err,D_err,"
            "dw_err,direct_fit,interpolated"
        ),
        comments="",
    )

    global_files: list[str] = []
    global_payload: dict[str, Any] = {
        "enabled": params.global_2d_analysis_enabled,
        "status": "disabled",
    }
    if params.global_2d_analysis_enabled:
        global_upper_hz = min(
            params.global_fit_frequency_max_hz,
            params.control_frequency_stop_hz,
            float(frequency_axis_hz[-1]),
        )
        global_frequency_mask = (
            (frequency_axis_hz >= params.global_fit_frequency_min_hz)
            & (frequency_axis_hz <= global_upper_hz)
        )
        global_frequency_hz = frequency_axis_hz[global_frequency_mask]
        global_psd_matrix = psd_matrix[:, global_frequency_mask]
        core_frequency_mask = (
            (frequency_axis_hz >= params.global_core_fit_frequency_min_hz)
            & (frequency_axis_hz <= global_upper_hz)
        )
        core_frequency_hz = frequency_axis_hz[core_frequency_mask]
        core_psd_matrix = psd_matrix[:, core_frequency_mask]
        if (
            global_frequency_hz.size >= 20
            and core_frequency_hz.size >= 20
            and calibrated_control_hz.size >= 20
        ):
            core_fit = fit_global_noise_separation(
                core_psd_matrix,
                core_frequency_hz,
                calibrated_control_hz,
                gamma_guess_hz=params.fit_gamma_guess_hz,
                background_margin_hz=params.global_background_margin_hz,
                cv_folds=params.global_cv_folds,
            )
            global_fit = project_global_noise_separation(
                global_psd_matrix,
                global_frequency_hz,
                calibrated_control_hz,
                core_fit,
                cv_folds=params.global_cv_folds,
            )
            global_accuracy = assess_global_noise_accuracy(
                core_psd_matrix,
                core_frequency_hz,
                calibrated_control_hz,
                core_fit,
                background_margin_hz=params.global_background_margin_hz,
                cv_folds=params.global_cv_folds,
                projection_psd_matrix=global_psd_matrix,
                projection_frequency_hz=global_frequency_hz,
                projection_result=global_fit,
            )
            valid = global_accuracy.quantitative_valid_mask
            valid_count = int(np.count_nonzero(valid))
            overlap_points = max(1, params.welch_nperseg // 2)
            welch_segment_counts = np.asarray(
                [
                    1 + max(0, (len(record["voltage_v"]) - params.welch_nperseg) // overlap_points)
                    for record in records
                ],
                dtype=int,
            )
            minimum_welch_segments = int(np.min(welch_segment_counts))
            expected_relative_sigma = 1.0 / np.sqrt(minimum_welch_segments)
            empirical_fractional_residual = np.abs(
                global_fit.residual_matrix
                / np.maximum(global_fit.model_matrix, np.finfo(float).tiny)
            )
            np.savez(
                results_dir / "global_2d_noise_separation.npz",
                frequency_hz=global_fit.frequency_hz,
                control_frequency_hz=global_fit.control_frequency_hz,
                gamma_hz=np.float64(global_fit.gamma_hz),
                control_offset_hz=np.float64(global_fit.control_offset_hz),
                N_S1=global_fit.n_s1,
                C_S_beta=global_fit.c_s_beta,
                peak_contribution_v2_hz=global_fit.peak_contribution,
                background_profile=global_fit.background_profile,
                model_matrix=global_fit.model_matrix,
                residual_matrix=global_fit.residual_matrix,
                median_absolute_fractional_residual=(
                    global_fit.median_absolute_fractional_residual
                ),
                cv_N_S1_relative_std=global_fit.cv_n_s1_relative_std,
                cv_C_S_beta_relative_std=global_fit.cv_c_s_beta_relative_std,
                N_S1_systematic_relative_std=(
                    global_accuracy.n_s1_systematic_relative_std
                ),
                C_S_beta_systematic_relative_std=(
                    global_accuracy.c_s_beta_systematic_relative_std
                ),
                N_S1_total_relative_uncertainty=(
                    global_accuracy.n_s1_total_relative_uncertainty
                ),
                C_S_beta_total_relative_uncertainty=(
                    global_accuracy.c_s_beta_total_relative_uncertainty
                ),
                cv_deviance=global_fit.cv_deviance,
                quantitative_valid_mask=valid,
                fold_gamma_hz=global_accuracy.fold_gamma_hz,
                fold_control_offset_hz=global_accuracy.fold_control_offset_hz,
                background_margin_hz=global_accuracy.background_margin_hz,
                margin_gamma_hz=global_accuracy.margin_gamma_hz,
                margin_control_offset_hz=(
                    global_accuracy.margin_control_offset_hz
                ),
            )
            np.savetxt(
                results_dir / "global_2d_noise_spectra.csv",
                np.column_stack(
                    [
                        global_fit.frequency_hz,
                        global_fit.c_s_beta,
                        global_fit.n_s1,
                        global_fit.peak_contribution,
                        global_fit.cv_c_s_beta_relative_std,
                        global_fit.cv_n_s1_relative_std,
                        global_accuracy.c_s_beta_systematic_relative_std,
                        global_accuracy.n_s1_systematic_relative_std,
                        global_accuracy.c_s_beta_total_relative_uncertainty,
                        global_accuracy.n_s1_total_relative_uncertainty,
                        global_fit.median_absolute_fractional_residual,
                        global_fit.cv_deviance,
                        valid.astype(int),
                    ]
                ),
                delimiter=",",
                header=(
                    "frequency_Hz,C_S_beta,N_S1_V2_per_Hz,"
                    "peak_contribution_V2_per_Hz,C_S_beta_cv_rel_std,"
                    "N_S1_cv_rel_std,C_S_beta_systematic_rel_std,"
                    "N_S1_systematic_rel_std,C_S_beta_total_rel_uncertainty,"
                    "N_S1_total_rel_uncertainty,median_abs_fractional_residual,"
                    "cv_deviance,quantitative_valid"
                ),
                comments="",
            )
            representative_points = []
            for target_hz in (
                500.0,
                1000.0,
                1500.0,
                2000.0,
                5500.0,
                11125.0,
                30000.0,
                40000.0,
            ):
                index = int(np.argmin(np.abs(global_fit.frequency_hz - target_hz)))
                representative_points.append(
                    {
                        "frequency_hz": float(global_fit.frequency_hz[index]),
                        "C_S_beta": float(global_fit.c_s_beta[index]),
                        "N_S1_v2_hz": float(global_fit.n_s1[index]),
                        "C_S_beta_total_relative_uncertainty": float(
                            global_accuracy.c_s_beta_total_relative_uncertainty[index]
                        ),
                        "N_S1_total_relative_uncertainty": float(
                            global_accuracy.n_s1_total_relative_uncertainty[index]
                        ),
                        "quantitative_valid": bool(valid[index]),
                    }
                )
            global_payload = {
                "enabled": True,
                "status": "completed",
                "preferred_quantitative_method": True,
                "separation_strategy": (
                    "fit global response and H(Omega) on the core band, then "
                    "project N_S1 and C_S_beta onto the reported band"
                ),
                "model": (
                    "P(Omega,w) = H(Omega)*N_S1(w) + "
                    "L(Omega,w;gamma,offset)*C_S_beta(w)"
                ),
                "C_S_beta_definition": "4*G^2*S_2^2*S_beta",
                "absolute_S_beta_available": False,
                "absolute_S_beta_limitation": (
                    "需要独立标定 4*G^2*S_2^2；当前结果只给出乘积 C_S_beta"
                ),
                "frequency_min_hz": float(global_fit.frequency_hz[0]),
                "frequency_max_hz": float(global_fit.frequency_hz[-1]),
                "frequency_points": int(global_fit.frequency_hz.size),
                "core_fit_frequency_min_hz": float(core_fit.frequency_hz[0]),
                "core_fit_frequency_max_hz": float(core_fit.frequency_hz[-1]),
                "core_fit_frequency_points": int(core_fit.frequency_hz.size),
                "gamma_hz": global_fit.gamma_hz,
                "control_offset_hz": global_fit.control_offset_hz,
                "optimizer_success": global_fit.optimizer_success,
                "optimizer_message": global_fit.optimizer_message,
                "background": {
                    "rank": 1,
                    "normalization": "median H above the 60th control-frequency percentile equals 1",
                    "margin_hz": params.global_background_margin_hz,
                    "H_at_control_start": float(global_fit.background_profile[0]),
                    "H_at_control_stop": float(global_fit.background_profile[-1]),
                },
                "accuracy": {
                    "interpretation": "内部精度，不包含 4*G^2*S_2^2 标定误差和模型外系统误差",
                    "cv_folds": params.global_cv_folds,
                    "fold_gamma_mean_hz": float(np.mean(global_accuracy.fold_gamma_hz)),
                    "fold_gamma_std_hz": float(np.std(global_accuracy.fold_gamma_hz, ddof=1)),
                    "fold_control_offset_mean_hz": float(
                        np.mean(global_accuracy.fold_control_offset_hz)
                    ),
                    "fold_control_offset_std_hz": float(
                        np.std(global_accuracy.fold_control_offset_hz, ddof=1)
                    ),
                    "background_margin_values_hz": global_accuracy.background_margin_hz,
                    "background_margin_gamma_hz": global_accuracy.margin_gamma_hz,
                    "whittle_deviance_mean": global_fit.whittle_deviance_mean,
                    "heldout_whittle_deviance_mean": global_fit.cv_deviance_mean,
                    "log_r_squared": global_fit.log_r_squared,
                    "core_whittle_deviance_mean": core_fit.whittle_deviance_mean,
                    "core_heldout_whittle_deviance_mean": core_fit.cv_deviance_mean,
                    "core_log_r_squared": core_fit.log_r_squared,
                    "constant_background_whittle_deviance_mean": (
                        global_accuracy.constant_background_whittle_deviance_mean
                    ),
                    "constant_background_log_r_squared": (
                        global_accuracy.constant_background_log_r_squared
                    ),
                    "minimum_welch_segment_count": minimum_welch_segments,
                    "independent_segment_relative_sigma_approx": float(
                        expected_relative_sigma
                    ),
                    "independent_segment_median_abs_residual_approx": float(
                        0.67448975 * expected_relative_sigma
                    ),
                    "empirical_pixel_median_abs_fractional_residual": float(
                        np.median(empirical_fractional_residual)
                    ),
                    "empirical_pixel_p90_abs_fractional_residual": float(
                        np.percentile(empirical_fractional_residual, 90.0)
                    ),
                    "valid_frequency_points": valid_count,
                    "total_frequency_points": int(valid.size),
                    "valid_frequency_min_hz": (
                        float(global_fit.frequency_hz[valid][0]) if valid_count else None
                    ),
                    "valid_frequency_max_hz": (
                        float(global_fit.frequency_hz[valid][-1]) if valid_count else None
                    ),
                    "C_S_beta_total_relative_uncertainty_median": float(
                        np.median(
                            global_accuracy.c_s_beta_total_relative_uncertainty[valid]
                        )
                    ) if valid_count else None,
                    "C_S_beta_total_relative_uncertainty_p90": float(
                        np.percentile(
                            global_accuracy.c_s_beta_total_relative_uncertainty[valid],
                            90.0,
                        )
                    ) if valid_count else None,
                    "N_S1_total_relative_uncertainty_median": float(
                        np.median(
                            global_accuracy.n_s1_total_relative_uncertainty[valid]
                        )
                    ) if valid_count else None,
                    "N_S1_total_relative_uncertainty_p90": float(
                        np.percentile(
                            global_accuracy.n_s1_total_relative_uncertainty[valid],
                            90.0,
                        )
                    ) if valid_count else None,
                },
                "representative_points": representative_points,
            }
            global_files = [
                "global_2d_noise_separation.npz",
                "global_2d_noise_spectra.csv",
                _plot_global_2d_spectra(results_dir, global_fit, global_accuracy),
                _plot_global_2d_background(results_dir, global_fit),
                _plot_global_2d_residual(results_dir, global_fit),
                _plot_global_2d_accuracy(results_dir, global_fit, global_accuracy),
                _plot_global_2d_examples(
                    results_dir, global_psd_matrix, global_fit
                ),
                "global_2d_analysis.yaml",
                "global_2d_analysis.json",
            ]
            with (results_dir / "global_2d_analysis.yaml").open(
                "w", encoding="utf-8"
            ) as stream:
                yaml.safe_dump(
                    _builtin(global_payload), stream, allow_unicode=True, sort_keys=False
                )
            with (results_dir / "global_2d_analysis.json").open(
                "w", encoding="utf-8"
            ) as stream:
                json.dump(
                    _builtin(global_payload), stream, ensure_ascii=False, indent=2
                )
        else:
            global_payload = {
                "enabled": True,
                "status": "skipped",
                "reason": "二维拟合频率范围内有效频率点不足 20 个",
            }

    files = [
        "psd_matrix.npz",
        "popt_fit.npz",
        "noise_spectra.npz",
        "noise_spectra.csv",
        "lorentzian_fit_parameters.csv",
        _plot_control_axis(
            results_dir, main_field_current_ma, calibrated_control_hz
        ),
        _plot_psd_matrix(
            results_dir,
            frequency_axis_hz,
            calibrated_control_hz,
            psd_matrix,
            params.control_frequency_stop_hz,
        ),
        _plot_extracted_spectra(
            results_dir, fit_frequency_axis_hz, fit.s_beta, fit.n_s1
        ),
        _plot_fit_parameters(results_dir, fit_frequency_axis_hz, fit),
    ]
    files.extend(global_files)
    for stale_example in results_dir.glob("fit_example_*.png"):
        stale_example.unlink()
    example_files = _plot_fit_examples(
        results_dir,
        calibrated_control_hz,
        fit_frequency_axis_hz,
        fit_psd_matrix,
        fit,
        params.fit_example_plot_count,
        params.fit_half_width_hz,
    )
    files.extend(example_files)

    fitted_count = int(np.count_nonzero(fit.fit_mask))
    interpolated_count = int(np.count_nonzero(fit.interpolated_mask))
    payload = {
        "success": fitted_count > 0,
        "experiment_id": config.get(
            "experiment_id", "mx-main-field-scope-noise-spectrum"
        ),
        "run_dir": str(run_dir),
        "acquisition_signal": "SDS CH1 PD voltage",
        "control_model": (
            "control_Hz = Larmor_Hz = "
            "K_f_Hz_per_mA * current_mA + f_0mA_Hz"
        ),
        "main_field_calibration": {
            "source_run": params.main_field_calibration_source_run,
            "slope_hz_per_ma": params.main_field_calibration_hz_per_ma,
            "intercept_hz": params.main_field_calibration_intercept_hz,
            "extrapolation_enabled": True,
        },
        "scan": {
            "completed_points": len(records),
            "target_points": params.control_frequency_points,
            "control_start_hz": float(calibrated_control_hz[0]),
            "control_stop_hz": float(calibrated_control_hz[-1]),
            "main_field_current_start_ma": float(main_field_current_ma[0]),
            "main_field_current_stop_ma": float(main_field_current_ma[-1]),
        },
        "welch": {
            "nperseg": params.welch_nperseg,
            "actual_rate_min_sa_s": float(np.min(actual_rate_sa_s)),
            "actual_rate_max_sa_s": float(np.max(actual_rate_sa_s)),
            "frequency_start_hz": float(frequency_axis_hz[0]),
            "frequency_stop_hz": float(frequency_axis_hz[-1]),
        },
        "fit": {
            "model": (
                "D + A*(gamma^2+w^2) / "
                "((gamma^2-w^2+(Omega+dw)^2)^2+4*w^2*gamma^2)"
            ),
            "dc_excluded": True,
            "control_frequency_half_width_hz": params.fit_half_width_hz,
            "frequency_min_hz": float(fit_frequency_axis_hz[0]),
            "frequency_max_hz": float(fit_frequency_axis_hz[-1]),
            "fitted_frequency_points": fitted_count,
            "interpolated_frequency_points": interpolated_count,
            "eligible_frequency_points": int(fit_frequency_axis_hz.size),
            "example_plot_target_count": params.fit_example_plot_count,
            "example_plot_created_count": len(example_files),
        },
        "global_2d_fit": global_payload,
        "files": files,
    }
    payload["files"].extend(["analysis.yaml", "analysis.json"])
    serializable = _builtin(payload)
    with (results_dir / "analysis.yaml").open("w", encoding="utf-8") as stream:
        yaml.safe_dump(serializable, stream, allow_unicode=True, sort_keys=False)
    with (results_dir / "analysis.json").open("w", encoding="utf-8") as stream:
        json.dump(serializable, stream, ensure_ascii=False, indent=2)
    print(
        f"Mx 主场示波器噪声谱分析完成: {len(records)} 个主场点，"
        f"{fitted_count}/{fit_frequency_axis_hz.size} 个频率点直接拟合成功，"
        f"输出 {len(example_files)} 张示例拟合图"
    )
    return serializable


def main() -> int:
    analyze(runtime_run_dir())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
