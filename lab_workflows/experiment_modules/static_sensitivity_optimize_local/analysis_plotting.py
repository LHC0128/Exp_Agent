"""局部优化原科学汇总图；所有绘图标注为英文。"""
import numpy as np
from ...plotting import new_figure, plot_style_context, save_figure


def plot_summary(run_dir, inputs, decision):
    sequences = inputs["optimize_sequence"]
    # 相同参数的重复扫描保留点记录，面板按参数去重而不改排名顺序。
    names = list(dict.fromkeys(item["parameter"] for item in sequences))
    if not names:
        raise ValueError("没有可绘制的扫描维度")
    with plot_style_context("paper"):
        figure, axes = new_figure(nrows=len(names), ncols=1, kind="wide",
            height_mm=max(70, 55 * len(names)), squeeze=False)
        for axis, name in zip(axes.flat, names):
            subset = [row for row in inputs["rows"] if row["parameter"] == name]
            values = np.asarray([row["value"] for row in subset], dtype=float)
            sensitivity = np.asarray([np.nan if row["sens_flat_fT_per_sqrt_Hz"] is None else row["sens_flat_fT_per_sqrt_Hz"] for row in subset])
            valid = np.asarray([bool(row["fit_is_valid"]) for row in subset]) & np.isfinite(sensitivity)
            axis.scatter(values[valid], sensitivity[valid], color="tab:green", label="Valid")
            if (~valid).any():
                axis.scatter(values[~valid], sensitivity[~valid], color="tab:red", marker="x", label="Rejected")
            if valid.sum() > 1:
                order = np.argsort(values[valid])
                axis.plot(values[valid][order], sensitivity[valid][order], "g--", alpha=.6)
            axis.axvline(inputs["baseline_params"][name], color="gray", ls=":", alpha=.6)
            if decision["baseline_median"] is not None:
                axis.axhline(decision["baseline_median"], color="gray", ls="--", alpha=.5, label="Baseline median")
            axis.set(xlabel=name, ylabel="Sensitivity (fT/sqrtHz)", title=f"Sensitivity vs {name}")
            axis.legend()
        save_figure(figure, run_dir / "results/optimization_local_summary.png")
