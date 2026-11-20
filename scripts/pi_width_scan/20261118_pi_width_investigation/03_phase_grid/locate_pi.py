"""从网格分解结果定位 c-1 的峰（|c-1| 抛物线）与 2π 回零点（复二次拟合的最近点）。

用法：python locate_pi.py <grid_xxx.npz> [edge_us=1.0]
面积 = 脉宽 − 沿宽（升余弦沿等效）。
"""
import sys

import numpy as np

path = sys.argv[1]
edge = float(sys.argv[2]) if len(sys.argv) > 2 else 1.0
d = np.load(path)
w, cm, c0 = d["w"], d["cm"], d["c0"]

k = int(np.argmax(abs(cm)))
sl = slice(max(k - 3, 0), min(k + 4, len(w)))
a, b, c = np.polyfit(w[sl], abs(cm[sl]), 2)
w_peak = -b / (2 * a)

j = int(np.argmin(abs(cm[k:]))) + k
out = {"peak_us": w_peak, "peak_mV": float(np.polyval([a, b, c], w_peak))}
if 0 < j < len(w) - 1 and j - k >= 2:
    sl2 = slice(max(j - 3, k), min(j + 4, len(w)))
    pr = np.polyfit(w[sl2], cm[sl2].real, 2)
    pi_ = np.polyfit(w[sl2], cm[sl2].imag, 2)
    fine = np.linspace(w[sl2][0], w[sl2][-1], 4001)
    mag = np.hypot(np.polyval(pr, fine), np.polyval(pi_, fine))
    m = int(np.argmin(mag))
    out.update(null_us=float(fine[m]), null_mV=float(mag[m]))
area_pi_from_peak = w_peak - edge
print(f"{path}")
print(f"  |c-1| peak at {w_peak:.3f} us ({out['peak_mV']:.2f} mV) -> pi area {area_pi_from_peak:.3f} us")
if "null_us" in out:
    area_2pi = out["null_us"] - edge
    print(f"  c-1 closest to 0 at {out['null_us']:.3f} us ({out['null_mV']:.2f} mV) -> 2pi area {area_2pi:.3f} us,"
          f" pi area {area_2pi / 2:.3f} us, tau_pi = {area_2pi / 2 + edge:.3f} us")
print(f"  electrical tau_pi 5.122 us (area 4.122 us)")
