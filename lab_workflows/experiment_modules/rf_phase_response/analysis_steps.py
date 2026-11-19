"""B01 离线分析：逐相位汇总 R/θ，解 ``Z(φ)=c0+c+1e^{iφ}+c−1e^{−iφ}``，给最佳读相位。

不连接仪器；只读运行目录 ``raw/phase_response.csv``，写 ``results/``。
"""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import numpy as np

from ...experiment_runtime import check_cancelled
from ..z_chain_common.analysis import decompose
from . import run_format

# 残差 RMS 超过逐相位 R 标准误中位数的这个倍数即判拟合质量差。
POOR_FIT_SIGMA = 3.0
# 最大 R 低于标准误中位数的这个倍数时判信号可忽略。
LOW_SIGNAL_MULTIPLE = 5.0
MIN_PHASES = 3


def output_path(run_dir, name):
    return Path(run_dir) / name


def _run_state(run_dir):
    return run_format.read_config(Path(run_dir) / "experiment_config.yaml") or {}


def _read_rows(path):
    rows = []
    with Path(path).open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            try:
                phase = float(row["phase_deg"])
                repeat = int(row["repeat"])
                r_v = float(row["R_v"])
                theta = float(row["theta_deg"])
            except (KeyError, TypeError, ValueError):
                continue
            if all(math.isfinite(value) for value in (phase, r_v, theta)):
                rows.append(dict(phase_deg=phase, repeat=repeat, R_v=r_v, theta_deg=theta))
    return rows


def _phase_summary(rows):
    """按相位汇总：R 的算术平均与标准误，θ 的圆周平均与去缠绕标准误。"""
    by_phase: dict[float, list[dict]] = {}
    for row in rows:
        by_phase.setdefault(row["phase_deg"], []).append(row)
    points = []
    for phase in sorted(by_phase):
        items = by_phase[phase]
        values = np.array([item["R_v"] for item in items], dtype=float)
        angles = np.radians(np.array([item["theta_deg"] for item in items], dtype=float))
        theta = math.atan2(float(np.mean(np.sin(angles))), float(np.mean(np.cos(angles))))
        unwrapped = np.unwrap(np.array([item["theta_deg"] for item in items], dtype=float),
                              period=360.0)
        theta_sem = float(np.std(unwrapped, ddof=1) / math.sqrt(len(items))) if len(items) > 1 else 0.0
        points.append(dict(phase_deg=float(phase), R_mean_v=float(np.mean(values)),
                           R_sem_v=(float(np.std(values, ddof=1) / math.sqrt(len(values)))
                                    if len(values) > 1 else 0.0),
                           theta_mean_deg=math.degrees(theta), theta_sem_deg=theta_sem,
                           samples=len(items)))
    return points


def _component_record(value, sem=0.0):
    return dict(re_v=float(value.real), im_v=float(value.imag), abs_v=float(abs(value)),
                arg_deg=float(math.degrees(math.atan2(value.imag, value.real))), sem_v=float(sem))


def _repeat_components(rows, phases):
    """每次重复都齐全时逐重复分解，给三分量的标准误；不齐全时返回空。"""
    by_repeat: dict[int, dict[float, complex]] = {}
    for row in rows:
        z = row["R_v"] * np.exp(1j * math.radians(row["theta_deg"]))
        by_repeat.setdefault(row["repeat"], {})[row["phase_deg"]] = z
    usable = [values for values in by_repeat.values() if set(phases) <= set(values)]
    if len(usable) < 2:
        return {}
    coefficients = [decompose(phases, [values[phase] for phase in phases])[0] for values in usable]
    coefficients = np.asarray(coefficients)
    sem = coefficients.std(axis=0, ddof=1) / math.sqrt(coefficients.shape[0])
    return {key: _component_record(coefficients[:, index].mean(), sem[index])
            for index, key in enumerate(("c0", "c_plus1", "c_minus1"))}


def _model_curve(coefficients, start=0.0, stop=360.0, step=1.0):
    phases = np.arange(start, stop + step / 2.0, step)
    phi = np.radians(phases)
    values = (coefficients[0] + coefficients[1] * np.exp(1j * phi)
              + coefficients[2] * np.exp(-1j * phi))
    return phases, values


def _plot(path, points, model_phases, model_values, summary):
    """上：R 随相位（带误差棒）与三分量模型 |Z(φ)|；下：θ 随相位。图中文字用英文。"""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from ...plotting import COLOR_OPTIMAL, COLOR_TRAD, format_axis, plot_style_context, save_figure

    phases = np.array([item["phase_deg"] for item in points], dtype=float)
    values = np.array([item["R_mean_v"] for item in points], dtype=float)
    errors = np.array([item["R_sem_v"] for item in points], dtype=float)
    thetas = np.array([item["theta_mean_deg"] for item in points], dtype=float)
    theta_errors = np.array([item["theta_sem_deg"] for item in points], dtype=float)
    with plot_style_context("paper"):
        figure, (top, bottom) = plt.subplots(2, 1, sharex=True, figsize=(7.0, 5.4),
                                            gridspec_kw={"height_ratios": [3, 2]})
        top.errorbar(phases, values, yerr=errors, fmt="o", ms=4, capsize=2, color=COLOR_OPTIMAL,
                     label="MFLI R (Demod0)")
        top.plot(model_phases, np.abs(model_values), "-", lw=1.2, color=COLOR_TRAD,
                 label="model |c0 + c+1 e^{i phi} + c-1 e^{-i phi}|")
        best = summary.get("model_best_phase_deg")
        if best is not None:
            top.axvline(float(best), color="0.4", ls=":", lw=0.9, label=f"best {float(best):.1f} deg")
        format_axis(top, xlabel="RF phase (deg)", ylabel="R (V)")
        top.legend(loc="best", fontsize=7)
        bottom.errorbar(phases, thetas, yerr=theta_errors, fmt="o-", ms=3, lw=0.8, capsize=2,
                        color=COLOR_OPTIMAL)
        format_axis(bottom, xlabel="RF phase (deg)", ylabel="theta (deg)")
        save_figure(figure, path)


