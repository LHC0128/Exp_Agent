"""锁相解调相位扫描的清单生成：0–345° 步 15°，两端各插一次 0° 参考点。

用法：python scripts/rf_phase_response/20261121_locking_phase_scan/scripts/make_runlist.py
输出 runlists/phase_scan.csv，列：order, tag, role, demod_deg, direction。
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "runlists"
STEP = 15
DIRECTION = "Y"


def tag_for(deg):
    return f"demod_{int(round(deg)):04d}"


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--step", type=int, default=STEP)
    args = parser.parse_args(argv)
    values = list(range(0, 360, args.step))

    rows = [{"order": 1, "tag": "demod_ref00", "role": "ref", "demod_deg": "0", "direction": DIRECTION}]
    order = 2
    for deg in values:
        rows.append({"order": order, "tag": tag_for(deg), "role": "grid", "demod_deg": f"{deg}",
                     "direction": DIRECTION})
        order += 1
    rows.append({"order": order, "tag": "demod_ref01", "role": "ref", "demod_deg": "0", "direction": DIRECTION})

    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / "phase_scan.csv"
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["order", "tag", "role", "demod_deg", "direction"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"{target}  共 {len(rows)} 行（相位 {len(values)} + 参考 2），步进 {args.step}°")


if __name__ == "__main__":
    main()
