"""B01 离线分析：逐相位汇总 R/θ，解 ``Z(φ)=c0+c+1e^{iφ}+c−1e^{−iφ}``，给最佳读相位。

不连接仪器；只读运行目录 ``raw/phase_response.csv``，写 ``results/``。
"""
from __future__ import annotations

import csv
import hashlib
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
    if options:
        raise ValueError("不支持的分析覆盖值：" + "、".join(sorted(options)))
    return base


def signature(path):
    path = Path(path)
    if not path.is_file():
        return None
    return [path.stat().st_size, hashlib.sha256(path.read_bytes()).hexdigest()]


def fingerprint(run_dir):
    """分步分析用的输入指纹：配置、相位表与全部逐点序列。"""
    root = Path(run_dir)
    raw = output_path(root, "raw")
    paths = [output_path(root, "experiment_config.yaml"), *sorted(raw.glob("*.csv"))]
    return {p.relative_to(root).as_posix(): signature(p) for p in paths}


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
                try:
                    x_v, y_v = float(row["x_v"]), float(row["y_v"])
                    if not (math.isfinite(x_v) and math.isfinite(y_v)):
                        x_v = y_v = None
                except (KeyError, TypeError, ValueError):
                    x_v = y_v = None
                rows.append(dict(phase_deg=phase, repeat=repeat, R_v=r_v, theta_deg=theta,
                                 x_v=x_v, y_v=y_v))
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
        xs = [item["x_v"] for item in items if item["x_v"] is not None]
        ys = [item["y_v"] for item in items if item["y_v"] is not None]
        points.append(dict(phase_deg=float(phase), R_mean_v=float(np.mean(values)),
                           R_sem_v=(float(np.std(values, ddof=1) / math.sqrt(len(values)))
                                    if len(values) > 1 else 0.0),
                           theta_mean_deg=math.degrees(theta), theta_sem_deg=theta_sem,
                           x_mean_v=float(np.mean(xs)) if xs else None,
                           y_mean_v=float(np.mean(ys)) if ys else None,
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


def _phase_sensitivity(coefficients, start=0.0, stop=360.0, step=0.1):
    """|dZ/dφ|（V/deg）：相量随射频相位变化的快慢，最大值处是相位响应最灵敏的工作点。"""
    phases = np.arange(start, stop, step)
    phi = np.radians(phases)
    derivative = 1j * (coefficients[1] * np.exp(1j * phi) - coefficients[2] * np.exp(-1j * phi))
    return phases, np.abs(derivative) * math.pi / 180.0


def _measured_sensitivity(phases, values):
    """相邻实测点的 |ΔZ/Δφ|，斜率记在两点中点；返回 (中点相位, 最大斜率 V/deg)。"""
    order = np.argsort(phases)
    p = np.asarray(phases, dtype=float)[order]
    z = np.asarray(values, dtype=complex)[order]
    if p.size < 2:
        return None, 0.0
    mids, slopes = [], []
    for index in range(p.size):
        nxt = (index + 1) % p.size
        delta = (p[nxt] - p[index]) % 360.0
        if delta <= 0:
            continue
        mids.append((p[index] + delta / 2.0) % 360.0)
        slopes.append(abs(z[nxt] - z[index]) / delta)
    if not slopes:
        return None, 0.0
    best = int(np.argmax(slopes))
    return float(mids[best]), float(slopes[best])


def _plot(path, points, model_phases, model_values, sensitivity, summary):
    """(a) R(φ) 与模型；(b) θ(φ)；(c) |dZ/dφ| 及实测差分。paper 样式，英文与 mathtext。"""
    import matplotlib

    matplotlib.use("Agg")

    from ...plotting import (COLOR_OPTIMAL, COLOR_TRAD, format_axis, new_figure, plot_style_context,
                             save_figure, style_legend)

    phases = np.array([item["phase_deg"] for item in points], dtype=float)
    values = np.array([item["R_mean_v"] for item in points], dtype=float)
    errors = np.array([item["R_sem_v"] for item in points], dtype=float)
    thetas = np.array([item["theta_mean_deg"] for item in points], dtype=float)
    theta_errors = np.array([item["theta_sem_deg"] for item in points], dtype=float)
    z_values = np.array([item["R_mean_v"] * np.exp(1j * math.radians(item["theta_mean_deg"]))
                         for item in points], dtype=complex)
    sens_phases, sens_values = sensitivity
    measured_mid, measured_max = _measured_sensitivity(phases, z_values)
    best = summary.get("best_phase_deg")
    alt = summary.get("best_phase_alt_deg")
    with plot_style_context("paper"):
        figure, (top, middle, bottom) = new_figure(
            nrows=3, sharex=True, gridspec_kw={"height_ratios": [3, 2, 2]})
        top.errorbar(phases, values, yerr=errors, fmt="o", ms=3.2, capsize=1.5, lw=0.8,
                     color=COLOR_OPTIMAL, label="MFLI R")
        top.plot(model_phases, np.abs(model_values), "-", lw=1.0, color=COLOR_TRAD,
                 label=r"model $|Z(\varphi)|$")
        if best is not None:
            top.axvline(float(best), color="0.4", ls=":", lw=0.8,
                        label=rf"max $|\mathrm{{d}}Z/\mathrm{{d}}\varphi|$: {float(best):.0f}$^\circ$ / {float(alt):.0f}$^\circ$")
        if alt is not None:
            top.axvline(float(alt), color="0.4", ls=(0, (1, 1)), lw=0.8)
        top.set_xlim(0.0, 360.0)
        top.set_xticks(np.arange(0.0, 361.0, 45.0))
        top.text(0.01, 1.01, "(a)", transform=top.transAxes, fontsize=7, va="bottom")
        format_axis(top, xlabel="", ylabel=r"$R$ (V)")
        style_legend(top, loc="lower left", fontsize=5.8, frameon=False)
        # θ 在 ±180° 处换支，连线会画出竖直跳变；这里只画散点。
        middle.errorbar(phases, thetas, yerr=theta_errors, fmt="o", ms=2.6, capsize=1.5, lw=0.8,
                        color=COLOR_OPTIMAL)
        middle.set_xlim(0.0, 360.0)
        middle.set_xticks(np.arange(0.0, 361.0, 45.0))
        middle.text(0.01, 1.01, "(b)", transform=middle.transAxes, fontsize=7, va="bottom")
        format_axis(middle, xlabel="", ylabel=r"$\theta$ (deg)")
        bottom.plot(sens_phases, sens_values * 1e3, "-", lw=1.0, color=COLOR_TRAD,
                    label=r"model $|\mathrm{d}Z/\mathrm{d}\varphi|$")
        if measured_mid is not None:
            bottom.plot([measured_mid], [measured_max * 1e3], "o", ms=3.0, mfc="none", mew=0.8,
                        color=COLOR_OPTIMAL, label="finite difference")
        if measured_mid is not None:
            bottom.annotate("finite diff", (measured_mid, measured_max * 1e3),
                            textcoords="offset points", xytext=(4, -2), fontsize=5.5, color="0.25")
        if best is not None:
            bottom.axvline(float(best), color="0.4", ls=":", lw=0.8)
        if alt is not None:
            bottom.axvline(float(alt), color="0.4", ls=(0, (1, 1)), lw=0.8)
        bottom.set_xlim(0.0, 360.0)
        bottom.set_xticks(np.arange(0.0, 361.0, 45.0))
        bottom.text(0.01, 1.01, "(c)", transform=bottom.transAxes, fontsize=7, va="bottom")
        format_axis(bottom, xlabel=r"RF phase $\varphi$ (deg)",
                    ylabel=r"$|\mathrm{d}Z/\mathrm{d}\varphi|$ (mV/deg)")
        save_figure(figure, path)


def _plot_phasor(path, points, model_values, coefficients, summary):
    """相量图：实测 Z(φ) 轨迹、模型闭曲线、c0 中心；星标为 |dZ/dφ| 最大处并画切向箭头。"""
    import matplotlib

    matplotlib.use("Agg")

    from ...plotting import (COLOR_OPTIMAL, COLOR_TRAD, format_axis, new_figure, plot_style_context,
                             save_figure, style_legend)

    phases = np.array([item["phase_deg"] for item in points], dtype=float)
    z_mv = np.array([item["R_mean_v"] * np.exp(1j * math.radians(item["theta_mean_deg"]))
                     for item in points]) * 1e3
    model_mv = np.asarray(model_values, dtype=complex) * 1e3
    best = float(summary["best_phase_deg"])
    alt = float(summary.get("best_phase_alt_deg", (best + 180.0) % 360.0))
    phi = math.radians(best)
    z_best = (coefficients[0] + coefficients[1] * np.exp(1j * phi)
              + coefficients[2] * np.exp(-1j * phi)) * 1e3
    phi_alt = math.radians(alt)
    z_alt = (coefficients[0] + coefficients[1] * np.exp(1j * phi_alt)
             + coefficients[2] * np.exp(-1j * phi_alt)) * 1e3
    tangent = 1j * (coefficients[1] * np.exp(1j * phi) - coefficients[2] * np.exp(-1j * phi))
    direction = tangent / abs(tangent) if abs(tangent) > 0 else 1.0 + 0.0j
    length = 0.22 * float(np.max(np.abs(model_mv)))
    with plot_style_context("paper"):
        figure, axis = new_figure(kind="square")
        axis.plot(model_mv.real, model_mv.imag, "-", color="0.72", lw=1.2, zorder=1,
                  label=r"model $Z(\varphi)$")
        axis.plot(z_mv.real, z_mv.imag, "-", color=COLOR_OPTIMAL, lw=0.5, alpha=0.45, zorder=2)
        scatter = axis.scatter(z_mv.real, z_mv.imag, c=phases, cmap="viridis", s=16, zorder=3,
                               edgecolors="k", linewidths=0.25, label="measured")
        step = max(1, len(phases) // 8)
        for index in range(0, len(phases) - 1, step):
            axis.annotate("", xy=(z_mv[index + 1].real, z_mv[index + 1].imag),
                          xytext=(z_mv[index].real, z_mv[index].imag),
                          arrowprops=dict(arrowstyle="-|>", color="0.25", lw=0.5,
                                          shrinkA=0.0, shrinkB=0.0), zorder=2)
        axis.plot([z_best.real - direction.real * length, z_best.real + direction.real * length],
                  [z_best.imag - direction.imag * length, z_best.imag + direction.imag * length],
                  "-", lw=1.0, color=COLOR_TRAD, zorder=4)
        axis.plot(z_best.real, z_best.imag, marker="*", ms=9, color=COLOR_TRAD, zorder=5,
                  label=rf"max $|\mathrm{{d}}Z/\mathrm{{d}}\varphi|$ = {best:.0f}$^\circ$ / {alt:.0f}$^\circ$")
        axis.plot(z_alt.real, z_alt.imag, marker="*", ms=9, mfc="none", mew=0.9,
                  color=COLOR_TRAD, zorder=5)
        axis.plot(0.0, 0.0, marker="+", ms=9, mew=1.0, color="k", zorder=4, label="origin")
        axis.plot(coefficients[0].real * 1e3, coefficients[0].imag * 1e3, marker="D", ms=4.0,
                  mfc="none", mew=0.8, color=COLOR_TRAD, zorder=4, label=r"$c_0$")
        axis.axhline(0.0, color="0.85", lw=0.5, zorder=0)
        axis.axvline(0.0, color="0.85", lw=0.5, zorder=0)
        axis.set_aspect("equal", adjustable="datalim")
        axis.margins(0.12)
        format_axis(axis, xlabel=r"Re $Z$ (mV)", ylabel=r"Im $Z$ (mV)")
        colorbar = figure.colorbar(scatter, ax=axis, pad=0.02)
        colorbar.set_label(r"RF phase $\varphi$ (deg)")
        colorbar.ax.tick_params(labelsize=6)
        style_legend(axis, loc="lower left", fontsize=6, frameon=False)
        save_figure(figure, path)


def _placeholder(root, name, xlabel, ylabel, message):
    """缺数据时的占位图；绘图失败不掩盖摘要里的原因。"""
    try:
        import matplotlib

        matplotlib.use("Agg")
        from ...plotting import format_axis, new_figure, plot_style_context, save_figure

        with plot_style_context("paper"):
            figure, axis = new_figure()
            axis.text(0.5, 0.5, message, ha="center", va="center", transform=axis.transAxes, fontsize=7)
            format_axis(axis, xlabel=xlabel, ylabel=ylabel)
            save_figure(figure, output_path(root, name))
    except Exception:
        pass


def _write_unavailable(summary, root, reason, plot_message):
    summary = dict(summary, error=reason)
    path = output_path(root, run_format.SUMMARY_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    _placeholder(root, run_format.PHASE_RESPONSE_PLOT, "RF phase (deg)", "R (V)", plot_message)
    _placeholder(root, run_format.PHASOR_PLOT, "Re Z (mV)", "Im Z (mV)",
                 "No phasor: phase response data unavailable")
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
    sensitivity = _phase_sensitivity(coefficients)
    sens_phases, sens_values = sensitivity
    best_index = int(np.argmax(sens_values))
    best_phase = float(sens_phases[best_index])
    # |dZ/dφ| 的幅值以 180° 为周期：相差 180° 的两点同样是最快点，方向相反。
    alt_phase = (best_phase + 180.0) % 360.0
    measured_phase, measured_sens = _measured_sensitivity([item["phase_deg"] for item in points], values)
    measured_alt = None if measured_phase is None else (measured_phase + 180.0) % 360.0
    sems = np.array([item["R_sem_v"] for item in points], dtype=float)
    positive = sems[sems > 0]
    sem_median = float(np.median(positive)) if positive.size else 0.0
    signal_low = bool(sem_median > 0 and max(item["R_mean_v"] for item in points) < LOW_SIGNAL_MULTIPLE * sem_median)
    fit_quality = "good" if sem_median == 0 or residual_rms <= POOR_FIT_SIGMA * sem_median else "poor"
    summary.update(mode="phase_response", phases_deg=phases, rows=len(rows), points=points,
                   components=components, residual_rms_v=float(residual_rms),
                   fit_quality=fit_quality,
                   best_phase_deg=best_phase,
                   best_phase_alt_deg=alt_phase,
                   best_phase_source="model_sensitivity",
                   sensitivity_max_v_per_deg=float(sens_values[best_index]),
                   measured_best_phase_deg=measured_phase,
                   measured_best_phase_alt_deg=measured_alt,
                   measured_sensitivity_max_v_per_deg=float(measured_sens),
                   sensitivity_curve=[dict(phase_deg=float(p), v_per_deg=float(s))
                                      for p, s in zip(sens_phases.tolist()[::10], sens_values[::10])],
                   model_v=[dict(phase_deg=float(p), R_v=float(abs(v)))
                            for p, v in zip(model_phases.tolist()[::5], model_values[::5])],
                   signal_low=signal_low,
                   phase_source="scan",
                   demod0_phase_deg=config.get("demod0_phase_deg"))
    check_cancelled()
    _plot(output_path(root, run_format.PHASE_RESPONSE_PLOT), points, model_phases, model_values,
          sensitivity, summary)
    _plot_phasor(output_path(root, run_format.PHASOR_PLOT), points, model_values, coefficients, summary)
    summary_path = output_path(root, run_format.SUMMARY_FILE)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary
