"""B01：从 X/Y 偏置网格解 10 kHz 复振幅的零点；不连接仪器，不改写 raw。

判据：``c2 = X2 + i·Y2`` 对偏移电压线性，解 ``c2 = 0`` 得补偿电压；5 kHz 复振幅 ``c1``
同样拟合，其零点与 2f 零点之差、以及补偿点处的 ``|c1|`` 作为残余奇部的指示量。
全部量以电压（V）和斜率（mV/V）报告，不做线圈常数或磁场换算。
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import yaml

from .models import BperpNullParams
from .run_format import GRID_FILE, NEXT_RUN_FILE, SUMMARY_FILE, require_raw_format
from .stages import ANALYSIS_KEYS

BOOTSTRAP_DRAWS = 600


def output_path(run_dir, name):
    root = Path(run_dir).resolve()
    path = (root / name).resolve()
    if path != root and root not in path.parents:
        raise ValueError("输入或产物路径越出运行目录")
    return path


def load_config(run_dir: Path):
    with output_path(run_dir, "experiment_config.yaml").open(encoding="utf-8") as stream:
        config = yaml.safe_load(stream)
    require_raw_format(config)
    if config.get("completion_status") == "running":
        raise ValueError("本轮采集仍在写入")
    return config, BperpNullParams.from_external(config.get("parameters", {}))


def resolve_options(base, options):
    if unknown := options.keys() - set(ANALYSIS_KEYS):
        raise ValueError("不支持的分析覆盖值：" + "、".join(sorted(unknown)))
    return type(base).from_external(base.to_external() | options)


def signature(path):
    return [path.stat().st_size, hashlib.sha256(path.read_bytes()).hexdigest()] if path.is_file() else None


def fingerprint(run_dir):
    root = Path(run_dir)
    paths = [output_path(root, "experiment_config.yaml"), output_path(root, GRID_FILE)]
    return {p.relative_to(root).as_posix(): signature(p) for p in paths if p.is_file()}


def _read_rows(run_dir):
    path = output_path(run_dir, GRID_FILE)
    with path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if not rows:
        raise ValueError("raw/bias_grid.csv 为空")
    return rows


def _point_means(rows):
    """按 (x_offset_mv, y_offset_mv) 聚合重复读数，返回偏移与复数均值/SEM（V）。"""
    groups = {}
    for row in rows:
        key = (float(row["x_offset_mv"]), float(row["y_offset_mv"]))
        groups.setdefault(key, []).append((complex(float(row["x2_v"]), float(row["y2_v"])),
                                           complex(float(row["x1_v"]), float(row["y1_v"]))))
    xs, ys, c2, c1, sem2, sem1 = [], [], [], [], [], []
    for (x_mv, y_mv), values in sorted(groups.items()):
        array2 = np.array([item[0] for item in values])
        array1 = np.array([item[1] for item in values])
        xs.append(x_mv)
        ys.append(y_mv)
        c2.append(complex(np.mean(array2)))
        c1.append(complex(np.mean(array1)))
        sem2.append(complex(np.std(array2.real, ddof=1) / math.sqrt(len(values)),
                            np.std(array2.imag, ddof=1) / math.sqrt(len(values))) if len(values) > 1 else 0j)
        sem1.append(complex(np.std(array1.real, ddof=1) / math.sqrt(len(values)),
                            np.std(array1.imag, ddof=1) / math.sqrt(len(values))) if len(values) > 1 else 0j)
    return (np.asarray(xs), np.asarray(ys), np.asarray(c2), np.asarray(c1),
            np.asarray(sem2), np.asarray(sem1))


def _fit_plane(x_mv, y_mv, values):
    """复数平面拟合 c = a + b·x + c·y（x、y 以 mV 计）；返回系数、协方差与残差。"""
    design = np.column_stack([np.ones_like(x_mv), x_mv, y_mv])
    coef, *_ = np.linalg.lstsq(design, values, rcond=None)
    residual = values - design @ coef
    dof = max(1, len(values) - design.shape[1])
    # 复高斯：每个实/虚分量的方差为 |r|²/(2·dof)。
    variance = float(np.sum(np.abs(residual) ** 2) / (2.0 * dof))
    cov = variance * np.linalg.inv(design.conj().T @ design)
    return dict(coef=coef, cov=cov, residual=residual,
                residual_rms=float(np.sqrt(np.mean(np.abs(residual) ** 2))),
                residual_max=float(np.max(np.abs(residual))) if residual.size else 0.0,
                variance=variance)


def _null(fit, x_bias_v, y_bias_v):
    """解 c = 0 的偏移（mV）与绝对电压（V），并用参数协方差做蒙特卡洛给不确定度。"""
    coef = fit["coef"]
    a, b, c = coef[0], coef[1], coef[2]
    matrix = np.array([[b.real, c.real], [b.imag, c.imag]])
    if abs(np.linalg.det(matrix)) < 1e-18:
        return None
    offset = np.linalg.solve(matrix, -np.array([a.real, a.imag]))
    cov = fit["cov"]
    block = np.zeros((6, 6))
    block[:3, :3] = cov
    block[3:, 3:] = cov
    rng = np.random.default_rng(20261118)
    draws = rng.multivariate_normal(np.zeros(6), block, size=BOOTSTRAP_DRAWS)
    nulls = []
    for draw in draws:
        pa = a + draw[0] + 1j * draw[3]
        pb = b + draw[1] + 1j * draw[4]
        pc = c + draw[2] + 1j * draw[5]
        m = np.array([[pb.real, pc.real], [pb.imag, pc.imag]])
        if abs(np.linalg.det(m)) < 1e-18:
            continue
        nulls.append(np.linalg.solve(m, -np.array([pa.real, pa.imag])))
    samples = np.array(nulls) if len(nulls) > 2 else np.full((1, 2), np.nan)
    sigma = np.std(samples, axis=0)
    covariance = np.cov(samples.T) if len(nulls) > 2 else np.full((2, 2), np.nan)
    return dict(offset_mv=[float(offset[0]), float(offset[1])],
                cov_offset_mv=[[float(covariance[0, 0]), float(covariance[0, 1])],
                               [float(covariance[1, 0]), float(covariance[1, 1])]],
                voltage_v=[float(x_bias_v + offset[0] / 1000.0), float(y_bias_v + offset[1] / 1000.0)],
                voltage_mv=[float(x_bias_v * 1000.0 + offset[0]), float(y_bias_v * 1000.0 + offset[1])],
                sigma_offset_mv=[float(sigma[0]), float(sigma[1])],
                sigma_voltage_v=[float(sigma[0] / 1000.0), float(sigma[1] / 1000.0)],
                slope_mv_per_v=[complex(b * 1000.0), complex(c * 1000.0)])


def _jsonable(value):
    if isinstance(value, complex):
        return dict(real=float(value.real), imag=float(value.imag))
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    return value


def scan_report(run_dir, params: BperpNullParams):
    """写 results/null_summary.json、next_run.yaml 与两张图；返回摘要字典。"""
    run_dir = Path(run_dir)
    rows = _read_rows(run_dir)
    x_mv, y_mv, c2, c1, sem2, sem1 = _point_means(rows)
    fit2 = _fit_plane(x_mv, y_mv, c2)
    fit1 = _fit_plane(x_mv, y_mv, c1)
    null2 = _null(fit2, params.x_bias_v, params.y_bias_v)
    null1 = _null(fit1, params.x_bias_v, params.y_bias_v)
    if null2 is None or null1 is None:
        raise ValueError("复平面拟合退化，无法解零点；请扩大扫描范围或检查信号")
    signal_ok = bool(np.max(np.abs(c2 - fit2["coef"][0])) > 4.0 * max(float(np.median(np.abs(sem2))), 1e-9))
    angle_deg = _zero_line_angle(fit2)
    outside = not (x_mv.min() <= null2["offset_mv"][0] <= x_mv.max()
                   and y_mv.min() <= null2["offset_mv"][1] <= y_mv.max())
    if outside:
        outside = {
            "x": bool(null2["offset_mv"][0] < x_mv.min() or null2["offset_mv"][0] > x_mv.max()),
            "y": bool(null2["offset_mv"][1] < y_mv.min() or null2["offset_mv"][1] > y_mv.max()),
            "distance_mv": [float(max(0.0, x_mv.min() - null2["offset_mv"][0], null2["offset_mv"][0] - x_mv.max())),
                            float(max(0.0, y_mv.min() - null2["offset_mv"][1], null2["offset_mv"][1] - y_mv.max()))],
        }
    summary = dict(
        experiment_id=run_dir.name, width_us=params.z_pulse_width_us,
        x_bias_mv=params.x_bias_mv, y_bias_mv=params.y_bias_mv,
        x_bias_v=params.x_bias_v, y_bias_v=params.y_bias_v,
        x_offsets_mv=[float(v) for v in x_mv], y_offsets_mv=[float(v) for v in y_mv],
        grid_points=int(len(x_mv)), repeats=int(params.n_repeat),
        zero_line_angle_deg=angle_deg,
        null_outside_grid=outside,
        c2=dict(null=null2, residual_rms_v=fit2["residual_rms"], residual_max_v=fit2["residual_max"],
                sem_median_v=float(np.median(np.abs(sem2))), signal_ok=signal_ok),
        c1=dict(null=null1, residual_rms_v=fit1["residual_rms"], residual_max_v=fit1["residual_max"],
                residual_at_bias=complex(fit1["coef"][0]), residual_at_bias_abs=float(abs(fit1["coef"][0])),
                sem_median_v=float(np.median(np.abs(sem1)))),
        null_offset_difference_mv=[float(null1["offset_mv"][i] - null2["offset_mv"][i]) for i in range(2)],
        noise_v=dict(c2_sem_median=float(np.median(np.abs(sem2))), c1_sem_median=float(np.median(np.abs(sem1)))),
        method="复平面拟合 c=a+b·x+c·y（x、y 为相对 X_BIAS_MV/Y_BIAS_MV 的 mV），取 c=0；"
               "不确定度为参数协方差蒙特卡洛",
        notes=("未做线圈常数换算；5 kHz 零点与 2f 零点之差只作残余奇部（拖尾横向分量）的指示量。"
               + (" 零点落在扫描范围外，结果为线性外推，系统不确定度未计入。" if outside else "")))
    results = output_path(run_dir, "results")
    results.mkdir(parents=True, exist_ok=True)
    (results / "null_summary.json").write_text(json.dumps(_jsonable(summary), ensure_ascii=False, indent=2),
                                               encoding="utf-8")
    half = max(5.0, min(50.0, 3.0 * max(null2["sigma_offset_mv"])))
    next_run = dict(X_BIAS_MV=round(null2["voltage_v"][0] * 1000.0, 3),
                    Y_BIAS_MV=round(null2["voltage_v"][1] * 1000.0, 3),
                    X_SCAN_START_MV=-round(half), X_SCAN_STOP_MV=round(half),
                    X_SCAN_STEP_MV=round(half / 2.0), Y_SCAN_START_MV=-round(half),
                    Y_SCAN_STOP_MV=round(half), Y_SCAN_STEP_MV=round(half / 2.0))
    (results / "next_run.yaml").write_text(yaml.safe_dump(next_run, allow_unicode=True, sort_keys=False),
                                           encoding="utf-8")
    bias_mv = (params.x_bias_mv, params.y_bias_mv)
    _plot_null_map(run_dir, x_mv, y_mv, c2, fit2, null2, filename="results/null_map.png",
                   label="c2 (10 kHz)", bias_mv=bias_mv)
    _plot_null_map(run_dir, x_mv, y_mv, c1, fit1, null1, filename="results/null_map_1f.png",
                   label="c1 (5 kHz)", bias_mv=bias_mv)
    _plot_component_maps(run_dir, x_mv, y_mv, c2, fit2, null2, c1, fit1, null1, bias_mv)
    _plot_complex_plane(run_dir, x_mv, y_mv, c2, fit2)
    _plot_residuals(run_dir, x_mv, y_mv, fit2, bias_mv=bias_mv)
    return _jsonable(summary)


def _zero_line_angle(fit):
    """两条实零线的锐夹角（度）；越小说明 2×2 条件数越差。"""
    b, c = fit["coef"][1], fit["coef"][2]
    n1, n2 = np.array([b.real, c.real]), np.array([b.imag, c.imag])
    denom = float(np.linalg.norm(n1) * np.linalg.norm(n2))
    if denom <= 0:
        return float("nan")
    cosine = min(1.0, abs(float(n1 @ n2)) / denom)
    return float(math.degrees(math.acos(cosine)))


def _ellipse(axis, center, covariance, n_sigma, color, linestyle):
    from matplotlib.patches import Ellipse
    values, vectors = np.linalg.eigh(np.asarray(covariance, dtype=float))
    order = np.argsort(values)[::-1]
    values, vectors = values[order], vectors[:, order]
    if not np.all(np.isfinite(values)) or np.any(values <= 0):
        return
    angle = math.degrees(math.atan2(vectors[1, 0], vectors[0, 0]))
    axis.add_patch(Ellipse(center, width=2 * n_sigma * math.sqrt(values[0]),
                           height=2 * n_sigma * math.sqrt(values[1]), angle=angle, fill=False,
                           edgecolor=color, linestyle=linestyle, lw=1.0, zorder=5))


def _draw_null_panel(axis, figure, x_mv, y_mv, values, fit, null, label, bias_mv, fontsize=7,
                     colorbar_label=None):
    """在 axes 上画一个分量的零点图；横纵轴与零点都用绝对补偿电压（mV）。"""
    from ...plotting import COLOR_OPTIMAL, COLOR_TRAD, format_axis
    a, b, c = fit["coef"][0], fit["coef"][1], fit["coef"][2]
    x_abs, y_abs = x_mv + bias_mv[0], y_mv + bias_mv[1]
    margin_x = max(1.0, 0.15 * (x_abs.max() - x_abs.min()))
    margin_y = max(1.0, 0.15 * (y_abs.max() - y_abs.min()))
    xx = np.linspace(x_abs.min() - margin_x, x_abs.max() + margin_x, 220)
    yy = np.linspace(y_abs.min() - margin_y, y_abs.max() + margin_y, 220)
    mesh_x, mesh_y = np.meshgrid(xx, yy)
    plane = a + b * (mesh_x - bias_mv[0]) + c * (mesh_y - bias_mv[1])
    magnitude_mv = np.abs(plane) * 1000.0
    center = (bias_mv[0] + null["offset_mv"][0], bias_mv[1] + null["offset_mv"][1])
    pad_x = max(4.0, 0.35 * (x_abs.max() - x_abs.min()))
    pad_y = max(4.0, 0.35 * (y_abs.max() - y_abs.min()))
    span_x = max(1.0, x_abs.max() - x_abs.min())
    span_y = max(1.0, y_abs.max() - y_abs.min())
    view_x = (max(min(x_abs.min(), center[0]) - pad_x, x_abs.min() - span_x),
              min(max(x_abs.max(), center[0]) + pad_x, x_abs.max() + span_x))
    view_y = (max(min(y_abs.min(), center[1]) - pad_y, y_abs.min() - span_y),
              min(max(y_abs.max(), center[1]) + pad_y, y_abs.max() + span_y))
    axis.set_facecolor("0.93")
    image = axis.pcolormesh(mesh_x, mesh_y, magnitude_mv, shading="auto", cmap="Blues", alpha=0.65)
    colorbar = figure.colorbar(image, ax=axis, pad=0.02)
    colorbar.set_label(colorbar_label or f"|{label}| (mV)")
    line_x = np.linspace(view_x[0], view_x[1], 2)
    if abs(c.imag) > 1e-15:
        axis.plot(line_x, bias_mv[1] - (a.imag + b.imag * (line_x - bias_mv[0])) / c.imag,
                  "-", color="k", lw=1.1)
    if abs(c.real) > 1e-15:
        axis.plot(line_x, bias_mv[1] - (a.real + b.real * (line_x - bias_mv[0])) / c.real,
                  "--", color="k", lw=1.1)
    axis.scatter(x_abs, y_abs, s=14, facecolors="white", edgecolors="0.25", linewidths=0.6, zorder=4)
    _ellipse(axis, center, null["cov_offset_mv"], 2.0, COLOR_TRAD, "--")
    _ellipse(axis, center, null["cov_offset_mv"], 1.0, COLOR_TRAD, "-")
    axis.plot(center[0], center[1], marker="*", ms=12, color=COLOR_OPTIMAL, markeredgecolor="k",
              markeredgewidth=0.4, zorder=6)
    axis.set_xlim(*view_x)
    axis.set_ylim(*view_y)
    sigma = null["sigma_offset_mv"]
    outside = not (x_mv.min() <= null["offset_mv"][0] <= x_mv.max()
                   and y_mv.min() <= null["offset_mv"][1] <= y_mv.max())
    suffix = "  [outside grid]" if outside else ""
    axis.set_title(f"{label} null ({center[0]:.2f}, {center[1]:.2f}) mV{suffix.replace('  [outside grid]', ' [outside]')}\n"
                   f"sigma ({sigma[0]:.3f}, {sigma[1]:.3f}) mV, {_zero_line_angle(fit):.1f} deg",
                   fontsize=fontsize)
    format_axis(axis, xlabel="X compensation voltage (mV)", ylabel="Y compensation voltage (mV)")


def _plot_null_map(run_dir, x_mv, y_mv, values, fit, null, *, filename, label, bias_mv):
    import matplotlib
    matplotlib.use("Agg")
    from ...plotting import COLOR_OPTIMAL, COLOR_TRAD, format_axis, new_figure, plot_style_context, save_figure

    with plot_style_context("paper"):
        figure, axis = new_figure()
        _draw_null_panel(axis, figure, x_mv, y_mv, values, fit, null, label, bias_mv)
        save_figure(figure, output_path(run_dir, filename))


def _plot_component_maps(run_dir, x_mv, y_mv, c2, fit2, null2, c1, fit1, null1, bias_mv):
    """2f 与 1f 两张零点图并排，便于直接比较两个零点。"""
    import matplotlib
    matplotlib.use("Agg")
    from ...plotting import new_figure, plot_style_context, save_figure

    with plot_style_context("paper"):
        figure, axes = new_figure(ncols=2)
        _draw_null_panel(axes[0], figure, x_mv, y_mv, c2, fit2, null2, "2f", bias_mv, fontsize=6.5,
                         colorbar_label="|c2| at 10 kHz (mV)")
        _draw_null_panel(axes[1], figure, x_mv, y_mv, c1, fit1, null1, "1f", bias_mv, fontsize=6.5,
                         colorbar_label="|c1| at 5 kHz (mV)")
        save_figure(figure, output_path(run_dir, "results/component_maps.png"))


def _plot_complex_plane(run_dir, x_mv, y_mv, c2, fit):
    """复平面图：实测 c2 与拟合仿射网格；原点在图中即为零点。"""
    import matplotlib
    matplotlib.use("Agg")
    from ...plotting import COLOR_OPTIMAL, format_axis, new_figure, plot_style_context, save_figure

    a, b, c = fit["coef"][0], fit["coef"][1], fit["coef"][2]
    with plot_style_context("paper"):
        figure, axis = new_figure()
        for offset in np.unique(y_mv):
            xs = np.linspace(x_mv.min(), x_mv.max(), 40)
            axis.plot((a + b * xs + c * offset).real * 1000, (a + b * xs + c * offset).imag * 1000,
                      "-", color="0.75", lw=0.7, zorder=1)
        for offset in np.unique(x_mv):
            ys = np.linspace(y_mv.min(), y_mv.max(), 40)
            axis.plot((a + b * offset + c * ys).real * 1000, (a + b * offset + c * ys).imag * 1000,
                      "-", color="0.75", lw=0.7, zorder=1)
        axis.scatter(c2.real * 1000, c2.imag * 1000, s=18, color=COLOR_OPTIMAL, zorder=3, label="measured c2")
        axis.plot(0.0, 0.0, marker="+", ms=12, mew=1.6, color="k", zorder=4, label="c2 = 0")
        format_axis(axis, xlabel="Re c2 (mV)", ylabel="Im c2 (mV)")
        axis.legend(loc="best", fontsize=7)
        save_figure(figure, output_path(run_dir, "results/complex_plane.png"))


def _plot_residuals(run_dir, x_mv, y_mv, fit, bias_mv):
    import matplotlib
    matplotlib.use("Agg")
    from ...plotting import COLOR_OPTIMAL, COLOR_TRAD, format_axis, new_figure, plot_style_context, save_figure

    bx, by = float(bias_mv[0]), float(bias_mv[1])
    residual = fit["residual"] * 1000.0
    with plot_style_context("paper"):
        figure, (left, right) = new_figure(nrows=2, sharex=False)
        left.plot(x_mv + bx, residual.real, "o", ms=3, color=COLOR_OPTIMAL, label="Re")
        left.plot(x_mv + bx, residual.imag, "s", ms=3, color=COLOR_TRAD, label="Im")
        right.plot(y_mv + by, residual.real, "o", ms=3, color=COLOR_OPTIMAL)
        right.plot(y_mv + by, residual.imag, "s", ms=3, color=COLOR_TRAD)
        format_axis(left, xlabel="X compensation voltage (mV)", ylabel="residual (mV)")
        format_axis(right, xlabel="Y compensation voltage (mV)", ylabel="residual (mV)")
        left.legend(loc="best", fontsize=7)
        save_figure(figure, output_path(run_dir, "results/residuals.png"))
