"""拖尾补偿驱动：精确尾参数、周期驱动波形、原子处场与周期稳态 Bloch 模拟。

拖尾模型（06_model/fits/rf_slow.json）：原子处 z 场 = peak·[s_f·I + (1−s_f)·x]，x = LP_τe[I]（一阶低通）。
补偿原理：脉冲结束（t ≥ w）后令 I = −c·x，c = (1−s_f)/s_f，则 z 场 = s_f·I + (1−s_f)·x ≡ 0，
且 x 满足 τe·dx/dt = I − x = −x/s_f，按 τc = s_f·τe 指数衰减，所以尾电流是单指数：
    I_tail(t) = −A·exp(−(t−w)/τc)，A = c·x(w)
x(w) 需在周期稳态下自洽求解（上一个反号脉冲的残余 x 也算进去），见 exact_tail()。
时间 µs；电流归一化到主脉冲平顶 = 1；场以 rad/µs 表示。只做离线计算，不连接仪器。
"""
import json
from pathlib import Path

import numpy as np
from scipy.linalg import expm

HERE = Path(__file__).resolve().parent
FIT = json.loads((HERE.parent / "06_model" / "fits" / "rf_slow.json").read_text(encoding="utf-8"))
TWO_PI = 2 * np.pi
PERIOD = 200.0
HALF = PERIOD / 2
OMEGA = TWO_PI / PERIOD
HZ = TWO_PI * 1e-6
K_HZ_PER_MA = 653.79
NT = 7.0 * HZ                   # 1 nT 对应 rad/µs
EDGE = 1.0
S_F, TAU_E = FIT["fast_fraction"], FIT["tau_e_us"]
GAMMA, W0 = FIT["Gamma_over_2pi_Hz"] * HZ, FIT["w0_over_2pi_Hz"] * HZ
N = 40000
TU = np.arange(N) * PERIOD / N


def pulse(t, width, edge=EDGE):
    """升余弦沿的单个脉冲，平顶 = 1，总宽 width（含两沿）。"""
    x = np.asarray(t, float)
    v = np.zeros_like(x)
    r = (x >= 0) & (x < edge); v[r] = 0.5 * (1 - np.cos(np.pi * x[r] / edge))
    f = (x >= edge) & (x < width - edge); v[f] = 1.0
    d = (x >= width - edge) & (x < width); v[d] = 0.5 * (1 + np.cos(np.pi * (x[d] - width + edge) / edge))
    return v


def lowpass(i, tau):
    """周期一阶低通（FFT，精确周期稳态）。"""
    if tau <= 0:
        return i.copy()
    k = np.fft.rfftfreq(len(i), PERIOD / len(i)) * TWO_PI
    return np.fft.irfft(np.fft.rfft(i) / (1 + 1j * k * tau), len(i))


def drive(width, A=0.0, tau_c=25.0, t=TU, n_wrap=8):
    """周期驱动：+脉冲（t=0）与 −脉冲（t=100 µs），各带 −A·exp 尾；尾按周期延拓叠加。"""
    out = np.zeros_like(np.asarray(t, float))
    for k in range(n_wrap):
        for sgn, t0 in ((1.0, 0.0), (-1.0, HALF)):
            x = (t - t0) % PERIOD + k * PERIOD
            seg = pulse(x, width) if k == 0 else 0.0
            tail = np.where(x >= width, -A * np.exp(-(x - width) / tau_c), 0.0) if A else 0.0
            out = out + sgn * (seg + tail)
    return out


def exact_tail(width, s_f=S_F, tau_e=TAU_E):
    """使脉冲后 z 场严格为零的 (A, τc)。

    线性叠加：每个脉冲连同它自己的尾单独满足"脉冲后 I_k = −c·x_k"，所以各自的场在脉冲后恒为零，
    周期叠加（drive() 把尾延拓到后续半周期）后总场在脉冲窗外也恒为零。
    每个脉冲从零状态出发，A = c·u，u 为脉冲结束时自身的 x。
    """
    tau_c = s_f * tau_e
    t = np.linspace(0, width, 20001)
    p = pulse(t, width)
    u = np.trapezoid(p * np.exp(-(width - t) / tau_e), t) / tau_e
    return (1 - s_f) / s_f * u, tau_c


def fields(width, A=0.0, tau_c=25.0, s_f=S_F, tau_e=TAU_E, current_scale=1.0, i=None):
    """返回 (I, z 场, 慢分量 (1−s_f)·x)，均归一化到平顶 1；i 给出时直接用该驱动。"""
    if i is None:
        i = current_scale * drive(width, A, tau_c)
    slow = (1 - s_f) * lowpass(i, tau_e)
    return i, s_f * i + slow, slow


