"""教学图：Z 任意波随宽度、沿宽、拖尾 s_f/τe 的变化。"""
from pathlib import Path

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from lab_workflows.experiment_modules.rf_phase_response.models import RfPhaseResponseParams
from lab_workflows.experiment_modules.z_chain_common.prep import pulse_wave
from lab_workflows.plotting import format_axis, plot_style_context, save_figure, style_legend

BASE = dict(y_rf_freq_hz=5000.0, z_peak_current_ma=190.0, z_pulse_edge_us=1.0,
            z_pulse_width_us=8.03, z_tail_comp_enabled=True,
            z_tail_s_f=0.544, z_tail_tau_e_us=46.9)


def curve(width=None, **overrides):
    values = dict(BASE) | overrides
    params = RfPhaseResponseParams(**values)
    w = float(width if width is not None else params.z_pulse_width_us)
    wave, record = pulse_wave(params, w)
    t = np.arange(wave.size) / wave.size * params.pulse_period_us
    return t, wave * params.z_peak_current_ma, record


with plot_style_context("paper"):
    figure, axes = plt.subplots(2, 2, figsize=(7.4, 5.6))
    (width_ax, edge_ax), (tail_ax, tau_ax) = axes

    for color, w in zip(plt.cm.viridis(np.linspace(0.1, 0.9, 5)), (4.0, 6.0, 8.03, 10.0, 16.0)):
        t, current, _ = curve(width=w)
        width_ax.plot(t, current, lw=0.9, color=color, label=f"w = {w:g} µs")
    width_ax.set_title("(a) pulse width w", loc="left", fontsize=7, pad=2)
    width_ax.set_xlim(0, 130)
    width_ax.set_ylim(-215, 215)
    width_ax.set_xticks(np.arange(0, 131, 20))
    width_ax.text(2, 200, r"+$\pi$", fontsize=6)
    width_ax.text(102, 200, r"-$\pi$", fontsize=6)
    format_axis(width_ax, xlabel="time (µs)", ylabel="current (mA)")
    style_legend(width_ax, loc="lower left", fontsize=5.2, frameon=False)

    for color, edge in zip(plt.cm.plasma(np.linspace(0.1, 0.85, 3)), (0.5, 1.0, 2.0)):
        t, current, _ = curve(z_pulse_edge_us=edge)
        edge_ax.plot(t, current, lw=0.9, color=color, label=f"e = {edge:g} µs")
    edge_ax.set_title("(b) edge e", loc="left", fontsize=7, pad=2)
    edge_ax.set_xlim(0, 12)
    edge_ax.set_ylim(-45, 210)
    edge_ax.set_xticks(np.arange(0, 13, 3))
    format_axis(edge_ax, xlabel="time (µs)", ylabel="current (mA)")
    style_legend(edge_ax, loc="upper right", fontsize=5.2, frameon=False)

    for color, kw, label in (
            ("0.45", dict(z_tail_comp_enabled=False), "tail off"),
            ("#1f77b4", dict(z_tail_s_f=0.44), "s$_f$ = 0.44"),
            ("#2ca02c", dict(z_tail_s_f=0.544), "s$_f$ = 0.544"),
            ("#d62728", dict(z_tail_s_f=0.65), "s$_f$ = 0.65")):
        t, current, record = curve(**kw)
        tail_ax.plot(t, current, lw=1.0, color=color, label=label)
        if label == "s$_f$ = 0.44":
            start = record["tail_start_current_ma"]
            tail_ax.plot([8.03], [start], "o", ms=2.8, color=color)
            tail_ax.annotate(f"A = {abs(start):.1f} mA", (8.03, start),
                             textcoords="offset points", xytext=(6, -10), fontsize=5.5, color=color)
            tau = record["tau_c_us"]
            tail_ax.annotate("", xy=(8.03 + tau, -1.5), xytext=(8.03, -1.5),
                             arrowprops=dict(arrowstyle="<->", lw=0.5, color=color, shrinkA=0, shrinkB=0))
            tail_ax.text(8.03 + tau / 2.0, -4.0, f"τ$_c$ = {tau:.0f} µs", ha="center", fontsize=5.5,
                         color=color)
    tail_ax.set_title("(c) tail amplitude: s$_f$ sets A, τ$_c$ = s$_f$τ$_e$", loc="left", fontsize=7, pad=2)
    tail_ax.set_xlim(8.2, 62)
    tail_ax.set_ylim(-42, 3)
    tail_ax.set_xticks(np.arange(10, 61, 10))
    format_axis(tail_ax, xlabel="time (µs)", ylabel="current (mA)")
    style_legend(tail_ax, loc="lower right", fontsize=5.2, frameon=False)

    for color, tau_e in zip(plt.cm.cool(np.linspace(0.15, 0.85, 3)), (35.0, 46.9, 60.0)):
        t, current, _ = curve(z_tail_tau_e_us=tau_e)
        tau_ax.plot(t, current, lw=1.0, color=color, label=f"τ$_e$ = {tau_e:g} µs")
    tau_ax.set_title("(d) tail decay τ$_e$", loc="left", fontsize=7, pad=2)
    tau_ax.set_xlim(8.2, 80)
    tau_ax.set_ylim(-42, 3)
    tau_ax.set_xticks(np.arange(10, 81, 15))
    format_axis(tau_ax, xlabel="time (µs)", ylabel="current (mA)")
    style_legend(tau_ax, loc="lower right", fontsize=5.2, frameon=False)

    figure.tight_layout()
    print(save_figure(figure, Path(__file__).resolve().parent.parent / "figures" / "z_waveform_params.png"))
