"""工作总结用图（2026-11-18）：相位 × 脉宽分解与模型证据。

输入（均为本目录下已有结果，不重新分析原始数据）：
- 03_phase_grid/grid_bias{0,m20,p20}.npz：每个脉宽 4 个射频相位的复数读数与分解 c0、c+1、c−1
  （运行目录见 01_acquisition/runs_index.csv）；
- 06_model/fits/rf_slow.npz、rf_scale.npz：只用 c±1 拟合的两种模型；
- 06_model/figures/predict_L_{slow,scaleonly}.npz：X 偏置响应 L 与 S 分解的样本外预言。
用法：python make_summary_figures.py
"""
import sys
from pathlib import Path

import numpy as np

from lab_workflows.plotting import (COLOR_GRAY, COLOR_GREEN, COLOR_OPTIMAL, COLOR_ORANGE, COLOR_PURPLE,
                                    COLOR_TRAD, new_figure, save_figure, set_plot_style)

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(__file__).parent
G = {b: np.load(ROOT / "03_phase_grid" / f"grid_{b}.npz") for b in ("biasm20", "bias0", "biasp20")}
BIAS = {"biasm20": ("X bias −20 mV", COLOR_OPTIMAL), "bias0": ("X bias 0", "k"), "biasp20": ("X bias +20 mV", COLOR_TRAD)}
PI_US, TWO_PI_US, ELEC_US = 6.67, 11.92, 5.122        # 03_phase_grid/locate_pi（bias 0）与电学标定

set_plot_style("paper")

# ---------- 图 A：分解 ----------
fig, ax = new_figure(nrows=2, ncols=2, kind="wide", height_mm=130)
g0 = G["bias0"]; w = g0["w"]
a = ax[0, 0]
for j, ph in enumerate(g0["phases"]):
    a.plot(w, abs(g0["Z"][:, j]), "o-", ms=2.5, label=f"RF phase {ph:g}°")
a.set_xlabel("pulse width (µs)"); a.set_ylabel("|Z| = R (mV)")
a.set_title("(a) single-phase R(τ): peak position depends on phase", loc="left")
a.legend(ncol=2)

b = ax[0, 1]
for key, lab, col in (("c0", "|c0| (RF-independent, S)", COLOR_TRAD), ("cp", "|c+1| (co-rotating)", COLOR_OPTIMAL),
                      ("cm", "|c−1| (pulse-only, counter-rotating)", "k")):
    b.plot(w, abs(g0[key]), "o-", ms=2.5, color=col, label=lab)
b.plot(w, g0["resid"], ":", color=COLOR_GRAY, label="4-phase fit residual")
b.axvline(ELEC_US, color=COLOR_GRAY, ls="--", lw=0.9)
b.axvline(PI_US, color="k", ls=":", lw=0.9); b.axvline(TWO_PI_US, color="k", ls=":", lw=0.9)
b.text(ELEC_US - 0.1, 19.5, "electrical π", rotation=90, ha="right", fontsize=6, color=COLOR_GRAY)
b.text(PI_US + 0.1, 19.5, "c−1 peak", rotation=90, fontsize=6); b.text(TWO_PI_US + 0.1, 3.6, "c−1 null", rotation=90, fontsize=6)
b.set_xlabel("pulse width (µs)"); b.set_ylabel("magnitude (mV)")
b.set_title(r"(b) $Z(\varphi) = c_0 + c_{+1}e^{i\varphi} + c_{-1}e^{-i\varphi}$, X bias 0", loc="left")
b.legend(loc="upper right", fontsize=6)

c = ax[1, 0]
for key, (lab, col) in BIAS.items():
    gg = G[key]
    c.plot(gg["cm"].real, gg["cm"].imag, "-", color=col, lw=1.2)
    c.plot(gg["c0"].real, gg["c0"].imag, "--", color=col, lw=1.0, label=f"c0, {lab}")
for k in (0, 4, 9, 14, 19, 24):
    c.annotate(f"{w[k]:g}", (g0["cm"].real[k], g0["cm"].imag[k]), fontsize=6)
    c.annotate(f"{w[k]:g}", (g0["c0"].real[k], g0["c0"].imag[k]), fontsize=6, color=COLOR_GRAY)
c.plot([], [], "-", color="k", label="c−1 (three biases overlap)")
c.plot(0, 0, "+", color=COLOR_PURPLE, ms=10, mew=1.5)
c.set_aspect("equal", adjustable="datalim")
c.set_xlabel("Re (mV)"); c.set_ylabel("Im (mV)")
c.set_title("(c) solid: c−1 loops (overlap); dashed: c0 shifts with X bias", loc="left")
c.legend(fontsize=6, loc="upper right")

