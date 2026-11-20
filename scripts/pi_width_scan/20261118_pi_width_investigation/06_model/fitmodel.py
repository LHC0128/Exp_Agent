"""用周期稳态 Bloch 模型拟合相位 × 脉宽网格的 c0、c+1、c-1。

z 向场 = 残余 w0 + 峰值速率 × [s_f·p(t) + (1 − s_f)·L(t)]，
p(t) 为归一化 ±升余弦脉冲，L 为 p 的周期一阶低通（时间常数 τe）：屏蔽/涡流慢响应。
s_f = 1 即无慢响应（全部按直流线圈常数瞬时作用）。
复增益 C 对每组参数解析求出；射频与静态横场的比例由 r 给出（射频处于线性区）。

用法：python fitmodel.py <grid.npz> <输出前缀> [conj]
"""
import json
import sys
import time

import numpy as np
from scipy.linalg import expm
from scipy.optimize import least_squares

TWO_PI = 2 * np.pi
K = 653.79
PERIOD, OMEGA = 200.0, TWO_PI / 200.0
EDGE = 1.0
RF_SIM = 1e-5


def pulse_shape(t, width, edge=EDGE):
    v = np.zeros_like(t)
    r = (t >= 0) & (t < edge); v[r] = 0.5 * (1 - np.cos(np.pi * t[r] / edge))
    f = (t >= edge) & (t < width - edge); v[f] = 1.0
    d = (t >= width - edge) & (t < width); v[d] = 0.5 * (1 + np.cos(np.pi * (t[d] - (width - edge)) / edge))
    return v


def zwave(width, s_f, tau_e, n=40000):
    """返回在均匀网格上的归一化 z 场波形（快 + 慢）。"""
    tu = np.arange(n) * PERIOD / n
    p = pulse_shape(tu, width) - pulse_shape(tu - PERIOD / 2, width)
    if s_f >= 1.0 or tau_e <= 0:
        return tu, p
    k = np.fft.rfftfreq(n, PERIOD / n) * TWO_PI
    L = np.fft.irfft(np.fft.rfft(p) / (1 + 1j * k * tau_e), n)
    return tu, s_f * p + (1 - s_f) * L


def grid(width, fine=0.05, coarse=0.5):
    seg = []
    for s in (0.0, PERIOD / 2):
        seg += [np.arange(s, s + width, fine), np.arange(s + width, s + PERIOD / 2, coarse)]
    return np.unique(np.round(np.concatenate(seg + [np.array([PERIOD])]), 9))


def cross(b):
    z = np.zeros(len(b))
    return np.stack([np.stack([z, -b[:, 2], b[:, 1]], -1), np.stack([b[:, 2], z, -b[:, 0]], -1),
                     np.stack([-b[:, 1], b[:, 0], z], -1)], 1)


def fundamental(width, gamma, w0, bx, by, s_f, tau_e, rf, phi, current_ma=190.0, pump=(0.0, 0.0, 1.0), tilt=(0.0, 0.0),
                slow_tilt=(0.0, 0.0)):
    t = grid(width)
    dt = np.diff(t); tm = t[:-1] + dt / 2
    tu, zw = zwave(width, s_f, tau_e)
    peak = TWO_PI * K * current_ma * 1e-6
    pz = peak * np.interp(tm, tu, zw, period=PERIOD)
    if slow_tilt != (0.0, 0.0):
        _, fast_only = zwave(width, 1.0, 0.0)
        ps = pz - s_f * peak * np.interp(tm, tu, fast_only, period=PERIOD)     # 只取慢分量
    else:
        ps = 0.0
    B = np.stack([bx + tilt[0] * pz + slow_tilt[0] * ps,
                  by + tilt[1] * pz + slow_tilt[1] * ps + rf * np.cos(OMEGA * tm + phi), w0 + pz], -1)
    n = len(dt)
    aug = np.zeros((n, 4, 4))
    aug[:, :3, :3] = (-cross(B) - gamma * np.eye(3)[None]) * dt[:, None, None]
    aug[:, :3, 3] = gamma * np.asarray(pump, dtype=float)[None, :] * dt[:, None]
    M = expm(aug)
    tot = np.eye(4)
    for m in M:
        tot = m @ tot
    v = np.append(np.linalg.solve(np.eye(3) - tot[:3, :3], tot[:3, 3]), 1.0)
    px = np.empty(n + 1); px[0] = v[0]
    for i, m in enumerate(M):
        v = m @ v; px[i + 1] = v[0]
    return 2 / PERIOD * np.trapezoid(px * np.exp(-1j * OMEGA * t), t)


def model(theta, widths, conj):
    lg, w0, bx, by, s_f, ltau, phi_off = theta
    g, tau = np.exp(lg), np.exp(ltau)
    out = []
    for w in widths:
        z0 = fundamental(w, g, w0, bx, by, s_f, tau, 0.0, 0.0)
        za = fundamental(w, g, w0, bx, by, s_f, tau, RF_SIM, phi_off) - z0
        zb = fundamental(w, g, w0, bx, by, s_f, tau, RF_SIM, phi_off + np.pi / 2) - z0
        cp, cm = (za - 1j * zb) / 2 / RF_SIM, (za + 1j * zb) / 2 / RF_SIM
        if conj:  # 仪器取共轭约定：Z = C·conj(p(−φ))，c+1 ↔ conj(c+1)，c0 ↔ conj(c0)
            z0, cp, cm = np.conj(z0), np.conj(cp), np.conj(cm)
        out.append((z0, cp, cm))
    return np.array(out)      # (nw, 3)


