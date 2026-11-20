"""把 11-17 相位扫描（5.0 µs、190 mA、Y RF 5 kHz 0.2 Vpp）分解成 c0、c+1、c-1 三条独立曲线。

输入：raw/phase_scan.csv（phase_deg, repeat, R_v, theta_deg）
模型：Z(phi) = c0 + c+1·e^{iφ} + c−1·e^{−iφ}
输出：phase_three_components_split.png、phase_three_components.csv
"""
from lab_workflows.common import data_root
import csv
import json
import pathlib

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

RUN = (data_root() / "pi_width_scan/1117_175856_pi_width_scan")
OUT = (data_root() / "pi_width_scan/_phase_background_diagnostic")

rows = list(csv.DictReader(open(RUN / "raw" / "phase_scan.csv", encoding="utf-8")))
phi = np.deg2rad(np.array([float(r["phase_deg"]) for r in rows]))
z = np.array([float(r["R_v"]) for r in rows]) * 1e3 * np.exp(
    1j * np.deg2rad(np.array([float(r["theta_deg"]) for r in rows]))
)

A = np.column_stack([np.ones_like(phi), np.exp(1j * phi), np.exp(-1j * phi)])
(c0, cp1, cm1), *_ = np.linalg.lstsq(A, z, rcond=None)
t0 = np.full(phi.shape, c0, dtype=complex)
tp = cp1 * np.exp(1j * phi)
tm = cm1 * np.exp(-1j * phi)
fit = t0 + tp + tm
res = fit - z

print(f"点数 {len(phi)}  |  c0={abs(c0):.2f} mV ∠{np.angle(c0,deg=True):.1f}°")
print(f"  c+1={abs(cp1):.2f} mV ∠{np.angle(cp1,deg=True):.1f}°")
print(f"  c-1={abs(cm1):.2f} mV ∠{np.angle(cm1,deg=True):.1f}°")
print(f"  残差 rms {np.sqrt(np.mean(abs(res)**2)):.2f} mV  max {abs(res).max():.2f} mV")

fig, axes = plt.subplots(2, 2, figsize=(11, 8))
fig.suptitle(
    "Z(φ) = c0 + c+1·e^{iφ} + c−1·e^{−iφ}   |   1117_175856, 5.0 µs, 190 mA, "
    "Y RF 5 kHz 0.2 Vpp, 73 phases",
    fontsize=11,
)

ax = axes[0, 0]
ax.plot(z.real, z.imag, "o", ms=4, color="#4878a8", label="measured Z(φ)")
ax.plot(fit.real, fit.imag, "-", lw=1.2, color="#c33", label="3-component sum")
ax.plot(tp.real, tp.imag, "--", lw=1.0, color="#e08a1e", label="c+1·e^{iφ} (co-rotating)")
ax.plot(tm.real, tm.imag, "--", lw=1.0, color="#3a9e5f", label="c−1·e^{−iφ} (counter-rotating)")
ax.plot(c0.real, c0.imag, "P", ms=11, color="#8a56a8", label="c0 (fixed)")
ax.set_xlabel("Re (mV)")
ax.set_ylabel("Im (mV)")
ax.set_aspect("equal", adjustable="datalim")
ax.grid(alpha=0.3)
ax.legend(fontsize=7, loc="best")
ax.set_title("(a) complex plane: three terms and their sum", fontsize=10)

deg = np.rad2deg(phi)
for ax, part, tag, title in (
    (axes[0, 1], np.real, "Re", "(b) real part, split by term"),
    (axes[1, 0], np.imag, "Im", "(c) imaginary part, split by term"),
):
    ax.plot(deg, part(z), "o", ms=4, color="#4878a8", label="measured")
    ax.plot(deg, part(t0), "-", lw=1.4, color="#8a56a8", label="c0")
    ax.plot(deg, part(tp), "-", lw=1.4, color="#e08a1e", label="c+1·e^{iφ}")
    ax.plot(deg, part(tm), "-", lw=1.4, color="#3a9e5f", label="c−1·e^{−iφ}")
    ax.plot(deg, part(fit), "--", lw=1.0, color="#c33", label="sum")
    ax.set_xlabel("RF phase (deg)")
    ax.set_ylabel(f"{tag}(mV)")
    ax.set_xlim(0, 360)
    ax.grid(alpha=0.3)
    ax.legend(fontsize=7, ncol=2)
    ax.set_title(title, fontsize=10)

ax = axes[1, 1]
ax.plot(deg, abs(z), "o", ms=4, color="#4878a8", label="measured |Z|")
ax.plot(deg, abs(fit), "--", lw=1.0, color="#c33", label="|sum|")
for val, name, color in (
    (abs(c0), "|c0|", "#8a56a8"),
    (abs(cp1), "|c+1|", "#e08a1e"),
    (abs(cm1), "|c−1|", "#3a9e5f"),
):
    ax.axhline(val, ls=":", lw=1.0, color=color, label=f"{name} = {val:.1f} mV")
ax.set_xlabel("RF phase (deg)")
ax.set_ylabel("magnitude (mV)")
ax.set_xlim(0, 360)
ax.grid(alpha=0.3)
ax.legend(fontsize=7)
ax.set_title("(d) magnitudes of the three terms", fontsize=10)

fig.tight_layout()
fig.savefig(OUT / "phase_three_components_split.png", dpi=140)
print("saved", OUT / "phase_three_components_split.png")

with open(OUT / "phase_three_components.csv", "w", encoding="utf-8", newline="") as fh:
    w = csv.writer(fh)
    w.writerow(["component", "abs_mV", "phase_deg", "re_mV", "im_mV"])
    for name, v in (("c0", c0), ("c+1", cp1), ("c-1", cm1)):
        w.writerow([name, f"{abs(v):.4f}", f"{np.angle(v, deg=True):.4f}",
                    f"{v.real:.4f}", f"{v.imag:.4f}"])
    w.writerow(["residual_rms_mV", f"{np.sqrt(np.mean(abs(res)**2)):.4f}", "", "", ""])
print("saved", OUT / "phase_three_components.csv")
