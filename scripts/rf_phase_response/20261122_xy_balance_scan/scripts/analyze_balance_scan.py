"""XY 平衡方格扫描分析：两个瓣的峰高是否随偏置平衡而等高。

用法：python analyze_balance_scan.py [--runlist balance_scan|balance_wide]
输入 _studies/20261122_xy_balance_scan/<runlist>_runs.jsonl；
输出 tables/<runlist>.csv、tables/<runlist>_fit.json、figures/<runlist>_{family,symmetry,maps}.png。

判据（用户口径）：R(φ) 两个瓣的峰高 P_A、P_B 相等 ⇔ 平衡好。不平衡度 =（P_A−P_B）/均值，
应当随 |c0| 增大；镜像残差 mean|R(φ)−R(φ+180°)| 是同一件事的连续版本。
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import numpy as np

from lab_workflows.common import data_root

DATA_TYPE = "rf_phase_response"
STUDY = "20261122_xy_balance_scan"
PROCESS_DIR = data_root() / DATA_TYPE / "_studies" / STUDY
RESULT_DIR = Path(__file__).resolve().parents[1]
TABLES = RESULT_DIR / "tables"
FIGURES = RESULT_DIR / "figures"
SATURATION_MV = 344.0


def load(runlist):
    path = PROCESS_DIR / f"{runlist}_runs.jsonl"
    if not path.is_file():
        raise SystemExit(f"{path} 不存在：先跑 run_balance_scan.py --runlist {runlist}")
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows.sort(key=lambda row: row["order"])
    try:  # 早先的批次没有记录瓣峰（lobe_*），就地补算，保证新旧批次同一口径
        from harmonics import summarise

        for row in rows:
            if "lobe_imbalance" in row:
                continue
            record, _, _ = summarise(row["run_dir"])
            row.update({key: record[key] for key in
                        ("peak_a_mV", "peak_b_mV", "lobe_imbalance", "mirror_rel")})
    except Exception as exc:
        raise SystemExit(f"补算瓣峰失败：{exc}")
    return rows


def correlation(x, y):
    x, y = np.asarray(x, float), np.asarray(y, float)
    slope, intercept = np.polyfit(x, y, 1)
    residual = y - (slope * x + intercept)
    dof = max(len(x) - 2, 1)
    sigma = float(np.sqrt(residual @ residual / dof))
    sx = float(np.sqrt(((x - x.mean()) ** 2).sum()))
    return {"slope": float(slope), "intercept": float(intercept), "slope_err": float(sigma / sx),
            "r": float(np.corrcoef(x, y)[0, 1]), "residual_rms": sigma}


def projection_fit(scans, imbalance):
    """不平衡度正比于 c0 在某个方向上的投影：扫描投影角，取相关性最强的方向。"""
    c0 = np.array([row["c0_mV"] * np.exp(1j * np.radians(row["c0_arg_deg"])) for row in scans])
    best = None
    for theta in np.linspace(-180.0, 180.0, 721):
        projected = (c0 * np.exp(-1j * np.radians(theta))).real
        r = float(np.corrcoef(projected, imbalance)[0, 1])
        if best is None or abs(r) > abs(best[1]):
            best = (float(theta), r)
    theta, r = best
    projected = (c0 * np.exp(-1j * np.radians(theta))).real
    fit = correlation(projected, imbalance)
    fit.update(theta_deg=theta, projected=projected.tolist(), zero_mV=float(-fit["intercept"] / fit["slope"])
               if fit["slope"] else float("nan"))
    return fit


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--runlist", default="balance_scan")
    args = parser.parse_args(argv)
    rows = load(args.runlist)
    scans = [row for row in rows if row["role"] != "ref"]
    refs = [row for row in rows if row["role"] == "ref"]
    grid = {(round(row["x"], 4), round(row["y"], 4)): row for row in scans}
    xs = sorted({key[0] for key in grid})
    ys = sorted({key[1] for key in grid})

    TABLES.mkdir(parents=True, exist_ok=True)
    with (TABLES / f"{args.runlist}.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["tag", "run_dir", "x", "y", "c0_mV", "c0_arg_deg", "c1_mV", "c2_mV", "c3_mV",
                         "even_ratio", "third_ratio", "S_mV_per_deg", "r_peak_mV", "headroom_mV",
                         "peak_a_mV", "peak_b_mV", "lobe_imbalance", "mirror_rel"])
        for row in rows:
            writer.writerow([row["tag"], Path(row["run_dir"]).name, "%.3f" % row["x"], "%.3f" % row["y"],
                             "%.2f" % row["c0_mV"], "%.2f" % row["c0_arg_deg"], "%.2f" % row["c1_mV"],
                             "%.2f" % row["c2_mV"], "%.2f" % row["c3_mV"], "%.4f" % row["even_ratio"],
                             "%.4f" % row["third_ratio"], "%.3f" % row["S_mV_per_deg"],
                             "%.1f" % row["r_peak_mV"], "%.1f" % row["headroom_mV"],
                             "%.1f" % row["peak_a_mV"], "%.1f" % row["peak_b_mV"],
                             "%+.4f" % row["lobe_imbalance"], "%.4f" % row["mirror_rel"]])

    c0 = np.array([row["c0_mV"] for row in scans])
    imbalance = np.array([row["lobe_imbalance"] for row in scans])
    mirror = np.array([row["mirror_rel"] for row in scans])
    peak = np.array([row["r_peak_mV"] for row in scans])
    projection = projection_fit(scans, imbalance)
    record = {
        "runlist": args.runlist, "points": len(scans),
        "imbalance_vs_projection": projection,
        "c0_mV": {"min": float(c0.min()), "median": float(np.median(c0)), "max": float(c0.max())},
        "imbalance": {"min": float(imbalance.min()), "median": float(np.median(imbalance)),
                      "max": float(imbalance.max())},
        "mirror_rel": {"min": float(mirror.min()), "median": float(np.median(mirror)), "max": float(mirror.max())},
        "peak_mV": {"min": float(peak.min()), "max": float(peak.max())},
        "imbalance_vs_c0": correlation(c0, imbalance),
        "mirror_vs_c0": correlation(c0, mirror),
        "clipped_points": int((peak > SATURATION_MV * 0.98).sum()),
        "refs": [{"tag": row["tag"], "c0_mV": row["c0_mV"], "peak_a_mV": row["peak_a_mV"],
                  "peak_b_mV": row["peak_b_mV"], "lobe_imbalance": row["lobe_imbalance"]} for row in refs],
    }
    (TABLES / f"{args.runlist}_fit.json").write_text(json.dumps(record, ensure_ascii=False, indent=2),
                                                     encoding="utf-8")

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from lab_workflows.plotting import format_axis, plot_style_context, save_figure, style_legend
    from harmonics import read_curve

    FIGURES.mkdir(parents=True, exist_ok=True)
    with plot_style_context("paper"):
        figure, axes = plt.subplots(len(ys), len(xs), figsize=(7.0, 6.2), sharex=True, sharey=True,
                                    layout="constrained")
        for row_index, y in enumerate(ys):
            for col_index, x in enumerate(xs):
                axis = axes[len(ys) - 1 - row_index][col_index]
                item = grid.get((round(x, 4), round(y, 4)))
                if item is None:
                    axis.set_axis_off()
                    continue
                phases, values = read_curve(item["run_dir"])
                magnitudes = np.abs(values) * 1e3
                axis.plot(np.degrees(phases), magnitudes, "-", lw=0.8, color="C0")
                axis.axhline(SATURATION_MV, color="C3", ls=":", lw=0.7)
                for peak_value, colour in ((item["peak_a_mV"], "C3"), (item["peak_b_mV"], "C1")):
                    axis.axhline(peak_value, color=colour, ls="--", lw=0.5)
                axis.set_ylim(0, 380)
                axis.text(0.04, 0.84, "|c$_0$|=%.0f" % item["c0_mV"], transform=axis.transAxes, fontsize=4.2)
                axis.text(0.04, 0.70, "imb=%+.2f" % item["lobe_imbalance"], transform=axis.transAxes,
                          fontsize=4.2, color="C3")
                if col_index == 0:
                    axis.set_ylabel("Y=%+.2f V" % y, fontsize=4.5)
                if row_index == 0:
                    axis.set_title("X=%.2f V" % x, fontsize=5)
        figure.supxlabel("RF phase φ (°)", fontsize=6)
        figure.supylabel("R (mV)", fontsize=6)
        save_figure(figure, FIGURES / f"{args.runlist}_family.png")

        figure, axes = plt.subplots(1, 2, figsize=(6.4, 2.7), layout="constrained")
        projected = np.array(projection["projected"])
        axes[0].plot(projected, imbalance, "o", ms=3.4, color="C0")
        grid_x = np.linspace(projected.min(), projected.max(), 50)
        axes[0].plot(grid_x, projection["slope"] * grid_x + projection["intercept"], "-", lw=0.9, color="0.4",
                     label="r=%.2f, zero %.1f mV" % (projection["r"], projection["zero_mV"]))
        axes[0].axhline(0.0, color="0.7", ls=":", lw=0.7)
        format_axis(axes[0], xlabel=r"Re[$c_0 e^{-i\,\theta}$] at $\theta$=%.0f° (mV)" % projection["theta_deg"],
                    ylabel="lobe peak imbalance")
        style_legend(axes[0], loc="upper left", fontsize=5, frameon=False)

        grid_c0 = np.linspace(c0.min(), c0.max(), 50)
        axes[1].plot(c0, mirror, "s", ms=3.2, color="C3", label="mirror residual")
        axes[1].plot(grid_c0, record["mirror_vs_c0"]["slope"] * grid_c0 + record["mirror_vs_c0"]["intercept"],
                     "-", lw=0.9, color="0.4", label="r=%.2f" % record["mirror_vs_c0"]["r"])
        format_axis(axes[1], xlabel="|c$_0$| (mV)", ylabel=r"mean $|R(\varphi)-R(\varphi+180°)|/\bar R$")
        style_legend(axes[1], loc="upper left", fontsize=5, frameon=False)
        save_figure(figure, FIGURES / f"{args.runlist}_symmetry.png")

        figure, axes = plt.subplots(1, 2, figsize=(6.4, 2.8), layout="constrained")
        for axis, key, title in ((axes[0], "c0_mV", "|c$_0$| (mV)"), (axes[1], "lobe_imbalance", "lobe imbalance")):
            data = np.array([[grid.get((round(x, 4), round(y, 4)), {}).get(key, np.nan) for x in xs] for y in ys])
            image = axis.imshow(data, origin="lower", aspect="auto", cmap="viridis",
                                extent=[min(xs) - 0.04, max(xs) + 0.04, min(ys) - 0.04, max(ys) + 0.04])
            for y in ys:
                for x in xs:
                    item = grid.get((round(x, 4), round(y, 4)))
                    if item:
                        text = "%.0f" % item[key] if key == "c0_mV" else "%+.2f" % item[key]
                        axis.text(x, y, text, ha="center", va="center", fontsize=4.2, color="w")
            figure.colorbar(image, ax=axis, label=title)
            format_axis(axis, xlabel="X bias (V)", ylabel="Y bias (V)")
        save_figure(figure, FIGURES / f"{args.runlist}_maps.png")

    print(json.dumps({key: record[key] for key in ("points", "c0_mV", "imbalance", "mirror_rel",
                                                   "imbalance_vs_projection", "clipped_points")},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
