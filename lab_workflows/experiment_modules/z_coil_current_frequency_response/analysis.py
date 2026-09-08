"""Z 线圈实际电流频率响应离线分析。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from ...current_feedback import sense_voltage_to_current
from ...plotting import format_axis, new_figure, save_figure, set_plot_style
from ..z_coil_inductance_frequency_response.analysis import (
    _as_scalar,
    _circular_mean_deg,
    _circular_std_deg,
    _load_capture,
    falling_edge_time,
    fit_sine,
)
from ..z_aw_waveform_scope_check.analysis import _saturation_diagnostic


EXPERIMENT_ID = "z-coil-current-frequency-response"


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
    with path.open(encoding="utf-8") as stream:
        payload = yaml.safe_load(stream) or {}
    if not isinstance(payload, dict):
        raise TypeError(f"YAML 顶层必须是映射: {path}")
    return payload


def _fit_current_capture(
    capture: dict[str, Any],
    *,
    resistance_ohm: float,
    frequency_hz: float,
    drive_amplitude_vpp: float,
    trigger_level_v: float,
) -> dict[str, Any]:
    edge_time = falling_edge_time(
        capture["trigger_time_s"], capture["trigger_voltage_v"], trigger_level_v
    )
    time_relative_s = capture["time_s"] - edge_time
    current_a = sense_voltage_to_current(
        capture["measured_voltage_v"], resistance_ohm
    )
    fit = fit_sine(time_relative_s, current_a, frequency_hz, 1.0, 0.0)
    input_amplitude_v = 0.5 * float(drive_amplitude_vpp)
    transfer = (
        fit["amplitude_v"] / input_amplitude_v
        * np.exp(1j * np.deg2rad(fit["phase_deg"]))
        if input_amplitude_v > 0.0
        else complex(np.nan, np.nan)
    )
    fitted_current = fit["offset_v"] + fit["amplitude_v"] * np.sin(
        2.0 * np.pi * frequency_hz * time_relative_s
        + np.deg2rad(fit["phase_deg"])
    )
    saturation = _saturation_diagnostic(
        np.asarray(capture["measured_voltage_v"], dtype=float),
        scale_v_div=_as_scalar(capture, "scale_used_v_div", np.nan),
        offset_v=_as_scalar(capture, "offset_used_v", 0.0),
    )
    return {
        "frequency_hz": float(frequency_hz),
        "time_relative_s": time_relative_s,
        "current_a": current_a,
        "fitted_current_a": fitted_current,
        "sense_voltage_v": np.asarray(capture["measured_voltage_v"], dtype=float),
        "trigger_edge_time_s": float(edge_time),
        "fit": fit,
        "transfer_real_a_per_v": float(np.real(transfer)),
        "transfer_imag_a_per_v": float(np.imag(transfer)),
        "transfer_magnitude_a_per_v": float(abs(transfer)),
        "transfer_phase_deg": float(np.rad2deg(np.angle(transfer))),
        "current_amplitude_a": float(fit["amplitude_v"]),
        "current_offset_a": float(fit["offset_v"]),
        "fit_residual_rms_a": float(fit["residual_rms_v"]),
        "fit_correlation": float(fit["correlation"]),
        "equivalent_delay_s": float(-fit["phase_deg"] / (360.0 * frequency_hz)),
        **saturation,
        "actual_rate_sa_s": float(
            _as_scalar(capture, "actual_rate_sa_s", 1.0 / np.median(np.diff(capture["time_s"])))
        ),
    }


def _plot_transfer(results_dir: Path, summary: dict[str, Any]) -> str:
    set_plot_style("paper")
    frequency = np.asarray(summary["frequency_hz"], dtype=float)
    magnitude = np.asarray(summary["transfer_magnitude_a_per_v_mean"], dtype=float)
    phase = np.asarray(summary["transfer_phase_deg_mean"], dtype=float)
    fig, axes = new_figure(nrows=2, ncols=1, kind="wide", constrained_layout=True)
    axes[0].errorbar(
        frequency,
        magnitude,
        yerr=summary["transfer_magnitude_a_per_v_std"],
        marker="o",
        ms=3,
        lw=1.0,
        capsize=2,
        label="Measured current transfer",
    )
    axes[0].set_xscale("log")
    format_axis(axes[0], xlabel="Drive frequency (Hz)", ylabel="|I/V| (A/V)")
    axes[0].grid(True, alpha=0.25, which="both")
    axes[0].legend(loc="best")
    axes[1].errorbar(
        frequency,
        phase,
        yerr=summary["transfer_phase_deg_std"],
        marker="o",
        ms=3,
        lw=1.0,
        capsize=2,
    )
    axes[1].set_xscale("log")
    format_axis(axes[1], xlabel="Drive frequency (Hz)", ylabel="Phase (deg)")
    axes[1].grid(True, alpha=0.25, which="both")
    filename = "transfer_function.png"
    save_figure(fig, results_dir / filename)
    return filename


def _plot_waveforms(results_dir: Path, records: list[dict[str, Any]]) -> str:
    set_plot_style("paper")
    selected = records[:: max(1, len(records) // 6)][:6]
    fig, axes = new_figure(
        nrows=len(selected), ncols=1, kind="wide", constrained_layout=True
    )
    axes = np.atleast_1d(axes)
    for axis, record in zip(axes, selected):
        time_ms = np.asarray(record["time_relative_s"]) * 1e3
        axis.plot(time_ms, record["current_a"], label="Measured current")
        axis.plot(time_ms, record["fitted_current_a"], ls="--", label="Sine fit")
        axis.set_title(f"{record['frequency_hz']:g} Hz")
        format_axis(axis, xlabel="Time from CH4 falling edge (ms)", ylabel="Current (A)")
        axis.grid(True, alpha=0.25)
        axis.legend(loc="best", fontsize=7)
    filename = "current_waveform_overview.png"
    save_figure(fig, results_dir / filename)
    return filename


def analyze(run_dir: Path) -> dict[str, Any]:
    """将 CH3 采样电阻电压转换为复数电流传递函数。"""
    run_dir = Path(run_dir).resolve()
    config = _load_yaml(run_dir / "experiment_config.yaml")
    parameters = config.get("parameters", {})
    resistance = float(parameters.get("SENSE_RESISTOR_OHM", 0.0))
    trigger_level = float(parameters.get("SCOPE_TRIGGER_LEVEL_V", 2.5))
    default_drive = float(parameters.get("DRIVE_AMPLITUDE_VPP", 0.1))
    if resistance <= 0.0:
        raise ValueError("分析配置缺少正的 SENSE_RESISTOR_OHM")
    capture_paths = sorted((run_dir / "raw").glob("scope_f*_r*.npz"))
    if not capture_paths:
        raise FileNotFoundError(f"未找到 SDS 采集文件: {run_dir / 'raw'}")
    records: list[dict[str, Any]] = []
    for path in capture_paths:
        capture = _load_capture(path)
        frequency = _as_scalar(capture, "frequency_hz")
        drive = _as_scalar(capture, "drive_amplitude_vpp", default_drive)
        record = _fit_current_capture(
            capture,
            resistance_ohm=resistance,
            frequency_hz=frequency,
            drive_amplitude_vpp=drive,
            trigger_level_v=trigger_level,
        )
        record["file"] = str(path.relative_to(run_dir))
        records.append(record)

    frequency_axis = np.asarray(sorted({r["frequency_hz"] for r in records}), dtype=float)
    summary: dict[str, Any] = {
        "frequency_hz": frequency_axis,
        "sense_resistor_ohm": resistance,
    }
    for name in (
        "transfer_magnitude_a_per_v",
        "transfer_real_a_per_v",
        "transfer_imag_a_per_v",
        "transfer_phase_deg",
        "current_amplitude_a",
        "current_offset_a",
        "fit_residual_rms_a",
        "fit_correlation",
        "equivalent_delay_s",
        "actual_rate_sa_s",
        "saturation_fraction",
        "scope_edge_fraction",
    ):
        means = []
        stds = []
        for frequency in frequency_axis:
            values = np.asarray(
                [r[name] for r in records if np.isclose(r["frequency_hz"], frequency)],
                dtype=float,
            )
            means.append(float(np.nanmean(values)))
            stds.append(float(np.nanstd(values)))
        summary[f"{name}_mean"] = np.asarray(means, dtype=float)
        summary[f"{name}_std"] = np.asarray(stds, dtype=float)
    summary["transfer_phase_deg_mean"] = np.asarray(
        [
            _circular_mean_deg(
                np.asarray(
                    [r["transfer_phase_deg"] for r in records if np.isclose(r["frequency_hz"], f)],
                    dtype=float,
                )
            )
            for f in frequency_axis
        ],
        dtype=float,
    )
    summary["transfer_phase_deg_std"] = np.asarray(
        [
            _circular_std_deg(
                np.asarray(
                    [r["transfer_phase_deg"] for r in records if np.isclose(r["frequency_hz"], f)],
                    dtype=float,
                )
            )
            for f in frequency_axis
        ],
        dtype=float,
    )
    saturation_detected = np.asarray(
        [
            any(
                bool(r["saturation_detected"])
                for r in records
                if np.isclose(r["frequency_hz"], frequency)
            )
            for frequency in frequency_axis
        ],
        dtype=bool,
    )
    summary["saturation_detected"] = saturation_detected
    reliable = (
        np.isfinite(summary["transfer_magnitude_a_per_v_mean"])
        & (summary["transfer_magnitude_a_per_v_mean"] > 0.0)
        & (np.asarray(summary["transfer_phase_deg_std"]) < 15.0)
        & (np.asarray(summary["fit_correlation_mean"]) > 0.98)
        & ~saturation_detected
    )
    summary["reliable_frequency_hz"] = frequency_axis[reliable]
    plot_transfer = _plot_transfer(run_dir / "results", summary)
    plot_waveforms = _plot_waveforms(run_dir / "results", records)
    serializable = _builtin(
        {
            "experiment_id": EXPERIMENT_ID,
            "run_dir": str(run_dir),
            "capture_count": len(records),
            "frequency_count": int(frequency_axis.size),
            "summary": summary,
            "reliable_frequency_hz": frequency_axis[reliable],
            "files": [plot_transfer, plot_waveforms, "frequency_response.npz"],
        }
    )
    results_dir = run_dir / "results"
    results_dir.mkdir(exist_ok=True)
    (results_dir / "frequency_response.yaml").write_text(
        yaml.safe_dump(serializable, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    (results_dir / "frequency_response.json").write_text(
        json.dumps(serializable, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    np.savez(
        results_dir / "frequency_response.npz",
        frequency_hz=frequency_axis,
        transfer_real_a_per_v_mean=summary["transfer_real_a_per_v_mean"],
        transfer_imag_a_per_v_mean=summary["transfer_imag_a_per_v_mean"],
        transfer_magnitude_a_per_v_mean=summary["transfer_magnitude_a_per_v_mean"],
        transfer_phase_deg_mean=summary["transfer_phase_deg_mean"],
        current_amplitude_a_mean=summary["current_amplitude_a_mean"],
        current_amplitude_a_std=summary["current_amplitude_a_std"],
        equivalent_delay_s_mean=summary["equivalent_delay_s_mean"],
        equivalent_delay_s_std=summary["equivalent_delay_s_std"],
        fit_residual_rms_a_mean=summary["fit_residual_rms_a_mean"],
        actual_rate_sa_s_mean=summary["actual_rate_sa_s_mean"],
        saturation_detected=np.asarray(saturation_detected, dtype=np.uint8),
        reliable=np.asarray(reliable, dtype=np.uint8),
        sense_resistor_ohm=np.float64(resistance),
    )
    return serializable


def main() -> int:
    from ...experiment_runtime import runtime_run_dir

    analyze(runtime_run_dir())
    return 0
