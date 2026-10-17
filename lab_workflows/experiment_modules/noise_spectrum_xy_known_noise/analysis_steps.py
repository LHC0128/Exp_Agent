"""两态谱计算、移动脊线、谱分离和定量比较；无硬件依赖。"""
from types import SimpleNamespace
import numpy as np
from scipy.signal import welch
from sensitivity_analysis.fitting import _dispersive
from .analysis_core import *
from .analysis_core import _reason_counts, _median_ratio, _injection_band_mask
from .calibration_source import load_source
from .dispersion import resolve_dispersion


def inputs(ctx):
    run_dir, results, config, options, summary = ctx.run_dir, ctx.results, ctx.config, ctx.options, ctx.summary
    return run_dir, results, config, options, summary


def spectral_data(ctx):
    with np.load(ctx.results / "psd_matrices.npz", allow_pickle=False) as data:
        psd_on, psd_off, frequency, voltage, rows = (data[key] for key in ("psd_on", "psd_off", "freq_axis", "amplitudes", "paired_rows"))
    band = (frequency > 0) & (frequency >= ctx.options["frequency_min_hz"]) & (frequency <= ctx.options["frequency_max_hz"])
    if band.sum() < 2:
        raise ValueError("分析频带内点数不足")
    return psd_on, psd_off, frequency, voltage, rows, band


def truth_calibration(ctx):
    truth = ctx.config["truth_chain"]
    k_z = truth.get("K_Z_Hz_per_V")
    if k_z is None:
        return load_z_calibration(ctx.root, truth["z_calibration_run"])
    if isinstance(k_z, bool) or not np.isfinite(k_z) or k_z <= 0:
        raise ValueError("本轮 K_Z 快照无效")
    return SimpleNamespace(k_hz_per_v=float(k_z), source_run=truth["z_calibration_run"],
        uncertainty_hz_per_v=float(truth.get("K_Z_uncertainty_Hz_per_V", 0)))


def step_B01(ctx):
    _, voltage, _, frequency = psd_sampling(ctx.run_dir, ctx.config, ctx.options, "on")
    _, voltage_off, _, frequency_off = psd_sampling(ctx.run_dir, ctx.config, ctx.options, "off")
    require_paired_axes(frequency, voltage, frequency_off, voltage_off)
    if not ctx.config.get("known_noise") or not ctx.config.get("truth_chain"):
        raise ValueError("运行配置缺少 known_noise/truth_chain")
    if not list((ctx.run_dir / "raw").glob("waveform_C*_*.npy")):
        raise ValueError("运行目录没有成对采集原始波形")
    truth_calibration(ctx)


def step_B02(ctx):
    run_dir, results, config, options, summary = inputs(ctx)
    known = config["known_noise"]
    psd_on, frequency, voltage, rows_on, psd_info = compute_psd_matrix(
        run_dir, config, options, "on")
    psd_off, frequency_off, voltage_off, rows_off, _ = compute_psd_matrix(run_dir, config, options, "off")
    require_paired_axes(frequency, voltage, frequency_off, voltage_off)
    save_known_noise_preview(results, run_dir, known, options["bin_width_hz"])
    rows = np.intersect1d(rows_on, rows_off)
    if rows.size < 20:
        raise ValueError(f"off/on 成对有效控制点不足 20 个（{rows.size}）")
    np.savez(results/"psd_matrices.npz", psd_on=psd_on, psd_off=psd_off,
             freq_axis=frequency, amplitudes=voltage, paired_rows=rows,
             actual_rate=psd_info["actual_rate_sa_s"], nperseg=psd_info["nperseg"])
    summary["psd"] = {**psd_info, "paired_points": int(rows.size)}
    differential = psd_on - psd_off
    average = 0.5*(psd_on + psd_off)
    print(f"Welch: {rows.size}/{voltage.size} 点成对, nperseg={psd_info['nperseg']}, "
          f"df={psd_info['bin_width_hz']:.3f} Hz", flush=True)


