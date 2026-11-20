"""离线试算：用现有 |I/V| 与相位分离「电感滞后」和「固定时间延迟」。只读运行目录数据。"""
from lab_workflows.common import data_root
import json
import math
import numpy as np
from pathlib import Path

RUN = (data_root() / "coil_drive_characterization/1114_114241_coil_drive/results/transfer.json")
rows = json.loads(RUN.read_text(encoding="utf-8"))["transfer"]
f = np.array([r["frequency_hz"] for r in rows])
mag = np.array([r["admittance_a_per_v"] for r in rows]) * 1e3      # mA/V
phase = np.deg2rad(np.array([r["phase_deg"] for r in rows]))       # rad
w = 2 * np.pi * f


def residuals(params, kind):
    """返回 (相对幅度残差, 相位残差/deg)，按模型计算。"""
    if kind == "rl_delay":            # G, tau_l, tau_d, phi0
        g, tl, td, p0 = params
        h = g * np.exp(-1j * w * td) / (1 + 1j * w * tl)
    elif kind == "rl":                # G, tau_l, phi0
        g, tl, p0 = params
        h = g / (1 + 1j * w * tl)
    elif kind == "zero_pole_delay":   # G, tau_z, tau_p, tau_d, phi0
        g, tz, tp, td, p0 = params
        h = g * np.exp(-1j * w * td) * (1 + 1j * w * tz) / (1 + 1j * w * tp)
    else:
        raise ValueError(kind)
    model_phase = np.angle(np.exp(1j * (np.angle(h) + p0)))
    dmag = np.abs(h) / mag - 1.0
    dphase = np.rad2deg(model_phase - phase)
    return dmag, dphase


def cost(params, kind):
    dmag, dphase = residuals(params, kind)
    return np.concatenate([dmag / 0.01, dphase / 1.0])   # 1% 幅度 与 1° 相位等权


def fit(kind, p0):
    from scipy.optimize import least_squares
    best = None
    for scale in (1.0, 0.5, 2.0):
        start = np.array(p0, dtype=float) * scale
        try:
            out = least_squares(cost, start, args=(kind,), x_scale="jac", max_nfev=4000)
        except Exception:
            continue
        if best is None or out.cost < best.cost:
            best = out
    return best


for kind, p0, names in (
        ("rl_delay", [140.0, 1e-6, 5e-7, -math.pi / 2], ["G/mA/V", "tau_L/us", "tau_d/us", "phi0/deg"]),
        ("rl", [140.0, 1e-6, -math.pi / 2], ["G/mA/V", "tau_L/us", "phi0/deg"]),
        ("zero_pole_delay", [140.0, 3e-7, 3e-6, 5e-7, -math.pi / 2], ["G", "tau_z/us", "tau_p/us", "tau_d/us", "phi0/deg"]),
):
    out = fit(kind, p0)
    dmag, dphase = residuals(out.x, kind)
    label = "  ".join(f"{n}={v:.4g}" for n, v in zip(names, out.x))
    print(f"[{kind}] {label}")
    print(f"    幅度相对残差 RMS={np.sqrt(np.mean(dmag**2))*100:.2f}%  "
          f"相位残差 RMS={np.sqrt(np.mean(dphase**2)):.2f}deg  最大相位残差={np.max(np.abs(dphase)):.2f}deg")

print()
print("  f/Hz      实测|I/V|   相位/deg   幅值隐含滞后/deg   (相位-90°)/deg  差/deg")
for fi, mi, pi in zip(f, mag, phase):
    wi = 2 * math.pi * fi
    # 由幅值反推一阶极点的相位滞后：|H| = G/sqrt(1+x^2)，G 取 10 Hz 处的值
    x = math.sqrt(max((mag[0] / mi) ** 2 - 1.0, 0.0))
    implied = math.degrees(math.atan(x))
    measured = math.degrees(pi) + 90.0
    print(f"{fi:10.4g}  {mi:8.2f}  {math.degrees(pi):9.2f}      {implied:9.2f}          {measured:9.2f}  {measured-implied:8.2f}")
