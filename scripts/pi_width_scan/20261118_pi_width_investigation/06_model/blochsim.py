"""周期 ±脉冲 + Y 射频 + 静态残余场的 Bloch 周期稳态模拟。

时间单位 µs，速率单位 rad/µs（γB 已并入）。dP/dt = P × B − Γ P + Γ P0 ez。
返回 P_x 在 5 kHz 上的复振幅 p，约定 P_x 基频 = Re[p e^{iωt}]。
"""
from __future__ import annotations

import numpy as np
from scipy.linalg import expm

TWO_PI = 2 * np.pi
K_HZ_PER_MA = 653.79
F_RF_MHZ = 0.005             # 5 kHz
PERIOD = 1 / F_RF_MHZ        # 200 µs
OMEGA = TWO_PI * F_RF_MHZ


def pulse_shape(t, width, edge):
    """单个升余弦脉冲（起点 0），与仓库 pulse_waveform 一致。"""
    v = np.zeros_like(t)
    r = (t >= 0) & (t < edge)
    v[r] = 0.5 * (1 - np.cos(np.pi * t[r] / edge))
    f = (t >= edge) & (t < width - edge)
    v[f] = 1.0
    d = (t >= width - edge) & (t < width)
    v[d] = 0.5 * (1 + np.cos(np.pi * (t[d] - (width - edge)) / edge))
    return v


def time_grid(width, fine=0.02, coarse=0.25):
    """脉冲内细步、其余粗步；返回各段边界。"""
    edges = []
    for start in (0.0, PERIOD / 2):
        edges.append(np.arange(start, start + width, fine))
        nxt = start + PERIOD / 2
        edges.append(np.arange(start + width, nxt, coarse))
    t = np.concatenate(edges + [np.array([PERIOD])])
    return np.unique(np.round(t, 9))


def cross_matrix(b):
    """[b]×，b 形状 (n,3)。"""
    z = np.zeros(len(b))
    return np.stack([np.stack([z, -b[:, 2], b[:, 1]], -1),
                     np.stack([b[:, 2], z, -b[:, 0]], -1),
                     np.stack([-b[:, 1], b[:, 0], z], -1)], 1)


def steady_fundamental(width, *, gamma, w0, bx=0.0, by=0.0, rf=0.0, phi=0.0, scale=1.0, current_ma=190.0,
                       edge=1.0, tilt=(0.0, 0.0), rf_delay=0.0, pulses=True, pump=(0.0, 0.0, 1.0)):
    """周期稳态下 P_x 的 5 kHz 复振幅。rf 为射频峰值速率，射频 = rf·cos(ω(t − rf_delay) + φ) 沿 y。"""
    t = time_grid(width if pulses else 1.0)
    dt = np.diff(t)
    tm = t[:-1] + dt / 2
    peak = scale * TWO_PI * K_HZ_PER_MA * current_ma * 1e-6      # rad/µs
    if pulses:
        p = peak * (pulse_shape(tm, width, edge) - pulse_shape(tm - PERIOD / 2, width, edge))
    else:
        p = np.zeros_like(tm)
    rfv = rf * np.cos(OMEGA * (tm - rf_delay) + phi)
    B = np.stack([bx + tilt[0] * p, by + tilt[1] * p + rfv, w0 + p], -1)
    n = len(dt)
    A = -cross_matrix(B) - gamma * np.eye(3)[None]
    aug = np.zeros((n, 4, 4))
    aug[:, :3, :3] = A * dt[:, None, None]
    aug[:, :3, 3] = gamma * np.asarray(pump)[None, :] * dt[:, None]
    M = expm(aug)
    # 一周期仿射映射
    total = np.eye(4)
    for k in range(n):
        total = M[k] @ total
    P0 = np.linalg.solve(np.eye(3) - total[:3, :3], total[:3, 3])
    state = np.empty((n + 1, 3))
    state[0] = P0
    v = np.append(P0, 1.0)
    for k in range(n):
        v = M[k] @ v
        state[k + 1] = v[:3]
    px = state[:, 0]
    # 基频复振幅（梯形积分）：p = (2/T)∫ P_x e^{-iωt} dt
    f = px * np.exp(-1j * OMEGA * t)
    return 2 / PERIOD * np.trapezoid(f, t)


def harmonics(width, **kw):
    """返回 (c0, c+1, c-1)，射频线性区由 φ = 0, 90° 两点求出；pulses=False 时 width 无意义。"""
    rf = kw.pop("rf")
    z_off = steady_fundamental(width, rf=0.0, **kw)
    z0 = steady_fundamental(width, rf=rf, phi=0.0, **kw) - z_off
    z90 = steady_fundamental(width, rf=rf, phi=np.pi / 2, **kw) - z_off
    return z_off, (z0 - 1j * z90) / 2, (z0 + 1j * z90) / 2
