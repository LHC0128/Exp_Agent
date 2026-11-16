"""离线输入资格、零点/脉冲 CSV 读取与两段表征；不连接仪器，不改写 raw。"""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import numpy as np

from ...experiment_runtime import check_cancelled
from . import processing, run_format
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
    root = Path(run_dir)
    raw = output_path(root, "raw")
    paths = [output_path(root, "experiment_config.yaml"), *sorted(raw.glob("*.csv")), *sorted(raw.glob("*.npz"))]
    return {p.relative_to(root).as_posix(): signature(p) for p in paths}


def _read_csv(path: Path) -> dict[str, np.ndarray]:
    rows = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            rows.append(row)
    if not rows:
        return {}
    def column(name, cast=float):
        return np.asarray([cast(row[name]) for row in rows])

    def optional(name):
        return column(name) if name in rows[0] else None
    return dict(elapsed_s=column("elapsed_s"), current_a=optional("current_a"),
                mean_v=optional("mean_v"), std_v=optional("std_v"),
                sign=np.asarray([row.get("sign", "") for row in rows]),
                theta_pi=optional("theta_pi"), q_a_s=optional("q_a_s"),
                i_base_a=optional("i_base_a"), peak_a=optional("peak_a"))


def _stats(values, elapsed, dt_s):
    values = np.asarray(values, float)
    elapsed = np.asarray(elapsed, float)
    taus, devs = processing.allan_deviation(values, dt_s)
    return dict(rows=int(values.size), mean=float(np.mean(values)), std=float(np.std(values)),
                min=float(np.min(values)), max=float(np.max(values)),
                drift_per_s=processing.drift_rate(values, elapsed),
                allan_dt_s=float(dt_s), allan_tau_s=taus, allan_dev=devs)


def _mode_file(run_dir, mode):
    return output_path(run_dir, f"raw/zero_{mode}.csv")


