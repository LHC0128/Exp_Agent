"""2f 偶部判据离线验证：零点位置、不理想因素、灵敏度、三点线性求解流程。

用法：python verify.py   （输出写到本目录 outputs/、figures/）
"""
import json
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import harmmodel as hm

OUT = Path(__file__).resolve().parent
(OUT / "outputs").mkdir(exist_ok=True); (OUT / "figures").mkdir(exist_ok=True)
NT = hm.GAMMA_HZ_PER_NT          # Hz per nT
# 拟合增益 C 由射频通道定（r 顶到上界），不约束静态场响应的绝对 mV，故用相对单位：
# 1 u = 基准条件（6.6 µs、w0=1426 Hz、慢拖尾）下 5 kHz 对 Bx 的斜率 × 1 nT
MV = 1.0 / (abs((hm.H(5.0, 0, ns=(1,))[0] - hm.H(-5.0, 0, ns=(1,))[0]) / 10.0) * NT)
LOG = []


def log(s=""):
    print(s, flush=True); LOG.append(s)


def zero(n, iters=6, start=(210.0, -140.0), **kw):
    """Newton 求 c_n(bx,by)=0（实 2×2），返回 (bx,by) Hz 与残余 |c_n|。"""
    b = np.array(start, float); d = 5.0
    for _ in range(iters):
        f0 = hm.H(*b, ns=(n,), **kw)[0]
        jx = (hm.H(b[0] + d, b[1], ns=(n,), **kw)[0] - hm.H(b[0] - d, b[1], ns=(n,), **kw)[0]) / (2 * d)
        jy = (hm.H(b[0], b[1] + d, ns=(n,), **kw)[0] - hm.H(b[0], b[1] - d, ns=(n,), **kw)[0]) / (2 * d)
        A = np.array([[jx.real, jy.real], [jx.imag, jy.imag]])
        b = b - np.linalg.lstsq(A, [f0.real, f0.imag], rcond=1e-9)[0]
    res = abs(hm.H(*b, ns=(n,), **kw)[0])
    cond = np.linalg.cond(A)
    return b, res, cond


def slopes(n, **kw):
    d = 5.0
    jx = (hm.H(d, 0, ns=(n,), **kw)[0] - hm.H(-d, 0, ns=(n,), **kw)[0]) / (2 * d)
    jy = (hm.H(0, d, ns=(n,), **kw)[0] - hm.H(0, -d, ns=(n,), **kw)[0]) / (2 * d)
    A = np.array([[jx.real, jy.real], [jx.imag, jy.imag]])
    s = np.linalg.svd(A, compute_uv=False)
    return jx, jy, s      # 每 Hz 的模型单位


summary = {}

# ---------- 1. 理想条件下零点位置 ----------
log("## 1. 理想条件零点（单位 nT；n=2 偶部，n=1 奇部作对照）")
rows = []
for sf in (hm.BASE["s_f"], 1.0):
    for w0 in (0.0, 700.0, 1426.0, 3000.0):
        for width in (5.0, 6.6, 8.0):
            kw = dict(s_f=sf, w0=w0 * hm.HZ, width=width)
            b2, r2, c2 = zero(2, **kw)
            b1, r1, c1 = zero(1, **kw)
            rows.append(dict(s_f=sf, w0_Hz=w0, width=width, zero2_nT=(b2 / NT).tolist(), cond2=c2,
                             zero1_nT=(b1 / NT).tolist(), cond1=c1))
            log(f"s_f={sf:.2f} w0={w0:6.0f} w={width:3.1f}: 2f zero=({b2[0]/NT:+.2e},{b2[1]/NT:+.2e}) cond={c2:7.1f} | "
                f"1f zero=({b1[0]/NT:+.2e},{b1[1]/NT:+.2e}) cond={c1:7.1f}")
summary["ideal"] = rows

# ---------- 2. 不理想因素 ----------
log("\n## 2. 不理想因素引起的零点移动（nT），基准 w0=1426 Hz, s_f=0.544, w=6.6 µs")
cases = []
for name, key, vals in [("slow_tilt x", "slow_tilt", [(0.02, 0), (0.05, 0)]), ("slow_tilt y", "slow_tilt", [(0, 0.02), (0, 0.05)]),
                        ("tilt x", "tilt", [(0.02, 0), (0.05, 0)]), ("tilt y", "tilt", [(0, 0.02), (0, 0.05)]),
                        ("pump x", "pump", [(0.02, 0, 1), (0.05, 0, 1)]), ("pump y", "pump", [(0, 0.02, 1), (0, 0.05, 1)])]:
    for v in vals:
        kw = {key: v}
        b2, _, _ = zero(2, **kw); b1, _, _ = zero(1, **kw)
        eps = max(abs(x) for x in v[:2])
        cases.append(dict(case=name, value=list(v), zero2_nT=(b2 / NT).tolist(), zero1_nT=(b1 / NT).tolist()))
        log(f"{name:12s} ε={eps:.2f}: 2f zero=({b2[0]/NT:+7.2f},{b2[1]/NT:+7.2f}) nT  |b|={np.hypot(*b2)/NT:6.2f}   "
            f"1f zero=({b1[0]/NT:+7.2f},{b1[1]/NT:+7.2f}) |b|={np.hypot(*b1)/NT:6.2f}")
