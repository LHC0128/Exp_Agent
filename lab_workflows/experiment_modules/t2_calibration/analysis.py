"""T2 光学 FID 标定离线分析。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use(os.environ.get("MPLBACKEND", "Agg"))
import numpy as np
import yaml
from scipy.optimize import curve_fit
from scipy.signal import hilbert

from ...experiment_runtime import runtime_run_dir
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


_T2_MIN_S = 1e-6
_T2_MAX_S = 0.1
_MAX_ENVELOPE_TAIL_RATIO = 0.9
_MAX_RELATIVE_UNCERTAINTY = 0.5
_MIN_R_SQUARED = 0.8
_PLOT_SHORT_WINDOW_S = 0.002
_PLOT_LONG_WINDOW_S = 0.010


@dataclass(frozen=True, slots=True)
class T2FitDiagnostics:
    """T2 拟合结果及质量诊断。"""

    t2_ms: float
    uncertainty_ms: float
    optimum: np.ndarray
    success: bool
    reason: str
    envelope_tail_ratio: float
    r_squared: float


def damped_oscillation(
    time_s: np.ndarray,
    amplitude: float,
    t2_s: float,
    frequency_hz: float,
    phase_rad: float,
    offset_v: float,
) -> np.ndarray:
    """返回带直流偏置的单频阻尼振荡。"""
    return amplitude * np.exp(-time_s / t2_s) * np.cos(
        2 * np.pi * frequency_hz * time_s + phase_rad
    ) + offset_v


def _failed_fit(
    reason: str,
    envelope_tail_ratio: float = np.nan,
    optimum: np.ndarray | None = None,
    r_squared: float = np.nan,
) -> T2FitDiagnostics:
    return T2FitDiagnostics(
        t2_ms=np.nan,
        uncertainty_ms=np.nan,
        optimum=np.full(5, np.nan) if optimum is None else optimum,
        success=False,
        reason=reason,
        envelope_tail_ratio=float(envelope_tail_ratio),
        r_squared=float(r_squared),
    )


def fit_t2_diagnostics(
    time_s: np.ndarray,
    waveform_v: np.ndarray,
    frequency_guess_hz: float = 90000.0,
) -> T2FitDiagnostics:
    """先验证 FID 衰减，再使用完整阻尼振荡拟合 T2。"""
    time_s = np.asarray(time_s, dtype=float)
    waveform_v = np.asarray(waveform_v, dtype=float)
    if time_s.ndim != 1 or waveform_v.ndim != 1 or len(time_s) != len(waveform_v):
        raise ValueError("T2 拟合要求长度相同的一维时间轴和波形")
    if len(time_s) < 8:
        raise ValueError("T2 拟合至少需要 8 个采样点")
    dt = float(np.median(np.diff(time_s)))
    if not np.isfinite(dt) or dt <= 0:
        raise ValueError("FID 时间轴必须严格递增")

    sample_rate = 1.0 / dt
    frequencies = np.fft.rfftfreq(len(waveform_v), dt)
    spectrum = np.abs(np.fft.rfft(waveform_v - np.mean(waveform_v)))
    band = (frequencies >= max(0.0, frequency_guess_hz - 5000.0)) & (
        frequencies <= frequency_guess_hz + 5000.0
    )
    if not np.any(band):
        raise ValueError(
            f"采样率 {sample_rate:.6g} Sa/s 无法覆盖 {frequency_guess_hz:.6g} Hz 拟合频带"
        )
    band_indices = np.flatnonzero(band)
    peak_index = band_indices[int(np.argmax(spectrum[band]))]
    peak_frequency = float(frequencies[peak_index])

    centered = waveform_v - np.mean(waveform_v)
    envelope = np.abs(hilbert(centered))
    samples_per_cycle = max(3, int(round(sample_rate / max(peak_frequency, 1.0))))
    smooth_points = min(max(3, samples_per_cycle * 3), max(3, len(envelope) // 20))
    if smooth_points % 2 == 0:
        smooth_points += 1
    smooth_envelope = np.convolve(
        envelope,
        np.ones(smooth_points, dtype=float) / smooth_points,
        mode="same",
    )
    edge_points = min(smooth_points, max(1, len(envelope) // 10))
    segment_points = max(8, len(envelope) // 10)
    initial_slice = smooth_envelope[edge_points:edge_points + segment_points]
    tail_stop = len(smooth_envelope) - edge_points
    tail_slice = smooth_envelope[max(edge_points, tail_stop - segment_points):tail_stop]
    initial_envelope = float(np.median(initial_slice))
    tail_envelope = float(np.median(tail_slice))
    envelope_tail_ratio = (
        tail_envelope / initial_envelope
        if np.isfinite(initial_envelope) and initial_envelope > 0
        else np.nan
    )
    if not np.isfinite(envelope_tail_ratio):
        return _failed_fit("invalid_envelope")
    if envelope_tail_ratio > _MAX_ENVELOPE_TAIL_RATIO:
        return _failed_fit("no_measurable_decay", envelope_tail_ratio)

    amplitude_guess = max(initial_envelope, float(np.max(smooth_envelope)), 1e-12)

    def exponential(time: np.ndarray, amplitude: float, t2_s: float, offset: float) -> np.ndarray:
        return amplitude * np.exp(-time / t2_s) + offset

    fit_length = max(4, int(len(envelope) * 0.8))
    try:
        envelope_fit, _ = curve_fit(
            exponential,
            time_s[:fit_length],
            smooth_envelope[:fit_length],
            p0=[amplitude_guess, 0.003, float(envelope[-1])],
            bounds=([0.0, _T2_MIN_S, -np.inf], [amplitude_guess * 10.0, 0.5, np.inf]),
            maxfev=5000,
        )
        amplitude_initial = abs(float(envelope_fit[0]))
        t2_initial = abs(float(envelope_fit[1]))
    except Exception:
        amplitude_initial = amplitude_guess
        t2_initial = 0.003

    frequency_low = max(peak_frequency - 200.0, frequency_guess_hz * 0.99)
    frequency_high = min(peak_frequency + 200.0, frequency_guess_hz * 1.01)
    if frequency_low >= frequency_high:
        frequency_low = peak_frequency - max(1.0, sample_rate / len(time_s))
        frequency_high = peak_frequency + max(1.0, sample_rate / len(time_s))
    try:
        optimum, covariance = curve_fit(
            damped_oscillation,
            time_s,
            waveform_v,
            p0=[amplitude_initial, t2_initial, peak_frequency, 0.0, float(np.mean(waveform_v))],
            bounds=(
                [0.0, _T2_MIN_S, frequency_low, -np.pi, -np.inf],
                [max(amplitude_initial * 5.0, 1e-11), _T2_MAX_S, frequency_high, np.pi, np.inf],
            ),
            maxfev=20000,
        )
        t2_s = abs(float(optimum[1]))
        variance = float(covariance[1, 1])
        uncertainty_s = np.sqrt(variance) if variance > 0 else 0.0
        fitted = damped_oscillation(time_s, *optimum)
        residual_sum = float(np.sum(np.square(waveform_v - fitted)))
        total_sum = float(np.sum(np.square(waveform_v - np.mean(waveform_v))))
        r_squared = 1.0 - residual_sum / total_sum if total_sum > 0 else np.nan
        if t2_s <= _T2_MIN_S * 1.01:
            return _failed_fit("t2_at_lower_bound", envelope_tail_ratio, optimum, r_squared)
        if t2_s >= _T2_MAX_S * 0.99:
            return _failed_fit("t2_at_upper_bound", envelope_tail_ratio, optimum, r_squared)
        if not np.isfinite(uncertainty_s) or uncertainty_s > _MAX_RELATIVE_UNCERTAINTY * t2_s:
            return _failed_fit("uncertainty_too_large", envelope_tail_ratio, optimum, r_squared)
        if not np.isfinite(r_squared) or r_squared < _MIN_R_SQUARED:
            return _failed_fit("low_fit_quality", envelope_tail_ratio, optimum, r_squared)
        return T2FitDiagnostics(
            t2_ms=t2_s * 1000.0,
            uncertainty_ms=uncertainty_s * 1000.0,
            optimum=optimum,
            success=True,
            reason="ok",
            envelope_tail_ratio=envelope_tail_ratio,
            r_squared=r_squared,
        )
    except Exception:
        return _failed_fit("optimizer_failed", envelope_tail_ratio)


def fit_t2(
    time_s: np.ndarray,
    waveform_v: np.ndarray,
    frequency_guess_hz: float = 90000.0,
) -> tuple[float, float, np.ndarray, bool]:
    """兼容旧调用方的 T2 拟合入口。"""
    result = fit_t2_diagnostics(time_s, waveform_v, frequency_guess_hz)
    return result.t2_ms, result.uncertainty_ms, result.optimum, result.success


def _require_run_directory(run_dir: Path) -> tuple[Path, Path]:
    run_dir = Path(run_dir).resolve()
    if not run_dir.is_dir():
        raise FileNotFoundError(f"T2 运行目录不存在: {run_dir}")
    raw_dir = run_dir / "raw"
    if not raw_dir.is_dir():
        raise FileNotFoundError(f"T2 原始数据目录不存在: {raw_dir}")
    results_dir = run_dir / "results"
    results_dir.mkdir(exist_ok=True)
    return raw_dir, results_dir


def _analyze_single(raw_dir: Path, results_dir: Path) -> dict[str, Any]:
    source = raw_dir / "fid_waveforms.npz"
    if not source.is_file():
        raise FileNotFoundError(f"T2 单功率原始数据不存在: {source}")
    with np.load(source) as loaded:
        average = np.asarray(loaded["avg_waveform"], dtype=float)
        time_axis = np.asarray(loaded["time"], dtype=float)
        waveforms = np.asarray(loaded["waveforms"], dtype=float)
        gate_frequency = float(loaded["rf_gate_freq"])
        trigger_slope = str(loaded["trigger_slope"]) if "trigger_slope" in loaded.files else "RISing"
        burst_duration = float(loaded["burst_duration"]) if "burst_duration" in loaded.files else 0.0

    if trigger_slope.upper().startswith("FALL"):
        fid_time = time_axis
        fid_waveform = average
    else:
        mask = time_axis > burst_duration
        fid_time = time_axis[mask] - burst_duration
        fid_waveform = average[mask]
    fit_result = fit_t2_diagnostics(
        fid_time, fid_waveform, gate_frequency
    )
    t2_ms = fit_result.t2_ms
    t2_uncertainty_ms = fit_result.uncertainty_ms
    optimum = fit_result.optimum
    success = fit_result.success
    amplitude, t2_s, fitted_frequency, phase, offset = optimum

    fig, axes = new_figure(
        figsize=(PAPER_WIDE[0], 2.0 * PAPER_WIDE[1]),
        nrows=2,
        ncols=2,
    )
    axis = axes[0, 0]
    axis.plot(fid_time * 1000, fid_waveform, color=COLOR_OPTIMAL, label="Data")
    if success:
        fitted = damped_oscillation(fid_time, *optimum)
        axis.plot(
            fid_time * 1000,
            fitted,
            "--",
            color=COLOR_TRAD,
            label=f"T2 = {t2_ms:.2f} ms",
        )
        envelope = amplitude * np.exp(-fid_time / t2_s)
        axis.plot(fid_time * 1000, envelope + offset, ":", color=COLOR_GRAY)
        axis.plot(fid_time * 1000, -envelope + offset, ":", color=COLOR_GRAY)
    format_axis(axis, xlabel="Time (ms)", ylabel="PD voltage (V)")
    axis.set_title(f"Optical FID - T2 = {t2_ms:.2f} ms")
    axis.set_xlim(0.0, _PLOT_SHORT_WINDOW_S * 1000.0)
    style_legend(axis)

    axis = axes[0, 1]
    zoom = min(_PLOT_SHORT_WINDOW_S, float(fid_time[-1]))
    zoom_mask = fid_time <= zoom
    axis.plot(
        fid_time[zoom_mask] * 1000,
        fid_waveform[zoom_mask],
        color=COLOR_OPTIMAL,
    )
    if success:
        axis.plot(
            fid_time[zoom_mask] * 1000,
            damped_oscillation(fid_time[zoom_mask], *optimum),
            "--",
            color=COLOR_TRAD,
        )
    format_axis(axis, xlabel="Time (ms)", ylabel="PD voltage (V)")
    axis.set_title(f"First {zoom * 1000:.0f} ms (zoom)")
    axis.set_xlim(0.0, _PLOT_SHORT_WINDOW_S * 1000.0)

    axis = axes[1, 0]
    if success:
        residual = fid_waveform - damped_oscillation(fid_time, *optimum)
        axis.plot(fid_time * 1000, residual, color=COLOR_GRAY)
        axis.axhline(0, color=COLOR_GRAY, linestyle=":")
        axis.set_title(f"Residual (RMS = {np.std(residual):.4f} V)")
    else:
        axis.text(
            0.5,
            0.5,
            f"Fit rejected: {fit_result.reason.replace('_', ' ')}",
            transform=axis.transAxes,
            ha="center",
            va="center",
        )
        axis.set_title("Residual")
    format_axis(axis, xlabel="Time (ms)", ylabel="Residual (V)")
    axis.set_xlim(0.0, _PLOT_SHORT_WINDOW_S * 1000.0)

    axis = axes[1, 1]
    for waveform in waveforms[:20]:
        axis.plot(time_axis * 1000, waveform, alpha=0.25, color=COLOR_GRAY)
    axis.plot(time_axis * 1000, average, color=COLOR_TRAD, label="Average")
    format_axis(axis, xlabel="Time (ms)", ylabel="PD voltage (V)")
    axis.set_title(f"All {min(len(waveforms), 20)} Shots + Average")
    axis.set_xlim(0.0, _PLOT_SHORT_WINDOW_S * 1000.0)
    style_legend(axis)
    figure_path = results_dir / "T2_fid_analysis.png"
    save_figure(fig, figure_path)

    result = {
        "method": "optical_FID",
        "T2_ms": float(t2_ms),
        "T2_err_ms": float(t2_uncertainty_ms),
        "f_L_fit_Hz": float(fitted_frequency),
        "f_L_set_Hz": gate_frequency,
        "amplitude_V": float(amplitude),
        "phase_rad": float(phase),
        "offset_V": float(offset),
        "fit_success": success,
        "fit_reason": fit_result.reason,
        "envelope_tail_ratio": float(fit_result.envelope_tail_ratio),
        "r_squared": float(fit_result.r_squared),
    }
    with (results_dir / "T2_results.yaml").open("w", encoding="utf-8") as stream:
        yaml.safe_dump(result, stream, allow_unicode=True, sort_keys=False)
    return {**result, "artifacts": [str(figure_path), str(results_dir / "T2_results.yaml")]}


def _analyze_power_scan(raw_dir: Path, results_dir: Path) -> dict[str, Any]:
    source = raw_dir / "power_scan_waveforms.npz"
    with np.load(source) as loaded:
        time_axis = np.asarray(loaded["t_axis"], dtype=float)
        average_stack = np.asarray(loaded["avg_stack"], dtype=float)
        powers = np.asarray(loaded["probe_power"], dtype=float)
    point_zero = raw_dir / "fid_p00.npz"
    if point_zero.is_file():
        with np.load(point_zero) as metadata:
            gate_frequency = float(metadata["rf_gate_freq"])
    else:
        gate_frequency = 90000.0

    t2_values = np.full(len(powers), np.nan)
    t2_uncertainties = np.full(len(powers), np.nan)
    optimums: list[np.ndarray] = []
    successes = np.zeros(len(powers), dtype=bool)
    fit_reasons: list[str] = []
    envelope_tail_ratios = np.full(len(powers), np.nan)
    r_squared_values = np.full(len(powers), np.nan)
    for index, waveform in enumerate(average_stack):
        fit_result = fit_t2_diagnostics(time_axis, waveform, gate_frequency)
        t2_values[index] = fit_result.t2_ms
        t2_uncertainties[index] = fit_result.uncertainty_ms
        optimums.append(fit_result.optimum)
        successes[index] = fit_result.success
        fit_reasons.append(fit_result.reason)
        envelope_tail_ratios[index] = fit_result.envelope_tail_ratio
        r_squared_values[index] = fit_result.r_squared

    artifacts: list[str] = []
    fig, axis = new_figure()
    valid_plot = successes & np.isfinite(t2_values) & np.isfinite(t2_uncertainties)
    if np.any(valid_plot):
        axis.errorbar(
            powers[valid_plot],
            t2_values[valid_plot],
            yerr=t2_uncertainties[valid_plot],
            fmt="o-",
            capsize=3,
            color=COLOR_OPTIMAL,
        )
    else:
        axis.text(
            0.5,
            0.5,
            "No valid FID fits",
            transform=axis.transAxes,
            ha="center",
            va="center",
        )
    format_axis(axis, xlabel="Probe power (V)", ylabel="T2 (ms)")
    path = results_dir / "T2_vs_probe_power.png"
    save_figure(fig, path)
    artifacts.append(str(path))

    valid = successes & np.isfinite(t2_values) & (t2_values > 0)
    valid_powers = powers[valid]
    inverse_t2 = 1.0 / t2_values[valid]
    inverse_uncertainty = t2_uncertainties[valid] / np.square(t2_values[valid])
    t2_zero_ms = np.nan
    alpha = np.nan
    inverse_path = results_dir / "inv_T2_vs_probe_power.png"
    if len(valid_powers) >= 2:
        coefficients = np.polyfit(
            valid_powers,
            inverse_t2,
            1,
            w=1.0 / np.maximum(inverse_uncertainty, 1e-6),
        )
        alpha = float(coefficients[0])
        intercept = float(coefficients[1])
        t2_zero_ms = 1.0 / intercept if intercept > 0 else np.inf
        polynomial = np.poly1d(coefficients)
        fig, axis = new_figure()
        axis.errorbar(
            valid_powers,
            inverse_t2,
            yerr=inverse_uncertainty,
            fmt="o",
            capsize=3,
            color=COLOR_OPTIMAL,
            label="Data",
        )
        fit_powers = np.linspace(valid_powers.min(), valid_powers.max(), 50)
        axis.plot(
            fit_powers,
            polynomial(fit_powers),
            "--",
            color=COLOR_TRAD,
            label=f"T2,0^-1={intercept:.2f} ms^-1, alpha={alpha:.3f}",
        )
        format_axis(
            axis,
            xlabel="Probe power (V)",
            ylabel="T2^-1 (ms^-1)",
        )
        style_legend(axis)
        save_figure(fig, inverse_path)
        artifacts.append(str(inverse_path))
    else:
        inverse_path.unlink(missing_ok=True)

    columns = min(5, len(powers))
    rows = int(np.ceil(len(powers) / columns))
    overview_plots = (
        (_PLOT_SHORT_WINDOW_S, results_dir / "T2_all_fid_curves.png"),
        (_PLOT_LONG_WINDOW_S, results_dir / "T2_all_fid_curves_0_10ms.png"),
    )
    for plot_window_s, path in overview_plots:
        fig, axes = new_figure(
            figsize=(PAPER_WIDE[0], 1.9 * rows),
            nrows=rows,
            ncols=columns,
            squeeze=False,
        )
        flat_axes = axes.flatten()
        for index, axis in enumerate(flat_axes):
            if index >= len(powers):
                axis.set_visible(False)
                continue
            axis.plot(
                time_axis * 1000,
                average_stack[index],
                color=COLOR_OPTIMAL,
            )
            if successes[index]:
                axis.plot(
                    time_axis * 1000,
                    damped_oscillation(time_axis, *optimums[index]),
                    "--",
                    color=COLOR_TRAD,
                )
                axis.text(
                    0.97,
                    0.92,
                    f"T2={t2_values[index]:.1f} ms",
                    transform=axis.transAxes,
                    ha="right",
                    va="top",
                    color=COLOR_TRAD,
                )
            else:
                axis.text(
                    0.97,
                    0.92,
                    fit_reasons[index].replace("_", " "),
                    transform=axis.transAxes,
                    ha="right",
                    va="top",
                    color=COLOR_TRAD,
                )
            axis.set_title(f"Probe = {powers[index]:.3f} V")
            format_axis(axis, xlabel="Time (ms)", ylabel="PD voltage (V)")
            axis.set_xlim(0.0, plot_window_s * 1000.0)
        save_figure(fig, path)
        artifacts.append(str(path))

    result = {
        "T2_0_ms": float(t2_zero_ms),
        "alpha": float(alpha),
        "probe_power": powers.tolist(),
        "T2_ms": t2_values.tolist(),
        "T2_err_ms": t2_uncertainties.tolist(),
        "fit_success": successes.tolist(),
        "fit_reason": fit_reasons,
        "envelope_tail_ratio": envelope_tail_ratios.tolist(),
        "r_squared": r_squared_values.tolist(),
        "inv_T2": inverse_t2.tolist(),
        "f_L_set_Hz": gate_frequency,
    }
    result_path = results_dir / "analysis.yaml"
    with result_path.open("w", encoding="utf-8") as stream:
        yaml.safe_dump(result, stream, allow_unicode=True, sort_keys=False)
    artifacts.append(str(result_path))
    return {**result, "artifacts": artifacts}


def analyze(run_dir: Path) -> dict[str, Any]:
    set_plot_style("paper")
    """只分析给定运行目录中的 raw，并将结果写入同目录 results。"""
    raw_dir, results_dir = _require_run_directory(run_dir)
    power_scan = raw_dir / "power_scan_waveforms.npz"
    if power_scan.is_file():
        return _analyze_power_scan(raw_dir, results_dir)
    return _analyze_single(raw_dir, results_dir)


def main() -> int:
    result = analyze(runtime_run_dir())
    print(f"T2 离线分析完成，共生成 {len(result.get('artifacts', []))} 个结果文件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
