"""Probe AM 两态谱计算、移动脊线、谱分离与有符号差谱；无硬件依赖。"""
import numpy as np

from ...plotting import format_axis, new_figure, save_figure
from ..noise_spectrum_xy.processing import SEPARATION_METHOD, fit_local_spectra, locate_ridge
from ..noise_spectrum_xy_known_noise.analysis_core import (
    _reason_counts,
    calibration_band,
    compute_psd_matrix,
    plot_controlled_uncontrolled,
    plot_line_shapes,
    plot_two_dimensional,
    save_known_noise_preview,
    select_line_shape_columns,
)


def inputs(ctx):
    run_dir, results, config, options, summary = ctx.run_dir, ctx.results, ctx.config, ctx.options, ctx.summary
    return run_dir, results, config, options, summary


def spectral_data(ctx):
    with np.load(ctx.results / "psd_matrices.npz", allow_pickle=False) as data:
        psd_on, psd_off, frequency, voltage, rows = (data[key] for key in
            ("psd_on", "psd_off", "freq_axis", "amplitudes", "paired_rows"))
    band = (frequency > 0) & (frequency >= ctx.options["frequency_min_hz"]) & (frequency <= ctx.options["frequency_max_hz"])
    if band.sum() < 2:
        raise ValueError("分析频带内点数不足")
    return psd_on, psd_off, frequency, voltage, rows, band


def _load_ridge(ctx):
    with np.load(ctx.results / "calibration.npz", allow_pickle=False) as cached:
        return dict(cached)


def _plot_signed_difference(results, f, n_off, delta, valid, controlled_off, delta_controlled, controlled_valid):
    """不可控背景与受控响应的 OFF 基线和有符号 ON−OFF 差谱（双 Y 轴）。"""
    fig, axes = new_figure(kind="wide", height_mm=110, nrows=2, constrained_layout=True)
    panels = ((axes[0], n_off, delta, valid,
               r"$N_{S_1}$ OFF (V$^2$/Hz)", r"$\Delta N_{S_1}$ (V$^2$/Hz)",
               "Uncontrolled background: OFF baseline and signed difference"),
              (axes[1], controlled_off, delta_controlled, controlled_valid,
               r"$S_\beta$ OFF (V$^2$·Hz)", r"$\Delta S_\beta$ (V$^2$·Hz)",
               "Controlled response: OFF baseline and signed difference"))
    for ax, baseline, difference, mask, baseline_label, delta_label, title in panels:
        difference_axis = ax.twinx()
        ax.semilogy(f[mask], baseline[mask], ".", ms=2.5, color="tab:blue", label="Noise OFF baseline")
        difference_axis.plot(f[mask], difference[mask], ".", ms=2.5,
                             color="tab:orange", label="Signed ON - OFF")
        difference_axis.axhline(0, color="black", lw=0.8)
        format_axis(ax, xlabel="Frequency (Hz)", ylabel=baseline_label)
        difference_axis.set_ylabel(delta_label)
        handles, labels = ax.get_legend_handles_labels()
        right_handles, right_labels = difference_axis.get_legend_handles_labels()
        ax.legend(handles + right_handles, labels + right_labels, loc="best")
        ax.set_title(title)
    save_figure(fig, results / "signed_spectral_difference.png")


def step_B01(ctx):
    if not ctx.config.get("probe_am_noise"):
        raise ValueError("运行配置缺少 probe_am_noise")
    if not list((ctx.run_dir / "raw").glob("waveform_C*_*.npy")):
        raise ValueError("运行目录没有成对采集原始波形")


def step_B02(ctx):
    run_dir, results, config, options, summary = inputs(ctx)
    psd_on, frequency, voltage, rows_on, psd_info = compute_psd_matrix(run_dir, config, options, "on")
    psd_off, frequency_off, voltage_off, rows_off, _ = compute_psd_matrix(run_dir, config, options, "off")
    if (frequency.shape != frequency_off.shape or not np.allclose(frequency, frequency_off)
            or voltage.shape != voltage_off.shape or not np.allclose(voltage, voltage_off)):
        raise ValueError("off/on 实际采样率或控制轴不一致，不能相减 PSD")
    rows = np.intersect1d(rows_on, rows_off)
    if rows.size < 20:
        raise ValueError(f"ON/OFF 成对有效控制点不足 20 个（{rows.size}）")
    average = 0.5 * (psd_on + psd_off)
    np.savez_compressed(results / "psd_matrices.npz", psd_on=psd_on, psd_off=psd_off,
                        average_psd=average, freq_axis=frequency, amplitudes=voltage, paired_rows=rows,
                        actual_rate_sa_s=psd_info["actual_rate_sa_s"], nperseg=psd_info["nperseg"])
    # 实际任意波 Welch PSD 与设计谱参考；不换算为绝对光功率谱。
    save_known_noise_preview(results, run_dir, config["probe_am_noise"], options["bin_width_hz"])
    summary["psd"] = {**psd_info, "paired_points": int(rows.size)}
    print(f"Welch: {rows.size}/{voltage.size} 点成对, nperseg={psd_info['nperseg']}, "
          f"df={psd_info['bin_width_hz']:.3f} Hz", flush=True)