# 不理想因素随 Bz 的依赖（ε=0.02）
log("\n不理想因素零点移动 |b| (nT) 随 w0（ε=0.02）：")
dep = {}
for name, key, v in [("slow_tilt", "slow_tilt", (0.02, 0)), ("tilt", "tilt", (0.02, 0)), ("pump", "pump", (0.02, 0, 1))]:
    line = []
    for w0 in (0.0, 700.0, 1426.0, 3000.0):
        b2, _, _ = zero(2, w0=w0 * hm.HZ, **{key: v})
        line.append(np.hypot(*b2) / NT)
    dep[name] = line
    log(f"  {name:10s} " + "  ".join(f"w0={w:4.0f}:{x:6.2f}" for w, x in zip((0, 700, 1426, 3000), line)))
summary["nonideal"] = cases; summary["nonideal_vs_w0"] = dep

# ---------- 3. 灵敏度 ----------
log("\n## 3. 斜率 |∂c/∂B⊥|（u/nT，u 见脚本头；给出 2×2 实雅可比的大/小奇异值）")
w0s = np.linspace(0, 3000, 31)
sens = {n: {"big": [], "small": []} for n in (1, 2)}
for w0 in w0s:
    for n in (1, 2):
        _, _, s = slopes(n, w0=w0 * hm.HZ)
        sens[n]["big"].append(s[0] * NT * MV); sens[n]["small"].append(s[1] * NT * MV)
for i in range(0, len(w0s), 5):
    log(f"w0={w0s[i]:5.0f} Hz: 2f {sens[2]['big'][i]:.3f}/{sens[2]['small'][i]:.3f}   1f {sens[1]['big'][i]:.3f}/{sens[1]['small'][i]:.3f}")
summary["sensitivity_u_per_nT"] = dict(w0_Hz=w0s.tolist(), **{f"n{n}": sens[n] for n in (1, 2)})
fig, ax = plt.subplots(figsize=(6, 4))
for n, c in ((2, "C0"), (1, "C1")):
    lab = "10 kHz (even)" if n == 2 else "5 kHz (odd)"
    ax.plot(w0s, sens[n]["big"], c, label=lab + " max SV"); ax.plot(w0s, sens[n]["small"], c + "--", label=lab + " min SV")
ax.axvline(1426, color="k", lw=0.8, ls=":")
ax.set_xlabel("residual Bz  w0/2π (Hz)"); ax.set_ylabel("|dc/dB⊥|  (u/nT; u = baseline 5 kHz Bx slope x 1 nT)")
ax.set_yscale("log"); ax.legend(fontsize=8); ax.set_title("Slope vs residual Bz (width 6.6 µs, slow tail on)")
fig.tight_layout(); fig.savefig(OUT / "figures" / "sensitivity_vs_bz.png", dpi=130); plt.close(fig)

log("\n斜率随脉宽（w0=1426 Hz，慢拖尾），大/小奇异值 u/nT：")
ws = np.arange(4.0, 10.01, 0.25); wsens = {1: [], 2: []}
for w in ws:
    for n in (1, 2):
        wsens[n].append((slopes(n, width=w)[2] * NT * MV).tolist())
    log(f"w={w:5.2f}: 2f {wsens[2][-1][0]:.3f}/{wsens[2][-1][1]:.3f}   1f {wsens[1][-1][0]:.3f}/{wsens[1][-1][1]:.3f}")
summary["sensitivity_vs_width"] = dict(width=ws.tolist(), n1=wsens[1], n2=wsens[2])
fig, ax = plt.subplots(figsize=(6, 4))
for n, c in ((2, "C0"), (1, "C1")):
    a = np.array(wsens[n]); lab = "10 kHz (even)" if n == 2 else "5 kHz (odd)"
    ax.plot(ws, a[:, 0], c, label=lab + " max SV"); ax.plot(ws, a[:, 1], c + "--", label=lab + " min SV")
ax.set_yscale("log"); ax.set_xlabel("pulse width (µs)"); ax.set_ylabel("|dc/dB⊥| (u/nT)")
ax.set_title("Slope vs pulse width (w0/2π = 1426 Hz, slow tail on)"); ax.legend(fontsize=8)
fig.tight_layout(); fig.savefig(OUT / "figures" / "sensitivity_vs_width.png", dpi=130); plt.close(fig)

