"""论文面板与诊断图；图形只消费分析结果，不改变统计口径。"""

import numpy as np
from matplotlib.ticker import ScalarFormatter

from lab_workflows.plotting import COLOR_OPTIMAL, COLOR_TRAD, new_figure, plot_style_context, save_figure
from paper_comparison_core import dispersive


COLORS = (COLOR_TRAD, COLOR_OPTIMAL)
MARKERS = ("o", "s")
STYLES = ("--", "-")


def response_panel(axis, runs, result):
    for run, summary, color, marker, style in zip(runs, result["runs"], COLORS, MARKERS, STYLES):
        axis.errorbar(run["field"], run["signal"], yerr=run["signal_sd"], fmt=marker,
                      color=color, ms=3.2, mew=0.8, mfc="white", capsize=1.5, elinewidth=0.6,
                      label=summary["label"], zorder=3)
        field = np.linspace(run["field"][0], run["field"][-1], 600)
        axis.plot(field, dispersive(field, *run["parameters"]), style, color=color, lw=1.15)
    axis.axhline(0, color="0.8", lw=0.5, zorder=0)
    axis.set(xlabel=r"RF field amplitude, $B_{\mathrm{rf,rms}}$ (nT)", ylabel=r"Lock-in quadrature $X_2$ (V)")
    axis.margins(x=0.025)
    axis.legend(loc="upper left", frameon=False, handlelength=1.4)
    for index, (summary, color) in enumerate(zip(result["runs"], COLORS)):
        axis.text(0.97, 0.19 - 0.065 * index,
                  rf"$g_0 = {summary['slope_at_zero_V_per_nT']:.3f}\,\mathrm{{V/nT}}$",
                  color=color, transform=axis.transAxes, ha="right", fontsize=7.5)
    axis.text(0.97, 0.96, rf"$f_s = {result['runs'][0]['rf_frequency_Hz'] / 1000:g}$ kHz", transform=axis.transAxes, ha="right", va="top", fontsize=8)
    axis.text(0.0, 1.04, "(c)", transform=axis.transAxes, fontweight="bold", fontsize=10)


def sensitivity_panel(axis, runs, result):
    low, high = result["common_band_Hz"]
    axis.axvspan(low, high, color="0.93", zorder=0)
    for run, summary, color, style in zip(runs, result["runs"], COLORS, STYLES):
        visible = (run["frequency"] >= 3) & (run["frequency"] <= 500)
        axis.plot(run["frequency"][visible], run["sensitivity"][visible], style,
                  color=color, lw=0.85, label=summary["label"])
    axis.set(xscale="log", yscale="log", xlim=(3, 500),
             xlabel=r"Output analysis frequency, $f_a$ (Hz)",
             ylabel=r"Noise-equivalent field (fT/$\sqrt{\mathrm{Hz}}$)")
    axis.set_xticks([3, 10, 30, 100, 300])
    axis.xaxis.set_major_formatter(ScalarFormatter())
    axis.legend(loc="upper left", frameon=False, handlelength=1.6)
    axis.text(0.0, 1.04, "(d)", transform=axis.transAxes, fontweight="bold", fontsize=10)


def save_comparison_figures(runs, result, output):
    with plot_style_context("paper"):
        for name, draw in (("rf_control_response", response_panel), ("rf_control_sensitivity", sensitivity_panel)):
            figure, axis = new_figure(width_mm=86, height_mm=64, layout="constrained")
            draw(axis, runs, result)
            save_figure(figure, output / f"{name}.pdf", dpi=600)
        figure, axes = new_figure(ncols=2, width_mm=178, height_mm=65, layout="constrained")
        response_panel(axes[0], runs, result)
        sensitivity_panel(axes[1], runs, result)
        save_figure(figure, output / "rf_control_comparison.pdf", dpi=600)
        figure, axes = new_figure(nrows=2, ncols=2, width_mm=178, height_mm=125, layout="constrained")
        for run, summary, color, style in zip(runs, result["runs"], COLORS, STYLES):
            residual = run["signal"] - dispersive(run["field"], *run["parameters"])
            axes[0, 0].plot(run["field"], residual * 1e3, "o" + style, color=color, ms=3, label=summary["label"])
            mask = (run["frequency"] >= 3) & (run["frequency"] <= 500)
            axes[0, 1].loglog(run["frequency"][mask], np.sqrt(run["mean_psd"][mask]), style, color=color, lw=0.8)
            axes[1, 0].loglog(run["frequency"][mask], run["raw_sensitivity"][mask], style, color=color, lw=0.8)
            axes[1, 1].semilogx(run["frequency"][mask], run["correction"][mask], style, color=color)
        axes[0, 0].axhline(0, color="0.7", lw=0.5)
        axes[0, 0].set(xlabel="RF field amplitude, RMS (nT)", ylabel="Fit residual (mV)")
        axes[0, 0].legend(frameon=False)
        axes[0, 1].set(xlabel="Output analysis frequency (Hz)", ylabel=r"Voltage ASD (V/$\sqrt{\mathrm{Hz}}$)")
        axes[1, 0].set(xlabel="Output analysis frequency (Hz)", ylabel=r"DC-gain-referred ASD (fT/$\sqrt{\mathrm{Hz}}$)")
        axes[1, 1].set(xlabel="Output analysis frequency (Hz)", ylabel="Assumed response correction")
        save_figure(figure, output / "rf_control_diagnostics.pdf", dpi=400)