def step_B03(ctx):
    run_dir, results, config, options, summary = inputs(ctx)
    saved = config.get("parameters", {})
    psd_on, psd_off, frequency, voltage, rows, band = spectral_data(ctx)
    average = 0.5 * (psd_on + psd_off)
    f, v = frequency[band], voltage[rows]
    ridge_band = calibration_band(frequency, options, saved)
    try:
        ridge = locate_ridge(psd_off[rows][:, ridge_band], frequency[ridge_band], v)
    except ValueError as exc:
        if str(exc) != "无足够显著的移动峰，拒绝生成控制轴":
            raise
        k = float(saved["XY_CTRL_K_HZ_PER_V"])
        b = float(saved["XY_CTRL_B_HZ"])
        if not np.isfinite(k) or k <= 0 or not np.isfinite(b):
            raise ValueError("移动峰不足，且运行参数中的正弦控制 K/B 无效") from exc
        omega_ctrl = k * voltage[rows] + b
        if not np.all(np.isfinite(omega_ctrl)) or np.any(np.diff(omega_ctrl) <= 0):
            raise ValueError("移动峰不足，且运行参数中的 K/B 未生成严格递增控制轴") from exc
        ridge = {"k": k, "b": b, "omega_ctrl": omega_ctrl,
                 "spur_mask": np.zeros(np.count_nonzero(ridge_band), dtype=bool),
                 "V_cal": voltage[rows], "provisional": True}
        summary.setdefault("warnings", []).append(
            "未检测到足够显著的移动峰；本次按运行参数 K/B 推算峰位，图和拟合仅供暂时分析。")
    np.savez(results / "calibration.npz", **ridge, amplitudes=v, freq_axis=frequency[ridge_band])
    plot_two_dimensional(results, average[rows][:, band], f, v, ridge,
                         "noise_spectrum_2d.png",
                         "Average ON/OFF PSD (provisional K/B axis)" if ridge.get("provisional") else "Average ON/OFF PSD",
                         r"$\log_{10}$ PSD (V$^2$/Hz)")
    summary["calibration"] = {"k": float(ridge["k"]), "b": float(ridge["b"]),
                              "support_points": int(len(ridge["V_cal"])),
                              "source": "configured_K_B" if ridge.get("provisional") else "moving_peaks"}
    print("移动峰标定:", summary["calibration"], flush=True)


