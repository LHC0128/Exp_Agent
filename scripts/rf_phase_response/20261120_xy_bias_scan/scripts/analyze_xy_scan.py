"""XY 偏置二维扫描分析：c0 零点（复平面线性拟合 + |c0|² 二次曲面）与 S 的平坦度检验。

跑在最优 Z 脉冲（8.6 / 0.5 / 38.3 / −0.0655 mA）下，回答两个问题：
1. c0 在 (X, Y) 平面的哪儿归零、误差多大；
2. 扫这段 XY 偏置时斜率 S 变了多少（用参考点做漂移归一后检验）。
输入 `_studies/20261120_xy_bias_scan/xy_scan_runs.jsonl`；输出 tables/ 与 figures/。
"""
from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path

import numpy as np
from lab_workflows.common import data_root

OUT = Path(__file__).resolve().parents[1]
TABLES = OUT / "tables"
FIGURES = OUT / "figures"
PROCESS_DIR = data_root() / "rf_phase_response" / "_studies" / "20261120_xy_bias_scan"
REF_XY = (-0.0409, -0.0069)


def load():
    path = PROCESS_DIR / "xy_scan_runs.jsonl"
    if not path.is_file():
        raise SystemExit(f"缺少逐点记录：{path}")
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows:
        raise SystemExit("xy_scan_runs.jsonl 为空：扫描还没有数据")
    for row in rows:
        row["time"] = datetime.fromisoformat(row["started_at"])
    return sorted(rows, key=lambda row: row["order"])


def drift_normalise(rows):
    """参考点之间按时间线性插值；参考点自身 r≡1。"""
    refs = sorted((row for row in rows if row["role"] == "ref"), key=lambda row: row["time"])
    base = refs[0]["time"]
    for row in rows:
        row["minutes"] = (row["time"] - base).total_seconds() / 60.0
        if row["role"] == "ref":
            row["S_ref"], row["r"] = row["S_mV_per_deg"], 1.0
            continue
        stamps = [item["time"] for item in refs]
        moment = row["time"]
        if len(stamps) == 1:
            value = refs[0]["S_mV_per_deg"]
        else:
            if moment <= stamps[0]:
                low = 0
            elif moment >= stamps[-1]:
                low = len(stamps) - 2
            else:
                low = max(index for index in range(len(stamps) - 1) if stamps[index] <= moment)
            left, right = refs[low], refs[low + 1]
            span = (right["time"] - left["time"]).total_seconds()
            fraction = (moment - left["time"]).total_seconds() / span if span else 0.0
            value = left["S_mV_per_deg"] + (right["S_mV_per_deg"] - left["S_mV_per_deg"]) * fraction
        row["S_ref"] = value
        row["r"] = row["S_mV_per_deg"] / value
    gaps = [(right["S_mV_per_deg"] / left["S_mV_per_deg"] - 1.0, left["tag"], right["tag"])
            for left, right in zip(refs, refs[1:])]
    return refs, gaps


