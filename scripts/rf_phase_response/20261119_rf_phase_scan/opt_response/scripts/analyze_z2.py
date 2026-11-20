"""Z 细扫（0.002 mA）分析：斜率/|c0| 峰位拟合与重复性。"""
from __future__ import annotations

from lab_workflows.common import data_root
import csv
import json
import math
import re
from pathlib import Path

import numpy as np
import yaml

ROOT = (data_root() / "rf_phase_response")
OUT = Path(__file__).resolve().parent.parent / "figures"
PHI = np.radians(np.arange(0.0, 360.0, 0.5))
TAG = re.compile(r"^z2_m(\d{3})$")


def decompose(phases_deg, values):
    matrix = np.column_stack([np.ones_like(phases_deg),
                             np.exp(1j * np.radians(phases_deg)),
                             np.exp(-1j * np.radians(phases_deg))])
    coef, *_ = np.linalg.lstsq(matrix, values, rcond=None)
    residual = values - matrix @ coef
    return coef, float(np.sqrt(np.mean(np.abs(residual) ** 2)))


def load(run_dir):
    config = yaml.safe_load((run_dir / "experiment_config.yaml").read_text(encoding="utf-8"))
    summary = json.loads((run_dir / "results/phase_response_summary.json").read_text(encoding="utf-8"))
    if "components" not in summary:
        raise ValueError("no components")
    comp = summary["components"]
    z = lambda key: complex(comp[key]["re_v"], comp[key]["im_v"])
    return dict(dir=run_dir, tag=str(config["parameters"].get("RUN_TAG")),
                field=float(config["parameters"].get("MAIN_FIELD_CURRENT_MA", 0.0)),
                c0=z("c0"), cp=z("c_plus1"), cm=z("c_minus1"),
                slope=float(summary["sensitivity_max_v_per_deg"]))


def collect():
    runs = []
    for path in sorted(ROOT.glob("*/experiment_config.yaml")):
        try:
            tag = str(yaml.safe_load(path.read_text(encoding="utf-8"))["parameters"].get("RUN_TAG", ""))
        except Exception:
            continue
        if not TAG.match(tag):
            continue
        if not (path.parent / "results/phase_response_summary.json").is_file():
            continue
        try:
            runs.append(load(path.parent))
        except (KeyError, ValueError):
            continue
    return sorted(runs, key=lambda item: item["field"])


def noise_sigma():
    """从 p0n3 的三次重复各自分解，给出斜率与 |c0| 的样本标准差。"""
    for path in sorted(ROOT.glob("*/experiment_config.yaml")):
        try:
            params = yaml.safe_load(path.read_text(encoding="utf-8"))["parameters"]
        except Exception:
            continue
        if params.get("RUN_TAG") != "p0n3":
            continue
        rows = []
        with (path.parent / "raw/phase_response.csv").open(encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                rows.append((int(float(row["repeat"])), float(row["phase_deg"]),
                             float(row["R_v"]) * np.exp(1j * np.radians(float(row["theta_deg"])))))
        slopes, centers = [], []
        for repeat in sorted({row[0] for row in rows}):
            subset = [(ph, val) for rep, ph, val in rows if rep == repeat]
            phases = np.array([ph for ph, _ in subset])
            values = np.array([val for _, val in subset])
            coef, _ = decompose(phases, values)
            slopes.append((abs(coef[1]) + abs(coef[2])) * 1e3 * math.pi / 180)
            centers.append(abs(coef[0]) * 1e3)
        return (float(np.std(slopes, ddof=1)) if len(slopes) > 1 else 0.0,
                float(np.std(centers, ddof=1)) if len(centers) > 1 else 0.0,
                slopes, centers)
    return 0.0, 0.0, [], []


def main():
    runs = collect()
    if len(runs) < 12:
        raise SystemExit(f"Z 细扫点不足：{len(runs)}")
    fields = np.array([item["field"] for item in runs])
    slope = np.array([item["slope"] for item in runs])
    c0 = np.array([abs(item["c0"]) * 1e3 for item in runs])
    cp = np.array([abs(item["cp"]) * 1e3 for item in runs])
    cm = np.array([abs(item["cm"]) * 1e3 for item in runs])

    peak_index = int(np.argmax(slope))
    centre = fields[peak_index]
    # 峰顶附近 7 点二次拟合
    lo, hi = max(0, peak_index - 3), min(len(fields), peak_index + 4)
    quad = np.polyfit(fields[lo:hi], slope[lo:hi], 2)
    if quad[0] < 0:
        centre = float(-quad[1] / (2 * quad[0]))
    c0_index = int(np.argmin(c0))
    c0_centre = float(fields[c0_index])

    from scipy.optimize import curve_fit

    def lorentzian(x, base, amp, x0, width):
        return base + amp / (1 + ((x - x0) / width) ** 2)

    try:
        popt, _ = curve_fit(lorentzian, fields, slope, p0=[slope.min(), slope.max() - slope.min(),
                                                            centre, 0.01], maxfev=20000)
        lorentz = dict(base=popt[0], amp=popt[1], centre=float(popt[2]), half_width=float(abs(popt[3])))
    except Exception as exc:
        lorentz = {"error": str(exc)}

    sigma_slope, sigma_c0, slopes, centers = noise_sigma()

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from lab_workflows.plotting import format_axis, plot_style_context, save_figure, style_legend

    OUT.mkdir(parents=True, exist_ok=True)
    with plot_style_context("paper"):
        figure, (top, bottom) = plt.subplots(2, 1, figsize=(3.8, 4.2), sharex=True,
                                            gridspec_kw={"height_ratios": [2, 1]})
        top.plot(fields, slope, "o-", ms=3, lw=0.8, color="C0", label="slope")
        top.axvline(centre, color="C3", ls="--", lw=0.8, label=f"peak {centre:.4f}")
        format_axis(top, xlabel="", ylabel="slope (mV/deg)")
        style_legend(top, loc="upper left", fontsize=5, frameon=False)
        bottom.plot(fields, c0, "s-", ms=2.6, lw=0.8, color="C1", label=r"$|c_0|$")
        bottom.axvline(c0_centre, color="C4", ls=":", lw=0.8)
        format_axis(bottom, xlabel="main field (mA)", ylabel=r"$|c_0|$ (mV)")
        style_legend(bottom, loc="upper left", fontsize=5, frameon=False)
        save_figure(figure, OUT / "z2_fine.png")

    record = dict(peak_field_ma=float(centre), peak_slope=float(np.max(slope)),
                  peak_by_grid=float(fields[peak_index]), c0_min_field_ma=c0_centre,
                  c0_min_mv=float(np.min(c0)), lorentzian=lorentz,
                  sigma_slope_mv_per_deg=sigma_slope, sigma_c0_mv=sigma_c0,
                  repeats=dict(slope=slopes, c0=centers))
    (OUT / "z2_peak.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    with (OUT / "z2_fine.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["field_mA", "slope", "c0_mV", "cplus_mV", "cminus_mV", "tag"])
        for item, s, z0, p, m in zip(runs, slope, c0, cp, cm):
            writer.writerow([f"{item['field']:.4f}", "%.6g" % s, "%.6g" % z0, "%.6g" % p,
                             "%.6g" % m, item["tag"]])
    print(json.dumps(record, ensure_ascii=False, indent=2))
    print(OUT / "z2_fine.png")


if __name__ == "__main__":
    main()