def _write_unavailable(summary, root, reason, plot_message):
    summary = dict(summary, error=reason)
    path = output_path(root, run_format.SUMMARY_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        import matplotlib

        matplotlib.use("Agg")
        from ...plotting import format_axis, new_figure, plot_style_context, save_figure

        with plot_style_context("paper"):
            figure, axis = new_figure()
            axis.text(0.5, 0.5, plot_message, ha="center", va="center", transform=axis.transAxes)
            format_axis(axis, xlabel="RF phase (deg)", ylabel="R (V)")
            save_figure(figure, output_path(root, run_format.PHASE_RESPONSE_PLOT))
    except Exception:
        pass
    return summary


def scan_report(run_dir, params):
    """B01：汇总相位响应、三分量分解与最佳读相位，写摘要与图。"""
    root = Path(run_dir)
    output_path(root, "results").mkdir(parents=True, exist_ok=True)
    config = _run_state(root)
    summary = dict(experiment_id=run_format.EXPERIMENT_ID,
                   rf_frequency_hz=params.y_rf_freq_hz, rf_vpp=params.y_rf_vpp,
                   pulse_period_us=params.pulse_period_us, peak_current_ma=params.z_peak_current_ma,
                   width_us=params.z_pulse_width_us, edge_us=params.z_pulse_edge_us,
                   tail_comp=bool(params.z_tail_comp_enabled), tail_tau_c_us=params.z_tail_tau_c_us,
                   n_repeat=params.n_repeat, point_duration_s=params.scan_point_duration_s,
                   demod0_rate_sa_s=params.demod0_rate_sa_s, demod0_tc_s=params.demod0_tc_s,
                   model="Z(phi) = c0 + c+1*exp(i*phi) + c-1*exp(-i*phi)",
                   drift_correction="none")
    path = output_path(root, run_format.PHASE_RESPONSE_FILE)
    if not path.is_file():
        return _write_unavailable(summary, root, "缺少 raw/phase_response.csv（未运行扫描）",
                                  "No phase data: raw/phase_response.csv missing")
    rows = _read_rows(path)
    phases = sorted({row["phase_deg"] for row in rows})
    if not rows:
        return _write_unavailable(summary, root, "phase_response.csv 无有效行", "No valid phase rows")
    if len(phases) < MIN_PHASES:
        return _write_unavailable(summary, root,
                                  f"只有 {len(phases)} 个相位，三分量分解至少需要 {MIN_PHASES} 个",
                                  f"Fewer than {MIN_PHASES} RF phases recorded")
    check_cancelled()
    points = _phase_summary(rows)
    values = [item["R_mean_v"] * np.exp(1j * math.radians(item["theta_mean_deg"])) for item in points]
    coefficients, residual_rms = decompose(phases, values)
    components = _repeat_components(rows, phases)
    if not components:
        components = {key: _component_record(coefficients[index])
                      for index, key in enumerate(("c0", "c_plus1", "c_minus1"))}
    model_phases, model_values = _model_curve(coefficients)
    best_index = int(np.argmax(np.abs(model_values)))
    measured_best = max(points, key=lambda item: item["R_mean_v"])
    sems = np.array([item["R_sem_v"] for item in points], dtype=float)
    positive = sems[sems > 0]
    sem_median = float(np.median(positive)) if positive.size else 0.0
    signal_low = bool(sem_median > 0 and max(item["R_mean_v"] for item in points) < LOW_SIGNAL_MULTIPLE * sem_median)
    fit_quality = "good" if sem_median == 0 or residual_rms <= POOR_FIT_SIGMA * sem_median else "poor"
    summary.update(mode="phase_response", phases_deg=phases, rows=len(rows), points=points,
                   components=components, residual_rms_v=float(residual_rms),
                   fit_quality=fit_quality,
                   best_phase_deg=float(model_phases[best_index]),
                   model_best_phase_deg=float(model_phases[best_index]),
                   best_phase_source="model",
                   measured_best_phase_deg=float(measured_best["phase_deg"]),
                   measured_best_R_v=float(measured_best["R_mean_v"]),
                   model_v=[dict(phase_deg=float(p), R_v=float(abs(v)))
                            for p, v in zip(model_phases.tolist()[::5], model_values[::5])],
                   signal_low=signal_low,
                   phase_source="scan",
                   demod0_phase_deg=config.get("demod0_phase_deg"))
    check_cancelled()
    _plot(output_path(root, run_format.PHASE_RESPONSE_PLOT), points, model_phases, model_values, summary)
    summary_path = output_path(root, run_format.SUMMARY_FILE)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary
