"""汇总四类扫描的关键趋势，输出一张 2x2 总览图。"""
from __future__ import annotations

from lab_workflows.common import data_root
import json
import math
from pathlib import Path

import numpy as np
import yaml

ROOT = (data_root() / "rf_phase_response")
OUT = Path(__file__).resolve().parent.parent / "figures"


def load(tag):
    for cfg in ROOT.glob("*/experiment_config.yaml"):
        try:
            p = yaml.safe_load(cfg.read_text(encoding="utf-8"))["parameters"]
        except Exception:
            continue
        if str(p.get("RUN_TAG")) != tag:
            continue
        s = cfg.parent / "results/phase_response_summary.json"
        if not s.is_file():
            continue
        d = json.loads(s.read_text(encoding="utf-8"))
        if "components" not in d:
            continue
        c = d["components"]
        return dict(tag=tag, width=float(p["Z_PULSE_WIDTH_US"]), sf=float(p["Z_TAIL_S_F"]),
                    x=float(p["X_BIAS_V"]) * 1e3, y=float(p["Y_BIAS_V"]) * 1e3,
                    z=float(p.get("MAIN_FIELD_CURRENT_MA", 0.0)),
                    c0=c["c0"]["abs_v"] * 1e3, cp=c["c_plus1"]["abs_v"] * 1e3,
                    cm=c["c_minus1"]["abs_v"] * 1e3, best=d["best_phase_deg"])
    raise KeyError(tag)


def main():
    base = load("rf_phase_response")
    width_runs = [load(t) for t in ("w4", "w6", "w10", "w12")] + [base]
    width_runs.sort(key=lambda r: r["width"])
    sf_runs = [load(t) for t in ("sf34", "sf44", "sf64", "sf74")] + [base]
    sf_runs.sort(key=lambda r: r["sf"])
    tail_off = load("tailoff")
    x_runs = sorted([r for t in ("xm005", "xm003", "xm001", "x001", "x003", "x005") for r in [load(t)]],
                    key=lambda r: r["x"])
    y_runs = sorted([r for t in ("ym005", "ym003", "ym001", "y001", "y003", "y005") for r in [load(t)]],
                    key=lambda r: r["y"])
    z_runs = [load(t) for t in ("zm020", "zm018", "zm016", "zm014", "zm012", "zm010", "zm008", "zm006",
                                "zm004", "zm002", "zp000", "zp002", "zp004", "zp006", "zp008")] + \
             [load(t) for t in ("zp010e", "zp012e", "zp014e", "zp016e", "zp018e", "zp020e")]
    z_runs.sort(key=lambda r: r["z"])

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from lab_workflows.plotting import format_axis, plot_style_context, save_figure, style_legend

    with plot_style_context("paper"):
        figure, axes = plt.subplots(2, 2, figsize=(7.2, 4.8))
        (a, b), (c, d) = axes

        a.plot([r["width"] for r in width_runs], [r["cm"] for r in width_runs], "^-", ms=3, lw=0.8,
               label=r"$|c_{-1}|$")
        a.plot([r["width"] for r in width_runs], [r["cp"] for r in width_runs], "s-", ms=3, lw=0.8,
               label=r"$|c_{+1}|$")
        a.plot([r["width"] for r in width_runs], [r["c0"] for r in width_runs], "o-", ms=3, lw=0.8,
               label=r"$|c_0|$")
        format_axis(a, xlabel=r"pulse width ($\mu$s)", ylabel="component (mV)")
        style_legend(a, loc="upper left", fontsize=5, ncol=3, frameon=False)
        a.set_title("(a) pulse width", fontsize=6.5)

        b.plot([r["sf"] for r in sf_runs], [r["c0"] for r in sf_runs], "o-", ms=3, lw=0.8,
               label=r"$|c_0|$")
        b.axhline(tail_off["c0"], color="0.5", ls="--", lw=0.8, label="tail off")
        format_axis(b, xlabel=r"tail fraction $s_f$", ylabel=r"$|c_0|$ (mV)")
        style_legend(b, loc="lower right", fontsize=5, frameon=False)
        b.set_title(r"(b) tail fraction", fontsize=6.5)

        c.plot([r["x"] for r in x_runs], [r["c0"] for r in x_runs], "o-", ms=3, lw=0.8, label="X bias")
        c.plot([r["y"] for r in y_runs], [r["c0"] for r in y_runs], "s-", ms=3, lw=0.8, label="Y bias")
        format_axis(c, xlabel="X / Y bias (mV)", ylabel=r"$|c_0|$ (mV)")
        style_legend(c, loc="upper right", fontsize=5, frameon=False)
        c.set_title("(c) transverse bias", fontsize=6.5)

        d.plot([r["z"] for r in z_runs], [r["c0"] for r in z_runs], "o-", ms=3, lw=0.8, label=r"$|c_0|$")
        d.plot([r["z"] for r in z_runs], [r["cp"] for r in z_runs], "s-", ms=3, lw=0.8, label=r"$|c_{+1}|$")
        d.plot([r["z"] for r in z_runs], [r["cm"] for r in z_runs], "^-", ms=3, lw=0.8, label=r"$|c_{-1}|$")
        d.axvline(0.0, color="0.7", ls=":", lw=0.6)
        format_axis(d, xlabel="main field (mA)", ylabel="component (mV)")
        style_legend(d, loc="upper left", fontsize=5, ncol=3, frameon=False)
        d.set_title("(d) main field, fine scan", fontsize=6.5)

        figure.tight_layout()
        save_figure(figure, OUT / "summary_overview.png")
    print(OUT / "summary_overview.png")


if __name__ == "__main__":
    main()
