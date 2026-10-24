"""保留原 Z 带宽科学图与英文标注；统一 paper 样式、无交互窗口。"""
import numpy as np
import matplotlib
matplotlib.use("Agg")
from lab_workflows.plotting import set_plot_style, new_figure, save_figure
from matplotlib import pyplot as plt
from .analysis_algorithms import _windowed_fft_full


def render(responses, bandwidth, config, params, point_data, results_dir):
    set_plot_style("paper")
    acquisition_mode = params.acquisition_mode
    DAQ_RESPONSE_METHOD = params.daq_response_method
    LOCAL_PEAK_REL_WINDOW = params.local_peak_rel_window
    MAX_PICK_FREQ_ERROR_HZ = params.max_pick_freq_error_hz
    EXCLUDE_SUSPICIOUS_FOR_BANDWIDTH = params.exclude_suspicious_for_bandwidth
    OFFLINE_LOCKIN_USE_INTEGER_CYCLES = params.offline_lockin_use_integer_cycles
    records_daq = responses["records_daq"]
    records_demod_r = responses["records_demod_r"]
    records_offline_lockin = responses["records_offline_lockin"]
    baseline_amp = responses["baseline_amp"]
    freqs_daq = responses["freqs_daq"]
    amps_raw = responses["amps_raw"]
    amps_corr = responses["amps_corr"]
    picked_freqs = responses["picked_freqs"]
    freq_errors = responses["freq_errors"]
    is_suspicious_arr = responses["is_suspicious_arr"]
    freqs_dR = responses["freqs_dR"]
    R_vec = responses["R_vec"]
    R_scalar = responses["R_scalar"]
    R_scalar_std = responses["R_scalar_std"]
    X_mean = responses["X_mean"]
    Y_mean = responses["Y_mean"]
    phase_vec_deg = responses["phase_vec_deg"]
    phase_vec_rad = responses["phase_vec_rad"]
    freqs_li = responses["freqs_li"]
    li_x = responses["li_x"]
    li_y = responses["li_y"]
    li_r = responses["li_r"]
    li_phase_deg = responses["li_phase_deg"]
    li_phase_rad = responses["li_phase_rad"]
    li_n_cycles = responses["li_n_cycles"]
    li_n_samples = responses["li_n_samples"]
    li_valid = responses["li_valid"]
    is_susp_daq = bandwidth["is_susp_daq"]
    bandwidth_daq_raw = bandwidth["bandwidth_daq_raw"]
    bandwidth_daq_corr = bandwidth["bandwidth_daq_corr"]
    bandwidth_daq_fft = bandwidth["bandwidth_daq_fft"]
    bandwidth_offline_lockin = bandwidth["bandwidth_offline_lockin"]
    bandwidth_hardware_demod_r_vector = bandwidth["bandwidth_hardware_demod_r_vector"]
    bandwidth_hardware_demod_r_scalar = bandwidth["bandwidth_hardware_demod_r_scalar"]
    bandwidth_hardware_demod_r = bandwidth["bandwidth_hardware_demod_r"]
    bandwidth_dR = bandwidth["bandwidth_dR"]
    ratio_fft_li = bandwidth["ratio_fft_li"]
    ratio_hw_li = bandwidth["ratio_hw_li"]
    common_freqs = bandwidth["common_freqs"]
    common_hw = bandwidth["common_hw"]
    stat_fft_li = bandwidth["stat_fft_li"]
    stat_hw_li = bandwidth["stat_hw_li"]
    Z_DRIVE_AMPLITUDE = config.get("freq_sweep", {}).get("Z_DRIVE_AMPLITUDE_Vpp", params.z_drive_amplitude)
    signal_pts = [p for p in point_data if not p["is_baseline"]]
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

        ax1.set_xlabel("Z field drive frequency (Hz)")
        ax1.set_ylabel("Amplitude at drive bin (V)")
        ax1.set_title("Z Field Frequency Response — daq_fft_y "
                      f"({DAQ_RESPONSE_METHOD}, Z_drive = {Z_DRIVE_AMPLITUDE} Vpp)")
        ax1.legend(fontsize=8, loc="best")
        ax1.grid(False)

        plt.gcf().set_layout_engine("constrained")
        save_figure(fig1, results_dir / "frequency_response.png", close=False)
        pass
        plt.close("all")
    else:
        pass

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
            pass

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

        ax2.set_xlabel("Z field drive frequency (Hz)")
        ax2.set_ylabel("Normalized response (a.u.)")
        ax2.set_title("Frequency Response Comparison — direct FFT vs offline lock-in vs hardware demod_r")
        ax2.legend(fontsize=8, loc="best")
        ax2.grid(False)

        plt.gcf().set_layout_engine("constrained")
        save_figure(fig2, results_dir / "frequency_response_comparison.png", close=False)
        pass
        plt.close("all")
    else:
        pass

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
        ax2b.set_xlabel("Z field drive frequency (Hz)")
        ax2b.set_ylabel("Amplitude (V, log scale)")
        ax2b.set_title("Frequency Response — Absolute Amplitude Comparison")
        ax2b.legend(fontsize=8, loc="best")
        ax2b.grid(False)
        plt.gcf().set_layout_engine("constrained")
        save_figure(fig2b, results_dir / "frequency_response_absolute.png", close=False)
        pass
        plt.close("all")
    else:
        pass

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
        ax2c.set_xlabel("Z field drive frequency (Hz)")
        ax2c.set_ylabel("FFT amplitude / lock-in amplitude")
        ax2c.set_title("Direct FFT vs Offline Lock-in — Amplitude Ratio")
        ax2c.legend(fontsize=8, loc="best")
        ax2c.grid(False)
        plt.gcf().set_layout_engine("constrained")
        save_figure(fig2c, results_dir / "fft_vs_offline_lockin_ratio.png", close=False)
        pass
        plt.close("all")
    else:
        pass

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
        ax2d.set_xlabel("Z field drive frequency (Hz)")
        ax2d.set_ylabel("hardware demod_r / lock-in amplitude")
        ax2d.set_title("Hardware Demod R vs Offline Lock-in — Amplitude Ratio")
        ax2d.legend(fontsize=8, loc="best")
        ax2d.grid(False)
        plt.gcf().set_layout_engine("constrained")
        save_figure(fig2d, results_dir / "hardware_vs_offline_lockin_ratio.png", close=False)
        pass
        plt.close("all")
    else:
        pass

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
        ax2e1.set_title("Offline Lock-in — Magnitude & Phase vs Drive Frequency")
        ax2e1.legend(fontsize=8, loc="best")
        ax2e1.grid(False)
        ax2e1.set_yscale("log")

        ax2e2.plot(freqs_li[valid], li_phase_deg[valid], "^-", color="C2",
                   lw=1.0, ms=4, label="offline lock-in phase")
        if np.any(~li_valid):
            ax2e2.plot(freqs_li[~li_valid], li_phase_deg[~li_valid], "x",
                       color="gray", ms=6, mew=1.5, alpha=0.5,
                       label=f"invalid (n={int(np.sum(~li_valid))})")
        ax2e2.set_xlabel("Z field drive frequency (Hz)")
        ax2e2.set_ylabel("Phase (deg)")
        ax2e2.legend(fontsize=8, loc="best")
        ax2e2.grid(False)

        plt.gcf().set_layout_engine("constrained")
        save_figure(fig2e, results_dir / "offline_lockin_phase.png", close=False)
        pass
        plt.close("all")
    else:
        pass

    # ---- 图 2f: hardware vector vs scalar ----
    if have_dR and len(R_vec) > 0 and len(R_scalar) > 0:
        fig2f, ax2f = new_figure(kind="wide", height_mm=65)
        ax2f.semilogy(freqs_dR, R_vec, "s-", color="C3",
                      lw=1.2, ms=4, label="R_vec = sqrt(<X>² + <Y>²)")
        ax2f.semilogy(freqs_dR, R_scalar, "o-", color="C4",
                      lw=1.0, ms=4, alpha=0.7, label="R_scalar = <|Z|>")
        ax2f.set_xlabel("Z field drive frequency (Hz)")
        ax2f.set_ylabel("Hardware Demod R (V, log scale)")
        ax2f.set_title("Hardware Demod — Vector Mean vs Scalar Mean")
        ax2f.legend(fontsize=8, loc="best")
        ax2f.grid(False)
        plt.gcf().set_layout_engine("constrained")
        save_figure(fig2f, results_dir / "hardware_vector_vs_scalar.png", close=False)
        pass
        plt.close("all")
    else:
        pass

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
        ax2g.set_xlabel("Z field drive frequency (Hz)")
        ax2g.set_ylabel("R_scalar / R_vec")
        ax2g.set_title("Hardware Demod — Scalar / Vector Ratio (positive bias of mean|R|)")
        ax2g.legend(fontsize=8, loc="best")
        ax2g.grid(False)
        plt.gcf().set_layout_engine("constrained")
        save_figure(fig2g, results_dir / "hardware_scalar_over_vector_ratio.png", close=False)
        pass
        plt.close("all")
    else:
        pass

    # ---- 图 2h: hardware vector phase ----
    if have_dR and len(phase_vec_deg) > 0:
        fig2h, ax2h = new_figure(kind="wide", height_mm=65)
        ax2h.plot(freqs_dR, phase_vec_deg, "s-", color="C3",
                  lw=1.0, ms=4, label="hardware vector phase")
        ax2h.set_xlabel("Z field drive frequency (Hz)")
        ax2h.set_ylabel("Phase (deg)")
        ax2h.set_title("Hardware Demod R — Vector Phase vs Drive Frequency")
        ax2h.legend(fontsize=8, loc="best")
        ax2h.grid(False)
        plt.gcf().set_layout_engine("constrained")
        save_figure(fig2h, results_dir / "hardware_phase_vector.png", close=False)
        pass
        plt.close("all")
    else:
        pass

    # ---- 图 3: 诊断图 — picked_freq 与 drive_freq 的偏差 ----
    if (acquisition_mode in ("daq_fft_y", "both")
            and len(freqs_daq) > 0 and len(freq_errors) > 0):
        fig3, ax3 = new_figure(kind="wide", height_mm=65)
        nonsusp = ~is_susp_daq if is_susp_daq is not None else np.ones_like(freqs_daq, bool)
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
        ax3.set_xlabel("Z field drive frequency (Hz)")
        ax3.set_ylabel("picked_freq - drive_freq (Hz)")
        ax3.set_title("daq_fft_y — Picked Frequency Error Diagnostic")
        ax3.legend(fontsize=8, loc="best")
        ax3.grid(False)
        plt.gcf().set_layout_engine("constrained")
        save_figure(fig3, results_dir / "daq_picked_frequency_error.png", close=False)
        pass
        plt.close("all")
    else:
        pass

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
            ax.set_title(f"Spectrum @ drive = {ef:.1f} Hz "
                         f"(df={df_s:.2f} Hz)")
            ax.legend(fontsize=7, loc="upper right")
            ax.grid(False)
        axes[-1].set_xlabel("Frequency (Hz)")

        plt.gcf().set_layout_engine("constrained")
        save_figure(fig4, results_dir / "response_spectrum_examples.png", close=False)
        pass
        plt.close("all")
    else:
        pass

