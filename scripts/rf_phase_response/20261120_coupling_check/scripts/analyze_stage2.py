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
import model_area

OUT = cc.RESULT_DIR
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


def bootstrap(surface, draws=2000, seed=20261120):
    """参数自助法：按残差 σ 重采噪声，给本征值、驻点与弱方向夹角的置信区间。"""
    coef = np.array(surface["coef"])
    u = np.array(surface["u"])
    v = np.array(surface["v"])
    target = np.array(surface["r"])
    matrix = design(u, v)
    fitted = matrix @ coef
    sigma = surface["sigma"]
    direction = area_direction()
    rng = np.random.default_rng(seed)
    samples = []
    angles = []
    for _ in range(draws):
        draw = fitted + rng.normal(0.0, sigma, size=target.size)
        current, *_ = np.linalg.lstsq(matrix, draw, rcond=None)
        hessian = np.array([[2 * current[3], current[4]], [current[4], 2 * current[5]]])
        values, vectors = np.linalg.eigh(hessian)
        try:
            point = -np.linalg.solve(hessian, np.array([current[1], current[2]]))
        except np.linalg.LinAlgError:
            point = np.array([np.nan, np.nan])
        samples.append([CENTER_W + point[0] * HALF_W, CENTER_SF + point[1] * HALF_SF, values[0], values[1]])
        weak = vectors[:, int(np.argmax(values))]
        angles.append(float(np.degrees(np.arccos(min(1.0, abs(float(weak @ direction)))))))
    samples = np.array(samples)
    names = ("w", "sf", "eig_strong", "eig_weak")
    summary = {}
    for index, name in enumerate(names):
        low, mid_low, median, mid_high, high = np.percentile(samples[:, index], [2.5, 16, 50, 84, 97.5])
        summary[name] = {"median": float(median), "lo68": float(mid_low), "hi68": float(mid_high),
                         "lo95": float(low), "hi95": float(high)}
    low, mid_low, median, mid_high, high = np.percentile(angles, [2.5, 16, 50, 84, 97.5])
    summary["weak_vs_area_deg"] = {"median": float(median), "lo68": float(mid_low), "hi68": float(mid_high),
                                   "lo95": float(low), "hi95": float(high)}
    summary["p_weak_negative"] = float(np.mean(samples[:, 3] < 0))
    summary["p_both_negative"] = float(np.mean((samples[:, 2] < 0) & (samples[:, 3] < 0)))
    return summary


def area_gradient(tau_e=38.3, step=1e-3):
    """总转角对归一化 (u, v) 的数值梯度：直接对 model_area 差商，不用解析幂律。

    实测局部幂指数是 A ∝ w^1.20·s_f^0.79（见 tables/stage2_fit.json 的 area_power），
    与“w·s_f^-0.5”的解析假设方向差约 19°，因此这里只信差商。
    """
    def area(u, v):
        return model_area.relative_area(CENTER_W + u * HALF_W, CENTER_SF + v * HALF_SF, tau_e)[2]

    gradient = np.array([(area(step, 0.0) - area(-step, 0.0)) / (2 * step),
                         (area(0.0, step) - area(0.0, -step)) / (2 * step)])
    return gradient / np.linalg.norm(gradient)


def area_direction(tau_e=38.3):
    """等转角方向（归一化 uv 坐标）：垂直于总转角梯度。"""
    gradient = area_gradient(tau_e)
    perpendicular = np.array([gradient[1], -gradient[0]])
    return perpendicular / np.linalg.norm(perpendicular)


def area_power(tau_e=38.3):
    """局部幂指数：A ∝ w^p_w · s_f^p_s，用中心两侧 ±6% 的对数斜率。"""
    reference = model_area.relative_area(CENTER_W, CENTER_SF, tau_e)[2]
    widths = np.linspace(0.94, 1.06, 9)
    fractions = np.linspace(0.94, 1.06, 9)
    width_area = np.array([model_area.relative_area(CENTER_W * f, CENTER_SF, tau_e)[2] for f in widths])
    tail_area = np.array([model_area.relative_area(CENTER_W, CENTER_SF * f, tau_e)[2] for f in fractions])
    return {"p_w": float(np.polyfit(np.log(widths), np.log(width_area / reference), 1)[0]),
            "p_sf": float(np.polyfit(np.log(fractions), np.log(tail_area / reference), 1)[0])}


