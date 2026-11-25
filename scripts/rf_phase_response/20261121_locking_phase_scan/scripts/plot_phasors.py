"""50 mVpp 射频场下相量随锁相解调相位的变化：Z(φ) 轨迹、整体旋转与分量箭头。

数据来自 20261121_locking_phase_scan 的 26 次运行（Y_RF_VPP = 0.05 V），只读 results/phase_response_summary.json。
输出 figures/phasor_rotation.png 与 tables/phasor_rotation.csv。
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
# 取平均 |Z| 最大的射频相位作为观察点（避开两处谷）。
ANCHOR_CANDIDATES = (90.0, 80.0, 100.0, 70.0, 110.0)


def load_curves():
    rows = [json.loads(line) for line in (PROCESS_DIR / "phase_scan_runs.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()]
    rows.sort(key=lambda row: row["demod_deg"])
    curves = []
    for row in rows:
        summary = json.loads((Path(row["run_dir"]) / "results" / "phase_response_summary.json").read_text(encoding="utf-8"))
        phases = np.array([point["phase_deg"] for point in summary["points"]])
        z = np.array([complex(point["x_mean_v"], point["y_mean_v"]) for point in summary["points"]]) * 1e3
        comps = summary["components"]
        components = {name: complex(comps[name]["re_v"], comps[name]["im_v"]) * 1e3
                      for name in ("c0", "c_plus1", "c_minus1")}
        curves.append({"tag": row["tag"], "demod": float(row["demod_deg"]), "role": row["role"],
                       "phases": phases, "z": z, "components": components})
    return curves


def main():
    curves = load_curves()
    grid = [curve for curve in curves if curve["role"] != "ref"]
    phases = grid[0]["phases"]
    anchor = ANCHOR_CANDIDATES[0]
    if anchor not in phases:
        mean_abs = np.mean([np.abs(curve["z"]) for curve in grid], axis=0)
        anchor = float(phases[int(np.argmax(mean_abs))])
    index = int(np.where(phases == anchor)[0][0])

    record = []
    for curve in grid:
        z_anchor = curve["z"][index]
        record.append({"tag": curve["tag"], "demod_deg": curve["demod"],
                       "z_anchor_mV": abs(z_anchor), "z_anchor_arg_deg": np.degrees(np.angle(z_anchor)),
                       "c0_mV": abs(curve["components"]["c0"]),
                       "c0_arg_deg": np.degrees(np.angle(curve["components"]["c0"]))})
    demod = np.array([item["demod_deg"] for item in record])
    z_abs = np.array([item["z_anchor_mV"] for item in record])
    z_arg = np.unwrap(np.radians([item["z_anchor_arg_deg"] for item in record]))
    c0_arg = np.unwrap(np.radians([item["c0_arg_deg"] for item in record]))
    matrix = np.column_stack([np.ones_like(demod), demod])
    z_slope = np.linalg.lstsq(matrix, z_arg, rcond=None)[0][1]
    c0_slope = np.linalg.lstsq(matrix, c0_arg, rcond=None)[0][1]

    TABLES.mkdir(parents=True, exist_ok=True)
    with (TABLES / "phasor_rotation.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["tag", "demod_deg", "z_abs_mV", "z_arg_deg", "c0_mV", "c0_arg_deg"])
        for item in record:
            writer.writerow([item["tag"], "%.1f" % item["demod_deg"], "%.3f" % item["z_anchor_mV"],
                             "%.2f" % item["z_anchor_arg_deg"], "%.3f" % item["c0_mV"],
                             "%.2f" % item["c0_arg_deg"]])

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from lab_workflows.plotting import format_axis, plot_style_context, save_figure, style_legend

    FIGURES.mkdir(parents=True, exist_ok=True)
    with plot_style_context("paper"):
        figure, axes = plt.subplots(1, 3, figsize=(7.6, 2.7), layout="constrained")

        colors = plt.cm.viridis(np.linspace(0.05, 0.95, len(grid)))
        for curve, color in zip(sorted(grid, key=lambda item: item["demod"]), colors):
            axes[0].plot(curve["z"].real, curve["z"].imag, "-", lw=0.6, color=color, alpha=0.9)
        axes[0].set_aspect("equal")
        format_axis(axes[0], xlabel="Re $Z$ (mV)", ylabel="Im $Z$ (mV)")
        scalar = plt.cm.ScalarMappable(cmap="viridis", norm=plt.Normalize(0, 360))
        figure.colorbar(scalar, ax=axes[0], label="demod phase (°)", pad=0.02)

        axes[1].plot(demod, np.degrees(z_arg), "o", ms=3, color="C0", label=f"arg $Z$({anchor:.0f}°)")
        axes[1].plot(demod, np.degrees(z_arg[0]) - demod, ":", lw=0.9, color="0.4", label="slope −1 reference")
        format_axis(axes[1], xlabel="demod phase (°)", ylabel=f"arg $Z$({anchor:.0f}°) (°)")
        style_legend(axes[1], loc="upper right", fontsize=5, frameon=False)
        twin = axes[1].twinx()
        twin.plot(demod, z_abs, "s", ms=2.6, color="C3")
        twin.set_ylabel(f"$|Z$({anchor:.0f}°)| (mV)", fontsize=6)
        twin.tick_params(labelsize=5)

        # (c) 三个分量的相位随解调相位刚性旋转：三条斜率 −1 的直线
        order = sorted(grid, key=lambda item: item["demod"])
        for name, color, style, label in (("c0", "C0", "-", r"$c_0$"),
                                          ("c_plus1", "C1", "--", r"$c_{+1}$"),
                                          ("c_minus1", "C2", ":", r"$c_{-1}$")):
            values = np.unwrap(np.radians([np.degrees(np.angle(curve["components"][name])) for curve in order]))
            demods = np.array([curve["demod"] for curve in order])
            axes[2].plot(demods, np.degrees(values), style, lw=1.0, color=color, label=label)
        format_axis(axes[2], xlabel="demod phase (°)", ylabel="component phase (°)")
        style_legend(axes[2], loc="lower left", fontsize=5, frameon=False)
        save_figure(figure, FIGURES / "phasor_rotation.png")

    print(json.dumps({"anchor_phase_deg": anchor, "z_slope_deg_per_deg": float(np.degrees(z_slope)),
                      "c0_slope_deg_per_deg": float(np.degrees(c0_slope)),
                      "z_abs_mean_mV": float(z_abs.mean()), "z_abs_spread_mV": float(z_abs.max() - z_abs.min()),
                      "points": len(record)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