def time_grid(width):
    seg = []
    for s in (0.0, HALF):
        seg += [np.arange(s, s + width + 2, 0.05), np.arange(s + width + 2, s + HALF, 0.25)]
    return np.unique(np.round(np.concatenate(seg + [np.array([PERIOD])]), 9))


def cross(b):
    z = np.zeros(len(b))
    return np.stack([np.stack([z, -b[:, 2], b[:, 1]], -1), np.stack([b[:, 2], z, -b[:, 0]], -1),
                     np.stack([-b[:, 1], b[:, 0], z], -1)], 1)


def simulate(bx, by, width, A=0.0, tau_c=25.0, *, current_ma=190.0, gamma=GAMMA, w0=W0, s_f=S_F, tau_e=TAU_E,
             slow_tilt=(0.0, 0.0), tilt=(0.0, 0.0), pump=(0.0, 0.0, 1.0), xy_comp=(0.0, 0.0), i=None):
    """周期稳态 P_x(t)。bx、by 以 rad/µs 给出。

    slow_tilt：横向场 = ε·(慢分量)，与 08/06 模型同定义；
    xy_comp：X/Y 线圈叠加的补偿波形 = κ·(慢分量)（κ = −slow_tilt 时完全抵消）。
    """
    t = time_grid(width); dt = np.diff(t); tm = t[:-1] + dt / 2
    _, z, slow = fields(width, A, tau_c, s_f, tau_e, i=i)
    peak = TWO_PI * K_HZ_PER_MA * current_ma * 1e-6
    pz = peak * np.interp(tm, TU, z, period=PERIOD)
    ps = peak * np.interp(tm, TU, slow, period=PERIOD)
    fz = pz - ps
    ex = slow_tilt[0] + xy_comp[0]; ey = slow_tilt[1] + xy_comp[1]
    B = np.stack([bx + tilt[0] * fz + ex * ps, by + tilt[1] * fz + ey * ps + 0 * tm, w0 + pz], -1)
    aug = np.zeros((len(dt), 4, 4))
    aug[:, :3, :3] = (-cross(B) - gamma * np.eye(3)[None]) * dt[:, None, None]
    aug[:, :3, 3] = gamma * np.asarray(pump, float)[None, :] * dt[:, None]
    M = expm(aug)
    tot = np.eye(4)
    for m in M:
        tot = m @ tot
    v = np.append(np.linalg.solve(np.eye(3) - tot[:3, :3], tot[:3, 3]), 1.0)
    px = np.empty(len(t)); px[0] = v[0]
    for k, m in enumerate(M):
        v = m @ v; px[k + 1] = v[0]
    return t, px


def harm(t, px, ns=(1, 2)):
    """c_n = (2/T)∫P_x e^{−inωt} dt（峰值复振幅）。"""
    return np.array([2 / PERIOD * np.trapezoid(px * np.exp(-1j * n * OMEGA * t), t) for n in ns])


def response(width, A=0.0, tau_c=25.0, d_nt=2.0, b0=(0.0, 0.0), **kw):
    """在 b0 (nT) 处的 c(b0) 与对 Bx/By 的斜率（每 nT）；返回 c0[n], jx[n], jy[n]，n 对应 (1f, 2f)。"""
    bx0, by0 = b0[0] * NT, b0[1] * NT
    h = lambda dx, dy: harm(*simulate(bx0 + dx * NT, by0 + dy * NT, width, A, tau_c, **kw))
    c0 = h(0, 0)
    jx = (h(d_nt, 0) - h(-d_nt, 0)) / (2 * d_nt)
    jy = (h(0, d_nt) - h(0, -d_nt)) / (2 * d_nt)
    return c0, jx, jy


def rotation(width, A=0.0, tau_c=25.0, current_ma=190.0, s_f=S_F, tau_e=TAU_E, upto=None, i=None):
    """+脉冲半周期内 z 场积分的转角 / π（不含残余 w0）；upto 给出只积分到的时刻 (µs)。"""
    _, z, _ = fields(width, A, tau_c, s_f, tau_e, i=i)
    peak = TWO_PI * K_HZ_PER_MA * current_ma * 1e-6
    m = TU < (HALF if upto is None else upto)
    return peak * np.trapezoid(z[m], TU[m]) / np.pi
