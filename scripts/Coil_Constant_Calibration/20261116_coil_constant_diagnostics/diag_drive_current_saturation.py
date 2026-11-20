"""±300 mA 那轮的驱动电压—实测电流曲线与高电流残差（读本目录 raw/ 与 results/analysis.json）。"""
from lab_workflows.common import data_root
import json
import pathlib

import matplotlib
import matplotlib.pyplot as plt
import numpy as np

matplotlib.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
matplotlib.rcParams["axes.unicode_minus"] = False

RUN = data_root() / "Coil_Constant_Calibration/1116_185249_coil_const_300"
OUT = RUN / "results/diag_drive_current_saturation.png"

index = np.load(RUN / "raw" / "current_scan_index.npz", allow_pickle=True)
drive = np.asarray(index["drive_v"], dtype=float)
current = np.asarray(index["current_ma"], dtype=float)
target = np.asarray(index["target_current_ma"], dtype=float)
order = np.argsort(drive)
drive, current, target = drive[order], current[order], target[order]

analysis = json.loads((RUN / "results" / "analysis.json").read_text(encoding="utf-8"))
measured = np.array([float(curve["current_ma"]) for curve in analysis["curves"] if curve.get("center_hz")])
centers = np.array([float(curve["center_hz"]) for curve in analysis["curves"] if curve.get("center_hz")])
predicted = np.abs(analysis["K_Hz_per_mA"] * (measured - analysis["I0_ma"]))

fig, axes = plt.subplots(1, 2, figsize=(12, 5), constrained_layout=True)

axis = axes[0]
axis.plot(drive, current, "o-", color="#1f77b4", label="measured current")
low = np.abs(current) <= 200
slope = float(np.polyfit(drive[low], current[low], 1)[0])
grid = np.linspace(drive.min(), drive.max(), 100)
axis.plot(grid, slope * grid, "--", color="#d62728", linewidth=1.2,
          label=f"linear extrapolation {slope:.1f} mA/V")
axis.axhline(200, color="#999999", linestyle=":", linewidth=1.0)
axis.axhline(-200, color="#999999", linestyle=":", linewidth=1.0)
for x, y in zip(drive, current):
    if abs(y) > 200:
        axis.annotate(f"{y:.0f} mA", (x, y), textcoords="offset points", xytext=(6, -10), fontsize=8)
axis.set_xlabel("Signal generator drive voltage (V)")
axis.set_ylabel("Measured coil current (mA)")
axis.set_title("Drive to current: compression above 200 mA")
axis.grid(alpha=0.3)
axis.legend(fontsize=8)

axis = axes[1]
axis.plot(measured, (centers - predicted) / 1e3, "o", color="#1f77b4")
axis.axhline(0, color="#999999", linestyle=":", linewidth=1.0)
axis.set_xlabel("Measured coil current (mA)")
axis.set_ylabel("Residual vs |K(I-I0)| (kHz)")
axis.set_title("Centre residual grows at high current")
axis.grid(alpha=0.3)

fig.savefig(OUT, dpi=140)
print(OUT)
print("low-current slope %.1f mA/V" % slope)
for d, i in zip(drive, current):
    print(f"  drive {d:+.3f} V -> {i:+.1f} mA  ({i / d * 1e-3 if d else float('nan'):.4f} A/V)")
