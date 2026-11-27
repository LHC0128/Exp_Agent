"""相位灵敏度与归一化频响的共享绘图；只做离线绘图，不连接仪器、不写文件。

左轴相位灵敏度（µrad/√Hz，实测相邻频点插值带 + 全频段 √Lorentzian 回补），右轴按 H0
归一化的频响 |H(f)|/H0（实测频段实线，最高实测频点之外画 model extrapolation 虚线），
两张图 x 轴都是 log 1–1000 Hz。实现由 rf_phase_sensitivity.analysis_steps 迁出，
两个实验共用同一套样式，便于结果直接对比。图中文字用英文。
"""
from __future__ import annotations

import math

import numpy as np

from .transfer import CLOSURE_RANGE

# B01 结果图统一只画 1–1000 Hz：频响在实测最高频点内画点与实线，之外用 √Lorentzian 外推虚线。
PLOT_FREQUENCY_RANGE_HZ = (1.0, 1000.0)


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        value = float(value)
    except OverflowError:
        return None
    return value if math.isfinite(value) else None


def _diagnostic(ax, text, *, y=0.5):
    """在空面板中央标注无数据原因；双纵轴图上左右两条说明用不同 y 错开，避免叠字。"""
    ax.text(0.5, y, text, ha="center", va="center", transform=ax.transAxes)


def _plot_band(frequency):
    """绘图 x 轴范围（log 1–1000 Hz）内的频点掩码。"""
    return (frequency >= PLOT_FREQUENCY_RANGE_HZ[0]) & (frequency <= PLOT_FREQUENCY_RANGE_HZ[1])


def _referral_plot_style(summary):
    """折算曲线的质量状态与图例文案；质量不足时改用灰色虚线并标 advisory。"""
    referral_available = bool(summary.get("referral_available",
                                          (summary.get("phase_referral") or {}).get("available")))
    referral_warnings = [text for text in summary.get("referral_warnings") or [] if isinstance(text, str)]
    advisory = referral_available and summary.get("referral_quality", "ok") != "ok"
    snr_advisory = any("音调信噪比" in text for text in referral_warnings)
    prefix = ("Measured band (advisory: tone SNR below threshold)" if advisory and snr_advisory
              else "Measured band (advisory: referral warnings, see summary)" if advisory
              else "Measured band direct interp")
    return dict(available=referral_available, advisory=advisory, snr_advisory=snr_advisory,
                color="#7f7f7f" if advisory else "#009e73", style="--" if advisory else "-",
                prefix=prefix)


def _draw_phase_sensitivity(ax, frequency, phase, phase_lor, band_ids, summary):
    """画折算相位灵敏度：实测相邻频点插值带 + 全频段 √Lorentzian 回补。返回是否画出数据。"""
    style = _referral_plot_style(summary)
    visible = _plot_band(frequency)
    if not style["available"]:
        _diagnostic(ax, "No phase referral: hard integrity failure (see summary)")
        return False
    referral = summary.get("phase_referral") or {}
    phase_urad = phase * 1e6
    phase_lor_urad = phase_lor * 1e6
    plotted = False
    lor_band = referral.get("lorentzian_full_band") or {}
    backfill = np.where(visible & (phase_lor_urad > 0), phase_lor_urad, np.nan)
    if lor_band.get("available") and np.isfinite(backfill).any():
        plotted = True
        median_urad = float(np.nanmedian(backfill))
        lo_hz, hi_hz = PLOT_FREQUENCY_RANGE_HZ
        ax.loglog(frequency, backfill, color="#1f77b4", lw=0.95, alpha=0.85,
                  label=f"Full-band sqrt-Lorentzian back-fill (median {median_urad:.3g} urad/sqrt(Hz), "
                        f"{lo_hz:g}-{hi_hz:g} Hz)")
        fc_val = _number(lor_band.get("fc_hz"))
        if fc_val is not None and lo_hz <= fc_val <= hi_hz:
            ax.axvline(fc_val, color="#7f7f7f", ls=":", lw=1.0, label=f"fc = {fc_val:.1f} Hz")
    bands_info = referral.get("bands") or []
    med_txt = ", ".join(f"{band['median_urad_per_sqrt_hz']:.3g}" for band in bands_info)
    label = f"{style['prefix']} (median {med_txt} urad/sqrt(Hz))"
    for idx, band in enumerate(bands_info):
        bins = (band_ids == band["transfer_band"]) & visible & (phase_urad > 0)
        if bins.any():
            plotted = True
            ax.loglog(frequency[bins], phase_urad[bins], color=style["color"], ls=style["style"], lw=1.3,
                      marker="." if bins.sum() == 1 else None, label=label if idx == 0 else None)
    if style["advisory"]:
        note = ("advisory: tone SNR below threshold" if style["snr_advisory"]
                else "advisory: referral warnings (see summary)")
        ax.text(0.015, 0.985, note, transform=ax.transAxes, ha="left", va="top", fontsize=7.2,
                color="#555555", bbox=dict(boxstyle="round,pad=0.25", facecolor="white",
                                           edgecolor="#999999", alpha=0.85))
    if not plotted:
        _diagnostic(ax, "Referred phase ASD is zero")
        return False
    ax.grid(True, which="both", alpha=0.2)
    ax.legend(fontsize=7.3, loc="lower left")
    return plotted