def step_B04(ctx):
    run_dir, results, config, options, summary = inputs(ctx)
    saved = config.get("parameters", {})
    psd_on, psd_off, frequency, voltage, rows, band = spectral_data(ctx)
    f, v = frequency[band], voltage[rows]
    psd_on_band, psd_off_band = psd_on[rows][:, band], psd_off[rows][:, band]
    ridge = _load_ridge(ctx)
    ridge_band = calibration_band(frequency, options, saved)
    spur_mask = ridge["spur_mask"][band[ridge_band]]
    support = (float(np.min(ridge["k"] * ridge["V_cal"] + ridge["b"])),
               float(np.max(ridge["k"] * ridge["V_cal"] + ridge["b"])))
    fit_on = fit_local_spectra(psd_on_band, f, ridge["omega_ctrl"], spur_mask,
                               options["fit_half_width_hz"], support, full_range=True)
    fit_off = fit_local_spectra(psd_off_band, f, ridge["omega_ctrl"], spur_mask,
                                options["fit_half_width_hz"], support, full_range=True)
    # 共享分析核心分别验收峰和背景；峰失败时允许独立的远端平台估计。
    controlled_valid = fit_on["fit_mask"] & fit_off["fit_mask"]
    valid = fit_on["background_fit_mask"] & fit_off["background_fit_mask"]
    n_on, n_off = fit_on["N_S1"], fit_off["N_S1"]
    delta = np.where(valid, n_on - n_off, np.nan)
    controlled_on, controlled_off = fit_on["S_beta"], fit_off["S_beta"]
    delta_controlled = np.where(controlled_valid, controlled_on - controlled_off, np.nan)
    plot_controlled_uncontrolled(
        results, f, controlled_on, controlled_off, None, n_on, n_off,
        controlled_valid, valid,
        controlled_ylabel=r"$S_\beta$ response coefficient (V$^2$·Hz)")
    plot_line_shapes(
        results, f, ridge["omega_ctrl"], psd_on_band, psd_off_band,
        fit_on["popt"], fit_off["popt"],
        fit_on["rejection_reason"], fit_off["rejection_reason"],
        select_line_shape_columns(f, valid | controlled_valid, 8),
        options["fit_half_width_hz"], ridge["k"], ridge["b"],
        background_on=n_on, background_off=n_off,
    )
    np.savez_compressed(
        results / "popt_fit.npz", freq_axis=f,
        **{f"{key}_{state}": value for state, fit in (("on", fit_on), ("off", fit_off))
           for key, value in fit.items() if key != "model_matrix"},
    )
    np.savez_compressed(
        results / "noise_spectra.npz", freq_axis=f, N_S1_on=n_on, N_S1_off=n_off,
        delta_N_S1=delta, valid_mask=valid,
        S_beta_on=controlled_on, S_beta_off=controlled_off,
        delta_S_beta=delta_controlled, controlled_valid_mask=controlled_valid,
        valid_mask_on=fit_on["background_fit_mask"],
        valid_mask_off=fit_off["background_fit_mask"],
        controlled_valid_mask_on=fit_on["fit_mask"],
        controlled_valid_mask_off=fit_off["fit_mask"],
        controlled_rejection_reason_on=fit_on["rejection_reason"],
        controlled_rejection_reason_off=fit_off["rejection_reason"],
        background_method_on=fit_on["background_method"],
        background_method_off=fit_off["background_method"],
        rejection_reason_on=fit_on["background_rejection_reason"],
        rejection_reason_off=fit_off["background_rejection_reason"],
        popt_on=fit_on["popt"], popt_off=fit_off["popt"],
        ridge_k=ridge["k"], ridge_b=ridge["b"], control_frequency=ridge["omega_ctrl"],
    )
    np.savetxt(results / "noise_spectra.csv",
               np.column_stack((f, controlled_on, controlled_off, delta_controlled,
                                n_on, n_off, delta, controlled_valid.astype(int),
                                valid.astype(int))), delimiter=",",
               header="frequency_Hz,S_beta_on_V2_Hz,S_beta_off_V2_Hz,delta_S_beta_V2_Hz,"
                      "N_S1_on_V2_per_Hz,N_S1_off_V2_per_Hz,delta_N_S1_V2_per_Hz,controlled_valid,background_valid",
               comments="")
    _plot_signed_difference(results, f, n_off, delta, valid,
                            controlled_off, delta_controlled, controlled_valid)
    summary.update(interpretation="Lock-in measurement-end noise spectrum; not calibrated to optical power.")
    summary["fit_quality"] = {
        "valid_background_bins": int(valid.sum()), "total_bins": int(f.size),
        "valid_controlled_bins": int(controlled_valid.sum()),
        "accepted_on": int(fit_on["background_fit_mask"].sum()),
        "accepted_off": int(fit_off["background_fit_mask"].sum()),
        "background_method_on": _reason_counts(fit_on["background_method"]),
        "background_method_off": _reason_counts(fit_off["background_method"]),
        "controlled_rejection_on": _reason_counts(fit_on["rejection_reason"]),
        "controlled_rejection_off": _reason_counts(fit_off["rejection_reason"]),
        "rejection_on": _reason_counts(fit_on["background_rejection_reason"]),
        "rejection_off": _reason_counts(fit_off["background_rejection_reason"]),
        "negative_delta_bins": int(np.sum(valid & (delta < 0))),
        "positive_delta_bins": int(np.sum(valid & (delta > 0))),
        "negative_controlled_delta_bins": int(np.sum(controlled_valid & (delta_controlled < 0))),
        "positive_controlled_delta_bins": int(np.sum(controlled_valid & (delta_controlled > 0))),
    }
    summary.setdefault("warnings", []).extend([
        "不可控背景独立验收；峰未通过时仍可由两侧稳定远端平台提取 N_S1。"
        "受控响应仅展示各态通过的部分，差谱只使用对应两态共同有效点。"
        "popt/perr 仅描述洛伦兹拟合，平台背景以 N_S1 和 background_method 为准。",
        "方法：" + SEPARATION_METHOD,
    ])
    summary["warnings"] = list(dict.fromkeys(summary["warnings"]))
    summary["signed_differences_retained"] = True
    print("分析完成:", summary["fit_quality"], flush=True)
