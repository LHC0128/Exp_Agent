# %% [markdown]
# # RF 灵敏度：RF 解调器 X 色散斜率与 X 噪声 PSD

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
from ..mx_y_rf_sensitivity.analysis_core import absolute_dispersive_response, fit_absolute_dispersive_response, fit_lorentzian_response, lorentzian_response


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
            return {"success": True, "method": "dispersive_curve_fit", "failure_reason": "", "r_squared": r_squared, "popt": popt, "perr": np.sqrt(np.diag(covariance)), "pcov": covariance, "slope_V_per_nT": slope, "optimum_nT": float(popt[2]), "fit_x_nT": fit_x, "fit_y_V": dispersive(fit_x, *popt)}
        except (RuntimeError, ValueError, FloatingPointError, ZeroDivisionError) as exc:
            failure_reason = str(exc)
    gradient = np.gradient(y, x)
    index = int(np.argmax(np.abs(gradient)))
    return {"success": False, "method": "numeric_gradient", "failure_reason": failure_reason, "r_squared": float("nan"), "popt": np.full(4, np.nan), "perr": np.full(4, np.nan), "slope_V_per_nT": float(abs(gradient[index])), "optimum_nT": float(x[index]), "fit_x_nT": np.array([]), "fit_y_V": np.array([])}


def fit_bandwidth(directory, config):
    """加载 A13B 每点 R 均值做洛伦兹拟合；色散等效模式返回 None。"""
    if config["parameters"].get("LINEWIDTH_MODE", "dispersive_equivalent") != "frequency_sweep":
        return None
    path = directory / "raw" / "bandwidth_data.npz"
    if not path.is_file():
        raise ValueError("线宽模式为扫频测线宽，但缺少 raw/bandwidth_data.npz，请重跑 A13B 带宽扫频")
    check_cancelled()
    expected_points = config["parameters"].get("BW_FREQ_POINTS")
    selection = {}
    with np.load(path, allow_pickle=False) as data:
        frequency = np.asarray(data["frequency_Hz"], dtype=float)
        if "demod3_r_V" not in data:
            raise ValueError("带宽扫频原始数据缺少 R 均值，请重跑 A13B 带宽扫频")
        response = np.asarray(data["demod3_r_V"], dtype=float)
        amplitude = float(data["z_rf_amplitude_vpp"])
        if "demod_idx" in data and int(data["demod_idx"]) != int(config["parameters"].get("DEMOD3_IDX", 3)):
            raise ValueError("带宽扫频记录的 RF 解调器索引与运行配置不一致")
        if "skipped_z_rf_frequency_Hz" in data:
            skipped = float(data["skipped_z_rf_frequency_Hz"])
            parameters = config["parameters"]
            if not math.isfinite(skipped) or not math.isclose(skipped, parameters["Z_RF_FREQ"], rel_tol=0, abs_tol=1e-6):
                raise ValueError("带宽扫频跳过的频率与运行配置 Z_RF_FREQ 不一致")
            expected = np.linspace(parameters["BW_FREQ_START"], parameters["BW_FREQ_STOP"], int(expected_points))
            expected = expected[~np.isclose(expected, skipped, rtol=0, atol=1e-6)]
            if frequency.shape != expected.shape or not np.allclose(frequency, expected, rtol=0, atol=1e-6):
                raise ValueError("带宽扫频原始数据不完整或与跳过 Z_RF_FREQ 后的网格不一致")
            expected_points = len(expected)
            selection["skipped_z_rf_frequency_Hz"] = skipped
    if frequency.ndim != 1 or frequency.size < 5 or frequency.size != int(expected_points if expected_points is not None else frequency.size) \
            or response.shape != frequency.shape or not np.all(np.isfinite(frequency)) \
            or not np.all(np.isfinite(response)) or np.any(np.diff(frequency) <= 0) or not math.isfinite(amplitude) or amplitude <= 0:
        raise ValueError("带宽扫频原始数据不完整或含非有限值")
    result = fit_lorentzian_response(frequency, response)
    if not result.success or not math.isfinite(result.gamma) or result.gamma <= 0:
        raise ValueError("带宽扫频洛伦兹拟合质量不合格：" + "；".join(result.rejection_reasons))
    fit_freq = np.linspace(float(frequency.min()), float(frequency.max()), 400)
    return {"center_Hz": float(result.center), "hwhm_Hz": float(result.gamma), "amplitude_V": float(result.amplitude),
            "offset_V": float(result.offset), "r_squared": float(result.r_squared), "gamma_uncertainty_Hz": float(result.uncertainties[1]),
            "n_points": int(frequency.size), "frequency_min_Hz": float(frequency.min()), "frequency_max_Hz": float(frequency.max()),
            "rf_amplitude_vpp": amplitude, "response_component": "R", **selection, "fit_freq_Hz": fit_freq, "fit_r_V": lorentzian_response(fit_freq, *result.parameters)}


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