def linear_null(grid):
    """把 c0 当复向量对 (X, Y) 做线性拟合，再解 c0=0。

    实部与虚部共用同一个设计矩阵，协方差可直接用于蒙特卡洛。
    """
    x = np.array([row["x"] for row in grid])
    y = np.array([row["y"] for row in grid])
    re = np.array([row["c0_re_mV"] for row in grid])
    im = np.array([row["c0_im_mV"] for row in grid])
    design = np.column_stack([np.ones_like(x), x, y])
    coef_re, *_ = np.linalg.lstsq(design, re, rcond=None)
    coef_im, *_ = np.linalg.lstsq(design, im, rcond=None)
    residual = np.concatenate([re - design @ coef_re, im - design @ coef_im])
    dof = max(2 * len(grid) - 6, 1)
    sigma = float(np.sqrt(residual @ residual / dof))
    covariance = sigma ** 2 * np.linalg.inv(design.T @ design)
    jacobian = np.array([[coef_re[1], coef_re[2]], [coef_im[1], coef_im[2]]])
    constant = np.array([coef_re[0], coef_im[0]])
    point = -np.linalg.solve(jacobian, constant)

    # 蒙特卡洛：把系数协方差搬成零点云，给 68%/95% 椭圆
    rng = np.random.default_rng(20261120)
    draws = []
    for _ in range(4000):
        cre = rng.multivariate_normal(coef_re, covariance)
        cim = rng.multivariate_normal(coef_im, covariance)
        try:
            draws.append(-np.linalg.solve(np.array([[cre[1], cre[2]], [cim[1], cim[2]]]),
                                          np.array([cre[0], cim[0]])))
        except np.linalg.LinAlgError:
            continue
    draws = np.array(draws)
    singular = np.linalg.svd(jacobian, compute_uv=False)
    return {"point": point.tolist(), "sigma_c0_mV": sigma, "dof": dof,
            "condition_number": float(singular[0] / singular[1]) if singular[1] else float("inf"),
            "sensitivity_mV_per_V": {"x": float(np.linalg.norm(jacobian[:, 0])),
                                     "y": float(np.linalg.norm(jacobian[:, 1]))},
            "slope_mV_per_V": {"x": [float(coef_re[1]), float(coef_im[1])],
                               "y": [float(coef_re[2]), float(coef_im[2])]},
            "monte_carlo": {"x68": np.percentile(draws[:, 0], [16, 84]).tolist(),
                            "y68": np.percentile(draws[:, 1], [16, 84]).tolist(),
                            "x95": np.percentile(draws[:, 0], [2.5, 97.5]).tolist(),
                            "y95": np.percentile(draws[:, 1], [2.5, 97.5]).tolist()},
            "samples": draws.tolist()}


def quadratic_null(grid):
    """|c0|² 对 (X, Y) 的二次拟合：给极小点与 Hessian，判断是否落在网格内。"""
    x = np.array([row["x"] for row in grid])
    y = np.array([row["y"] for row in grid])
    target = np.array([row["c0_abs_mV"] for row in grid]) ** 2
    design = np.column_stack([np.ones_like(x), x, y, x * x, x * y, y * y])
    coef, *_ = np.linalg.lstsq(design, target, rcond=None)
    residual = target - design @ coef
    dof = max(len(target) - 6, 1)
    sigma = float(np.sqrt(residual @ residual / dof))
    hessian = np.array([[2 * coef[3], coef[4]], [coef[4], 2 * coef[5]]])
    eigenvalues = np.linalg.eigh(hessian)[0]
    if abs(np.linalg.det(hessian)) > 1e-12:
        point = -np.linalg.solve(hessian, np.array([coef[1], coef[2]]))
    else:
        point = np.array([np.nan, np.nan])
    inside = bool(np.all(np.isfinite(point))
                  and x.min() <= point[0] <= x.max() and y.min() <= point[1] <= y.max())

    # 参数自助法：给极小点的位置误差；落在网格外时同时说明是外推方向
    rng = np.random.default_rng(20261121)
    fitted = design @ coef
    draws = []
    for _ in range(2000):
        sample = fitted + rng.normal(0.0, sigma, size=target.size)
        current, *_ = np.linalg.lstsq(design, sample, rcond=None)
        local = np.array([[2 * current[3], current[4]], [current[4], 2 * current[5]]])
        if abs(np.linalg.det(local)) > 1e-12:
            draws.append(-np.linalg.solve(local, np.array([current[1], current[2]])))
    draws = np.array(draws)
    return {"point": point.tolist(), "inside_grid": inside, "hessian": hessian.tolist(),
            "eigenvalues": eigenvalues.tolist(), "sigma_mV2": sigma, "dof": dof,
            "coef": coef.tolist(),
            "monte_carlo": {"x68": np.percentile(draws[:, 0], [16, 84]).tolist(),
                            "y68": np.percentile(draws[:, 1], [16, 84]).tolist(),
                            "x95": np.percentile(draws[:, 0], [2.5, 97.5]).tolist(),
                            "y95": np.percentile(draws[:, 1], [2.5, 97.5]).tolist()},
            "samples": draws.tolist()}


