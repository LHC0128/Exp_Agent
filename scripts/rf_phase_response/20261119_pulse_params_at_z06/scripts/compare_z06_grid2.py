"""以 (s_f=0.65, τe=45) 为中心的 5×5 局部扫描：热图与绝对相量阵列。"""
from __future__ import annotations

from lab_workflows.common import data_root
import csv
import json
import math
import re
from pathlib import Path

import numpy as np
import yaml

ROOT = (data_root() / "rf_phase_response")
OUT = Path(__file__).resolve().parent.parent / "figures"
PHI = np.radians(np.arange(0.0, 360.0, 0.5))
TAG = re.compile(r"^[gh](\d{2,3})t(\d{2})$")
SFS = [0.55, 0.60, 0.65, 0.70, 0.75]
TES = [35, 40, 45, 50, 55]


def load(run_dir):
    config = yaml.safe_load((run_dir / "experiment_config.yaml").read_text(encoding="utf-8"))
    summary = json.loads((run_dir / "results/phase_response_summary.json").read_text(encoding="utf-8"))
    if "components" not in summary:
        raise ValueError("no components")
    p = config["parameters"]
    comp = summary["components"]
    z = lambda key: complex(comp[key]["re_v"], comp[key]["im_v"])
    return dict(dir=run_dir, tag=str(p.get("RUN_TAG")), sf=round(float(p["Z_TAIL_S_F"]), 3),
                te=round(float(p["Z_TAIL_TAU_E_US"]), 1), width=float(p["Z_PULSE_WIDTH_US"]),
                edge=float(p["Z_PULSE_EDGE_US"]), field=float(p.get("MAIN_FIELD_CURRENT_MA", 0.0)),
                c0=z("c0"), cp=z("c_plus1"), cm=z("c_minus1"),
                best=float(summary["best_phase_deg"]),
                alt=float(summary.get("best_phase_alt_deg", (summary["best_phase_deg"] + 180) % 360)),
                sensitivity=float(summary.get("sensitivity_max_v_per_deg",
                                              (abs(comp["c_plus1"]["abs_v"]) + abs(comp["c_minus1"]["abs_v"]))
                                              * math.pi / 180) * 1e3))


def collect():
    runs = []
    for path in sorted(ROOT.glob("*/experiment_config.yaml")):
        try:
            tag = str(yaml.safe_load(path.read_text(encoding="utf-8"))["parameters"].get("RUN_TAG", ""))
        except Exception:
            continue
        if not TAG.match(tag):
            continue
        if not (path.parent / "results/phase_response_summary.json").is_file():
            continue
        try:
            item = load(path.parent)
        except (KeyError, ValueError):
            continue
        if item["sf"] in SFS and item["te"] in TES:
            runs.append(item)
    return runs


def representative(item):
    distance = lambda angle: min(abs(angle - 97.3), 360 - abs(angle - 97.3))
    return item["best"] if distance(item["best"]) <= distance(item["alt"]) else item["alt"]


