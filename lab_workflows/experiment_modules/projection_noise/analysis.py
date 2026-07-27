"""SDS 原始 PD 投影噪声谱离线分析与洛伦兹拟合。"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import matplotlib
import numpy as np
import yaml
from scipy import signal as scipy_signal
from scipy.optimize import least_squares
from sds_acquisition import load_npz

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
from .legacy_analysis import analyze_legacy
from .models import ProjectionNoiseParams

matplotlib.use("Agg")


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


def lorentzian_psd(
    frequency_hz: np.ndarray,
    amplitude_v2_hz: float,
    gamma_hz: float,
    center_hz: float,
    offset_v2_hz: float,
) -> np.ndarray:
    """带常数差值背景的单峰洛伦兹 PSD。"""
    frequency_hz = np.asarray(frequency_hz, dtype=float)
    return offset_v2_hz + amplitude_v2_hz * gamma_hz**2 / (
        (frequency_hz - center_hz) ** 2 + gamma_hz**2
    )


def _load_params(run_dir: Path) -> tuple[ProjectionNoiseParams, dict[str, Any]]:
    config_path = run_dir / "experiment_config.yaml"
    if not config_path.is_file():
        raise FileNotFoundError(f"未找到实验配置: {config_path}")
    with config_path.open(encoding="utf-8") as stream:
        config = yaml.safe_load(stream) or {}
    params = ProjectionNoiseParams.from_external(
        config.get("parameters", {}),
        schema_version=int(config.get("schema_version", 2)),
    )
    return params, config


def _indexed_files(
    raw_dir: Path, *, require_field_off: bool
) -> dict[str, list[Path]]:
    index_path = raw_dir / "scope_acquisition_index.npz"
    phases: dict[str, list[Path]] = {"field_off": [], "field_on": []}
    if index_path.is_file():
        with np.load(index_path) as data:
            phase_values = np.asarray(data["phase"], dtype=str)
            file_key = "relative_file" if "relative_file" in data.files else "file"
            file_values = np.asarray(data[file_key], dtype=str)
        if phase_values.size != file_values.size:
            raise ValueError("示波器采集索引的 phase/file 长度不一致")
        run_dir = raw_dir.parent
        for phase, relative in zip(phase_values, file_values, strict=True):
            if phase not in phases:
                continue
            phases[phase].append(run_dir / relative)
    else:
        for phase in phases:
            phases[phase] = sorted((raw_dir / phase).glob("waveform_*.npz"))
    for phase, files in phases.items():
        if phase == "field_off" and not require_field_off and not files:
            continue
        if not files:
            raise FileNotFoundError(f"缺少 {phase} 示波器波形")
        missing = [path for path in files if not path.is_file()]
        if missing:
            raise FileNotFoundError(f"{phase} 索引包含缺失文件: {missing[0]}")
    return phases


def _load_phase_psd(
    files: list[Path], nperseg: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    spectra: list[np.ndarray] = []
    rates: list[float] = []
    durations: list[float] = []
    reference_frequency: np.ndarray | None = None
    for path in files:
        results, _ = load_npz(str(path))
        if len(results) != 1:
            raise ValueError(f"每个投影噪声帧必须仅包含一个通道: {path}")
        result = results[0]
        time_s = np.asarray(result.time, dtype=float).reshape(-1)
        voltage_v = np.asarray(result.voltage, dtype=float).reshape(-1)
        if time_s.size != voltage_v.size or time_s.size < nperseg:
            raise ValueError(f"波形点数不足或时间轴不一致: {path}")
        if not np.all(np.isfinite(voltage_v)):
            raise ValueError(f"波形包含非有限值: {path}")
        sample_interval_s = float(np.median(np.diff(time_s)))
        if not np.isfinite(sample_interval_s) or sample_interval_s <= 0.0:
            raise ValueError(f"实际采样间隔无效: {path}")
        actual_rate = 1.0 / sample_interval_s
        frequency, psd = scipy_signal.welch(
            voltage_v,
            fs=actual_rate,
            window="hann",
            nperseg=nperseg,
            noverlap=nperseg // 2,
            detrend="constant",
            scaling="density",
            return_onesided=True,
        )
        if reference_frequency is None:
            reference_frequency = frequency
        elif not np.allclose(
            reference_frequency, frequency, rtol=1e-8, atol=1e-8
        ):
            raise ValueError("示波器帧的实际采样率不一致，无法直接平均 PSD")
        spectra.append(psd)
        rates.append(actual_rate)
        durations.append(sample_interval_s * (time_s.size - 1))
    assert reference_frequency is not None
    matrix = np.asarray(spectra, dtype=float)
    mean = np.mean(matrix, axis=0)
    std = np.std(matrix, axis=0, ddof=1) if matrix.shape[0] > 1 else np.zeros_like(mean)
    sem = std / np.sqrt(matrix.shape[0])
    return (
        reference_frequency,
        mean,
        sem,
        np.asarray(rates, dtype=float),
        np.asarray(durations, dtype=float),
    )


def fit_spin_noise_lorentzian(
    frequency_hz: np.ndarray,
    fit_psd_v2_hz: np.ndarray,
    sigma_v2_hz: np.ndarray,
    *,
    predicted_center_hz: float,
    fit_half_width_hz: float,
) -> dict[str, Any]:
    """在预测中心窗口内执行带稳健损失的四参数洛伦兹拟合。"""
    frequency_hz = np.asarray(frequency_hz, dtype=float)
    fit_psd = np.asarray(fit_psd_v2_hz, dtype=float)
    sigma = np.asarray(sigma_v2_hz, dtype=float)
    fit_min = predicted_center_hz - fit_half_width_hz
    fit_max = predicted_center_hz + fit_half_width_hz
    mask = (
        np.isfinite(frequency_hz)
        & np.isfinite(fit_psd)
        & (frequency_hz >= fit_min)
        & (frequency_hz <= fit_max)
    )
    x = frequency_hz[mask]
    y = fit_psd[mask]
    if x.size < 10:
        return {
            "success": False,
            "reason": "拟合频带内有效频点不足 10 个",
            "fit_mask": mask,
        }
    df = float(np.median(np.diff(x)))
    edge_count = max(2, x.size // 10)
    baseline = float(np.median(np.r_[y[:edge_count], y[-edge_count:]]))
    peak_index = int(np.argmax(y))
    amplitude_guess = float(max(y[peak_index] - baseline, np.std(y), np.finfo(float).tiny))
    center_guess = float(np.clip(x[peak_index], fit_min + df, fit_max - df))
    gamma_guess = float(np.clip(500.0, df, fit_half_width_hz))
    positive_sigma = sigma[mask]
    valid_sigma = np.isfinite(positive_sigma) & (positive_sigma > 0.0)
    sigma_floor = (
        float(np.median(positive_sigma[valid_sigma]))
        if np.any(valid_sigma)
        else max(float(np.std(y)) * 1e-3, np.finfo(float).tiny)
    )
    weights = np.where(valid_sigma, positive_sigma, sigma_floor)

    def residual(parameters: np.ndarray) -> np.ndarray:
        return (
            lorentzian_psd(x, *parameters) - y
        ) / weights

    try:
        fitted = least_squares(
            residual,
            x0=np.asarray([amplitude_guess, gamma_guess, center_guess, baseline]),
            bounds=(
                np.asarray([0.0, df, fit_min, -np.inf]),
                np.asarray([np.inf, fit_half_width_hz, fit_max, np.inf]),
            ),
            loss="soft_l1",
            f_scale=1.0,
            x_scale="jac",
            max_nfev=20000,
        )
    except (TypeError, ValueError) as exc:
        return {"success": False, "reason": str(exc), "fit_mask": mask}
    parameters = np.asarray(fitted.x, dtype=float)
    predicted = lorentzian_psd(x, *parameters)
    residual_unweighted = y - predicted
    ss_res = float(np.sum(residual_unweighted**2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0.0 else float("nan")
    dof = max(1, x.size - parameters.size)
    try:
        covariance = np.linalg.inv(fitted.jac.T @ fitted.jac)
        covariance *= 2.0 * float(fitted.cost) / dof
        uncertainties = np.sqrt(np.maximum(np.diag(covariance), 0.0))
    except np.linalg.LinAlgError:
        covariance = np.full((4, 4), np.nan)
        uncertainties = np.full(4, np.nan)
    amplitude, gamma, center, offset = parameters
    success = bool(
        fitted.success
        and np.all(np.isfinite(parameters))
        and amplitude > 0.0
        and gamma > df
        and fit_min < center < fit_max
    )
    reason = "" if success else (fitted.message or "拟合未满足物理边界")
    return {
        "success": success,
        "reason": reason,
        "fit_mask": mask,
        "frequency_hz": x,
        "observed_psd_v2_hz": y,
        "fitted_psd_v2_hz": predicted,
        "residual_psd_v2_hz": residual_unweighted,
        "parameters": parameters,
        "uncertainties": uncertainties,
        "covariance": covariance,
        "r_squared": r_squared,
        "optimizer_message": str(fitted.message),
        "amplitude_v2_hz": float(amplitude),
        "gamma_hz": float(gamma),
        "center_hz": float(center),
        "offset_v2_hz": float(offset),
        "fwhm_hz": float(2.0 * gamma),
        "t2_s": float(1.0 / (2.0 * np.pi * gamma)),
        "lorentzian_area_v2": float(np.pi * amplitude * gamma),
        "fit_min_hz": float(fit_min),
        "fit_max_hz": float(fit_max),
        "frequency_resolution_hz": df,
    }


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
        axes[2].text(0.5, 0.5, f"Fit failed: {fit.get('reason', '')}", ha="center", va="center", transform=axes[2].transAxes)
    axes[2].axhline(0.0, color=COLOR_GRAY)
    format_axis(
        axes[2],
        xlabel="Frequency (kHz)",
        ylabel="Residual (V²/Hz)",
    )
    save_figure(fig, results_dir / filename)
    return filename


def analyze(run_dir: Path) -> dict[str, Any]:
    """只读取指定运行目录，生成 PSD、洛伦兹拟合和英文图表。"""
    set_plot_style("paper")
    run_dir = Path(run_dir).resolve()
    raw_dir = run_dir / "raw"
    if (raw_dir / "waveforms_phase1_light.npz").is_file() and (
        raw_dir / "waveforms_thermal.npz"
    ).is_file():
        return analyze_legacy(run_dir)

    params, config = _load_params(run_dir)
    results_dir = run_dir / "results"
    results_dir.mkdir(exist_ok=True)
    files = _indexed_files(
        raw_dir, require_field_off=params.measure_field_off_control
    )
    on = _load_phase_psd(files["field_on"], params.welch_nperseg)
    frequency_hz, on_mean, on_sem, on_rates, on_durations = on
    off_mean: np.ndarray | None = None
    off_sem: np.ndarray | None = None
    off_rates: np.ndarray | None = None
    off_durations: np.ndarray | None = None
    if files["field_off"]:
        off = _load_phase_psd(files["field_off"], params.welch_nperseg)
        frequency_off, off_mean, off_sem, off_rates, off_durations = off
        if not np.allclose(frequency_hz, frequency_off, rtol=1e-8, atol=1e-8):
            raise ValueError("GS200 OFF/ON 两阶段 PSD 频率轴不一致")
        fit_psd = on_mean - off_mean
        fit_sem = np.sqrt(on_sem**2 + off_sem**2)
        analysis_mode = "field_on_minus_field_off"
        fit_input_label = "PSD difference (V²/Hz)"
    else:
        fit_psd = on_mean
        fit_sem = on_sem
        analysis_mode = "field_on_only"
        fit_input_label = "Field-on PSD (V²/Hz)"
    calibration = config.get("main_field_calibration", {})
    predicted_center_hz = float(
        calibration.get(
            "predicted_larmor_frequency_hz",
            params.target_larmor_frequency_hz,
        )
    )
    fit = fit_spin_noise_lorentzian(
        frequency_hz,
        fit_psd,
        fit_sem,
        predicted_center_hz=predicted_center_hz,
        fit_half_width_hz=params.fit_half_width_hz,
    )
    spectra_payload: dict[str, Any] = {
        "frequency_hz": frequency_hz,
        "field_on_mean_psd_v2_hz": on_mean,
        "field_on_sem_psd_v2_hz": on_sem,
        "fit_input_psd_v2_hz": fit_psd,
        "fit_input_sem_psd_v2_hz": fit_sem,
        "field_on_actual_rates_sa_s": on_rates,
        "field_on_actual_durations_s": on_durations,
        "control_group_measured": np.bool_(off_mean is not None),
        "analysis_mode": np.asarray(analysis_mode),
        "welch_nperseg": np.int64(params.welch_nperseg),
        "predicted_center_hz": np.float64(predicted_center_hz),
        "fit_mask": np.asarray(fit["fit_mask"], dtype=bool),
    }
    if off_mean is not None:
        assert off_sem is not None
        assert off_rates is not None
        assert off_durations is not None
        spectra_payload.update(
            {
                "field_off_mean_psd_v2_hz": off_mean,
                "field_off_sem_psd_v2_hz": off_sem,
                "delta_psd_v2_hz": fit_psd,
                "delta_sem_psd_v2_hz": fit_sem,
                "field_off_actual_rates_sa_s": off_rates,
                "field_off_actual_durations_s": off_durations,
            }
        )
    np.savez(results_dir / "psd_spectra.npz", **spectra_payload)
    fit_summary = {
        key: value
        for key, value in fit.items()
        if key
        not in {
            "fit_mask",
            "frequency_hz",
            "observed_psd_v2_hz",
            "fitted_psd_v2_hz",
            "residual_psd_v2_hz",
            "covariance",
        }
    }
    if fit.get("success"):
        np.savez(
            results_dir / "lorentzian_fit.npz",
            parameters=np.asarray(fit["parameters"], dtype=float),
            uncertainties=np.asarray(fit["uncertainties"], dtype=float),
            covariance=np.asarray(fit["covariance"], dtype=float),
            frequency_hz=np.asarray(fit["frequency_hz"], dtype=float),
            observed_psd_v2_hz=np.asarray(fit["observed_psd_v2_hz"], dtype=float),
            fitted_psd_v2_hz=np.asarray(fit["fitted_psd_v2_hz"], dtype=float),
            residual_psd_v2_hz=np.asarray(fit["residual_psd_v2_hz"], dtype=float),
        )
    else:
        np.savez(results_dir / "lorentzian_fit.npz", success=np.bool_(False))

    overview_file = _plot_overview(
        results_dir,
        frequency_hz,
        off_mean,
        on_mean,
        fit_psd,
        predicted_center_hz,
        fit_input_label=fit_input_label,
    )
    fit_file = _plot_fit(
        results_dir,
        frequency_hz,
        off_mean,
        on_mean,
        fit_psd,
        fit,
        fit_input_label=fit_input_label,
    )
    csv_path = results_dir / "lorentzian_fit.csv"
    csv_fields = (
        "success",
        "amplitude_v2_hz",
        "gamma_hz",
        "center_hz",
        "offset_v2_hz",
        "fwhm_hz",
        "t2_s",
        "lorentzian_area_v2",
        "r_squared",
        "reason",
    )
    with csv_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=csv_fields)
        writer.writeheader()
        writer.writerow({field: fit_summary.get(field, "") for field in csv_fields})

    payload = {
        "success": bool(fit.get("success", False)),
        "experiment_id": "projection-noise",
        "acquisition_backend": "sds_pd_raw",
        "run_dir": str(run_dir),
        "control_group_measured": off_mean is not None,
        "analysis_mode": analysis_mode,
        "phase_frame_counts": {
            "field_off": len(files["field_off"]),
            "field_on": len(files["field_on"]),
        },
        "welch": {
            "window": "hann",
            "nperseg": params.welch_nperseg,
            "noverlap": params.welch_nperseg // 2,
            "detrend": "constant",
            "field_on_actual_rate_min_sa_s": float(np.min(on_rates)),
            "field_on_actual_rate_max_sa_s": float(np.max(on_rates)),
        },
        "fit_model": "C + A*gamma^2/((f-f0)^2+gamma^2)",
        "fit": fit_summary,
        "pnl_metrics_emitted": False,
        "files": [
            "psd_spectra.npz",
            "lorentzian_fit.npz",
            "lorentzian_fit.csv",
            overview_file,
            fit_file,
            "analysis.yaml",
            "analysis.json",
        ],
    }
    if off_rates is not None:
        payload["welch"].update(
            {
                "field_off_actual_rate_min_sa_s": float(np.min(off_rates)),
                "field_off_actual_rate_max_sa_s": float(np.max(off_rates)),
            }
        )
    with (results_dir / "analysis.yaml").open("w", encoding="utf-8") as stream:
        yaml.safe_dump(_builtin(payload), stream, allow_unicode=True, sort_keys=False)
    with (results_dir / "analysis.json").open("w", encoding="utf-8") as stream:
        json.dump(_builtin(payload), stream, ensure_ascii=False, indent=2)
    print(
        "投影噪声示波器分析完成："
        + (f"f0={fit['center_hz']:.6g} Hz，FWHM={fit['fwhm_hz']:.6g} Hz" if fit.get("success") else f"拟合失败：{fit.get('reason', '')}")
    )
    return payload


def main() -> int:
    analyze(runtime_run_dir())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
