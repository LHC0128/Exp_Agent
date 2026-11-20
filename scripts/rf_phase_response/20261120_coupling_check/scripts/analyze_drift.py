"""各阶段参考点随时间的变化：漂移曲线与相邻参考点相对变化。

输入 coupling_check/data/<stage>_runs.jsonl（存在的阶段都画）；输出 figures/drift.png 与 data/drift_summary.json。
"""
from __future__ import annotations

import json
from pathlib import Path

import cc_common as cc

OUT = cc.RESULT_DIR
STAGES = (("stage1", "stage 1 Z check"), ("stage2", "stage 2 w x sf grid"), ("stage3", "stage 3 A/B/C"))


def main():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from lab_workflows.plotting import format_axis, plot_style_context, save_figure, style_legend

    present = []
    record = {}
    for stage, label in STAGES:
        rows = cc.load(stage)
        if not rows:
            continue
        if not any(row["role"] == "ref" for row in rows):
            # 阶段 3 是轮内配对（A 即该轮参考），没有独立参考点，不做漂移归一。
            record[stage] = {"label": label, "refs": [], "gaps": [], "max_abs_change": None,
                             "pairs": len({row["tag"][-1] for row in rows}),
                             "scan_points": len(rows), "note": "轮内配对，无独立参考点"}
            continue
        summary = cc.drift_normalise(rows)
        base = min(item["time"] for item in summary["refs"])
        present.append((label, summary, base))
        record[stage] = {
            "label": label,
            "max_abs_change": summary["max_abs_change"],
            "gaps": [{"from": item["from"], "to": item["to"], "change": item["change"],
                      "flagged": item["flagged"]} for item in summary["gaps"]],
            "refs": [{"tag": item["tag"], "S_mV_per_deg": item["S_mV_per_deg"],
                      "time": item["time"].isoformat(timespec="seconds")} for item in summary["refs"]],
            "scan_points": len([row for row in rows if row["role"] != "ref"]),
        }
    if not present:
        raise SystemExit("没有任何阶段的参考点数据")

    figures = OUT / "figures"
    figures.mkdir(parents=True, exist_ok=True)
    with plot_style_context("paper"):
        figure, axis = plt.subplots(figsize=(5.2, 2.8))
        for index, (label, summary, base) in enumerate(present):
            minutes = [(item["time"] - base).total_seconds() / 60.0 for item in summary["refs"]]
            values = [item["S_mV_per_deg"] for item in summary["refs"]]
            axis.plot(minutes, values, "o-", ms=3.0, lw=0.9, color=f"C{index}", label=label)
            for minute, value, item in zip(minutes, values, summary["refs"]):
                axis.annotate(item["tag"].split("_")[-1], (minute, value), textcoords="offset points",
                              xytext=(0, 5), fontsize=4.2, ha="center")
        format_axis(axis, xlabel="elapsed time within stage (min)", ylabel="reference S (mV/deg)")
        style_legend(axis, loc="lower left", fontsize=5, frameon=False)
        save_figure(figure, figures / "drift.png")

    TABLES = OUT / "tables"
    TABLES.mkdir(parents=True, exist_ok=True)
    (TABLES / "drift_summary.json").write_text(json.dumps(record, ensure_ascii=False, indent=2),
                                                     encoding="utf-8")
    for stage, item in record.items():
        if item["max_abs_change"] is None:
            print(f"{stage}: {item['note']}（{item['scan_points']} 点）")
            continue
        flagged = [gap for gap in item["gaps"] if gap["flagged"]]
        print(f"{stage}: refs={len(item['refs'])} max|Δ|={item['max_abs_change']:.3%} flagged={len(flagged)}")
        for gap in flagged:
            print(f"   flagged {gap['from']} -> {gap['to']}: {gap['change']:+.2%}")


if __name__ == "__main__":
    main()
