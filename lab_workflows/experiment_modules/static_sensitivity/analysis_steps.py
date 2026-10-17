"""静磁场分阶段分析算法；只读取原始数据与阶段产物。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from lab_workflows.plotting import new_figure, plot_style_context, save_figure
from sensitivity_analysis import compute_sensitivity, fit_dispersive, write_run_record
from sensitivity_analysis.fitting import _dispersive, DispersionFitResult
from dataclasses import asdict, fields
from ...experiment_runtime import check_cancelled
from sensitivity_analysis.acquisition import load_acquisition_rate
from .run_format import RAW_FORMAT_VERSION


QUALITY_THRESHOLDS = {"r_squared_min": 0.85, "relative_gamma_uncertainty_max": 0.5, "residual_sign_change_ratio_min": 0.0}


def _value(values: dict[str, Any], key: str, default: Any) -> Any:
    return values[key] if key in values else default


def _load_noise(raw: Path, values: dict[str, Any], actual_rates: dict[str, Any], measurements: list[dict[str, Any]]) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, float, int]:
    # 仅从原始波形计算；中断运行使用已经完成并保存的噪声段。
    noise_dirs = sorted((raw / "noise_raw").glob("gs200_range_*"))
    if noise_dirs:
        from scipy.signal import welch

        fs = float(actual_rates["hf2_noise_rate_Sa_s"])
        if not np.isfinite(fs) or fs <= 0:
            raise ValueError("运行记录中的实际噪声采样率无效")
        duration = float(_value(values, "noise_duration", 1.0))
        by_directory = {item["directory"]: item for item in measurements}
        psd_by_range: list[np.ndarray] = []
        range_set: list[float] = []
        range_actual: list[float] = []
        freq: np.ndarray | None = None
        n_avg = 0
        for directory in noise_dirs:
            files = sorted(directory.glob("noise_*.npy"))
            if not files:
                continue
            psd_sum = None
            for file in files:
                check_cancelled()
                signal = np.asarray(np.load(file, allow_pickle=False), dtype=float)
                if signal.ndim != 1 or signal.size < 2 or not np.isfinite(signal).all():
                    raise ValueError(f"噪声原始数据无效: {file}")
                capture_rate = load_acquisition_rate(file, fs)
                segment = min(signal.size, max(2, int(capture_rate * duration)))
                freq_this, psd = welch(signal - np.mean(signal), fs=capture_rate, nperseg=segment, scaling="density")
                if psd_sum is None:
                    psd_sum = psd
                    freq = freq_this
                else:
                    if not np.array_equal(freq, freq_this):
                        raise ValueError("噪声波形得到的 PSD 频率轴不一致")
                    psd_sum += psd
            if psd_by_range and not np.array_equal(freq_axis, freq):
                raise ValueError("不同量程的 PSD 频率轴不一致")
            freq_axis = freq.copy()
            psd_by_range.append(psd_sum / len(files))
            measurement = by_directory[directory.name]
            range_set.append(float(measurement["current_range_set_A"]))
            range_actual.append(float(measurement["current_range_actual_A"]))
            n_avg = max(n_avg, len(files))
        if not psd_by_range or freq is None:
            raise FileNotFoundError(f"噪声原始数据不存在: {raw}")
        return (freq, np.asarray(psd_by_range), np.asarray(range_set), np.asarray(range_actual), float(_value(values, "main_magnetic_field", 0.0)) / 1000.0, n_avg)
    raise FileNotFoundError(f"噪声原始数据不存在: {raw}")


def _save_summary(results: Path, summary: dict[str, Any]) -> None:
    for name, content in (("analysis.yaml", yaml.safe_dump(summary, allow_unicode=True, sort_keys=False)),
                          ("analysis.json", json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False))):
        path = results / name
        if not path.is_file() or path.read_text(encoding="utf-8") != content:
            path.write_text(content, encoding="utf-8")


def load_scan(run_dir):
    scan_path = run_dir / "raw" / "dispersion_scan.npz"
    if not scan_path.is_file():
        raise FileNotFoundError(f"直流色散原始数据不存在: {scan_path}")
    with np.load(scan_path, allow_pickle=False) as data:
        scan = {name: data[name] for name in data.files}
    if scan["schema_version"].shape != () or scan["schema_version"].item() != RAW_FORMAT_VERSION:
        raise ValueError("色散原始文件必须为版本 3")
    if scan["completed_mask"].dtype.kind != "b":
        raise ValueError("色散完成掩码必须为布尔数组")
    offsets = scan["offsets_v"]
    if offsets.ndim != 1 or not np.isfinite(offsets).all() or np.any(np.diff(offsets) <= 0):
        raise ValueError("色散电压轴必须有限且严格递增")
    for key in ("completed_mask", "x_median", "y_median", "r_median"):
        if scan[key].shape != offsets.shape:
            raise ValueError(f"色散 {key} 长度与电压轴不一致")
    return scan


def load_inputs(run_dir, config):
    scan = load_scan(run_dir)
    fs = config.get("actual_rates", {}).get("hf2_noise_rate_Sa_s")
    duration = config["parameters"].get("noise_duration")
    for name, value in (("实际噪声采样率", fs), ("噪声时长", duration)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value) or value <= 0:
            raise ValueError(f"运行快照中的{name}必须为有限正数")
    files = sorted((run_dir / "raw/noise_raw").glob("gs200_range_*/noise_*.npy"))
    if not files:
        raise FileNotFoundError("噪声原始数据不存在")
    directories = {item["directory"] for item in config.get("gs200_range_measurements", [])}
    for item in config.get("gs200_range_measurements", []):
        for key in ("current_range_set_A", "current_range_actual_A"):
            value = item.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value) or value <= 0:
                raise ValueError("运行快照中的 GS200 量程无效")
        count = sum(path.parent.name == item["directory"] for path in files)
        if "samples_saved" in item and item["samples_saved"] != count:
            raise ValueError("噪声波形数与采集完成记录不一致，原始文件可能缺失")
    for path in files:
        check_cancelled()
        if path.parent.name not in directories:
            raise ValueError("噪声量程文件与采集快照不一致")
        waveform = np.load(path, allow_pickle=False)
        if waveform.ndim != 1 or waveform.size < 2 or not np.isfinite(waveform).all():
            raise ValueError(f"噪声原始数据无效: {path}")
    return dict(dispersion_points=int(scan["offsets_v"].size), noise_files=len(files))


def dispersion_fit(run_dir, config, z_v_to_nt):
    results = run_dir / "results"
    scan = load_scan(run_dir)
    offsets = scan["offsets_v"]
    fit_mask = scan["completed_mask"] & np.isfinite(scan["y_median"])
    if np.count_nonzero(fit_mask) < 7:
        error = "色散有效点不足 7 点，不能拟合"
        np.savez(results / "dispersion_fit.npz", offsets_v=offsets, fit_mask=fit_mask,
                 popt=np.full(4, np.nan), fit_valid=False, rejection_reasons=np.asarray([error]))
        _save_summary(results, {"success": False, "experiment_id": "static-sensitivity", "fit_valid": False,
                                "fit_rejection_reasons": [error], "fit_points": int(np.count_nonzero(fit_mask))})
        raise ValueError(error)
    z_v_to_ft = float(z_v_to_nt) * 1_000_000.0
    if not np.isfinite(z_v_to_ft) or z_v_to_ft <= 0:
        raise ValueError("Z 场换算系数必须为有限正数")
    fit_result = fit_dispersive(offsets[fit_mask], scan["y_median"][fit_mask], z_v_to_ft / 1_000_000.0, z_v_to_ft, quality_thresholds=QUALITY_THRESHOLDS)
    np.savez(results / "dispersion_fit.npz", offsets_v=offsets, fit_mask=fit_mask,
             popt=fit_result.popt, fit_valid=fit_result.is_valid,
             rejection_reasons=np.asarray(fit_result.rejection_reasons, dtype=str))
    if (not np.isfinite(fit_result.popt).all() or not np.isfinite(fit_result.slope_V_per_fT)
            or fit_result.slope_V_per_fT <= 0 or not np.isfinite(fit_result.f_larmor_Hz)
            or fit_result.f_larmor_Hz <= 0 or not np.isfinite(fit_result.r_squared)
            or not np.isfinite(fit_result.rmse)):
        error = "色散拟合参数或中心斜率不可用，不能计算灵敏度"
        _save_summary(results, {"success": False, "experiment_id": "static-sensitivity", "fit_valid": False,
                                "fit_rejection_reasons": [*fit_result.rejection_reasons, error]})
        raise ValueError(error)
    if not fit_result.is_valid:
        error = "色散拟合质量不通过，不能计算灵敏度：" + "；".join(fit_result.rejection_reasons)
        _save_summary(results, {"success": False, "experiment_id": "static-sensitivity", "fit_valid": False,
                                "fit_rejection_reasons": fit_result.rejection_reasons})
        raise ValueError(error)
    payload = {"fit_" + name: value if value is not None else np.empty((0, 0))
               for name, value in asdict(fit_result).items()}
    payload["fit_rejection_reasons"] = np.asarray(fit_result.rejection_reasons, dtype=str)
    np.savez(results / "dispersion_fit.npz", offsets_v=offsets, fit_mask=fit_mask,
             popt=fit_result.popt, fit_valid=True, rejection_reasons=np.asarray([], dtype=str), **payload)
    return dict(fit_valid=True, fit_points=int(np.count_nonzero(fit_mask)),
                fit_rejection_reasons=[], fit_center_v=float(fit_result.V0),
                fit_r_squared=float(fit_result.r_squared), fit_rmse=float(fit_result.rmse))


def noise_psd(run_dir, config):
    freq, psd, ranges, actual, current, n_avg = _load_noise(run_dir / "raw", config["parameters"],
        config.get("actual_rates", {}), config.get("gs200_range_measurements", []))
    np.savez(run_dir / "results/noise_psd.npz", freq=freq, psd_avg_by_range=psd,
             current_range_set_A=ranges, current_range_actual_A=actual, current_setpoint_A=current, n_avg=n_avg)
    return dict(noise_ranges=int(ranges.size), noise_n_avg=n_avg)


def sensitivity_and_plots(run_dir, config, z_v_to_nt):
    results = run_dir / "results"
    values = config["parameters"]
    scan = load_scan(run_dir)
    offsets = scan["offsets_v"]
    z_v_to_ft = z_v_to_nt * 1_000_000.0
    with np.load(results / "dispersion_fit.npz", allow_pickle=False) as data:
        fit_mask = data["fit_mask"]
        payload = {field.name: data["fit_" + field.name] for field in fields(DispersionFitResult)}
        payload = {key: value.item() if value.shape == () else value for key, value in payload.items()}
        payload["rejection_reasons"] = payload["rejection_reasons"].tolist()
        fit_result = DispersionFitResult(**payload)
    voltage_fit = offsets[fit_mask]
    fit_voltage_axis = np.linspace(float(voltage_fit[0]), float(voltage_fit[-1]), 200)
    fit_signal = _dispersive(fit_voltage_axis, *fit_result.popt)
    with np.load(results / "noise_psd.npz", allow_pickle=False) as data:
        freq, psd_by_range, range_set, range_actual = (data[key] for key in
            ("freq", "psd_avg_by_range", "current_range_set_A", "current_range_actual_A"))
        current_setpoint, n_avg = float(data["current_setpoint_A"]), int(data["n_avg"])
    sensitivity = [compute_sensitivity(psd, freq, slope_V_per_fT=fit_result.slope_V_per_fT, f_larmor_Hz=fit_result.f_larmor_Hz) for psd in psd_by_range]
    flat = np.asarray([item.sens_flat for item in sensitivity], dtype=float)
    if not np.isfinite(flat).all():
        error = "噪声平坦频段灵敏度不可用"
        _save_summary(results, {"success": False, "experiment_id": "static-sensitivity", "fit_valid": bool(fit_result.is_valid),
                                "fit_rejection_reasons": fit_result.rejection_reasons, "error": error})
        raise ValueError(error)
    best_index = int(np.argmin(flat))
    best = sensitivity[best_index]
    labels = [f"{float(item) * 1000:g}mA" for item in range_set]
    ranges = [{"range_label": labels[index], "current_range_set_A": float(range_set[index]), "current_range_actual_A": float(range_actual[index]), "current_setpoint_A": current_setpoint, "sens_flat_fT_per_sqrt_Hz": float(item.sens_flat), "flat_fmin_Hz": float(item.flat_fmin), "flat_fmax_Hz": float(item.flat_fmax)} for index, item in enumerate(sensitivity)]
    np.savez(results / "gs200_current_range_sensitivity.npz", freq=freq, psd_avg_by_range=psd_by_range, sens_corrected_by_range=np.asarray([item.sens_corrected for item in sensitivity]), sens_raw_by_range=np.asarray([item.sens_raw for item in sensitivity]), sens_flat_by_range=flat, current_range_set_A=range_set, current_range_actual_A=range_actual, best_range_idx=best_index)
    (results / "gs200_current_range_summary.yaml").write_text(yaml.safe_dump({"best_range_label": labels[best_index], "ranges": ranges}, allow_unicode=True, sort_keys=False), encoding="utf-8")
    with plot_style_context("paper"):
        figure, (axis_scan, axis_sensitivity) = new_figure(nrows=2, height_ratios=(1.2, 1), width_mm=177.8, height_mm=130)
        field = offsets * z_v_to_ft
        axis_scan.plot(field, scan["x_median"], ".-", label="X median")
        axis_scan.plot(field, scan["y_median"], ".-", label="Y median")
        axis_scan.plot(field, scan["r_median"], ".-", label="R median")
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
        axis_scan.set(xlabel="Z magnetic field (fT)", ylabel="Signal (V)", title="DC step dispersion" + (" [INVALID]" if not fit_result.is_valid else ""))
        axis_scan.grid(True, alpha=0.3); axis_scan.legend(fontsize=8)
        colors = __import__("matplotlib").colormaps["tab10"](np.linspace(0, 1, max(len(sensitivity), 1)))
        for index, item in enumerate(sensitivity):
            axis_sensitivity.plot(item.freq, item.sens_corrected, color=colors[index], label=f"{labels[index]} ({item.sens_flat:.0f} fT/√Hz)", alpha=0.9 if index == best_index else 0.55)
        axis_sensitivity.plot(best.freq[best.flat_mask], best.sens_corrected[best.flat_mask], "darkgreen", label=f"Best flat: {best.flat_fmin:.0f}-{best.flat_fmax:.0f} Hz")
        axis_sensitivity.plot(best.freq, best.sens_raw, "gray", linewidth=0.5, alpha=0.35, label=f"Raw ({labels[best_index]})")
        axis_sensitivity.axhline(best.sens_flat, color="darkgreen", linestyle="--", alpha=0.7, label=f"Best = {best.sens_flat:.0f} fT/√Hz")
        axis_sensitivity.axvline(fit_result.f_larmor_Hz, color="red", linestyle=":", alpha=0.5, label=f"HWHM = {fit_result.f_larmor_Hz:.0f} Hz")
        axis_sensitivity.set(xlabel="Frequency (Hz)", ylabel="Sensitivity (fT/√Hz)", title="Sensitivity spectrum (flat region median)")
        axis_sensitivity.set_xlim(0.5, max(fit_result.f_larmor_Hz * 2, 1000)); axis_sensitivity.set_xscale("log"); axis_sensitivity.set_yscale("log")
        # 纵轴只覆盖平坦段附近，裁掉 1/f 噟声与共振发散，避免平坦段被压扁
        visible = (freq >= 0.5) & (freq <= max(fit_result.f_larmor_Hz * 2, 1000))
        curves = np.concatenate([np.asarray(item.sens_corrected)[visible] for item in sensitivity] + [best.sens_raw[visible]])
        axis_sensitivity.set_ylim(0.8 * float(np.nanmin(curves)), 20 * best.sens_flat); axis_sensitivity.grid(True, alpha=0.3, which="both"); axis_sensitivity.legend(fontsize=8)
        save_figure(figure, results / "full_analysiswithoutpump.png")
    timestamp = str(config.get("timestamp", run_dir.name)); run_tag = str(config.get("run_tag", "sens"))
    params = {"Pump_laser_power": _value(values, "pump_laser_power", 0.0), "Probe_laser_power": _value(values, "probe_laser_power", 0.0), "X_magnetic_field": _value(values, "x_magnetic_field", 0.0), "Y_magnetic_field": _value(values, "y_magnetic_field", 0.0), "main_magnetic_field": _value(values, "main_magnetic_field", 0.0), "temperature": _value(values, "temperature", 0.0), "PUMP_MOD_DUTY": _value(values, "pump_mod_duty", 0.0), "PUMP_MOD_AMPLITUDE": _value(values, "pump_mod_amplitude", 0.0), "GS200_current_range_A": float(range_set[best_index]), "GS200_current_range_actual_A": float(range_actual[best_index])}
    write_run_record(run_dir, params, fit_result, best, summary_path=results / "run_summary.csv", timestamp=timestamp, run_tag=run_tag, n_avg=n_avg, freq_resolution_Hz=float(freq[1] - freq[0]))
    summary = {"success": True, "experiment_id": "static-sensitivity", "run_dir": str(run_dir), "timestamp": timestamp, "best_range_label": labels[best_index], "fit_valid": bool(fit_result.is_valid), "fit_rejection_reasons": fit_result.rejection_reasons, "acquisition_method": "dc_step", "fit_points": int(np.count_nonzero(fit_mask)), "fit_center_v": float(fit_result.V0), "fit_r_squared": float(fit_result.r_squared), "fit_rmse": float(fit_result.rmse), "fit_B0_fT": float(fit_result.B0_fT), "fit_gamma_fT": float(fit_result.gamma_fT), "fit_slope_V_per_fT": float(fit_result.slope_V_per_fT), "fit_larmor_frequency_Hz": float(fit_result.f_larmor_Hz), "fit_linewidth_hwhm_Hz": float(fit_result.f_larmor_Hz), "sensitivity_flat_fT_per_sqrt_Hz": float(best.sens_flat), "flat_frequency_min_Hz": float(best.flat_fmin), "flat_frequency_max_Hz": float(best.flat_fmax), "files": sorted(path.name for path in results.iterdir() if path.is_file()), "plot_profile": "paper"}
    _save_summary(results, summary)
    return summary

