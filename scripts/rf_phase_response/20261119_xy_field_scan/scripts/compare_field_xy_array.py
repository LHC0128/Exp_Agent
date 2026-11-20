"""X/Y 偏置扫描合成一个 2×6 相量阵列：绝对幅值、共享坐标、标出零点。"""
from __future__ import annotations

from lab_workflows.common import data_root
import json
import math
import re
from pathlib import Path

import numpy as np
import yaml

ROOT = (data_root() / "rf_phase_response")
OUT = Path(__file__).resolve().parent.parent / "figures"
PHI = np.radians(np.arange(0.0, 360.0, 0.5))
TAG = re.compile(r"^[xy](m\d{3}|\d{3})$")


def load(run_dir):
    summary = json.loads((run_dir / "results/phase_response_summary.json").read_text(encoding="utf-8"))
    if "components" not in summary:
        raise ValueError
    config = yaml.safe_load((run_dir / "experiment_config.yaml").read_text(encoding="utf-8"))
    p = config["parameters"]
    comp = summary["components"]
    z = lambda key: complex(comp[key]["re_v"], comp[key]["im_v"])
    return dict(dir=run_dir, tag=str(p.get("RUN_TAG")), x=float(p["X_BIAS_V"]) * 1e3,
                y=float(p["Y_BIAS_V"]) * 1e3, c0=z("c0"), cp=z("c_plus1"), cm=z("c_minus1"))


def main():
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
        except (KeyError, ValueError, TypeError):
            continue
    xs = sorted((r for r in runs if r["tag"].startswith("x")), key=lambda r: r["x"])
    ys = sorted((r for r in runs if r["tag"].startswith("y")), key=lambda r: r["y"])
    if len(xs) < 5 or len(ys) < 5:
        raise SystemExit(f"运行不足：X={len(xs)} Y={len(ys)}")

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from lab_workflows.plotting import plot_style_context, save_figure

    rows = [("X bias (mV)", xs, "x"), ("Y bias (mV)", ys, "y")]
    curves = []
    for _, items, key in rows:
        for item in items:
            z = (item["c0"] + item["cp"] * np.exp(1j * PHI) + item["cm"] * np.exp(-1j * PHI)) * 1e3
            curves.append(z)
    limit = max(max(abs(z.real).max(), abs(z.imag).max()) for z in curves) * 1.12

    with plot_style_context("paper"):
        figure, axes = plt.subplots(2, 6, figsize=(7.2, 2.9))
        for row, (label, items, key) in enumerate(rows):
            for col, item in enumerate(items):
                ax = axes[row, col]
                z = (item["c0"] + item["cp"] * np.exp(1j * PHI) + item["cm"] * np.exp(-1j * PHI)) * 1e3
                ax.plot(z.real, z.imag, "-", lw=0.8, color="C0")
                ax.plot(item["c0"].real * 1e3, item["c0"].imag * 1e3, "o", ms=1.6, color="C3")
                ax.axhline(0.0, color="0.82", lw=0.4)
                ax.axvline(0.0, color="0.82", lw=0.4)
                ax.plot(0, 0, "+", ms=3, color="k")
                ax.set_xlim(-limit, limit)
                ax.set_ylim(-limit, limit)
                ax.set_aspect("equal", adjustable="box")
                ax.set_xticks([])
                ax.set_yticks([])
                ax.text(0.05, 0.05, f"{item[key]:+.0f}", transform=ax.transAxes, fontsize=5.5)
                if row == 0:
                    ax.set_title("", fontsize=5)
            axes[row, 0].set_ylabel(label, fontsize=6.5)
        figure.text(0.01, 0.005, f"shared axes, ±{limit:.0f} mV; red dot = $c_0$, black cross = origin",
                    fontsize=5, color="0.35")
        save_figure(figure, OUT / "field_xy_array.png")
    print(OUT / "field_xy_array.png")


if __name__ == "__main__":
    main()
