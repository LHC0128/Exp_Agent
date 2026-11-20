"""阶段 2 的 5×5 网格直接成图：每格一个实测值，不做任何曲面拟合。

左图 r=S/S_ref(t)，右图 |c0|。图上标出 A（8.03/0.575）与 B（8.60/0.50）；
C（8.03/0.544）不在网格点上，故不标。对照 `grid_contour.png`（拟合曲面）使用：
本图只呈现测到的数，拟合图给出的驻点/本征值/近最优区才需要可微曲面。
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

import cc_common as cc

OUT = cc.RESULT_DIR
W_VALUES = (7.5, 7.75, 8.03, 8.3, 8.6)
SF_VALUES = (0.50, 0.54, 0.575, 0.61, 0.65)
MARKS = {"A": (8.03, 0.575), "B": (8.60, 0.50)}


def collect(rows):
    """按 (w, sf) 摆成 5×5 矩阵；缺格留 NaN。"""
    r = np.full((len(SF_VALUES), len(W_VALUES)), np.nan)
    c0 = np.full_like(r, np.nan)
    for row in rows:
        if row["role"] == "ref":
            continue
        i = SF_VALUES.index(round(row["sf"], 3))
        j = W_VALUES.index(round(row["w"], 2))
        r[i, j] = row["r"]
        c0[i, j] = row["c0_mV"]
    return r, c0


def panel(axis, values, title, unit, figure, fmt):
    import matplotlib.pyplot as plt

    image = axis.pcolormesh(np.arange(values.shape[1] + 1), np.arange(values.shape[0] + 1), values,
                            cmap="viridis", edgecolors="w", linewidth=0.6)
    low, high = np.nanmin(values), np.nanmax(values)
    for i in range(values.shape[0]):
        for j in range(values.shape[1]):
            value = values[i, j]
            if not np.isfinite(value):
                continue
            colour = "k" if (value - low) / (high - low) > 0.55 else "w"
            axis.text(j + 0.5, i + 0.5, fmt % value, ha="center", va="center", fontsize=5, color=colour)
    for name, (w, sf) in MARKS.items():
        j, i = W_VALUES.index(round(w, 2)), SF_VALUES.index(round(sf, 3))
        # 用格子边框圈出该点，避免挡住格内数值。
        axis.add_patch(plt.Rectangle((j, i), 1, 1, fill=False, edgecolor="w", linewidth=1.8))
        axis.text(j + 0.94, i + 0.94, name, ha="right", va="top", fontsize=6.5, color="w",
                  fontweight="bold")
    axis.set_xticks(np.arange(len(W_VALUES)) + 0.5, [f"{value:g}" for value in W_VALUES], fontsize=5.5)
    axis.set_yticks(np.arange(len(SF_VALUES)) + 0.5, [f"{value:g}" for value in SF_VALUES], fontsize=5.5)
    axis.set_xlabel("pulse width w (µs)")
    axis.set_ylabel("s$_f$")
    axis.set_title(title, fontsize=6.5)
    bar = figure.colorbar(image, ax=axis, fraction=0.046, pad=0.03)
    if unit:
        bar.set_label(unit, fontsize=5.5)
    bar.ax.tick_params(labelsize=5)
    return image


def main():
    rows = cc.load("stage2")
    if not rows:
        raise SystemExit("stage2_runs.jsonl 为空：阶段 2 还没有数据")
    cc.drift_normalise(rows)
    r, c0 = collect(rows)

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from lab_workflows.plotting import plot_style_context, save_figure

    figures = OUT / "figures"
    figures.mkdir(parents=True, exist_ok=True)
    with plot_style_context("paper"):
        figure, axes = plt.subplots(1, 2, figsize=(7.0, 3.2), layout="constrained")
        panel(axes[0], r, "(a) measured r = S / S$_{ref}$", "", figure, "%.3f")
        panel(axes[1], c0, "(b) measured |c$_0$| (mV)", "", figure, "%.0f")
        save_figure(figure, figures / "grid_maps.png")

    record = {"w_us": list(W_VALUES), "sf": list(SF_VALUES),
              "r": np.where(np.isfinite(r), r, None).tolist(),
              "c0_mv": np.where(np.isfinite(c0), c0, None).tolist(),
              "note": "每格一次测量，重复性 σ≈1%（阶段 2 二次曲面拟合残差）；未做任何插值或平滑。"}
    (OUT / "tables" / "grid_maps.json").write_text(json.dumps(record, ensure_ascii=False, indent=2),
                                                   encoding="utf-8")
    print(json.dumps({"shape": list(r.shape), "r_min": float(np.nanmin(r)), "r_max": float(np.nanmax(r)),
                      "c0_min": float(np.nanmin(c0)), "c0_max": float(np.nanmax(c0))}))
    print(figures / "grid_maps.png")


if __name__ == "__main__":
    main()
