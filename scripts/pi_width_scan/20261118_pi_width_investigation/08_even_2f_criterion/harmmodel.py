"""周期稳态 Bloch 模型：输出整段 P_x 与任意谐波 n·ω 的复振幅。

物理与 scripts/fitmodel.py 的 fundamental() 相同（快 + 慢拖尾 z 场、tilt、slow_tilt、pump），
此处复制一份并扩展输出；原文件不改。fitmodel 与 rf_slow.json 取自同级 06_model/。射频关闭。
单位：时间 µs，场以角频率 rad/µs 表示。
"""
import json
import sys
from pathlib import Path

import numpy as np
from scipy.linalg import expm

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "06_model"))
import fitmodel as fm  # noqa: E402  只读复用 grid/zwave/cross/常数

TWO_PI, PERIOD, OMEGA = fm.TWO_PI, fm.PERIOD, fm.OMEGA
HZ = TWO_PI * 1e-6          # 1 Hz 对应的 rad/µs
GAMMA_HZ_PER_NT = 7.0       # Rb87 F=2

_fit = json.loads((Path(__file__).resolve().parents[1] / "06_model" / "fits" / "rf_slow.json").read_text(encoding="utf-8"))
BASE = dict(gamma=_fit["Gamma_over_2pi_Hz"] * HZ, w0=_fit["w0_over_2pi_Hz"] * HZ, s_f=_fit["fast_fraction"],
            tau_e=_fit["tau_e_us"], width=6.6, current_ma=190.0, pump=(0.0, 0.0, 1.0), tilt=(0.0, 0.0),
            slow_tilt=(0.0, 0.0))
C_V = complex(*_fit["C"])   # 拟合的模型→伏特复增益（含解调约定），仅用于量级换算
FIT_BPERP_HZ = (_fit["bx_over_2pi_Hz"], _fit["by_over_2pi_Hz"])


def simulate(bx, by, width, gamma, w0, s_f, tau_e, current_ma=190.0, pump=(0.0, 0.0, 1.0), tilt=(0.0, 0.0),
             slow_tilt=(0.0, 0.0)):
    """返回 (t, P_x(t))，t 覆盖一个周期（含端点）。"""
    t = fm.grid(width)
    dt = np.diff(t); tm = t[:-1] + dt / 2
    tu, zw = fm.zwave(width, s_f, tau_e)
    peak = TWO_PI * fm.K * current_ma * 1e-6
    pz = peak * np.interp(tm, tu, zw, period=PERIOD)
    if tuple(slow_tilt) != (0.0, 0.0):
        _, fast_only = fm.zwave(width, 1.0, 0.0)
        ps = pz - s_f * peak * np.interp(tm, tu, fast_only, period=PERIOD)
    else:
        ps = 0.0
    B = np.stack([bx + tilt[0] * pz + slow_tilt[0] * ps,
                  by + tilt[1] * pz + slow_tilt[1] * ps + 0 * tm, w0 + pz], -1)
    aug = np.zeros((len(dt), 4, 4))
    aug[:, :3, :3] = (-fm.cross(B) - gamma * np.eye(3)[None]) * dt[:, None, None]
    aug[:, :3, 3] = gamma * np.asarray(pump, dtype=float)[None, :] * dt[:, None]
    M = expm(aug)
    tot = np.eye(4)
    for m in M:
        tot = m @ tot
    v = np.append(np.linalg.solve(np.eye(3) - tot[:3, :3], tot[:3, 3]), 1.0)
    px = np.empty(len(t)); px[0] = v[0]
    for i, m in enumerate(M):
        v = m @ v; px[i + 1] = v[0]
    return t, px


def harmonics(t, px, ns=(1, 2)):
    """复振幅 c_n = (2/T)∫P_x e^{-i nωt} dt（n=1 与 fitmodel.fundamental 同约定）。"""
    return np.array([2 / PERIOD * np.trapezoid(px * np.exp(-1j * n * OMEGA * t), t) for n in ns])


def H(bx_hz, by_hz, ns=(1, 2), **kw):
    p = {**BASE, **kw}
    t, px = simulate(bx_hz * HZ, by_hz * HZ, **p)
    return harmonics(t, px, ns)
