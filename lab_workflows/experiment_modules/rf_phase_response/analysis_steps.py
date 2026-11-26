"""B01 离线分析：按理论式 (2.2.2) 拟合对齐后的 Y(φ)，给 K₀、工作点与相量闭曲线。

新格式（raw v3）读取 ``raw/coarse_XX.npz`` 逐相位 XY 帧，按 A03 记录的 ``rotation_deg``
把原始相量旋转到对齐帧（与「射频相位灵敏度」A03 同源做法），再对 Y(φ) 用
``signal_fit.fit_eq222_full_cycle`` 拟合。历史格式（raw v1/v2）继续读取
``raw/phase_response.csv``：带 X/Y 的 v2 目录同样做 (2.2.2) 拟合（无旋转），只有 R/θ 的 v1
目录保留原三分量/最佳相位输出并标 ``fit_mode="three_component"``。

本模块不连接仪器；只读运行目录的配置与原始帧，产物写 ``results/``。
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path
from zipfile import BadZipFile

import numpy as np

from ...experiment_runtime import check_cancelled
from ..z_chain_common import signal_fit
from ..z_chain_common.analysis import decompose
from . import run_format

# 拟合残差 rms_v 不超过对齐后 Y(φ) 波形峰峰值的这个比例即判 good；超过即 poor。
FIT_QUALITY_RMS_FRACTION = 0.05
# (2.2.2) 整周拟合的 5 个自由参数至少需要 6 个相位点。
MIN_PHASES = 6
# 三分量模型至少需要 3 个相位点。
MIN_THREE_COMPONENT_PHASES = 3
# 三分量路径（历史 v1 数据）沿用原质量判据。
POOR_FIT_SIGMA = 3.0
LOW_SIGNAL_MULTIPLE = 5.0
# 模型闭曲线的相位采样步长。
CURVE_STEP_DEG = 0.25
FRAME_ERRORS = (OSError, ValueError, TypeError, KeyError, EOFError, BadZipFile, np.linalg.LinAlgError)


class _MissingData(ValueError):
    """缺帧、缺行或点数不足：写原因、空摘要与占位图，不中断分析。"""


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
    """分步分析用的输入指纹：配置与 raw 下全部原始文件。"""
    root = Path(run_dir)
    raw = output_path(root, "raw")
    paths = [output_path(root, "experiment_config.yaml")]
    if raw.is_dir():
        paths.extend(sorted(path for path in raw.iterdir() if path.is_file()))
    return {path.relative_to(root).as_posix(): signature(path) for path in paths}


def _run_state(run_dir):
    return run_format.read_config(Path(run_dir) / "experiment_config.yaml") or {}


def _base_summary(params, version):
    return dict(experiment_id=run_format.EXPERIMENT_ID,
                raw_format_version=version,
                rf_frequency_hz=params.y_rf_freq_hz, rf_vpp=params.y_rf_vpp,
                y_bias_v=params.y_bias_v,
                pulse_period_us=params.pulse_period_us, peak_current_ma=params.z_peak_current_ma,
                width_us=params.z_pulse_width_us, edge_us=params.z_pulse_edge_us,
                main_field_current_ma=params.main_field_current_ma,
                tail_comp=bool(params.z_tail_comp_enabled), tail_tau_c_us=params.z_tail_tau_c_us,
                demod0_idx=params.demod0_idx, demod0_rate_sa_s=params.demod0_rate_sa_s,
                demod0_tc_s=params.demod0_tc_s, point_duration_s=params.static_point_duration_s,
                n_repeat=params.n_repeat, drift_correction="none")


def _write_summary(root, summary):
    path = output_path(root, run_format.SUMMARY_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def _placeholder(root, name, xlabel, ylabel, message):
    """缺数据时的占位图；绘图失败不掩盖摘要里的原因。"""
    try:
        import matplotlib

        matplotlib.use("Agg")
        from ...plotting import format_axis, new_figure, plot_style_context, save_figure

        with plot_style_context("paper"):
            figure, axis = new_figure()
            axis.text(0.5, 0.5, message, ha="center", va="center", transform=axis.transAxes,
                      fontsize=7)
            format_axis(axis, xlabel=xlabel, ylabel=ylabel)
            save_figure(figure, output_path(root, name))
    except Exception:
        pass


def _write_unavailable(summary, root, reason, plot_message):
    summary = dict(summary, error=reason, fit_mode="unavailable")
    _write_summary(root, summary)
    _placeholder(root, run_format.PHASE_RESPONSE_PLOT, "RF phase (deg)", "Y (mV)", plot_message)
    _placeholder(root, run_format.PHASOR_PLOT, "Re Z (mV)", "Im Z (mV)",
                 "No phasor: phase response data unavailable")
    return summary


# ---------------------------------------------------------------- 历史 CSV（格式 1/2）

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


def _point_complex(point):
    """相位点的复相量：优先用 X/Y 均值，缺 X/Y 时由 R/θ 还原。"""
    if point.get("x_mean_v") is not None and point.get("y_mean_v") is not None:
        return complex(point["x_mean_v"], point["y_mean_v"])
    return point["R_mean_v"] * np.exp(1j * math.radians(point["theta_mean_deg"]))


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


# ---------------------------------------------------------------- (2.2.2) 拟合与闭曲线

def _eq222_curve(fit):
    """(2.2.2) 拟合的 Y(φ) 模型曲线；参数化与 ``signal_fit.eq222_y`` 一致。"""
    grid = np.arange(0.0, 360.0 + CURVE_STEP_DEG / 2.0, CURVE_STEP_DEG)
    values = signal_fit.eq222_y(
        np.deg2rad(grid), fit["y0"], fit["amp"] * fit["r"], fit["r"] ** 2,
        math.radians(fit["phi_c_deg"]), fit["v0"])
    return grid, np.asarray(values, dtype=float)


def _eq222_locus(points, fit, grid_deg):
    """用 (2.2.2) 的 Y 形状加 X 的低阶正交回归拼出闭合相量轨迹。

    X 用 ``[1, sin(φ−φc), cos(φ−φc), y_unit]`` 回归；设计矩阵退化时退化为常数 X，
    闭曲线因此只覆盖 Y 方向，图注与摘要都不把退化情形当拟合成功。
    """
    phases = np.array([point["phase_deg"] for point in points], dtype=float)
    x_measured = np.array([point["x_mean_v"] for point in points], dtype=float)
    phi_data = np.deg2rad(phases)
    phi_c = math.radians(fit["phi_c_deg"])
    v_data = fit["v0"] + fit["r"] * np.sin(phi_data - phi_c)
    y_unit_data = v_data / (1.0 + v_data * v_data)
    design = np.column_stack((np.ones_like(phi_data), np.sin(phi_data - phi_c),
                              np.cos(phi_data - phi_c), y_unit_data))
    x_coef, _, x_rank, _ = np.linalg.lstsq(design, x_measured, rcond=None)
    grid_rad = np.deg2rad(grid_deg)
    v_grid = fit["v0"] + fit["r"] * np.sin(grid_rad - phi_c)
    _, y_grid = _eq222_curve(fit)
    if x_rank == 4:
        x_grid = (x_coef[0] + x_coef[1] * np.sin(grid_rad - phi_c)
                  + x_coef[2] * np.cos(grid_rad - phi_c)
                  + x_coef[3] * (v_grid / (1.0 + v_grid * v_grid)))
        degenerate = False
    else:
        x_grid = np.full_like(grid_rad, float(np.mean(x_measured)))
        degenerate = True
    return np.asarray(x_grid, dtype=float) + 1j * np.asarray(y_grid, dtype=float), degenerate


def _k0_is_undefined(v0, r) -> bool:
    """|v0| ≥ r 时色散零点不在扫描范围内，全局 eq222 的 K0 无定义。"""
    return not (math.isfinite(float(r)) and float(r) > 0.0) or abs(float(v0)) >= abs(float(r))


def _eq222_summary(summary, points, fit, locus, grid_deg, *, work_point_deg, rotation_deg,
                   rotated, phase_cfg):
    """把 (2.2.2) 拟合、工作点与逐点均值写进摘要。"""
    phases = np.array([point["phase_deg"] for point in points], dtype=float)
    y_v = np.array([point["y_mean_v"] for point in points], dtype=float)
    z_aligned = np.array([_point_complex(point) for point in points], dtype=complex)
    y_ptp = float(np.ptp(y_v))
    limit = FIT_QUALITY_RMS_FRACTION * y_ptp
    quality = "good" if math.isfinite(fit["rms_v"]) and fit["rms_v"] <= max(limit, 1e-15) else "poor"
    coefficients, residual = decompose(phases, z_aligned)
    components = {key: _component_record(coefficients[index])
                  for index, key in enumerate(("c0", "c_plus1", "c_minus1"))}
    k0_undefined = _k0_is_undefined(fit["v0"], fit["r"])
    summary.update(
        fit_mode="eq222_full_cycle",
        model="Y(phi) = Y0 + A*v/(1+v^2), v = v0 + r*sin(phi - phi_c)",
        k0_v_per_rad=None if k0_undefined else float(fit["k0_v_per_rad"]),
        k0_fit_v_per_rad=float(fit["k0_v_per_rad"]),
        k0_defined=not k0_undefined,
        k0_degenerate_reason=("拟合静态失衡 |v0| 不小于调制深度 r，色散零点落在扫描范围外，"
                              "K0 不定义；请用 response_v_per_rad 或局部标定" if k0_undefined else ""),
        response_v_per_rad=float(abs(coefficients[1]) + abs(coefficients[2])),
        y0=float(fit["y0"]),
        amp=float(fit["amp"]),
        r=float(fit["r"]),
        v0=float(fit["v0"]),
        phi_c_deg=float(fit["phi_c_deg"]),
        phi_z1_deg=float(fit["phi_z1_deg"]),
        rms_v=float(fit["rms_v"]),
        y_peak_to_peak_v=y_ptp,
        fit_quality=quality,
        fit_quality_rule=f"rms_v <= {FIT_QUALITY_RMS_FRACTION:.0%} * Y(phi) peak-to-peak",
        fit_quality_limit_v=float(limit),
        work_point_deg=work_point_deg,
        rotation_deg=float(rotation_deg),
        rotated=bool(rotated),
        phases_deg=[float(phase) for phase in phases],
        points=points,
        n_phases=len(points),
        components=components,
        residual_rms_three_component_v=float(residual),
        locus_degenerate=bool(locus[1]),
        locus=[dict(phase_deg=float(phi), x_v=float(value.real), y_v=float(value.imag))
               for phi, value in zip(grid_deg.tolist()[::4], locus[0].tolist()[::4])],
        demod0_phase_deg=phase_cfg.get("lockin_phase_deg"),
        lockin=phase_cfg.get("lockin"),
        phase_source="scan",
    )
    return summary


def _load_coarse_points(root, config):
    """读取 raw/coarse_XX.npz 逐相位帧；缺帧或帧不可读时抛 ``_MissingData``。"""
    labels = []
    for item in config.get("data_files") or ():
        if not isinstance(item, str) or not item.startswith(run_format.COARSE_FILE_PREFIX):
            continue
        if not item.endswith(".npz"):
            continue
        stem = Path(item).stem
        if run_format.COARSE_LABEL_PATTERN.fullmatch(stem):
            labels.append(stem)
    if not labels:
        raw = output_path(root, "raw")
        if raw.is_dir():
            labels = [path.stem for path in raw.iterdir()
                      if path.is_file() and path.suffix == ".npz"
                      and run_format.COARSE_LABEL_PATTERN.fullmatch(path.stem)]
    if not labels:
        raise _MissingData("缺少 raw/coarse_*.npz 逐相位帧（未运行 A03 或数据未落盘）")
    labels.sort(key=lambda stem: int(stem.split("_")[1]))
    points, failures = [], []
    for label in labels:
        check_cancelled()
        try:
            metadata, arrays = run_format.read_frame(root, label, "xy")
        except FRAME_ERRORS as exc:
            failures.append(f"{label}: {exc}")
            continue
        phi = metadata.get("burst_command_deg")
        if type(phi) not in (int, float) or not math.isfinite(phi):
            failures.append(f"{label}: 缺少写入的射频指令相位")
            continue
        x_mean = float(np.mean(arrays["x_v"]))
        y_mean = float(np.mean(arrays["y_v"]))
        if not (math.isfinite(x_mean) and math.isfinite(y_mean)):
            failures.append(f"{label}: X/Y 均值非有限")
            continue
        points.append(dict(phase_deg=float(phi % 360.0), x_raw_v=x_mean, y_raw_v=y_mean,
                           samples=int(arrays["x_v"].size)))
    if not points:
        raise _MissingData("raw/coarse_*.npz 均不可用：" + "；".join(failures[:3]))
    return points, failures


def _align_points(points, rotation_deg):
    """按 A03 的 rotation_deg 把原始相量旋转到对齐帧，并逐点给 X/Y/R/θ 均值。"""
    factor = np.exp(-1j * np.deg2rad(float(rotation_deg)))
    aligned = []
    for point in points:
        z = complex(point["x_raw_v"], point["y_raw_v"]) * factor
        aligned.append(dict(phase_deg=float(point["phase_deg"]),
                            x_mean_v=float(z.real), y_mean_v=float(z.imag),
                            R_mean_v=float(abs(z)),
                            theta_mean_deg=float(math.degrees(math.atan2(z.imag, z.real))),
                            samples=int(point["samples"])))
    return aligned


def _state_phase_config(config):
    return config.get("phase_response") if isinstance(config.get("phase_response"), dict) else {}


# ---------------------------------------------------------------- 绘图

def _plot_eq222(path, points, grid_deg, y_curve, locus, summary):
    """(a) 对齐 Y(φ) 散点与 (2.2.2) 拟合；(b) R(φ) 与模型 |Z|。paper 样式、英文图注。"""
    import matplotlib

    matplotlib.use("Agg")

    from ...plotting import (COLOR_OPTIMAL, COLOR_TRAD, format_axis, new_figure,
                             plot_style_context, save_figure, style_legend)

    phases = np.array([point["phase_deg"] for point in points], dtype=float)
    y_mv = np.array([point["y_mean_v"] for point in points]) * 1e3
    r_mv = np.array([point["R_mean_v"] for point in points]) * 1e3
    with plot_style_context("paper"):
        figure, (top, bottom) = new_figure(nrows=2, sharex=True,
                                           gridspec_kw={"height_ratios": [3, 2]})
        top.plot(phases, y_mv, "o", ms=3.2, mfc="none", mew=0.8, color=COLOR_OPTIMAL,
                 label=r"measured $Y(\varphi)$")
        top.plot(grid_deg, y_curve * 1e3, "-", lw=1.0, color=COLOR_TRAD,
                 label=r"Eq. (2.2.2) fit")
        if summary.get("work_point_deg") is not None:
            top.axvline(float(summary["work_point_deg"]), color="0.4", ls=":", lw=0.8,
                        label=f"work point {float(summary['work_point_deg']):.1f}$^\\circ$")
        top.axvline(float(summary["phi_z1_deg"]), color="0.55", ls=(0, (1, 1)), lw=0.8,
                    label=r"$\varphi_{z1}$")
        top.set_xlim(0.0, 360.0)
        top.set_xticks(np.arange(0.0, 361.0, 45.0))
        top.text(0.01, 1.01, "(a)", transform=top.transAxes, fontsize=7, va="bottom")
        format_axis(top, xlabel="", ylabel=r"$Y$ (mV)")
        style_legend(top, loc="best", fontsize=5.8, frameon=False)
        bottom.plot(phases, r_mv, "o", ms=2.8, mfc="none", mew=0.7, color=COLOR_OPTIMAL,
                    label=r"measured $R(\varphi)$")
        bottom.plot(grid_deg, np.abs(locus) * 1e3, "-", lw=1.0, color=COLOR_TRAD,
                    label=r"Eq. (2.2.2) $|Z(\varphi)|$")
        bottom.set_xlim(0.0, 360.0)
        bottom.set_xticks(np.arange(0.0, 361.0, 45.0))
        bottom.text(0.01, 1.01, "(b)", transform=bottom.transAxes, fontsize=7, va="bottom")
        format_axis(bottom, xlabel=r"RF phase $\varphi$ (deg)", ylabel=r"$R$ (mV)")
        style_legend(bottom, loc="best", fontsize=5.8, frameon=False)
        save_figure(figure, path)


def _plot_eq222_phasor(path, points, grid_deg, locus, summary):
    """相量轨迹：实测对齐相量、式 (2.2.2) 闭曲线与工作点；颜色表示射频相位。"""
    import matplotlib

    matplotlib.use("Agg")

    from ...plotting import (COLOR_OPTIMAL, COLOR_TRAD, format_axis, new_figure,
                             plot_style_context, save_figure, style_legend)

    phases = np.array([point["phase_deg"] for point in points], dtype=float)
    z_mv = np.array([_point_complex(point) for point in points]) * 1e3
    locus_mv = np.asarray(locus, dtype=complex) * 1e3
    with plot_style_context("paper"):
        figure, axis = new_figure(kind="square")
        axis.plot(locus_mv.real, locus_mv.imag, "-", color="0.72", lw=1.2, zorder=1,
                  label=r"Eq. (2.2.2) locus")
        axis.plot(z_mv.real, z_mv.imag, "-", color=COLOR_OPTIMAL, lw=0.5, alpha=0.45, zorder=2)
        scatter = axis.scatter(z_mv.real, z_mv.imag, c=phases, cmap="viridis", s=16, zorder=3,
                               edgecolors="k", linewidths=0.25, label="measured")
        step = max(1, len(phases) // 8)
        for index in range(0, len(phases) - 1, step):
            axis.annotate("", xy=(z_mv[index + 1].real, z_mv[index + 1].imag),
                          xytext=(z_mv[index].real, z_mv[index].imag),
                          arrowprops=dict(arrowstyle="-|>", color="0.25", lw=0.5,
                                          shrinkA=0.0, shrinkB=0.0), zorder=2)
        if summary.get("work_point_deg") is not None:
            work = float(summary["work_point_deg"]) % 360.0
            index = int(np.argmin(np.abs(grid_deg - work)))
            axis.plot(locus_mv[index].real, locus_mv[index].imag, marker="*", ms=9,
                      color=COLOR_TRAD, zorder=5,
                      label=f"work point {work:.1f}$^\\circ$")
        axis.plot(0.0, 0.0, marker="+", ms=9, mew=1.0, color="k", zorder=4, label="origin")
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


def _plot_three_component(path, points, model_phases, model_values, sensitivity, summary):
    """历史 v1 数据的三面板图：(a) R(φ)；(b) θ(φ)；(c) |dZ/dφ| 与实测差分。"""
    import matplotlib

    matplotlib.use("Agg")

    from ...plotting import (COLOR_OPTIMAL, COLOR_TRAD, format_axis, new_figure,
                             plot_style_context, save_figure, style_legend)

    phases = np.array([item["phase_deg"] for item in points], dtype=float)
    values = np.array([item["R_mean_v"] for item in points], dtype=float)
    errors = np.array([item["R_sem_v"] for item in points], dtype=float)
    thetas = np.array([item["theta_mean_deg"] for item in points], dtype=float)
    theta_errors = np.array([item["theta_sem_deg"] for item in points], dtype=float)
    z_values = np.array([_point_complex(item) for item in points], dtype=complex)
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


def _plot_three_component_phasor(path, points, model_values, coefficients, summary):
    """历史 v1 数据的相量图：实测 Z(φ) 轨迹、三分量模型闭曲线、最佳工作点。"""
    import matplotlib

    matplotlib.use("Agg")

    from ...plotting import (COLOR_OPTIMAL, COLOR_TRAD, format_axis, new_figure,
                             plot_style_context, save_figure, style_legend)

    phases = np.array([item["phase_deg"] for item in points], dtype=float)
    z_mv = np.array([_point_complex(item) for item in points]) * 1e3
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


# ---------------------------------------------------------------- 三条分析路径

def _report_eq222_with_rotation(root, config, summary, points_raw):
    phase_cfg = _state_phase_config(config)
    rotation = phase_cfg.get("rotation_deg")
    recorded = isinstance(rotation, (int, float)) and math.isfinite(float(rotation))
    rotation_deg = float(rotation) if recorded else 0.0
    rotated = bool(recorded and abs(rotation_deg) > 1e-12)
    work_point = phase_cfg.get("burst_command_deg")
    if not isinstance(work_point, (int, float)) or not math.isfinite(float(work_point)):
        work_point = phase_cfg.get("carrier_command_deg")
    work_point_deg = float(work_point) if isinstance(work_point, (int, float)) and math.isfinite(
        float(work_point)) else None
    points = _align_points(points_raw, rotation_deg)
    phases = np.array([point["phase_deg"] for point in points], dtype=float)
    if len(points) < MIN_PHASES:
        raise _MissingData(f"有效相位点只有 {len(points)} 个，(2.2.2) 整周拟合至少需要 {MIN_PHASES} 个")
    y_v = np.array([point["y_mean_v"] for point in points], dtype=float)
    try:
        fit = signal_fit.fit_eq222_full_cycle(
            phases, y_v, phi_c_init_deg=0.0 if work_point_deg is None else work_point_deg)
    except (ValueError, np.linalg.LinAlgError) as exc:
        raise _MissingData(f"式 (2.2.2) 整周拟合失败：{exc}") from exc
    grid_deg, y_curve = _eq222_curve(fit)
    locus = _eq222_locus(points, fit, grid_deg)
    summary = _eq222_summary(summary, points, fit, locus, grid_deg,
                             work_point_deg=work_point_deg, rotation_deg=rotation_deg,
                             rotated=rotated, phase_cfg=phase_cfg)
    check_cancelled()
    _plot_eq222(output_path(root, run_format.PHASE_RESPONSE_PLOT), points, grid_deg, y_curve,
                locus[0], summary)
    _plot_eq222_phasor(output_path(root, run_format.PHASOR_PLOT), points, grid_deg, locus[0], summary)
    return _write_summary(root, summary)


def _report_eq222_legacy_csv(root, config, summary):
    path = output_path(root, run_format.PHASE_RESPONSE_FILE)
    if not path.is_file():
        raise _MissingData("缺少 raw/phase_response.csv（未运行扫描）")
    rows = _read_rows(path)
    if not rows:
        raise _MissingData("phase_response.csv 无有效行")
    points = _phase_summary(rows)
    if len(points) < MIN_PHASES:
        raise _MissingData(f"有效相位点只有 {len(points)} 个，(2.2.2) 整周拟合至少需要 {MIN_PHASES} 个")
    phases = np.array([point["phase_deg"] for point in points], dtype=float)
    y_v = np.array([_point_complex(point).imag for point in points], dtype=float)
    try:
        fit = signal_fit.fit_eq222_full_cycle(phases, y_v, phi_c_init_deg=0.0)
    except (ValueError, np.linalg.LinAlgError) as exc:
        raise _MissingData(f"式 (2.2.2) 整周拟合失败：{exc}") from exc
    eq_points = []
    for point in points:
        z = _point_complex(point)
        eq_points.append(dict(phase_deg=point["phase_deg"], x_mean_v=float(z.real),
                              y_mean_v=float(z.imag), R_mean_v=float(abs(z)),
                              theta_mean_deg=float(math.degrees(math.atan2(z.imag, z.real))),
                              samples=point["samples"]))
    grid_deg, y_curve = _eq222_curve(fit)
    locus = _eq222_locus(eq_points, fit, grid_deg)
    # 历史 v2 目录没有 rotation_deg：按无旋转处理，明确写进摘要。
    summary = _eq222_summary(summary, eq_points, fit, locus, grid_deg, work_point_deg=None,
                             rotation_deg=0.0, rotated=False, phase_cfg={})
    summary.update(rows=len(rows), phase_source="csv")
    check_cancelled()
    _plot_eq222(output_path(root, run_format.PHASE_RESPONSE_PLOT), eq_points, grid_deg, y_curve,
                locus[0], summary)
    _plot_eq222_phasor(output_path(root, run_format.PHASOR_PLOT), eq_points, grid_deg, locus[0], summary)
    return _write_summary(root, summary)


def _report_three_component(root, config, summary):
    """历史 v1（只有 R/θ）：保留原三分量分解与最佳读相位输出。"""
    path = output_path(root, run_format.PHASE_RESPONSE_FILE)
    if not path.is_file():
        raise _MissingData("缺少 raw/phase_response.csv（未运行扫描）")
    rows = _read_rows(path)
    if not rows:
        raise _MissingData("phase_response.csv 无有效行")
    points = _phase_summary(rows)
    phases = sorted({row["phase_deg"] for row in rows})
    if len(phases) < MIN_THREE_COMPONENT_PHASES:
        raise _MissingData(f"只有 {len(phases)} 个相位，三分量分解至少需要 "
                           f"{MIN_THREE_COMPONENT_PHASES} 个")
    values = [_point_complex(point) for point in points]
    coefficients, residual_rms = decompose(phases, values)
    components = _repeat_components(rows, phases)
    if not components:
        components = {key: _component_record(coefficients[index])
                      for index, key in enumerate(("c0", "c_plus1", "c_minus1"))}
    model_phases, model_values = _model_curve(coefficients)
    sens_phases, sens_values = _phase_sensitivity(coefficients)
    best_index = int(np.argmax(sens_values))
    best_phase = float(sens_phases[best_index])
    alt_phase = (best_phase + 180.0) % 360.0
    measured_phase, measured_sens = _measured_sensitivity([point["phase_deg"] for point in points],
                                                          values)
    measured_alt = None if measured_phase is None else (measured_phase + 180.0) % 360.0
    sems = np.array([point["R_sem_v"] for point in points], dtype=float)
    positive = sems[sems > 0]
    sem_median = float(np.median(positive)) if positive.size else 0.0
    signal_low = bool(sem_median > 0
                      and max(point["R_mean_v"] for point in points) < LOW_SIGNAL_MULTIPLE * sem_median)
    fit_quality = "good" if sem_median == 0 or residual_rms <= POOR_FIT_SIGMA * sem_median else "poor"
    summary.update(
        fit_mode="three_component",
        mode="phase_response",
        model="Z(phi) = c0 + c+1*exp(i*phi) + c-1*exp(-i*phi)",
        rows=len(rows), phases_deg=phases, points=points, n_phases=len(phases),
        components=components, residual_rms_v=float(residual_rms), fit_quality=fit_quality,
        response_v_per_rad=float(abs(coefficients[1]) + abs(coefficients[2])),
        best_phase_deg=best_phase, best_phase_alt_deg=alt_phase,
        best_phase_source="model_sensitivity",
        sensitivity_max_v_per_deg=float(sens_values[best_index]),
        measured_best_phase_deg=measured_phase, measured_best_phase_alt_deg=measured_alt,
        measured_sensitivity_max_v_per_deg=float(measured_sens),
        sensitivity_curve=[dict(phase_deg=float(p), v_per_deg=float(s))
                           for p, s in zip(sens_phases.tolist()[::10], sens_values[::10])],
        model_v=[dict(phase_deg=float(p), R_v=float(abs(v)))
                 for p, v in zip(model_phases.tolist()[::5], model_values[::5])],
        signal_low=signal_low, phase_source="csv",
    )
    check_cancelled()
    _plot_three_component(output_path(root, run_format.PHASE_RESPONSE_PLOT), points, model_phases,
                          model_values, (sens_phases, sens_values), summary)
    _plot_three_component_phasor(output_path(root, run_format.PHASOR_PLOT), points, model_values,
                                 coefficients, summary)
    return _write_summary(root, summary)


def scan_report(run_dir, params):
    """B01：按原始格式分派到 (2.2.2) 整周拟合或历史三分量分解，写摘要与图。"""
    root = Path(run_dir)
    output_path(root, "results").mkdir(parents=True, exist_ok=True)
    config = _run_state(root)
    version = config.get("raw_format_version", 1)
    summary = _base_summary(params, version)
    try:
        if version >= 3:
            points_raw, failures = _load_coarse_points(root, config)
            summary["frame_failures"] = failures
            return _report_eq222_with_rotation(root, config, summary, points_raw)
        if version >= 2:
            return _report_eq222_legacy_csv(root, config, summary)
        return _report_three_component(root, config, summary)
    except _MissingData as exc:
        return _write_unavailable(summary, root, str(exc),
                                  "No phase response: raw data unavailable")
