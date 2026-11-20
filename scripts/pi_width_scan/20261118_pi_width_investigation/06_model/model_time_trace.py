"""自洽检验：用频域拟合参数（fits/rf_slow.json）算关射频时的 P_x(t)，对其奇部做与实测相同的指数拟合。

实测（05_time_domain/px_odd_even.json）：奇部 20–98 µs 衰减 τ = 31.9 µs。
模型中静态横场与慢拖尾横向分量的实际大小未定（复增益简并），这里扫几组取值，看 τ 是否稳定在同一量级。
用法：python model_time_trace.py
"""
import json
import sys
from pathlib import Path

import numpy as np
from scipy.linalg import expm
from scipy.optimize import curve_fit

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import fitmodel as fm

fit = json.load(open(HERE / "fits" / "rf_slow.json", encoding="utf-8-sig"))
lg, w0, bx0, by0, s_f, ltau, _, _ = fit["x"]
g, tau = np.exp(lg), np.exp(ltau)
TWO_PI = 2 * np.pi


def px_trace(width, bx, by, slow_tilt=(0.0, 0.0), current_ma=190.0):
    t = fm.grid(width, fine=0.05, coarse=0.25)
    dt = np.diff(t); tm = t[:-1] + dt / 2
    tu, zw = fm.zwave(width, s_f, tau)
    _, fast = fm.zwave(width, 1.0, 0.0)
    peak = TWO_PI * fm.K * current_ma * 1e-6
    pz = peak * np.interp(tm, tu, zw, period=fm.PERIOD)
    ps = pz - s_f * peak * np.interp(tm, tu, fast, period=fm.PERIOD)
    B = np.stack([bx + slow_tilt[0] * ps, by + slow_tilt[1] * ps, w0 + pz], -1)
    n = len(dt)
    aug = np.zeros((n, 4, 4))
    aug[:, :3, :3] = (-fm.cross(B) - g * np.eye(3)[None]) * dt[:, None, None]
    aug[:, 2, 3] = g * dt
    M = expm(aug)
    tot = np.eye(4)
    for mm in M:
        tot = mm @ tot
    v = np.append(np.linalg.solve(np.eye(3) - tot[:3, :3], tot[:3, 3]), 1.0)
    px = [v[0]]
    for mm in M:
        v = mm @ v; px.append(v[0])
    tg = np.arange(0, fm.PERIOD, 0.1)
    return tg, np.interp(tg, t, np.array(px))


fexp = lambda x, a, tt, b: a * np.exp(-x / tt) + b
rows = []
for label, bx, by, st in (("static field only (fitted bx,by)", bx0, by0, (0, 0)),
                          ("static x 100 Hz", TWO_PI * 1e-4, 0.0, (0, 0)),
                          ("static y 100 Hz", 0.0, TWO_PI * 1e-4, (0, 0)),
                          ("slow-tail transverse x 0.02", 0.0, 0.0, (0.02, 0)),
                          ("slow-tail transverse y 0.02", 0.0, 0.0, (0, 0.02)),
                          ("fitted static + slow-tail x 0.02", bx0, by0, (0.02, 0))):
    tg, px = px_trace(5.0, bx, by, st)
    h = len(tg) // 2
    odd = (px[:h] - px[h:2 * h]) / 2
    th = tg[:h]
    m = (th > 20) & (th < 98)
    try:
        p, _ = curve_fit(fexp, th[m], odd[m], p0=(odd[m][0], 40, 0), maxfev=20000)
        tau_fit = p[1]
    except RuntimeError:
        tau_fit = float("nan")
    rows.append(dict(case=label, tau_odd_us=float(tau_fit)))
    print(f"{label:38s}: odd-part exp fit 20-98 us -> tau = {tau_fit:.1f} us")
print(f"(model tau_e = {tau:.1f} us, fast fraction {s_f:.2f}; measured odd tau = 31.9 us)")
(HERE / "model_time_trace.json").write_text(json.dumps(dict(model_tau_e_us=tau, rows=rows), indent=1),
                                            encoding="utf-8")


# 对照：无拖尾（s_f=1，5 µs 即电学 0.97π）时奇部与偶部之比；实测奇部峰 33.9 mV、偶部峰峰 13.5 mV（比值 2.5）
def odd_even_ratio(sf_value):
    global s_f
    keep = s_f
    s_f = sf_value
    try:
        tg, px = px_trace(5.0, bx0, by0, (0, 0))
    finally:
        s_f = keep
    h = len(tg) // 2
    odd, even = (px[:h] - px[h:2 * h]) / 2, (px[:h] + px[h:2 * h]) / 2
    th = tg[:h]
    return float(np.max(abs(odd[th < 20])) / np.ptp(even[th > 10]))


r_tail, r_none = odd_even_ratio(fit["x"][4]), odd_even_ratio(0.999999)
print(f"odd peak / even p2p at 5 us, fitted static field: with tail {r_tail:.2f}, without tail {r_none:.3f}; measured 2.51")
d = json.loads((HERE / "model_time_trace.json").read_text(encoding="utf-8"))
d.update(odd_even_ratio_with_tail=r_tail, odd_even_ratio_without_tail=r_none, odd_even_ratio_measured=33.868 / 13.508)
(HERE / "model_time_trace.json").write_text(json.dumps(d, indent=1), encoding="utf-8")
