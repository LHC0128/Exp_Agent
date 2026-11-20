"""跨运行对比：射频相位响应参数扫描。

读取一批 rf_phase_response 运行目录的摘要，输出
- 相量椭圆叠加图（左：宽度扫描；右：拖尾/沿宽）
- |c0|/|c+1|/|c−1| 与最佳相位随条件的变化
- components.csv 汇总
只读运行目录，产物写 param_scan/figures/。
"""
from __future__ import annotations

from lab_workflows.common import data_root
import csv
import json
from pathlib import Path

import numpy as np
import yaml

ROOT = (data_root() / "rf_phase_response")
OUT = Path(__file__).resolve().parent.parent / "figures"
BASELINE = ROOT / "1119_092825_rf_phase_response"

WIDTH_TAGS = ("w4", "w6", "w10", "w12")
TAIL_TAGS = ("tailoff", "sf34", "sf44", "sf64", "sf74")
TAU_TAGS = ("te369", "te569")
EDGE_TAGS = ("edge05", "edge15")
ALL_TAGS = WIDTH_TAGS + TAIL_TAGS + TAU_TAGS + EDGE_TAGS


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
                best=float(summary["best_phase_deg"]), residual=float(summary["residual_rms_v"]))


def collect():
    runs = []
    for path in sorted(ROOT.glob("*/experiment_config.yaml")):
        run_dir = path.parent
        try:
            config = yaml.safe_load(path.read_text(encoding="utf-8"))
            tag = str(config["parameters"].get("RUN_TAG"))
        except Exception:
            continue
        if (tag in ALL_TAGS or run_dir == BASELINE) and (run_dir / "results/phase_response_summary.json").is_file():
            runs.append(load(run_dir))
    return runs


def group_of(item):
    if item["dir"] == BASELINE:
        return "baseline"
    for name, tags in (("width", WIDTH_TAGS), ("tail", TAIL_TAGS), ("tau", TAU_TAGS), ("edge", EDGE_TAGS)):
        if item["tag"] in tags:
            return name
    return "other"


