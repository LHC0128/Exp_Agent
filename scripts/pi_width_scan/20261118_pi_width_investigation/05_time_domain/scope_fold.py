"""按 200 µs 周期折叠示波器波形：脉冲间电流基线（奇/偶部）与 CH1 探测信号。"""
import sys
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

src = Path(sys.argv[1]); out = Path(sys.argv[2])
d = np.load(src, allow_pickle=True)
t = d["ch2_time"]; i_ma = d["ch2_voltage"] / 10.0 * 1e3          # 10 Ω 采样电阻
T = 200e-6
dt = t[1] - t[0]
# 以 +脉冲上升沿对齐：找电流越过 +50 mA 的上升沿
above = i_ma > 50
rise = np.flatnonzero(above[1:] & ~above[:-1]) + 1
t0 = t[rise[0]] - 1.5e-6
n_per = int(round(T / dt))
nper = int((t[-1] - t0) // T)
start = np.searchsorted(t, t0)
seg = lambda x: x[start:start + nper * n_per].reshape(nper, n_per)
I = seg(i_ma); tt = (np.arange(n_per) * dt) * 1e6
Imean = I.mean(0)
half = n_per // 2
odd = (Imean[:half] - Imean[half:2 * half]) / 2      # 对 T/2 平移反号的部分
even = (Imean[:half] + Imean[half:2 * half]) / 2
lines = [f"periods folded: {nper}, dt = {dt*1e9:.1f} ns"]
for a, b in ((15, 30), (30, 60), (60, 95)):
    m = (tt[:half] >= a) & (tt[:half] < b)
    lines.append(f"baseline {a}-{b} us after +pulse start: odd part {odd[m].mean():+.3f} mA, even part {even[m].mean():+.3f} mA")
m = (tt[:half] >= 12) & (tt[:half] < 98)
area_odd = np.trapezoid(odd[m], tt[:half][m]) * 1e-6     # mA·s
rot_deg = np.degrees(2 * np.pi * 653.79 * area_odd)
lines.append(f"odd-baseline area 12-98 us: {area_odd*1e9:.2f} mA*us*1e3 -> extra rotation per half period {rot_deg:+.2f} deg")
pulse = (tt[:half] < 12)
lines.append(f"pulse area (0-12 us, odd part): {np.trapezoid(odd[pulse], tt[:half][pulse]):.1f} mA*us")
have_probe = "ch1_voltage" in d.files
if have_probe:
    P = seg(d["ch1_voltage"] * 1e3).mean(0)
    P = P - P.mean()
text = "\n".join(lines); print(text)
(out / f"{src.stem}_fold.txt").write_text(text, encoding="utf-8")

fig, ax = plt.subplots(3 if have_probe else 2, 1, figsize=(11, 9 if have_probe else 6), sharex=True)
ax[0].plot(tt, Imean, lw=0.8); ax[0].set_ylabel("current (mA)")
ax[0].set_title(f"{src.name}: folded over {nper} periods")
ax[1].plot(tt[:half], odd, label="odd part [I(t) - I(t+T/2)]/2")
ax[1].plot(tt[:half], even, label="even part")
ax[1].set_ylim(-3, 3); ax[1].set_ylabel("mA (zoomed)"); ax[1].legend(fontsize=8); ax[1].axhline(0, color="k", lw=0.5)
if have_probe:
    ax[2].plot(tt, P, lw=0.8); ax[2].set_ylabel("CH1 probe (mV, mean removed)")
ax[-1].set_xlabel("time from +pulse (us)")
fig.tight_layout(); fig.savefig(out / f"{src.stem}_fold.png", dpi=120)
