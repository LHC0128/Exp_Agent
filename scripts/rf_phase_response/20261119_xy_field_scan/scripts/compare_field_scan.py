"""X/Y/Z 场扫描对比：每个方向一张图（相量、分量+斜率、最佳相位）。

读取 data/rf_phase_response 下带 x*/y*/z* 标签的运行目录，输出到 field_scan/figures/。
"""
from __future__ import annotations

from lab_workflows.common import data_root
import csv
import json
import math
from pathlib import Path

import numpy as np
import yaml

ROOT = (data_root() / "rf_phase_response")
OUT = Path(__file__).resolve().parent.parent / "figures"
BASELINE = ROOT / "1119_092825_rf_phase_response"
PHI = np.radians(np.arange(0.0, 360.0, 0.5))


def load(run_dir: Path):
    config = yaml.safe_load((run_dir / "experiment_config.yaml").read_text(encoding="utf-8"))
    summary = json.loads((run_dir / "results/phase_response_summary.json").read_text(encoding="utf-8"))
    if "components" not in summary:
        raise ValueError("no components")
    p = config["parameters"]
    comp = summary["components"]
    z = lambda key: complex(comp[key]["re_v"], comp[key]["im_v"])
    return dict(dir=run_dir, tag=str(p.get("RUN_TAG")), x=float(p["X_BIAS_V"]) * 1e3,
                y=float(p["Y_BIAS_V"]) * 1e3, zfield=float(p.get("MAIN_FIELD_CURRENT_MA", 0.0)),
                c0=z("c0"), cp=z("c_plus1"), cm=z("c_minus1"),
                best=float(summary["best_phase_deg"]),
                alt=float(summary.get("best_phase_alt_deg", (summary["best_phase_deg"] + 180.0) % 360.0)),
                sensitivity=float(summary.get("sensitivity_max_v_per_deg")
                                  or (abs(comp["c_plus1"]["abs_v"]) + abs(comp["c_minus1"]["abs_v"]))
                                  * math.pi / 180.0) * 1e3)


def collect():
    runs = []
    for path in sorted(ROOT.glob("*/experiment_config.yaml")):
        run_dir = path.parent
        try:
            tag = str(yaml.safe_load(path.read_text(encoding="utf-8"))["parameters"].get("RUN_TAG"))
        except Exception:
            continue
        if (tag[:1] in {"x", "y", "z"} and tag not in {"", "zt"}) and \
                (run_dir / "results/phase_response_summary.json").is_file():
            try:
                runs.append(load(run_dir))
            except (KeyError, ValueError, TypeError):
                continue
    return runs


def curve(item):
    return (item["c0"] + item["cp"] * np.exp(1j * PHI) + item["cm"] * np.exp(-1j * PHI)) * 1e3