def main():
    runs = collect()
    if not runs:
        raise SystemExit("没有找到带结果的运行目录")
    OUT.mkdir(parents=True, exist_ok=True)
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from lab_workflows.plotting import format_axis, plot_style_context, save_figure, style_legend

    phi = np.radians(np.arange(0.0, 360.0, 0.5))
    baseline = next(item for item in runs if item["dir"] == BASELINE)
    width_runs = sorted((item for item in runs if group_of(item) == "width"), key=lambda item: item["width"])
    other_runs = [item for item in runs if group_of(item) in {"tail", "tau", "edge"}]
    order = (["tailoff"] if any(item["tag"] == "tailoff" for item in other_runs) else [])
    other_runs.sort(key=lambda item: {"tail": 0, "tau": 1, "edge": 2}[group_of(item)])

    def curve(item):
        return (item["c0"] + item["cp"] * np.exp(1j * phi)
                + item["cm"] * np.exp(-1j * phi)) * 1e3

    with plot_style_context("paper"):
        figure, (left, right) = plt.subplots(1, 2, figsize=(7.2, 3.6))
        width_colors = plt.cm.Blues(np.linspace(0.35, 0.95, len(width_runs)))
        left.plot(curve(baseline).real, curve(baseline).imag, "-", color="k", lw=1.0,
                  label="baseline 8.03")
        left.plot(baseline["c0"].real * 1e3, baseline["c0"].imag * 1e3, "o", ms=2.0, color="k")
        for item, color in zip(width_runs, width_colors):
            left.plot(curve(item).real, curve(item).imag, "-", lw=1.0, color=color,
                      label=f"w = {item['width']:g} µs")
            left.plot(item["c0"].real * 1e3, item["c0"].imag * 1e3, "o", ms=2.0, color=color)

        right.plot(curve(baseline).real, curve(baseline).imag, "-", color="k", lw=1.0,
                   label="baseline 8.03")
        colors = {"tail": plt.cm.Reds(np.linspace(0.4, 0.95, len(TAIL_TAGS))),
                  "tau": plt.cm.Greens(np.linspace(0.45, 0.9, len(TAU_TAGS))),
                  "edge": plt.cm.Oranges(np.linspace(0.5, 0.9, len(EDGE_TAGS)))}
        counts = {"tail": 0, "tau": 0, "edge": 0}
        for item in other_runs:
            group = group_of(item)
            color = colors[group][counts[group]]
            counts[group] += 1
            label = {"tail": f"$s_f$ = {item['sf']:.2f}" if item["tag"] != "tailoff" else "tail off",
                     "tau": rf"$\tau_e$ = {item['te']:g} µs",
                     "edge": f"edge = {item['edge']:g} µs"}[group]
            right.plot(curve(item).real, curve(item).imag, "-", lw=1.0, color=color, label=label)
            right.plot(item["c0"].real * 1e3, item["c0"].imag * 1e3, "o", ms=2.0, color=color)

        for axis, title in ((left, "(a) pulse width"), (right, "(b) tail and edge")):
            axis.axhline(0.0, color="0.85", lw=0.4)
            axis.axvline(0.0, color="0.85", lw=0.4)
            axis.set_aspect("equal", adjustable="datalim")
            axis.margins(0.08)
            format_axis(axis, xlabel=r"Re $Z$ (mV)", ylabel=r"Im $Z$ (mV)")
            axis.set_title(title, fontsize=7)
            style_legend(axis, loc="upper left", fontsize=5.0, frameon=False)
        save_figure(figure, OUT / "phasors_overlay.png")

        rank = {"baseline": 0, "width": 1, "tail": 2, "tau": 3, "edge": 4}

        def sort_key(item):
            group = group_of(item)
            physical = {"baseline": 0.0, "width": item["width"], "tail": item["sf"],
                        "tau": item["te"], "edge": item["edge"]}[group]
            return (rank[group], physical)

        ordered = sorted(runs, key=sort_key)
        labels = ["base" if item["dir"] == BASELINE else item["tag"] for item in ordered]
        x = np.arange(len(ordered))
        figure, (top, bottom) = plt.subplots(2, 1, figsize=(7.0, 3.8), sharex=True,
                                            gridspec_kw={"height_ratios": [2, 1]})
        top.plot(x, [abs(item["c0"]) * 1e3 for item in ordered], "o-", ms=3, lw=0.8, label=r"$|c_0|$")
        top.plot(x, [abs(item["cp"]) * 1e3 for item in ordered], "s-", ms=3, lw=0.8, label=r"$|c_{+1}|$")
        top.plot(x, [abs(item["cm"]) * 1e3 for item in ordered], "^-", ms=3, lw=0.8, label=r"$|c_{-1}|$")
        format_axis(top, xlabel="", ylabel="component (mV)")
        style_legend(top, loc="upper right", fontsize=5.5, ncol=3, frameon=False)
        bottom.plot(x, [item["best"] for item in ordered], "o", ms=3, color="0.2")
        format_axis(bottom, xlabel="condition", ylabel=r"best $\varphi$ (deg)")
        bottom.set_xticks(x)
        bottom.set_xticklabels(labels, rotation=60, ha="right", fontsize=5)
        save_figure(figure, OUT / "components_vs_condition.png")

    with (OUT / "components.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["tag", "run_dir", "width_us", "edge_us", "tail_on", "sf", "te_us",
                         "c0_mV", "cplus_mV", "cminus_mV", "best_deg", "residual_V"])
        for item in ordered:
            writer.writerow([item["tag"] or "baseline", item["dir"].name, item["width"], item["edge"],
                             item["tail"], item["sf"], item["te"], "%.6g" % (abs(item["c0"]) * 1e3),
                             "%.6g" % (abs(item["cp"]) * 1e3), "%.6g" % (abs(item["cm"]) * 1e3),
                             "%.6g" % item["best"], "%.6g" % item["residual"]])
    print(OUT / "phasors_overlay.png")
    print(OUT / "components_vs_condition.png")
    print(OUT / "components.csv")


if __name__ == "__main__":
    main()
