"""射频幅度扫描分析（Y 或 X 方向）：S、|c0|、R 峰、残差与逐幅度谐波随 Y_RF_VPP 的变化。

用法：python scripts/rf_phase_response/20261120_rf_vpp_scan/scripts/analyze_vpp_sweep.py [--runlist x_sweep]
输入 _studies/20261120_rf_vpp_scan/<runlist>_runs.jsonl；输出按 runlist 前缀命名：
tables/<前缀>_vpp.csv、<前缀>_vpp_fit.json、<前缀>_harmonics.csv/.json，figures/<前缀>_vpp.png、
<前缀>_phase_family.png。参考点用来做漂移归一。
"""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import numpy as np

from lab_workflows.common import data_root

STUDY = "20261120_rf_vpp_scan"
PROCESS = data_root() / "rf_phase_response" / "_studies" / STUDY
OUT = Path(__file__).resolve().parents[1]
SATURATION_V = 0.344


def load(runlist):
    path = PROCESS / f"{runlist}_runs.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return sorted(rows, key=lambda row: int(row["order"]))


def drift(rows):
    """参考点的 S 线性插值（按 order 近似时间），给网格点做漂移归一。"""
    refs = [(row["order"], row["S_mV_per_deg"]) for row in rows if row["role"] == "ref"]
    if len(refs) < 2:
        return lambda order: refs[0][1] if refs else float("nan")
    xs = [item[0] for item in refs]
    ys = [item[1] for item in refs]
    return lambda order: float(np.interp(order, xs, ys))