def step_B03(ctx):
    run_dir, results, config, options, summary = inputs(ctx)
    saved = config.get("parameters", {})
    psd_on, psd_off, frequency, voltage, rows, band = spectral_data(ctx)
    differential, average = psd_on-psd_off, .5*(psd_on+psd_off)
    f, v = frequency[band], voltage[rows]
    # 脊线定位用 OFF（本底）矩阵：注入是带限的，ON 侧脊线在带边饱和变形。
    data_off = psd_off[rows][:, band]
    data_on = psd_on[rows][:, band]
    cal_band = calibration_band(frequency, options, saved)
    ridge = locate_ridge(psd_off[rows][:, cal_band], frequency[cal_band], v)
    fit_spur = ridge["spur_mask"][band[cal_band]]
    np.savez(results/"calibration.npz", **ridge, amplitudes=v, freq_axis=frequency[cal_band])
    plot_two_dimensional(results, average[rows][:, band], f, v, ridge,
                         "noise_spectrum_2d.png", "Average ON/OFF PSD",
                         r"$\log_{10}$ PSD (V$^2$/Hz)")
    plot_two_dimensional(results, np.abs(differential[rows][:, band]), f, v, ridge,
                         "differential_psd.png", "Absolute differential PSD (ON - OFF)",
                         r"$\log_{10}|\mathrm{PSD}_{on}-\mathrm{PSD}_{off}|$ (V$^2$/Hz)")
    summary["calibration"] = {key: float(ridge[key]) for key in ("k", "b", "residual_std_hz", "coverage")}
    summary["calibration"]["support_points"] = int(len(ridge["V_cal"]))
    summary["calibration"]["search_frequency_hz"] = [float(frequency[cal_band][0]), float(frequency[cal_band][-1])]
    support = ridge["k"]*ridge["V_cal"]+ridge["b"]
    summary["calibration"]["support_frequency_hz"] = [float(support.min()), float(support.max())]
    print("移动峰标定:", summary["calibration"], flush=True)


def step_B04(ctx):
    run_dir, results, config, options, summary = inputs(ctx)
    psd_on, psd_off, frequency, voltage, rows, band = spectral_data(ctx)
    f, v = frequency[band], voltage[rows]
    data_on, data_off = psd_on[rows][:, band], psd_off[rows][:, band]
    differential, average = psd_on-psd_off, .5*(psd_on+psd_off)
    with np.load(results / "calibration.npz", allow_pickle=False) as cached:
        ridge = {key: cached[key] for key in cached.files}
    cal_band = calibration_band(frequency, options, config.get("parameters", {}))
    fit_spur = ridge["spur_mask"][band[cal_band]]
    support = ridge["k"]*ridge["V_cal"]+ridge["b"]
    fit_on = fit_local_spectra(data_on, f, ridge["omega_ctrl"], fit_spur,
                               options["fit_half_width_hz"], (support.min(), support.max()),
                               full_range=True)
    fit_off = fit_local_spectra(data_off, f, ridge["omega_ctrl"], fit_spur,
                                options["fit_half_width_hz"], (support.min(), support.max()),
                                full_range=True)
    # 共享分析核心分别验收峰和背景；峰失败时允许独立的远端平台估计。
    background_good = fit_on["background_fit_mask"] & fit_off["background_fit_mask"]
    good = fit_on["fit_mask"] & fit_off["fit_mask"]
    a_diff = fit_on["S_beta"] - fit_off["S_beta"]
    gamma = fit_on["popt"][:, 0]
    summary["fit_quality"] = dict(
        on=_reason_counts(fit_on["rejection_reason"]),
        off=_reason_counts(fit_off["rejection_reason"]),
        fit_half_width_hz=float(options["fit_half_width_hz"]),
        method=SEPARATION_METHOD,
        background_method_on=_reason_counts(fit_on["background_method"]),
        background_method_off=_reason_counts(fit_off["background_method"]),
        background_on=_reason_counts(fit_on["background_rejection_reason"]),
        background_off=_reason_counts(fit_off["background_rejection_reason"]),
        background_both_accepted=int(background_good.sum()),
        control_step_hz=float(np.median(np.diff(ridge["omega_ctrl"]))),
        both_accepted=int(good.sum()),
        either_rejected=int((~good).sum()),
    )
    plot_line_shapes(
        results, f, ridge["omega_ctrl"], data_on, data_off,
        fit_on["popt"], fit_off["popt"],
        fit_on["rejection_reason"], fit_off["rejection_reason"],
        select_line_shape_columns(f, fit_on["rejection_reason"] != "outside_ridge_support", 8),
        options["fit_half_width_hz"], ridge["k"], ridge["b"],
        background_on=fit_on["N_S1"], background_off=fit_off["N_S1"],
    )
    np.savez(results/"popt_fit.npz",
             popt_on=fit_on["popt"], perr_on=fit_on["perr"],
             popt_off=fit_off["popt"], perr_off=fit_off["perr"],
             rejection_on=fit_on["rejection_reason"], rejection_off=fit_off["rejection_reason"],
             background_rejection_on=fit_on["background_rejection_reason"],
             background_rejection_off=fit_off["background_rejection_reason"],
             holdout_on=fit_on["holdout_relative_error"], holdout_off=fit_off["holdout_relative_error"],
             tail_holdout_on=fit_on["tail_holdout_relative_error"],
             tail_holdout_off=fit_off["tail_holdout_relative_error"],
             background_anchor_points_on=fit_on["background_anchor_points"],
             background_anchor_points_off=fit_off["background_anchor_points"],
             background_method_on=fit_on["background_method"],
             background_method_off=fit_off["background_method"],
             background_plateau_relative_spread_on=fit_on["background_plateau_relative_spread"],
             background_plateau_relative_spread_off=fit_off["background_plateau_relative_spread"],
             freq_axis=f, param_names=["gamma", "Amp", "D", "dw"])
    plot_differential_response(results, f, a_diff, good)

    # 两态完整结果使 B06 可在独立进程读取，不重算 PSD 和拟合。
    np.savez(results / "separated_spectra.npz", freq_axis=f,
             **{name+"_"+state: value for state, fit in (("on", fit_on), ("off", fit_off)) for name, value in fit.items()})
    np.savez(results/"fit_diagnostics.npz", frequency_hz=f,
             control_hz=ridge["omega_ctrl"], differential=differential[rows][:, band],
             average=average[rows][:, band], psd_on=data_on, psd_off=data_off,
             spur_mask=fit_spur)

    if not good.any():
        raise ValueError("没有频率点通过全控制轴拟合质量验收")


