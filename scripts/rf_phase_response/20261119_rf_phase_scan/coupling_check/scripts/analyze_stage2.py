"""阶段 2（w × s_f 网格，Z 跟随）分析：二次曲面、Hessian 与转角塌缩检验。

回答 Q2：配上各自最优 Z 之后，斜率的最大值是一个闭合峰，还是一条等转角脊。
输入 coupling_check/data/stage2_runs.jsonl；输出 data/stage2.csv、data/stage2_fit.json 与两张图。
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import numpy as np

import cc_common as cc

OUT = Path(__file__).resolve().parent.parent
CENTER_W, CENTER_SF = 8.03, 0.575
HALF_W, HALF_SF = 0.55, 0.075


def design(u, v):
    return np.column_stack([np.ones_like(u), u, v, u * u, u * v, v * v])


def fit_surface(rows):
    u = (np.array([row["w"] for row in rows]) - CENTER_W) / HALF_W
    v = (np.array([row["sf"] for row in rows]) - CENTER_SF) / HALF_SF
    target = np.array([row["r"] for row in rows])
    matrix = design(u, v)
    coef, *_ = np.linalg.lstsq(matrix, target, rcond=None)
    residual = target - matrix @ coef
    dof = max(len(target) - matrix.shape[1], 1)
    sigma = float(np.sqrt(residual @ residual / dof))
    hessian = np.array([[2 * coef[3], coef[4]], [coef[4], 2 * coef[5]]])
    eigenvalues, eigenvectors = np.linalg.eigh(hessian)
    gradient = np.array([coef[1], coef[2]])
    stationary = -np.linalg.solve(hessian, gradient) if abs(np.linalg.det(hessian)) > 1e-14 else np.array([np.nan, np.nan])
    stationary_w = CENTER_W + stationary[0] * HALF_W
    stationary_sf = CENTER_SF + stationary[1] * HALF_SF
    return {"coef": coef.tolist(), "sigma": sigma, "dof": dof,
            "hessian": hessian.tolist(), "eigenvalues": eigenvalues.tolist(),
            "eigenvectors": eigenvectors.tolist(), "gradient": gradient.tolist(),
            "stationary": {"w": float(stationary_w), "sf": float(stationary_sf),
                           "in_grid": bool(abs(stationary[0]) <= 1 and abs(stationary[1]) <= 1)
                           if np.all(np.isfinite(stationary)) else False},
            "residual": residual.tolist(), "u": u.tolist(), "v": v.tolist(), "r": target.tolist()}


def fit_area(rows):
    area = np.array([row["area_total"] for row in rows])
    target = np.array([row["r"] for row in rows])
    scale = float(np.mean(area))
    matrix = np.column_stack([np.ones_like(area), area - scale, (area - scale) ** 2])
    coef, *_ = np.linalg.lstsq(matrix, target, rcond=None)
    residual = target - matrix @ coef
    dof = max(len(target) - 3, 1)
    sigma = float(np.sqrt(residual @ residual / dof))
    vertex = scale - coef[1] / (2 * coef[2]) if abs(coef[2]) > 1e-12 else float("nan")
    return {"coef": coef.tolist(), "scale": scale, "sigma": sigma, "dof": dof, "vertex_area": float(vertex)}


def plot(surface, area_fit, rows, near):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from lab_workflows.plotting import format_axis, plot_style_context, save_figure, style_legend

    figures = OUT / "figures"
    figures.mkdir(parents=True, exist_ok=True)
    with plot_style_context("paper"):
        width = np.linspace(-1.15, 1.15, 200)
        grid_u, grid_v = np.meshgrid(width, width)
        model = design(grid_u.ravel(), grid_v.ravel()) @ np.array(surface["coef"])
        figure, axis = plt.subplots(figsize=(4.6, 3.4))
        filled = axis.contourf(CENTER_W + grid_u * HALF_W, CENTER_SF + grid_v * HALF_SF,
                               model.reshape(grid_u.shape), levels=14, cmap="viridis")
        figure.colorbar(filled, ax=axis, label="r = S / S$_{ref}$")
        axis.contour(CENTER_W + grid_u * HALF_W, CENTER_SF + grid_v * HALF_SF,
                     model.reshape(grid_u.shape), levels=[near], colors="w", linestyles="--", linewidths=0.8)
        axis.plot([row["w"] for row in rows], [row["sf"] for row in rows], "o", ms=3.0,
                  mfc="none", mec="w")
        if np.all(np.isfinite([surface["stationary"]["w"], surface["stationary"]["sf"]])):
            axis.plot(surface["stationary"]["w"], surface["stationary"]["sf"], "*", ms=9, color="C3")
        format_axis(axis, xlabel="pulse width w (µs)", ylabel="s$_f$")
        save_figure(figure, figures / "grid_contour.png")

        figure, axis = plt.subplots(figsize=(4.6, 2.8))
        sf_values = sorted({row["sf"] for row in rows})
        for index, sf in enumerate(sf_values):
            items = sorted((row for row in rows if row["sf"] == sf), key=lambda row: row["area_total"])
            axis.plot([row["area_total"] for row in items], [row["r"] for row in items], "o", ms=3.0,
                      color=f"C{index}", label=f"s$_f$={sf:g}")
        areas = np.linspace(min(row["area_total"] for row in rows), max(row["area_total"] for row in rows), 100)
        shift = areas - area_fit["scale"]
        axis.plot(areas, area_fit["coef"][0] + area_fit["coef"][1] * shift + area_fit["coef"][2] * shift ** 2,
                  "-", lw=0.9, color="0.3")
        format_axis(axis, xlabel="model total area (relative to clean π)", ylabel="r = S / S$_{ref}$")
        style_legend(axis, loc="lower right", fontsize=5, frameon=False)
        save_figure(figure, figures / "r_vs_area.png")


def main():
    rows = cc.load("stage2")
    if not rows:
        raise SystemExit("stage2_runs.jsonl 为空：阶段 2 还没有数据")
    drift = cc.drift_normalise(rows)
    scans = [row for row in rows if row["role"] != "ref"]
    surface = fit_surface(scans)
    area_fit = fit_area(scans)
    r_max = max(row["r"] for row in scans)
    near = r_max - 2 * surface["sigma"]

    OUT.joinpath("data").mkdir(parents=True, exist_ok=True)
    with (OUT / "data" / "stage2.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["tag", "run_dir", "Z", "w", "sf", "te", "S_mV_per_deg", "c0_mV",
                         "S_ref", "r", "area_total"])
        for row in rows:
            writer.writerow([row["tag"], Path(row["run_dir"]).name, "%.4f" % row["z"], "%.3f" % row["w"],
                             "%.3f" % row["sf"], "%.1f" % row["te"], "%.4f" % row["S_mV_per_deg"],
                             "%.2f" % row["c0_mV"], "%.4f" % row["S_ref"], "%.4f" % row["r"],
                             "%.4f" % row["area_total"]])

    record = {"surface": surface, "area_fit": area_fit, "near_optimal_threshold": near,
              "drift": {"max_abs_change": drift["max_abs_change"], "gaps": drift["gaps"]}}
    (OUT / "data" / "stage2_fit.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    plot(surface, area_fit, scans, near)

    print(json.dumps({"sigma": surface["sigma"], "stationary": surface["stationary"],
                      "eigenvalues": surface["eigenvalues"], "eigenvectors": surface["eigenvectors"],
                      "area_sigma": area_fit["sigma"], "area_vertex": area_fit["vertex_area"],
                      "near_optimal": near, "max_drift": drift["max_abs_change"]},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    sys.exit(main())
