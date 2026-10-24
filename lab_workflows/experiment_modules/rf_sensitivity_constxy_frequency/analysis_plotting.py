"""保留原十二组响应、拟合残差、比值、相位及典型谱图。"""
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from ...plotting import new_figure, save_figure, set_plot_style
from .analysis_algorithms import _windowed_fft_full

def render(responses, fits, config, params, point_data, results_dir):
    set_plot_style("paper")
    acquisition_mode = params.acquisition_mode
    LARMOR_FREQ_HZ = config.get("xy_const_field", {}).get("LARMOR_FREQ_Hz")
    signal_pts = [p for p in point_data if not p["is_baseline"]]
    DAQ_RESPONSE_METHOD = params.daq_response_method
    FIT_MODEL = params.fit_model
    LARMOR_FREQ_HZ = params.larmor_freq_hz
    MAX_PICK_FREQ_ERROR_HZ = params.max_pick_freq_error_hz
    Z_RF_AMPLITUDE = params.z_rf_amplitude
    records_daq = responses['records_daq']
    baseline_amp = responses['baseline_amp']
    freqs_daq = responses['freqs_daq']
    amps_raw = responses['amps_raw']
    amps_corr = responses['amps_corr']
    freq_errors = responses['freq_errors']
    freqs_dR = responses['freqs_dR']
    R_vec = responses['R_vec']
    R_scalar = responses['R_scalar']
    phase_vec_deg = responses['phase_vec_deg']
    freqs_li = responses['freqs_li']
    li_r = responses['li_r']
    li_phase_deg = responses['li_phase_deg']
    li_valid = responses['li_valid']
    is_susp_daq = fits['is_susp_daq']
    bandwidth_daq_corr = fits['bandwidth_daq_corr']
    bandwidth_daq_fft = fits['bandwidth_daq_fft']
    bandwidth_offline_lockin = fits['bandwidth_offline_lockin']
    bandwidth_hardware_demod_r_vector = fits['bandwidth_hardware_demod_r_vector']
    lorentzian_daq_fft = fits['lorentzian_daq_fft']
    lorentzian_offline_lockin = fits['lorentzian_offline_lockin']
    lorentzian_hardware_demod_r = fits['lorentzian_hardware_demod_r']
    doublelorentzian_daq_fft = fits['doublelorentzian_daq_fft']
    doublelorentzian_offline_lockin = fits['doublelorentzian_offline_lockin']
    doublelorentzian_hardware_demod_r = fits['doublelorentzian_hardware_demod_r']
    ratio_fft_li = fits['ratio_fft_li']
    ratio_hw_li = fits['ratio_hw_li']
    common_freqs = fits['common_freqs']
    common_hw = fits['common_hw']
    stat_fft_li = fits['stat_fft_li']
    stat_hw_li = fits['stat_hw_li']
    Z_RF_AMPLITUDE = (config.get("freq_sweep", {})
                      .get("Z_RF_AMPLITUDE_Vpp", 0.01))

    # ---- 图 1: daq_fft_y 幅频特性 ----
    if acquisition_mode in ("daq_fft_y", "both") and len(freqs_daq) > 0:
        fig1, ax1 = new_figure(kind="wide", height_mm=65)

        # suspicious 点单独标记
        susp_mask = is_susp_daq
        if susp_mask is not None and np.any(susp_mask):
            nonsusp = ~susp_mask
        else:
            nonsusp = np.ones_like(freqs_daq, dtype=bool)

        ax1.semilogy(freqs_daq[nonsusp], amps_raw[nonsusp], "o-",
                     color="C0", lw=1.0, ms=4,
                     label="Raw amplitude (drive bin)")
        if np.any(~nonsusp):
            ax1.semilogy(freqs_daq[~nonsusp], amps_raw[~nonsusp], "x",
                         color="C3", ms=8, mew=2,
                         label=f"Suspicious (n={int(np.sum(~nonsusp))})")
        ax1.semilogy(freqs_daq[nonsusp], amps_corr[nonsusp], "s-",
                     color="C1", lw=1.0, ms=4, alpha=0.85,
                     label="Background-corrected")
        if np.isfinite(baseline_amp):
            ax1.axhline(baseline_amp, color="gray", ls=":", lw=0.8,
                        label=f"Baseline median = {baseline_amp:.2e} V")

        if bandwidth_daq_corr["peak_freq_Hz"] is not None:
            ax1.axvline(bandwidth_daq_corr["peak_freq_Hz"], color="red",
                        ls="--", lw=0.8, alpha=0.6,
                        label=f"Peak: {bandwidth_daq_corr['peak_freq_Hz']:.1f} Hz")
        if bandwidth_daq_corr["f_low_Hz"] is not None:
            ax1.axvline(bandwidth_daq_corr["f_low_Hz"], color="C2",
                        ls=":", lw=0.8, alpha=0.7)
            ax1.axvline(bandwidth_daq_corr["f_high_Hz"], color="C2",
                        ls=":", lw=0.8, alpha=0.7,
                        label=f"-3dB: {bandwidth_daq_corr['f_low_Hz']:.1f}-"
                              f"{bandwidth_daq_corr['f_high_Hz']:.1f} Hz")
        if bandwidth_daq_corr["threshold"] is not None:
            ax1.axhline(bandwidth_daq_corr["threshold"], color="C2", ls=":",
                        lw=0.6, alpha=0.5,
                        label=f"-3dB threshold = {bandwidth_daq_corr['threshold']:.2e} V")

        ax1.set_xlabel("Z RF field drive frequency (Hz)")
        ax1.set_ylabel("Amplitude at drive bin (V)")
        title = (f"ConstXY RF Field Frequency Response — daq_fft_y "
                 f"({DAQ_RESPONSE_METHOD}, Z_drive = {Z_RF_AMPLITUDE} Vpp")
        if LARMOR_FREQ_HZ is not None:
            title += f", Larmor = {LARMOR_FREQ_HZ} Hz"
        title += ")"
        ax1.set_title(title)
        ax1.legend(fontsize=8, loc="best")
        ax1.grid(False)

        plt.gcf().set_layout_engine("constrained")
        save_figure(fig1, results_dir / "frequency_response.png", close=False)
        print(f"图已保存: {results_dir / 'frequency_response.png'}")
        plt.close("all")
    else:
        print("⚠ 跳过 daq_fft_y 单图 (无数据)")

    # ---- 图 2: 三组响应归一化对比 (normalized) ----
    have_daq = (acquisition_mode in ("daq_fft_y", "both")
                and len(freqs_daq) > 0)
    have_dR = (acquisition_mode in ("demod_r", "both")
               and len(freqs_dR) > 0)
    have_li = (len(freqs_li) > 0 and np.any(li_valid))
    if have_daq or have_dR or have_li:
        def _safe_norm(a):
            m = np.nanmax(a) if np.any(np.isfinite(a)) else 0
            return a / m if m > 0 else a

        fig2, ax2 = new_figure(kind="wide", height_mm=65)
        n_curves = 0
        if have_daq:
            nonsusp = (~is_susp_daq if is_susp_daq is not None
                       else np.ones_like(freqs_daq, bool))
            norm_daq = _safe_norm(amps_corr)
            ax2.plot(freqs_daq[nonsusp], norm_daq[nonsusp], "o-",
                     color="C0", lw=1.2, ms=4,
                     label="direct FFT (y_V → nearest bin)")
            if np.any(~nonsusp):
                ax2.plot(freqs_daq[~nonsusp], norm_daq[~nonsusp], "x",
                         color="C0", ms=8, mew=2, alpha=0.5,
                         label=f"FFT suspicious (n={int(np.sum(~nonsusp))})")
            n_curves += 1
        if have_li:
            valid_li = li_valid & np.isfinite(li_r)
            norm_li = _safe_norm(li_r)
            ax2.plot(freqs_li[valid_li], norm_li[valid_li], "^-",
                     color="C2", lw=1.2, ms=4,
                     label="offline lock-in (y_V → software demod)")
            if np.any(~valid_li):
                ax2.plot(freqs_li[~valid_li], norm_li[~valid_li], "x",
                         color="C2", ms=8, mew=2, alpha=0.5,
                         label=f"LI invalid (n={int(np.sum(~valid_li))})")
            n_curves += 1
        if have_dR:
            norm_dR = _safe_norm(R_vec)
            ax2.plot(freqs_dR, norm_dR, "s-", color="C3", lw=1.2, ms=4,
                     label="hardware demod_r (vector mean)")
            n_curves += 1
        if n_curves == 0:
            print("⚠ 跳过归一化对比图 (无可用响应)")

        # ---- 拟合曲线 (在归一化坐标上叠加): 优先双 Lorentzian, 回落到单 Lorentzian ----
        def _pick_fit(single, double):
            if double is not None and double.get("success"):
                return double, "double"
            if single is not None and single.get("success"):
                return single, "single"
            return None, None

        fit_pairs = [
            (have_daq, "C0",
             _pick_fit(lorentzian_daq_fft, doublelorentzian_daq_fft),
             amps_corr if have_daq else None, "FFT"),
            (have_li,  "C2",
             _pick_fit(lorentzian_offline_lockin, doublelorentzian_offline_lockin),
             li_r if have_li else None, "LI"),
            (have_dR,  "C3",
             _pick_fit(lorentzian_hardware_demod_r, doublelorentzian_hardware_demod_r),
             R_vec if have_dR else None, "HW"),
        ]
        for present, color, picked, resp, tag in fit_pairs:
            if not present:
                continue
            fit, kind = picked
            if fit is None:
                continue
            fgrid = fit["fit_curve_freqs"]
            fit_vals = fit["fit_curve_values"]
            # 归一化到与对应数据相同的 peak
            if resp is None or not np.any(np.isfinite(resp)):
                continue
            peak_data = float(np.nanmax(resp))
            peak_fit = float(np.nanmax(fit_vals))
            if peak_fit <= 0 or peak_data <= 0:
                continue
            norm_fit = fit_vals * (peak_data / peak_fit)  # 与数据曲线对齐 peak
            if kind == "double":
                lbl = (f"Double-Lorentzian fit ({tag}): "
                       f"Ω={fit['Omega_Ctrl_Hz']:.1f}±{fit['Omega_unc_Hz']:.1f} Hz, "
                       f"Γ={fit['Gamma_Hz']:.2f}±{fit['Gamma_unc_Hz']:.2f} Hz")
            else:
                lbl = (f"Single-Lorentzian fit ({tag}): "
                       f"f0={fit['f0_Hz']:.1f} Hz, "
                       f"FWHM={fit['fwhm_Hz']:.1f}±{fit['fwhm_unc_Hz']:.1f} Hz")
            ax2.plot(fgrid, _safe_norm(norm_fit), "--", color=color,
                     lw=1.0, alpha=0.7, label=lbl)

        # peak 标注
        for bw, color, tag in [
            (bandwidth_daq_fft, "C0", "FFT"),
            (bandwidth_offline_lockin, "C2", "LI"),
            (bandwidth_hardware_demod_r_vector, "C3", "HW vec"),
        ]:
            if bw["peak_freq_Hz"] is not None:
                ax2.axvline(bw["peak_freq_Hz"], color=color,
                            ls="--", lw=0.7, alpha=0.5,
                            label=f"Peak ({tag}): {bw['peak_freq_Hz']:.1f} Hz")

        ax2.set_xlabel("Z RF field drive frequency (Hz)")
        ax2.set_ylabel("Normalized response (a.u.)")
        title_main = ("ConstXY Frequency Response Comparison — "
                      "direct FFT vs offline lock-in vs hardware demod_r")
        if FIT_MODEL == "double_lorentzian":
            title_main += "\n(fits: dispersive+Lorentzian Bloch steady-state model)"
        ax2.set_title(title_main)
        ax2.legend(fontsize=7, loc="best")
        ax2.grid(False)

        plt.gcf().set_layout_engine("constrained")
        save_figure(fig2, results_dir / "frequency_response_comparison.png", close=False)
        print(f"图已保存: {results_dir / 'frequency_response_comparison.png'}")
        plt.close("all")
    else:
        print("⚠ 跳过归一化对比图 (无数据)")

    # ---- 图 2b: 三组响应绝对幅值 (log scale) ----
    if have_daq or have_dR or have_li:
        fig2b, ax2b = new_figure(kind="wide", height_mm=65)
        if have_daq:
            ax2b.semilogy(freqs_daq, amps_corr, "o-", color="C0",
                          lw=1.0, ms=4, label="direct FFT")
        if have_li:
            valid_li = li_valid & np.isfinite(li_r)
            ax2b.semilogy(freqs_li[valid_li], li_r[valid_li], "^-",
                          color="C2", lw=1.0, ms=4, label="offline lock-in")
        if have_dR:
            ax2b.semilogy(freqs_dR, R_vec, "s-", color="C3",
                          lw=1.0, ms=4, label="hardware demod_r (vector)")
        if np.isfinite(baseline_amp):
            ax2b.axhline(baseline_amp, color="gray", ls=":", lw=0.8,
                         label=f"Baseline median = {baseline_amp:.2e} V")
        ax2b.set_xlabel("Z RF field drive frequency (Hz)")
        ax2b.set_ylabel("Amplitude (V, log scale)")
        ax2b.set_title("ConstXY Frequency Response — Absolute Amplitude Comparison")
        ax2b.legend(fontsize=8, loc="best")
        ax2b.grid(False)
        plt.gcf().set_layout_engine("constrained")
        save_figure(fig2b, results_dir / "frequency_response_absolute.png", close=False)
        print(f"图已保存: {results_dir / 'frequency_response_absolute.png'}")
        plt.close("all")
    else:
        print("⚠ 跳过绝对幅值对比图 (无数据)")

    # ---- 图 2b-LT: Lorentzian 拟合 + 残差 (三组响应, 各一个 subplot) ----
    # 优先使用双 Lorentzian 拟合 (物理模型), 否则回落到单 Lorentzian
    def _pick_for_panel(single, double):
        if double is not None and double.get("success"):
            return double, "double"
        if single is not None and single.get("success"):
            return single, "single"
        return None, None


    panels_lt = []
    if have_daq:
        fit_pick, kind_pick = _pick_for_panel(lorentzian_daq_fft,
                                               doublelorentzian_daq_fft)
        if fit_pick is not None:
            panels_lt.append(("direct FFT", freqs_daq, amps_corr,
                               is_susp_daq, fit_pick, kind_pick, "C0"))
    if have_li:
        fit_pick, kind_pick = _pick_for_panel(lorentzian_offline_lockin,
                                               doublelorentzian_offline_lockin)
        if fit_pick is not None:
            valid_li_mask = li_valid & np.isfinite(li_r)
            panels_lt.append(("offline lock-in", freqs_li, li_r,
                               ~valid_li_mask, fit_pick, kind_pick, "C2"))
    if have_dR:
        fit_pick, kind_pick = _pick_for_panel(lorentzian_hardware_demod_r,
                                               doublelorentzian_hardware_demod_r)
        if fit_pick is not None:
            panels_lt.append(("hardware demod_r",
                               freqs_dR, R_vec, None,
                               fit_pick, kind_pick, "C3"))

    if panels_lt:
        fig_lf, axes_lf = new_figure(nrows=len(panels_lt), ncols=2, kind="wide", height_mm=max(65, 55 * (len(panels_lt))), gridspec_kw={"width_ratios": [3, 1]}, squeeze=False)
        for ax_row, (tag, freqs, resp, susp, fit, kind, color) in zip(
                axes_lf, panels_lt):
            ax_fit, ax_res = ax_row
            # 用与 -3 dB 同样的 valid+suspicious 过滤
            valid = np.isfinite(freqs) & np.isfinite(resp)
            if susp is not None and len(susp) == len(freqs):
                valid = valid & ~np.asarray(susp, dtype=bool)
            f_v = np.asarray(freqs, dtype=float)[valid]
            r_v = np.asarray(resp, dtype=float)[valid]
            # 拟合曲线
            f_fit = fit["fit_curve_freqs"]
            r_fit = fit["fit_curve_values"]
            # 数据曲线
            ax_fit.plot(f_v, r_v, "o", color=color, ms=4, label=f"{tag} data")
            if kind == "double":
                ax_fit.plot(f_fit, r_fit, "-", color=color, lw=1.4,
                            label=(f"Double-Lorentzian fit:\n"
                                   f"  Ω_Ctrl={fit['Omega_Ctrl_Hz']:.1f}"
                                   f"±{fit['Omega_unc_Hz']:.1f} Hz\n"
                                   f"  Γ={fit['Gamma_Hz']:.2f}"
                                   f"±{fit['Gamma_unc_Hz']:.2f} Hz\n"
                                   f"  C=({fit['offset_V']:.2e}"
                                   f"±{fit['offset_unc_V']:.1e}) V"))
                ax_fit.axvline(fit["Omega_Ctrl_Hz"], color=color,
                               ls="--", lw=0.7, alpha=0.5,
                               label=f"Ω_Ctrl={fit['Omega_Ctrl_Hz']:.1f} Hz")
                if LARMOR_FREQ_HZ is not None:
                    ax_fit.axvline(LARMOR_FREQ_HZ, color="magenta", ls=":",
                                   lw=0.6, alpha=0.5,
                                   label=f"LARMOR set={LARMOR_FREQ_HZ:.1f} Hz")
            else:
                ax_fit.plot(f_fit, r_fit, "-", color=color, lw=1.4,
                            label=(f"Lorentzian fit: "
                                   f"f0={fit['f0_Hz']:.1f}±{fit['f0_unc_Hz']:.1f} Hz, "
                                   f"FWHM={fit['fwhm_Hz']:.1f}±"
                                   f"{fit['fwhm_unc_Hz']:.1f} Hz"))
                threshold = fit["A"] / np.sqrt(2.0)
                ax_fit.axhline(threshold, color="gray", ls=":", lw=0.6,
                               label=f"half max = {threshold:.3e}")
                ax_fit.axvline(fit["f0_Hz"], color=color,
                               ls="--", lw=0.7, alpha=0.5)
                ax_fit.axvline(fit["f0_Hz"] - fit["fwhm_Hz"] / 2,
                               color=color, ls=":", lw=0.6, alpha=0.4)
                ax_fit.axvline(fit["f0_Hz"] + fit["fwhm_Hz"] / 2,
                               color=color, ls=":", lw=0.6, alpha=0.4,
                               label=f"±FWHM/2 = {fit['fwhm_Hz']/2:.1f} Hz")
            ax_fit.set_ylabel("Amplitude (V)")
            ax_fit.set_title(f"{tag} — {kind}-Lorentzian fit "
                             f"(residual std={fit['residual_std']:.2e})")
            ax_fit.legend(fontsize=7, loc="best")
            ax_fit.grid(False)

            # 残差
            if kind == "double":
                num = np.sqrt(fit["Gamma_Hz"] ** 2 + f_v ** 2)
                d1 = fit["Gamma_Hz"] ** 2 - f_v ** 2 + fit["Omega_Ctrl_Hz"] ** 2
                d2 = 4.0 * fit["Gamma_Hz"] ** 2 * f_v ** 2
                den = np.sqrt(d1 * d1 + d2)
                r_model = fit["offset_V"] + fit["P0"] * num / np.maximum(den, 1e-12)
            else:
                r_model = (fit["A"] * fit["hwhm_Hz"] ** 2
                           / ((f_v - fit["f0_Hz"]) ** 2 + fit["hwhm_Hz"] ** 2))
            residual = r_v - r_model
            ax_res.axhline(0, color="black", lw=0.8)
            ax_res.axhline(fit["residual_std"], color="gray", ls=":", lw=0.5,
                           alpha=0.6)
            ax_res.axhline(-fit["residual_std"], color="gray", ls=":", lw=0.5,
                           alpha=0.6)
            ax_res.plot(f_v, residual, "o", color=color, ms=3)
            ax_res.set_ylabel("residual")
            ax_res.grid(False)
            ax_res.set_xlabel("Frequency (Hz)")

        plt.gcf().set_layout_engine("constrained")
        save_figure(fig_lf, results_dir / "frequency_response_fits.png", close=False)
        print(f"图已保存: {results_dir / 'frequency_response_fits.png'}")
        plt.close("all")
    else:
        print("⚠ 跳过 Lorentzian 拟合图（无拟合成功的曲线）")

    # ---- 图 2c: direct FFT / offline lock-in 比值 ----
    if len(ratio_fft_li) > 0 and len(common_freqs) > 0:
        valid = np.isfinite(ratio_fft_li) & (ratio_fft_li > 0)
        fig2c, ax2c = new_figure(kind="wide", height_mm=65)
        ax2c.axhline(1.0, color="black", lw=0.8)
        if stat_fft_li is not None:
            ax2c.axhline(stat_fft_li["median"], color="C0", ls="--", lw=0.8,
                         label=f"median = {stat_fft_li['median']:.4f}")
        ax2c.plot(common_freqs[valid], ratio_fft_li[valid], "o-",
                  color="C0", lw=1.0, ms=4, label="direct FFT / offline lock-in")
        ax2c.set_xlabel("Z RF field drive frequency (Hz)")
        ax2c.set_ylabel("FFT amplitude / lock-in amplitude")
        ax2c.set_title("ConstXY: Direct FFT vs Offline Lock-in — Amplitude Ratio")
        ax2c.legend(fontsize=8, loc="best")
        ax2c.grid(False)
        plt.gcf().set_layout_engine("constrained")
        save_figure(fig2c, results_dir / "fft_vs_offline_lockin_ratio.png", close=False)
        print(f"图已保存: {results_dir / 'fft_vs_offline_lockin_ratio.png'}")
        plt.close("all")
    else:
        print("⚠ 跳过 FFT / lock-in 比值图 (无交集)")

    # ---- 图 2d: hardware demod_r / offline lock-in 比值 ----
    if len(ratio_hw_li) > 0 and len(common_hw) > 0:
        valid = np.isfinite(ratio_hw_li) & (ratio_hw_li > 0)
        fig2d, ax2d = new_figure(kind="wide", height_mm=65)
        ax2d.axhline(1.0, color="black", lw=0.8)
        if stat_hw_li is not None:
            ax2d.axhline(stat_hw_li["median"], color="C3", ls="--", lw=0.8,
                         label=f"median = {stat_hw_li['median']:.4f}")
        ax2d.plot(common_hw[valid], ratio_hw_li[valid], "s-",
                  color="C3", lw=1.0, ms=4, label="hardware demod_r / offline lock-in")
        ax2d.set_xlabel("Z RF field drive frequency (Hz)")
        ax2d.set_ylabel("hardware demod_r / lock-in amplitude")
        ax2d.set_title("ConstXY: Hardware Demod R vs Offline Lock-in — Amplitude Ratio")
        ax2d.legend(fontsize=8, loc="best")
        ax2d.grid(False)
        plt.gcf().set_layout_engine("constrained")
        save_figure(fig2d, results_dir / "hardware_vs_offline_lockin_ratio.png", close=False)
        print(f"图已保存: {results_dir / 'hardware_vs_offline_lockin_ratio.png'}")
        plt.close("all")
    else:
        print("⚠ 跳过 hardware / lock-in 比值图 (无交集)")

    # ---- 图 2e: offline lock-in 相位 ----
    if len(freqs_li) > 0 and np.any(li_valid):
        valid = li_valid & np.isfinite(li_phase_deg)
        fig2e, (ax2e1, ax2e2) = new_figure(nrows=2, ncols=1, kind="wide", height_mm=110, sharex=True)
        ax2e1.plot(freqs_li[valid], li_r[valid], "^-", color="C2",
                   lw=1.0, ms=4, label="offline lock-in R")
        if np.isfinite(baseline_amp):
            ax2e1.axhline(baseline_amp, color="gray", ls=":", lw=0.7,
                          label=f"Baseline = {baseline_amp:.2e} V")
        ax2e1.set_ylabel("R (V)")
        ax2e1.set_title("ConstXY: Offline Lock-in — Magnitude & Phase vs Drive Frequency")
        ax2e1.legend(fontsize=8, loc="best")
        ax2e1.grid(False)
        ax2e1.set_yscale("log")

        ax2e2.plot(freqs_li[valid], li_phase_deg[valid], "^-", color="C2",
                   lw=1.0, ms=4, label="offline lock-in phase")
        if np.any(~li_valid):
            ax2e2.plot(freqs_li[~li_valid], li_phase_deg[~li_valid], "x",
                       color="gray", ms=6, mew=1.5, alpha=0.5,
                       label=f"invalid (n={int(np.sum(~li_valid))})")
        ax2e2.set_xlabel("Z RF field drive frequency (Hz)")
        ax2e2.set_ylabel("Phase (deg)")
        ax2e2.legend(fontsize=8, loc="best")
        ax2e2.grid(False)

        plt.gcf().set_layout_engine("constrained")
        save_figure(fig2e, results_dir / "offline_lockin_phase.png", close=False)
        print(f"图已保存: {results_dir / 'offline_lockin_phase.png'}")
        plt.close("all")
    else:
        print("⚠ 跳过 offline lock-in 相位图 (无数据)")

    # ---- 图 2f: hardware vector vs scalar ----
    if have_dR and len(R_vec) > 0 and len(R_scalar) > 0:
        fig2f, ax2f = new_figure(kind="wide", height_mm=65)
        ax2f.semilogy(freqs_dR, R_vec, "s-", color="C3",
                      lw=1.2, ms=4, label="R_vec = sqrt(<X>² + <Y>²)")
        ax2f.semilogy(freqs_dR, R_scalar, "o-", color="C4",
                      lw=1.0, ms=4, alpha=0.7, label="R_scalar = <|Z|>")
        ax2f.set_xlabel("Z RF field drive frequency (Hz)")
        ax2f.set_ylabel("Hardware Demod R (V, log scale)")
        ax2f.set_title("ConstXY: Hardware Demod — Vector Mean vs Scalar Mean")
        ax2f.legend(fontsize=8, loc="best")
        ax2f.grid(False)
        plt.gcf().set_layout_engine("constrained")
        save_figure(fig2f, results_dir / "hardware_vector_vs_scalar.png", close=False)
        print(f"图已保存: {results_dir / 'hardware_vector_vs_scalar.png'}")
        plt.close("all")
    else:
        print("⚠ 跳过 hardware vector vs scalar 图 (无数据)")

    # ---- 图 2g: hardware scalar / vector ratio ----
    if have_dR and len(R_vec) > 0 and len(R_scalar) > 0:
        ratio_sv = R_scalar / R_vec
        valid = (R_vec > 0) & np.isfinite(ratio_sv)
        fig2g, ax2g = new_figure(kind="wide", height_mm=65)
        ax2g.axhline(1.0, color="black", lw=0.8)
        median_sv = float(np.median(ratio_sv[valid]))
        ax2g.axhline(median_sv, color="C3", ls="--", lw=0.8,
                     label=f"median = {median_sv:.4f}")
        ax2g.plot(freqs_dR[valid], ratio_sv[valid], "s-", color="C3",
                  lw=1.0, ms=4, label="R_scalar / R_vec")
        ax2g.set_xlabel("Z RF field drive frequency (Hz)")
        ax2g.set_ylabel("R_scalar / R_vec")
        ax2g.set_title("ConstXY: Hardware Demod — Scalar / Vector Ratio "
                       "(positive bias of mean|R|)")
        ax2g.legend(fontsize=8, loc="best")
        ax2g.grid(False)
        plt.gcf().set_layout_engine("constrained")
        save_figure(fig2g, results_dir / "hardware_scalar_over_vector_ratio.png", close=False)
        print(f"图已保存: {results_dir / 'hardware_scalar_over_vector_ratio.png'}")
        plt.close("all")
    else:
        print("⚠ 跳过 hardware scalar/vector 比值图 (无数据)")

    # ---- 图 2h: hardware vector phase ----
    if have_dR and len(phase_vec_deg) > 0:
        fig2h, ax2h = new_figure(kind="wide", height_mm=65)
        ax2h.plot(freqs_dR, phase_vec_deg, "s-", color="C3",
                  lw=1.0, ms=4, label="hardware vector phase")
        ax2h.set_xlabel("Z RF field drive frequency (Hz)")
        ax2h.set_ylabel("Phase (deg)")
        ax2h.set_title("ConstXY: Hardware Demod R — Vector Phase vs Drive Frequency")
        ax2h.legend(fontsize=8, loc="best")
        ax2h.grid(False)
        plt.gcf().set_layout_engine("constrained")
        save_figure(fig2h, results_dir / "hardware_phase_vector.png", close=False)
        print(f"图已保存: {results_dir / 'hardware_phase_vector.png'}")
        plt.close("all")
    else:
        print("⚠ 跳过 hardware vector phase 图 (无数据)")

    # ---- 图 3: 诊断图 — picked_freq 与 drive_freq 的偏差 ----
    if (acquisition_mode in ("daq_fft_y", "both")
            and len(freqs_daq) > 0 and len(freq_errors) > 0):
        fig3, ax3 = new_figure(kind="wide", height_mm=65)
        nonsusp = ~is_susp_daq if is_susp_daq is not None else np.ones_like(freqs_daq, dtype=bool)
        ax3.axhline(0, color="black", lw=0.6)
        ax3.axhline(MAX_PICK_FREQ_ERROR_HZ, color="C3", ls=":", lw=0.7,
                    label=f"+{MAX_PICK_FREQ_ERROR_HZ:.0f} Hz threshold")
        ax3.axhline(-MAX_PICK_FREQ_ERROR_HZ, color="C3", ls=":", lw=0.7,
                    label=f"-{MAX_PICK_FREQ_ERROR_HZ:.0f} Hz threshold")
        ax3.plot(freqs_daq[nonsusp], freq_errors[nonsusp], "o-",
                 color="C0", lw=0.8, ms=3, label="non-suspicious")
        if np.any(~nonsusp):
            ax3.plot(freqs_daq[~nonsusp], freq_errors[~nonsusp], "x",
                     color="C3", ms=8, mew=2,
                     label=f"suspicious (n={int(np.sum(~nonsusp))})")
        ax3.set_xlabel("Z RF field drive frequency (Hz)")
        ax3.set_ylabel("picked_freq - drive_freq (Hz)")
        ax3.set_title("ConstXY: daq_fft_y — Picked Frequency Error Diagnostic")
        ax3.legend(fontsize=8, loc="best")
        ax3.grid(False)
        plt.gcf().set_layout_engine("constrained")
        save_figure(fig3, results_dir / "daq_picked_frequency_error.png", close=False)
        print(f"图已保存: {results_dir / 'daq_picked_frequency_error.png'}")
        plt.close("all")
    else:
        print("⚠ 跳过 picked_freq 诊断图 (无数据)")

    # ---- 图 4: 典型频点谱示意 ----
    example_freqs = []
    if len(signal_pts) >= 3:
        example_freqs = [
            signal_pts[0]["freq_Hz"],
            signal_pts[len(signal_pts) // 3]["freq_Hz"],
            signal_pts[2 * len(signal_pts) // 3]["freq_Hz"],
            signal_pts[-1]["freq_Hz"],
        ]
    elif signal_pts:
        example_freqs = [p["freq_Hz"] for p in signal_pts]

    if acquisition_mode in ("daq_fft_y", "both") and example_freqs:
        fig4, axes = new_figure(nrows=len(example_freqs), ncols=1, kind="wide", height_mm=max(65, 55 * (len(example_freqs))), sharex=False)
        if len(example_freqs) == 1:
            axes = [axes]

        # 建立 freq -> record_daq 的索引，方便拿到 picked_freq_Hz
        rec_by_freq = {r["freq_Hz"]: r for r in records_daq}

        for ax, ef in zip(axes, example_freqs):
            target = next((p for p in signal_pts
                           if abs(p["freq_Hz"] - ef) < 1e-3), None)
            if target is None or "y_V" not in target["data"]:
                ax.text(0.5, 0.5, f"No data @ {ef:.1f} Hz",
                        ha="center", va="center", transform=ax.transAxes)
                continue
            y = target["data"]["y_V"]
            fs = float(target["data"]["actual_rate_Sa_s"])
            freqs_s, amp_s, df_s = _windowed_fft_full(y, fs)
            f_show_max = max(2 * ef, 200.0)
            m_show = freqs_s <= f_show_max
            ax.semilogy(freqs_s[m_show], amp_s[m_show], color="C0", lw=0.8,
                        label="FFT amplitude")
            ax.axvline(ef, color="red", ls="--", lw=0.8,
                       label=f"drive = {ef:.1f} Hz")
            # 标出实际 picked_freq
            rec = rec_by_freq.get(ef)
            if rec is not None:
                pf = rec["picked_freq_Hz"]
                if np.isfinite(pf) and pf != ef:
                    ax.axvline(pf, color="C3", ls="-", lw=1.0,
                               label=f"picked = {pf:.1f} Hz")
            ax.set_ylabel("Amp (V)")
            ax.set_title(f"ConstXY Spectrum @ drive = {ef:.1f} Hz "
                         f"(df={df_s:.2f} Hz)")
            ax.legend(fontsize=7, loc="upper right")
            ax.grid(False)
        axes[-1].set_xlabel("Frequency (Hz)")

        plt.gcf().set_layout_engine("constrained")
        save_figure(fig4, results_dir / "response_spectrum_examples.png", close=False)
        print(f"图已保存: {results_dir / 'response_spectrum_examples.png'}")
        plt.close("all")
    else:
        print("⚠ 跳过典型频点谱图 (无 daq_fft_y 数据)")

