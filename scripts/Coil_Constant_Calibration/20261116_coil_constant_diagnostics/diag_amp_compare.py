"""三档 Y RF 幅度的第一版对比（本图保存在 0.05 Vpp 那轮的 results/）。

这一版里 0.5 Vpp 只采到 12/31 点（std 判据打断），所以图中 0.5 Vpp 是半条曲线；
完整版见 1116_170645_coil_amp500b/results/diag_amp_compare_complete.py。

数据来源：
  0.05 Vpp 1116_165757_coil_amp050/raw/current_000（本目录）
  0.5 Vpp  1116_165554_coil_amp500/raw/current_000（该运行目录已按用户要求删除）
  1.5 Vpp  1116_163244_coil_const_fine/raw/current_002（同上，已删除）

缺失目录会被自动跳过，届时只剩 0.05 Vpp 一条可复现。
"""
from lab_workflows.common import data_root
import pathlib

import matplotlib.pyplot as plt
import numpy as np

BASE = data_root() / "Coil_Constant_Calibration"
OUT = BASE / "1116_165757_coil_amp050/results/diag_amp_compare.png"
CASES = [
    ("1.5 Vpp", BASE / "1116_163244_coil_const_fine/raw/current_002", "#1f77b4"),
    ("0.5 Vpp", BASE / "1116_165554_coil_amp500/raw/current_000", "#ff7f0e"),
    ("0.05 Vpp", BASE / "1116_165757_coil_amp050/raw/current_000", "#2ca02c"),
]

fig, axes = plt.subplots(1, 2, figsize=(14, 5.4), constrained_layout=True)
for label, directory, color in CASES:
    if not directory.exists():
        print(f"missing {label}: {directory}")
        continue
    scan = directory / "frequency_scan.npz"
    if scan.exists():
        frame = np.load(scan, allow_pickle=True)
        frequency, response = frame["frequency_hz"], frame["r_mean_v"]
        current = float(frame["current_ma"])
    else:
        files = sorted(directory.glob("frequency_*_attempt_*.npz"), key=lambda p: int(p.name.split("_")[1]))
        frequency = np.array([float(np.load(p)["frequency_hz"]) for p in files])
        response = np.array([float(np.mean(np.load(p)["r_v"])) for p in files])
        current = float("nan")
    if frequency.size == 0:
        continue
    order = np.argsort(frequency)
    frequency, response = frequency[order], response[order]
    axes[0].plot(frequency / 1e3, response, "o-", color=color, markersize=4, linewidth=1.2,
                 label=f"{label} ({frequency.size} pts, I={current:.1f} mA)")
    base = float(np.median(np.r_[response[:2], response[-2:]]))
    axes[1].plot(frequency / 1e3, (response - base) / max(float(response.max() - base), 1e-12), "o-",
                 color=color, markersize=4, linewidth=1.2, label=label)

axes[0].set_xlabel("Y RF frequency (kHz)")
axes[0].set_ylabel("R (V)")
axes[0].set_title("R vs Y RF frequency, three amplitudes (first pass)")
axes[0].grid(alpha=0.3)
axes[0].legend(fontsize=8)
axes[1].set_xlabel("Y RF frequency (kHz)")
axes[1].set_ylabel("(R - baseline) / peak")
axes[1].set_title("Baseline-subtracted, per-curve normalized")
axes[1].grid(alpha=0.3)
axes[1].legend(fontsize=9)
fig.savefig(OUT, dpi=140)
print(OUT)
