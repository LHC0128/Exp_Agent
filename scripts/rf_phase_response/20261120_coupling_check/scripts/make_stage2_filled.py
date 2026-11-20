"""按阶段 1 的 Q1 判据填写阶段 2 清单的 z 列，另存 runlists/stage2_filled.csv。

判定为不耦合：所有网格点用 Z0(P0)；判定为耦合：用线性模型 Z0(w, s_f) 的预言值。
两种情形都四舍五入到 0.0005 mA；参考点固定 Z=-0.068 不跟随。
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import cc_common as cc

OUT = cc.RESULT_DIR
STEP_MA = 0.0005
# 用户 2026-11-20 指示：阶段 2 参考点随当天 Z 峰改到 −0.0655（交接方案原写 −0.068）。
REF_Z = -0.0655


def round_step(value):
    return round(value / STEP_MA) * STEP_MA


def main():
    fit = json.loads((OUT / "tables" / "stage1_fit.json").read_text(encoding="utf-8"))
    model = fit["linear_model"]
    coupled = fit["coupling"]
    z0_p0 = fit["positions"]["P0"]["Z0"]

    def predict(w, sf):
        if coupled:
            return model["Zc"] + model["a_w"] * (w - 8.03) + model["a_s"] * (sf - 0.575)
        return z0_p0

    source = OUT / "runlists" / "stage2.csv"
    (OUT / "runlists").mkdir(parents=True, exist_ok=True)
    target = OUT / "runlists" / "stage2_filled.csv"
    with source.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        if row["role"] == "ref":
            row["z"] = "%.4f" % REF_Z
        else:
            row["z"] = "%.4f" % round_step(predict(float(row["w"]), float(row["sf"])))
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    values = sorted({row["z"] for row in rows if row["role"] != "ref"})
    print(json.dumps({"coupled": coupled, "z0_p0": z0_p0, "grid_z_values": values,
                      "a_w": model["a_w"], "a_s": model["a_s"]}, ensure_ascii=False, indent=2))
    print(target)


if __name__ == "__main__":
    main()