def control_inputs(directory, config):
    from .control_analysis import control_inputs as inputs
    return inputs(directory, config)


def load_inputs(directory, config):
    if run_format.is_multi_control(config):
        from . import control_analysis
        return control_analysis.load_inputs(directory, config)
    parameters = config["parameters"]
    expected_points = int(parameters["RF_AMP_POINTS"])
    expected_noise = int(parameters["NOISE_N_AVG"])
    nperseg_config = int(parameters["NOISE_NPERSEG"])
    if expected_points < 2 or expected_noise < 1 or nperseg_config < 2:
        raise ValueError("运行参数中的响应点数、噪声次数或 Welch 段长无效")
    check_cancelled()
    with np.load(directory / "raw" / "response_data.npz", allow_pickle=False) as data:
        if "demod_idx" in data and int(data["demod_idx"]) != int(parameters.get("DEMOD3_IDX", 3)):
            raise ValueError("响应记录的 RF 解调器索引与运行配置不一致")
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
    if run_format.is_multi_control(config):
        from . import control_analysis
        return control_analysis.response_fit(directory, config, component=component)
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
    if run_format.is_multi_control(config):
        from . import control_analysis
        return control_analysis.noise_psd(directory, config, nperseg_config)
    from .noise_analysis import noise_psd as calculate
    _, _, noise_files = load_inputs(directory, config)
    return calculate(directory, config, nperseg_config, noise_files)


