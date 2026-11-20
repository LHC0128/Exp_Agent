"""结论.md 的两张对照图（原会话 2026-11-18 补）。
fig_j1_physics.png：静态 Bx = 20 nT 下 P_x 两个半周期叠画 —— 为什么 1f 斜率 J1 量的是"脉冲干不干净"。
fig_meas_vs_model.png：20 组上机 |J1|/基线 与模型（名义参数、反拟合参数）逐点对照。
"""
import json, sys
from pathlib import Path
import numpy as np
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
HERE = Path(__file__).resolve().parent; sys.path.insert(0, str(HERE))
import tailcomp as tc  # noqa: E402

R = json.loads((HERE / "outputs" / "scan_results.json").read_text(encoding="utf-8"))
TC = 25.5064
best = R["w8.20_A0.114"]
cases = [("(a) now: 6.6 µs, no tail", dict(width=6.6, A=0.0), 1.0),
         ("(b) designed: 7.93 µs + exact tail", dict(width=7.93, A=tc.exact_tail(7.93)[0]), 1.0),
         ("(c) best measured: 8.20 µs, tail 21.6 mA", dict(width=8.20, A=0.114), best["scale"])]

# ---------- 图 A ----------
fig, ax = plt.subplots(2, 3, figsize=(14, 6.6), sharex=True, gridspec_kw=dict(height_ratios=[1, 1.4]))
tt = np.linspace(0, 100, 2001)
for k, (title, kw, scale) in enumerate(cases):
    i, z, slow = tc.fields(kw["width"], kw["A"], TC)
    peak_ma = 190.0 / scale
    m = tc.TU < 100
    ax[0, k].plot(tc.TU[m], peak_ma * i[m], "C7", lw=1, label="coil current")
    ax[0, k].plot(tc.TU[m], peak_ma * z[m], "C0", lw=1.4, label="z field at atoms")
    ax[0, k].axhline(0, color="k", lw=0.5); ax[0, k].set_ylim(-30, 60)
    ax[0, k].set_title(title, fontsize=10); ax[0, k].set_ylabel("mA (equiv.)")
    rot = tc.rotation(kw["width"], kw["A"], TC, current_ma=peak_ma)
    ax[0, k].text(98, 50, f"total rotation {rot:.2f}π", ha="right", fontsize=9)
    t, px = tc.simulate(20 * tc.NT, 0.0, kw["width"], kw["A"], TC, current_ma=peak_ma)
    a = np.interp(tt, t, px); b = np.interp(tt + 100, t, px)
    s = 1 / np.max(np.abs(np.r_[a, b]))
    ax[1, k].plot(tt, a * s, "C0", label="after +pulse")
    ax[1, k].plot(tt, b * s, "C1--", label="after −pulse")
    ax[1, k].plot(tt, (a - b) / 2 * s, "C3", lw=1.2, label="difference → 5 kHz (J1)")
    c1 = tc.harm(t, px)[0]
    ax[1, k].text(98, -0.9, f"|5 kHz| = {abs(c1) * s:.3f}", ha="right", fontsize=9)
    ax[1, k].axhline(0, color="k", lw=0.5); ax[1, k].set_ylim(-1.05, 1.05)
    ax[1, k].set_xlabel("time since pulse (µs)"); ax[1, k].set_ylabel("Px (normalized)")
ax[0, 0].legend(fontsize=8, loc="center right"); ax[1, 0].legend(fontsize=8, loc="lower left")
fig.suptitle("Model, static Bx = 20 nT, RF off: the two half-periods match only when the pulse leaves no tail", fontsize=11)
fig.tight_layout(); fig.savefig(HERE / "figures" / "fig_j1_physics.png", dpi=120); plt.close(fig)

# ---------- 图 B ----------
pts = [(k, v) for k, v in R.items() if k != "base"]
def model_ratio(sf, te):
    j = lambda w, A, s: np.linalg.norm([x[0] for x in tc.response(w, A, TC, current_ma=190.0 / s, s_f=sf, tau_e=te)[1:]])
    b = j(6.6, 0.0, 1.0)
    return np.array([j(v["w"], v["A"], v["scale"]) / b for _, v in pts])
meas = np.array([v["ratio_meas"] for _, v in pts])
nom = np.array([v["ratio_model"] for _, v in pts])
fitp = model_ratio(0.595, 60.0)
Aval = np.array([v["A"] for _, v in pts]); wval = np.array([v["w"] for _, v in pts])
rot = np.array([v["model_rot_pi"] for _, v in pts])
np.savez(HERE / "outputs" / "meas_vs_model.npz", meas=meas, nominal=nom, fitted=fitp, A=Aval, w=wval, rot=rot)

fig, ax = plt.subplots(1, 2, figsize=(11.5, 4.8))
colors = {0.0: "C0", 0.06: "C1", 0.114: "C2", 0.16: "C3"}
labels = {0.0: "tail 0 mA", 0.06: "tail 11 mA", 0.114: "tail 22 mA", 0.16: "tail 30 mA"}
for A, c in colors.items():
    m = np.isclose(Aval, A)
    ax[0].plot(nom[m], meas[m], "o", color=c, label=labels[A])
    ax[0].plot(fitp[m], meas[m], "s", mfc="none", color=c, ms=7)
lim = [0.08, 2.2]
ax[0].plot(lim, lim, "k", lw=0.8); ax[0].set_xscale("log"); ax[0].set_yscale("log"); ax[0].set_xlim(lim); ax[0].set_ylim(lim)
ax[0].set_xlabel("model |J1| / baseline"); ax[0].set_ylabel("measured |J1| / baseline")
rn = np.sqrt(np.mean(np.log(meas / nom) ** 2)); rf = np.sqrt(np.mean(np.log(meas / fitp) ** 2))
ax[0].set_title(f"(a) 20 settings: filled = nominal model (rms {rn:.2f}),\nopen = refit s_f 0.60, τe 60 µs (rms {rf:.2f})", fontsize=9)
ax[0].legend(fontsize=8)
for A, c in colors.items():
    m = np.isclose(Aval, A); o = np.argsort(wval[m])
    ax[1].plot(wval[m][o], meas[m][o], "o-", color=c, label=labels[A])
    ax[1].plot(wval[m][o], nom[m][o], "--", color=c, lw=0.9)
    ax[1].plot(wval[m][o], fitp[m][o], ":", color=c, lw=1.2)
ax[1].axhline(1, color="k", lw=0.5)
ax[1].set_xlabel("pulse width w (µs)"); ax[1].set_ylabel("|J1| / baseline")
ax[1].set_title("(b) solid = measured, dashed = nominal model,\ndotted = refit model", fontsize=9); ax[1].legend(fontsize=8)
fig.tight_layout(); fig.savefig(HERE / "figures" / "fig_meas_vs_model.png", dpi=120); plt.close(fig)
print("rms nominal", rn, "rms fit", rf)
