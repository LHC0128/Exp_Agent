# %% [markdown]
# # RF 灵敏度：Demod3 X 色散斜率与 X 噪声 PSD

from __future__ import annotations

from pathlib import Path
import json
import math

import numpy as np
import yaml
from scipy.optimize import curve_fit
from scipy.signal import welch

from sensitivity_analysis import compute_sensitivity
from sensitivity_analysis.fitting import LARMOR_PER_NT

from ...experiment_runtime import check_cancelled, report_runtime_progress
from ...plotting import new_figure, plot_style_context, save_figure
from . import run_format
from ..mx_y_rf_sensitivity.analysis_core import absolute_dispersive_response, fit_absolute_dispersive_response


def dispersive(x, amplitude, gamma, x0, offset):
    return amplitude * (x - x0) / ((x - x0) ** 2 + gamma ** 2) + offset


def fit_response_curve(x: np.ndarray, y: np.ndarray, *, component="X") -> dict:
    """保持原 X 色散模型；失败时保留数值斜率与回退原因。"""
    failure_reason = "响应点数不足 5，使用数值斜率"
    if component == "R":
        result = fit_absolute_dispersive_response(x, y, r_squared_min=None,
            relative_gamma_uncertainty_max=None, initial_center=float(np.clip(0.0, x.min(), x.max())))
        slope = abs(result.amplitude) / result.gamma**2 if result.gamma > 0 else float("nan")
        if result.success and math.isfinite(slope) and slope > 0:
            fit_x = np.linspace(float(x.min()), float(x.max()), 400)
            return {"success": True, "method": result.method, "failure_reason": "", "r_squared": result.r_squared,
                    "popt": np.asarray(result.parameters), "perr": np.asarray(result.uncertainties),
                    "slope_V_per_nT": slope, "optimum_nT": result.center, "fit_x_nT": fit_x,
                    "fit_y_V": absolute_dispersive_response(fit_x, *result.parameters)}
        failure_reason = "；".join(result.rejection_reasons) or "绝对色散拟合斜率无效"
    elif len(x) >= 5:
        gamma = float(np.ptp(x)) / 4
        try:
            popt, covariance = curve_fit(dispersive, x, y, p0=[float(np.ptp(y)) * gamma, gamma, 0.0, float(np.median(y))], maxfev=20000)
            slope = abs(float(popt[0])) / float(popt[1]) ** 2
            if not math.isfinite(slope) or slope <= 0:
                raise ValueError("色散拟合斜率无效")
            residuals = y - dispersive(x, *popt)
            total = float(np.sum((y - float(np.mean(y))) ** 2))
            r_squared = 1.0 - float(np.sum(residuals**2)) / total if total > 0 else float("nan")
            fit_x = np.linspace(float(x.min()), float(x.max()), 400)
            return {"success": True, "method": "dispersive_curve_fit", "failure_reason": "", "r_squared": r_squared, "popt": popt, "perr": np.sqrt(np.diag(covariance)), "slope_V_per_nT": slope, "optimum_nT": float(popt[2]), "fit_x_nT": fit_x, "fit_y_V": dispersive(fit_x, *popt)}
        except (RuntimeError, ValueError, FloatingPointError, ZeroDivisionError) as exc:
            failure_reason = str(exc)
    gradient = np.gradient(y, x)
    index = int(np.argmax(np.abs(gradient)))
    return {"success": False, "method": "numeric_gradient", "failure_reason": failure_reason, "r_squared": float("nan"), "popt": np.full(4, np.nan), "perr": np.full(4, np.nan), "slope_V_per_nT": float(abs(gradient[index])), "optimum_nT": float(x[index]), "fit_x_nT": np.array([]), "fit_y_V": np.array([])}