FIX = {}
RFONLY = False


def expand(theta_r):
    """把被固定的参数插回完整向量（索引 5 = log τe）。"""
    x = list(theta_r)
    for idx in sorted(FIX):
        x.insert(idx, FIX[idx])
    return np.array(x)


def residuals(theta_r, widths, data, conj, cache):
    theta_r = expand(theta_r)
    theta, log_r = theta_r[:-1], theta_r[-1]
    m = model(theta, widths, conj)
    m[:, 1:] *= np.exp(log_r)
    cols = slice(1, 3) if RFONLY else slice(0, 3)
    mm, dd = m[:, cols], data[:, cols]
    C = np.vdot(mm.ravel(), dd.ravel()) / np.vdot(mm.ravel(), mm.ravel())
    res = (C * mm - dd).ravel()
    cache["C"], cache["m"] = C, C * m
    return np.concatenate([res.real, res.imag])


def main():
    global RFONLY
    d = np.load(sys.argv[1]); prefix = sys.argv[2]; conj = "conj" in sys.argv[3:]
    seeds, sf0 = [], None
    for arg in sys.argv[3:]:
        if arg.startswith("fixtau="):
            FIX[5] = np.log(float(arg.split("=", 1)[1]))
        elif arg == "rfonly":
            RFONLY = True
        elif arg.startswith("sf0="):
            sf0 = float(arg.split("=", 1)[1])
        elif arg.startswith("seed="):
            seeds.append(list(json.loads(open(arg.split("=", 1)[1], encoding="utf-8").read())["x"]))
    sel = np.arange(0, len(d["w"]), 2)
    widths = d["w"][sel]
    data = np.stack([d["c0"][sel], d["cp"][sel], d["cm"][sel]], 1)
    best = None
    starts = seeds + [
        # log Γ, w0, bx, by, s_f, log τe, φoff, log r
        [np.log(TWO_PI * 4e-4), TWO_PI * 1.3e-3, TWO_PI * 3e-4, TWO_PI * 1e-4, 0.75, np.log(300.0), 0.0, np.log(3.0)],
        [np.log(TWO_PI * 4e-4), -TWO_PI * 1.3e-3, TWO_PI * 3e-4, -TWO_PI * 1e-4, 0.75, np.log(300.0), 1.5, np.log(3.0)],
        [np.log(TWO_PI * 3e-4), TWO_PI * 1.0e-3, -TWO_PI * 3e-4, TWO_PI * 1e-4, 0.75, np.log(50.0), 3.0, np.log(3.0)],
        [np.log(TWO_PI * 5e-4), TWO_PI * 1.5e-3, TWO_PI * 3e-4, TWO_PI * 2e-4, 0.999, np.log(1.0), -1.5, np.log(3.0)],
    ]
    lo = [np.log(TWO_PI * 2e-5), -TWO_PI * 5e-3, -TWO_PI * 5e-3, -TWO_PI * 5e-3, 0.3, np.log(0.5), -np.pi * 2, -5]
    hi = [np.log(TWO_PI * 5e-3), TWO_PI * 5e-3, TWO_PI * 5e-3, TWO_PI * 5e-3, 1.0, np.log(1e5), np.pi * 2, 8]
    if sf0 is not None:
        for x0 in starts:
            x0[4] = sf0
    for x0 in starts:
        for idx, val in FIX.items():
            x0[idx] = val
        x0[:] = list(np.clip(x0, np.array(lo) + 1e-9, np.array(hi) - 1e-9))
    free = [i for i in range(len(lo)) if i not in FIX]
    lo = [lo[i] for i in free]; hi = [hi[i] for i in free]
    starts = [[x0[i] for i in free] for x0 in starts]
    for x0 in starts:
        cache = {}
        t0 = time.time()
        r = least_squares(residuals, x0, args=(widths, data, conj, cache), bounds=(lo, hi), x_scale="jac",
                          diff_step=1e-3, max_nfev=200)
        rms = np.sqrt(np.mean(r.fun ** 2))
        print(f"start -> rms {rms:.3f} mV, {time.time()-t0:.0f}s, x = {np.round(r.x, 4).tolist()}", flush=True)
        if best is None or rms < best[0]:
            best = (rms, r.x.copy(), cache["C"])
    rms, x, C = best
    x = expand(x)
    lg, w0, bx, by, s_f, ltau, phi_off, lr = x
    summary = dict(conj=conj, rms_mV=rms, Gamma_over_2pi_Hz=np.exp(lg) / TWO_PI * 1e6, w0_over_2pi_Hz=w0 / TWO_PI * 1e6,
                   bx_over_2pi_Hz=bx / TWO_PI * 1e6, by_over_2pi_Hz=by / TWO_PI * 1e6, fast_fraction=s_f,
                   tau_e_us=np.exp(ltau), phi_off_deg=np.degrees(phi_off), rf_ratio=np.exp(lr),
                   C=[C.real, C.imag], x=x.tolist())
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    with open(prefix + ".json", "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, ensure_ascii=False)
    # 全网格预测
    m = model(x[:-1], d["w"], conj); m[:, 1:] *= np.exp(lr); m *= C
    np.savez(prefix + ".npz", w=d["w"], model=m, data=np.stack([d["c0"], d["cp"], d["cm"]], 1))


if __name__ == "__main__":
    main()