def main():
    runs = collect()
    grid = {}
    for item in runs:
        key = (item["sf"], item["te"])
        # 同一格若有两轮测量，保留较早的 g 轮（两轮都已核验 36 行）。
        if key not in grid or item["tag"].startswith("g"):
            grid[key] = item
    if len(grid) < 24:
        raise SystemExit(f"网格不完整：{len(grid)}/25")

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from lab_workflows.plotting import format_axis, plot_style_context, save_figure, style_legend

    slope = np.full((len(SFS), len(TES)), np.nan)
    c0 = np.full_like(slope, np.nan)
    for i, sf in enumerate(SFS):
        for j, te in enumerate(TES):
            item = grid.get((sf, te))
            if item:
                slope[i, j] = item["sensitivity"]
                c0[i, j] = abs(item["c0"]) * 1e3

    OUT.mkdir(parents=True, exist_ok=True)
    with plot_style_context("paper"):
        figure, axes = plt.subplots(1, 2, figsize=(7.2, 3.2))
        for ax, values, title, unit, cmap in (
                (axes[0], slope, "(a) tangent slope", "mV/deg", "viridis"),
                (axes[1], c0, r"(b) $|c_0|$", "mV", "magma")):
            image = ax.imshow(values, origin="lower", aspect="auto", cmap=cmap,
                              extent=[min(TES) - 2.5, max(TES) + 2.5, min(SFS) - 0.025, max(SFS) + 0.025])
            span = np.nanmax(values) - np.nanmin(values)
            for i in range(len(SFS)):
                for j in range(len(TES)):
                    if np.isfinite(values[i, j]):
                        norm = (values[i, j] - np.nanmin(values)) / max(span, 1e-9)
                        ax.text(TES[j], SFS[i], f"{values[i, j]:.2f}", ha="center", va="center",
                                fontsize=4.8, color="w" if norm > 0.55 else "k")
            format_axis(ax, xlabel=r"$\tau_e$ (µs)", ylabel=r"$s_f$")
            ax.set_title(title + f" ({unit})", fontsize=6.5)
            figure.colorbar(image, ax=ax, fraction=0.046, pad=0.03).ax.tick_params(labelsize=5)
        save_figure(figure, OUT / "z06_grid2_maps.png")

        figure, axes = plt.subplots(len(SFS), len(TES), figsize=(7.2, 7.2))
        bounds = [1e9, -1e9, 1e9, -1e9]
        for i, sf in enumerate(SFS):
            for j, te in enumerate(TES):
                ax = axes[i, j]
                ax.set_xticks([])
                ax.set_yticks([])
                item = grid.get((sf, te))
                if item is None:
                    continue
                z = (item["c0"] + item["cp"] * np.exp(1j * PHI)
                     + item["cm"] * np.exp(-1j * PHI)) * 1e3
                ax.plot(z.real, z.imag, "-", lw=0.7, color="C0")
                ax.plot(item["c0"].real * 1e3, item["c0"].imag * 1e3, "o", ms=1.4, color="C3")
                ax.axhline(0.0, color="0.8", lw=0.4)
                ax.axvline(0.0, color="0.8", lw=0.4)
                ax.plot(0, 0, "+", ms=3, color="k")
                ax.text(0.04, 0.06, f"$c_0$={abs(item['c0']) * 1e3:.1f}", transform=ax.transAxes,
                        fontsize=4.5, va="bottom")
                bounds = [min(bounds[0], z.real.min()), max(bounds[1], z.real.max()),
                          min(bounds[2], z.imag.min()), max(bounds[3], z.imag.max())]
                if i == 0:
                    ax.set_title(f"{te:g}", fontsize=5.5)
                if j == 0:
                    ax.set_ylabel(f"{sf:.2f}", fontsize=5.5, rotation=0, labelpad=8)
        limit = max(abs(bounds[0]), abs(bounds[1]), abs(bounds[2]), abs(bounds[3])) * 1.12
        for ax in axes.flat:
            ax.set_xlim(-limit, limit)
            ax.set_ylim(-limit, limit)
            ax.set_aspect("equal", adjustable="box")
            ax.set_box_aspect(1)
        figure.text(0.01, 0.005, f"shared axes, ±{limit:.0f} mV; red dot = $c_0$, black cross = origin",
                    fontsize=5, color="0.35")
        figure.supxlabel(r"$\tau_e$ (µs)", fontsize=6.5)
        figure.supylabel(r"$s_f$", fontsize=6.5)
        save_figure(figure, OUT / "z06_grid2_phasors.png")

    with (OUT / "z06_grid2.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["tag", "run_dir", "sf", "te_us", "width_us", "edge_us", "field_mA",
                         "c0_mV", "cplus_mV", "cminus_mV", "slope_mV_per_deg", "best_deg"])
        for key in sorted(grid):
            item = grid[key]
            writer.writerow([item["tag"], item["dir"].name, f"{item['sf']:.2f}", f"{item['te']:.0f}",
                             item["width"], item["edge"], f"{item['field']:.2f}",
                             "%.6g" % (abs(item["c0"]) * 1e3), "%.6g" % (abs(item["cp"]) * 1e3),
                             "%.6g" % (abs(item["cm"]) * 1e3), "%.6g" % item["sensitivity"],
                             "%.6g" % representative(item)])
    print(OUT / "z06_grid2_maps.png")
    print(OUT / "z06_grid2_phasors.png")
    print(OUT / "z06_grid2.csv")


if __name__ == "__main__":
    main()
