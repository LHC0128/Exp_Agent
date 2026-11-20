"""拖尾补偿的说明图（英文标注）。

fig1_principle：驱动电流与原子处 z 场（不补偿 6.6 µs 对比精确补偿 7.93 µs），以及累计转角。
fig2_j1_map：|J1| 在 (A, w) 平面，标出极小点、精确补偿点与 π 等值线（读 outputs/j1_map.npz）。
fig3_px_predict：2f 零点处 P_x(t) 的模型预言：不补偿 / 只补 Z / Z + X/Y（slow_tilt 取示波器量级）。
"""
import sys
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import tailcomp as tc  # noqa: E402
from transverse import null2f  # noqa: E402

FIG = HERE / "figures"; FIG.mkdir(exist_ok=True)
MA = 190.0
W_PI = 7.930
A_PI, TAU_C = tc.exact_tail(W_PI)


def fig1():
    fig, ax = plt.subplots(3, 1, figsize=(8.5, 8.5), sharex=True)
    for lab, w, A, col in (("uncompensated, 6.6 us", 6.6, 0.0, "C0"), (f"tail-cancelled, {W_PI:.2f} us", W_PI, A_PI, "C3")):
        i, z, slow = tc.fields(w, A, TAU_C)
        ax[0].plot(tc.TU, MA * i, col, label=lab)
        ax[1].plot(tc.TU, MA * z, col, label=lab + " (total)")
        ax[1].plot(tc.TU, MA * slow, col, ls=":", lw=1, label=lab + " (slow part)")
        peak = tc.TWO_PI * tc.K_HZ_PER_MA * MA * 1e-6
        cum = np.cumsum(z) * (tc.TU[1] - tc.TU[0]) * peak / np.pi
        ax[2].plot(tc.TU, cum, col, label=lab)
    ax[0].set_ylabel("coil current (mA)"); ax[0].set_ylim(-60, 220)
    ax[0].axhline(0, color="k", lw=0.5)
    ax[0].annotate(f"tail: -{MA*A_PI:.1f} mA, tau_c = {TAU_C:.1f} us", xy=(W_PI + 3, -MA * A_PI * 0.8), xytext=(25, -45),
                   arrowprops=dict(arrowstyle="->"), fontsize=9)
    ax[1].set_ylabel("z field at atoms\n(mA-equivalent, DC coil const.)"); ax[1].set_ylim(-30, 220)
    ax[1].axhline(0, color="k", lw=0.5)
    ax[2].set_ylabel("accumulated rotation (pi)"); ax[2].axhline(1, color="k", lw=0.5, ls="--")
    ax[2].set_xlabel("time since +pulse (us)")
    for a in ax:
        a.set_xlim(-2, 98); a.legend(fontsize=8, loc="upper right")
    ax[0].set_title("Shield model: B = s_f*I + (1-s_f)*LP_tau_e[I], s_f=0.544, tau_e=46.9 us. "
                    "Tail current -c*x makes B = 0 after the pulse", fontsize=9)
    fig.tight_layout(); fig.savefig(FIG / "fig1_principle.png", dpi=130); plt.close(fig)


def fig2():
    d = np.load(HERE / "outputs" / "j1_map.npz")
    ws, As, J, ROT = d["ws"], d["As"], d["J"], d["ROT"]
    fig, ax = plt.subplots(figsize=(7.5, 5.5))
    m = ax.pcolormesh(ws, MA * As, J / float(d["n_uncomp"]), shading="auto", cmap="viridis", vmin=0, vmax=1.5)
    fig.colorbar(m, label="|J1| / |J1| (uncompensated, 6.6 us)")
    cs = ax.contour(ws, MA * As, ROT, levels=[0.9, 1.0, 1.1], colors="w", linewidths=[0.6, 1.4, 0.6])
    ax.clabel(cs, fmt=lambda v: f"{v:.1f} pi", fontsize=8)
    wl = np.linspace(ws[0], ws[-1], 60)
    ax.plot(wl, [MA * tc.exact_tail(w)[0] for w in wl], "r--", lw=1, label="exact tail amplitude A*(w)")
    ax.plot(float(d["w_m"]), MA * float(d["A_m"]), "r*", ms=14, label=f"|J1| minimum ({float(d['w_m']):.2f} us, {MA*float(d['A_m']):.1f} mA)")
    ax.plot(float(d["w_pi"]), MA * float(d["A_pi"]), "wo", mfc="none", ms=10, label=f"exact pi + tail ({float(d['w_pi']):.2f} us, {MA*float(d['A_pi']):.1f} mA)")
    ax.set_xlabel("pulse width w (us)"); ax.set_ylabel("tail amplitude (mA, at 190 mA peak)")
    ax.set_title(f"1f slope criterion |J1| (tau_c = {TAU_C:.1f} us fixed); floor = finite-width residual", fontsize=9)
    ax.legend(fontsize=8, loc="upper left"); fig.tight_layout(); fig.savefig(FIG / "fig2_j1_map.png", dpi=130); plt.close(fig)


def fig3():
    st = (0.025, 0.033)
    G = 950.0                     # mV / 单位极化（bperp_null 1f 斜率 + 5128.4 Hz/V 推得 0.9–1.0 V）
    fig, ax = plt.subplots(figsize=(9, 4.5))
    for lab, w, A, comp, col in (("uncompensated 6.6 us", 6.6, 0.0, (0, 0), "C0"),
                                 (f"Z tail-cancelled {W_PI:.2f} us", W_PI, A_PI, (0, 0), "C1"),
                                 ("Z + X/Y compensation", W_PI, A_PI, (-st[0], -st[1]), "C3")):
        b, _ = null2f(w, A, TAU_C, slow_tilt=st, xy_comp=comp)
        t, px = tc.simulate(b[0] * tc.NT, b[1] * tc.NT, w, A, TAU_C, slow_tilt=st, xy_comp=comp)
        ax.plot(t, G * (px - np.trapezoid(px, t) / tc.PERIOD), col,
                label=f"{lab}: 2f null at ({b[0]:+.1f}, {b[1]:+.1f}) nT")
    ax.set_xlabel("time since +pulse (us)"); ax.set_ylabel("Px - mean (mV, G = 0.95 V)")
    ax.set_title("Model Px at the 2f null, slow_tilt = (0.025, 0.033) (scope-fit scale)", fontsize=9)
    ax.legend(fontsize=8); fig.tight_layout(); fig.savefig(FIG / "fig3_px_predict.png", dpi=130); plt.close(fig)


if __name__ == "__main__":
    fig1(); fig2(); fig3()
    print("figures written to", FIG)