def read_curve(run_dir):
    """读一次运行的逐相位 Z=x+iy（与 11-20 上午幅度扫描同一口径）。"""
    phases, zs = [], []
    with (Path(run_dir) / "raw" / "phase_response.csv").open(encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            phases.append(float(row["phase_deg"]))
            zs.append(complex(float(row["x_v"]), float(row["y_v"])))
    order = np.argsort(phases)
    return np.asarray(phases, float)[order], np.asarray(zs, complex)[order]


def fit_harmonics(phases, zs, n_harm):
    """对 Z(φ) 做 ±1…±n 次谐波最小二乘，返回系数与残差 RMS（复数值 V）。"""
    phi = np.radians(phases)
    cols, names = [np.ones_like(phi)], ["c0"]
    for h in range(1, n_harm + 1):
        cols.append(np.exp(1j * h * phi))
        names.append(f"c_plus{h}")
    for h in range(1, n_harm + 1):
        cols.append(np.exp(-1j * h * phi))
        names.append(f"c_minus{h}")
    basis = np.stack(cols, axis=1)
    coef, *_ = np.linalg.lstsq(basis, zs, rcond=None)
    residual = zs - basis @ coef
    return dict(zip(names, coef)), float(np.sqrt(np.mean(np.abs(residual) ** 2)))


def family(grid, figures, prefix):
    """逐幅度的相位响应：谐波内容表 + R(φ) 曲线族 + Z(φ) 轨迹。

    指标与 11-20 上午的幅度扫描一致（S_model / S_raw / c0 / c1 / c3 / c5 / R 峰谷 / 残差），
    便于跨工作点对比。
    """
    series = []
    for row in sorted(grid, key=lambda item: item["vpp"]):
        phases, zs = read_curve(row["run_dir"])
        three, rms3 = fit_harmonics(phases, zs, 1)
        full, rms_full = fit_harmonics(phases, zs, 5)
        rad = np.radians(phases)
        slope = np.abs(zs[2:] - zs[:-2]) / (rad[2:] - rad[:-2])
        series.append({
            "vpp_v": float(row["vpp"]),
            "tag": row["tag"],
            "S_model_mV_deg": (abs(three["c_plus1"]) + abs(three["c_minus1"])) * math.pi / 180 * 1e3,
            "S_raw_mV_deg": float(slope.max()) * math.pi / 180 * 1e3,
            "c0_mV": abs(three["c0"]) * 1e3,
            "c1_mV": 0.5 * (abs(full["c_plus1"]) + abs(full["c_minus1"])) * 1e3,
            "c3_mV": 0.5 * (abs(full["c_plus3"]) + abs(full["c_minus3"])) * 1e3,
            "c5_mV": 0.5 * (abs(full["c_plus5"]) + abs(full["c_minus5"])) * 1e3,
            "R_peak_V": float(np.max(np.abs(zs))),
            "R_min_V": float(np.min(np.abs(zs))),
            "rms3_mV": rms3 * 1e3,
            "rms_full_mV": rms_full * 1e3,
            "phases": phases, "R": np.abs(zs), "Z": zs,
        })

    OUT.joinpath("tables").mkdir(parents=True, exist_ok=True)
    flat = [{key: value for key, value in item.items() if key not in ("phases", "R", "Z")} for item in series]
    with (OUT / "tables" / f"{prefix}_harmonics.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(flat[0].keys()))
        writer.writeheader()
        writer.writerows([{key: (round(value, 6) if isinstance(value, float) else value)
                           for key, value in row.items()} for row in flat])
    (OUT / "tables" / f"{prefix}_harmonics.json").write_text(
        json.dumps(flat, ensure_ascii=False, indent=2), encoding="utf-8")

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from lab_workflows.plotting import format_axis, plot_style_context, save_figure, style_legend

    vpp = np.array([item["vpp_v"] for item in series]) * 1e3
    colours = plt.cm.viridis(np.linspace(0.05, 0.9, len(series)))
    with plot_style_context("paper"):
        figure, axes = plt.subplots(2, 2, figsize=(6.4, 4.6), layout="constrained")
        axis = axes[0, 0]
        for item, colour in zip(series, colours):
            axis.plot(item["phases"], item["R"], "-", lw=0.9, color=colour)
        format_axis(axis, xlabel=r"RF phase $\varphi$ (°)", ylabel="R (V)")
        axis.set_title("(a) R($\\varphi$) per amplitude", fontsize=6, loc="left")
        from matplotlib.colors import LogNorm
        mapper = plt.cm.ScalarMappable(norm=LogNorm(vmin=max(float(vpp.min()), 1.0), vmax=float(vpp.max())),
                                       cmap="viridis")
        bar = figure.colorbar(mapper, ax=axis, pad=0.02, fraction=0.05)
        bar.set_label("Y_RF_VPP (mVpp)", fontsize=5)
        bar.ax.tick_params(labelsize=4.5)

        axis = axes[0, 1]
        for item, colour in zip(series, colours):
            axis.plot(item["Z"].real * 1e3, item["Z"].imag * 1e3, "-", lw=0.9, color=colour)
        format_axis(axis, xlabel="Re Z (mV)", ylabel="Im Z (mV)")
        axis.set_title("(b) Z($\\varphi$) locus", fontsize=6, loc="left")

        axis = axes[1, 0]
        for key, label, marker in (("c1_mV", r"$|c_1|$", "o"), ("c3_mV", r"$|c_3|$", "^"),
                                   ("c5_mV", r"$|c_5|$", "v")):
            axis.semilogx(vpp, [item[key] for item in series], marker + "-", ms=2.6, lw=0.8, label=label)
        format_axis(axis, xlabel="Y_RF_VPP (mVpp)", ylabel="component (mV)")
        style_legend(axis, loc="upper left", fontsize=5, frameon=False)
        axis.set_title("(c) harmonic content", fontsize=6, loc="left")

        axis = axes[1, 1]
        axis.semilogx(vpp, [item["S_model_mV_deg"] for item in series], "o-", ms=2.6, lw=0.8,
                      label="3-component fit")
        axis.semilogx(vpp, [item["S_raw_mV_deg"] for item in series], "s--", ms=2.6, lw=0.8,
                      label="raw max |dZ/d$\\varphi$|")
        format_axis(axis, xlabel="Y_RF_VPP (mVpp)", ylabel="slope (mV/deg)")
        style_legend(axis, loc="upper left", fontsize=5, frameon=False)
        axis.set_title("(d) fit vs raw slope", fontsize=6, loc="left")

        save_figure(figure, figures / f"{prefix}_phase_family.png")
    return flat


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--runlist", default="y_sweep", help="runlists 与 _studies 里的名字，默认 y_sweep")
    args = parser.parse_args(argv)
    prefix = args.runlist.replace("_sweep", "")
    axis_label = "X_RF" if prefix == "x" else "Y_RF"
    rows = load(args.runlist)
    ref_at = drift(rows)
    grid = [row for row in rows if row["role"] == "grid"]
    for row in rows:
        row["r"] = row["S_mV_per_deg"] / ref_at(row["order"]) if row["role"] == "grid" else 1.0

    vpp = np.array([row["vpp"] for row in grid])
    slope = np.array([row["S_mV_per_deg"] for row in grid])
    rpeak = np.array([row["r_peak_v"] for row in grid])
    residual = np.array([row["residual_mV"] for row in grid])

    # 线性区：只用残差 < 6 mV 且 R 峰 < 0.25 V 的点做零截距拟合
    clean = (residual < 6.0) & (rpeak < 0.25)
    if clean.sum() >= 2:
        gain = float(np.sum(slope[clean] * vpp[clean]) / np.sum(vpp[clean] ** 2))
    else:
        gain = float("nan")
    expected = gain * vpp
    compression = np.where(expected > 0, slope / expected, np.nan)
    first_below = [float(v) for v, c in zip(vpp, compression) if np.isfinite(c) and c < 0.9]

    record = {
        "points": len(rows), "grid_points": len(grid),
        "linear_gain_mV_per_deg_per_vpp": gain,
        "linear_region_vpp": [float(vpp[clean].min()), float(vpp[clean].max())] if clean.any() else None,
        "first_vpp_below_90pct_linear": min(first_below) if first_below else None,
        "saturation_peak_v": SATURATION_V,
        "max_r_peak_v": float(rpeak.max()),
        "refs": [{"tag": row["tag"], "S": row["S_mV_per_deg"]} for row in rows if row["role"] == "ref"],
        "ref_spread": float(np.ptp([row["S_mV_per_deg"] for row in rows if row["role"] == "ref"])),
    }

    OUT.joinpath("tables").mkdir(parents=True, exist_ok=True)
    with (OUT / "tables" / f"{prefix}_vpp.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["tag", "run_dir", "vpp_v", "S_mV_per_deg", "r", "c0_mV", "R_peak_V",
                         "residual_mV", "fit_quality"])
        for row in rows:
            writer.writerow([row["tag"], Path(row["run_dir"]).name, "%.3f" % row["vpp"],
                             "%.4f" % row["S_mV_per_deg"], "%.4f" % row["r"], "%.2f" % row["c0_abs_mV"],
                             "%.4f" % row["r_peak_v"], "%.2f" % row["residual_mV"], row["fit_quality"]])
    (OUT / "tables" / f"{prefix}_vpp_fit.json").write_text(json.dumps(record, ensure_ascii=False, indent=2),
                                                       encoding="utf-8")

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from lab_workflows.plotting import format_axis, plot_style_context, save_figure, style_legend

    figures = OUT / "figures"
    figures.mkdir(parents=True, exist_ok=True)
    order = np.argsort(vpp)
    vpp_mV = vpp * 1e3
    with plot_style_context("paper"):
        figure, axes = plt.subplots(2, 2, figsize=(6.4, 4.4), sharex=True)
        panels = (
            (axes[0, 0], slope, "S (mV/deg)", "C0"),
            (axes[0, 1], np.array([row["c0_abs_mV"] for row in grid]), r"$|c_0|$ (mV)", "C1"),
            (axes[1, 0], rpeak * 1e3, "R peak (mV)", "C3"),
            (axes[1, 1], residual, "fit residual (mV)", "C4"),
        )
        for axis, values, label, colour in panels:
            axis.plot(vpp_mV[order], values[order], "o", ms=3.2, color=colour)
            if label.startswith("S "):
                grid_v = np.linspace(0, vpp_mV.max(), 100)
                axis.plot(grid_v, gain * grid_v / 1e3, "-", lw=0.8, color="0.5",
                          label="linear fit (20–40 mVpp)")
                style_legend(axis, loc="upper left", fontsize=5, frameon=False)
            if label.startswith("R peak"):
                axis.axhline(SATURATION_V * 1e3, color="0.4", ls=":", lw=0.8)
            format_axis(axis, xlabel="", ylabel=label)
        for axis in axes[1]:
            format_axis(axis, xlabel=f"{axis_label}_VPP (mVpp)", ylabel=axis.get_ylabel())
        figure.tight_layout()
        save_figure(figure, figures / f"{prefix}_vpp.png")

    harmonics = family(grid, figures, prefix)
    print(json.dumps(record, ensure_ascii=False, indent=2))
    print("线性区增益 %.4f mV/deg 每 Vpp；压缩到 90%% 以下的首个幅度 %s Vpp"
          % (gain, record["first_vpp_below_90pct_linear"]))
    print("%6s %8s %8s %7s %7s %7s %7s %7s %7s"
          % ("mVpp", "S_model", "S_raw", "c0", "c1", "c3", "c5", "Rpk", "rmsH"))
    for row in harmonics:
        print("%6.0f %8.2f %8.2f %7.1f %7.1f %7.1f %7.1f %7.3f %7.1f"
              % (row["vpp_v"] * 1e3, row["S_model_mV_deg"], row["S_raw_mV_deg"], row["c0_mV"],
                 row["c1_mV"], row["c3_mV"], row["c5_mV"], row["R_peak_V"], row["rms_full_mV"]))


if __name__ == "__main__":
    main()
