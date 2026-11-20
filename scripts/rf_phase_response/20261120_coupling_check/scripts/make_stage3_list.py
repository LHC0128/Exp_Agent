"""按阶段 1 拟合与阶段 2 驻点生成阶段 3 清单：A 当前最优、B 网格最优、C 干净 π，ABC 交替。

z 的取法按交接方案：A、C 用阶段 1 的峰位拟合值，B 用阶段 1 的线性模型预言值；
都四舍五入到 0.0005 mA。驻点若落在网格外，B 取最近的网格点。

2026-11-20 用户决定：B 取 8.6/0.50/38.3（模型转角 0.999，与 C 同转角，检验沿脊方向），
交替 5 轮；生成命令 `make_stage3_list.py --b-w 8.6 --b-sf 0.50 --rounds 5`。
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import cc_common as cc
import model_area

OUT = cc.RESULT_DIR
STEP_Z = 0.0005
GRID_W = (7.5, 7.75, 8.03, 8.3, 8.6)
GRID_SF = (0.50, 0.54, 0.575, 0.61, 0.65)
A = (8.03, 0.575, 38.3, "A")
C = (8.03, 0.544, 46.9, "C")


def round_step(value, step):
    return round(value / step) * step


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--b-w", type=float, default=None, help="B 的脉宽，默认取阶段 2 实测 r 最大的网格点")
    parser.add_argument("--b-sf", type=float, default=None, help="B 的 s_f，默认取阶段 2 实测 r 最大的网格点")
    parser.add_argument("--rounds", type=int, default=5, help="ABC 交替轮数（标签 cc3_<x><轮号> 只支持 1–9）")
    args = parser.parse_args(argv)
    if not 1 <= args.rounds <= 9:
        raise SystemExit("--rounds 须在 1–9 之间")
    stage1 = json.loads((OUT / "tables" / "stage1_fit.json").read_text(encoding="utf-8"))
    stage2 = json.loads((OUT / "tables" / "stage2_fit.json").read_text(encoding="utf-8"))
    model = stage1["linear_model"]
    stationary = stage2["surface"]["stationary"]

    def predict(w, sf):
        if stage1["coupling"]:
            return model["Zc"] + model["a_w"] * (w - 8.03) + model["a_s"] * (sf - 0.575)
        return stage1["positions"]["P0"]["Z0"]

    if args.b_w is not None or args.b_sf is not None:
        b_w = args.b_w if args.b_w is not None else stationary["w"]
        b_sf = args.b_sf if args.b_sf is not None else stationary["sf"]
    else:
        # 脊的情形没有内点极大：取阶段 2 实测 r 最大的网格点作为“网格最优”。
        rows = cc.load("stage2")
        cc.drift_normalise(rows)
        best = max((row for row in rows if row["role"] != "ref"), key=lambda row: row["r"])
        b_w, b_sf = best["w"], best["sf"]
    b_z = round_step(predict(b_w, b_sf), STEP_Z)
    a_z = round_step(stage1["positions"]["P0"]["Z0"], STEP_Z)
    c_z = round_step(stage1["positions"]["P5"]["Z0"], STEP_Z)

    waves = {"A": (A[0], A[1], A[2], a_z), "B": (b_w, b_sf, 38.3, b_z), "C": (C[0], C[1], C[2], c_z)}
    (OUT / "runlists").mkdir(parents=True, exist_ok=True)
    target = OUT / "runlists" / "stage3.csv"
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["order", "tag", "role", "position", "z", "w", "sf", "te", "area_total"])
        order = 1
        for repeat in range(1, args.rounds + 1):
            for letter in ("A", "B", "C"):
                w, sf, te, z = waves[letter]
                area = model_area.relative_area(w, sf, te)[2]
                writer.writerow([order, f"cc3_{letter.lower()}{repeat}", "pair", letter, "%.4f" % z,
                                 "%.2f" % w, "%g" % sf, "%.1f" % te, area])
                order += 1
    print(json.dumps({"stationary": stationary, "B": {"w": b_w, "sf": b_sf, "z": b_z},
                      "A_z": a_z, "C_z": c_z}, ensure_ascii=False, indent=2))
    print(target)


if __name__ == "__main__":
    main()
