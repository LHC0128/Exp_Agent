"""阶段 3（三点对比）分析：轮内配对比值 B/A、C/A 与标准误。

输入 coupling_check/data/stage3_runs.jsonl；输出 data/stage3.csv、data/stage3_pairs.json 与 figures/stage3_pairs.png。
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

import numpy as np

import cc_common as cc

OUT = Path(__file__).resolve().parent.parent
ROUND = re.compile(r"^cc3_([abc])(\d)$")
LABELS = {"a": "A current optimum", "b": "B stage-2 optimum", "c": "C clean π"}


def main():
    rows = cc.load("stage3")
    if not rows:
        raise SystemExit("stage3_runs.jsonl 为空：阶段 3 还没有数据")
    rounds = {}
    for row in rows:
        match = ROUND.match(row["tag"])
        if not match:
            continue
        rounds.setdefault(int(match.group(2)), {})[match.group(1)] = row
    complete = {index: items for index, items in rounds.items() if {"a", "b", "c"} <= set(items)}

    # 阶段 3 用轮内配对，A 即该轮的参考：S_ref 取同轮 A，r 为配对比值。
    for row in rows:
        match = ROUND.match(row["tag"])
        index = int(match.group(2)) if match else 0
        anchor = rounds.get(index, {}).get("a")
        row["S_ref"] = anchor["S_mV_per_deg"] if anchor else float("nan")
        row["r"] = row["S_mV_per_deg"] / row["S_ref"] if anchor else float("nan")

    OUT.joinpath("data").mkdir(parents=True, exist_ok=True)
    with (OUT / "data" / "stage3.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["tag", "run_dir", "Z", "w", "sf", "te", "S_mV_per_deg", "c0_mV", "S_ref", "r", "area_total"])
        for row in rows:
            writer.writerow([row["tag"], Path(row["run_dir"]).name, "%.4f" % row["z"], "%.3f" % row["w"],
                             "%.3f" % row["sf"], "%.1f" % row["te"], "%.4f" % row["S_mV_per_deg"],
                             "%.2f" % row["c0_mV"], "%.4f" % row["S_ref"], "%.4f" % row["r"],
                             "%.4f" % row["area_total"]])

    if not complete:
        print("没有完整的一轮 A/B/C")
        return
    ratios = {"b_over_a": [], "c_over_a": []}
    for index in sorted(complete):
        items = complete[index]
        ratios["b_over_a"].append(items["b"]["S_mV_per_deg"] / items["a"]["S_mV_per_deg"])
        ratios["c_over_a"].append(items["c"]["S_mV_per_deg"] / items["a"]["S_mV_per_deg"])
    record = {"rounds": len(complete), "ratios": {}}
    for key, values in ratios.items():
        array = np.array(values)
        sem = float(np.std(array, ddof=1) / np.sqrt(array.size)) if array.size > 1 else float("nan")
        record["ratios"][key] = {"values": values, "mean": float(np.mean(array)), "sem": sem,
                                 "significant_vs_1": bool(abs(np.mean(array) - 1) > 2 * sem) if sem > 0 else None}

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from lab_workflows.plotting import format_axis, plot_style_context, save_figure, style_legend

    figures = OUT / "figures"
    figures.mkdir(parents=True, exist_ok=True)
    with plot_style_context("paper"):
        figure, axis = plt.subplots(figsize=(3.6, 2.8))
        for index, (key, label) in enumerate((("b_over_a", "B / A"), ("c_over_a", "C / A"))):
            values = np.array(record["ratios"][key]["values"])
            axis.errorbar([index], [values.mean()], yerr=[record["ratios"][key]["sem"]], fmt="o",
                          ms=4, capsize=3, color=f"C{index}", label=label)
            axis.plot(np.full(values.size, index) + np.linspace(-0.08, 0.08, values.size), values, ".",
                      ms=4, color=f"C{index}")
        axis.axhline(1.0, color="0.5", ls=":", lw=0.8)
        axis.set_xticks([0, 1], [LABELS["b"].replace("stage-2 optimum", "stage 2"), LABELS["c"]])
        format_axis(axis, ylabel="paired sensitivity ratio")
        style_legend(axis, loc="best", fontsize=5, frameon=False)
        save_figure(figure, figures / "stage3_pairs.png")

    (OUT / "data" / "stage3_pairs.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(record, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
