"""离线分析：按宽度汇总逐发 θ，线性内插 θ = 目标角 的宽度，并反推等效线圈常数。"""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import numpy as np

from ...experiment_runtime import check_cancelled
from . import run_format
from .stages import ANALYSIS_KEYS

GYROMAGNETIC_RATIO_HZ_PER_NT = 7.0048


class AnalysisQualityError(ValueError):
    """输入或拟合不满足分析条件；诊断仍保存。"""


def output_path(run_dir, name):
    root = Path(run_dir).resolve()
    path = (root / name).resolve()
    if path != root and root not in path.parents:
        raise ValueError("输入或产物路径越出运行目录")
    return path


def load_config(run_dir):
    config = run_format.read_config(output_path(run_dir, "experiment_config.yaml"))
    if reason := run_format.analysis_block_reason(config):
        raise ValueError(reason)
    return config, run_format.parameters(config)


def _read_scan(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def resolve_options(base, options):
    """本实验没有独立分析覆盖值；出现未知键时明确拒绝。"""
    if unknown := options.keys() - set(ANALYSIS_KEYS):
        raise ValueError("不支持的分析覆盖值：" + "、".join(sorted(unknown)))
    return base


def signature(path):
    if not path.is_file():
        return None
    return [path.stat().st_size, hashlib.sha256(path.read_bytes()).hexdigest()]


def fingerprint(run_dir):
    """分步分析用的输入指纹：配置、扫描表与全部原始帧。"""
    root = Path(run_dir)
    raw = output_path(root, "raw")
    paths = [output_path(root, "experiment_config.yaml"), *sorted(raw.glob("*.csv")), *sorted(raw.glob("*.npz"))]
    return {p.relative_to(root).as_posix(): signature(p) for p in paths}


def _fit_line(width, value, weight):
    """加权最小二乘 y = m x + b；返回 (m, b, 预测值, 残差, 参数协方差)。"""
    x = np.asarray(width, float)
    y = np.asarray(value, float)
    w = np.asarray(weight, float)
    A = np.vstack([x, np.ones_like(x)]).T
    W = np.diag(w)
    normal = A.T @ W @ A
    coef = np.linalg.solve(normal, A.T @ W @ y)
    model = A @ coef
    return float(coef[0]), float(coef[1]), model, y - model, np.linalg.inv(normal)


def scan_report(run_dir, params):
    """B01：θ(宽度) 的线性内插、π 宽度、等效线圈常数与残差。"""
    (output_path(run_dir, "results")).mkdir(parents=True, exist_ok=True)
    path = output_path(run_dir, "raw/scan.csv")
    summary = dict(experiment_id=run_format.EXPERIMENT_ID,
                   coil_constant_hz_per_ma=params.coil_constant_hz_per_ma,
                   target_angle_pi=params.target_angle_pi,
                   predicted_width_us=params.predicted_width_us(),
                   peak_current_ma=params.pulse_peak_current_ma,
                   edge_us=params.pulse_edge_us)
    if not path.is_file():
        summary.update(rows=0, reason="缺少 raw/scan.csv（未运行扫描）")
        output_path(run_dir, "results/scan_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        _plot_unavailable(run_dir, "No scan data")
        return summary

    rows = _read_scan(path)
    if not rows:
        summary.update(rows=0, reason="scan.csv 无有效行")
        output_path(run_dir, "results/scan_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        _plot_unavailable(run_dir, "No valid scan rows")
        return summary

    widths = np.array([float(r["width_us"]) for r in rows])
    theta = np.array([float(r["theta_pi"]) for r in rows])
    sign = np.array([r["sign"] for r in rows])
    peak = np.array([float(r["peak_a"]) for r in rows])
    q = np.array([float(r["q_a_s"]) for r in rows])
    check_cancelled()

    points = []
    summary["points"] = points
    summary["rows"] = int(theta.size)
    for label, mask in (("plus", sign == "+"), ("minus", sign == "-")):
        if np.count_nonzero(mask) < 2:
            continue
        per_width = []
        for width in sorted(set(widths[mask])):
            values = theta[mask & (widths == width)]
            per_width.append(dict(width_us=float(width), rows=int(values.size),
                                  mean_q_a_s=float(np.mean(np.abs(q[mask & (widths == width)]))),
                                  mean_pi=float(np.mean(values)),
                                  sem_pi=float(np.std(values, ddof=1) / np.sqrt(values.size))
                                  if values.size > 1 else 0.0))
        if len(per_width) < 2:
            continue
        x = np.array([item["width_us"] for item in per_width])
        y = np.array([abs(item["mean_pi"]) for item in per_width])
        sem = np.array([max(item["sem_pi"], 1e-6) for item in per_width])
        slope, intercept, model, resid, cov = _fit_line(x, y, 1.0 / sem ** 2)
        if abs(slope) < 1e-12:
            continue
        width_pi = (params.target_angle_pi - intercept) / slope
        # σ(w*) 由 (m, b) 的协方差传播：w* = (T − b)/m
        der_b, der_m = -1.0 / slope, -(params.target_angle_pi - intercept) / slope ** 2
        var_w = der_m ** 2 * cov[0, 0] + der_b ** 2 * cov[1, 1] + 2 * der_m * der_b * cov[0, 1]
        width_pi_sem = float(np.sqrt(max(var_w, 0.0)))
        # 理想升余弦沿下 θ(0) = −m·沿宽，用截距反推等效沿宽，可与 PULSE_EDGE_US 对照
        effective_edge_us = float(-intercept / slope)
        peak_ma = float(np.mean(peak[mask])) * 1e3
        # 脉冲的有效平顶电流由 ∫I dt 随宽度的斜率给出；它与峰值之比描述波形过冲，
        # 不是线圈常数——θ 本身用 K 定义，反解 K 只会回到 K×该比值。
        q_slope = float(np.polyfit(x, np.array([abs(item["mean_q_a_s"]) for item in per_width]), 1)[0])
        flat_ma = q_slope * 1e6 * 1e3
        points.append(dict(side=label, width_pi=float(width_pi), width_pi_sem=width_pi_sem,
                           slope_pi_per_us=float(slope), intercept_pi=float(intercept),
                           effective_edge_us=effective_edge_us,
                           residual_pi=float(np.sqrt(np.mean(resid ** 2))),
                           peak_current_ma=peak_ma, flat_current_ma=flat_ma,
                           flat_over_peak_ratio=(flat_ma / peak_ma) if peak_ma else None,
                           per_width=per_width,
                           fit_width_us=x.tolist(), fit_value_pi=y.tolist(), fit_model_pi=model.tolist()))
    summary["reason"] = "" if points else "每个符号至少需要两个宽度点才能内插"
    summary["note"] = ("width_pi 是在当前 COIL_CONSTANT_HZ_PER_MA 下定义的角度；K 改变时 width_pi 按 1/K 缩放。"
                       "flat_current_ma 由 ∫I dt 对宽度的斜率给出，与 peak_current_ma 之比描述波形过冲，"
                       "不是线圈常数。")
    output_path(run_dir, "results/scan_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    if points:
        _plot_scan(run_dir, summary, points)
    else:
        _plot_unavailable(run_dir, "Need two or more widths")
    return summary


def _plot_scan(run_dir, summary, points):
    import matplotlib
    matplotlib.use("Agg")
    from ...plotting import COLOR_OPTIMAL, COLOR_TRAD, format_axis, new_figure, plot_style_context, save_figure

    with plot_style_context("paper"):
        figure, axis = new_figure()
        for item, color in zip(points, (COLOR_OPTIMAL, COLOR_TRAD)):
            x = np.asarray(item["fit_width_us"], float)
            y = np.asarray(item["fit_value_pi"], float)
            axis.errorbar(x, y, yerr=[p["sem_pi"] for p in item["per_width"]], fmt="o", ms=4, capsize=2,
                          color=color, label=r"$+\pi$" if item["side"] == "plus" else r"$-\pi$")
            axis.plot(x, item["fit_model_pi"], "-", lw=1, color=color)
            axis.axvline(item["width_pi"], color=color, ls=":", lw=0.8)
        axis.axhline(summary["target_angle_pi"], color="0.5", ls="--", lw=0.8)
        axis.axvline(summary["predicted_width_us"], color="0.3", ls="-.", lw=0.8)
        format_axis(axis, xlabel="Pulse width (us)", ylabel=r"Pulse angle $|\theta|/\pi$")
        axis.legend(loc="best", fontsize=7)
        save_figure(figure, output_path(run_dir, "results/scan_fit.png"))


def _plot_unavailable(run_dir, message):
    import matplotlib
    matplotlib.use("Agg")
    from ...plotting import new_figure, plot_style_context, save_figure

    with plot_style_context("paper"):
        figure, axis = new_figure()
        axis.text(0.5, 0.5, message, ha="center", va="center", transform=axis.transAxes, fontsize=8)
        axis.set_axis_off()
        save_figure(figure, output_path(run_dir, "results/scan_fit.png"))
