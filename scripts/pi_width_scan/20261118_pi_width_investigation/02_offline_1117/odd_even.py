"""把 10° 与 190° 两次脉宽扫描按复数拆成射频奇部 O 与偶部 E。"""
from lab_workflows.common import data_root
import csv
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = (data_root() / "pi_width_scan")
OUT = Path(sys.argv[1])


def load(run):
    groups = defaultdict(list)
    times = defaultdict(list)
    with open(ROOT / run / "raw" / "scan.csv", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            w = float(row["width_us"])
            groups[w].append(float(row["R_v"]) * np.exp(1j * np.radians(float(row["theta_deg"]))))
            times[w].append(row["timestamp"])
    w = np.array(sorted(groups))
    z = np.array([np.mean(groups[k]) for k in w])
    sd = np.array([np.std(groups[k]) / np.sqrt(len(groups[k])) for k in w])
    return w, z, sd


w1, z10, s10 = load("1117_232929_pi_width_scan")
w2, z190, s190 = load("1117_234916_pi_width_scan")
assert np.allclose(w1, w2)
O = (z10 - z190) / 2
E = (z10 + z190) / 2

np.set_printoptions(linewidth=200, precision=4, suppress=True)
print(" w    |Z10|  |Z190|   |O|   argO   |E|   argE  (mV, deg)")
for k in range(0, len(w1), 2):
    print(f"{w1[k]:5.1f} {abs(z10[k])*1e3:6.2f} {abs(z190[k])*1e3:6.2f} {abs(O[k])*1e3:6.2f} {np.degrees(np.angle(O[k])):7.1f}"
          f" {abs(E[k])*1e3:6.2f} {np.degrees(np.angle(E[k])):7.1f}")
print("peak |O| at", w1[np.argmax(abs(O))], "us;  peak |E| at", w1[np.argmax(abs(E))])

fig, ax = plt.subplots(1, 3, figsize=(15, 4.2))
ax[0].plot(w1, abs(z10) * 1e3, "o-", ms=3, label="|Z| phase 10 deg")
ax[0].plot(w1, abs(z190) * 1e3, "s-", ms=3, label="|Z| phase 190 deg")
ax[0].plot(w1, abs(O) * 1e3, "k-", lw=2, label="|O| = |Z10 - Z190|/2 (RF-odd)")
ax[0].plot(w1, abs(E) * 1e3, "r-", lw=2, label="|E| = |Z10 + Z190|/2 (RF-even)")
ax[0].set_xlabel("Pulse width (us)"); ax[0].set_ylabel("mV"); ax[0].legend(fontsize=8)
ax[1].plot(w1, np.degrees(np.angle(O)), "k.-", label="arg O")
ax[1].plot(w1, np.degrees(np.angle(E)), "r.-", label="arg E")
ax[1].set_xlabel("Pulse width (us)"); ax[1].set_ylabel("deg"); ax[1].legend(fontsize=8)
ax[2].plot(O.real * 1e3, O.imag * 1e3, "k.-", label="O")
ax[2].plot(E.real * 1e3, E.imag * 1e3, "r.-", label="E")
ax[2].plot(0, 0, "b+", ms=12)
for k in range(0, len(w1), 10):
    ax[2].annotate(f"{w1[k]:g}", (O.real[k] * 1e3, O.imag[k] * 1e3), fontsize=7)
    ax[2].annotate(f"{w1[k]:g}", (E.real[k] * 1e3, E.imag[k] * 1e3), fontsize=7, color="r")
ax[2].set_aspect("equal"); ax[2].set_xlabel("X (mV)"); ax[2].set_ylabel("Y (mV)"); ax[2].legend(fontsize=8)
fig.tight_layout()
fig.savefig(OUT / "odd_even_10_190.png", dpi=130)
np.savez(OUT / "odd_even.npz", w=w1, z10=z10, z190=z190)