def resolve_detection_gain(ctx):
    calibration = truth_calibration(ctx)
    source = ctx.options["dispersion_source_run"]
    if source:
        fit = load_source(ctx.root, source, calibration.k_hz_per_v, ctx.config)
    elif ctx.legacy_dispersion:
        fit = resolve_dispersion(ctx.run_dir, ctx.config)
    else:
        raise ValueError("待选择标定曲线")
    if not np.isfinite(fit["slope_v_per_hz"]) or fit["slope_v_per_hz"] <= 0:
        raise ValueError("检测增益无效")
    return fit


def step_B05(ctx, fit):
    arrays = {key: value for key, value in fit.items() if isinstance(value, np.ndarray)}
    metadata = {key: value for key, value in fit.items() if key not in arrays}
    np.savez(ctx.results / "dispersion_source.npz", **arrays, slope_v_per_hz=fit["slope_v_per_hz"])
    write_json(ctx.results / "dispersion_source.json", metadata)
    ctx.summary["dispersion"] = metadata
    if metadata.get("condition_differences"):
        ctx.summary["warnings"].append("静磁场标定来源与本轮条件不同；允许复用，检测增益精度需实验判断。")
    popt = np.asarray(fit["popt"], float)
    plot_dispersion(ctx.results, fit["offsets_v"], fit["y_mean"],
        curve=lambda axis: _dispersive(axis, *popt), slope=fit["slope_v_per_v"], r_squared=fit["r_squared"])