def zero_report(run_dir, params):
    """B01：各零点段的均值、噪声、漂移、Allan 与电流—场换算；缺段留空。"""
    (output_path(run_dir, "results")).mkdir(parents=True, exist_ok=True)
    b_nt_per_ma = params.coil_constant_hz_per_ma / GYROMAGNETIC_RATIO_HZ_PER_NT
    segments = {}
    for mode in ("dg0", "baseline", "dg0_fast", "baseline_fast"):
        path = _mode_file(run_dir, mode)
        if not path.is_file():
            continue
        check_cancelled()
        data = _read_csv(path)
        if not data or data["current_a"].size < 2:
            continue
        dt = float(np.median(np.diff(data["elapsed_s"]))) if data["elapsed_s"].size > 1 else 0.0
        stats = _stats(data["current_a"], data["elapsed_s"], dt)
        segments[mode] = {**stats,
                          "mean_nt": stats["mean"] * 1e3 * b_nt_per_ma,
                          "std_nt": stats["std"] * 1e3 * b_nt_per_ma,
                          "drift_nt_per_day": stats["drift_per_s"] * 86400.0 * 1e3 * b_nt_per_ma}
    summary = dict(experiment_id=run_format.EXPERIMENT_ID, b_nt_per_ma=b_nt_per_ma,
                   coil_constant_hz_per_ma=params.coil_constant_hz_per_ma,
                   sense_resistor_ohm=params.sense_resistor_ohm, segments=segments)
    output_path(run_dir, "results/zero_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    if segments:
        _plot_zero(run_dir, segments)
    return summary


def _plot_zero(run_dir, segments):
    import matplotlib
    matplotlib.use("Agg")
    from lab_workflows.plotting import COLOR_OPTIMAL, COLOR_TRAD, format_axis, new_figure, plot_style_context, save_figure

    with plot_style_context("paper"):
        figure, axis = new_figure()
        for mode, color in (("dg0", COLOR_OPTIMAL), ("baseline", COLOR_TRAD)):
            path = _mode_file(run_dir, mode)
            if not path.is_file():
                continue
            data = _read_csv(path)
            axis.plot(data["elapsed_s"], data["current_a"] * 1e3, ".", ms=2, color=color, label=mode)
        format_axis(axis, xlabel="Elapsed (s)", ylabel="Current (mA)")
        if axis.get_legend_handles_labels()[0]:
            axis.legend(loc="best", fontsize=7)
        save_figure(figure, output_path(run_dir, "results/zero_timeseries.png"))

        figure, axis = new_figure()
        for mode, color in (("dg0", COLOR_OPTIMAL), ("baseline", COLOR_TRAD)):
            if mode not in segments or not segments[mode]["allan_tau_s"]:
                continue
            axis.loglog(segments[mode]["allan_tau_s"], segments[mode]["allan_dev"], "o-", ms=3, color=color, label=mode)
        format_axis(axis, xlabel=r"$\tau$ (s)", ylabel=r"Allan deviation (mA)")
        if axis.get_legend_handles_labels()[0]:
            axis.legend(loc="best", fontsize=7)
        save_figure(figure, output_path(run_dir, "results/zero_allan.png"))


def pulse_report(run_dir, params):
    """B02：+π/−π 的 Q/θ 分布、漂移、Allan 与不对称；缺数据写空摘要与原因。"""
    (output_path(run_dir, "results")).mkdir(parents=True, exist_ok=True)
    path = output_path(run_dir, "raw/pulse_angles.csv")
    summary = dict(experiment_id=run_format.EXPERIMENT_ID, coil_constant_hz_per_ma=params.coil_constant_hz_per_ma,
                   pulse_width_us=params.pulse_width_us, pulse_period_us=params.pulse_period_us,
                   source="pulse_angles.csv")
    if not path.is_file():
        summary.update(rows=0, reason="缺少 raw/pulse_angles.csv（未运行 A05）")
        output_path(run_dir, "results/pulse_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return summary
    data = _read_csv(path)
    if not data or data["theta_pi"] is None or data["theta_pi"].size == 0:
        summary.update(rows=0, reason="pulse_angles.csv 无有效行")
        output_path(run_dir, "results/pulse_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return summary
    sign, theta, elapsed = data["sign"], data["theta_pi"], data["elapsed_s"]
    positive, negative = sign == "+", sign == "-"
    dt = float(np.median(np.diff(elapsed))) if elapsed.size > 1 else 0.0
    stats = {}
    for name, mask in (("plus", positive), ("minus", negative)):
        if np.count_nonzero(mask) >= 2:
            stats[name] = _stats(theta[mask], elapsed[mask], dt)
    summary.update(rows=int(theta.size), plus=stats.get("plus"), minus=stats.get("minus"),
                   asymmetry_pi=(stats["plus"]["mean"] + stats["minus"]["mean"])
                   if "plus" in stats and "minus" in stats else None)
    output_path(run_dir, "results/pulse_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    _plot_pulse(run_dir, data, positive, negative)
    return summary


def _plot_pulse(run_dir, data, positive, negative):
    import matplotlib
    matplotlib.use("Agg")
    from lab_workflows.plotting import COLOR_OPTIMAL, COLOR_TRAD, format_axis, new_figure, plot_style_context, save_figure

    with plot_style_context("paper"):
        figure, axis = new_figure()
        axis.plot(data["elapsed_s"][positive], data["theta_pi"][positive], ".", ms=3, color=COLOR_OPTIMAL, label=r"$+\pi$")
        axis.plot(data["elapsed_s"][negative], data["theta_pi"][negative], ".", ms=3, color=COLOR_TRAD, label=r"$-\pi$")
        format_axis(axis, xlabel="Elapsed (s)", ylabel=r"Pulse angle $\theta/\pi$")
        axis.legend(loc="best", fontsize=7)
        save_figure(figure, output_path(run_dir, "results/pulse_timeseries.png"))

        figure, axis = new_figure()
        bins = max(10, min(60, int(np.sqrt(max(1, data["theta_pi"].size)))))
        axis.hist(data["theta_pi"], bins=bins, color=COLOR_OPTIMAL, alpha=0.75)
        format_axis(axis, xlabel=r"Pulse angle $\theta/\pi$", ylabel="Count")
        save_figure(figure, output_path(run_dir, "results/pulse_hist.png"))
