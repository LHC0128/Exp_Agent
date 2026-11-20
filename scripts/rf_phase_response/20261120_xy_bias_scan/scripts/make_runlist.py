"""生成 XY 偏置扫描的运行清单，顺序打乱、固定种子。

两轮：
- 第 1 轮（`xy_scan.csv`，5×5 + 5 参考点）：以 11-19 阶段 F 的 Jacobian 零点预言
  (X=+0.077, Y=−0.158) V 为中心，X 覆盖 0.00…0.16 V、Y 覆盖 −0.28…−0.04 V，找 |c0| 最小点。
- 第 2 轮（`ysweep.csv`，2×7 + 3 参考点）：X 停在第 1 轮的最优/拟合零点（0.16、0.19 V），
  Y 从 −0.04 延到 +0.20 V，找 S 是否在上边界之外继续上升。

参考点都用当前默认偏置 (−0.0409, −0.0069) V。
"""
from __future__ import annotations

import argparse
import csv
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "runlists"
REF = (-0.0409, -0.0069)
ROUNDS = {
    1: {"name": "xy_scan", "xs": (0.00, 0.04, 0.08, 0.12, 0.16),
        "ys": (-0.28, -0.22, -0.16, -0.10, -0.04), "prefix": "xy", "ref_every": 8, "seed": 20261120},
    2: {"name": "ysweep", "xs": (0.16, 0.19),
        "ys": (-0.04, 0.00, 0.04, 0.08, 0.12, 0.16, 0.20), "prefix": "ys", "ref_every": 7, "seed": 20261121},
}


def tag_of(prefix, x, y):
    sign = "p" if y >= 0 else "m"
    return f"{prefix}_x{round(x * 1000):03d}_y{sign}{abs(round(y * 1000)):03d}"


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--round", type=int, choices=sorted(ROUNDS), default=1)
    args = parser.parse_args(argv)
    spec = ROUNDS[args.round]

    points = [(x, y) for x in spec["xs"] for y in spec["ys"]]
    random.Random(spec["seed"]).shuffle(points)
    rows, order, refs_used = [], 1, 0

    def add_ref():
        nonlocal order, refs_used
        rows.append([order, f"{spec['prefix']}_ref{refs_used:02d}", "ref", "%.4f" % REF[0], "%.4f" % REF[1]])
        refs_used += 1
        order += 1

    add_ref()
    for index, (x, y) in enumerate(points, start=1):
        rows.append([order, tag_of(spec["prefix"], x, y), "grid", "%.4f" % x, "%.4f" % y])
        order += 1
        if index % spec["ref_every"] == 0 and index < len(points):
            add_ref()
    add_ref()

    target = ROOT / f"{spec['name']}.csv"
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["order", "tag", "role", "x", "y"])
        writer.writerows(rows)
    print(f"{target}  共 {len(rows)} 行（网格 {len(points)} + 参考 {refs_used}）")


if __name__ == "__main__":
    main()
