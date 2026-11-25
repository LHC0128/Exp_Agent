"""锁相解调相位扫描分析：检查模量、斜率不变而相位整体旋转。

输入 _studies/20261121_locking_phase_scan/phase_scan_runs.jsonl；
输出 tables/phase_scan.csv、tables/phase_scan_fit.json、figures/phase_scan.png。

判据：arg(c0) 与最佳读相位随解调相位以斜率 1 平移，S 与 |c0| 与解调相位无关。
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np

from lab_workflows.common import data_root

DATA_TYPE = "rf_phase_response"
STUDY = "20261121_locking_phase_scan"
PROCESS_DIR = data_root() / DATA_TYPE / "_studies" / STUDY
RESULT_DIR = Path(__file__).resolve().parents[1]
TABLES = RESULT_DIR / "tables"
FIGURES = RESULT_DIR / "figures"


def load():
    path = PROCESS_DIR / "phase_scan_runs.jsonl"
    if not path.is_file():
        raise SystemExit(f"{path} 不存在：先跑 run_phase_scan.py")
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return sorted(rows, key=lambda row: row["order"])


def unwrap(values, period):
    """按给定周期（360 或 180）展开，使相邻差落在 (−period/2, period/2]。"""
    out = [values[0]]
    for value in values[1:]:
        previous = out[-1]
        delta = (value - previous + period / 2.0) % period - period / 2.0
        out.append(previous + delta)
    return np.array(out)


def fit_slope(x, y):
    """y = a + b x，返回 b、a 与其标准误。"""
    matrix = np.column_stack([np.ones_like(x), x])
    coef, *_ = np.linalg.lstsq(matrix, y, rcond=None)
    residual = y - matrix @ coef
    dof = max(len(x) - 2, 1)
    sigma2 = float(residual @ residual / dof)
    covariance = sigma2 * np.linalg.inv(matrix.T @ matrix)
    return {"slope": float(coef[1]), "intercept": float(coef[0]),
            "slope_err": float(np.sqrt(covariance[1, 1])), "intercept_err": float(np.sqrt(covariance[0, 0])),
            "residual_rms": float(np.sqrt(sigma2)), "dof": dof}


def main():
    rows = load()
    grid = [row for row in rows if row["role"] != "ref"]
    refs = [row for row in rows if row["role"] == "ref"]
    delta = np.array([row["demod_deg"] for row in grid])
    order = np.argsort(delta)
    delta = delta[order]
    grid = [grid[index] for index in order]

    c0_arg = unwrap([row["c0_arg_deg"] for row in grid], 360.0)
    best = unwrap([row["best_phase_deg"] for row in grid], 180.0)
    c1_arg = unwrap([row["c1_arg_deg"] for row in grid], 360.0)
    slope = np.array([row["S_mV_per_deg"] for row in grid])
    c0_abs = np.array([row["c0_abs_mV"] for row in grid])
    rpeak = np.array([row["r_peak_v"] for row in grid])
    residual = np.array([row["residual_mV"] for row in grid])

    record = {
        "c0_arg": fit_slope(delta, c0_arg),
        "best_phase": fit_slope(delta, best),
        "c1_arg": fit_slope(delta, c1_arg),
        "S_vs_demod": fit_slope(delta, slope),
        "c0_abs_vs_demod": fit_slope(delta, c0_abs),
        "S_mV_per_deg": {"mean": float(slope.mean()), "std": float(slope.std(ddof=1)),
                         "min": float(slope.min()), "max": float(slope.max()),
                         "relative_spread": float((slope.max() - slope.min()) / slope.mean())},
        "c0_abs_mV": {"mean": float(c0_abs.mean()), "std": float(c0_abs.std(ddof=1)),
                      "min": float(c0_abs.min()), "max": float(c0_abs.max())},
        "r_peak_v": {"mean": float(rpeak.mean()), "std": float(rpeak.std(ddof=1))},
        "residual_mV": {"mean": float(residual.mean()), "max": float(residual.max())},
        "refs": [{"tag": row["tag"], "S": row["S_mV_per_deg"], "c0_arg": row["c0_arg_deg"],
                  "best_phase": row["best_phase_deg"]} for row in refs],
        "ref_spread": float(max(row["S_mV_per_deg"] for row in refs) - min(row["S_mV_per_deg"] for row in refs))
        if len(refs) > 1 else 0.0,
    }

    TABLES.mkdir(parents=True, exist_ok=True)
    with (TABLES / "phase_scan.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["tag", "run_dir", "demod_deg", "S_mV_per_deg", "c0_mV", "c0_arg_deg",
                         "c1_mV", "c1_arg_deg", "cm1_mV", "best_phase_deg", "r_peak_v", "residual_mV"])
        for row in rows:
            writer.writerow([row["tag"], Path(row["run_dir"]).name, "%.1f" % row["demod_deg"],
                             "%.4f" % row["S_mV_per_deg"], "%.2f" % row["c0_abs_mV"], "%.2f" % row["c0_arg_deg"],
                             "%.2f" % row["c1_mV"], "%.2f" % row["c1_arg_deg"], "%.2f" % row["cm1_mV"],
                             "%.1f" % row["best_phase_deg"], "%.4f" % row["r_peak_v"],
                             "%.1f" % row["residual_mV"]])
    (TABLES / "phase_scan_fit.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from lab_workflows.plotting import format_axis, plot_style_context, save_figure, style_legend

    FIGURES.mkdir(parents=True, exist_ok=True)
    with plot_style_context("paper"):
        figure, axes = plt.subplots(1, 3, figsize=(7.2, 2.6))
        axes[0].axhspan(slope.mean() - slope.std(ddof=1), slope.mean() + slope.std(ddof=1),
                        color="0.85", zorder=0)
        axes[0].plot(delta, slope, "o", ms=3.2, color="C0")
        axes[0].axhline(slope.mean(), color="0.4", ls="--", lw=0.8)
        format_axis(axes[0], xlabel="lock-in demod phase (°)", ylabel="S (mV/deg)")

        axes[1].plot(delta, c0_arg, "o", ms=3, color="C1", label=r"arg($c_0$)")
        fit = record["c0_arg"]
        axes[1].plot(delta, fit["intercept"] + fit["slope"] * delta, "-", lw=0.9, color="0.3",
                     label=f"fit slope {fit['slope']:.3f}")
        format_axis(axes[1], xlabel="lock-in demod phase (°)", ylabel="phase (°)")
        style_legend(axes[1], loc="upper left", fontsize=5, frameon=False)

        axes[2].plot(delta, best, "s-", ms=3, lw=0.8, color="C2", label="best read phase (°)")
        axes[2].plot(delta, c0_abs, "^-", ms=3, lw=0.8, color="C3", label=r"$|c_0|$ (mV)")
        format_axis(axes[2], xlabel="lock-in demod phase (°)", ylabel="phase (°) / mV")
        style_legend(axes[2], loc="center right", fontsize=5, frameon=False)
        save_figure(figure, FIGURES / "phase_scan.png")

    print(json.dumps({key: record[key] for key in ("c0_arg", "c1_arg", "best_phase", "S_vs_demod",
                                                   "c0_abs_vs_demod", "S_mV_per_deg", "c0_abs_mV",
                                                   "ref_spread")},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
