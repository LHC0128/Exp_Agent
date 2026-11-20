"""把 41 点（10 Hz–1 MHz）与 20 点（200–600 kHz）两次扫频合到同一张图。只读运行目录数据。"""
from lab_workflows.common import data_root
import json
import pathlib

import numpy as np

from lab_workflows.plotting import (COLOR_GRAY, COLOR_OPTIMAL, COLOR_TRAD, format_axis, new_figure,
                                    plot_style_context, save_figure, style_legend)

BASE = (data_root() / "coil_drive_characterization")
OUT = pathlib.Path(__file__).resolve().parent / "figures"
RUNS = [("41 pts, 10 Hz-1 MHz (200 mA)", BASE / "1114_170336_coil_drive" / "results" / "transfer.json", COLOR_OPTIMAL, "o"),
        ("20 pts, 200-600 kHz (200 mA)", BASE / "1114_171514_coil_drive" / "results" / "transfer.json", COLOR_TRAD, "s")]


def load(path):
    rows = json.loads(path.read_text(encoding="utf-8"))["transfer"]
    f = np.array([row["frequency_hz"] for row in rows])
    mag = np.array([row["admittance_a_per_v"] for row in rows]) * 1e3
    phase = np.unwrap(np.radians([row["phase_deg"] for row in rows])) * 180 / np.pi
    return f, mag, phase


OUT.mkdir(parents=True, exist_ok=True)
with plot_style_context("paper"):
    figure, axis = new_figure()
    for label, path, color, marker in RUNS:
        f, mag, _ = load(path)
        axis.semilogx(f, mag, marker + "-", ms=3.0, lw=1.0, color=color, label=label)
    axis.axvline(310e3, color=COLOR_GRAY, lw=0.8, ls=":", label="Reference (310 kHz)")
    format_axis(axis, xlabel="Frequency (Hz)", ylabel=r"$|I_\mathrm{pk}/V_\mathrm{drive,pk}|$ (mA/V)")
    style_legend(axis, loc="lower left", fontsize=6.5)
    save_figure(figure, OUT / "coil_drive_compare_magnitude.png")

    figure, axis = new_figure()
    for label, path, color, marker in RUNS:
        f, _, phase = load(path)
        axis.semilogx(f, phase, marker + "-", ms=3.0, lw=1.0, color=color, label=label)
    axis.axvline(310e3, color=COLOR_GRAY, lw=0.8, ls=":", label="Reference (310 kHz)")
    format_axis(axis, xlabel="Frequency (Hz)", ylabel="Phase vs sync edge (deg)")
    style_legend(axis, loc="lower left", fontsize=6.5)
    save_figure(figure, OUT / "coil_drive_compare_phase.png")

print("written:", *(p.name for p in sorted(OUT.glob("*.png"))))
