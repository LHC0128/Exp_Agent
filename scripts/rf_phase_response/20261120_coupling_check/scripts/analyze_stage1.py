"""阶段 1（Z 耦合检查）分析：拟合每个位置的 Z 峰位与峰高，再拟合 Z0(w, s_f) 线性模型。

回答 Q1：最优 Z 是否随脉冲宽度 w 或拖尾比例 s_f 移动。
输入 coupling_check/data/stage1_runs.jsonl；输出 data/stage1.csv、data/stage1_fit.json 与两张图。
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
from scipy.optimize import curve_fit

import cc_common as cc

OUT = cc.RESULT_DIR
POSITIONS = ("P0", "P1", "P2", "P3", "P4", "P5")
TITLES = {"P0": "P0 current optimum 8.03/0.575/38.3", "P1": "P1 narrow 7.5",
          "P2": "P2 wide 8.6", "P3": "P3 low sf 0.50", "P4": "P4 high sf 0.65",
          "P5": "P5 clean pi 8.03/0.544/46.9"}


def fit_peak(rows):
    """固定峰形，只拟合峰高 H 与峰位 Z0。"""
    z = np.array([row["z"] for row in rows])
    r = np.array([row["r"] for row in rows])

    def model(field, height, centre):
        return height * cc.peak_shape(field, centre)

    start = [float(np.max(r)), -0.068]
    width = 0.004
    params, covariance = curve_fit(model, z, r, p0=start, maxfev=20000,
                                   bounds=([0.0, -0.10], [10.0, -0.04]))
    residual = r - model(z, *params)
    dof = max(len(z) - 2, 1)
    return {"H": float(params[0]), "Z0": float(params[1]),
            "H_err": float(np.sqrt(covariance[0, 0])), "Z0_err": float(np.sqrt(covariance[1, 1])),
            "residual_rms": float(np.sqrt(np.mean(residual ** 2))), "dof": dof,
            "residual": residual.tolist(), "flagged_extrapolation":
                not (-0.0725 <= params[1] <= -0.0635)}


def linear_model(fits):
    """Z0(w, s_f) = Zc + a_w (w-8.03) + a_s (s_f-0.575)，用 P0–P4 最小二乘。"""
    keys = [key for key in POSITIONS if key != "P5" and key in fits]
    matrix = np.array([[1.0, fits[key]["w"] - 8.03, fits[key]["sf"] - 0.575] for key in keys])
    target = np.array([fits[key]["Z0"] for key in keys])
    coef, *_ = np.linalg.lstsq(matrix, target, rcond=None)
    residual = target - matrix @ coef
    dof = max(len(keys) - 3, 1)
    sigma2 = float(residual @ residual / dof)
    covariance = sigma2 * np.linalg.inv(matrix.T @ matrix)
    return {"keys": keys, "Zc": float(coef[0]), "a_w": float(coef[1]), "a_s": float(coef[2]),
            "a_w_err": float(np.sqrt(covariance[1, 1])), "a_s_err": float(np.sqrt(covariance[2, 2])),
            "Zc_err": float(np.sqrt(covariance[0, 0])), "residual_rms": float(np.sqrt(sigma2)),
            "dof": dof}


def plot(fits, model):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from lab_workflows.plotting import format_axis, plot_style_context, save_figure

    figures = OUT / "figures"
    figures.mkdir(parents=True, exist_ok=True)
    grid = np.linspace(-0.0745, -0.0615, 200)
    with plot_style_context("paper"):
        figure, axes = plt.subplots(2, 3, figsize=(7.4, 4.2), sharex=True, sharey=True)
        for axis, key in zip(axes.ravel(), POSITIONS):
            item = fits.get(key)
            if item is None:
                axis.set_axis_off()
                continue
            z = np.array(item["z"])
            r = np.array(item["r"])
            axis.plot(z, r, "o", ms=3.2, color="C0")
            axis.plot(grid, item["H"] * cc.peak_shape(grid, item["Z0"]), "-", lw=0.9, color="C3")
            axis.axvline(-0.068, color="0.6", ls=":", lw=0.7)
            axis.set_title(TITLES[key], fontsize=5.5)
            axis.text(0.03, 0.06, f"$Z_0$={item['Z0']:.4f}\nH={item['H']:.3f}", transform=axis.transAxes,
                      fontsize=5, va="bottom")
            format_axis(axis, xlabel="Z pulse current (mA)", ylabel="r = S / S$_{ref}$")
        figure.tight_layout()
        save_figure(figure, figures / "z_peaks_by_position.png")

        figure, (left, right) = plt.subplots(1, 2, figsize=(6.0, 2.6))
        for axis, keys, values, label in ((left, ("P0", "P1", "P2"), "w", "w (µs)"),
                                          (right, ("P0", "P3", "P4"), "sf", "s$_f$")):
            xs = [fits[key][values] for key in keys if key in fits]
            ys = [fits[key]["Z0"] * 1e3 for key in keys if key in fits]
            es = [fits[key]["Z0_err"] * 1e3 for key in keys if key in fits]
            axis.errorbar(xs, ys, yerr=es, fmt="o-", ms=3.4, lw=0.8, color="C0", capsize=2)
            for x, y, key in zip(xs, ys, [key for key in keys if key in fits]):
                axis.annotate(key, (x, y), textcoords="offset points", xytext=(4, 4), fontsize=5)
            format_axis(axis, xlabel=label, ylabel="$Z_0$ (µA)")
        if "P5" in fits:
            right.axhline(fits["P5"]["Z0"] * 1e3, color="C2", ls="--", lw=0.8)
            right.text(0.60, fits["P5"]["Z0"] * 1e3, " P5 clean π", color="C2", fontsize=5, va="bottom")
        figure.tight_layout()
        save_figure(figure, figures / "z0_vs_params.png")
    return model


def main():
    rows = cc.load("stage1")
    if not rows:
        raise SystemExit("stage1_runs.jsonl 为空：阶段 1 还没有数据")
    summary = cc.drift_normalise(rows)

    by_position = {}
    for row in rows:
        if row["role"] != "scan":
            continue
        by_position.setdefault(row["position"], []).append(row)
    fits = {}
    for key, items in by_position.items():
        fit = fit_peak(sorted(items, key=lambda row: row["z"]))
        fit.update(w=items[0]["w"], sf=items[0]["sf"], te=items[0]["te"], n=len(items),
                   z=[row["z"] for row in sorted(items, key=lambda row: row["z"])],
                   r=[row["r"] for row in sorted(items, key=lambda row: row["z"])])
        fits[key] = fit
    model = linear_model(fits)
    base = fits["P0"]["Z0"]
    deviations = {key: fits[key]["Z0"] - base for key in fits}
    coupled = any(abs(value) > cc.COUPLING_TOLERANCE_MA for value in deviations.values())

    TABLES = OUT / "tables"
    TABLES.mkdir(parents=True, exist_ok=True)
    with (TABLES / "stage1.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["tag", "run_dir", "Z", "w", "sf", "te", "S_mV_per_deg", "c0_mV",
                         "S_ref", "r", "area_total"])
        for row in rows:
            writer.writerow([row["tag"], Path(row["run_dir"]).name, "%.4f" % row["z"], "%.3f" % row["w"],
                             "%.3f" % row["sf"], "%.1f" % row["te"], "%.4f" % row["S_mV_per_deg"],
                             "%.2f" % row["c0_mV"], "%.4f" % row["S_ref"], "%.4f" % row["r"],
                             "%.4f" % row["area_total"]])

    record = {"coupling": bool(coupled), "tolerance_ma": cc.COUPLING_TOLERANCE_MA,
              "positions": {key: {name: value for name, value in fits[key].items() if name not in ("z", "r")}
                            for key in fits},
              "deviation_from_p0_ma": deviations, "linear_model": model,
              "drift": {"max_abs_change": summary["max_abs_change"],
                        "gaps": summary["gaps"],
                        "refs": [{"tag": item["tag"], "S": item["S_mV_per_deg"],
                                  "time": item["time"].isoformat(timespec="seconds")} for item in summary["refs"]]}}
    (TABLES / "stage1_fit.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    plot(fits, model)

    print(json.dumps({key: {name: round(value, 5) for name, value in fits[key].items()
                            if name in ("H", "Z0", "Z0_err", "H_err", "residual_rms")} for key in fits},
                     ensure_ascii=False, indent=2))
    print(json.dumps({key: round(value, 6) for key, value in model.items() if key != "keys"}, indent=2))
    print("Q1 coupled =", coupled, " max|dZ0| =", max(abs(value) for value in deviations.values()))
    print("max ref drift =", summary["max_abs_change"])


if __name__ == "__main__":
    main()
