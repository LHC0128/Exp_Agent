"""拖尾补偿驱动的周期稳态 Bloch 模型。

驱动电流（归一化到主脉冲峰值 1）= ±π 升余弦主脉冲 + 脉冲结束后的反号指数尾
    I(t) = pulse(t) − A·exp(−(t − w)/τc)·[t ≥ w]  （−π 半周期取相反号）
原子处 z 场 = peak·[s_f·I + (1 − s_f)·LP_τe(I)]；slow_tilt 只作用在慢分量 (1 − s_f)·LP_τe(I) 上。
参数取 06_model/fits/rf_slow.json。时间 µs，场 rad/µs。
"""
import json
from pathlib import Path

import numpy as np
from scipy.linalg import expm

HERE = Path(__file__).resolve().parent
FIT = json.loads((HERE.parent / "06_model" / "fits" / "rf_slow.json").read_text(encoding="utf-8"))
TWO_PI = 2 * np.pi
PERIOD = 200.0
OMEGA = TWO_PI / PERIOD
HZ = TWO_PI * 1e-6
K = 653.79                     # Hz/mA
NT = 7.0 * HZ                  # 1 nT
EDGE = 1.0
S_F, TAU_E = FIT["fast_fraction"], FIT["tau_e_us"]
BASE = dict(gamma=FIT["Gamma_over_2pi_Hz"] * HZ, w0=FIT["w0_over_2pi_Hz"] * HZ, s_f=S_F, tau_e=TAU_E,
            current_ma=190.0)
N = 40000
TU = np.arange(N) * PERIOD / N


def pulse(t, width, edge=EDGE):
    x = np.asarray(t, float)
    v = np.zeros_like(x)
    r = (x >= 0) & (x < edge); v[r] = 0.5 * (1 - np.cos(np.pi * x[r] / edge))
    f = (x >= edge) & (x < width - edge); v[f] = 1.0
    d = (x >= width - edge) & (x < width); v[d] = 0.5 * (1 + np.cos(np.pi * (x[d] - width + edge) / edge))
    return v


def drive(width, A=0.0, tau_c=25.0, t=TU):
    """一个周期的归一化驱动电流（与 DG 任意波同形）。"""
    def half(x):
        tail = np.where(x >= width, -A * np.exp(-(x - width) / tau_c), 0.0) if A else 0.0
        return pulse(x, width) + tail
    x1 = t % PERIOD
    x2 = (t - PERIOD / 2) % PERIOD
    # 尾跨过半周期时按周期叠加：用 x 在 [0, PERIOD) 内的单边指数，近似足够（τc ≪ 100 µs 时残余 < e^-4）
    return half(np.where(x1 < PERIOD, x1, 0)) * (x1 < PERIOD / 2 + 0) + 0 - half(x2) * (x2 < PERIOD / 2) \
        + _wrap(width, A, tau_c, x1, x2)


def _wrap(width, A, tau_c, x1, x2):
    """把落到另一半周期的指数尾补上（周期延拓）。"""
    if not A:
        return 0.0
    out = np.zeros_like(x1)
    m1 = x1 >= PERIOD / 2          # +π 的尾延伸进后半周期
    out[m1] += -A * np.exp(-(x1[m1] - width) / tau_c)
    m2 = x2 >= PERIOD / 2          # −π 的尾延伸进下一周期前半
    out[m2] -= -A * np.exp(-(x2[m2] - width) / tau_c)
    return out


def lowpass(i, tau):
    k = np.fft.rfftfreq(N, PERIOD / N) * TWO_PI
    return np.fft.irfft(np.fft.rfft(i) / (1 + 1j * k * tau), N)


def fields(width, A=0.0, tau_c=25.0, s_f=S_F, tau_e=TAU_E):
    """返回 (I, 原子处 z 场归一化, 慢分量归一化)。"""
    i = drive(width, A, tau_c)
    slow = (1 - s_f) * lowpass(i, tau_e) if s_f < 1 else np.zeros_like(i)
    return i, s_f * i + slow, slow


