"""教学示意图：c0 / c+1 / c−1 三个分量与脉冲的关系。

不含新测量，只用 1117_175856 相位扫描的拟合结果做相量示意。
"""
from pathlib import Path

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

plt.rcParams.update({
    "font.size": 9.5,
    "axes.grid": True,
    "grid.alpha": 0.25,
    "savefig.dpi": 170,
    "figure.dpi": 170,
    "axes.titlesize": 10.5,
})

# 1117_175856, 73 相位, 0.2 Vpp, 5.0 us 的复数最小二乘结果
C0 = 12.40 * np.exp(np.deg2rad(-47.8) * 1j)
CP = 34.38 * np.exp(np.deg2rad(-131.8) * 1j)
CM = 22.00 * np.exp(np.deg2rad(-112.4) * 1j)

fig = plt.figure(figsize=(13.2, 9.6))
gs = fig.add_gridspec(2, 2, hspace=0.34, wspace=0.24)

# ---------------------------------------------------------------- (a) 两个时钟
ax = fig.add_subplot(gs[0, 0])
t = np.linspace(0, 200, 4001)
ax.plot(t, np.sin(2 * np.pi * t / 200), lw=2.2, color="#2b6cb0",
        label=r"RF drive, $\varphi=0^\circ$")
ax.plot(t, np.sin(2 * np.pi * t / 200 + np.pi / 2), lw=1.6, ls="--", color="#dd6b20",
        label=r"RF drive, $\varphi=90^\circ$")
ax.axhline(0, color="k", lw=0.7)
ax.axvspan(0, 100, color="#2b6cb0", alpha=0.06)
ax.axvspan(100, 200, color="#c53030", alpha=0.06)
ax.vlines([0, 200], 0, 1.55, color="0.25", lw=3.5)
ax.vlines([100], 0, -1.55, color="0.25", lw=3.5)
ax.text(4, 1.68, r"$+\pi$ pulse", color="0.25", fontsize=9)
ax.text(104, -1.95, r"$-\pi$ pulse", color="0.25", fontsize=9)
ax.text(50, -2.35, r"$+\pi$ window", ha="center", color="#2b6cb0", fontsize=9)
ax.text(150, -2.35, r"$-\pi$ window", ha="center", color="#c53030", fontsize=9)
ax.set_xlim(-4, 204)
ax.set_ylim(-2.6, 2.1)
ax.set_xlabel("time (µs)")
ax.set_ylabel("normalized amplitude")
ax.set_title(r"(a) two clocks: one RF cycle (200 µs) = one $\pm\pi$ pair")
ax.legend(loc="upper right", fontsize=8.5, framealpha=0.9)

# ---------------------------------------------------------------- (b) 相位共轭
ax = fig.add_subplot(gs[0, 1])
th = np.linspace(0, 2 * np.pi, 500)
ax.plot(np.cos(th), np.sin(th), color="0.72", lw=1.0)
ax.axhline(0, color="k", lw=0.6)
ax.axvline(0, color="k", lw=0.6)
phi = np.deg2rad(52)
ax.annotate("", xy=(np.cos(phi), np.sin(phi)), xytext=(0, 0),
            arrowprops=dict(arrowstyle="-|>", lw=2.4, color="#2b6cb0"))
ax.annotate("", xy=(np.cos(phi), -np.sin(phi)), xytext=(0, 0),
            arrowprops=dict(arrowstyle="-|>", lw=2.4, color="#c53030", ls="--"))
ax.text(np.cos(phi) * 1.06, np.sin(phi) * 1.06 + 0.07, r"coherence $e^{+i\varphi}$",
        color="#2b6cb0", fontsize=9.5)
ax.text(np.cos(phi) * 1.06, -np.sin(phi) * 1.06 - 0.17, r"after $\pi$ pulse: $e^{-i\varphi}$",
        color="#c53030", fontsize=9.5)
arc = matplotlib.patches.Arc((0, 0), 1.4, 1.4, theta1=-52, theta2=52,
                              color="0.45", lw=1.2, ls=":")
ax.add_patch(arc)
ax.text(-0.62, -0.62, "mirror about Re axis", color="0.45", fontsize=8.2)
ax.set_xlim(-1.35, 1.35)
ax.set_ylim(-1.3, 1.3)
ax.set_aspect("equal")
ax.set_xlabel("Re")
ax.set_ylabel("Im")
ax.set_title(r"(b) a $\pi$ pulse reverses it: $+\varphi \rightarrow -\varphi$")

# ---------------------------------------------------------------- (c) 三个分量
ax = fig.add_subplot(gs[1, 0])
phis = np.linspace(0, 2 * np.pi, 721)
Z = C0 + CP * np.exp(1j * phis) + CM * np.exp(-1j * phis)