def slope_regression(grid):
    """S 对 X、Y 与时间做线性回归，把偏置效应和漂移分开。"""
    s = np.array([row["S_mV_per_deg"] for row in grid])
    design = np.column_stack([np.ones_like(s), [row["x"] for row in grid],
                              [row["y"] for row in grid], [row["minutes"] for row in grid]])
    coef, *_ = np.linalg.lstsq(design, s, rcond=None)
    residual = s - design @ coef
    dof = max(len(s) - 4, 1)
    sigma = float(np.sqrt(residual @ residual / dof))
    covariance = sigma ** 2 * np.linalg.inv(design.T @ design)
    error = np.sqrt(np.diag(covariance))
    return {"names": ["const", "X_per_V", "Y_per_V", "per_min"], "coef": coef.tolist(),
            "error": error.tolist(), "sigma_mV_per_deg": sigma, "dof": dof,
            "rms_residual": float(np.sqrt(np.mean(residual ** 2)))}


def plot(rows, grid, linear, quadratic, regression):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from lab_workflows.plotting import format_axis, plot_style_context, save_figure, style_legend

    FIGURES.mkdir(parents=True, exist_ok=True)
    xs = sorted({row["x"] for row in grid})
    ys = sorted({row["y"] for row in grid})
    lookup = {(row["x"], row["y"]): row for row in grid}
    refs = [row for row in rows if row["role"] == "ref"]

    def panel(axis, key, label, cmap, title, fmt="{:.2f}"):
        matrix = np.array([[lookup[(x, y)][key] for y in ys] for x in xs])
        mesh = axis.pcolormesh(ys, xs, matrix, cmap=cmap, shading="nearest")
        low, high = float(np.nanmin(matrix)), float(np.nanmax(matrix))
        span = (high - low) or 1.0
        for i, x in enumerate(xs):
            for j, y in enumerate(ys):
                # 浅色格子用黑字，深色格子用白字，避免读数看不清。
                colour = "k" if (matrix[i, j] - low) / span > 0.6 else "w"
                axis.text(y, x, fmt.format(matrix[i, j]), ha="center", va="center", fontsize=4.4, color=colour)
        axis.set_xlim(ys[0] - 0.03, ys[-1] + 0.03)
        axis.set_ylim(xs[0] - 0.02, xs[-1] + 0.02)
        format_axis(axis, xlabel="Y bias (V)", ylabel="X bias (V)")
        axis.set_title(title, fontsize=6)
        return mesh

    with plot_style_context("paper"):
        figure, (left, right) = plt.subplots(1, 2, figsize=(6.6, 2.9), layout="constrained")
        mesh = panel(left, "c0_abs_mV", "c0", "viridis", "$|c_0|$ (mV)")
        figure.colorbar(mesh, ax=left, fraction=0.046, pad=0.02)
        point = quadratic["point"]
        if np.all(np.isfinite(point)):
            left.plot(point[1], point[0], "*", ms=10, color="C3")
        mesh = panel(right, "S_mV_per_deg", "S", "magma", "$S$ (mV/deg)", fmt="{:.3f}")
        figure.colorbar(mesh, ax=right, fraction=0.046, pad=0.02)
        save_figure(figure, FIGURES / "xy_c0_maps.png")

        figure, axes = plt.subplots(1, 3, figsize=(8.4, 2.6), layout="constrained")
        axis = axes[0]
        cloud = np.array(quadratic["samples"])
        axis.plot(cloud[:, 0], cloud[:, 1], ".", ms=0.6, color="C3", alpha=0.12)
        axis.plot([row["x"] for row in grid], [row["y"] for row in grid], "o", ms=3, mfc="none", mec="0.45")
        axis.plot(*quadratic["point"], "*", ms=11, color="C3")
        axis.plot(*REF_XY, "s", ms=4, mfc="none", mec="C0")
        axis.set_xlim(min(xs) - 0.03, max(xs) + 0.05)
        axis.set_ylim(min(ys) - 0.04, max(ys) + 0.04)
        format_axis(axis, xlabel="X bias (V)", ylabel="Y bias (V)")
        axis.set_title("$|c_0|^2$ minimum (2000 draws)", fontsize=6)

        axis = axes[1]
        for x in xs:
            items = [lookup[(x, y)] for y in ys]
            axis.plot([row["y"] for row in items], [row["S_mV_per_deg"] for row in items], "o-", ms=2.6,
                      lw=0.7, label=f"X={x:.2f}")
        slope_y = regression["coef"][2]
        axis.set_title(f"S per volt of Y: {slope_y:+.2f} mV/deg", fontsize=6)
        format_axis(axis, xlabel="Y bias (V)", ylabel="S (mV/deg)")
        style_legend(axis, loc="lower right", fontsize=4.2, ncol=1, frameon=False)

        axis = axes[2]
        axis.plot([row["minutes"] for row in refs], [row["S_mV_per_deg"] for row in refs], "o-", ms=3,
                  lw=0.8, color="C1", label="ref S")
        axis.plot([row["minutes"] for row in grid], [row["S_mV_per_deg"] for row in grid], ".", ms=2.4,
                  color="0.6", label="grid S")
        format_axis(axis, xlabel="elapsed time (min)", ylabel="S (mV/deg)")
        style_legend(axis, loc="lower left", fontsize=4.6, frameon=False)
        figure.tight_layout()
        save_figure(figure, FIGURES / "xy_null_and_slope.png")