def time_grid(width, A):
    seg = []
    end = width + (60 if A else 0)
    for s in (0.0, PERIOD / 2):
        seg += [np.arange(s, s + width + 2, 0.05), np.arange(s + width + 2, s + PERIOD / 2, 0.25)]
    return np.unique(np.round(np.concatenate(seg + [np.array([PERIOD])]), 9))


def cross(b):
    z = np.zeros(len(b))
    return np.stack([np.stack([z, -b[:, 2], b[:, 1]], -1), np.stack([b[:, 2], z, -b[:, 0]], -1),
                     np.stack([-b[:, 1], b[:, 0], z], -1)], 1)


def simulate(bx, by, width, A=0.0, tau_c=25.0, gamma=BASE["gamma"], w0=BASE["w0"], s_f=S_F, tau_e=TAU_E,
             current_ma=190.0, slow_tilt=(0.0, 0.0), tilt=(0.0, 0.0), pump=(0.0, 0.0, 1.0)):
    """(t, Px)；bx, by 以 rad/µs 给出。"""
    t = time_grid(width, A); dt = np.diff(t); tm = t[:-1] + dt / 2
    _, z, slow = fields(width, A, tau_c, s_f, tau_e)
    peak = TWO_PI * K * current_ma * 1e-6
    pz = peak * np.interp(tm, TU, z, period=PERIOD)
    ps = peak * np.interp(tm, TU, slow, period=PERIOD)
    fz = pz - ps
    B = np.stack([bx + tilt[0] * fz + slow_tilt[0] * ps, by + tilt[1] * fz + slow_tilt[1] * ps + 0 * tm, w0 + pz], -1)
    aug = np.zeros((len(dt), 4, 4))
    aug[:, :3, :3] = (-cross(B) - gamma * np.eye(3)[None]) * dt[:, None, None]
    aug[:, :3, 3] = gamma * np.asarray(pump, float)[None, :] * dt[:, None]
    M = expm(aug)
    tot = np.eye(4)
    for m in M:
        tot = m @ tot
    v = np.append(np.linalg.solve(np.eye(3) - tot[:3, :3], tot[:3, 3]), 1.0)
    px = np.empty(len(t)); px[0] = v[0]
    for i, m in enumerate(M):
        v = m @ v; px[i + 1] = v[0]
    return t, px


def harm(t, px, ns=(1, 2)):
    return np.array([2 / PERIOD * np.trapezoid(px * np.exp(-1j * n * OMEGA * t), t) for n in ns])


def response(width, A=0.0, tau_c=25.0, d_nt=2.0, **kw):
    """c(0)、对 Bx/By 的斜率（每 nT），下标 [n=1, n=2]。"""
    c0 = harm(*simulate(0.0, 0.0, width, A, tau_c, **kw))
    jx = (harm(*simulate(d_nt * NT, 0, width, A, tau_c, **kw)) - harm(*simulate(-d_nt * NT, 0, width, A, tau_c, **kw))) / (2 * d_nt)
    jy = (harm(*simulate(0, d_nt * NT, width, A, tau_c, **kw)) - harm(*simulate(0, -d_nt * NT, width, A, tau_c, **kw))) / (2 * d_nt)
    return c0, jx, jy


def ideal_tail(width, s_f=S_F, tau_e=TAU_E):
    """使脉冲后 z 场为零的尾：A = (1−s_f)/s_f · LP(I)(w)，τc = s_f·τe（不含尾时的 LP 近似）。"""
    i = drive(width, 0.0)
    lp = lowpass(i, tau_e)
    return (1 - s_f) / s_f * float(np.interp(width, TU, lp)), s_f * tau_e


def rotation_angle(width, A=0.0, tau_c=25.0, current_ma=190.0, window=None):
    """+π 半周期内 z 场的转角（rad）；window 给出只积分到的时刻。"""
    _, z, _ = fields(width, A, tau_c)
    peak = TWO_PI * K * current_ma * 1e-6
    m = TU < (PERIOD / 2 if window is None else window)
    return peak * np.trapezoid(z[m], TU[m])
