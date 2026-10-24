"""保留原五组科研图与英文标注，图仅消费已经拟合的数值产物。"""
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as tck
from lab_workflows.plotting import new_figure, save_figure, set_plot_style
from . import analysis_steps as steps


def render(run_dir):
    set_plot_style("paper")
    config, _ = steps.load_config(run_dir)
    results = steps.load_fits(run_dir)
    stats = steps.statistics(results)
    results_dir = steps.output_path(run_dir, "results")
    valid_mask = stats["valid_mask"]
    duty_pct = stats["duty_pct"]
    R_eff = stats["R_eff"]
    tBB = stats["tBB"]
    tD = stats["tD"]
    eBB = stats["eBB"]
    eD = stats["eD"]
    r2b = stats["r2b"]
    GBB = stats["GBB"]
    GD = stats["GD"]
    rms_d = stats["rms_d"]
    e_GBB = stats["e_GBB"]
    e_GD = stats["e_GD"]
    e_R = stats["e_R"]
    rms_limit = stats["rms_limit"]
    suspect = stats["suspect"]
    hq = stats["hq"]
    CB_BLUE   = "#0072B2"
    CB_ORANGE = "#E69F00"
    CB_GREEN  = "#009E73"
    CB_RED    = "#D55E00"
    CB_PURPLE = "#CC79A7"
    CB_GRAY   = "#999999"


    def save_result_figure(fig, base_name):
        """按公共规范保存 PNG 与 PDF，保留交互式窗口。"""
        save_figure(fig, results_dir / base_name, close=True)
        print(f"  已保存: {base_name}.png/.pdf")


    # --- experimental condition annotation ---
    _fc = config.get("burst_pump", {}).get("modulation_freq_Hz", 90000) / 1000
    _fpv = config.get("fixed_pump", {}).get("fixed_pump_power_voltage", 0.2)
    _Nc = config.get("burst_pump", {}).get("burst_cycles", 1000)
    COND_TEXT = (
        f"$f_L={_fc:.0f}" + r"\,\mathrm{kHz},\;"
        f"\\mathrm{{pump}}={_fpv:.2f}" + r"\,\mathrm{V},\;"
        f"N={_Nc}$"
    )


    def finite_or_nan(value):
        return value if value is not None and np.isfinite(value) else np.nan


    def nearest_index_by_duty(target_pct):
        return int(np.nanargmin(np.abs(duty_pct - target_pct)))

    REPRESENT_DUTY_PCT = [5.0, 10.0, 20.0]
    repr_indices = []
    for rd in REPRESENT_DUTY_PCT:
        repr_indices.append(nearest_index_by_duty(rd))

    fig1, axes1 = new_figure(nrows=1, ncols=3, kind="wide", height_mm=65, sharey=True)
    DS = max(1, len(results[0]["signal"]) // 5000)

    for ax, idx in zip(axes1, repr_indices):
        r = results[idx]
        t_ms = r["time_s"][::DS] * 1000.0
        ax.plot(t_ms, r["signal"][::DS] * 1e3, color=CB_GRAY, lw=0.2, alpha=0.30, label="Raw (90 kHz)")
        ax.plot(t_ms, r["envelope"][::DS] * 1e3, color=CB_BLUE, lw=1.0, label="Envelope")
        if r["fit_success_build"]:
            ax.plot(t_ms, r["fit_build_curve"][::DS] * 1e3, color=CB_RED, lw=1.4, ls="--",
                    label=r"$\tau_{\rm BB}$" + f"={r['tau_BB_s']*1e3:.2f} ms")
        if r["fit_success_decay"]:
            ax.plot(t_ms, r["fit_decay_curve"][::DS] * 1e3, color=CB_GREEN, lw=1.4, ls="-.",
                    label=r"$\tau_{\rm dark}$" + f"={r['tau_dark_s']*1e3:.2f} ms")
        ax.axvspan(0, r["t_burst_end_s"] * 1000, color=CB_ORANGE, alpha=0.08)
        ax.axvline(0, color=CB_ORANGE, ls=":", lw=0.6)
        ax.axvline(r["t_burst_end_s"] * 1000, color=CB_ORANGE, ls=":", lw=0.6)
        r2_str = f"$R^2={r['r2_build']:.2f}$" if np.isfinite(r["r2_build"]) else ""
        ax.set_title(f"Duty {duty_pct[idx]:.1f}%  |  {r2_str}", fontsize=8)
        ax.set_xlabel("Time (ms)")
        ax.set_xlim(-5, 35)
        ax.xaxis.set_minor_locator(tck.AutoMinorLocator(2))
        ax.yaxis.set_minor_locator(tck.AutoMinorLocator(2))
        ax.grid(False)

    axes1[0].set_ylabel("Signal / Envelope (mV)")
    handles, labels = axes1[0].get_legend_handles_labels()
    axes1[0].legend(handles, labels, fontsize=7, loc="upper right", framealpha=0.8)

    fig1.suptitle(f"Bell-Bloom transient fits (representative duty cycles)   [{COND_TEXT}]",
                  fontsize=8)
    fig1.set_layout_engine("constrained")
    save_result_figure(fig1, "transient_fit_duty_examples_pub")



    # ================================================================
    # Fig 2: R_pump vs Duty Cycle (with error bars)
    # ================================================================
    fig2, ax2 = new_figure(kind="wide", height_mm=65)

    # 绘图不再次截短分析器给出的不确定度；上游历史限幅规则另见分析说明。
    _eR_plot = e_R

    if hq.sum() > 0:
        ax2.errorbar(duty_pct[hq], R_eff[hq], yerr=_eR_plot[hq],
                     fmt="o", color=CB_BLUE, capsize=3, capthick=0.8, markersize=7, zorder=5,
                     label="Accepted" + (r" ($R^2 \geq 0.7$)" if suspect.sum() > 0 else ""))
    if suspect.sum() > 0:
        ax2.errorbar(duty_pct[suspect], R_eff[suspect],
                     yerr=_eR_plot[suspect],
                     fmt="o", mfc="none", mec=CB_GRAY, color=CB_GRAY,
                     capsize=3, capthick=0.8, markersize=7, alpha=0.55,
                     label="Suspect (low SNR / large err)")

    # 主拟合：优先用 HQ 点加权；HQ 不够则回退到全体等权
    coeff = stats["coeff"]
    d_fit = np.linspace(duty_pct.min(), duty_pct.max(), 60)
    ax2.plot(d_fit, np.polyval(coeff, d_fit), "-", color=CB_RED, lw=1.6, zorder=6,
             label=f"Fit: $R_{{\\rm pump}} = {coeff[0]:.2f}\\,d + {coeff[1]:+.1f}$")

    # 辅助拟合（全体 valid 等权，虚线，供参考趋势）
    if suspect.sum() > 0 and hq.sum() >= 2:
        coeff2 = np.polyfit(duty_pct[valid_mask], R_eff[valid_mask], 1)
        ax2.plot(d_fit, np.polyval(coeff2, d_fit), ":", color=CB_ORANGE, lw=0.9, alpha=0.7, zorder=3,
                 label="Trend (all points, unweighted)")

    ax2.axhline(0, color="k", lw=0.6)
    ax2.set_xlabel("Duty Cycle (%)")
    ax2.set_ylabel(r"$R_{\rm pump}$ (s$^{-1}$)")
    ax2.set_title(f"$R_{{\\rm pump}}$ vs Duty Cycle  [{COND_TEXT}]", fontsize=8)
    ax2.xaxis.set_minor_locator(tck.AutoMinorLocator(2))
    ax2.yaxis.set_minor_locator(tck.AutoMinorLocator(2))
    ax2.grid(False)
    ax2.legend(fontsize=8, framealpha=0.8)
    fig2.set_layout_engine("constrained")
    save_result_figure(fig2, "rpump_eff_vs_duty_cycle_pub")



    # ================================================================
    # Fig 3: Gamma rates vs Duty Cycle
    # ================================================================
    fig3, ax3 = new_figure(kind="wide", height_mm=65)

    _eGBB_plot = e_GBB * 1e-3
    _eGD_plot = e_GD * 1e-3

    ax3.errorbar(duty_pct, GBB * 1e-3, yerr=_eGBB_plot,
                 fmt="o-", color=CB_RED, capsize=3, capthick=0.8, lw=1.2,
                 label=r"$\Gamma_{\rm BB}=1/\tau_{\rm BB}$")
    ax3.errorbar(duty_pct, GD * 1e-3, yerr=_eGD_plot,
                 fmt="s-", color=CB_GREEN, capsize=3, capthick=0.8, lw=1.2,
                 label=r"$\Gamma_{\rm dark}=1/\tau_{\rm dark}$")

    # 趋势辅助线（全体 valid 等权线性拟合）
    if np.sum(valid_mask) >= 2:
        c_bb = np.polyfit(duty_pct[valid_mask], (GBB * 1e-3)[valid_mask], 1)
        c_d  = np.polyfit(duty_pct[valid_mask], (GD  * 1e-3)[valid_mask], 1)
        _dx = np.linspace(duty_pct.min(), duty_pct.max(), 60)
        ax3.plot(_dx, np.polyval(c_bb, _dx), ":", color=CB_RED, lw=0.8, alpha=0.6, zorder=3)
        ax3.plot(_dx, np.polyval(c_d,  _dx), ":", color=CB_GREEN, lw=0.8, alpha=0.6, zorder=3)

    ax3.set_xlabel("Duty Cycle (%)")
    ax3.set_ylabel(r"Rate ($10^3$ s$^{-1}$)")
    ax3.set_title(r"$\Gamma_{\rm BB},\;\Gamma_{\rm dark}$ vs Duty Cycle  " + f"[{COND_TEXT}]", fontsize=8)
    ax3.xaxis.set_minor_locator(tck.AutoMinorLocator(2))
    ax3.yaxis.set_minor_locator(tck.AutoMinorLocator(2))
    ax3.grid(False)
    ax3.legend(fontsize=8, framealpha=0.8)
    fig3.set_layout_engine("constrained")
    save_result_figure(fig3, "gamma_rates_vs_duty_cycle_pub")



    # ================================================================
    # Fig 4: Zoomed transient
    # ================================================================
    mid_idx = min(len(results) - 1, len(results) // 2)
    r = results[mid_idx]
    t_ms_full = r["time_s"] * 1000.0
    zoom_mask = (t_ms_full >= -3) & (t_ms_full <= r["t_burst_end_s"] * 1000 + 8)
    t_zoom = t_ms_full[zoom_mask]
    ds_zoom = max(1, len(t_zoom) // 8000)

    fig4, (ax4t, ax4b) = new_figure(nrows=2, ncols=1, kind="wide", height_mm=110, sharex=True, gridspec_kw={"height_ratios": [3, 1]})
    ax4t.plot(t_zoom[::ds_zoom], r["signal"][zoom_mask][::ds_zoom] * 1e3,
              color=CB_GRAY, lw=0.2, alpha=0.25, label="Raw (90 kHz)")
    ax4t.plot(t_zoom[::ds_zoom], r["envelope"][zoom_mask][::ds_zoom] * 1e3,
              color=CB_BLUE, lw=1.2, label="Envelope")
    if r["fit_success_build"]:
        ax4t.plot(t_zoom[::ds_zoom], r["fit_build_curve"][zoom_mask][::ds_zoom] * 1e3,
                  color=CB_RED, lw=1.5, ls="--", label="Build-up fit")
    if r["fit_success_decay"]:
        ax4t.plot(t_zoom[::ds_zoom], r["fit_decay_curve"][zoom_mask][::ds_zoom] * 1e3,
                  color=CB_GREEN, lw=1.5, ls="-.", label="Decay fit")
    ax4t.axvspan(0, r["t_burst_end_s"] * 1000, color=CB_ORANGE, alpha=0.07)
    ax4t.axvline(0, color=CB_ORANGE, ls=":", lw=0.8)
    ax4t.axvline(r["t_burst_end_s"] * 1000, color=CB_ORANGE, ls=":", lw=0.8)
    ax4t.set_ylabel("Signal / Envelope (mV)")
    ax4t.grid(False)
    ax4t.legend(fontsize=7, loc="upper right", framealpha=0.8)
    tb_s = r["tau_BB_s"] * 1e3 if r["tau_BB_s"] is not None else 0
    td_s = r["tau_dark_s"] * 1e3 if r["tau_dark_s"] is not None else 0
    ax4t.set_title(f"Zoom: Duty {duty_pct[mid_idx]:.1f}%  |  "
                   + r"$\tau_{\rm BB}$" + f"={tb_s:.2f} ms,  "
                   + r"$\tau_{\rm dark}$" + f"={td_s:.2f} ms", fontsize=8)

    ax4b.axhline(0, color="k", lw=0.5)
    if r["fit_success_build"]:
        ax4b.plot(t_zoom[::ds_zoom], r["res_build"][zoom_mask][::ds_zoom] * 1e3,
                  color=CB_RED, lw=0.5, alpha=0.8, label="Build-up resid.")
    if r["fit_success_decay"]:
        ax4b.plot(t_zoom[::ds_zoom], r["res_decay"][zoom_mask][::ds_zoom] * 1e3,
                  color=CB_GREEN, lw=0.5, alpha=0.8, label="Decay resid.")
    ax4b.set_xlabel("Time (ms)")
    ax4b.set_ylabel("Residual (mV)")
    ax4b.grid(False)
    ax4b.legend(fontsize=7, loc="upper right", framealpha=0.8)
    for _ax in [ax4t, ax4b]:
        _ax.xaxis.set_minor_locator(tck.AutoMinorLocator(2))
        _ax.yaxis.set_minor_locator(tck.AutoMinorLocator(2))
    fig4.set_layout_engine("constrained")
    save_result_figure(fig4, "transient_fit_zoom_pub")



    # ================================================================
    # Fig 5: Residuals diagnostics
    # ================================================================
    n_pts = len(results)
    t_ref = results[0]["time_s"] * 1000.0
    t_hm_min, t_hm_max = -2, 18
    hm_mask = (t_ref >= t_hm_min) & (t_ref <= t_hm_max)
    t_hm = t_ref[hm_mask]

    res_matrix = np.full((n_pts, hm_mask.sum()), np.nan)
    rms_vals = []
    for i, r in enumerate(results):
        rb = r["res_build"] * 1e3
        rd = r["res_decay"] * 1e3
        combined = np.where(np.isfinite(rb), rb, rd)
        res_matrix[i, :] = combined[hm_mask]
        bm = (r["time_s"] >= 0) & (r["time_s"] <= r["t_burst_end_s"])
        if np.any(np.isfinite(r["res_build"][bm])):
            rms_vals.append(float(np.sqrt(np.nanmean(r["res_build"][bm]**2))) * 1e3)
        else:
            rms_vals.append(np.nan)

    fig5, (ax5h, ax5r) = new_figure(nrows=2, ncols=1, kind="wide", height_mm=110, gridspec_kw={"height_ratios": [3, 1]})
    vlim = max(float(np.nanmax(np.abs(res_matrix))), np.finfo(float).eps)
    im = ax5h.pcolormesh(t_hm, duty_pct, res_matrix, cmap="RdBu_r",
                          vmin=-vlim, vmax=vlim, shading="auto", rasterized=True)
    cb = fig5.colorbar(im, ax=ax5h, label="Residual (mV)")
    ax5h.axvline(0, color="k", lw=0.5, ls="--")
    t_end_ms = float(results[0]["t_burst_end_s"]) * 1000
    ax5h.axvline(t_end_ms, color=CB_ORANGE, lw=0.8, ls=":")
    ax5h.set_ylabel("Duty Cycle (%)")
    ax5h.set_title(f"Fit residuals heatmap  [{COND_TEXT}]", fontsize=8)

    ax5r.plot(duty_pct, rms_vals, "o-", color=CB_PURPLE, lw=1.2, label="RMS build-up resid.")
    ax5r.set_xlabel("Duty Cycle (%)")
    ax5r.set_ylabel("RMS res. (mV)")
    ax5r.grid(False)
    ax5r.legend(fontsize=8)
    for _ax in [ax5h, ax5r]:
        _ax.xaxis.set_minor_locator(tck.AutoMinorLocator(2))
    fig5.set_layout_engine("constrained")
    save_result_figure(fig5, "residuals_diagnostics_pub")