def draw(name, xlabel, items, key):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.gridspec import GridSpec
    from lab_workflows.plotting import format_axis, plot_style_context, save_figure, style_legend

    base = load(BASELINE)
    items = sorted(items, key=lambda item: item[key])
    values = [item[key] for item in items]
    colors = plt.cm.coolwarm(np.linspace(0.1, 0.9, len(items)))
    with plot_style_context("paper"):
        figure = plt.figure(figsize=(7.4, 3.8))
        grid = GridSpec(2, 2, figure=figure, width_ratios=[1.1, 1.0], wspace=0.34, hspace=0.28)
        ax = figure.add_subplot(grid[:, 0])
        comp_ax = figure.add_subplot(grid[0, 1])
        best_ax = figure.add_subplot(grid[1, 1], sharex=comp_ax)

        ax.plot(curve(base).real, curve(base).imag, "-", color="k", lw=1.0, label="baseline")
        ax.plot(base["c0"].real * 1e3, base["c0"].imag * 1e3, "o", ms=2, color="k")
        for item, color in zip(items, colors):
            ax.plot(curve(item).real, curve(item).imag, "-", lw=0.9, color=color,
                    label=f"{item[key]:+g}")
            ax.plot(item["c0"].real * 1e3, item["c0"].imag * 1e3, "o", ms=2, color=color)
        ax.axhline(0.0, color="0.85", lw=0.4)
        ax.axvline(0.0, color="0.85", lw=0.4)
        ax.set_aspect("equal", adjustable="datalim")
        ax.margins(0.08)
        format_axis(ax, xlabel=r"Re $Z$ (mV)", ylabel=r"Im $Z$ (mV)")
        ax.set_title(f"(a) {name} phasors", fontsize=6.5)
        style_legend(ax, loc="upper left", fontsize=4.5, frameon=False)

        series = sorted(items + [base], key=lambda item: item[key])
        xs = [item[key] for item in series]
        comp_ax.plot(xs, [abs(item["c0"]) * 1e3 for item in series], "o-", ms=3, lw=0.8, label=r"$|c_0|$")
        comp_ax.plot(xs, [abs(item["cp"]) * 1e3 for item in series], "s-", ms=3, lw=0.8, label=r"$|c_{+1}|$")
        comp_ax.plot(xs, [abs(item["cm"]) * 1e3 for item in series], "^-", ms=3, lw=0.8, label=r"$|c_{-1}|$")
        comp_ax.axvline(0.0, color="0.6", ls=":", lw=0.7)
        component_peak = max(max(abs(item[k]) * 1e3 for k in ("c0", "cp", "cm")) for item in series)
        comp_ax.set_ylim(0.0, max(18.0, component_peak * 1.15))
        slope_ax = comp_ax.twinx()
        slope_color = "#6d28d9"
        slope_ax.plot(xs, [(abs(item["cp"]) + abs(item["cm"])) * 1e3 * math.pi / 180.0 for item in series],
                      "D:", ms=2.5, lw=0.8, mfc="none", color=slope_color)
        slope_peak = max((abs(item["cp"]) + abs(item["cm"])) * 1e3 * math.pi / 180.0 for item in series)
        slope_ax.set_ylim(0.15, max(0.45, slope_peak * 1.1))
        slope_ax.set_ylabel(r"$|\mathrm{d}Z/\mathrm{d}\varphi|$ (mV/deg)", color=slope_color)
        slope_ax.tick_params(axis="y", colors=slope_color, labelsize=6)
        format_axis(comp_ax, xlabel="", ylabel="component (mV)")
        comp_ax.set_title(f"(b) {name} components", fontsize=6.5)
        style_legend(comp_ax, loc="upper center", fontsize=5.0, ncol=3, frameon=False)

        def representative(item):
            # |dZ/dφ| 以 180° 为周期，取圆周上离基线 98.1° 最近的那个等价点。
            distance = lambda angle: min(abs(angle - 98.1), 360.0 - abs(angle - 98.1))
            return item["best"] if distance(item["best"]) <= distance(item["alt"]) else item["alt"]

        best_ax.plot(xs, [representative(item) for item in series], "o-", ms=3, lw=0.8, color="0.2")
        best_ax.set_ylim(70.0, 150.0)
        best_ax.axvline(0.0, color="0.6", ls=":", lw=0.7)
        format_axis(best_ax, xlabel=xlabel, ylabel=r"best $\varphi$ (deg)")
        best_ax.set_title(f"(c) {name} best phase", fontsize=6.5)
        save_figure(figure, OUT / f"field_{name.lower()}.png")


def main():
    runs = collect()
    if not runs:
        raise SystemExit("没有找到场扫描运行")
    OUT.mkdir(parents=True, exist_ok=True)
    draw("X", r"X bias (mV)", [r for r in runs if r["tag"].startswith("x")], "x")
    draw("Y", r"Y bias (mV)", [r for r in runs if r["tag"].startswith("y")], "y")
    draw("Z", r"main field (mA)", [r for r in runs if r["tag"].startswith("z")], "zfield")
    with (OUT / "field_components.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["tag", "x_mV", "y_mV", "z_mA", "c0_mV", "cplus_mV", "cminus_mV",
                         "slope_mV_per_deg", "best_deg"])
        for item in sorted(runs, key=lambda row: (row["tag"][:1], row["x"] + row["y"] + row["zfield"])):
            writer.writerow([item["tag"], "%.4g" % item["x"], "%.4g" % item["y"], "%.4g" % item["zfield"],
                             "%.6g" % (abs(item["c0"]) * 1e3), "%.6g" % (abs(item["cp"]) * 1e3),
                             "%.6g" % (abs(item["cm"]) * 1e3), "%.6g" % item["sensitivity"],
                             "%.6g" % item["best"]])
    print(OUT / "field_x.png")
    print(OUT / "field_y.png")
    print(OUT / "field_z.png")
    print(OUT / "field_components.csv")


if __name__ == "__main__":
    main()