def _plain(value):
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, np.ndarray)):
        return [_plain(item) for item in value]
    if isinstance(value, np.generic):
        return _plain(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def load_inputs(directory, config):
    parameters = config["parameters"]
    expected_points = int(parameters["RF_AMP_POINTS"])
    expected_noise = int(parameters["NOISE_N_AVG"])
    nperseg_config = int(parameters["NOISE_NPERSEG"])
    if expected_points < 2 or expected_noise < 1 or nperseg_config < 2:
        raise ValueError("运行参数中的响应点数、噪声次数或 Welch 段长无效")
    check_cancelled()
    with np.load(directory / "raw" / "response_data.npz", allow_pickle=False) as data:
        response = {key: np.asarray(data[key], dtype=float) for key in ("amplitudes_V", "amplitudes_nT", "demod3_x_V", "demod3_y_V", "demod3_r_V")}
        z_v_to_nt = float(data["z_v_to_nT"])
    if any(value.shape != (expected_points,) or not np.all(np.isfinite(value)) for value in response.values()):
        raise ValueError("响应原始数据不完整或含非有限值")
    field = response["amplitudes_nT"]
    if np.any(np.diff(field) <= 0) or not math.isfinite(z_v_to_nt) or z_v_to_nt <= 0 or not np.allclose(field, response["amplitudes_V"] * z_v_to_nt):
        raise ValueError("响应磁场轴或电压到磁场标定无效")
    noise_files = sorted((directory / "raw").glob("noise_D3_*.npz"))
    if [path.name for path in noise_files] != [f"noise_D3_{index:04d}.npz" for index in range(expected_noise)]:
        raise ValueError("噪声原始记录不完整")
    return response, z_v_to_nt, noise_files


def response_fit(directory, config, *, component="X"):
    response, z_v_to_nt, _ = load_inputs(directory, config)
    fit = fit_response_curve(response["amplitudes_nT"], response[f"demod3_{component.lower()}_V"], component=component)
    slope_ft = fit["slope_V_per_nT"] / 1_000_000
    if not math.isfinite(slope_ft) or slope_ft <= 1e-30:
        raise ValueError(f"{component} 响应斜率过小或无效，无法计算灵敏度")
    gamma = abs(float(fit["popt"][1])) if fit["success"] else float("nan")
    np.savez(directory / "results/response_fit.npz", **fit, **response, z_v_to_nT=z_v_to_nt,
             slope_V_per_fT=slope_ft, gamma_nT=gamma, f_larmor_Hz=gamma * LARMOR_PER_NT,
             response_component=component, slope_estimated=not fit["success"])
    return _plain({key: value for key, value in fit.items() if not key.startswith("fit_")})


def noise_psd(directory, config, nperseg_config):
    _, _, noise_files = load_inputs(directory, config)
    spectra, rates = [], []
    frequency = None
    for path in noise_files:
        check_cancelled()
        with np.load(path, allow_pickle=False) as data:
            waveform = np.asarray(data["noise_x_V"], dtype=float)
            rate = float(data["actual_rate_Sa_s"])
            if str(data["signal_path"]) != "sample.x" or int(data["demod_idx"]) != 3:
                raise ValueError("噪声记录必须来自 Demod3 X")
        if waveform.ndim != 1 or len(waveform) < 2 or not np.all(np.isfinite(waveform)) or not math.isfinite(rate) or rate <= 0:
            raise ValueError(f"无效噪声记录：{path.name}")
        segment = min(nperseg_config, len(waveform))
        f, psd = welch(waveform - waveform.mean(), fs=rate, nperseg=segment, noverlap=segment // 2, scaling="density")
        if frequency is None:
            frequency = f
        elif len(f) != len(frequency) or not np.allclose(f, frequency):
            psd = np.interp(frequency, f, psd)
        spectra.append(psd)
        rates.append(rate)
    matrix = np.asarray(spectra)
    average = matrix.mean(axis=0)
    np.savez(directory / "results/psd_avg.npz", freq_Hz=frequency, psd_avg_V2_per_Hz=average,
             psd_matrix_V2_per_Hz=matrix, n_avg=len(spectra), actual_rate_Sa_s=float(np.median(rates)),
             actual_rates_Sa_s=rates, nperseg=nperseg_config)
    return dict(n_avg=len(spectra), actual_rates_Sa_s=rates, nperseg=nperseg_config)


def sensitivity_and_plots(directory, config, nperseg_config, *, identity=run_format):
    component = identity.RESPONSE_COMPONENT
    with np.load(directory / "results/response_fit.npz", allow_pickle=False) as saved:
        cached = dict(saved)
    response = {key: cached[key] for key in ("amplitudes_V", "amplitudes_nT", "demod3_x_V", "demod3_y_V", "demod3_r_V")}
    fit = {key: cached[key].item() if cached[key].ndim == 0 else cached[key]
           for key in ("success", "method", "failure_reason", "r_squared", "popt", "perr", "slope_V_per_nT", "optimum_nT", "fit_x_nT", "fit_y_V")}
    fit.update(success=bool(fit["success"]), method=str(fit["method"]), failure_reason=str(fit["failure_reason"]))
    z_v_to_nt = float(cached["z_v_to_nT"])
    slope_nt, slope_ft = float(fit["slope_V_per_nT"]), float(cached["slope_V_per_fT"])
    field, x = response["amplitudes_nT"], response[f"demod3_{component.lower()}_V"]
    with np.load(directory / "results/psd_avg.npz", allow_pickle=False) as saved:
        frequency, average, matrix, rates = (saved[key] for key in ("freq_Hz", "psd_avg_V2_per_Hz", "psd_matrix_V2_per_Hz", "actual_rates_Sa_s"))
    spectra = matrix
    hwhm_nT = None
    hwhm_hz = None
    # shortcut: 用幅度域色散 gamma 作为等效频域 HWHM；需要真实扫频线宽时接入独立频响标定。
    if fit["success"]:
        fitted_gamma_nT = abs(float(np.asarray(fit["popt"], dtype=float)[1]))
        if math.isfinite(fitted_gamma_nT) and fitted_gamma_nT > 0:
            hwhm_nT = fitted_gamma_nT
            hwhm_hz = fitted_gamma_nT * LARMOR_PER_NT
    sensitivity_result = compute_sensitivity(
        average,
        frequency,
        slope_V_per_fT=slope_ft,
        f_larmor_Hz=hwhm_hz or 0.0,
        f_skip_low=3.0,
        flat_frac=1.0,
    )
    sensitivity_raw = sensitivity_result.sens_raw
    sensitivity_corrected = sensitivity_result.sens_corrected
    sensitivity = sensitivity_corrected
    flat_mask = sensitivity_result.flat_mask & np.isfinite(sensitivity)
    median = float(np.median(sensitivity[flat_mask])) if flat_mask.any() else float("nan")
    raw_median = float(np.median(sensitivity_raw[flat_mask])) if flat_mask.any() else float("nan")
    flat_min, flat_max = (float(frequency[flat_mask].min()), float(frequency[flat_mask].max())) if flat_mask.any() else (float("nan"), float("nan"))
    actual_rate = float(np.median(rates))
    analysis = _plain({
        "experiment_id": identity.EXPERIMENT_ID, "experiment_type": identity.DATA_TYPE, "schema_version": 2,
        "data_dir": str(directory), "signal_chain": identity.SIGNAL_CHAIN, "response_component": component, "noise_component": "X",
        "stage_frequencies": config.get("stage_frequencies", {}),
        "response_fit": {key: value for key, value in fit.items() if not key.startswith("fit_")} | {"slope_V_per_fT": slope_ft, "z_v_to_nT": z_v_to_nt, "gamma_nT": hwhm_nT, "f_larmor_Hz": hwhm_hz,
            "slope_estimated": not fit["success"], "slope_source": "underlying_dispersion_center" if component == "R" and fit["success"] else fit["method"]},
        "frequency_response_correction": {
            "applied": hwhm_hz is not None,
            "model": "sqrt(1 + (f / f_larmor_Hz)^2)",
            "source": f"Demod3 {component} 对 Z RF 场色散拟合的 gamma",
            "gamma_nT": hwhm_nT,
            "hwhm_Hz": hwhm_hz,
        },
        "noise_psd": {"n_avg": len(spectra), "actual_rate_Sa_s": actual_rate, "actual_rates_Sa_s": rates, "nperseg": nperseg_config, "freq_min_Hz": float(frequency[0]), "freq_max_Hz": float(frequency[-1])},
        "sensitivity": {"best_median_fT_per_sqrtHz": median, "flat_median_fT_per_sqrtHz": median, "raw_flat_median_fT_per_sqrtHz": raw_median, "flat_fmin_Hz": flat_min, "flat_fmax_Hz": flat_max, "report_fmin_Hz": 3.0, "report_fmax_Hz": flat_max, "correction_applied": hwhm_hz is not None},
    })
    if component == "R":
        # shortcut: 跨 RF 频率共用中心斜率；实测增益不一致时接入独立频响标定。
        analysis["calibration_assumption"] = {
            "response_frequency_Hz": config["parameters"]["Z_RF_FREQ"],
            "noise_frequency_Hz": config["parameters"]["Z_RF_NOISE_FREQ"],
            "gain_equivalence_verified": False,
            "description": "假定 R 标定中心斜率可用于另一 RF 频率的 X 噪声；增益一致性及等效线宽补偿待实测验证。",
        }
    # 全部输入和计算验证完成后才创建或写入结果，旧格式不产生任何结果文件。
    check_cancelled()
    output = directory / "results"
    output.mkdir(exist_ok=True)
    report_runtime_progress("analysis", f"保存 {component} 标定 / X 噪声灵敏度结果", 95)
    np.savez(output / "sensitivity.npz", freq_Hz=frequency, sensitivity_fT_per_sqrtHz=sensitivity, sensitivity_raw_fT_per_sqrtHz=sensitivity_raw, sensitivity_corrected_fT_per_sqrtHz=sensitivity_corrected, correction_factor=sensitivity_result.correction_factor, psd_avg_V2_per_Hz=average, slope_V_per_nT=slope_nt, slope_V_per_fT=slope_ft, gamma_nT=np.nan if hwhm_nT is None else hwhm_nT, f_larmor_Hz=np.nan if hwhm_hz is None else hwhm_hz, best_median_fT_per_sqrtHz=median, flat_median_fT_per_sqrtHz=median, raw_flat_median_fT_per_sqrtHz=raw_median, flat_fmin_Hz=flat_min, flat_fmax_Hz=flat_max, flat_mask=flat_mask.astype(np.uint8))
    (output / "analysis.yaml").write_text(yaml.safe_dump(analysis, allow_unicode=True, sort_keys=False), encoding="utf-8")
    (output / "analysis.json").write_text(json.dumps(analysis, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")

    def response_plot(axis):
        for other in ("X", "Y", "R"):
            if other != component:
                axis.plot(field, response[f"demod3_{other.lower()}_V"], ".", color="gray", alpha=0.5, ms=4, label=f"Demod3 {other}")
        axis.plot(field, x, "o", ms=3, label=f"Demod3 {component} (calibration)")
        if fit["success"]:
            fit_label = "Absolute dispersive fit" if component == "R" else "Dispersive fit"
            axis.plot(fit["fit_x_nT"], fit["fit_y_V"], "k--", label=f"{fit_label} (R²={fit['r_squared']:.3f})")
            axis.axvline(fit["optimum_nT"], color="gray", linestyle=":", alpha=0.5)
            slope_label = "Underlying center |dD/dB|" if component == "R" else "dX/dB"
            annotation = f"{slope_label} = {slope_nt:.3e} V/nT\nDispersive HWHM = {abs(float(fit['popt'][1])):.3g} nT"
            if hwhm_hz is not None:
                annotation += f" ({hwhm_hz:.3g} Hz)"
        else:
            annotation = f"|d{component}/dB| ≈ {slope_nt:.3e} V/nT (numeric estimate)"
        axis.text(0.02, 0.96, annotation, transform=axis.transAxes, va="top", ha="left", fontsize=8, bbox={"boxstyle": "round,pad=0.3", "facecolor": "white", "alpha": 0.8, "edgecolor": "0.7"})
        axis.set(xlabel="RF field (nT)", ylabel="Demod3 (V)", title=f"{component} calibration / X noise" + ("" if fit["success"] else " [estimated slope]"))
        axis.grid(True, alpha=0.3)
        axis.legend(fontsize=8)

    def sensitivity_plot(axis):
        positive = frequency > 0
        axis.loglog(frequency[positive], sensitivity[positive], label="Lorentzian-corrected sensitivity" if hwhm_hz is not None else "Estimated sensitivity")
        axis.loglog(frequency[positive], sensitivity_raw[positive], color="gray", linewidth=0.7, alpha=0.5, label="Raw sensitivity")
        if flat_mask.any():
            axis.plot(frequency[flat_mask], sensitivity[flat_mask], color="darkgreen", lw=1.6, label=f"Flat band {flat_min:.0f}–{flat_max:.0f} Hz")
        if math.isfinite(median):
            axis.axhline(median, color="darkgreen", linestyle="--", alpha=0.7, label=f"Median = {median:.3g} fT/√Hz")
        if hwhm_hz is not None:
            axis.axvline(hwhm_hz, color="tab:orange", linestyle=":", alpha=0.7, label=f"HWHM = {hwhm_hz:.0f} Hz")
        axis.set(xlabel="Frequency (Hz)", ylabel="Sensitivity (fT/√Hz)", title=f"{component} calibration / X noise sensitivity" + (" (Lorentzian corrected)" if hwhm_hz is not None else " (estimated, uncorrected)"))
        # 与静磁场灵敏度分析一致：横轴只到 max(2×HWHM, 平坦段上限, 1000 Hz)，不再铺满全频段
        x_max = max(2.0 * (hwhm_hz or 0.0), flat_max if math.isfinite(flat_max) else 0.0, 1000.0)
        axis.set_xlim(0.5, x_max)
        # 纵轴只覆盖平坦段附近，裁掉 1/f 噪声与共振发散，避免平坦段被压扁
        visible = (frequency >= 0.5) & (frequency <= x_max)
        finite_curves = np.concatenate([sensitivity[visible], sensitivity_raw[visible]])
        finite_curves = finite_curves[np.isfinite(finite_curves)]
        lower = 0.8 * float(np.nanmin(finite_curves)) if finite_curves.size else 0.0
        if lower > 0 and math.isfinite(median) and median > 0:
            axis.set_ylim(lower, 20.0 * median)
        axis.grid(True, alpha=0.3, which="both")
        axis.legend(fontsize=8)

    with plot_style_context("paper"):
        for name, draw in (("response_curve.png", response_plot), ("sensitivity.png", sensitivity_plot)):
            check_cancelled()
            figure, axis = new_figure(layout="constrained")
            draw(axis)
            save_figure(figure, output / name)
        figure, axis = new_figure(layout="constrained")
        axis.loglog(frequency[frequency > 0], average[frequency > 0])
        axis.set(xlabel="Frequency (Hz)", ylabel="X noise PSD (V²/Hz)")
        save_figure(figure, output / "noise_psd.png")
        check_cancelled()
        figure, axes = new_figure(nrows=2, height_ratios=(1.2, 1), width_mm=177.8, height_mm=130, layout="constrained")
        response_plot(axes[0])
        sensitivity_plot(axes[1])
        save_figure(figure, output / "full_analysis.png")
    report_runtime_progress("analysis", "离线分析完成", 100)
    return analysis