def collapse_test(scans, surface, area_fit):
    """一维（总转角）与二维曲面的残差对比：F 检验 + 本征方向与等转角线的夹角。"""
    from scipy import stats

    target = np.array([row["r"] for row in scans])
    residual2 = np.array(surface["residual"])
    rss2 = float(np.sum(residual2 ** 2))
    dof2 = max(target.size - 6, 1)
    areas = np.array([row["area_total"] for row in scans])
    shift = areas - area_fit["scale"]
    model1 = area_fit["coef"][0] + area_fit["coef"][1] * shift + area_fit["coef"][2] * shift ** 2
    rss1 = float(np.sum((target - model1) ** 2))
    dof1 = max(target.size - 3, 1)
    statistic = ((rss1 - rss2) / (dof1 - dof2)) / (rss2 / dof2) if rss2 > 0 else float("inf")
    eigenvalues = np.array(surface["eigenvalues"])
    eigenvectors = np.array(surface["eigenvectors"])
    weak = eigenvectors[:, int(np.argmax(eigenvalues))]
    strong = eigenvectors[:, int(np.argmin(eigenvalues))]
    direction = area_direction()

    def angle(vector):
        return float(np.degrees(np.arccos(min(1.0, abs(float(vector @ direction))))))

    return {"sigma_2d": float(np.sqrt(rss2 / dof2)), "sigma_area": float(np.sqrt(rss1 / dof1)),
            "F": float(statistic), "p_value": float(stats.f.sf(statistic, dof1 - dof2, dof2)),
            "angle_weak_vs_area_deg": angle(weak), "angle_strong_vs_area_deg": angle(strong),
            "area_direction": direction.tolist(), "area_vertex": area_fit["vertex_area"]}


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
                  mfc="none", mec="w", label="measured grid")
        # 等转角线：用与清单同一套 model_area 计算，证明 r 的等高线沿着等转角方向。
        coarse = np.linspace(-1.05, 1.05, 33)
        uu, vv = np.meshgrid(coarse, coarse)
        area_grid = np.array([[model_area.relative_area(CENTER_W + u * HALF_W, CENTER_SF + v * HALF_SF, 38.3)[2]
                               for u in coarse] for v in coarse])
        iso = axis.contour(CENTER_W + uu * HALF_W, CENTER_SF + vv * HALF_SF,
                           area_grid, levels=[0.90, 0.95, 1.00, 1.05, 1.10, 1.15],
                           colors="0.25", linestyles=":", linewidths=0.7)
        axis.clabel(iso, fmt="%.2f", fontsize=4.2)
        a_w, a_sf = 8.03, 0.575
        c_w, c_sf = 8.03, 0.544
        axis.plot(a_w, a_sf, "s", ms=4.5, mfc="none", mec="w", label="A current optimum")
        axis.plot(c_w, c_sf, "^", ms=5.0, mfc="none", mec="w", label="C clean π")
        axis.annotate("A", (a_w, a_sf), textcoords="offset points", xytext=(5, -8), color="w", fontsize=5.5)
        axis.annotate("C", (c_w, c_sf), textcoords="offset points", xytext=(6, 3), color="w", fontsize=5.5)
        if np.all(np.isfinite([surface["stationary"]["w"], surface["stationary"]["sf"]])):
            axis.plot(surface["stationary"]["w"], surface["stationary"]["sf"], "*", ms=9, color="C3",
                      label="quadratic stationary point")
        axis.plot([], [], "--", color="w", lw=0.8, label="r = r$_{max}$ - 2σ")
        format_axis(axis, xlabel="pulse width w (µs)", ylabel="s$_f$")
        style_legend(axis, loc="lower left", fontsize=4.5, frameon=False)
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

    TABLES = OUT / "tables"
    TABLES.mkdir(parents=True, exist_ok=True)
    with (TABLES / "stage2.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["tag", "run_dir", "Z", "w", "sf", "te", "S_mV_per_deg", "c0_mV",
                         "S_ref", "r", "area_total"])
        for row in rows:
            writer.writerow([row["tag"], Path(row["run_dir"]).name, "%.4f" % row["z"], "%.3f" % row["w"],
                             "%.3f" % row["sf"], "%.1f" % row["te"], "%.4f" % row["S_mV_per_deg"],
                             "%.2f" % row["c0_mV"], "%.4f" % row["S_ref"], "%.4f" % row["r"],
                             "%.4f" % row["area_total"]])

    record = {"surface": surface, "area_fit": area_fit, "near_optimal_threshold": near,
              "bootstrap": bootstrap(surface),
              "collapse": collapse_test(scans, surface, area_fit),
              "area_geometry": {"gradient_uv": area_gradient().tolist(),
                                "iso_direction_uv": area_direction().tolist(),
                                "power": area_power()},
              "drift": {"max_abs_change": drift["max_abs_change"], "gaps": drift["gaps"]}}
    (TABLES / "stage2_fit.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    plot(surface, area_fit, scans, near)

    print(json.dumps({"sigma": surface["sigma"], "stationary": surface["stationary"],
                      "eigenvalues": surface["eigenvalues"], "eigenvectors": surface["eigenvectors"],
                      "bootstrap": record["bootstrap"], "collapse": record["collapse"],
                      "near_optimal": near, "max_drift": drift["max_abs_change"]},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    sys.exit(main())
