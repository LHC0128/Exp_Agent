"""三档 Y RF 幅度在 −100 mA 附近的线型对比（本图保存在 0.5 Vpp 那轮的 results/）。

数据来源：
  0.5 Vpp  1116_170645_coil_amp500b/raw/current_000
  0.05 Vpp 1116_165757_coil_amp050/raw/current_001
  1.5 Vpp  1116_163244_coil_const_fine/raw/current_002（该运行目录已按用户要求删除）

1.5 Vpp 那条曲线只保留在 diag_amp_compare_complete.png 里；脚本在缺少该目录时自动跳过，
其余两条可复现。
"""
from lab_workflows.common import data_root
import pathlib

import matplotlib.pyplot as plt
import numpy as np
from scipy.optimize import curve_fit

BASE = data_root() / "Coil_Constant_Calibration"
OUT = BASE / "1116_170645_coil_amp500b/results/diag_amp_compare_complete.png"
CASES = [
    ("1.5 Vpp", BASE / "1116_163244_coil_const_fine/raw/current_002", "#1f77b4"),
    ("0.5 Vpp", BASE / "1116_170645_coil_amp500b/raw/current_000", "#ff7f0e"),
    ("0.05 Vpp", BASE / "1116_165757_coil_amp050/raw/current_001", "#2ca02c"),
]


def model(x, c, a, mu, g):
    return c + a * g**2 / ((x - mu) ** 2 + g**2)


def load(directory):
    scan = directory / "frequency_scan.npz"
    if scan.exists():
        frame = np.load(scan, allow_pickle=True)
        return frame["frequency_hz"], frame["r_mean_v"], float(frame["current_ma"])
    files = sorted(directory.glob("frequency_*_attempt_*.npz"), key=lambda p: int(p.name.split("_")[1]))
    frequency = np.array([float(np.load(p)["frequency_hz"]) for p in files])
    response = np.array([float(np.mean(np.load(p)["r_v"])) for p in files])
    return frequency, response, float("nan")


fig, axes = plt.subplots(1, 2, figsize=(14, 5.4), constrained_layout=True)
summary = []
for label, directory, color in CASES:
    if not directory.exists():
        print(f"missing {label}: {directory}")
        continue
    frequency, response, current = load(directory)
    if frequency.size == 0:
        continue
    order = np.argsort(frequency)
    frequency, response = frequency[order], response[order]
    axes[0].plot(frequency / 1e3, response, "o-", color=color, markersize=4, linewidth=1.2,
                 label=f"{label} ({frequency.size} pts, I={current:.2f} mA)")
    base = float(np.median(np.r_[response[:2], response[-2:]]))
    axes[1].plot(frequency / 1e3, (response - base) / max(float(response.max() - base), 1e-12), "o-",
                 color=color, markersize=4, linewidth=1.2, label=label)
    try:
        popt, _ = curve_fit(model, frequency, response,
                            p0=[base, float(response.max() - base), frequency[int(np.argmax(response))], 800.0],
                            maxfev=40000)
        summary.append((label, current, popt[2], popt[3], popt[1], popt[0], frequency.size))
    except Exception:
        summary.append((label, current, float("nan"), float("nan"), float("nan"), base, frequency.size))

axes[0].set_xlabel("Y RF frequency (kHz)")
axes[0].set_ylabel("R (V)")
axes[0].set_title("R vs Y RF frequency at fixed Z current (~-100 mA)")
axes[0].grid(alpha=0.3)
axes[0].legend(fontsize=8)
axes[1].set_xlabel("Y RF frequency (kHz)")
axes[1].set_ylabel("(R - baseline) / peak")
axes[1].set_title("Baseline-subtracted, per-curve normalized")
axes[1].grid(alpha=0.3)
axes[1].legend(fontsize=9)
fig.savefig(OUT, dpi=140)
print(OUT)
for label, current, center, gamma, amp, base, count in summary:
    print(f"{label:9s} I={current:8.2f} mA  n={count:2d}  center={center / 1e3:8.3f} kHz  "
          f"gamma={gamma:6.0f} Hz  amp={amp:.4f} V  base={base:.4f} V")