def _transfer_gain_groups(rows, accepted, summary):
    """归一化频响的分组、纵轴含义与 √Lorentzian 参数。"""
    lor_fit = summary.get("transfer_lorentzian_fit") or {}
    lor_ok = bool(lor_fit.get("available"))
    clean_primary = [row for row in accepted.values() if row["quality_passed"] and row.get("h_v_per_rad", 0) > 0]
    warn_primary = [row for row in accepted.values() if not row["quality_passed"] and row.get("h_v_per_rad", 0) > 0]
    clean_repeat = [row for row in rows if row["repeat"] and row["accepted"] and row["quality_passed"]
                    and row.get("h_v_per_rad", 0) > 0]
    warn_repeat = [row for row in rows if row["repeat"] and row["accepted"] and not row["quality_passed"]
                   and row.get("h_v_per_rad", 0) > 0]
    return dict(lor_fit=lor_fit, lor_ok=lor_ok,
                key="normalized_h0_gain" if lor_ok else "normalized_gain",
                error="normalized_h0_gain_se" if lor_ok else "normalized_gain_se",
                ylabel="Normalized |H(f)| / H0" if lor_ok else "Normalized response |H| / |K0|",
                series=(
                    (clean_primary, "o", "#1f77b4", "Primary (no warnings)"),
                    (warn_primary, "^", "#d55e00", "Primary (with warnings)"),
                    (clean_repeat, "x", "#009e73", "Repeat (no warnings)"),
                    (warn_repeat, "d", "#cc3311", "Repeat (with warnings)"),
                ))


def _draw_transfer_gain(ax, rows, accepted, summary):
    """右轴：按 H0 归一化的频响；实测频段画点与实线，超出实测最高频点后画模型外推虚线。"""
    groups = _transfer_gain_groups(rows, accepted, summary)
    key, error = groups["key"], groups["error"]
    ax.axhline(1.0, color="#666666", linestyle="--", linewidth=0.9, label="H0 unity reference")
    if not groups["lor_ok"]:
        ax.axhspan(CLOSURE_RANGE[0], CLOSURE_RANGE[1], color="#888888", alpha=0.12,
                   label=f"Closure band {CLOSURE_RANGE[0]:g}-{CLOSURE_RANGE[1]:g}")
    plotted_any = False
    f_plotted = []
    for selection, marker, color, label in groups["series"]:
        points = [row for row in selection if (row.get(key) or 0) > 0]
        if not points:
            continue
        plotted_any = True
        frequencies = [row["frequency_hz"] for row in points]
        f_plotted.extend(frequencies)
        ax.plot(frequencies, [row[key] for row in points], linestyle="none", marker=marker, color=color,
                label=label)
        bars = [row for row in points if row.get(error) is not None and row[error] < row[key]]
        if bars:
            ax.errorbar([row["frequency_hz"] for row in bars], [row[key] for row in bars],
                        yerr=[row[error] for row in bars], fmt="none", ecolor=color, capsize=2, alpha=0.85)
    if groups["lor_ok"] and f_plotted:
        h0_fit = float(groups["lor_fit"]["h0_v_per_rad"])
        fc_fit = float(groups["lor_fit"]["fc_hz"])
        f_lo = max(min(f_plotted), PLOT_FREQUENCY_RANGE_HZ[0])
        f_hi = max(f_plotted)
        f_fit = np.geomspace(f_lo, f_hi, 200)
        ax.plot(f_fit, 1.0 / np.sqrt(1.0 + (f_fit / fc_fit) ** 2), "-", color="#1f77b4", lw=1.3,
                label=f"sqrt-Lorentzian fit (H0 = {h0_fit:.3g} V/rad)")
        if f_hi < PLOT_FREQUENCY_RANGE_HZ[1]:
            f_extra = np.geomspace(f_hi, PLOT_FREQUENCY_RANGE_HZ[1], 200)
            ax.plot(f_extra, 1.0 / np.sqrt(1.0 + (f_extra / fc_fit) ** 2), "--", color="#1f77b4", lw=1.5,
                    label=f"model extrapolation > {f_hi:g} Hz (fc = {fc_fit:.1f} Hz)")
    ax.set(yscale="linear", ylabel=groups["ylabel"])
    if plotted_any:
        values = [row[key] for selection, _, _, _ in groups["series"] for row in selection if (row.get(key) or 0) > 0]
        upper = max(1.08, 1.05 * max(values))
        if not groups["lor_ok"]:
            upper = max(upper, CLOSURE_RANGE[1] * 1.05)
        ax.set_ylim(0.0, upper)
        ax.legend(fontsize=7.0, loc="upper right")
    else:
        ax.set_ylim(0.0, 1.08)
        _diagnostic(ax, "No usable normalized estimates", y=0.42)
    return plotted_any


