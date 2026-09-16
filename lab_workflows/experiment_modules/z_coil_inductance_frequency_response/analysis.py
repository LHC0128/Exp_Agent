"""Z 线圈电感效应频率响应离线分析器。"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import yaml
from scipy.optimize import curve_fit

from ...experiment_runtime import runtime_run_dir
from ...plotting import format_axis, new_figure, save_figure, set_plot_style


EXPERIMENT_ID = "z-coil-inductance-frequency-response"


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


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"缺少分析输入: {path}")
    with path.open(encoding="utf-8") as stream:
        payload = yaml.safe_load(stream) or {}
    if not isinstance(payload, dict):
        raise TypeError(f"YAML 顶层必须是映射: {path}")
    return payload


def _as_scalar(data: Any, key: str, default: float | None = None) -> float:
    if key not in data:
        if default is None:
            raise ValueError(f"缺少标量字段: {key}")
        return float(default)
    value = float(np.asarray(data[key], dtype=float).reshape(()))
    if not np.isfinite(value):
        raise ValueError(f"字段 {key} 不是有限数值")
    return value


def _load_capture(path: Path) -> dict[str, Any]:
    with np.load(path, allow_pickle=False) as data:
        required = {
            "time_s",
            "measured_voltage_v",
            "trigger_time_s",
            "trigger_voltage_v",
        }
        missing = sorted(required.difference(data.files))
        if missing:
            raise ValueError(f"{path.name} 缺少字段: {', '.join(missing)}")
        result: dict[str, Any] = {
            "time_s": np.asarray(data["time_s"], dtype=float).reshape(-1),
            "measured_voltage_v": np.asarray(
                data["measured_voltage_v"], dtype=float
            ).reshape(-1),
            "trigger_time_s": np.asarray(data["trigger_time_s"], dtype=float).reshape(-1),
            "trigger_voltage_v": np.asarray(
                data["trigger_voltage_v"], dtype=float
            ).reshape(-1),
        }
        for key in (
            "theory_time_s",
            "theory_voltage_v",
            "frequency_hz",
            "drive_amplitude_vpp",
            "drive_offset_v",
            "trigger_edge_time_s",
            "actual_rate_sa_s",
            "scale_used_v_div",
            "offset_used_v",
            "reference_voltage_v",
        ):
            if key in data.files:
                result[key] = np.asarray(data[key], dtype=float).copy()
    for key in ("time_s", "measured_voltage_v", "trigger_time_s", "trigger_voltage_v"):
        values = np.asarray(result[key], dtype=float)
        if values.size < 4 or not np.all(np.isfinite(values)):
            raise ValueError(f"{path.name} 的 {key} 无有效数据")
    if result["time_s"].size != result["measured_voltage_v"].size:
        raise ValueError(f"{path.name} 的 CH3 时间轴和电压长度不一致")
    if result["trigger_time_s"].size != result["trigger_voltage_v"].size:
        raise ValueError(f"{path.name} 的 CH4 时间轴和电压长度不一致")
    if not np.all(np.diff(result["time_s"]) > 0):
        raise ValueError(f"{path.name} 的 CH3 时间轴必须严格递增")
    if not np.all(np.diff(result["trigger_time_s"]) > 0):
        raise ValueError(f"{path.name} 的 CH4 时间轴必须严格递增")
    return result


def falling_edge_time(time_s: np.ndarray, voltage_v: np.ndarray, level_v: float) -> float:
    """用线性插值返回第一个下降沿穿越触发电平的时刻。"""
    time_s = np.asarray(time_s, dtype=float)
    voltage_v = np.asarray(voltage_v, dtype=float)
    indices = np.flatnonzero((voltage_v[:-1] >= level_v) & (voltage_v[1:] < level_v))
    if indices.size == 0:
        raise ValueError("CH4 未检测到下降沿触发")
    index = int(indices[0])
    t0, t1 = float(time_s[index]), float(time_s[index + 1])
    v0, v1 = float(voltage_v[index]), float(voltage_v[index + 1])
    if v1 == v0:
        return t0
    fraction = (level_v - v0) / (v1 - v0)
    return t0 + float(np.clip(fraction, 0.0, 1.0)) * (t1 - t0)


def fit_sine(
    time_s: np.ndarray,
    voltage_v: np.ndarray,
    frequency_hz: float,
    expected_amplitude_vpp: float,
    expected_offset_v: float = 0.0,
) -> dict[str, float]:
    """拟合 offset + a*sin + b*cos，并返回相对于理想正弦的幅相指标。"""
    time_s = np.asarray(time_s, dtype=float).reshape(-1)
    voltage_v = np.asarray(voltage_v, dtype=float).reshape(-1)
    if time_s.size != voltage_v.size or time_s.size < 4:
        raise ValueError("正弦拟合输入长度不足或不一致")
    if not np.all(np.isfinite(time_s)) or not np.all(np.isfinite(voltage_v)):
        raise ValueError("正弦拟合输入包含非有限值")
    omega_t = 2.0 * np.pi * float(frequency_hz) * time_s
    design = np.column_stack((np.ones_like(time_s), np.sin(omega_t), np.cos(omega_t)))
    coefficients, _, rank, _ = np.linalg.lstsq(design, voltage_v, rcond=None)
    if int(rank) < 3:
        raise ValueError("正弦拟合设计矩阵秩不足")
    offset, sin_coefficient, cos_coefficient = map(float, coefficients)
    amplitude_v = math.hypot(sin_coefficient, cos_coefficient)
    phase_deg = float(np.rad2deg(np.arctan2(cos_coefficient, sin_coefficient)))
    phase_deg = float((phase_deg + 180.0) % 360.0 - 180.0)
    fitted = design @ coefficients
    residual = voltage_v - fitted
    residual_rms_v = float(np.sqrt(np.mean(residual * residual)))
    centered_measured = voltage_v - float(np.mean(voltage_v))
    centered_fitted = fitted - float(np.mean(fitted))
    measured_std = float(np.std(centered_measured))
    fitted_std = float(np.std(centered_fitted))
    correlation = (
        float(np.dot(centered_measured, centered_fitted) / voltage_v.size / measured_std / fitted_std)
        if measured_std > 0.0 and fitted_std > 0.0
        else float("nan")
    )
    expected_amplitude_v = 0.5 * float(expected_amplitude_vpp)
    gain = amplitude_v / expected_amplitude_v if expected_amplitude_v > 0.0 else float("nan")
    gain_db = 20.0 * np.log10(gain) if np.isfinite(gain) and gain > 0.0 else float("nan")
    return {
        "amplitude_v": amplitude_v,
        "expected_amplitude_v": expected_amplitude_v,
        "gain": float(gain),
        "gain_db": float(gain_db),
        "phase_deg": phase_deg,
        "offset_v": offset,
        "expected_offset_v": float(expected_offset_v),
        "residual_rms_v": residual_rms_v,
        "correlation": correlation,
        "measured_peak_to_peak_v": float(np.ptp(voltage_v)),
    }


def _circular_mean_deg(values: np.ndarray) -> float:
    radians = np.deg2rad(np.asarray(values, dtype=float))
    return float(np.rad2deg(np.angle(np.mean(np.exp(1j * radians)))))


def _circular_std_deg(values: np.ndarray) -> float:
    radians = np.deg2rad(np.asarray(values, dtype=float))
    resultant = float(np.abs(np.mean(np.exp(1j * radians))))
    return float(np.rad2deg(np.sqrt(max(0.0, -2.0 * np.log(max(resultant, 1e-15))))))


def normalize_amplitude_to_reference(
    frequency_hz: np.ndarray,
    amplitude_v: np.ndarray,
    amplitude_std_v: np.ndarray | None = None,
) -> dict[str, Any]:
    """按最低测量频率归一化幅值，并返回变化百分比和 dB。"""
    frequency_hz = np.asarray(frequency_hz, dtype=float).reshape(-1)
    amplitude_v = np.asarray(amplitude_v, dtype=float).reshape(-1)
    if frequency_hz.size == 0 or frequency_hz.size != amplitude_v.size:
        raise ValueError("频率轴和幅值数组必须非空且长度一致")
    finite = np.isfinite(frequency_hz) & np.isfinite(amplitude_v)
    if not np.any(finite):
        raise ValueError("归一化没有有限的频率或幅值")
    reference_index = int(np.flatnonzero(finite)[np.argmin(frequency_hz[finite])])
    reference_amplitude_v = float(amplitude_v[reference_index])
    if not np.isfinite(reference_amplitude_v) or reference_amplitude_v <= 0.0:
        raise ValueError("归一化参考幅值必须为正")
    normalized = amplitude_v / reference_amplitude_v
    normalized_std = np.full_like(normalized, np.nan)
    if amplitude_std_v is not None:
        amplitude_std_v = np.asarray(amplitude_std_v, dtype=float).reshape(-1)
        if amplitude_std_v.size != amplitude_v.size:
            raise ValueError("幅值标准差数组长度不一致")
        normalized_std = np.abs(amplitude_std_v / reference_amplitude_v)
    change_fraction = normalized - 1.0
    change_db = np.full_like(normalized, np.nan)
    positive = normalized > 0.0
    change_db[positive] = 20.0 * np.log10(normalized[positive])
    return {
        "reference_index": reference_index,
        "reference_frequency_hz": float(frequency_hz[reference_index]),
        "reference_amplitude_v": reference_amplitude_v,
        "normalized_amplitude": normalized,
        "normalized_amplitude_std": normalized_std,
        "amplitude_change_fraction": change_fraction,
        "amplitude_change_percent": 100.0 * change_fraction,
        "amplitude_drop_percent": -100.0 * change_fraction,
        "amplitude_change_db": change_db,
    }


def _inductive_voltage_divider_model(
    frequency_hz: np.ndarray,
    amplitude_zero_frequency_v: float,
    amplitude_high_frequency_v: float,
    corner_frequency_hz: float,
) -> np.ndarray:
    """一阶串联 RL 端电压幅值模型，不将参数解释为唯一电感值。"""
    frequency_hz = np.asarray(frequency_hz, dtype=float)
    ratio = frequency_hz / float(corner_frequency_hz)
    return np.sqrt(
        (
            float(amplitude_zero_frequency_v) ** 2
            + float(amplitude_high_frequency_v) ** 2 * ratio**2
        )
        / (1.0 + ratio**2)
    )


def fit_inductive_voltage_response(
    frequency_hz: np.ndarray,
    amplitude_v: np.ndarray,
    amplitude_std_v: np.ndarray | None = None,
) -> dict[str, Any]:
    """拟合端电压幅值的零频/高频极限和转折频率。"""
    frequency_hz = np.asarray(frequency_hz, dtype=float).reshape(-1)
    amplitude_v = np.asarray(amplitude_v, dtype=float).reshape(-1)
    valid = (
        np.isfinite(frequency_hz)
        & np.isfinite(amplitude_v)
        & (frequency_hz > 0.0)
        & (amplitude_v >= 0.0)
    )
    if np.count_nonzero(valid) < 4:
        return {
            "available": False,
            "reason": "至少需要 4 个正频率幅值点",
            "model": "first_order_rl_voltage_divider_magnitude",
        }
    frequency_hz = frequency_hz[valid]
    amplitude_v = amplitude_v[valid]
    order = np.argsort(frequency_hz)
    frequency_hz = frequency_hz[order]
    amplitude_v = amplitude_v[order]
    if amplitude_std_v is None:
        sigma = None
    else:
        amplitude_std_v = np.asarray(amplitude_std_v, dtype=float).reshape(-1)
        if amplitude_std_v.size != valid.size:
            raise ValueError("幅值标准差数组长度不一致")
        sigma = amplitude_std_v[valid][order]
        positive_sigma = sigma[np.isfinite(sigma) & (sigma > 0.0)]
        sigma_floor = (
            max(float(np.median(positive_sigma)) * 0.25, 1e-9)
            if positive_sigma.size
            else None
        )
        sigma = np.maximum(
            np.where(np.isfinite(sigma) & (sigma > 0.0), sigma, sigma_floor or 1e-9),
            sigma_floor or 1e-9,
        )
    frequency_min = float(np.min(frequency_hz))
    frequency_max = float(np.max(frequency_hz))
    amplitude_max = max(float(np.max(amplitude_v)), 1e-9)
    initial = [
        float(amplitude_v[0]),
        float(amplitude_v[-1]),
        float(np.sqrt(frequency_min * frequency_max)),
    ]
    try:
        parameters, covariance = curve_fit(
            _inductive_voltage_divider_model,
            frequency_hz,
            amplitude_v,
            p0=initial,
            sigma=sigma,
            absolute_sigma=False,
            bounds=(
                [0.0, 0.0, max(frequency_min * 1e-3, 1e-9)],
                [10.0 * amplitude_max, 10.0 * amplitude_max, frequency_max * 1e3],
            ),
            maxfev=20000,
        )
    except (RuntimeError, ValueError) as exc:
        return {
            "available": False,
            "reason": f"幅值模型拟合失败: {exc}",
            "model": "first_order_rl_voltage_divider_magnitude",
        }
    fitted = _inductive_voltage_divider_model(frequency_hz, *parameters)
    residual = amplitude_v - fitted
    total = float(np.sum((amplitude_v - np.mean(amplitude_v)) ** 2))
    r_squared = (
        float(1.0 - np.sum(residual**2) / total) if total > 0.0 else float("nan")
    )
    parameter_std = np.sqrt(np.maximum(np.diag(covariance), 0.0))
    return {
        "available": True,
        "model": "first_order_rl_voltage_divider_magnitude",
        "equation": "A(f)=sqrt((A0^2+Ainf^2*(f/fc)^2)/(1+(f/fc)^2))",
        "amplitude_zero_frequency_v": float(parameters[0]),
        "amplitude_high_frequency_v": float(parameters[1]),
        "corner_frequency_hz": float(parameters[2]),
        "amplitude_zero_frequency_std_v": float(parameter_std[0]),
        "amplitude_high_frequency_std_v": float(parameter_std[1]),
        "corner_frequency_std_hz": float(parameter_std[2]),
        "r_squared": r_squared,
        "residual_rms_v": float(np.sqrt(np.mean(residual**2))),
        "frequency_hz": frequency_hz,
        "fitted_amplitude_v": fitted,
    }


def _select_waveform_frequencies(frequencies: np.ndarray) -> np.ndarray:
    """低频全部保留，高频用对数间隔补足代表点。"""
    frequencies = np.sort(np.unique(np.asarray(frequencies, dtype=float)))
    if frequencies.size <= 9:
        return frequencies
    low_count = min(6, frequencies.size)
    selected = list(frequencies[:low_count])
    targets = np.geomspace(frequencies[low_count], frequencies[-1], 5)[1:-1]
    selected.extend(
        float(frequencies[int(np.argmin(np.abs(frequencies - target)))])
        for target in targets[: 9 - low_count]
    )
    return np.asarray(sorted(set(selected)), dtype=float)


def _plot_waveforms(results_dir: Path, captures: list[dict[str, Any]]) -> str:
    set_plot_style("paper")
    frequencies = sorted({float(item["frequency_hz"]) for item in captures})
    selected_frequencies = _select_waveform_frequencies(np.asarray(frequencies))
    ncols = 3
    nrows = int(math.ceil(len(selected_frequencies) / ncols))
    fig, axes = new_figure(
        figsize=(10.0, max(6.0, 2.5 * nrows)),
        nrows=nrows,
        ncols=ncols,
        constrained_layout=True,
    )
    axes = np.asarray(axes, dtype=object).reshape(-1)
    for panel_index, (axis, frequency) in enumerate(zip(axes, selected_frequencies)):
        item = next(item for item in captures if np.isclose(item["frequency_hz"], frequency))
        time_cycles = np.asarray(item["time_relative_s"], dtype=float) * frequency
        indices = np.linspace(
            0, time_cycles.size - 1, min(time_cycles.size, 5000), dtype=int
        )
        axis.plot(
            time_cycles[indices],
            item["measured_voltage_v"][indices],
            lw=0.9,
            label="Measured",
        )
        axis.plot(
            time_cycles[indices],
            item["theory_voltage_v"][indices],
            lw=1.0,
            ls="--",
            label="Theory",
        )
        axis.plot(
            time_cycles[indices],
            item["fitted_voltage_v"][indices],
            lw=0.9,
            ls=":",
            color="0.15",
            label="Sine fit",
        )
        fit = item["fit"]
        axis.set_title(
            f"{frequency:g} Hz\nA = {fit['amplitude_v']:.4g} V, "
            f"phase = {fit['phase_deg']:.2f} deg"
        )
        format_axis(
            axis,
            xlabel="Time from CH4 falling edge (cycles)",
            ylabel="Voltage (V)",
        )
        if panel_index == 0:
            axis.legend(loc="best", fontsize=7)
        axis.grid(True, alpha=0.25)
    for axis in axes[selected_frequencies.size :]:
        axis.set_visible(False)
    filename = "waveform_overview.png"
    save_figure(fig, results_dir / filename)
    return filename


def _plot_frequency_response(results_dir: Path, summary: dict[str, Any]) -> str:
    set_plot_style("paper")
    frequency = summary["frequency_hz"]
    expected_amplitude = np.asarray(summary["expected_amplitude_v_mean"], dtype=float)
    fig, axes = new_figure(
        figsize=(7.0, 7.0),
        nrows=4,
        ncols=1,
        constrained_layout=True,
    )
    axes[0].errorbar(
        frequency,
        summary["gain_db_mean"],
        yerr=summary["gain_db_std"],
        marker="o",
        ms=3,
        lw=1.0,
        capsize=2,
        label="Measured",
    )
    if summary.get("theory_fit_available", False):
        fitted_amplitude = np.asarray(summary["theory_fit_amplitude_v"], dtype=float)
        fitted_gain_db = 20.0 * np.log10(
            np.divide(
                fitted_amplitude,
                expected_amplitude,
                out=np.full_like(fitted_amplitude, np.nan),
                where=expected_amplitude > 0.0,
            )
        )
        axes[0].plot(
            frequency,
            fitted_gain_db,
            color="0.15",
            lw=1.2,
            ls="--",
            label="First-order theory fit",
        )
        axes[0].text(
            0.02,
            0.97,
            (
                f"A0={summary['theory_fit_amplitude_zero_frequency_v']:.4g} V\n"
                f"Ainf={summary['theory_fit_amplitude_high_frequency_v']:.4g} V\n"
                f"fc={summary['theory_fit_corner_frequency_hz']:.4g} Hz\n"
                f"R2={summary['theory_fit_r_squared']:.5f}"
            ),
            transform=axes[0].transAxes,
            va="top",
            ha="left",
            fontsize=7,
            bbox={"boxstyle": "round,pad=0.25", "fc": "white", "ec": "0.7", "alpha": 0.85},
        )
    axes[0].legend(loc="best", fontsize=7)
    axes[0].set_xscale("log")
    format_axis(axes[0], xlabel="Drive frequency (Hz)", ylabel="Amplitude ratio (dB)")
    axes[0].grid(True, alpha=0.25, which="both")
    axes[1].errorbar(
        frequency,
        summary["phase_deg_mean"],
        yerr=summary["phase_deg_std"],
        marker="o",
        ms=3,
        lw=1.0,
        capsize=2,
    )
    axes[1].set_xscale("log")
    format_axis(axes[1], xlabel="Drive frequency (Hz)", ylabel="Phase difference (deg)")
    axes[1].grid(True, alpha=0.25, which="both")
    axes[2].errorbar(
        frequency,
        summary["residual_rms_v_mean"],
        yerr=summary["residual_rms_v_std"],
        marker="o",
        ms=3,
        lw=1.0,
        capsize=2,
    )
    axes[2].set_xscale("log")
    axes[2].set_yscale("log")
    format_axis(axes[2], xlabel="Drive frequency (Hz)", ylabel="Sine fit residual RMS (V)")
    axes[2].grid(True, alpha=0.25, which="both")
    if summary.get("theory_fit_available", False):
        drop_mean = summary["amplitude_drop_from_fitted_zero_percent_mean"]
        drop_std = summary["amplitude_drop_from_fitted_zero_percent_std"]
        drop_label = "Amplitude drop from fitted zero f (%)"
    else:
        drop_mean = summary["amplitude_drop_percent_mean"]
        drop_std = summary["amplitude_drop_percent_std"]
        drop_label = "Amplitude drop from lowest measured f (%)"
    axes[3].errorbar(
        frequency,
        drop_mean,
        yerr=drop_std,
        marker="o",
        ms=3,
        lw=1.0,
        capsize=2,
    )
    if summary.get("theory_fit_available", False):
        axes[3].plot(
            frequency,
            summary["amplitude_drop_percent_mean"],
            color="0.45",
            lw=1.0,
            ls=":",
            label="Drop from lowest measured f",
        )
        axes[3].legend(loc="best", fontsize=7)
    axes[3].axhline(0.0, color="0.25", lw=0.8)
    axes[3].set_xscale("log")
    format_axis(
        axes[3],
        xlabel="Drive frequency (Hz)",
        ylabel=drop_label,
    )
    axes[3].grid(True, alpha=0.25, which="both")
    filename = "frequency_response.png"
    save_figure(fig, results_dir / filename)
    return filename


def analyze(run_dir: Path) -> dict[str, Any]:
    """只读取指定运行目录的 raw 数据并生成 results。"""
    run_dir = Path(run_dir).resolve()
    config = _load_yaml(run_dir / "experiment_config.yaml")
    parameters = config.get("parameters", {})
    if not isinstance(parameters, dict):
        raise TypeError("experiment_config.yaml 的 parameters 必须是映射")
    trigger_level_v = float(parameters.get("SCOPE_TRIGGER_LEVEL_V", 2.5))
    default_amplitude = float(parameters.get("DRIVE_AMPLITUDE_VPP", 1.0))
    default_offset = float(parameters.get("DRIVE_OFFSET_V", 0.0))
    capture_paths = sorted((run_dir / "raw").glob("scope_f*_r*.npz"))
    if not capture_paths:
        raise FileNotFoundError(f"未找到 SDS 采集文件: {run_dir / 'raw'}")

    captures: list[dict[str, Any]] = []
    for path in capture_paths:
        capture = _load_capture(path)
        frequency_hz = _as_scalar(capture, "frequency_hz")
        amplitude_vpp = _as_scalar(capture, "drive_amplitude_vpp", default_amplitude)
        offset_v = _as_scalar(capture, "drive_offset_v", default_offset)
        edge_time = falling_edge_time(
            capture["trigger_time_s"],
            capture["trigger_voltage_v"],
            trigger_level_v,
        )
        time_relative_s = capture["time_s"] - edge_time
        theory_voltage_v = offset_v + 0.5 * amplitude_vpp * np.sin(
            2.0 * np.pi * frequency_hz * time_relative_s
        )
        fit = fit_sine(
            time_relative_s,
            capture["measured_voltage_v"],
            frequency_hz,
            amplitude_vpp,
            offset_v,
        )
        fitted_voltage_v = fit["offset_v"] + fit["amplitude_v"] * np.sin(
            2.0 * np.pi * frequency_hz * time_relative_s
            + np.deg2rad(fit["phase_deg"])
        )
        captures.append(
            {
                "file": str(path.relative_to(run_dir)),
                "frequency_hz": frequency_hz,
                "time_relative_s": time_relative_s,
                "measured_voltage_v": capture["measured_voltage_v"],
                "theory_voltage_v": theory_voltage_v,
                "fitted_voltage_v": fitted_voltage_v,
                "trigger_edge_time_s": edge_time,
                "fit": fit,
                "actual_rate_sa_s": float(
                    _as_scalar(capture, "actual_rate_sa_s", 1.0 / np.median(np.diff(capture["time_s"]))
                )
                ),
            }
        )

    frequency_axis = np.asarray(
        sorted({float(item["frequency_hz"]) for item in captures}),
        dtype=float,
    )
    summary: dict[str, Any] = {"frequency_hz": frequency_axis}
    metric_names = (
        "amplitude_v",
        "expected_amplitude_v",
        "gain",
        "gain_db",
        "offset_v",
        "residual_rms_v",
        "correlation",
    )
    for metric in metric_names:
        means: list[float] = []
        stds: list[float] = []
        for frequency in frequency_axis:
            values = np.asarray(
                [item["fit"][metric] for item in captures if np.isclose(item["frequency_hz"], frequency)],
                dtype=float,
            )
            means.append(float(np.nanmean(values)))
            stds.append(float(np.nanstd(values, ddof=0)))
        summary[f"{metric}_mean"] = np.asarray(means, dtype=float)
        summary[f"{metric}_std"] = np.asarray(stds, dtype=float)
    phase_means: list[float] = []
    phase_stds: list[float] = []
    for frequency in frequency_axis:
        values = np.asarray(
            [item["fit"]["phase_deg"] for item in captures if np.isclose(item["frequency_hz"], frequency)],
            dtype=float,
        )
        phase_means.append(_circular_mean_deg(values))
        phase_stds.append(_circular_std_deg(values))
    summary["phase_deg_mean"] = np.asarray(phase_means, dtype=float)
    summary["phase_deg_std"] = np.asarray(phase_stds, dtype=float)

    normalization = normalize_amplitude_to_reference(
        frequency_axis,
        summary["amplitude_v_mean"],
        summary["amplitude_v_std"],
    )
    normalized = np.asarray(normalization["normalized_amplitude"], dtype=float)
    normalized_std = np.asarray(
        normalization["normalized_amplitude_std"], dtype=float
    )
    change_db_std = np.full_like(normalized, np.nan)
    positive = normalized > 0.0
    change_db_std[positive] = (
        20.0 / np.log(10.0) * normalized_std[positive] / normalized[positive]
    )
    summary.update(
        {
            "normalization_reference_index": int(normalization["reference_index"]),
            "normalization_reference_frequency_hz": float(
                normalization["reference_frequency_hz"]
            ),
            "normalization_reference_amplitude_v": float(
                normalization["reference_amplitude_v"]
            ),
            "amplitude_normalized_to_reference_mean": normalized,
            "amplitude_normalized_to_reference_std": normalized_std,
            "amplitude_change_percent_mean": np.asarray(
                normalization["amplitude_change_percent"], dtype=float
            ),
            "amplitude_change_percent_std": 100.0 * normalized_std,
            "amplitude_drop_percent_mean": np.asarray(
                normalization["amplitude_drop_percent"], dtype=float
            ),
            "amplitude_drop_percent_std": 100.0 * normalized_std,
            "amplitude_change_db_mean": np.asarray(
                normalization["amplitude_change_db"], dtype=float
            ),
            "amplitude_change_db_std": change_db_std,
        }
    )
    theory_fit = fit_inductive_voltage_response(
        frequency_axis,
        summary["amplitude_v_mean"],
        summary["amplitude_v_std"],
    )
    summary["theory_fit_available"] = bool(theory_fit.get("available", False))
    summary["theory_fit_model"] = str(theory_fit.get("model", ""))
    summary["theory_fit_equation"] = str(theory_fit.get("equation", ""))
    summary["theory_fit_reason"] = str(theory_fit.get("reason", ""))
    if theory_fit.get("available", False):
        fitted_amplitude = np.asarray(theory_fit["fitted_amplitude_v"], dtype=float)
        zero_amplitude_v = float(theory_fit["amplitude_zero_frequency_v"])
        zero_amplitude_std_v = float(theory_fit["amplitude_zero_frequency_std_v"])
        zero_normalized = summary["amplitude_v_mean"] / zero_amplitude_v
        zero_normalized_std = zero_normalized * np.sqrt(
            np.square(
                np.divide(
                    summary["amplitude_v_std"],
                    summary["amplitude_v_mean"],
                    out=np.zeros_like(summary["amplitude_v_std"]),
                    where=summary["amplitude_v_mean"] > 0.0,
                )
            )
            + (zero_amplitude_std_v / zero_amplitude_v) ** 2
        )
        zero_change_percent = 100.0 * (zero_normalized - 1.0)
        zero_change_db = np.full_like(zero_normalized, np.nan)
        positive = zero_normalized > 0.0
        zero_change_db[positive] = 20.0 * np.log10(zero_normalized[positive])
        summary.update(
            {
                "theory_fit_amplitude_v": fitted_amplitude,
                "theory_fit_amplitude_zero_frequency_v": zero_amplitude_v,
                "theory_fit_amplitude_zero_frequency_std_v": zero_amplitude_std_v,
                "theory_fit_amplitude_high_frequency_v": float(
                    theory_fit["amplitude_high_frequency_v"]
                ),
                "theory_fit_amplitude_high_frequency_std_v": float(
                    theory_fit["amplitude_high_frequency_std_v"]
                ),
                "theory_fit_corner_frequency_hz": float(
                    theory_fit["corner_frequency_hz"]
                ),
                "theory_fit_corner_frequency_std_hz": float(
                    theory_fit["corner_frequency_std_hz"]
                ),
                "theory_fit_r_squared": float(theory_fit["r_squared"]),
                "theory_fit_residual_rms_v": float(theory_fit["residual_rms_v"]),
                "fitted_zero_frequency_amplitude_v": zero_amplitude_v,
                "fitted_zero_frequency_amplitude_std_v": zero_amplitude_std_v,
                "amplitude_normalized_to_fitted_zero_mean": zero_normalized,
                "amplitude_normalized_to_fitted_zero_std": zero_normalized_std,
                "amplitude_change_from_fitted_zero_percent_mean": zero_change_percent,
                "amplitude_change_from_fitted_zero_percent_std": 100.0
                * zero_normalized_std,
                "amplitude_drop_from_fitted_zero_percent_mean": -zero_change_percent,
                "amplitude_drop_from_fitted_zero_percent_std": 100.0
                * zero_normalized_std,
                "amplitude_change_from_fitted_zero_db_mean": zero_change_db,
                "amplitude_change_from_fitted_zero_db_std": np.full_like(
                    zero_change_db,
                    20.0
                    / np.log(10.0)
                    * np.divide(
                        zero_normalized_std,
                        zero_normalized,
                        out=np.zeros_like(zero_normalized_std),
                        where=positive,
                    ),
                ),
            }
        )

    results_dir = run_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    plot_files = [
        _plot_waveforms(results_dir, captures),
        _plot_frequency_response(results_dir, summary),
    ]
    np.savez(results_dir / "frequency_response.npz", **summary)
    capture_report = [
        {
            "file": item["file"],
            "frequency_hz": item["frequency_hz"],
            "trigger_edge_time_s": item["trigger_edge_time_s"],
            "actual_rate_sa_s": item["actual_rate_sa_s"],
            **item["fit"],
        }
        for item in captures
    ]
    theory_fit_report = {
        key: theory_fit[key]
        for key in (
            "available",
            "model",
            "equation",
            "reason",
            "amplitude_zero_frequency_v",
            "amplitude_zero_frequency_std_v",
            "amplitude_high_frequency_v",
            "amplitude_high_frequency_std_v",
            "corner_frequency_hz",
            "corner_frequency_std_hz",
            "r_squared",
            "residual_rms_v",
        )
        if key in theory_fit
    }
    report = {
        "experiment_id": EXPERIMENT_ID,
        "run_dir": str(run_dir),
        "capture_count": len(captures),
        "frequency_count": int(frequency_axis.size),
        "frequency_hz": frequency_axis,
        "summary": summary,
        "captures": capture_report,
        "theory_fit": theory_fit_report,
        "zero_frequency_normalization": {
            "reference_amplitude_v": summary.get(
                "fitted_zero_frequency_amplitude_v", float("nan")
            ),
            "frequency_hz": frequency_axis,
            "normalized_amplitude": summary.get(
                "amplitude_normalized_to_fitted_zero_mean",
                np.full_like(frequency_axis, np.nan),
            ),
            "amplitude_change_percent": summary.get(
                "amplitude_change_from_fitted_zero_percent_mean",
                np.full_like(frequency_axis, np.nan),
            ),
            "amplitude_drop_percent": summary.get(
                "amplitude_drop_from_fitted_zero_percent_mean",
                np.full_like(frequency_axis, np.nan),
            ),
            "amplitude_change_db": summary.get(
                "amplitude_change_from_fitted_zero_db_mean",
                np.full_like(frequency_axis, np.nan),
            ),
        },
        "physical_limit": (
            "理论值是 DG 正弦设定值；当前仅测量线圈端电压，不能单独确定唯一电感值。"
        ),
        "files": ["frequency_response.npz", "frequency_response.yaml", "frequency_response.json", *plot_files],
    }
    (results_dir / "frequency_response.yaml").write_text(
        yaml.safe_dump(_builtin(report), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    (results_dir / "frequency_response.json").write_text(
        json.dumps(_builtin(report), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report


def main() -> int:
    result = analyze(runtime_run_dir())
    print(f"Z 线圈电感效应频率响应分析完成: {result['run_dir']}")
    print(f"频率点数: {result['frequency_count']}，采集帧数: {result['capture_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
