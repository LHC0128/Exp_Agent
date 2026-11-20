"""零点的两小时时间序列 + 左侧边缘分布（不新增测量，数据取自运行目录 raw/zero_dg0.csv）。"""
from lab_workflows.common import data_root
import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

BASE = data_root() / "z_chain_stability/1117_013211_z_chain_stability"
B_NT_PER_MA = 653.79 / 7.0048

d = np.genfromtxt(f"{BASE}/raw/zero_dg0.csv", delimiter=",", names=True,
                  dtype=None, encoding="utf-8")
elapsed = np.asarray(d["elapsed_s"], float)
field = np.asarray(d["current_a"], float) * 1e3 * B_NT_PER_MA
mean, std = field.mean(), field.std(ddof=0)

plt.rcParams.update({"font.size": 10, "axes.grid": True, "grid.alpha": 0.25,
                     "savefig.dpi": 170, "figure.dpi": 170})

fig = plt.figure(figsize=(9.6, 4.35))
gs = fig.add_gridspec(1, 2, width_ratios=[0.9, 4.4], wspace=0.04)
axh = fig.add_subplot(gs[0, 0])
ax = fig.add_subplot(gs[0, 1], sharey=axh)

edges = np.arange(119.6, 123.6, 0.09)
counts, _ = np.histogram(field, bins=edges)
centers = 0.5 * (edges[1:] + edges[:-1])
axh.fill_betweenx(centers, 0, counts, step="mid", color="#3f7f4f", alpha=0.8,
                  lw=1.2, edgecolor="#2c5f3b")
axh.axhline(mean, color="0.45", ls="--", lw=1.0)
axh.set_xlim(0, counts.max() * 1.12)
axh.set_xticks([])
axh.grid(False)
axh.spines["top"].set_visible(False)
axh.spines["bottom"].set_visible(False)
axh.spines["right"].set_visible(False)
axh.set_ylabel("Field equivalent (nT)")

ax.plot(elapsed, field, ".", ms=3.0, color="#2b6cb0", alpha=0.9, zorder=3)
ax.axhline(mean, color="0.45", ls="--", lw=1.0, zorder=2)
ax.axhspan(mean - std, mean + std, color="0.6", alpha=0.12, zorder=1)
ax.set_xlabel("Elapsed (s)")
ax.spines["left"].set_visible(False)
ax.tick_params(axis="y", left=False, labelleft=False)
ax.set_xlim(-300, elapsed.max() + 300)
ax.text(0.975, 0.94, "dg0", transform=ax.transAxes, ha="right", color="0.25")
ax.text(0.975, 0.86,
        f"n = {len(field)}\nmean = {mean:.2f} nT\nstd = {std:.2f} nT",
        transform=ax.transAxes, ha="right", va="top", fontsize=8.5, color="0.25")

out = f"{BASE}/results/zero_timeseries_marginal.png"
fig.savefig(out, bbox_inches="tight", facecolor="white")
print("saved", out)
print(f"n={len(field)}  mean={mean:.3f}  std={std:.4f}  min={field.min():.2f}  max={field.max():.2f}")
