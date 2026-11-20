"""Z 主磁场细扫（-0.20..+0.20 mA，步进 0.02）的对比图与峰值定位。"""
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
TAG = re.compile(r"^z[m|p]\d{3}[a-z]?$")


def load(run_dir: Path):
    config = yaml.safe_load((run_dir / "experiment_config.yaml").read_text(encoding="utf-8"))
    summary = json.loads((run_dir / "results/phase_response_summary.json").read_text(encoding="utf-8"))
    if "components" not in summary:
        raise ValueError("no components")
    comp = summary["components"]
    z = lambda key: complex(comp[key]["re_v"], comp[key]["im_v"])
    return dict(dir=run_dir, tag=str(config["parameters"].get("RUN_TAG")),
                field=float(config["parameters"].get("MAIN_FIELD_CURRENT_MA", 0.0)),
                c0=z("c0"), cp=z("c_plus1"), cm=z("c_minus1"),
                best=float(summary["best_phase_deg"]),
                alt=float(summary.get("best_phase_alt_deg", (summary["best_phase_deg"] + 180.0) % 360.0)),
                sensitivity=float(summary.get("sensitivity_max_v_per_deg",
                                              (abs(comp["c_plus1"]["abs_v"]) + abs(comp["c_minus1"]["abs_v"]))
                                              * math.pi / 180.0) * 1e3))


def main():
    runs = []
    for path in sorted(ROOT.glob("*/experiment_config.yaml")):
        try:
            params = yaml.safe_load(path.read_text(encoding="utf-8"))["parameters"]
            tag = str(params.get("RUN_TAG", ""))
        except Exception:
            continue
        if not TAG.match(tag):
            continue
        if not (path.parent / "results/phase_response_summary.json").is_file():
            continue
        try:
            runs.append(load(path.parent))
        except (KeyError, ValueError):
            continue
    if not runs:
        raise SystemExit("没有找到 Z 细扫运行")
    runs.sort(key=lambda item: item["field"])

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.gridspec import GridSpec
    from lab_workflows.plotting import format_axis, plot_style_context, save_figure, style_legend

    OUT.mkdir(parents=True, exist_ok=True)
    fields = [item["field"] for item in runs]
    colors = plt.cm.coolwarm(np.linspace(0.05, 0.95, len(runs)))
    with plot_style_context("paper"):
        figure = plt.figure(figsize=(7.4, 3.8))
        grid = GridSpec(2, 2, figure=figure, width_ratios=[1.1, 1.0], wspace=0.34, hspace=0.28)
        ax = figure.add_subplot(grid[:, 0])
        comp_ax = figure.add_subplot(grid[0, 1])
        best_ax = figure.add_subplot(grid[1, 1], sharex=comp_ax)

        for item, color in zip(runs, colors):
            values = (item["c0"] + item["cp"] * np.exp(1j * PHI) + item["cm"] * np.exp(-1j * PHI)) * 1e3
            ax.plot(values.real, values.imag, "-", lw=0.8, color=color, label=f"{item['field']:+.2f}")
            ax.plot(item["c0"].real * 1e3, item["c0"].imag * 1e3, "o", ms=1.8, color=color)
        ax.axhline(0.0, color="0.85", lw=0.4)
        ax.axvline(0.0, color="0.85", lw=0.4)
        ax.set_aspect("equal", adjustable="datalim")
        ax.margins(0.06)
        format_axis(ax, xlabel=r"Re $Z$ (mV)", ylabel=r"Im $Z$ (mV)")
        ax.set_title("(a) Z fine scan phasors", fontsize=6.5)
        style_legend(ax, loc="upper left", fontsize=4.2, ncol=2, frameon=False)

        comp_ax.plot(fields, [abs(item["c0"]) * 1e3 for item in runs], "o-", ms=3, lw=0.8, label=r"$|c_0|$")
        comp_ax.plot(fields, [abs(item["cp"]) * 1e3 for item in runs], "s-", ms=2.6, lw=0.8, label=r"$|c_{+1}|$")
        comp_ax.plot(fields, [abs(item["cm"]) * 1e3 for item in runs], "^-", ms=2.6, lw=0.8, label=r"$|c_{-1}|$")
        comp_ax.axvline(0.0, color="0.6", ls=":", lw=0.7)
        comp_ax.set_ylim(0.0, max(abs(item["c0"]) * 1e3 for item in runs) * 1.15)
        slope_ax = comp_ax.twinx()
        slope_color = "#6d28d9"
        slope_ax.plot(fields, [item["sensitivity"] for item in runs], "D:", ms=2.2, lw=0.8, mfc="none",
                      color=slope_color)
        slope_ax.set_ylim(0.0, max(item["sensitivity"] for item in runs) * 1.1)
        slope_ax.set_ylabel(r"$|\mathrm{d}Z/\mathrm{d}\varphi|$ (mV/deg)", color=slope_color)
        slope_ax.tick_params(axis="y", colors=slope_color, labelsize=6)
        format_axis(comp_ax, xlabel="", ylabel="component (mV)")
        comp_ax.set_title("(b) components", fontsize=6.5)
        style_legend(comp_ax, loc="upper center", fontsize=5.0, ncol=3, frameon=False)

        def representative(item):
            distance = lambda angle: min(abs(angle - 98.1), 360.0 - abs(angle - 98.1))
            return item["best"] if distance(item["best"]) <= distance(item["alt"]) else item["alt"]

        best_ax.plot(fields, [representative(item) for item in runs], "o-", ms=3, lw=0.8, color="0.2")
        best_ax.axvline(0.0, color="0.6", ls=":", lw=0.7)
        format_axis(best_ax, xlabel="main field (mA)", ylabel=r"best $\varphi$ (deg)")
        best_ax.set_title("(c) best phase", fontsize=6.5)
        save_figure(figure, OUT / "z_fine.png")

    # 峰值：|c0| 最大点（网格值）
    peak = max(range(len(runs)), key=lambda i: abs(runs[i]["c0"]))
    peak_record = dict(peak_field_ma=runs[peak]["field"], peak_c0_mv=abs(runs[peak]["c0"]) * 1e3,
                       peak_cplus_mv=abs(runs[peak]["cp"]) * 1e3,
                       peak_cminus_mv=abs(runs[peak]["cm"]) * 1e3)
    with (OUT / "z_fine_components.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["tag", "field_mA", "c0_mV", "cplus_mV", "cminus_mV", "slope_mV_per_deg", "best_deg"])
        for item in runs:
            writer.writerow([item["tag"], "%.3f" % item["field"], "%.6g" % (abs(item["c0"]) * 1e3),
                             "%.6g" % (abs(item["cp"]) * 1e3), "%.6g" % (abs(item["cm"]) * 1e3),
                             "%.6g" % item["sensitivity"], "%.6g" % representative(item)])
    (OUT / "z_fine_peak.json").write_text(json.dumps(peak_record, ensure_ascii=False, indent=2),
                                          encoding="utf-8")
    print(OUT / "z_fine.png")
    print(OUT / "z_fine_components.csv")
    print(peak_record)


if __name__ == "__main__":
    main()
