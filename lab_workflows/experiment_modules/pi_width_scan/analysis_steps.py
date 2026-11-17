"""离线分析：按宽度汇总 R 的均值与标准误，加权拟合 R(τ) 的峰位给出 π 宽度。

判据模型为 ``R(τ) = R_max |sin(π(τ − τ0) / (2 τ_π))|``：峰位即 π 宽度。拟合用逐点标准误
作权重；残差 RMS 超过单点标准误中位数的 3 倍时标记 ``fit_quality: poor`` 并写明原因。
R_max 小于单点标准误中位数的 5 倍时标记 ``signal_low``，只提示可能是射频相位落在零点附近。
本实验不做漂移校正。
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path

import numpy as np

from ...experiment_runtime import check_cancelled
from . import run_format
from .stages import ANALYSIS_KEYS

# 单点离开拟合曲线超过 3 倍标准误即不保留；残差 RMS 超过 3 倍中位标准误即判拟合质量差。
KEEP_SIGMA = 3.0
POOR_FIT_SIGMA = 3.0
# R_max 低于单点标准误中位数的 5 倍时判信号可忽略（相位可能落在零点附近）。
LOW_SIGNAL_MULTIPLE = 5.0
MIN_WIDTH_POINTS = 4


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
    """分步分析用的输入指纹：配置、扫描表与全部逐点序列。"""
    root = Path(run_dir)
    raw = output_path(root, "raw")
    paths = [output_path(root, "experiment_config.yaml"), *sorted(raw.glob("*.csv"))]
    return {p.relative_to(root).as_posix(): signature(p) for p in paths}


def model_r(width_us, r_max, tau0_us, tau_pi_us):
    """R(τ) 的判据模型；用于拟合与作图。"""
    x = np.asarray(width_us, dtype=float)
    return r_max * np.abs(np.sin(np.pi * (x - tau0_us) / (2.0 * tau_pi_us)))


def fit_r_vs_width(widths, values, sem):
    """加权最小二乘拟合 (R_max, τ0, τπ)；返回参数、1σ 与残差。

    网格给出稳健初值（幅度对固定 τ0、τπ 是线性的），再用 ``least_squares`` 在带边界的
    参数上收敛。协方差按加权最小二乘的 ``inv(JᵀJ)`` 计，残差超过权重时按约化 χ² 放大。
    """
    from scipy.optimize import least_squares

    x = np.asarray(widths, dtype=float)
    y = np.asarray(values, dtype=float)
    s = np.asarray(sem, dtype=float)
    if x.size != y.size or x.size != s.size:
        raise AnalysisQualityError("宽度、R 与标准误长度不一致")
    if x.size < MIN_WIDTH_POINTS:
        raise AnalysisQualityError(f"至少需要 {MIN_WIDTH_POINTS} 个宽度点才能拟合 3 个参数")
    if not np.all(np.isfinite(x)) or not np.all(np.isfinite(y)):
        raise AnalysisQualityError("宽度或 R 含非有限值")
    usable = np.isfinite(s) & (s > 0)
    sigma = s if np.all(usable) else np.full(x.size, float(np.median(s[usable])) if np.any(usable) else 1.0)
    sqrt_w = 1.0 / sigma
    span = float(x.max() - x.min())
    if not math.isfinite(span) or span <= 0:
        raise AnalysisQualityError("宽度点必须有不同的取值")
    steps = np.diff(np.unique(x))
    step = float(np.min(steps)) if steps.size else span

    best = None
    for tau0 in np.linspace(x.min() - 2.0 * span, x.min() + 0.25 * span, 41):
        for tau_pi in np.linspace(0.25 * span, 3.0 * span, 80):
            shape = np.abs(np.sin(np.pi * (x - tau0) / (2.0 * tau_pi)))
            denominator = float(np.sum((shape / sigma) ** 2))
            if denominator <= 0:
                continue
            amplitude = float(np.sum(y * shape / sigma ** 2) / denominator)
            chi2 = float(np.sum(((y - amplitude * shape) / sigma) ** 2))
            if best is None or chi2 < best[0]:
                best = (chi2, amplitude, tau0, tau_pi)
    if best is None:
        raise AnalysisQualityError("模型在扫描区间内没有任何有效取值")
    _, amplitude, tau0, tau_pi = best
    amplitude_ceiling = max(4.0 * float(np.max(np.abs(y))), 1e-12)

    def residual(parameters):
        r_max, tau0_value, tau_pi_value = parameters
        return (model_r(x, r_max, tau0_value, tau_pi_value) - y) / sigma

    result = least_squares(
        residual, x0=[amplitude, tau0, tau_pi],
        bounds=([0.0, x.min() - 2.0 * span, 0.25 * step],
                [amplitude_ceiling, x.max(), 4.0 * span]),
        xtol=1e-12, ftol=1e-12, gtol=1e-12, max_nfev=20000)
    if not result.success or not np.all(np.isfinite(result.x)):
        raise AnalysisQualityError(f"R(τ) 拟合未收敛：{result.message}")
    r_max, tau0_value, tau_pi_value = (float(value) for value in result.x)
    if r_max <= 0 or tau_pi_value <= 0:
        raise AnalysisQualityError("拟合得到的幅度或 τπ 非正")
    model = model_r(x, r_max, tau0_value, tau_pi_value)
    residuals = y - model
    dof = int(x.size - 3)
    reduced_chi2 = float(2.0 * result.cost / dof) if dof > 0 else float("nan")
    try:
        covariance = np.linalg.inv(result.jac.T @ result.jac) * max(1.0, reduced_chi2 if dof > 0 else 1.0)
        errors = np.sqrt(np.clip(np.diag(covariance), 0.0, None))
    except np.linalg.LinAlgError as exc:
        raise AnalysisQualityError(f"拟合协方差奇异：{exc}") from exc
    return dict(r_max_v=r_max, r_max_sem_v=float(errors[0]),
                tau0_us=tau0_value, tau0_sem_us=float(errors[1]),
                tau_pi_us=tau_pi_value, tau_pi_sem_us=float(errors[2]),
                residual_rms_v=float(np.sqrt(np.mean(residuals ** 2))),
                reduced_chi2=reduced_chi2, dof=dof,
                model_v=model.tolist(), residual_v=residuals.tolist())


def _read_scan(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _summarize_points(rows):
    """按宽度汇总：R 均值与标准误、θ 圆周均值、温度均值。"""
    widths = sorted({float(row["width_us"]) for row in rows})
    points = []
    for width in widths:
        group = [row for row in rows if math.isclose(float(row["width_us"]), width, rel_tol=0, abs_tol=1e-9)]
        values = np.array([float(row["R_v"]) for row in group], dtype=float)
        finite = np.isfinite(values)
        if not np.any(finite):
            continue
        theta = np.radians(np.array([float(row["theta_deg"]) for row in group], dtype=float)[finite])
        temperatures = [float(row["temp_c"]) for row in group if str(row.get("temp_c", "")).strip()]
        points.append(dict(
            width_us=float(width), repeats=int(values.size),
            r_mean_v=float(np.mean(values[finite])),
            r_sem_v=float(np.std(values[finite], ddof=1) / math.sqrt(np.count_nonzero(finite)))
            if np.count_nonzero(finite) > 1 else 0.0,
            theta_mean_deg=math.degrees(math.atan2(float(np.mean(np.sin(theta))), float(np.mean(np.cos(theta))))),
            temperature_c=float(np.mean(temperatures)) if temperatures else None))
    return points


def _run_state(run_dir):
    """读运行目录的 experiment_config.yaml；缺文件返回空映射。"""
    config = run_format.read_config(output_path(run_dir, "experiment_config.yaml"))
    return config if isinstance(config, dict) else {}


def _phase_record(config, params):
    """本轮实际生效的射频相位、来源与相位粗扫记录；配置缺键时回落到参数值。"""
    record = config.get("phase_check")
    if isinstance(record, dict) and record.get("enabled"):
        chosen, refined = record.get("chosen_deg"), record.get("refined_deg")
        for value in (refined, chosen):
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return float(value), dict(record), "stage"
    elif isinstance(record, dict):
        return float(params.y_rf_phase_deg), dict(record), "parameter"
    # 没有记录（A03 未运行）时以运行配置里最后一次写入的相位为准，再退回参数。
    phase = config.get("rf_phase_deg")
    source = config.get("phase_source")
    phase = float(phase) if isinstance(phase, (int, float)) and not isinstance(phase, bool) else float(params.y_rf_phase_deg)
    return phase, dict(enabled=False), source if source in {"stage", "parameter"} else "parameter"


def _demod_phase_deg(config):
    """MFLI 解调相位回读值；本实验只用 R，不依赖它。"""
    lockin = config.get("lockin")
    value = lockin.get("phase_deg") if isinstance(lockin, dict) else None
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def scan_report(run_dir, params):
    """B01：R(宽度) 加权拟合、τπ 及其 1σ、残差判据与电学参考比值。"""
    root = Path(run_dir)
    output_path(root, "results").mkdir(parents=True, exist_ok=True)
    config = _run_state(root)
    rf_phase_deg, phase_check, phase_source = _phase_record(config, params)
    summary = dict(experiment_id=run_format.EXPERIMENT_ID,
                   rf_frequency_hz=params.y_rf_freq_hz, rf_vpp=params.y_rf_vpp,
                   pulse_period_us=params.pulse_period_us, peak_current_ma=params.z_peak_current_ma,
                   edge_us=params.z_pulse_edge_us, n_repeat=params.n_repeat,
                   point_duration_s=params.scan_point_duration_s,
                   coil_constant_hz_per_ma=params.coil_constant_hz_per_ma,
                   target_angle_pi=params.target_angle_pi,
                   predicted_width_us=params.predicted_width_us(),
                   rf_phase_deg=rf_phase_deg,
                   rf_phase_offset_us=params.rf_phase_offset_us(rf_phase_deg),
                   phase_source=phase_source,
                   phase_check=phase_check,
                   demod0_phase_deg=_demod_phase_deg(config),
                   electrical_pi_width_us=params.reference_pi_width_us,
                   demod0_rate_sa_s=params.demod0_rate_sa_s, demod0_tc_s=params.demod0_tc_s,
                   model="R(tau) = R_max * |sin(pi*(tau - tau0)/(2*tau_pi))|",
                   drift_correction="none")
    path = output_path(root, "raw/scan.csv")
    if not path.is_file():
        return _write_unavailable(summary, root, "缺少 raw/scan.csv（未运行扫描）",
                                  plot_message="No R data: raw/scan.csv missing")
    rows = _read_scan(path)
    if not rows:
        return _write_unavailable(summary, root, "scan.csv 无有效行",
                                  plot_message="No valid scan rows")
    check_cancelled()
    points = _summarize_points(rows)
    summary["points"] = points
    summary["rows"] = len(rows)
    summary["widths"] = len(points)
    if len(points) < MIN_WIDTH_POINTS:
        return _write_unavailable(summary, root,
                                  f"有效宽度点 {len(points)} 个，少于拟合所需的 {MIN_WIDTH_POINTS} 个",
                                  plot_message=f"Fewer than {MIN_WIDTH_POINTS} widths: cannot fit 3 parameters",
                                  write_points=True)
    fit = fit_r_vs_width([item["width_us"] for item in points], [item["r_mean_v"] for item in points],
                         [item["r_sem_v"] for item in points])
    sems = np.array([item["r_sem_v"] for item in points], dtype=float)
    positive = sems[sems > 0]
    sem_median = float(np.median(positive)) if positive.size else 0.0
    for item, model_value, residual_value in zip(points, fit["model_v"], fit["residual_v"]):
        reference = item["r_sem_v"] if item["r_sem_v"] > 0 else sem_median
        # 没有任何标准误时（重复读数相同或 N_REPEAT=1）无法做残差比较，不据此剔除点。
        normalized = abs(residual_value) / reference if reference > 0 else None
        item.update(model_v=float(model_value), residual_v=float(residual_value),
                    normalized_residual=float(normalized) if normalized is not None else None,
                    keep=bool(normalized is None or normalized <= KEEP_SIGMA))
    fit_quality, fit_reason = "good", ""
    if sem_median > 0 and fit["residual_rms_v"] > POOR_FIT_SIGMA * sem_median:
        fit_quality = "poor"
        fit_reason = (f"残差 RMS {fit['residual_rms_v']:.3g} V 超过单点标准误中位数 {sem_median:.3g} V 的 "
                      f"{POOR_FIT_SIGMA:g} 倍，判据模型与数据不自洽")
    elif sem_median <= 0:
        fit_reason = "单点标准误中位数为零（重复读数相同或 N_REPEAT=1），无法用残差与标准误比较，只记录原因"
    summary.update(
        fit=dict(r_max_v=fit["r_max_v"], r_max_sem_v=fit["r_max_sem_v"],
                 tau0_us=fit["tau0_us"], tau0_sem_us=fit["tau0_sem_us"],
                 tau_pi_us=fit["tau_pi_us"], tau_pi_sem_us=fit["tau_pi_sem_us"],
                 residual_rms_v=fit["residual_rms_v"], sem_median_v=sem_median,
                 reduced_chi2=fit["reduced_chi2"], dof=fit["dof"]),
        tau_pi_us=fit["tau_pi_us"], tau_pi_sem_us=fit["tau_pi_sem_us"],
        ratio_to_electrical=fit["tau_pi_us"] / params.reference_pi_width_us,
        ratio_to_predicted=fit["tau_pi_us"] / params.predicted_width_us(),
        fit_quality=fit_quality, fit_quality_reason=fit_reason,
        dropped_points=sum(1 for item in points if not item["keep"]),
        keep=fit_quality == "good" and all(item["keep"] for item in points),
        reason="")
    if sem_median > 0 and fit["r_max_v"] < LOW_SIGNAL_MULTIPLE * sem_median:
        summary["signal_low"] = True
        summary["signal_low_reason"] = (
            f"拟合幅度 R_max {fit['r_max_v']:.3g} V 小于单点标准误中位数 {sem_median:.3g} V 的 "
            f"{LOW_SIGNAL_MULTIPLE:g} 倍：信号可忽略，可能是射频相位落在零点附近；"
            f"不改变拟合结果，先核对相位自检记录")
    output_path(root, "results/scan_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    _plot_scan(root, summary, points, fit)
    return summary


def _write_unavailable(summary, run_dir, reason, *, plot_message, write_points=False):
    summary.update(rows=summary.get("rows", 0), reason=reason, fit_quality="unavailable",
                   fit_quality_reason=reason, keep=False, fit=None)
    if not write_points:
        summary.pop("points", None)
    output_path(run_dir, "results/scan_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    _plot_unavailable(run_dir, plot_message)
    return summary


def _plot_scan(run_dir, summary, points, fit):
    import matplotlib
    matplotlib.use("Agg")
    from ...plotting import COLOR_OPTIMAL, COLOR_TRAD, format_axis, new_figure, plot_style_context, save_figure

    widths = np.array([item["width_us"] for item in points], dtype=float)
    means = np.array([item["r_mean_v"] for item in points], dtype=float)
    sems = np.array([item["r_sem_v"] for item in points], dtype=float)
    span = float(widths.max() - widths.min())
    curve_x = np.linspace(widths.min() - 0.1 * span, widths.max() + 0.1 * span, 600)
    with plot_style_context("paper"):
        figure, axis = new_figure()
        axis.errorbar(widths, means, yerr=sems, fmt="o", ms=4, capsize=2, color=COLOR_OPTIMAL,
                      label="MFLI R (Demod0)")
        axis.plot(curve_x, model_r(curve_x, fit["r_max_v"], fit["tau0_us"], fit["tau_pi_us"]), "-", lw=1,
                  color=COLOR_TRAD, label="weighted fit")
        axis.axvline(fit["tau_pi_us"], color="0.3", ls=":", lw=0.9,
                     label=fr"$\tau_\pi$ = {fit['tau_pi_us']:.3f} us")
        axis.axvline(summary["predicted_width_us"], color="0.6", ls="--", lw=0.8,
                     label=f"predicted {summary['predicted_width_us']:.3f} us")
        format_axis(axis, xlabel="Pulse width (us)", ylabel="R (V)")
        axis.legend(loc="best", fontsize=7)
        save_figure(figure, output_path(run_dir, "results/r_vs_width.png"))


def _plot_unavailable(run_dir, message):
    import matplotlib
    matplotlib.use("Agg")
    from ...plotting import new_figure, plot_style_context, save_figure

    with plot_style_context("paper"):
        figure, axis = new_figure()
        axis.text(0.5, 0.5, message, ha="center", va="center", transform=axis.transAxes, fontsize=8,
                  wrap=True)
        axis.set_axis_off()
        save_figure(figure, output_path(run_dir, "results/r_vs_width.png"))
