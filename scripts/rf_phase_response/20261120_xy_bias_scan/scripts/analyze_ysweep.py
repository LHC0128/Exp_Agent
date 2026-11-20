"""第 2 轮：Y 方向扫描的两条切片分析——找 S 的极大点，并与 c0、R 峰对照。

输入 `_studies/20261120_xy_bias_scan/ysweep_runs.jsonl`；输出 tables/ysweep.csv、
tables/ysweep_fit.json 与 figures/ysweep.png。回答：把 Y 从 −0.04 推到 +0.20 V，
S 是否有一个真正可分辨的极大，它和 c0 零点是不是同一个位置。
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from lab_workflows.common import data_root

DATA_TYPE = "rf_phase_response"
STUDY = "20261120_xy_bias_scan"
PROCESS_DIR = data_root() / DATA_TYPE / "_studies" / STUDY
RESULT = Path(__file__).resolve().parents[1]
TABLES = RESULT / "tables"
FIGURES = RESULT / "figures"


def load():
    path = PROCESS_DIR / "ysweep_runs.jsonl"
    if not path.is_file():
        raise SystemExit("ysweep_runs.jsonl 不存在：第 2 轮还没跑")
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows:
        raise SystemExit("ysweep_runs.jsonl 为空")
    for row in rows:
        row["time"] = _time(row["run_dir"])
    return sorted(rows, key=lambda row: row["order"]), path


def _time(run_dir):
    from datetime import datetime

    name = Path(run_dir).name
    month, day, clock = int(name[0:2]), int(name[2:4]), name[5:11]
    now = datetime.now()
    year = now.year if month <= now.month else now.year - 1
    return datetime(year, month, day, int(clock[0:2]), int(clock[2:4]), int(clock[4:6]))


def drift_normalise(rows):
    """参考点在时间上线性插值成 S_ref(t)，r=S/S_ref；参考点自身 r≡1。"""
    refs = sorted((r for r in rows if r["role"] == "ref"), key=lambda r: r["time"])
    if len(refs) < 2:
        for row in rows:
            row["S_ref"], row["r"] = row["S_mV_per_deg"], 1.0
        return 0.0
    stamps = [r["time"] for r in refs]
    values = [r["S_mV_per_deg"] for r in refs]
    for row in rows:
        moment = row["time"]
        if row["role"] == "ref":
            row["S_ref"], row["r"] = row["S_mV_per_deg"], 1.0
            continue
        if moment <= stamps[0]:
            low = 0
        elif moment >= stamps[-1]:
            low = len(stamps) - 2
        else:
            low = max(i for i in range(len(stamps) - 1) if stamps[i] <= moment)
        span = (stamps[low + 1] - stamps[low]).total_seconds()
        frac = (moment - stamps[low]).total_seconds() / span if span > 0 else 0.0
        reference = values[low] + (values[low + 1] - values[low]) * frac
        row["S_ref"], row["r"] = reference, row["S_mV_per_deg"] / reference
    return max(abs(values[i + 1] / values[i] - 1) for i in range(len(values) - 1))


def parabola(xs, ys):
    coef = np.polyfit(xs, ys, 2)
    vertex = -coef[1] / (2 * coef[0]) if abs(coef[0]) > 1e-12 else float("nan")
    residual = np.asarray(ys) - np.polyval(coef, xs)
    dof = max(len(xs) - 3, 1)
    return {"vertex_y": float(vertex), "peak": float(np.polyval(coef, vertex)),
            "curvature": float(coef[0]), "rms": float(np.sqrt(np.sum(residual ** 2) / dof)),
            "in_range": bool(min(xs) <= vertex <= max(xs))}


def analyse(grid):
    xs_rows = sorted({r["x"] for r in grid})
    out = {"rows": {}}
    for x in xs_rows:
        items = sorted((r for r in grid if r["x"] == x), key=lambda r: r["y"])
        ys = [r["y"] for r in items]
        out["rows"][f"{x:.3f}"] = {
            "n": len(items),
            "parabola_S": parabola(ys, [r["S_mV_per_deg"] for r in items]),
            "parabola_r": parabola(ys, [r["r"] for r in items]),
        }
    s = np.array([r["S_mV_per_deg"] for r in grid])
    r_values = np.array([r["r"] for r in grid])
    c0 = np.array([r["c0_abs_mV"] for r in grid])
    r_peak = np.array([r["r_peak_v"] for r in grid])
    residual = np.array([r["residual_mV"] for r in grid])
    best = grid[int(np.argmax(s))]
    best_r = grid[int(np.argmax(r_values))]
    null = grid[int(np.argmin(c0))]
    out.update({
        "S_range": [float(s.min()), float(s.max())],
        "r_range": [float(r_values.min()), float(r_values.max())],
        "S_max_point": {"x": best["x"], "y": best["y"], "S": float(best["S_mV_per_deg"]), "r": float(best["r"]),
                        "c0": float(best["c0_abs_mV"]), "R_peak": float(best["r_peak_v"])},
        "r_max_point": {"x": best_r["x"], "y": best_r["y"], "S": float(best_r["S_mV_per_deg"]), "r": float(best_r["r"])},
        "c0_min_point": {"x": null["x"], "y": null["y"], "c0": float(null["c0_abs_mV"]),
                         "S": float(null["S_mV_per_deg"]), "r": float(null["r"]), "R_peak": float(null["r_peak_v"])},
        "corr": {"S_vs_c0": float(np.corrcoef(s, c0)[0, 1]),
                 "S_vs_Rpeak": float(np.corrcoef(s, r_peak)[0, 1]),
                 "S_vs_residual": float(np.corrcoef(s, residual)[0, 1]),
                 "c0_vs_Rpeak": float(np.corrcoef(c0, r_peak)[0, 1])},
    })
    return out


def plot(grid, result):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from lab_workflows.plotting import format_axis, plot_style_context, save_figure, style_legend

    FIGURES.mkdir(parents=True, exist_ok=True)
    xs_rows = sorted({r["x"] for r in grid})
    with plot_style_context("paper"):
        figure, axes = plt.subplots(2, 2, figsize=(6.4, 4.8))
        samples = np.linspace(min(r["y"] for r in grid) - 0.02, max(r["y"] for r in grid) + 0.02, 120)
        for index, x in enumerate(xs_rows):
            items = sorted((r for r in grid if r["x"] == x), key=lambda r: r["y"])
            ys = [r["y"] for r in items]
            axis = axes[0, 0]
            axis.plot(ys, [r["S_mV_per_deg"] for r in items], "o", ms=3.2, color=f"C{index}")
            coef = np.polyfit(ys, [r["S_mV_per_deg"] for r in items], 2)
            axis.plot(samples, np.polyval(coef, samples), "-", lw=0.9, color=f"C{index}", label=f"X={x:g} V")
            axis = axes[0, 1]
            axis.plot(ys, [r["c0_abs_mV"] for r in items], "o-", ms=3.2, lw=0.8, color=f"C{index}",
                      label=f"X={x:g} V")
            axis = axes[1, 0]
            axis.plot(ys, [r["r_peak_v"] for r in items], "o-", ms=3.2, lw=0.8, color=f"C{index}",
                      label=f"X={x:g} V")
            axis = axes[1, 1]
            axis.plot(ys, [r["residual_mV"] for r in items], "o-", ms=3.2, lw=0.8, color=f"C{index}",
                      label=f"X={x:g} V")
        format_axis(axes[0, 0], xlabel="", ylabel="S (mV/deg)")
        style_legend(axes[0, 0], loc="lower center", fontsize=5, frameon=False)
        format_axis(axes[0, 1], xlabel="", ylabel="|c$_0$| (mV)")
        format_axis(axes[1, 0], xlabel="Y bias (V)", ylabel="R peak (V)")
        format_axis(axes[1, 1], xlabel="Y bias (V)", ylabel="fit residual (mV)")
        for axis in axes.ravel():
            axis.axvline(0.0, color="0.6", ls=":", lw=0.7)
        save_figure(figure, FIGURES / "ysweep.png")


def main():
    rows, path = load()
    max_gap = drift_normalise(rows)
    grid = [r for r in rows if r["role"] != "ref"]
    result = analyse(grid)
    result["drift_max_gap"] = max_gap
    result["points"] = len(rows)

    TABLES.mkdir(parents=True, exist_ok=True)
    lines = ["tag,x,y,role,S_mV_per_deg,S_ref,r,c0_mV,c0_arg_deg,R_peak_V,residual_mV,fit_quality"]
    for row in rows:
        lines.append(",".join([
            row["tag"], f"{row['x']:.4f}", f"{row['y']:.4f}", row["role"],
            f"{row['S_mV_per_deg']:.5f}", f"{row['S_ref']:.5f}", f"{row['r']:.5f}",
            f"{row['c0_abs_mV']:.3f}", f"{row['c0_arg_deg']:.2f}", f"{row['r_peak_v']:.4f}",
            f"{row['residual_mV']:.2f}", row["fit_quality"]]))
    (TABLES / "ysweep.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (TABLES / "ysweep_fit.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    plot(grid, result)

    print(json.dumps({k: v for k, v in result.items() if k != "rows"}, ensure_ascii=False, indent=2))
    for key, value in result["rows"].items():
        s, r = value["parabola_S"], value["parabola_r"]
        print(f"X={key}: S 顶点 y={s['vertex_y']:+.3f} ({'in' if s['in_range'] else 'out'}) 峰 {s['peak']:.4f} "
              f"rms {s['rms']:.4f} | r 顶点 {r['vertex_y']:+.3f} rms {r['rms']:.4f}")


if __name__ == "__main__":
    main()