ax.add_patch(plt.Circle((0, 0), abs(CP), fill=False, ls="--", lw=1.5, color="#dd6b20"))
ax.add_patch(plt.Circle((0, 0), abs(CM), fill=False, ls="--", lw=1.5, color="#2f855a"))
ax.plot([0, C0.real], [0, C0.imag], "-", lw=2.2, color="#6b46c1")
ax.plot(C0.real, C0.imag, "P", ms=11, color="#6b46c1")
ax.plot(Z.real, Z.imag, "-", lw=2.2, color="#c53030")

marks = np.deg2rad([10, 100, 190, 280])
Zm = C0 + CP * np.exp(1j * marks) + CM * np.exp(-1j * marks)
ax.plot(Zm.real, Zm.imag, "o", ms=6.5, mfc="white", mec="#c53030", mew=1.5)
for a, z in zip([10, 100, 190, 280], Zm):
    u = z / abs(z)
    ax.annotate(str(a) + " deg", (z.real, z.imag),
                xytext=(z.real + u.real * 9, z.imag + u.imag * 9),
                fontsize=8.2, color="#c53030", ha="center", va="center")

handles = [
    Line2D([], [], color="#c53030", lw=2.2, label=r"$Z(\varphi)=c_0+c_{+1}e^{i\varphi}+c_{-1}e^{-i\varphi}$"),
    Line2D([], [], color="#dd6b20", ls="--", lw=1.5, label=r"$+1$: $|c_{+1}|=34.4$ mV, rotates $+\varphi$"),
    Line2D([], [], color="#2f855a", ls="--", lw=1.5, label=r"$-1$: $|c_{-1}|=22.0$ mV, rotates $-\varphi$"),
    Line2D([], [], color="#6b46c1", lw=0, marker="P", ms=9, label=r"$c_0=12.4$ mV: fixed, pulse clock"),
]
ax.legend(handles=handles, loc="upper left", fontsize=7.6, framealpha=0.95)
ax.set_aspect("equal")
ax.set_xlim(-72, 72)
ax.set_ylim(-72, 72)
ax.set_xlabel("Re (mV)")
ax.set_ylabel("Im (mV)")
ax.set_title("(c) three terms, three clocks (fit of 1117_175856)")

# ---------------------------------------------------------------- (d) sin theta
ax = fig.add_subplot(gs[1, 1])
th = np.linspace(0, 2 * np.pi, 600)
ax.plot(th / np.pi, np.sin(th), lw=2.4, color="#2b6cb0", label=r"odd part $\propto \sin\theta$")
ax.plot(th / np.pi, np.cos(th), lw=1.5, ls="--", color="#718096", label=r"even part $\propto 1-\cos\theta$ (ideal $\pi$: constant)")
ax.axhline(0, color="k", lw=0.7)
ax.axvline(1, color="#c53030", lw=1.2, ls=":")
ax.axvline(2, color="#2f855a", lw=1.2, ls=":")
ax.annotate(r"$\theta=\pi$: max $\to$ the $\pi$ criterion",
            xy=(1, 1), xytext=(1.06, 0.60), fontsize=9, color="#c53030",
            arrowprops=dict(arrowstyle="-", lw=1.0, color="#c53030"))
ax.annotate(r"$\theta=2\pi$: zero, flipped twice $\rightarrow$ back",
            xy=(2, 0), xytext=(1.30, -0.55), fontsize=9, color="#2f855a",
            arrowprops=dict(arrowstyle="-", lw=1.0, color="#2f855a"))
d = 0.22
ax.plot([1 - d / np.pi, 1 + d / np.pi], [np.sin(np.pi - d)] * 2, "-", lw=2.2, color="#dd6b20")
ax.text(1.06, np.sin(np.pi - d) + 0.10, r"imperfect: residual $\sin\delta$", color="#dd6b20", fontsize=8.5)
ax.set_xlim(0, 2)
ax.set_ylim(-1.25, 1.3)
ax.set_xticks([0, 0.5, 1, 1.5, 2])
ax.set_xlabel(r"flip angle $\theta/\pi$")
ax.set_ylabel("normalized signal")
ax.set_title(r"(d) why the peak sits at $\pi$ and vanishes at $2\pi$")
ax.legend(loc="lower left", fontsize=8.2, framealpha=0.92)

fig.suptitle("c0 / c+1 / c−1: which clock each term is locked to", fontsize=12.5, y=0.975)
fig.savefig(Path(__file__).resolve().parents[4] / "工作总结/2026-11-18/fig_three_component_concept.png",
            bbox_inches="tight", facecolor="white")

zmax, zmin = np.abs(Z).max(), np.abs(Z).min()
print(f"|Z| min={zmin:.2f} mV  max={zmax:.2f} mV")
print("saved fig_three_component_concept.png")
