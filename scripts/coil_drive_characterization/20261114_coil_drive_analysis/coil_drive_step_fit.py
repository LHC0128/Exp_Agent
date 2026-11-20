"""离线试算：用脉冲上升沿分离「固定延迟 δ」与「电感时间常数 τ」。只读运行目录数据。"""
from lab_workflows.common import data_root
import json
import numpy as np
from pathlib import Path

RAW = (data_root() / "coil_drive_characterization/1114_114241_coil_drive/raw")


def load(name, r=10.0):
    with np.load(RAW / name, allow_pickle=False) as a:
        t = a["time"]
        v = a["sense_v"]
        meta = json.loads(str(a["meta"]))
    return t, v / r, meta


def model(params, t):
    plateau, delay, tau, base = params
    x = np.clip((t - delay) / tau, None, 700.0)
    step = np.where(t >= delay, 1.0 - np.exp(-x), 0.0)
    return base + plateau * step


def fit_edge(t, i, width):
    from scipy.optimize import least_squares
    mask = (t > -1e-6) & (t < width)
    tt, ii = t[mask], i[mask]
    base = float(np.median(ii[tt < -0.05e-6]))
    plateau = float(np.mean(ii[(tt > 0.8 * width) & (tt < 0.97 * width)]))
    best = None
    for delay0 in (0.0, 0.15e-6, 0.3e-6):
        for tau0 in (0.2e-6, 0.5e-6, 0.8e-6):
            out = least_squares(lambda p: model(p, tt) - ii,
                                [plateau - base, delay0, tau0, base],
                                bounds=([0, -1e-6, 1e-9, -1], [1, 2e-6, 5e-6, 1]), x_scale="jac")
            if best is None or out.cost < best.cost:
                best = out
    return best, plateau, base, tt, ii


print("脉冲上升沿拟合 I(t)=b+Ip(1-exp(-(t-δ)/τ))，t=0 为同步沿（触发点）")
for name in ("pulse_09.npz", "pulse_08.npz", "pulse_05.npz", "pulse_02.npz"):
    t, i, meta = load(name)
    width = meta["width_s"]
    out, plateau, base, tt, ii = fit_edge(t, i, width)
    ip, delay, tau, b = out.x
    rms = np.sqrt(np.mean((model(out.x, tt) - ii) ** 2)) * 1e3
    print(f"  {name} w={width*1e6:5.2f}us  δ={delay*1e6:6.3f}us  τ={tau*1e6:6.3f}us  "
          f"Ip={ip*1e3:6.1f}mA  残差RMS={rms:5.3f}mA")
