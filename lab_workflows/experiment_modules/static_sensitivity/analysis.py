"""静磁场灵敏度离线分析；只读取 raw/，不连接仪器。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from lab_workflows.plotting import new_figure, plot_style_context, save_figure
from sensitivity_analysis import compute_sensitivity, fit_dispersive, write_run_record
from sensitivity_analysis.fitting import _dispersive


QUALITY_THRESHOLDS = {"r_squared_min": 0.85, "relative_gamma_uncertainty_max": 0.5, "residual_sign_change_ratio_min": 0.0}


def _value(values: dict[str, Any], key: str, default: Any) -> Any:
    return values[key] if key in values else default


def _load_noise(raw: Path, values: dict[str, Any]) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, float, int]:
    path = raw / "noise_range_scan.npz"
    if path.is_file():
        with np.load(path) as data:
            return (data["freq"], data["psd_avg_by_range"], data["current_range_set_A"], data["current_range_actual_A"], float(data["current_setpoint_A"]), int(data["n_avg"]))
    path = raw / "noise_data.npz"
    if path.is_file():
        with np.load(path) as data:
            selected = float(data["current_range_A"]) if "current_range_A" in data else np.nan
            actual = float(data["current_range_actual_A"]) if "current_range_actual_A" in data else selected
            return (data["freq"], data["psd_avg"][None, ...], np.array([selected]), np.array([actual]), float(_value(values, "main_magnetic_field", 0.0)) / 1000.0, int(data["n_avg"]))

    # 兼容采集阶段中断后只留下 noise_raw/noise_*.npy 的运行目录。
    noise_dirs = sorted((raw / "noise_raw").glob("gs200_range_*"))
    if noise_dirs:
        from scipy.signal import welch

        fs = float(_value(values, "hf2_noise_rate", 50000.0))
        duration = float(_value(values, "noise_duration", 1.0))
        configured_ranges = [float(item) for item in _value(values, "gs200_current_ranges", [])]
        psd_by_range: list[np.ndarray] = []
        range_set: list[float] = []
        range_actual: list[float] = []
        freq: np.ndarray | None = None
        n_avg = 0
        for index, directory in enumerate(noise_dirs):
            files = sorted(directory.glob("noise_*.npy"))
            if not files:
                continue
            psd_sum = None
            for file in files:
                signal = np.asarray(np.load(file), dtype=float).ravel()
                segment = min(signal.size, max(1, int(fs * duration)))
                freq_this, psd = welch(signal - np.mean(signal), fs=fs, nperseg=segment, scaling="density")
                if psd_sum is None:
                    psd_sum = psd
                    freq = freq_this
                else:
                    psd_sum += psd
            psd_by_range.append(psd_sum / len(files))
            if index < len(configured_ranges):
                current_range = configured_ranges[index]
            else:
                current_range = float(directory.name.removeprefix("gs200_range_").removesuffix("mA")) / 1000.0
            range_set.append(current_range)
            range_actual.append(current_range)
            n_avg = max(n_avg, len(files))
        if not psd_by_range or freq is None:
            raise FileNotFoundError(f"噪声原始数据不存在: {raw}")
        return (freq, np.asarray(psd_by_range), np.asarray(range_set), np.asarray(range_actual), float(_value(values, "main_magnetic_field", 0.0)) / 1000.0, n_avg)
    raise FileNotFoundError(f"噪声原始数据不存在: {raw}")


def analyze(run_dir: str | Path) -> dict[str, Any]:
    run_dir = Path(run_dir).resolve()
    raw = run_dir / "raw"
    results = run_dir / "results"
    results.mkdir(parents=True, exist_ok=True)
    config_path = run_dir / "experiment_config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) if config_path.is_file() else {}
    if not isinstance(config, dict):
        raise ValueError(f"运行配置格式错误: {config_path}")
    values = config.get("parameters", {})
    if not isinstance(values, dict):
        values = {}
    scan_path = raw / "scan_data.npz"
    if not scan_path.is_file():
        raise FileNotFoundError(f"扫描原始数据不存在: {scan_path}")
    with np.load(scan_path) as data:
        scan = {name: data[name] for name in data.files}
    z_v_to_ft = float(_value(values, "z_v_to_nt", 3517.0)) * 1_000_000.0
    symmetry = float(_value(values, "ramp_symmetry", 20.0)) / 100.0
    t_norm = scan["t_norm"]
    fall_mask = (t_norm >= symmetry + (1.0 - symmetry) * 0.1) & (t_norm < symmetry + (1.0 - symmetry) * 0.9)
    fit_result = fit_dispersive(scan["Z_voltage"][fall_mask], scan["sample.y"][fall_mask], z_v_to_ft / 1_000_000.0, z_v_to_ft, quality_thresholds=QUALITY_THRESHOLDS)
    voltage_fit = scan["Z_voltage"][fall_mask]
    fit_voltage_axis = np.linspace(float(voltage_fit[0]), float(voltage_fit[-1]), 200)
    fit_signal = _dispersive(fit_voltage_axis, *fit_result.popt)
    freq, psd_by_range, range_set, range_actual, current_setpoint, n_avg = _load_noise(raw, values)
    sensitivity = [compute_sensitivity(psd, freq, slope_V_per_fT=fit_result.slope_V_per_fT, f_larmor_Hz=fit_result.f_larmor_Hz) for psd in psd_by_range]
    flat = np.asarray([item.sens_flat for item in sensitivity], dtype=float)
    best_index = int(np.nanargmin(flat))
    best = sensitivity[best_index]
    labels = [f"{float(item) * 1000:g}mA" for item in range_set]
    ranges = [{"range_label": labels[index], "current_range_set_A": float(range_set[index]), "current_range_actual_A": float(range_actual[index]), "current_setpoint_A": current_setpoint, "sens_flat_fT_per_sqrt_Hz": float(item.sens_flat), "flat_fmin_Hz": float(item.flat_fmin), "flat_fmax_Hz": float(item.flat_fmax)} for index, item in enumerate(sensitivity)]
    np.savez(results / "gs200_current_range_sensitivity.npz", freq=freq, psd_avg_by_range=psd_by_range, sens_corrected_by_range=np.asarray([item.sens_corrected for item in sensitivity]), sens_raw_by_range=np.asarray([item.sens_raw for item in sensitivity]), sens_flat_by_range=flat, current_range_set_A=range_set, current_range_actual_A=range_actual, best_range_idx=best_index)
    (results / "gs200_current_range_summary.yaml").write_text(yaml.safe_dump({"best_range_label": labels[best_index], "ranges": ranges}, allow_unicode=True, sort_keys=False), encoding="utf-8")
    with plot_style_context("paper"):
        figure, (axis_scan, axis_sensitivity) = new_figure(nrows=2, height_ratios=(1.2, 1), width_mm=177.8, height_mm=130)
        field = voltage_fit * z_v_to_ft
        axis_scan.plot(field, scan["sample.x"][fall_mask], label="X")
        axis_scan.plot(field, scan["sample.y"][fall_mask], label="Y")
        axis_scan.plot(field, scan["sample.r"][fall_mask], label="R")
        axis_scan.plot(fit_voltage_axis * z_v_to_ft, fit_signal, "k--", label=f"DisFit (R²={fit_result.r_squared:.3f})")
        axis_scan.axvline(fit_result.B0_fT, color="gray", linestyle=":", alpha=0.5)
        axis_scan.text(
            0.02,
            0.96,
            f"dY/dB = {fit_result.slope_V_per_fT:.3e} V/fT\nLinewidth (HWHM) = {fit_result.f_larmor_Hz:.1f} Hz",
            transform=axis_scan.transAxes,
            va="top",
            ha="left",
            fontsize=8,
            bbox={"boxstyle": "round,pad=0.3", "facecolor": "white", "alpha": 0.8, "edgecolor": "0.7"},
        )
        axis_scan.set(xlabel="Z magnetic field (fT)", ylabel="Signal (V)", title="Dispersion curve (falling edge)")
        axis_scan.grid(True, alpha=0.3); axis_scan.legend(fontsize=8)
        colors = __import__("matplotlib").colormaps["tab10"](np.linspace(0, 1, max(len(sensitivity), 1)))
        for index, item in enumerate(sensitivity):
            axis_sensitivity.plot(item.freq, item.sens_corrected, color=colors[index], label=f"{labels[index]} ({item.sens_flat:.0f} fT/√Hz)", alpha=0.9 if index == best_index else 0.55)
        axis_sensitivity.plot(best.freq[best.flat_mask], best.sens_corrected[best.flat_mask], "darkgreen", label=f"Best flat: {best.flat_fmin:.0f}-{best.flat_fmax:.0f} Hz")
        axis_sensitivity.plot(best.freq, best.sens_raw, "gray", linewidth=0.5, alpha=0.35, label=f"Raw ({labels[best_index]})")
        axis_sensitivity.axhline(best.sens_flat, color="darkgreen", linestyle="--", alpha=0.7, label=f"Best = {best.sens_flat:.0f} fT/√Hz")
        axis_sensitivity.axvline(fit_result.f_larmor_Hz, color="red", linestyle=":", alpha=0.5, label=f"HWHM = {fit_result.f_larmor_Hz:.0f} Hz")
        axis_sensitivity.set(xlabel="Frequency (Hz)", ylabel="Sensitivity (fT/√Hz)", title="Sensitivity spectrum (flat region median)")
        axis_sensitivity.set_xlim(0.5, max(fit_result.f_larmor_Hz * 2, 1000)); axis_sensitivity.set_yscale("log"); axis_sensitivity.grid(True, alpha=0.3, which="both"); axis_sensitivity.legend(fontsize=8)
        save_figure(figure, results / "full_analysiswithoutpump.png")
    timestamp = str(config.get("timestamp", run_dir.name)); run_tag = str(config.get("run_tag", "sens"))
    params = {"Pump_laser_power": _value(values, "pump_laser_power", 0.0), "Probe_laser_power": _value(values, "probe_laser_power", 0.0), "X_magnetic_field": _value(values, "x_magnetic_field", 0.0), "Y_magnetic_field": _value(values, "y_magnetic_field", 0.0), "main_magnetic_field": _value(values, "main_magnetic_field", 0.0), "temperature": _value(values, "temperature", 0.0), "PUMP_MOD_DUTY": _value(values, "pump_mod_duty", 0.0), "PUMP_MOD_AMPLITUDE": _value(values, "pump_mod_amplitude", 0.0), "GS200_current_range_A": float(range_set[best_index]), "GS200_current_range_actual_A": float(range_actual[best_index])}
    write_run_record(run_dir, params, fit_result, best, summary_path=run_dir.parent / "run_summary.csv", timestamp=timestamp, run_tag=run_tag, n_avg=n_avg, freq_resolution_Hz=float(freq[1] - freq[0]))
    summary = {"success": True, "experiment_id": "static-sensitivity", "run_dir": str(run_dir), "timestamp": timestamp, "best_range_label": labels[best_index], "fit_valid": bool(fit_result.is_valid), "fit_r_squared": float(fit_result.r_squared), "fit_rmse": float(fit_result.rmse), "fit_B0_fT": float(fit_result.B0_fT), "fit_gamma_fT": float(fit_result.gamma_fT), "fit_slope_V_per_fT": float(fit_result.slope_V_per_fT), "fit_larmor_frequency_Hz": float(fit_result.f_larmor_Hz), "fit_linewidth_hwhm_Hz": float(fit_result.f_larmor_Hz), "sensitivity_flat_fT_per_sqrt_Hz": float(best.sens_flat), "flat_frequency_min_Hz": float(best.flat_fmin), "flat_frequency_max_Hz": float(best.flat_fmax), "files": sorted(path.name for path in results.iterdir() if path.is_file()), "plot_profile": "paper"}
    (results / "analysis.yaml").write_text(yaml.safe_dump(summary, allow_unicode=True, sort_keys=False), encoding="utf-8")
    (results / "analysis.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> int:
    from ...experiment_runtime import runtime_run_dir
    result = analyze(runtime_run_dir())
    print(f"静磁场灵敏度离线分析完成: {result['run_dir']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
