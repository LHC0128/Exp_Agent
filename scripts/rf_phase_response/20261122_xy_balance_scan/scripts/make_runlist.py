"""XY 平衡方格扫描的清单生成：1 Vpp 射频下扫 X/Y 偏置，相位步进 5°。

用法：python scripts/rf_phase_response/20261122_xy_balance_scan/scripts/make_runlist.py
输出 runlists/balance_scan.csv，列：order, tag, role, x, y；顺序按固定种子打乱，首尾各一个参考点。
"""
from __future__ import annotations

import csv
import random
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "runlists"
SEED = 20261122
GRIDS = {
    "narrow": ((0.13, 0.16, 0.19, 0.22, 0.25), (-0.08, -0.04, 0.00, 0.04, 0.08)),
    "wide": ((-0.50, -0.25, 0.00, 0.25, 0.50), (-0.50, -0.25, 0.00, 0.25, 0.50)),
}
REF = (0.19, 0.00)


def _signed(value):
    return ("p" if value >= 0 else "m") + "%03d" % abs(round(value * 1000))


def tag_for(x, y):
    return f"bal_x{_signed(x)}_y{_signed(y)}"


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--grid", default="narrow", choices=sorted(GRIDS))
    args = parser.parse_args()
    xs, ys = GRIDS[args.grid]
    points = [(x, y) for x in xs for y in ys]
    random.Random(SEED).shuffle(points)
    rows = [{"order": 1, "tag": "bal_ref00", "role": "ref",
             "x": f"{REF[0]:g}", "y": f"{REF[1]:g}"}]
    for index, (x, y) in enumerate(points):
        rows.append({"order": index + 2, "tag": tag_for(x, y), "role": "grid", "x": f"{x:g}", "y": f"{y:g}"})
        if index == 12:
            rows.append({"order": index + 3, "tag": "bal_ref01", "role": "ref",
                         "x": f"{REF[0]:g}", "y": f"{REF[1]:g}"})
    rows.append({"order": len(rows) + 1, "tag": "bal_ref02", "role": "ref",
                 "x": f"{REF[0]:g}", "y": f"{REF[1]:g}"})
    for order, row in enumerate(rows, start=1):
        row["order"] = str(order)

    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / ("balance_scan.csv" if args.grid == "narrow" else f"balance_{args.grid}.csv")
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["order", "tag", "role", "x", "y"])
        writer.writeheader()
        writer.writerows(rows)
    grid = [row for row in rows if row["role"] == "grid"]
    print(f"{target}  共 {len(rows)} 行（网格 {len(grid)} + 参考 {len(rows) - len(grid)}）")
    print("X", sorted({row['x'] for row in grid}), "Y", sorted({row['y'] for row in grid}))


if __name__ == "__main__":
    main()
