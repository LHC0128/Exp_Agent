"""生成阶段 1 / 阶段 2 运行清单（固定随机种子，顺序打乱以免漂移与参数相关）。

阶段 2 的 Z 列先写 NaN，由阶段 1 的线性模型填入（见 交接方案.md §阶段 2）。
用法：在开发目录 D:/Code/exp_agent 下运行，输出到本目录上一级的 runlists/。
"""
from __future__ import annotations

import csv
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from model_area import relative_area  # noqa: E402

OUT = HERE.parent / "runlists"
REF = dict(role="ref", z=-0.068, w=8.03, sf=0.575, te=38.3)
FIELDS = ["order", "tag", "role", "position", "z", "w", "sf", "te", "area_total"]


def with_refs(runs, prefix):
    """开头、每 4 个运行之后、结尾各插一次参考点。"""
    out, k = [], 0
    for i, run in enumerate(runs):
        if i % 4 == 0:
            out.append(dict(REF, position="R", tag=f"{prefix}_ref{k:02d}")); k += 1
        out.append(run)
    out.append(dict(REF, position="R", tag=f"{prefix}_ref{k:02d}"))
    return out


def write(rows, name):
    OUT.mkdir(exist_ok=True)
    with (OUT / name).open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        for i, row in enumerate(rows, 1):
            area = relative_area(row["w"], row["sf"], row["te"])[2]
            writer.writerow(dict(row, order=i, area_total=round(area, 4)))


def stage1():
    positions = {"P0": (8.03, 0.575, 38.3), "P1": (7.5, 0.575, 38.3), "P2": (8.6, 0.575, 38.3),
                 "P3": (8.03, 0.50, 38.3), "P4": (8.03, 0.65, 38.3), "P5": (8.03, 0.544, 46.9)}
    runs = []
    for pos, (w, sf, te) in positions.items():
        for z in (-0.072, -0.068, -0.064):
            runs.append(dict(role="scan", position=pos, z=z, w=w, sf=sf, te=te,
                             tag=f"cc1_{pos.lower()}_m{round(abs(z) * 1000):03d}"))
    random.Random(20261120).shuffle(runs)
    write(with_refs(runs, "cc1"), "stage1.csv")


def stage2():
    runs = []
    for i, w in enumerate((7.5, 7.75, 8.03, 8.3, 8.6)):
        for j, sf in enumerate((0.50, 0.54, 0.575, 0.61, 0.65)):
            runs.append(dict(role="grid", position=f"G{i}{j}", z=float("nan"), w=w, sf=sf, te=38.3,
                             tag=f"cc2_g{i}{j}"))
    random.Random(20261121).shuffle(runs)
    write(with_refs(runs, "cc2"), "stage2.csv")


if __name__ == "__main__":
    stage1()
    stage2()
    print("written to", OUT)