def main():
    rows = load()
    refs, gaps = drift_normalise(rows)
    grid = [row for row in rows if row["role"] == "grid"]
    linear = linear_null(grid)
    quadratic = quadratic_null(grid)
    regression = slope_regression(grid)
    plot(rows, grid, linear, quadratic, regression)

    TABLES.mkdir(parents=True, exist_ok=True)
    with (TABLES / "xy_scan.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["tag", "run_dir", "role", "x_V", "y_V", "c0_re_mV", "c0_im_mV", "c0_abs_mV",
                         "c0_arg_deg", "c1_mV", "cm1_mV", "S_mV_per_deg", "S_ref", "r"])
        for row in rows:
            writer.writerow([row["tag"], Path(row["run_dir"]).name, row["role"], "%.4f" % row["x"],
                             "%.4f" % row["y"], "%.3f" % row["c0_re_mV"], "%.3f" % row["c0_im_mV"],
                             "%.3f" % row["c0_abs_mV"], "%.2f" % row["c0_arg_deg"], "%.3f" % row["c1_mV"],
                             "%.3f" % row["cm1_mV"], "%.4f" % row["S_mV_per_deg"],
                             "%.4f" % row["S_ref"], "%.4f" % row["r"]])

    record = {"linear_null": {k: v for k, v in linear.items() if k != "samples"},
              "quadratic_null": {k: v for k, v in quadratic.items() if k != "samples"},
              "slope_regression": regression,
              "refs": [{"tag": row["tag"], "S_mV_per_deg": row["S_mV_per_deg"],
                        "minutes": row["minutes"]} for row in refs],
              "ref_gaps": [{"from": name_from, "to": name_to, "change": change} for change, name_from, name_to in gaps],
              "grid": {"x": sorted({row["x"] for row in grid}), "y": sorted({row["y"] for row in grid})},
              "n_grid": len(grid)}
    (TABLES / "xy_fit.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({k: record[k] for k in ("linear_null", "quadratic_null", "slope_regression")},
                     ensure_ascii=False, indent=2)[:2600])
    print("max ref gap", max(change for change, _, _ in gaps))


if __name__ == "__main__":
    main()
