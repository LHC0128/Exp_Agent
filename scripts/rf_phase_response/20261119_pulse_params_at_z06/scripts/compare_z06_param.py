"""Z=-0.06 mA 固定时的脉冲参数扫描：每个参数一张图（相量、分量+斜率、最佳相位）。"""
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
FIELD = -0.06


def load(run_dir: Path):
    config = yaml.safe_load((run_dir / "experiment_config.yaml").read_text(encoding="utf-8"))
    summary = json.loads((run_dir / "results/phase_response_summary.json").read_text(encoding="utf-8"))
    if "components" not in summary:
        raise ValueError("no components")
    p = config["parameters"]
    comp = summary["components"]
    z = lambda key: complex(comp[key]["re_v"], comp[key]["im_v"])
    return dict(dir=run_dir, tag=str(p.get("RUN_TAG")), width=float(p["Z_PULSE_WIDTH_US"]),
                edge=float(p["Z_PULSE_EDGE_US"]), sf=float(p["Z_TAIL_S_F"]),
                te=float(p["Z_TAIL_TAU_E_US"]), tail=bool(p["Z_TAIL_COMP_ENABLED"]),
                field=float(p.get("MAIN_FIELD_CURRENT_MA", 0.0)),
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
        if not tag.startswith("z06"):
            continue
        if not (path.parent / "results/phase_response_summary.json").is_file():
            continue
        try:
            runs.append(load(path.parent))
        except (KeyError, ValueError):
            continue
    return runs


def representative(item):
    distance = lambda angle: min(abs(angle - 97.3), 360 - abs(angle - 97.3))
    return item["best"] if distance(item["best"]) <= distance(item["alt"]) else item["alt"]


def draw(name, title, xlabel, items, key, baseline, reference=None, tailoff=None):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.gridspec import GridSpec
    from lab_workflows.plotting import format_axis, plot_style_context, save_figure, style_legend

    items = sorted(items, key=lambda item: item[key])
    if reference is None:
        reference = tailoff
    series = sorted(items + [baseline], key=lambda item: item[key])
    values = [item[key] for item in series]
    colors = plt.cm.viridis(np.linspace(0.12, 0.9, len(items)))
    with plot_style_context("paper"):
        figure = plt.figure(figsize=(7.4, 3.8))
        grid = GridSpec(2, 2, figure=figure, width_ratios=[1.1, 1.0], wspace=0.34, hspace=0.28)
        ax = figure.add_subplot(grid[:, 0])
        comp_ax = figure.add_subplot(grid[0, 1])
        best_ax = figure.add_subplot(grid[1, 1], sharex=comp_ax)

        ax.plot((baseline["c0"] + baseline["cp"] * np.exp(1j * PHI) + baseline["cm"] * np.exp(-1j * PHI)).real * 1e3,
                (baseline["c0"] + baseline["cp"] * np.exp(1j * PHI) + baseline["cm"] * np.exp(-1j * PHI)).imag * 1e3,
                "-", color="k", lw=1.0, label="baseline")
        for item, color in zip(items, colors):
            z = item["c0"] + item["cp"] * np.exp(1j * PHI) + item["cm"] * np.exp(-1j * PHI)
            ax.plot(z.real * 1e3, z.imag * 1e3, "-", lw=0.9, color=color, label=f"{item[key]:g}")
            ax.plot(item["c0"].real * 1e3, item["c0"].imag * 1e3, "o", ms=1.8, color=color)
        if tailoff is not None:
            z = tailoff["c0"] + tailoff["cp"] * np.exp(1j * PHI) + tailoff["cm"] * np.exp(-1j * PHI)
            ax.plot(z.real * 1e3, z.imag * 1e3, "--", lw=0.8, color="0.45", label="tail off")
        ax.axhline(0.0, color="0.85", lw=0.4)
        ax.axvline(0.0, color="0.85", lw=0.4)
        ax.set_aspect("equal", adjustable="datalim")
        ax.margins(0.06)
        format_axis(ax, xlabel=r"Re $Z$ (mV)", ylabel=r"Im $Z$ (mV)")
        ax.set_title(f"(a) {title} phasor", fontsize=6.5)
        style_legend(ax, loc="upper left", fontsize=4.6, frameon=False)

        comp_ax.plot(values, [abs(i["c0"]) * 1e3 for i in series], "o-", ms=3, lw=0.8, label=r"$|c_0|$")
        comp_ax.plot(values, [abs(i["cp"]) * 1e3 for i in series], "s-", ms=3, lw=0.8, label=r"$|c_{+1}|$")
        comp_ax.plot(values, [abs(i["cm"]) * 1e3 for i in series], "^-", ms=3, lw=0.8, label=r"$|c_{-1}|$")
        x_ref = min(values) - (max(values) - min(values)) * 0.12
        if reference is not None:
            comp_ax.annotate("tail off", (x_ref, abs(reference["c0"]) * 1e3),
                             textcoords="offset points", xytext=(3, 5), fontsize=5, color="0.45")
            for k, style in (("c0", "o"), ("cp", "s"), ("cm", "^")):
                comp_ax.plot([x_ref], [abs(reference[k]) * 1e3], style, ms=3, mfc="none", color="0.45")
        comp_ax.axvline(baseline[key], color="0.6", ls=":", lw=0.7)
        if reference is not None:
            comp_ax.set_xlim(x_ref - 0.02, max(values) + 0.03)
        comp_ax.set_ylim(0.0, max(max(abs(i[k]) * 1e3 for k in ("c0", "cp", "cm")) for i in series) * 1.15)
        slope_ax = comp_ax.twinx()
        slope_color = "#6d28d9"
        slopes = [i["sensitivity"] for i in series]
        slope_ax.plot(values, slopes, "D:", ms=2.4, lw=0.8, mfc="none", color=slope_color)
        slope_ax.set_ylim(0.0, max(slopes) * 1.12)
        slope_ax.set_ylabel(r"$|\mathrm{d}Z/\mathrm{d}\varphi|$ (mV/deg)", color=slope_color)
        slope_ax.tick_params(axis="y", colors=slope_color, labelsize=6)
        format_axis(comp_ax, xlabel="", ylabel="component (mV)")
        comp_ax.set_title(f"(b) {title} components", fontsize=6.5)
        style_legend(comp_ax, loc="upper center", fontsize=5.0, ncol=3, frameon=False)

        best_ax.plot(values, [representative(i) for i in series], "o-", ms=3, lw=0.8, color="0.2")
        if reference is not None:
            best_ax.plot([x_ref], [representative(reference)], "o", ms=3, mfc="none", color="0.45")
        best_ax.axvline(baseline[key], color="0.6", ls=":", lw=0.7)
        format_axis(best_ax, xlabel=xlabel, ylabel=r"best $\varphi$ (deg)")
        best_ax.set_title(f"(c) {title} best phase", fontsize=6.5)
        save_figure(figure, OUT / f"z06_{name}.png")


def main():
    runs = collect()
    if len(runs) < 10:
        raise SystemExit(f"运行不足：{len(runs)}")
    by_tag = {item["tag"]: item for item in runs}
    base = by_tag.get("z06w803")
    if base is None:
        raise SystemExit("缺少 z06w803")
    OUT.mkdir(parents=True, exist_ok=True)
    draw("width", "pulse width", r"$w$ (µs)",
         [item for item in runs if re.fullmatch(r"z06w(4|6|10|12)", item["tag"])], "width", base)
    draw("sf", "tail fraction", r"$s_f$",
         [item for item in runs if re.fullmatch(r"z06sf(34|44|64|74)", item["tag"])], "sf", base,
         tailoff=by_tag.get("z06tailoff"))
    draw("te", "tail time", r"$\tau_e$ (µs)",
         [item for item in runs if re.fullmatch(r"z06te(369|569)", item["tag"])], "te", base)
    draw("edge", "edge width", r"edge (µs)",
         [item for item in runs if re.fullmatch(r"z06e(05|15)", item["tag"])], "edge", base)

    with (OUT / "z06_components.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["tag", "run_dir", "width_us", "edge_us", "tail_on", "sf", "te_us",
                         "field_mA", "c0_mV", "cplus_mV", "cminus_mV", "slope_mV_per_deg", "best_deg"])
        for item in sorted(runs, key=lambda i: i["tag"]):
            writer.writerow([item["tag"], item["dir"].name, item["width"], item["edge"], item["tail"],
                             item["sf"], item["te"], "%.2f" % item["field"],
                             "%.6g" % (abs(item["c0"]) * 1e3), "%.6g" % (abs(item["cp"]) * 1e3),
                             "%.6g" % (abs(item["cm"]) * 1e3), "%.6g" % item["sensitivity"],
                             "%.6g" % representative(item)])
    print(OUT / "z06_width.png")
    print(OUT / "z06_sf.png")
    print(OUT / "z06_te.png")
    print(OUT / "z06_edge.png")
    print(OUT / "z06_components.csv")


if __name__ == "__main__":
    main()
