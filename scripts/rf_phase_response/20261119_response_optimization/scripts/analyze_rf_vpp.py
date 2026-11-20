"""射频幅度扫描的离线分析：复用运行目录原始数据，输出 CSV/JSON 与四联图。

用法：python analyze_rf_vpp.py [运行目录 …]；不给参数时自动查找 Y_RF_VPP 在
0.1…0.5 V 之间的 rf_phase_response 运行。
"""
from __future__ import annotations

from lab_workflows.common import data_root
import csv
import glob
import json
import math
import re
import sys
from pathlib import Path

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = (data_root() / "rf_phase_response")
OUT = Path(__file__).resolve().parent.parent  # opt_response/
WANTED = (0.05, 0.1, 0.2, 0.3, 0.4, 0.5)
BASELINE = ROOT / "1120_102315_rf_phase_response"


def vpp_of(run_dir: Path) -> float | None:
    text = (run_dir / "experiment_config.yaml").read_text(encoding="utf-8")
    match = re.search(r"^  Y_RF_VPP: *([0-9.eE+-]+) *$", text, re.MULTILINE)
    return float(match.group(1)) if match else None


def discover() -> list[Path]:
    found = [BASELINE] if (BASELINE / "raw/phase_response.csv").exists() else []
    for name in sorted(glob.glob(str(ROOT / "1120_*"))):
        path = Path(name)
        if path == BASELINE or not (path / "raw/phase_response.csv").exists():
            continue
        value = vpp_of(path)
        if value is not None and any(abs(value - want) < 1e-9 for want in WANTED):
            found.append(path)
    return found