def sensitivity_and_plots(directory, config, nperseg_config, *, identity=run_format):
    if run_format.is_multi_control(config):
        from . import control_analysis
        return control_analysis.sensitivity_and_plots(directory, config, nperseg_config, identity=identity)
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
        before = saved["unfiltered_psd_avg_V2_per_Hz"] if "unfiltered_psd_avg_V2_per_Hz" in saved else average
        keep = saved["included_record_mask"] if "included_record_mask" in saved else np.ones(len(matrix), dtype=bool)
    quality_path = directory / "results/noise_rejection_summary.json"
    quality = json.loads(quality_path.read_text(encoding="utf-8")) if quality_path.is_file() else {"policy": "none", "n_rejected": 0}
    spectra = matrix
    hwhm_nT = None
    hwhm_hz = None
    bandwidth = fit_bandwidth(directory, config)
    if bandwidth is not None:
        hwhm_hz = bandwidth["hwhm_Hz"]
    elif fit["success"]:
        # 回退：未选择带宽扫频时，用幅度域色散 gamma 作为等效频域 HWHM。
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
    before_result = compute_sensitivity(before, frequency, slope_V_per_fT=slope_ft,
        f_larmor_Hz=hwhm_hz or 0.0, f_skip_low=3.0, flat_frac=1.0) if quality["n_rejected"] else sensitivity_result
    sensitivity_raw = sensitivity_result.sens_raw
    sensitivity_corrected = sensitivity_result.sens_corrected
    sensitivity = sensitivity_corrected
    flat_mask = sensitivity_result.flat_mask & np.isfinite(sensitivity)
    median = float(np.median(sensitivity[flat_mask])) if flat_mask.any() else float("nan")
    raw_median = float(np.median(sensitivity_raw[flat_mask])) if flat_mask.any() else float("nan")
    flat_min, flat_max = (float(frequency[flat_mask].min()), float(frequency[flat_mask].max())) if flat_mask.any() else (float("nan"), float("nan"))
    actual_rate = float(np.median(rates))
    quality.update(before_flat_median_fT_per_sqrtHz=float(np.median(before_result.sens_corrected[flat_mask])) if flat_mask.any() else None,
                   after_flat_median_fT_per_sqrtHz=median)
    analysis = _plain({
        "experiment_id": identity.EXPERIMENT_ID, "experiment_type": identity.DATA_TYPE, "schema_version": 2,
        "data_dir": str(directory), "signal_chain": config.get("signal_chain", identity.SIGNAL_CHAIN), "response_component": component, "noise_component": "X",
        "rf_demod_index": int(config["parameters"].get("DEMOD3_IDX", 3)),
        "stage_frequencies": config.get("stage_frequencies", {}),
        "response_fit": {key: value for key, value in fit.items() if not key.startswith("fit_")} | {"slope_V_per_fT": slope_ft, "z_v_to_nT": z_v_to_nt, "gamma_nT": hwhm_nT, "f_larmor_Hz": hwhm_hz,
            "slope_estimated": not fit["success"], "slope_source": "underlying_dispersion_center" if component == "R" and fit["success"] else fit["method"]},
        "frequency_response_correction": {
            "applied": hwhm_hz is not None,
            "model": "sqrt(1 + (f / f_larmor_Hz)^2)",
            "source": "bandwidth_lorentzian_fit" if bandwidth is not None else "dispersive_gamma",
            "source_detail": (f"A13B R 均值带宽扫频洛伦兹拟合（中心 {bandwidth['center_Hz']:.6g} Hz）" if bandwidth is not None else f"RF 解调器 {component} 对 Z RF 场色散拟合的 gamma"),
            "gamma_nT": hwhm_nT,
            "hwhm_Hz": hwhm_hz,
        },
        "bandwidth_fit": {"enabled": False, "reason": "LINEWIDTH_MODE 为色散等效线宽，未启用带宽扫频"} if bandwidth is None
            else {"enabled": True, **{key: value for key, value in bandwidth.items() if not key.startswith("fit_")}},
        "noise_rejection": quality,
        "noise_psd": {"n_avg": int(keep.sum()), "n_records": len(spectra), "n_rejected": int((~keep).sum()), "actual_rate_Sa_s": actual_rate, "actual_rates_Sa_s": rates, "nperseg": nperseg_config, "freq_min_Hz": float(frequency[0]), "freq_max_Hz": float(frequency[-1])},
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
    np.savez(output / "sensitivity.npz", freq_Hz=frequency, unfiltered_psd_avg_V2_per_Hz=before, unfiltered_sensitivity_fT_per_sqrtHz=before_result.sens_corrected, included_record_mask=keep, sensitivity_fT_per_sqrtHz=sensitivity, sensitivity_raw_fT_per_sqrtHz=sensitivity_raw, sensitivity_corrected_fT_per_sqrtHz=sensitivity_corrected, correction_factor=sensitivity_result.correction_factor, psd_avg_V2_per_Hz=average, slope_V_per_nT=slope_nt, slope_V_per_fT=slope_ft, gamma_nT=np.nan if hwhm_nT is None else hwhm_nT, f_larmor_Hz=np.nan if hwhm_hz is None else hwhm_hz, bandwidth_hwhm_Hz=np.nan if bandwidth is None else bandwidth["hwhm_Hz"], best_median_fT_per_sqrtHz=median, flat_median_fT_per_sqrtHz=median, raw_flat_median_fT_per_sqrtHz=raw_median, flat_fmin_Hz=flat_min, flat_fmax_Hz=flat_max, flat_mask=flat_mask.astype(np.uint8))
    (output / "analysis.yaml").write_text(yaml.safe_dump(analysis, allow_unicode=True, sort_keys=False), encoding="utf-8")
    (output / "analysis.json").write_text(json.dumps(analysis, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")

    def response_plot(axis):
        for other in ("X", "Y", "R"):
            if other != component:
                axis.plot(field, response[f"demod3_{other.lower()}_V"], ".", color="gray", alpha=0.5, ms=4, label=f"RF demodulator {other}")
        axis.plot(field, x, "o", ms=3, label=f"RF demodulator {component} (calibration)")
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
        axis.set(xlabel="RF field (nT)", ylabel="RF demodulator (V)", title=f"{component} calibration / X noise" + ("" if fit["success"] else " [estimated slope]"))
        axis.grid(True, alpha=0.3)
        axis.legend(fontsize=8)

    def sensitivity_plot(axis):
        positive = frequency > 0
        if quality["n_rejected"]:
            axis.loglog(frequency[positive], before_result.sens_corrected[positive], "--", color="tab:orange", alpha=0.7, label="Unfiltered sensitivity")
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

    def bandwidth_plot(axis):
        axis.plot(frequency_bw, response_bw, "o", ms=3, label="RF demodulator R (bandwidth sweep)")
        axis.plot(bandwidth["fit_freq_Hz"], bandwidth["fit_r_V"], "k--", label=f"Lorentzian fit (R²={bandwidth['r_squared']:.3f})")
        axis.axvline(bandwidth["center_Hz"], color="gray", linestyle=":", alpha=0.5)
        annotation = (f"Center = {bandwidth['center_Hz']:.6g} Hz\nHWHM = {bandwidth['hwhm_Hz']:.6g} Hz\n"
                      f"RF amplitude = {bandwidth['rf_amplitude_vpp']:.6g} Vpp")
        axis.text(0.02, 0.96, annotation, transform=axis.transAxes, va="top", ha="left", fontsize=8, bbox={"boxstyle": "round,pad=0.3", "facecolor": "white", "alpha": 0.8, "edgecolor": "0.7"})
        axis.set(xlabel="RF frequency (Hz)", ylabel="RF demodulator R (V)", title="R bandwidth Lorentzian fit")
        axis.grid(True, alpha=0.3)
        axis.legend(fontsize=8)

    with plot_style_context("paper"):
        for name, draw in (("response_curve.png", response_plot), ("sensitivity.png", sensitivity_plot)):
            check_cancelled()
            figure, axis = new_figure(layout="constrained")
            draw(axis)
            save_figure(figure, output / name)
        figure, axis = new_figure(layout="constrained")
        if quality["n_rejected"]:
            axis.loglog(frequency[frequency > 0], before[frequency > 0], "--", label="All records")
        axis.loglog(frequency[frequency > 0], average[frequency > 0], label=f"Accepted records ({int(keep.sum())}/{len(keep)})")
        axis.set(xlabel="Frequency (Hz)", ylabel="X noise PSD (V²/Hz)")
        axis.legend(fontsize=8)
        save_figure(figure, output / "noise_psd.png")
        check_cancelled()
        figure, axes = new_figure(nrows=2, height_ratios=(1.2, 1), width_mm=177.8, height_mm=130, layout="constrained")
        response_plot(axes[0])
        sensitivity_plot(axes[1])
        save_figure(figure, output / "full_analysis.png")
        if bandwidth is not None:
            with np.load(directory / "raw" / "bandwidth_data.npz", allow_pickle=False) as data:
                frequency_bw = np.asarray(data["frequency_Hz"], dtype=float)
                response_bw = np.asarray(data["demod3_r_V"], dtype=float)
            for extension in ("png", "pdf"):
                check_cancelled()
                figure, axis = new_figure(layout="constrained")
                bandwidth_plot(axis)
                save_figure(figure, output / f"bandwidth_fit.{extension}")
    report_runtime_progress("analysis", "离线分析完成", 100)
    return analysis

