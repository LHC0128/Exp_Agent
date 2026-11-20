"""工作总结图 C：2f 偶部判据的物理图像与模型验证结果。

数据来源：harmmodel.py 直接模拟（a、b），verify.py 写出的 outputs/summary.json（c、d、e）。
用法：python make_figC.py  → figC_even_2f_criterion.{png,pdf}
"""
import json
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import harmmodel as hm

HERE = Path(__file__).resolve().parent
S = json.loads((HERE / "outputs" / "summary.json").read_text(encoding="utf-8"))
NT = hm.GAMMA_HZ_PER_NT
BX = 20 * NT                      # 静态 Bx = 20 nT

fig = plt.figure(figsize=(13, 8.2))
gs = fig.add_gridspec(2, 6, hspace=0.42, wspace=0.9)

# (a)(b) 时域：P_x(t) 与 P_x(t+T/2) 叠画
for k, (title, kw) in enumerate([("(a) ideal π (no tail, 5.12 µs): halves identical after the pulse",
                                  dict(s_f=1.0, width=5.12)),
                                 ("(b) fitted slow tail (6.6 µs): halves differ",
                                  {})]):
    ax = fig.add_subplot(gs[0, 3 * k:3 * k + 3])
    t, px = hm.simulate(BX * hm.HZ, 0.0, **{**hm.BASE, **kw})
    tt = np.linspace(0, 100, 1001)
    a = np.interp(tt, t, px); b = np.interp(tt + 100, t, px)
    sc = 1 / np.max(np.abs(np.concatenate([a, b])))
    ax.plot(tt, a * sc, "C0", label="first half: after +pulse")
    ax.plot(tt, b * sc, "C1--", label="second half: after −pulse")
    ax.plot(tt, (a - b) / 2 * sc, "C3", lw=0.9, label="odd part → 5 kHz")
    ax.axhline(0, color="0.6", lw=0.5)
    ax.set_xlabel("time since pulse (µs)"); ax.set_ylabel("P_x (normalized)")
    ax.set_title(title, fontsize=10); ax.legend(fontsize=8, loc="lower right")

# (c) 零点移动
ax = fig.add_subplot(gs[1, 0:2])
cases = [c for c in S["nonideal"] if max(abs(v) for v in c["value"][:2]) == 0.02]
lab = [c["case"] for c in cases]
z2 = [np.hypot(*c["zero2_nT"]) for c in cases]; z1 = [np.hypot(*c["zero1_nT"]) for c in cases]
y = np.arange(len(cases))
ax.barh(y + 0.2, z2, 0.4, color="C0", label="10 kHz (even)")
ax.barh(y - 0.2, z1, 0.4, color="C1", label="5 kHz (odd)")
ax.set_yticks(y, lab, fontsize=8); ax.invert_yaxis()
ax.set_xlabel("zero shift |ΔB⊥| (nT) for ε = 0.02"); ax.legend(fontsize=8)
ax.set_title("(c) zero shift by non-ideal terms", fontsize=10)

# (d) 灵敏度随 Bz
ax = fig.add_subplot(gs[1, 2:4])
sv = S["sensitivity_u_per_nT"]; w0 = np.array(sv["w0_Hz"])
for n, c, l in (("n2", "C0", "10 kHz"), ("n1", "C1", "5 kHz")):
    ax.plot(w0, sv[n]["big"], c, label=l + " max SV")
    ax.plot(w0, sv[n]["small"], c + "--", label=l + " min SV")
ax.axvline(1426, color="k", lw=0.7, ls=":"); ax.text(1460, 0.05, "fitted Bz", fontsize=8)
ax.set_xlabel("residual Bz  w0/2π (Hz)"); ax.set_ylabel("|dc/dB⊥| (u/nT)")
ax.set_title("(d) slope vs residual Bz (6.6 µs)", fontsize=10); ax.legend(fontsize=7)

# (e) 三点求解收敛
ax = fig.add_subplot(gs[1, 4:6])
for p in S["procedure"]:
    if p["case"] == "ideal" and p["d_nT"] in (5, 20):
        e = np.maximum(p["err_nT_per_iter"], 1e-3)
        ax.semilogy(range(1, 5), e, "o-", ms=3, label=f"d={p['d_nT']} nT, noise={p['noise_u']} u")
ax.set_xticks(range(1, 5)); ax.set_xlabel("iteration"); ax.set_ylabel("|error| (nT, floored at 1e-3)")
ax.set_title("(e) 3-point linear solve from 91 nT", fontsize=10); ax.legend(fontsize=7)

fig.savefig(HERE / "figC_even_2f_criterion.png", dpi=130, bbox_inches="tight")
fig.savefig(HERE / "figC_even_2f_criterion.pdf", bbox_inches="tight")
