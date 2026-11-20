"""用法：python bias_response.py <03_phase_grid 目录> [输出目录]。c0 对 X 偏置的分解：L = [c0(+) − c0(−)]/2（线性响应），M = [c0(+) + c0(−)]/2 与 c0(0) 对照。"""
import sys
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

grid_dir = Path(sys.argv[1])                       # 03_phase_grid
fig_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else grid_dir
g0, gm, gp = (np.load(grid_dir / f"grid_{t}.npz") for t in ("bias0", "biasm20", "biasp20"))
w = g0["w"]
L = (gp["c0"] - gm["c0"]) / 2          # 每 20 mV X 偏置的线性响应
M = (gp["c0"] + gm["c0"]) / 2
Q = M - g0["c0"]                        # 二次项（应很小）
lines = [" w     |L|   argL   |M|   argM  |c0(0)|  |M-c0(0)|  ->  zero-X-field residual |c0(0)-x*L| at best x"]
# 找使 Σ|c0(0) + x·L|² 最小的实数 x（等效于外推到横场为零的偏置）
x_best = -np.real(np.vdot(L, g0["c0"])) / np.real(np.vdot(L, L))
R = g0["c0"] + x_best * L
for k in range(len(w)):
    lines.append(f"{w[k]:5.2f} {abs(L[k]):6.2f} {np.degrees(np.angle(L[k])):6.1f} {abs(M[k]):6.2f} "
                 f"{np.degrees(np.angle(M[k])):6.1f} {abs(g0['c0'][k]):7.2f} {abs(Q[k]):8.2f}    {abs(R[k]):6.2f}")
lines.append(f"# best X bias to null the bias-linear part: x = {x_best:.3f} x 20 mV = {x_best * 20:.1f} mV")
lines.append(f"# c-1 consistency across biases: max |c-1(+) - c-1(-)| = {np.max(abs(gp['cm'] - gm['cm'])):.2f} mV,"
             f" max |c+1(+) - c+1(-)| = {np.max(abs(gp['cp'] - gm['cp'])):.2f} mV")
text = "\n".join(lines)
print(text)
(fig_dir / "bias_response.txt").write_text(text, encoding="utf-8")

fig, ax = plt.subplots(1, 3, figsize=(15, 4.3))
ax[0].plot(w, abs(L), "o-", ms=3, label="|L| = |c0(+20mV) - c0(-20mV)|/2 (X-field linear)")
ax[0].plot(w, abs(M), "s-", ms=3, label="|M| = |c0(+) + c0(-)|/2")
ax[0].plot(w, abs(g0["c0"]), "k--", label="|c0| at X bias 0")
ax[0].plot(w, abs(R), "r-", lw=2, label=f"|c0(0) + x L|, x = {x_best:.2f} (X-field-independent part)")
for a in ax[:2]:
    a.axvline(6.5, color="gray", ls=":", lw=1); a.axvline(12.0, color="gray", ls=":", lw=1)
    a.set_xlabel("Pulse width (us)")
ax[0].set_ylabel("mV"); ax[0].legend(fontsize=7)
ax[1].plot(w, np.degrees(np.unwrap(np.angle(L))), "o-", ms=3, label="arg L")
ax[1].plot(w, np.degrees(np.unwrap(np.angle(R))), "r.-", label="arg (X-independent part)")
ax[1].set_ylabel("deg"); ax[1].legend(fontsize=8)
ax[2].plot(L.real, L.imag, "o-", ms=3, label="L")
ax[2].plot(R.real, R.imag, "r.-", label="X-independent part")
for k in range(0, len(w), 4):
    ax[2].annotate(f"{w[k]:g}", (L.real[k], L.imag[k]), fontsize=7)
    ax[2].annotate(f"{w[k]:g}", (R.real[k], R.imag[k]), fontsize=7, color="r")
ax[2].plot(0, 0, "b+", ms=12); ax[2].set_aspect("equal"); ax[2].legend(fontsize=8)
ax[2].set_xlabel("Re (mV)"); ax[2].set_ylabel("Im (mV)")
ax[0].set_title("dotted lines: pi (6.5 us) and 2pi (12 us) from c-1")
fig.tight_layout()
fig.savefig(fig_dir / "bias_response.png", dpi=130)
