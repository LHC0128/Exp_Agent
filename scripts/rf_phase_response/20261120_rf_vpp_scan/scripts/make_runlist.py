"""射频幅度扫描（Y_RF_VPP）的清单生成：20–200 步 20、200–1000 步 100，加参考点。

用法：python scripts/rf_phase_response/20261120_rf_vpp_scan/scripts/make_runlist.py [--round y]
输出 runlists/<round>_sweep.csv，列：order, tag, role, vpp；顺序按固定种子打乱，参考点每 4–5 点插一次。
"""
from __future__ import annotations

import argparse
import csv
import random
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "runlists"
REF_VPP = 0.05
SEED = 20261120

LOW = [0.02 * index for index in range(1, 11)]          # 20…200 mVpp，步 20
HIGH = [0.1 * index for index in range(3, 11)]          # 300…1000 mVpp，步 100


def tag_for(vpp):
    return f"vpp_{int(round(vpp * 1000)):04d}"


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--round", default="y", choices=["y", "x"], help="扫描方向，决定文件名与标签前缀")
    args = parser.parse_args(argv)

    values = LOW + HIGH
    random.Random(SEED).shuffle(values)
    prefix = args.round
    direction = "X" if args.round == "x" else "Y"
    points = [(prefix, vpp) for vpp in values]

    rows = []
    order = 1
    since_ref = 0
    ref_index = 0
    for index, (axis, vpp) in enumerate(points):
        if since_ref == 0:
            rows.append({"order": order, "tag": f"{axis}_ref{ref_index:02d}", "role": "ref",
                         "vpp": f"{REF_VPP:g}", "direction": direction})
            order += 1
            ref_index += 1
        rows.append({"order": order, "tag": f"{axis}_{tag_for(vpp)}", "role": "grid", "vpp": f"{vpp:g}",
                     "direction": direction})
        order += 1
        since_ref += 1
        if since_ref >= (4 if index < 8 else 5):
            since_ref = 0
    rows.append({"order": order, "tag": f"{prefix}_ref{ref_index:02d}", "role": "ref", "vpp": f"{REF_VPP:g}",
                 "direction": direction})

    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / f"{args.round}_sweep.csv"
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["order", "tag", "role", "vpp", "direction"])
        writer.writeheader()
        writer.writerows(rows)
    grid = [float(row["vpp"]) for row in rows if row["role"] == "grid"]
    print(f"{target}  共 {len(rows)} 行（网格 {len(grid)} + 参考 {sum(1 for r in rows if r['role'] == 'ref')}）")
    print("幅度范围", min(grid), "…", max(grid), "Vpp")


if __name__ == "__main__":
    main()