def load(run_dir: Path):
    phases, zs = [], []
    with (run_dir / "raw/phase_response.csv").open(encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            phases.append(float(row["phase_deg"]))
            zs.append(complex(float(row["x_v"]), float(row["y_v"])))
    order = np.argsort(phases)
    return np.asarray(phases, float)[order], np.asarray(zs, complex)[order]


def fit(phases, zs, n_harm):
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
    resid = zs - basis @ coef
    return dict(zip(names, coef)), float(np.sqrt(np.mean(np.abs(resid) ** 2)))


def main() -> None:
    run_dirs = [Path(item) for item in sys.argv[1:]] or discover()
    series = []
    for run_dir in run_dirs:
        vpp = vpp_of(run_dir)
        if vpp is None:
            continue
        phases, zs = load(run_dir)
        c3, rms3 = fit(phases, zs, 1)
        c5, rms5 = fit(phases, zs, 2)
        c6, rms6 = fit(phases, zs, 5)
        rad = np.radians(phases)
        slope = np.abs(zs[2:] - zs[:-2]) / (rad[2:] - rad[:-2])
        s_model = (abs(c3["c_plus1"]) + abs(c3["c_minus1"])) * math.pi / 180.0 * 1e3
        series.append({
            "vpp_v": vpp,
            "run_dir": run_dir.name,
            "S_model_mV_deg": s_model,
            "S_raw_mV_deg": float(slope.max()) * math.pi / 180.0 * 1e3,
            "c0_mV": abs(c3["c0"]) * 1e3,
            "c1_mV": 0.5 * (abs(c6["c_plus1"]) + abs(c6["c_minus1"])) * 1e3,
            "c3_mV": 0.5 * (abs(c6["c_plus3"]) + abs(c6["c_minus3"])) * 1e3,
            "c5_mV": 0.5 * (abs(c6["c_plus5"]) + abs(c6["c_minus5"])) * 1e3,
            "R_peak_V": float(np.max(np.abs(zs))),
            "R_min_V": float(np.min(np.abs(zs))),
            "rms3_mV": rms3 * 1e3,
            "rms5_mV": rms5 * 1e3,
            "rms_full_mV": rms6 * 1e3,
            "phases": phases,
            "R": np.abs(zs),
        })
    series.sort(key=lambda item: item["vpp_v"])

    OUT.mkdir(parents=True, exist_ok=True)
    records = [{k: v for k, v in item.items() if k not in ("phases", "R")} for item in series]
    csv_path = OUT / "tables/rf_amplitude_scan.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0].keys()))
        writer.writeheader()
        writer.writerows(records)
    (OUT / "tables/rf_amplitude_scan.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"{'Vpp':>5} {'S_model':>8} {'S_raw':>7} {'c0':>6} {'c1':>6} {'c3':>6} "
          f"{'c5':>5} {'Rpk':>6} {'Rmin':>6} {'rmsH':>6}")
    for row in series:
        print(f"{row['vpp_v']*1e3:5.0f} {row['S_model_mV_deg']:8.2f} {row['S_raw_mV_deg']:7.2f} "
              f"{row['c0_mV']:6.1f} {row['c1_mV']:6.1f} {row['c3_mV']:6.1f} {row['c5_mV']:5.1f} "
              f"{row['R_peak_V']:6.3f} {row['R_min_V']:6.3f} {row['rms_full_mV']:6.1f}")

    x = np.array([row["vpp_v"] for row in series]) * 1e3
    fig, axes = plt.subplots(2, 2, figsize=(9.6, 7.0), dpi=150)
    ax = axes[0, 0]
    ax.plot(x, [row["S_model_mV_deg"] for row in series], "o-", color="#1f6fb4",
            label="3-component fit")
    ax.plot(x, [row["S_raw_mV_deg"] for row in series], "s--", color="#c1553b",
            label="raw max $|dZ/d\\varphi|$")
    ax.set_xlabel("$V_{RF}$ (mVpp)")
    ax.set_ylabel("slope (mV/deg)")
    ax.legend(frameon=False, fontsize=9)
    ax.grid(alpha=0.3)
    ax.set_title("(a) response slope", loc="left", fontsize=10)

    ax = axes[0, 1]
    for item in series:
        if item["vpp_v"] * 1e3 in (50, 100, 200, 500):
            ax.plot(item["phases"], item["R"], ".-", ms=3, lw=0.9,
                    label=f"{item['vpp_v']*1e3:.0f} mVpp")
    ax.set_xlabel("RF burst phase $\\varphi$ (deg)")
    ax.set_ylabel("$R$ (V)")
    ax.legend(frameon=False, fontsize=8, ncol=2)
    ax.grid(alpha=0.3)
    ax.set_title("(b) waveform vs phase", loc="left", fontsize=10)

    ax = axes[1, 0]
    ax.plot(x, [row["c1_mV"] for row in series], "o-", color="#1f6fb4", label="$|c_1|$")
    ax.plot(x, [row["c3_mV"] for row in series], "^-", color="#c1553b", label="$|c_3|$")
    ax.plot(x, [row["c5_mV"] for row in series], "v-", color="#7a5ea8", label="$|c_5|$")
    ax.plot(x, [row["c0_mV"] for row in series], "s--", color="#555555", label="$|c_0|$")
    ax.set_xlabel("$V_{RF}$ (mVpp)")
    ax.set_ylabel("component (mV)")
    ax.legend(frameon=False, fontsize=9)
    ax.grid(alpha=0.3)
    ax.set_title("(c) harmonic content", loc="left", fontsize=10)

    ax = axes[1, 1]
    ax.plot(x, [row["rms3_mV"] for row in series], "o-", color="#1f6fb4", label="3-comp RMS")
    ax.plot(x, [row["rms_full_mV"] for row in series], "s--", color="#3f8f6b", label="11-term RMS")
    ax.set_xlabel("$V_{RF}$ (mVpp)")
    ax.set_ylabel("residual (mV)")
    ax.grid(alpha=0.3)
    ax2 = ax.twinx()
    ax2.plot(x, [row["R_peak_V"] for row in series], "^:", color="#c1553b", label="$R_{peak}$")
    ax2.set_ylabel("$R_{peak}$ (V)", color="#c1553b")
    ax2.tick_params(axis="y", colors="#c1553b")
    lines = ax.get_legend_handles_labels()[0] + ax2.get_legend_handles_labels()[0]
    labels = ax.get_legend_handles_labels()[1] + ax2.get_legend_handles_labels()[1]
    ax.legend(lines, labels, frameon=False, fontsize=8, loc="upper left")
    ax.set_title("(d) fit residual and $R_{peak}$", loc="left", fontsize=10)

    fig.tight_layout()
    png = OUT / "figures/rf_amplitude_scan.png"
    png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(png)
    fig.savefig(png.with_suffix(".pdf"))
    print(f"\nwrote {csv_path}\nwrote {png}")


if __name__ == "__main__":
    main()