# ---------- 2D 图：|c2| 与 |c1| 在 (bx,by) 网格 ----------
g = np.linspace(-60, 60, 25) * NT
fig, axs = plt.subplots(2, 3, figsize=(12, 7))
for col, (title, kw) in enumerate([("ideal", {}), ("slow_tilt=(0.05,0)", dict(slow_tilt=(0.05, 0))),
                                   ("pump=(0.05,0,1)", dict(pump=(0.05, 0, 1)))]):
    for row, n in enumerate((2, 1)):
        Z = np.array([[abs(hm.H(x, y, ns=(n,), **kw)[0]) * MV for x in g] for y in g])
        im = axs[row, col].pcolormesh(g / NT, g / NT, Z, shading="auto")
        axs[row, col].contour(g / NT, g / NT, Z, 8, colors="w", linewidths=0.5)
        axs[row, col].plot(0, 0, "r+", ms=10)
        axs[row, col].set_title(f"|c{n}| ({'10' if n == 2 else '5'} kHz), {title}", fontsize=9)
        axs[row, col].set_xlabel("Bx (nT)"); axs[row, col].set_ylabel("By (nT)")
        fig.colorbar(im, ax=axs[row, col], label="u")
fig.tight_layout(); fig.savefig(OUT / "figures" / "zero_maps.png", dpi=110); plt.close(fig)

# ---------- 时域 P_x ----------
fig, ax = plt.subplots(figsize=(8, 4))
for b, lab in [((20 * NT, 0), "Bx=20 nT"), ((0, 20 * NT), "By=20 nT")]:
    t, px = hm.simulate(b[0] * hm.HZ, b[1] * hm.HZ, **hm.BASE)
    ax.plot(t, px * MV, label=lab)
    tt = np.linspace(0, 200, 2001); p = np.interp(tt, t, px)
    sh = np.interp((tt + 100) % 200, t, px)
    ax.plot(tt, (p + sh) / 2 * MV, "--", lw=0.8, label=lab + " even part")
ax.set_xlabel("t (µs)"); ax.set_ylabel("P_x (u)"); ax.legend(fontsize=8)
ax.set_title("Time-domain P_x, baseline parameters"); fig.tight_layout()
fig.savefig(OUT / "figures" / "px_time.png", dpi=130); plt.close(fig)

# ---------- 4. 三点线性求解流程 ----------
log("\n## 4. 三点线性求解：每轴 {-d,0,+d} 偏置，回归复斜率，解零点；真值取拟合 B⊥ 并叠加不理想因素")
rng = np.random.default_rng(1)


def procedure(b_true_hz, d_nT, noise_u, n=2, kw=None, b_start=(0.0, 0.0)):
    kw = kw or {}
    est = np.array(b_start, float)    # 当前已加的补偿（Hz）
    hist = []
    for it in range(4):
        def meas(dx, dy):
            z = hm.H(b_true_hz[0] + est[0] + dx, b_true_hz[1] + est[1] + dy, ns=(n,), **kw)[0] * MV
            return z + noise_u * (rng.normal() + 1j * rng.normal()) / np.sqrt(2)
        d = d_nT * NT
        xs = np.array([-d, 0, d])
        zx = np.array([meas(x, 0) for x in xs]); zy = np.array([meas(0, y) for y in xs])
        jx = np.polyfit(xs, zx.real, 1)[0] + 1j * np.polyfit(xs, zx.imag, 1)[0]
        jy = np.polyfit(xs, zy.real, 1)[0] + 1j * np.polyfit(xs, zy.imag, 1)[0]
        z0 = (zx.mean() + zy.mean()) / 2     # 线性模型下均值 = 中心值
        A = np.array([[jx.real, jy.real], [jx.imag, jy.imag]])
        est = est - np.linalg.solve(A, [z0.real, z0.imag])
        # 真零点（含不理想因素）
        hist.append(est.copy())
    return hist


true_zero = {}
for label, kw in [("ideal", {}), ("slow_tilt 0.02", dict(slow_tilt=(0.02, 0))), ("pump 0.02", dict(pump=(0.02, 0, 1)))]:
    bz, _, _ = zero(2, **kw); true_zero[label] = bz
proc = []
b_true = np.array(hm.FIT_BPERP_HZ)    # 起点残余 B⊥：取拟合值（约 −48/+77 nT；拟合中只作量级参考）
for label, kw in [("ideal", {}), ("slow_tilt 0.02", dict(slow_tilt=(0.02, 0))), ("pump 0.02", dict(pump=(0.02, 0, 1)))]:
    target = -b_true + true_zero[label]          # 应加的补偿
    for d_nT in (5, 20, 50):
        for noise in (0.0, 0.1, 0.5):
            errs = []
            for rep in range(20 if noise else 1):
                hist = procedure(b_true, d_nT, noise, kw=kw)
                errs.append([np.hypot(*(h - target)) / NT for h in hist])
            e = np.sqrt(np.mean(np.square(errs), 0))   # rms
            proc.append(dict(case=label, d_nT=d_nT, noise_u=noise, err_nT_per_iter=e.tolist()))
            log(f"{label:15s} d={d_nT:3d} nT noise={noise:4.2f} u: |err| per iter (nT) = " + " ".join(f"{x:7.3f}" for x in e))
summary["procedure"] = proc
log(f"\n起点残余 |B⊥| = {np.hypot(*b_true)/NT:.1f} nT（拟合值）")

(OUT / "outputs" / "summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False), encoding="utf-8")
(OUT / "outputs" / "verify_log.txt").write_text("\n".join(LOG), encoding="utf-8")