def phase_noise_figure(frequency, phase, phase_lor, band_ids, summary, *, title="Phase sensitivity spectrum"):
    """相位灵敏度单面板图（µrad/√Hz），x 轴 log 1–1000 Hz。"""
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    from ...plotting import figure_size

    fig = Figure(figsize=figure_size("wide", height_mm=80), layout="constrained")
    FigureCanvasAgg(fig)
    ax = fig.subplots()
    ax.set(xscale="log", xlabel="Frequency (Hz)",
           ylabel=r"Phase sensitivity ASD ($\mu$rad/$\sqrt{\mathrm{Hz}}$)")
    _draw_phase_sensitivity(ax, frequency, phase, phase_lor, band_ids, summary)
    ax.set_xlim(*PLOT_FREQUENCY_RANGE_HZ)
    fig.suptitle(title)
    return fig


def phase_transfer_figure(rows, accepted, summary, frequency, phase, phase_lor, band_ids):
    """合并双纵轴图：左轴折算相位灵敏度，右轴归一化频响 |H|/H0，x 轴 log 1–1000 Hz。"""
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    from ...plotting import figure_size

    fig = Figure(figsize=figure_size("wide", height_mm=98), layout="constrained")
    FigureCanvasAgg(fig)
    ax_phase = fig.subplots()
    ax_gain = ax_phase.twinx()
    ax_phase.set(xscale="log", xlabel="Frequency (Hz)",
                 ylabel=r"Phase sensitivity ASD ($\mu$rad/$\sqrt{\mathrm{Hz}}$)")
    _draw_phase_sensitivity(ax_phase, frequency, phase, phase_lor, band_ids, summary)
    _draw_transfer_gain(ax_gain, rows, accepted, summary)
    ax_phase.set_xlim(*PLOT_FREQUENCY_RANGE_HZ)
    n = len(summary.get("configured_frequencies_hz") or [])
    clean_n = summary.get("clean_primary_points", 0)
    headline = summary.get("transfer_plot_title") or "Phase sensitivity ASD (left) and |H(f)| / H0 (right)"
    detail = f"{len(accepted)}/{n} transfer points ({clean_n} clean, {len(accepted) - clean_n} warned)"
    lor_fit = summary.get("transfer_lorentzian_fit") or {}
    if lor_fit.get("available"):
        detail += f" | H0 = {lor_fit['h0_v_per_rad']:.3g} V/rad, fc = {lor_fit['fc_hz']:.1f} Hz"
        if (ratio := lor_fit.get("h0_to_k0_ratio")) is not None:
            detail += f", H0/K0 = {ratio:.3f}"
    fig.suptitle(f"{headline}\n{detail}")
    return fig