d = ax[1, 1]
for key, (lab, col) in BIAS.items():
    gg = G[key]
    d.plot(w, abs(gg["cm"]), "-", color=col, lw=1.4, label=f"|c−1|, {lab}")
    d.plot(w, abs(gg["c0"]), "--", color=col, lw=1.0, label=f"|c0|, {lab}")
d.axvline(PI_US, color="k", ls=":", lw=0.9); d.axvline(TWO_PI_US, color="k", ls=":", lw=0.9)
d.set_xlabel("pulse width (µs)"); d.set_ylabel("magnitude (mV)")
d.set_title("(d) c−1 is bias-independent (≤0.73 mV); c0 is not", loc="left")
d.set_ylim(0, 31); d.legend(ncol=2, fontsize=6, loc="upper right")
save_figure(fig, OUT / "figA_phase_grid_decomposition.png")

# ---------- 图 B：模型证据 ----------
fs, fc = np.load(ROOT / "06_model" / "fits" / "rf_slow.npz"), np.load(ROOT / "06_model" / "fits" / "rf_scale.npz")
ps, pc = np.load(ROOT / "06_model" / "figures" / "predict_L_slow.npz"), np.load(ROOT / "06_model" / "figures" / "predict_L_scaleonly.npz")
fig, ax = new_figure(nrows=2, ncols=2, kind="wide", height_mm=130)
for a, j, name in ((ax[0, 0], 1, "c+1"), (ax[0, 1], 2, "c−1")):
    a.plot(fs["w"], abs(fs["data"][:, j]), "ko", ms=2.5, label="data (X bias 0)")
    a.plot(fs["w"], abs(fs["model"][:, j]), "-", color=COLOR_OPTIMAL, label="slow-tail model (rms 0.42 mV)")
    a.plot(fc["w"], abs(fc["model"][:, j]), "--", color=COLOR_TRAD, label="scaled coil constant (rms 2.52 mV)")
    a.set_xlabel("pulse width (µs)"); a.set_ylabel(f"|{name}| (mV)")
ax[0, 0].set_title("(a) fit to RF channels only: |c+1|", loc="left")
ax[0, 1].set_title("(b) |c−1| (same fit)", loc="left")
ax[0, 1].legend(loc="lower center", fontsize=6)

a = ax[1, 0]
a.plot(ps["w"], abs(ps["L"]), "ko", ms=2.5, label="data: L = [c0(+20) − c0(−20)]/2")
a.plot(ps["w"], abs(ps["L_model"]), "-", color=COLOR_OPTIMAL, label=f"slow-tail model, shape predicted (rms {float(ps['L_rms']):.2f} mV)")
a.plot(pc["w"], abs(pc["L_model"]), "--", color=COLOR_TRAD, label=f"scaled coil constant (rms {float(pc['L_rms']):.2f} mV)")
for x in (PI_US, TWO_PI_US):
    a.axvline(x, color="k", ls=":", lw=0.9)
a.text(PI_US + 0.1, 0.4, "π", fontsize=7); a.text(TWO_PI_US + 0.1, 0.4, "2π", fontsize=7)
a.set_xlabel("pulse width (µs)"); a.set_ylabel("|L| (mV per 20 mV X bias)")
a.set_title("(c) out-of-sample: X-field response is non-zero at π and 2π", loc="left")
a.set_ylim(0, 15.5); a.legend(fontsize=6, loc="upper right")

a = ax[1, 1]
for key, b_val in (("c0_m20", -1), ("c0_0", 0), ("c0_p20", 1)):
    lab, col = BIAS[{-1: "biasm20", 0: "bias0", 1: "biasp20"}[b_val]]
    a.plot(ps["w"], abs(ps[key]), "o", ms=2.5, color=col, label=f"|c0| data, {lab}")
    a.plot(ps["w"], abs(ps["S_model_base"] + b_val * ps["S_model_xlin"]), "-", color=col, lw=1.0)
a.set_xlabel("pulse width (µs)"); a.set_ylabel("|c0| = S (mV)")
a.set_title("(d) S model: static field × tail + transverse part of tail", loc="left")
a.plot([], [], "-", color=COLOR_GRAY, label=f"model, 6 real coeffs (rms {float(ps['S_rms']):.1f} mV)")
a.legend(fontsize=6)
save_figure(fig, OUT / "figB_model_evidence.png")
print("saved", OUT / "figA_phase_grid_decomposition.png", OUT / "figB_model_evidence.png")