def step_B06(ctx):
    run_dir, results, config, options, summary = inputs(ctx)
    known, truth_cfg = config["known_noise"], config["truth_chain"]
    saved = config.get("parameters", {})
    pass_low, pass_high = options["ratio_pass_low"], options["ratio_pass_high"]
    with np.load(results / "separated_spectra.npz", allow_pickle=False) as cached:
        f = cached["freq_axis"]
        fit_on = {key[:-3]: cached[key] for key in cached.files if key.endswith("_on")}
        fit_off = {key[:-4]: cached[key] for key in cached.files if key.endswith("_off")}
    slope_v_per_hz = summary["dispersion"]["slope_v_per_hz"]
    good = fit_on["fit_mask"] & fit_off["fit_mask"]
    background_good = fit_on["background_fit_mask"] & fit_off["background_fit_mask"]
    a_diff = fit_on["S_beta"] - fit_off["S_beta"]
    gamma = fit_on["popt"][:, 0]
    # 增益换算：静态极限连接 A/(gamma^2*sigma^2) 把 V^2 Hz 响应系数换算到 Hz^2/Hz。
    s_meas = a_diff / (gamma**2 * slope_v_per_hz**2)
    # 同一换算分别作用在 ON/OFF 总谱上，得到注入前后的可控噪声谱；
    # N_S1 是不可控噪声（含电子噪声）基线，不参与色散增益换算。
    controlled_on = fit_on["S_beta"] / (gamma**2 * slope_v_per_hz**2)
    controlled_off = fit_off["S_beta"] / (fit_off["popt"][:, 0]**2 * slope_v_per_hz**2)
    uncontrolled_on, uncontrolled_off = fit_on["N_S1"], fit_off["N_S1"]
    negative = good & (a_diff <= 0)
    # 负差仍是有效估计，不能以符号筛选造成向上偏差。
    # 本次实验线圈频响修正不适用；真值仅使用波形数值 PSD × K_Z²。
    project_root = ctx.root
    waveform_path = (run_dir / str(known.get("waveform_file", "raw/known_noise_waveform.npy"))).resolve()
    if run_dir.resolve() not in waveform_path.parents:
        raise ValueError("噪声波形路径超出运行目录")
    normalized = np.load(waveform_path, allow_pickle=False)
    amplitude_vpp = float(known["amplitude_vpp"])
    sample_rate = float(known["sample_rate_sa_s"])
    voltage_wave = normalized * (amplitude_vpp / 2.0)
    wf_nperseg = max(2, int(np.ceil(sample_rate / options["bin_width_hz"])))
    wf_nperseg = min(wf_nperseg, voltage_wave.size)
    wf_freq, wf_psd = welch(voltage_wave, fs=sample_rate, nperseg=wf_nperseg,
                            noverlap=wf_nperseg//2, window="hann",
                            detrend="constant", scaling="density")
    # 去掉直流 bin 后做对数插值，避免 log(0)。
    usable = wf_freq > 0
    wf_freq, wf_psd = wf_freq[usable], wf_psd[usable]
    calibration = truth_calibration(ctx)
    gain_chain = calibration.k_hz_per_v**2
    s_truth_lines = wf_psd * gain_chain
    s_truth = np.exp(np.interp(np.log(f), np.log(wf_freq), np.log(s_truth_lines)))
    summary["truth_chain"] = dict(
        z_calibration_source=truth_cfg.get("z_calibration_source", "calibration_run"),
        z_calibration_run=calibration.source_run,
        K_Z_Hz_per_V=calibration.k_hz_per_v,
        K_Z_uncertainty_Hz_per_V=calibration.uncertainty_hz_per_v,
        coil_frequency_response_applied=False,
        coil_frequency_response_note="按实验约定忽略线圈频率响应。",
        dispersion_slope_v_per_hz=slope_v_per_hz,
        gain_formula="S_meas = (A_on - A_off) / (gamma_on^2 * sigma^2); "
                     "S_truth = PSD(waveform*Vpp/2) * K_Z^2",
    )

    ratio = np.full(f.size, np.nan)
    ratio[good] = s_meas[good]/s_truth[good]
    # 判据只在注入谱段内评估：带外真值只是波形数值 PSD 的本底，
    # 逐 bin 比值在那里没有物理意义。
    injection_mask = _injection_band_mask(f, known.get("bands"))
    band_valid = good & (s_truth > 0) & injection_mask
    median_ratio = float(np.median(ratio[band_valid])) if band_valid.any() else float("nan")
    coverage = float(band_valid.sum()/injection_mask.sum())
    criteria_pass = bool(coverage >= .5 and pass_low <= median_ratio <= pass_high)
    summary["comparison"] = dict(
        valid_points=int(good.sum()), negative_differential_points=int(negative.sum()),
        total_points=len(f), median_ratio=median_ratio,
        pass_low=pass_low, pass_high=pass_high, criteria_pass=criteria_pass,
        in_band_valid_points=int(band_valid.sum()),
        in_band_total_points=int(injection_mask.sum()), in_band_coverage=coverage,
        minimum_criteria_coverage=.5, signed_differences_retained=True,
        out_of_band_valid_points=int((good & ~injection_mask).sum()),
        injection_band_frequency_hz=[
            [float(band["start_hz"]), float(band["stop_hz"])]
            for band in (known.get("bands") or ())
        ],
        ratio_quartiles=[float(x) for x in np.percentile(ratio[band_valid], [25, 75])]
        if band_valid.any() else None,
        controlled_on_off_median_ratio=_median_ratio(controlled_on, controlled_off, good),
        uncontrolled_on_off_median_ratio=_median_ratio(
            uncontrolled_on, uncontrolled_off, background_good),
    )
    for label, selected_band in (("in_band", injection_mask), ("out_of_band", ~injection_mask)):
        bg = background_good & selected_band
        summary["comparison"][label+"_background"] = dict(
            resolved_points=int(bg.sum()), total_points=int(selected_band.sum()),
            median_ratio=_median_ratio(uncontrolled_on, uncontrolled_off, bg),
            median_tail_holdout_error_on=float(np.median(fit_on["tail_holdout_relative_error"][bg]))
                if bg.any() else float("nan"),
        )
    if (background_good & injection_mask).sum() < .5*injection_mask.sum():
        summary["warnings"].append("注入带内不足一半频点能独立约束背景；磁响应尾部可能仍高于本底，不能判定不可控噪声是否变化。")
    if coverage < .5:
        summary["warnings"].append("注入带内有效覆盖不足 50%，不以少数通过点宣布谱验证通过。")
    summary["warnings"].append("共享分析核心独立验收峰与背景，不强制两态背景相等；模型背景不可辨识时检验两侧远端平台。popt/perr 仅描述洛伦兹拟合，平台背景以 N_S1 和 background_method 为准。")
    plot_controlled_uncontrolled(results, f, controlled_on, controlled_off, s_truth,
                                 uncontrolled_on, uncontrolled_off, good, background_good)
    np.savez(results/"noise_spectra.npz",
             freq_axis=f, S_measured_hz2_per_hz=s_meas, S_truth_hz2_per_hz=s_truth,
             ratio=ratio, A_on=fit_on["S_beta"], A_off=fit_off["S_beta"],
             A_diff=a_diff, gamma_on=gamma, gamma_off=fit_off["popt"][:, 0],
             S_beta_controlled_on_hz2_per_hz=controlled_on,
             S_beta_controlled_off_hz2_per_hz=controlled_off,
             N_S1_on=uncontrolled_on, N_S1_off=uncontrolled_off,
             background_fit_mask=background_good,
             background_fit_mask_on=fit_on["background_fit_mask"],
             background_fit_mask_off=fit_off["background_fit_mask"],
             background_method_on=fit_on["background_method"],
             background_method_off=fit_off["background_method"],
             fit_mask=good,
             negative_differential_mask=negative,
             quantitative_valid_mask=good,
             criteria_band_mask=band_valid,
             K_Z_hz_per_v=calibration.k_hz_per_v,
             dispersion_slope_v_per_hz=slope_v_per_hz)
    np.savetxt(results/"noise_spectra.csv",
               np.column_stack((f, s_meas, s_truth, ratio, good.astype(int),
                                uncontrolled_on, uncontrolled_off, background_good.astype(int))),
               delimiter=",",
               header="frequency_Hz,S_measured_Hz2_per_Hz,S_truth_Hz2_per_Hz,ratio,valid,N_on_V2_per_Hz,N_off_V2_per_Hz,background_valid",
               comments="")
    if band_valid.any():
        plot_comparison(results, f, s_meas, s_truth, band_valid, good, pass_low, pass_high)

    summary["status"] = "completed"
