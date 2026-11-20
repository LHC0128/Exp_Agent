"""时域 P_x 的奇偶分解：证明脉冲拖尾与静态场对极化的作用。

输入：_phase_background_diagnostic/px_gated_rf_off.npz（2026-11-17 21:18，射频关，190 mA / 5 µs，
关温控窗口内单发采集 2 ms，CH1 探测光 P_x、CH2 10 Ω 采样电阻电压）。
处理：以 +脉冲上升沿对齐，按 200 µs 折叠平均（10 个周期），不做滤波或平滑。
- 奇部 [P(t) − P(t+T/2)]/2：随脉冲极性反号，只含奇次谐波，是锁相 5 kHz 读数 S 的唯一来源；
- 偶部 [P(t) + P(t+T/2)]/2：两半周期相同，只含偶次谐波（10 kHz …），反映静态场下的极化平衡与翻转。
用法：python px_odd_even.py [输入 npz] [输出目录]
"""
from lab_workflows.common import data_root
import json
import sys
from pathlib import Path

import numpy as np
from scipy.optimize import curve_fit

from lab_workflows.plotting import (COLOR_GRAY, COLOR_OPTIMAL, COLOR_TRAD, COLOR_GREEN, new_figure,
                                    save_figure, set_plot_style)

SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else (data_root() / "pi_width_scan/_phase_background_diagnostic/px_gated_rf_off.npz")
OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(__file__).parent
T_US = 200.0
GAMMA_HZ = 548.0          # 模型拟合（06_model/fits/rf_slow.json），仅用于画 1/Γ 参考线


def fold(src):
    d = np.load(src, allow_pickle=True)
    t = d["ch2_time"]; i_ma = d["ch2_voltage"] / 10.0 * 1e3; px = d["ch1_voltage"] * 1e3
    dt = t[1] - t[0]
    above = i_ma > 50
    rise = np.flatnonzero(above[1:] & ~above[:-1]) + 1
    t0 = t[rise[0]] - 1.5e-6
    n = int(round(T_US * 1e-6 / dt)); nper = int((t[-1] - t0) // (T_US * 1e-6)); s = np.searchsorted(t, t0)
    f = lambda x: x[s:s + nper * n].reshape(nper, n).mean(0)
    return np.arange(n) * dt * 1e6, f(i_ma), f(px), nper


def harmonic(tt, y, k):
    return 2 / T_US * np.trapezoid(y * np.exp(-2j * np.pi * k * tt / T_US), tt)


tt, I, P, nper = fold(SRC)
h = len(tt) // 2
th = tt[:h]
Po, Pe = (P[:h] - P[h:2 * h]) / 2, (P[:h] + P[h:2 * h]) / 2
Io = (I[:h] - I[h:2 * h]) / 2
t_end = th[np.flatnonzero((abs(Io) > 2) & (th < 90))[-1]]
m = (th > 20) & (th < 98)
fexp = lambda x, a, tau, b: a * np.exp(-x / tau) + b
popt, pcov = curve_fit(fexp, th[m], Po[m], p0=(25, 40, 5))
perr = np.sqrt(np.diag(pcov))
k_pk = int(np.argmax(abs(Po[th < 20])))
c1, c2 = harmonic(tt, P - P.mean(), 1), harmonic(tt, P - P.mean(), 2)
res = dict(source=str(SRC), periods=nper, current_end_us=float(t_end), odd_peak_mV=float(Po[k_pk]),
           odd_peak_us=float(th[k_pk]), odd_fit_window_us=[20, 98], odd_tau_us=float(popt[1]),
           odd_tau_err_us=float(perr[1]), odd_amp_mV=float(popt[0]), odd_offset_mV=float(popt[2]),
           even_p2p_after10us_mV=float(np.ptp(Pe[th > 10])),
           fundamental_5k_rms_mV=float(abs(c1) / np.sqrt(2)), harmonic_10k_rms_mV=float(abs(c2) / np.sqrt(2)),
           relaxation_reference_us=float(1e6 / (2 * np.pi * GAMMA_HZ)))
print(json.dumps(res, indent=1))
(OUT / "px_odd_even.json").write_text(json.dumps(res, indent=1), encoding="utf-8")

set_plot_style("paper")
fig, ax = new_figure(nrows=3, ncols=1, kind="wide", height_mm=150, sharex=False)
# (a) 原始折叠波形
a = ax[0]
a.plot(tt, P - P.mean(), color=COLOR_OPTIMAL, lw=0.7, label="probe $P_x$ (CH1, mean removed)")
a.set_ylabel("$P_x$ (mV)")
a2 = a.twinx()
a2.plot(tt, I, color=COLOR_GRAY, lw=0.7, label="coil current (CH2/10 Ω)")
a2.set_ylabel("current (mA)", color=COLOR_GRAY)
a.set_xlabel("time from +pulse (µs)")
a.set_title(f"(a) folded over {nper} periods, RF off, 190 mA / 5 µs", loc="left")
a.legend(loc="upper right"); a2.legend(loc="lower right")
# (b) 奇部
b = ax[1]
b.plot(th, Po, color=COLOR_OPTIMAL, lw=0.8, label="odd part of $P_x$ (source of 5 kHz S)")
xf = np.linspace(20, 100, 200)
b.plot(xf, fexp(xf, *popt), color=COLOR_TRAD, lw=1.4,
       label=f"exp fit 20–98 µs: τ = {popt[1]:.1f} ± {perr[1]:.1f} µs")
tau_rel = res["relaxation_reference_us"]
y0 = fexp(20, *popt)
b.plot(xf, (y0 - popt[2]) * np.exp(-(xf - 20) / tau_rel) + popt[2], "--", color=COLOR_GRAY,
       label=f"transverse relaxation only (1/Γ = {tau_rel:.0f} µs)")
b3 = b.twinx()
b3.plot(th, Io, color=COLOR_GREEN, lw=0.6, alpha=0.7)
b3.set_ylabel("odd current (mA)", color=COLOR_GREEN); b3.set_ylim(-260, 260)
b.axvline(t_end, color=COLOR_GREEN, ls=":", lw=1)
b.text(t_end + 1, b.get_ylim()[1] * 0.85, f"current < 2 mA after {t_end:.1f} µs", color=COLOR_GREEN, fontsize=7)
b.set_xlabel("time from pulse (µs)"); b.set_ylabel("odd $P_x$ (mV)")
b.set_title("(b) polarity-odd part keeps evolving after the current has ended", loc="left")
b.legend(loc="upper right")
# (c) 偶部
c = ax[2]
c.plot(th, Pe - Pe.mean(), color=COLOR_OPTIMAL, lw=0.8, label="even part of $P_x$ (mean removed)")
c.set_xlabel("time from pulse (µs)"); c.set_ylabel("even $P_x$ (mV)")
c.set_title(f"(c) polarity-even part: static-field equilibrium + flip; 10 kHz = {res['harmonic_10k_rms_mV']:.1f} mV rms, "
            f"5 kHz = 0 by symmetry", loc="left")
c.legend(loc="upper right")
for x in (b, c):
    x.axvspan(0, 5, color=COLOR_GRAY, alpha=0.15, lw=0)
save_figure(fig, OUT / "px_time_domain_odd_even.png")
print(OUT / "px_time_domain_odd_even.png")
