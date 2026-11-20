"""每个参数单独一张图：相量椭圆 + 三分量 + 最佳相位。

宽度、s_f、τe、沿宽各一张，互不叠加，便于解耦查看。
只读运行目录，产物写 param_scan/figures/param_*.png。
"""
from __future__ import annotations

from lab_workflows.common import data_root
import json
import math
from pathlib import Path

import numpy as np
import yaml

ROOT = (data_root() / "rf_phase_response")
OUT = Path(__file__).resolve().parent.parent / "figures"
BASELINE = ROOT / "1119_092825_rf_phase_response"


def load(run_dir: Path):
    config = yaml.safe_load((run_dir / "experiment_config.yaml").read_text(encoding="utf-8"))
    summary = json.loads((run_dir / "results/phase_response_summary.json").read_text(encoding="utf-8"))
    params = config["parameters"]
    comp = summary["components"]

    def z(key):
        return complex(comp[key]["re_v"], comp[key]["im_v"])

    return dict(dir=run_dir, tag=str(params.get("RUN_TAG")), width=float(params["Z_PULSE_WIDTH_US"]),
                edge=float(params["Z_PULSE_EDGE_US"]), sf=float(params["Z_TAIL_S_F"]),
                te=float(params["Z_TAIL_TAU_E_US"]), tail=bool(params["Z_TAIL_COMP_ENABLED"]),
                c0=z("c0"), cp=z("c_plus1"), cm=z("c_minus1"),
                best=float(summary["best_phase_deg"]),
                sensitivity=float(summary.get("sensitivity_max_v_per_deg")
                                  or (abs(comp["c_plus1"]["abs_v"]) + abs(comp["c_minus1"]["abs_v"]))
                                  * math.pi / 180.0) * 1e3)


BY_TAG = {}
for path in ROOT.glob("*/experiment_config.yaml"):
    try:
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
        tag = str(config["parameters"].get("RUN_TAG"))
    except Exception:
        continue
    if (path.parent / "results/phase_response_summary.json").is_file():
        BY_TAG[tag] = load(path.parent)
BASE = load(BASELINE)
OFF = BY_TAG.get("tailoff")

PHI = np.radians(np.arange(0.0, 360.0, 0.5))


def curve(item):
    return (item["c0"] + item["cp"] * np.exp(1j * PHI) + item["cm"] * np.exp(-1j * PHI)) * 1e3


