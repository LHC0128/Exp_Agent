"""按阶段 1 拟合与阶段 2 驻点生成阶段 3 清单：A 当前最优、B 网格最优、C 干净 π，ABC 交替。

z 的取法按交接方案：A、C 用阶段 1 的峰位拟合值，B 用阶段 1 的线性模型预言值；
都四舍五入到 0.0005 mA。驻点若落在网格外，B 取最近的网格点。
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import cc_common as cc
import model_area

OUT = Path(__file__).resolve().parent.parent
STEP_Z = 0.0005
GRID_W = (7.5, 7.75, 8.03, 8.3, 8.6)
GRID_SF = (0.50, 0.54, 0.575, 0.61, 0.65)
A = (8.03, 0.575, 38.3, "A")
C = (8.03, 0.544, 46.9, "C")


def round_step(value, step):
    return round(value / step) * step


def main():
    stage1 = json.loads((OUT / "data" / "stage1_fit.json").read_text(encoding="utf-8"))
    stage2 = json.loads((OUT / "data" / "stage2_fit.json").read_text(encoding="utf-8"))
    model = stage1["linear_model"]
    stationary = stage2["surface"]["stationary"]

    def predict(w, sf):
        if stage1["coupling"]:
            return model["Zc"] + model["a_w"] * (w - 8.03) + model["a_s"] * (sf - 0.575)
        return stage1["positions"]["P0"]["Z0"]

    if stationary["in_grid"]:
        b_w = round_step(stationary["w"], 0.05)
        b_sf = round_step(stationary["sf"], 0.005)
        b_w = min(max(b_w, GRID_W[0]), GRID_W[-1])
        b_sf = min(max(b_sf, GRID_SF[0]), GRID_SF[-1])
    else:
        b_w = min(GRID_W, key=lambda value: abs(value - stationary["w"]))
        b_sf = min(GRID_SF, key=lambda value: abs(value - stationary["sf"]))
    b_z = round_step(predict(b_w, b_sf), STEP_Z)
    a_z = round_step(stage1["positions"]["P0"]["Z0"], STEP_Z)
    c_z = round_step(stage1["positions"]["P5"]["Z0"], STEP_Z)

    waves = {"A": (A[0], A[1], A[2], a_z), "B": (b_w, b_sf, 38.3, b_z), "C": (C[0], C[1], C[2], c_z)}
    target = OUT / "runlists" / "stage3.csv"
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["order", "tag", "role", "position", "z", "w", "sf", "te", "area_total"])
        order = 1
        for repeat in range(1, 4):
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
