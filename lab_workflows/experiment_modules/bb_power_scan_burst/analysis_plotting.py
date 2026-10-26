"""原五组英文科研图，统一 paper 样式；只读取明确目录的拟合结果。"""
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.ticker as tck
from lab_workflows.plotting import new_figure, save_figure, set_plot_style
from lab_workflows.workflow_state import write_state
from .analysis_steps import load_fits, load_config, output_path
from .stages import PLOT_NAMES


def render(run_dir):
    set_plot_style("paper")
    config, _ = load_config(run_dir)
    results_dir = output_path(run_dir, "results")
    results = load_fits(run_dir)
    # Colorblind-friendly palette (Wong 2011, Nature Methods)
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
    _dc = config.get("burst_pump", {}).get("fixed_duty_cycle_pct", 5.0)
    _Nc = config.get("burst_pump", {}).get("burst_cycles", 1000)
    COND_TEXT = (
        f"$f_L={_fc:.0f}" + r"\,\mathrm{kHz},\;"
        f"\\mathrm{{duty}}={_dc:.1f}" + r"\%,"
        rf"\;N={_Nc}$"
    )

    # --- assemble data arrays ---
    valid_mask = np.array([np.isfinite(r["R_pump_eff_per_s"]) for r in results])
    pumps  = np.array([r["pump_high_v"] for r in results])
    R_eff  = np.array([r["R_pump_eff_per_s"] for r in results])
    tBB    = np.array([(r["tau_BB_s"] if r["tau_BB_s"] is not None else np.nan) for r in results])
    tD     = np.array([(r["tau_dark_s"] if r["tau_dark_s"] is not None else np.nan) for r in results])
    eBB    = np.array([(r["tau_BB_err_s"] if r["tau_BB_err_s"] is not None and np.isfinite(r["tau_BB_err_s"]) else np.nan) for r in results])
    eD     = np.array([(r["tau_dark_err_s"] if r["tau_dark_err_s"] is not None and np.isfinite(r["tau_dark_err_s"]) else np.nan) for r in results])
    r2b    = np.array([(r["r2_build"] if np.isfinite(r["r2_build"]) else np.nan) for r in results])
    GBB    = np.array([(r["Gamma_BB_per_s"] if np.isfinite(r["Gamma_BB_per_s"]) else np.nan) for r in results])
    GD     = np.array([(r["Gamma_dark_per_s"] if np.isfinite(r["Gamma_dark_per_s"]) else np.nan) for r in results])

    # error propagation
    with np.errstate(divide="ignore", invalid="ignore"):
        e_R   = np.sqrt((eBB / tBB**2)**2 + (eD / tD**2)**2)
        e_GBB = eBB / tBB**2
        e_GD  = eD  / tD**2

    low_quality = (r2b < 0.7) | (R_eff < 0) | (~valid_mask)
    hq = ~low_quality

    print(f"[data ready] {low_quality.sum()}/{len(results)} low-quality pts")


    # ================================================================
    # Fig 1: Representative transient fits (3 panels)
    # ================================================================
    REPRESENT_V = [0.02, 0.12, 0.20]
    repr_indices = []
    for rv in REPRESENT_V:
        for i, r in enumerate(results):
            if abs(r["pump_high_v"] - rv) < 0.005:
                repr_indices.append(i)
                break
    if len(repr_indices) < 2:
        repr_indices = [0, len(results)//2, -1]

    fig1, axes1 = new_figure(nrows=1, ncols=3, kind="wide", height_mm=65, sharey=True)
    DS = max(1, len(results[0]["signal"]) // 5000)

    for ax, idx in zip(axes1, repr_indices):
        r = results[idx]
        t_ms = r["time_s"][::DS] * 1000.0
        ax.plot(t_ms, r["signal"][::DS] * 1e3, color=CB_GRAY, lw=0.2, alpha=0.30, label="Raw (90 kHz)")
        ax.plot(t_ms, r["envelope"][::DS] * 1e3, color=CB_BLUE, lw=1.0, label="Envelope")
        if r["fit_success_build"]:
            ax.plot(t_ms, r["fit_build_curve"][::DS] * 1e3, color=CB_RED, lw=1.4, ls="--",
                    label="$\\tau_{\\rm BB}$" + f"={r['tau_BB_s']*1e3:.2f} ms")
        if r["fit_success_decay"]:
            ax.plot(t_ms, r["fit_decay_curve"][::DS] * 1e3, color=CB_GREEN, lw=1.4, ls="-.",
                    label="$\\tau_{\\rm dark}$" + f"={r['tau_dark_s']*1e3:.2f} ms")
        ax.axvspan(0, r["t_burst_end_s"] * 1000, color=CB_ORANGE, alpha=0.08)
        ax.axvline(0, color=CB_ORANGE, ls=":", lw=0.6)
        ax.axvline(r["t_burst_end_s"] * 1000, color=CB_ORANGE, ls=":", lw=0.6)
        r2_str = f"$R^2={r['r2_build']:.2f}$" if np.isfinite(r["r2_build"]) else ""
        ax.set_title(f"Pump {r['pump_high_v']:.2f} V  |  {r2_str}", fontsize=8)
        ax.set_xlabel("Time (ms)")
        ax.set_xlim(-5, 35)
        ax.xaxis.set_minor_locator(tck.AutoMinorLocator(2))
        ax.yaxis.set_minor_locator(tck.AutoMinorLocator(2))
        ax.grid(False)

    axes1[0].set_ylabel("Signal / Envelope (mV)")
    handles, labels = axes1[0].get_legend_handles_labels()
    axes1[0].legend(handles, labels, fontsize=7, loc="upper right", framealpha=0.8)

    fig1.suptitle(f"Bell-Bloom transient fits (representative powers)   [{COND_TEXT}]",
                  fontsize=8)
    fig1.set_layout_engine("constrained")
    save_result_figure(fig1, "transient_fit_examples_pub")



    # ================================================================
    # Fig 2: R_pump vs Pump Power (with error bars)
    # ================================================================
    fig2, ax2 = new_figure(kind="wide", height_mm=65)
    if hq.sum() > 0:
        ax2.errorbar(pumps[hq], R_eff[hq], yerr=e_R[hq],
                     fmt="o", color=CB_BLUE, capsize=3, capthick=0.8, markersize=7, zorder=5,
                     label="$R^2 \\geq 0.7$")
    if low_quality.sum() > 0:
        ax2.errorbar(pumps[low_quality], R_eff[low_quality],
                     yerr=e_R[low_quality],
                     fmt="o", mfc="none", mec=CB_GRAY, color=CB_GRAY,
                     capsize=3, capthick=0.8, markersize=7, alpha=0.55,
                     label="Low SNR / $R^2 < 0.7$")
    if hq.sum() >= 3:
        w = 1.0 / np.maximum(e_R[hq], 1.0)
        coeff = np.polyfit(pumps[hq], R_eff[hq], 1, w=w)
        p_fit = np.linspace(pumps.min(), pumps.max(), 50)
        ax2.plot(p_fit, np.polyval(coeff, p_fit), "--", color=CB_RED, lw=1.0,
                 label=f"Linear: $R_{{\\rm pump}} = {coeff[0]:.0f}\\,V {coeff[1]:+.0f}$")

    ax2.axhline(0, color="k", lw=0.6)
    ax2.set_xlabel("Pump high voltage (V)")
    ax2.set_ylabel("$R_{\\rm pump}$ (s$^{-1}$)")
    ax2.set_title(f"$R_{{\\rm pump}}$ vs Pump Power  [{COND_TEXT}]", fontsize=8)
    ax2.xaxis.set_minor_locator(tck.AutoMinorLocator(2))
    ax2.yaxis.set_minor_locator(tck.AutoMinorLocator(2))
    ax2.grid(False)
    ax2.legend(fontsize=8, framealpha=0.8)
    fig2.set_layout_engine("constrained")
    save_result_figure(fig2, "rpump_eff_vs_pump_power_pub")



    # ================================================================
    # Fig 3: Gamma rates vs Pump Power
    # ================================================================
    fig3, ax3 = new_figure(kind="wide", height_mm=65)
    ax3.errorbar(pumps, GBB * 1e-3, yerr=e_GBB * 1e-3,
                 fmt="o-", color=CB_RED, capsize=3, capthick=0.8, lw=1.2,
                 label="$\\Gamma_{\\rm BB}=1/\\tau_{\\rm BB}$")
    ax3.errorbar(pumps, GD * 1e-3, yerr=e_GD * 1e-3,
                 fmt="s-", color=CB_GREEN, capsize=3, capthick=0.8, lw=1.2,
                 label="$\\Gamma_{\\rm dark}=1/\\tau_{\\rm dark}$")
    ax3.set_xlabel("Pump high voltage (V)")
    ax3.set_ylabel("Rate ($10^3$ s$^{-1}$)")
    ax3.set_title("$\\Gamma_{\\rm BB},\\;\\Gamma_{\\rm dark}$ vs Pump Power  " + f"[{COND_TEXT}]", fontsize=8)
    ax3.xaxis.set_minor_locator(tck.AutoMinorLocator(2))
    ax3.yaxis.set_minor_locator(tck.AutoMinorLocator(2))
    ax3.grid(False)
    ax3.legend(fontsize=8, framealpha=0.8)
    fig3.set_layout_engine("constrained")
    save_result_figure(fig3, "gamma_rates_vs_pump_power_pub")



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
    ax4t.set_title(f"Zoom: Pump {r['pump_high_v']:.2f} V  |  "
                   + "$\\tau_{\\rm BB}$" + f"={tb_s:.2f} ms,  "
                   + "$\\tau_{\\rm dark}$" + f"={td_s:.2f} ms", fontsize=8)

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
    # Fig 5: Residuals heatmap + RMS
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
    vlim = max(float(np.nanmax(np.abs(res_matrix))) if np.isfinite(res_matrix).any() else 0, np.finfo(float).eps)
    im = ax5h.pcolormesh(t_hm, pumps, res_matrix, cmap="RdBu_r",
                          vmin=-vlim, vmax=vlim, shading="auto", rasterized=True)
    cb = fig5.colorbar(im, ax=ax5h, label="Residual (mV)")
    ax5h.axvline(0, color="k", lw=0.5, ls="--")
    t_end_ms = float(results[0]["t_burst_end_s"]) * 1000
    ax5h.axvline(t_end_ms, color=CB_ORANGE, lw=0.8, ls=":")
    ax5h.set_ylabel("Pump high voltage (V)")
    ax5h.set_title(f"Fit residuals heatmap  [{COND_TEXT}]", fontsize=8)

    ax5r.plot(pumps, rms_vals, "o-", color=CB_PURPLE, lw=1.2, label="RMS build-up resid.")
    ax5r.set_xlabel("Pump high voltage (V)")
    ax5r.set_ylabel("RMS res. (mV)")
    ax5r.grid(False)
    ax5r.legend(fontsize=8)
    for _ax in [ax5h, ax5r]:
        _ax.xaxis.set_minor_locator(tck.AutoMinorLocator(2))
    fig5.set_layout_engine("constrained")
    save_result_figure(fig5, "residuals_diagnostics_pub")



    files = [name+suffix for name in PLOT_NAMES for suffix in (".png", ".pdf")]
    write_state(results_dir/"plot_manifest.json", {"files": files})
