"""为优化各阶段生成绝对相量图。"""
from __future__ import annotations

from lab_workflows.common import data_root
import csv
import json
import math
import re
from pathlib import Path

import numpy as np
import yaml
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from lab_workflows.plotting import format_axis, plot_style_context, save_figure, style_legend

ROOT = (data_root() / "rf_phase_response")
OUT = Path(__file__).resolve().parent.parent / "figures"
PHI = np.radians(np.arange(0.0, 360.0, 0.5))


_INDEX = None


def _build_index():
    index = {}
    for cfg in ROOT.glob("*/experiment_config.yaml"):
        try:
            tag = str(yaml.safe_load(cfg.read_text(encoding="utf-8"))["parameters"].get("RUN_TAG"))
        except Exception:
            continue
        index.setdefault(tag, []).append(cfg.parent)
    return index


def load(tag):
    global _INDEX
    if _INDEX is None:
        _INDEX = _build_index()
    for run_dir in sorted(_INDEX.get(tag, [])):
        summary = run_dir / "results/phase_response_summary.json"
        if not summary.exists():
            continue
        d = json.loads(summary.read_text(encoding="utf-8"))
        if "components" not in d:
            continue
        c = d["components"]
        z = lambda k: complex(c[k]["re_v"], c[k]["im_v"])
        return dict(tag=tag, dir=run_dir, c0=z("c0"), cp=z("c_plus1"), cm=z("c_minus1"))
    raise KeyError(tag)


def phasor_figure(items, labels, name, title, xlabel, reverse=False, cmap="viridis"):
    values = []
    for item in items:
        z = (item["c0"] + item["cp"] * np.exp(1j * PHI) + item["cm"] * np.exp(-1j * PHI)) * 1e3
        values.append(z)
    bound = max(max(abs(z.real).max(), abs(z.imag).max()) for z in values) * 1.15
    colors = plt.get_cmap(cmap)(np.linspace(0.08, 0.92, len(items)))
    with plot_style_context("paper"):
        fig, ax = plt.subplots(figsize=(3.4, 3.4))
        for item, z, color, label in zip(items, values, colors, labels):
            ax.plot(z.real, z.imag, "-", lw=0.8, color=color, label=label)
            ax.plot(item["c0"].real * 1e3, item["c0"].imag * 1e3, "o", ms=1.5, color=color)
        ax.axhline(0.0, color="0.85", lw=0.4)
        ax.axvline(0.0, color="0.85", lw=0.4)
        ax.plot(0, 0, "+", ms=4, color="k")
        ax.set_xlim(-bound, bound)
        ax.set_ylim(-bound, bound)
        ax.set_aspect("equal", adjustable="box")
        format_axis(ax, xlabel=r"Re $Z$ (mV)", ylabel=r"Im $Z$ (mV)")
        ax.set_title(title, fontsize=6.5)
        style_legend(ax, loc="upper left", fontsize=4.4, ncol=2, frameon=False)
        save_figure(fig, OUT / name)


def main():
    OUT.mkdir(parents=True, exist_ok=True)

    coarse = [load(t) for t in ("zm02", "zm01", "z000", "z01", "z02")]
    phasor_figure(coarse, ["-0.2", "-0.1", "0", "+0.1", "+0.2"],
                  "phasors_z_coarse.png", "Z coarse scan", "field (mA)")

    widths = [("w3_5", "5"), ("w3_6", "6"), ("w3_7", "7"), ("w3_75", "7.5"),
              ("w3_775", "7.75"), ("w3_803", "8.03"), ("w3_85", "8.5"), ("w3_9", "9"),
              ("w3_10", "10"), ("w3_11", "11"), ("w3_12", "12")]
    items = [load(t) for t, _ in widths]
    phasor_figure(items, [l for _, l in widths], "phasors_width.png",
                  "pulse width (µs)", "width (µs)")

    ridge = [("ridge2_1", "0.55/35"), ("ridge2_2", "0.575/36.7"), ("ridge2_3", "0.60/38.3"),
             ("ridge2_4", "0.625/40"), ("ridge2_5", "0.65/41.7"), ("ridge2_6", "0.675/43.3"),
             ("ridge2_7", "0.70/45")]
    items = [load(t) for t, _ in ridge]
    phasor_figure(items, [l for _, l in ridge], "phasors_ridge.png",
                  r"ridge ($s_f/\tau_e$)", "ridge point")

    reloc = [("z3_m074", "-0.074"), ("z3_m072", "-0.072"), ("z3_m070", "-0.070"),
             ("z3_m068", "-0.068"), ("z3_m066", "-0.066"), ("z3_m064", "-0.064"),
             ("z3_m062", "-0.062")]
    items = [load(t) for t, _ in reloc]
    phasor_figure(items, [l for _, l in reloc], "phasors_z_relocate.png",
                  "Z relocate (0.60/38.3)", "field (mA)")

    pe = [("e_ref", "best"), ("e_w75", "w 7.5"), ("e_w85", "w 8.5"),
          ("e_sf575", "sf .575"), ("e_sf625", "sf .625"),
          ("e_te358", "te 35.8"), ("e_te408", "te 40.8")]
    items = [load(t) for t, _ in pe]
    phasor_figure(items, [l for _, l in pe], "phasors_phase_e.png",
                  "phase E perturbations", "perturbation")

    sf = [("e2_sf50", "0.50"), ("e2_sf55", "0.55"), ("e_sf575", "0.575"),
          ("e_ref", "0.60"), ("e_sf625", "0.625")]
    items = [load(t) for t, _ in sf]
    phasor_figure(items, [l for _, l in sf], "phasors_sf.png",
                  r"$s_f$ descent ($\tau_e$=38.3)", r"$s_f$")

    c0xy = [("f2_xm", "X -200"), ("f2_base2", "base"), ("f2_xp", "X +200"),
            ("f2_ym", "Y -200"), ("f2_yp", "Y +200")]
    items = [load(t) for t, _ in c0xy]
    phasor_figure(items, [l for _, l in c0xy], "phasors_c0xy.png",
                  "X/Y bias at best point", "bias")

    # 最终三次重复：从 raw 各自分解
    d = ROOT / "1119_204116_final2"
    rows = []
    with (d / "raw/phase_response.csv").open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            rows.append((int(float(row["repeat"])), float(row["phase_deg"]),
                         float(row["R_v"]) * np.exp(1j * math.radians(float(row["theta_deg"])))))
    items = []
    for repeat in sorted({r[0] for r in rows}):
        subset = [(ph, val) for rep, ph, val in rows if rep == repeat]
        phases = np.array([ph for ph, _ in subset])
        vals = np.array([val for _, val in subset])
        mat = np.column_stack([np.ones_like(phases), np.exp(1j * np.radians(phases)),
                               np.exp(-1j * np.radians(phases))])
        coef = np.linalg.lstsq(mat, vals, rcond=None)[0]
        items.append(dict(tag=str(repeat), c0=coef[0], cp=coef[1], cm=coef[2]))
    phasor_figure(items, [f"repeat {i + 1}" for i in range(len(items))],
                  "phasors_final_repeat.png", "final repeats", "repeat")


if __name__ == "__main__":
    main()