def draw(name, title, xlabel, items, baseline_value, reference=None):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.gridspec import GridSpec
    from lab_workflows.plotting import format_axis, plot_style_context, save_figure, style_legend

    items = sorted(items, key=lambda item: item["latest"])
    series = sorted(items + [dict(BASE, latest=baseline_value)], key=lambda item: item["latest"])
    values = [item["latest"] for item in series]
    colors = plt.cm.viridis(np.linspace(0.15, 0.9, len(items)))
    with plot_style_context("paper"):
        figure = plt.figure(figsize=(7.4, 3.8))
        grid = GridSpec(2, 2, figure=figure, width_ratios=[1.1, 1.0], wspace=0.32, hspace=0.28)
        phasor = figure.add_subplot(grid[:, 0])
        comp_ax = figure.add_subplot(grid[0, 1])
        best_ax = figure.add_subplot(grid[1, 1], sharex=comp_ax)

        phasor.plot(curve(BASE).real, curve(BASE).imag, "-", color="k", lw=1.0, label="baseline")
        phasor.plot(BASE["c0"].real * 1e3, BASE["c0"].imag * 1e3, "o", ms=2.0, color="k")
        for item, color in zip(items, colors):
            phasor.plot(curve(item).real, curve(item).imag, "-", lw=0.9, color=color,
                        label=f"{item['latest']:g}")
            phasor.plot(item["c0"].real * 1e3, item["c0"].imag * 1e3, "o", ms=2.0, color=color)
        if reference is not None:
            phasor.plot(curve(reference).real, curve(reference).imag, "--", lw=0.8, color="0.45",
                        label="tail off")
            phasor.plot(reference["c0"].real * 1e3, reference["c0"].imag * 1e3, "o", ms=2.0, color="0.45")
        phasor.axhline(0.0, color="0.85", lw=0.4)
        phasor.axvline(0.0, color="0.85", lw=0.4)
        phasor.set_aspect("equal", adjustable="datalim")
        phasor.margins(0.08)
        format_axis(phasor, xlabel=r"Re $Z$ (mV)", ylabel=r"Im $Z$ (mV)")
        phasor.set_title(f"(a) phasor, {title}", fontsize=6.5)
        style_legend(phasor, loc="upper left", fontsize=5.0, frameon=False)

        comp_ax.plot(values, [abs(item["c0"]) * 1e3 for item in series], "o-", ms=3, lw=0.8, label=r"$|c_0|$")
        comp_ax.plot(values, [abs(item["cp"]) * 1e3 for item in series], "s-", ms=3, lw=0.8, label=r"$|c_{+1}|$")
        comp_ax.plot(values, [abs(item["cm"]) * 1e3 for item in series], "^-", ms=3, lw=0.8, label=r"$|c_{-1}|$")
        if reference is not None:
            for key, style in (("c0", "o"), ("cp", "s"), ("cm", "^")):
                comp_ax.plot([reference["latest"]], [abs(reference[key]) * 1e3], style, ms=3,
                             mfc="none", color="0.45")
            comp_ax.annotate("tail off", (reference["latest"], abs(reference["c0"]) * 1e3),
                             textcoords="offset points", xytext=(3, 6), fontsize=5, color="0.45")
        comp_ax.set_ylim(0.0, 14.0)
        comp_ax.axvline(baseline_value, color="0.6", ls=":", lw=0.7)
        for key, style in (("c0", "o"), ("cp", "s"), ("cm", "^")):
            comp_ax.plot([baseline_value], [abs(BASE[key]) * 1e3], style, ms=4, mfc="none", mew=0.9,
                         color="k", zorder=5)
        comp_ax.text(baseline_value, 0.2, "baseline", fontsize=5, ha="center", va="bottom",
                     color="0.35")
        slope_ax = comp_ax.twinx()
        slope_color = "#6d28d9"
        slopes = [(abs(item["cp"]) + abs(item["cm"])) * 1e3 * 3.141592653589793 / 180.0
                  for item in series]
        slope_ax.plot(values, slopes, "D:", ms=2.5, lw=0.8, mfc="none", color=slope_color)
        if reference is not None:
            slope_ax.plot([reference["latest"]],
                          [(abs(reference["cp"]) + abs(reference["cm"])) * 1e3 * 3.141592653589793 / 180.0],
                          "D", ms=2.5, mfc="none", color=slope_color)
        slope_ax.plot([baseline_value],
                      [(abs(BASE["cp"]) + abs(BASE["cm"])) * 1e3 * 3.141592653589793 / 180.0],
                      "D", ms=4, mfc="none", mew=0.9, color="k", zorder=5)
        slope_ax.set_ylim(0.18, 0.30)
        slope_ax.set_ylabel(r"$|\mathrm{d}Z/\mathrm{d}\varphi|$ (mV/deg)", color=slope_color)
        slope_ax.tick_params(axis="y", colors=slope_color, labelsize=6)
        format_axis(comp_ax, xlabel="", ylabel="component (mV)")
        comp_ax.set_title(f"(b) components, {title}", fontsize=6.5)
        style_legend(comp_ax, loc="upper center", fontsize=5.0, ncol=3, frameon=False)

        best_ax.plot(values, [item["best"] for item in series], "o-", ms=3, lw=0.8, color="0.2")
        if reference is not None:
            best_ax.plot([reference["latest"]], [reference["best"]], "o", ms=3, mfc="none", color="0.45")
        best_ax.set_ylim(94.0, 124.0)
        best_ax.axvline(baseline_value, color="0.6", ls=":", lw=0.7)
        best_ax.plot([baseline_value], [BASE["best"]], "o", ms=4, mfc="none", mew=0.9, color="k", zorder=5)
        format_axis(best_ax, xlabel=xlabel, ylabel=r"best $\varphi$ (deg)")
        best_ax.set_title("(c) best phase", fontsize=6.5)
        save_figure(figure, OUT / f"param_{name}.png")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    width_items = [BY_TAG[tag] | {"latest": BY_TAG[tag]["width"]} for tag in ("w4", "w6", "w10", "w12")]
    draw("width", "pulse width", r"$w$ ($\mu$s)", width_items, 8.03)

    sf_items = [BY_TAG[tag] | {"latest": BY_TAG[tag]["sf"]}
                for tag in ("sf34", "sf44", "sf64", "sf74")]
    draw("sf", "tail fraction $s_f$", r"$s_f$", sf_items, 0.544,
         reference=(OFF | {"latest": 0.30}) if OFF else None)

    te_items = [BY_TAG[tag] | {"latest": BY_TAG[tag]["te"]} for tag in ("te369", "te569")]
    draw("te", "tail time $\\tau_e$", r"$\tau_e$ ($\mu$s)", te_items, 46.9)

    edge_items = [BY_TAG[tag] | {"latest": BY_TAG[tag]["edge"]} for tag in ("edge05", "edge15")]
    draw("edge", "edge width", r"edge ($\mu$s)", edge_items, 1.0)
    print("written", OUT)


if __name__ == "__main__":
    main()
