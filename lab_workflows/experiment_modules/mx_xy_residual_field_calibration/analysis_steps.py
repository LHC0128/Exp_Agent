"""历史资格、网格对齐、科学数值接线与内容指纹；不连接仪器。"""
from __future__ import annotations
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path
import csv
import hashlib
import json
import math
import numpy as np
import yaml
from ...experiment_runtime import check_cancelled
from lab_workflows.workflow_state import write_state
from .models import MxXYResidualFieldCalibrationParams
from .fitting import fit_bloch_2d, fit_dispersive_line, bloch_2d_surface
from . import run_format


class AnalysisQualityError(ValueError):
    """无效原始数据或拟合资格；不伪造补偿值。"""


def plain(value):
    if isinstance(value, dict):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [plain(item) for item in value]
    if isinstance(value, np.generic):
        return plain(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def contained(path, root):
    path = Path(path)
    if not path.resolve().is_relative_to(Path(root).resolve()) or path.resolve() != path.absolute():
        raise AnalysisQualityError(f"路径经过目录联接或越出运行目录: {path}")
    return path


def signature(path):
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return [path.stat().st_size, digest.hexdigest()]


def load_config(run_dir):
    path = contained(run_dir / "experiment_config.yaml", run_dir)
    config = yaml.safe_load(path.read_text(encoding="utf8")) or {}
    if reason := run_format.analysis_block_reason(config):
        raise AnalysisQualityError(reason)
    params = MxXYResidualFieldCalibrationParams.from_external(config["parameters"],
        schema_version=int(config.get("schema_version", 1)))
    if errors := params.validate_model():
        raise AnalysisQualityError("；".join(errors))
    return config, params


def resolve_options(params, options):
    if options:
        raise ValueError("本实验没有独立分析覆盖参数")
    return params


def fingerprint(run_dir):
    config, _ = load_config(run_dir)
    config = {key: value for key, value in config.items() if key not in ("analysis_status", "analysis_error")}
    raw = contained(run_dir / "raw", run_dir)
    return dict(config_sha256=hashlib.sha256(json.dumps(config, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
        raw_files={p.relative_to(run_dir).as_posix(): signature(contained(p, run_dir))
                   for p in sorted(raw.rglob("*")) if p.is_file()}, analysis_version="xy-residual-v1")


def load_data(run_dir):
    config, params = load_config(run_dir)
    raw = contained(run_dir / "raw", run_dir)
    with np.load(contained(raw / "xy_residual_scan_axes.npz", run_dir), allow_pickle=False) as axes:
        x_axis, y_axis = np.asarray(axes["x_field_v"], dtype=float), np.asarray(axes["y_field_v"], dtype=float)
        if str(axes["order"]) != "x_outer_y_forward":
            raise AnalysisQualityError("扫描顺序不是 X 外层、Y 正向")
    for axis, start, stop, count in ((x_axis, params.x_field_start_v, params.x_field_stop_v, params.x_field_points),
                                    (y_axis, params.y_field_start_v, params.y_field_stop_v, params.y_field_points)):
        if axis.shape != (count,) or not np.allclose(axis, np.linspace(start, stop, count), rtol=1e-12, atol=1e-15):
            raise AnalysisQualityError("扫描轴与当轮参数快照不一致")
    grouped = defaultdict(list)
    seen = set()
    for path in sorted(raw.glob("grid_X*_Y*_R*.npz")):
        check_cancelled()
        with np.load(contained(path, run_dir), allow_pickle=False) as values:
            ix, iy, repeat = (int(values[key]) for key in ("x_index", "y_index", "repeat_index"))
            seq = int(values["sequence_index"])
            expected = (ix * y_axis.size + iy) * params.point_repeats + repeat
            if (not 0 <= ix < x_axis.size or not 0 <= iy < y_axis.size or not 0 <= repeat < params.point_repeats
                    or (ix, iy, repeat) in seen or seq != expected):
                raise AnalysisQualityError("网格索引、重复或采集顺序无效")
            seen.add((ix, iy, repeat))
            if (str(values["phase"]) != "grid" or not bool(values["x_output_on"]) or not bool(values["y_output_on"])
                    or not np.isclose(float(values["x_field_v"]), x_axis[ix], rtol=0, atol=1e-12)
                    or not np.isclose(float(values["y_field_v"]), y_axis[iy], rtol=0, atol=1e-12)
                    or not np.isclose(float(values["main_field_ma"]), params.scan_main_field_ma, rtol=0, atol=1e-12)):
                raise AnalysisQualityError("网格电压、主场或输出状态与快照不一致")
            t, voltage = np.asarray(values["time_s"], dtype=float), np.asarray(values["voltage_v"], dtype=float)
            if (t.ndim != 1 or voltage.shape != t.shape or t.size < params.scope_minimum_points
                    or not np.all(np.isfinite(t)) or not np.all(np.isfinite(voltage)) or np.any(np.diff(t) <= 0)):
                raise AnalysisQualityError("波形和时间轴无效")
            dt = float(np.median(np.diff(t)))
            if not np.isclose(float(values["actual_rate_sa_s"]), 1 / dt, rtol=1e-6, atol=1e-6):
                raise AnalysisQualityError("实际采样率与波形时间轴不一致")
            mean, std, midpoint = (float(values[key]) for key in ("pd_mean_v", "pd_std_v", "capture_midpoint_unix_s"))
            if (not math.isfinite(midpoint) or not np.isclose(mean, np.mean(voltage), rtol=1e-10, atol=1e-14)
                    or not np.isclose(std, np.std(voltage), rtol=1e-10, atol=1e-14)):
                raise AnalysisQualityError("PD 统计量或采集时刻无效")
            grouped[(ix, iy)].append(dict(repeat_index=repeat, time_unix_s=midpoint, pd_mean_v=mean))
    shape = (x_axis.size, y_axis.size)
    mean_matrix, point_std_matrix, time_matrix = (np.full(shape, np.nan) for _ in range(3))
    repeat_count_matrix = np.zeros(shape, dtype=int)
    for ix in range(x_axis.size):
        for iy in range(y_axis.size):
            captures = sorted(grouped.get((ix, iy), []), key=lambda item: item["repeat_index"])
            if len(captures) != params.point_repeats:
                raise AnalysisQualityError(f"网格 X{ix:03d}/Y{iy:03d} 的重复数与 POINT_REPEATS 不一致")
            time_matrix[ix, iy] = float(np.mean([item["time_unix_s"] for item in captures]))
            means = [item["pd_mean_v"] for item in captures]
            mean_matrix[ix, iy] = float(np.mean(means))
            point_std_matrix[ix, iy] = float(np.std(means, ddof=1)) if len(means) > 1 else 0.0
            repeat_count_matrix[ix, iy] = len(captures)
    return dict(config=config, params=params, x_axis=x_axis, y_axis=y_axis, mean_matrix=mean_matrix,
        time_matrix=time_matrix, point_std_matrix=point_std_matrix, repeat_count_matrix=repeat_count_matrix)


def input_summary(data):
    return dict(grid_shape=list(data["mean_matrix"].shape), captures=int(np.sum(data["repeat_count_matrix"])),
        raw_format_version=data["config"].get("raw_format_version"), acquisition_signal="SDS CH1 DC raw waveform mean")


def write_reports(results_dir, summary):
    with (results_dir / "analysis.yaml").open("w", encoding="utf8") as stream:
        yaml.safe_dump(summary, stream, allow_unicode=True, sort_keys=False)
    write_state(results_dir / "analysis.json", summary)


def evaluate(run_dir, data=None):
    data = data if data is not None else load_data(run_dir)
    config, params = data["config"], data["params"]
    x_axis, y_axis = data["x_axis"], data["y_axis"]
    mean_matrix, time_matrix = data["mean_matrix"], data["time_matrix"]
    point_std_matrix, repeat_count_matrix = data["point_std_matrix"], data["repeat_count_matrix"]
    results_dir = run_dir / "results"
    rows = []
    plot_files = []
    if not np.all(np.isfinite(mean_matrix)):
        raise RuntimeError("PD 均值矩阵包含无效值")
    check_cancelled()
    bloch_fit = fit_bloch_2d(
        x_axis,
        y_axis,
        mean_matrix,
        time_matrix_unix_s=time_matrix,
    )
    if not bloch_fit.valid_for_selection:
        raise AnalysisQualityError(f"二维 Bloch 拟合失败: {bloch_fit.message}")
    x_grid, y_grid = np.meshgrid(x_axis, y_axis, indexing="ij")
    bloch_fit_matrix = bloch_2d_surface(
        x_grid,
        y_grid,
        bloch_fit.offset_v,
        bloch_fit.amplitude_v,
        bloch_fit.balance_x_v,
        bloch_fit.balance_y_v,
        bloch_fit.width_x_v,
        bloch_fit.width_y_v,
    )
    fitted_drift_matrix = bloch_fit.linear_drift_v_per_s * (
        time_matrix - bloch_fit.time_origin_unix_s
    )
    drift_corrected_mean_matrix = mean_matrix - fitted_drift_matrix
    bloch_total_fit_matrix = bloch_fit_matrix + fitted_drift_matrix
    bloch_residual_matrix = mean_matrix - bloch_total_fit_matrix
    best_x_index = int(np.argmin(np.abs(x_axis - bloch_fit.balance_x_v)))
    best_y_index = int(np.argmin(np.abs(y_axis - bloch_fit.balance_y_v)))
    best_x_v = float(x_axis[best_x_index])
    best_y_v = float(y_axis[best_y_index])

    dispersive_fits = []
    for x_index in range(x_axis.size):
        check_cancelled()
        dispersive_fits.append(fit_dispersive_line(y_axis, drift_corrected_mean_matrix[x_index, :]))
    valid_fit_mask = np.asarray(
        [item.valid_for_selection for item in dispersive_fits], dtype=bool
    )
    fit_csv_name = "y_dispersive_fits.csv"
    fit_rows = []
    for x_index, fit in enumerate(dispersive_fits):
        fit_rows.append(
            {
                "x_index": x_index,
                "x_field_v": float(x_axis[x_index]),
                **asdict(fit),
            }
        )
    with (results_dir / fit_csv_name).open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(fit_rows[0]))
        writer.writeheader()
        writer.writerows(fit_rows)
    fit_slopes = np.asarray(
        [item.central_slope_v_per_v for item in dispersive_fits], dtype=float
    )
    fit_amplitudes = np.asarray(
        [abs(item.amplitude_v) for item in dispersive_fits], dtype=float
    )
    fit_linewidths = np.asarray(
        [item.linewidth_v for item in dispersive_fits], dtype=float
    )
    dispersive_best_x_index: int | None = None
    amplitude_best_x_index: int | None = None
    linewidth_best_x_index: int | None = None
    dispersive_best_fit: DispersiveFitResult | None = None
    dispersive_best_y_index: int | None = None
    metric_extrema_agree = False
    if np.any(valid_fit_mask):
        dispersive_best_x_index = int(
            np.argmax(np.where(valid_fit_mask, fit_slopes, -np.inf))
        )
        amplitude_best_x_index = int(
            np.argmax(np.where(valid_fit_mask, fit_amplitudes, -np.inf))
        )
        linewidth_best_x_index = int(
            np.argmin(np.where(valid_fit_mask, fit_linewidths, np.inf))
        )
        metric_extrema_agree = bool(
            dispersive_best_x_index
            == amplitude_best_x_index
            == linewidth_best_x_index
        )
        dispersive_best_fit = dispersive_fits[dispersive_best_x_index]
        dispersive_best_y_index = int(
            np.argmin(np.abs(y_axis - dispersive_best_fit.center_y_v))
        )

    for x_index in range(x_axis.size):
        for y_index in range(y_axis.size):
            rows.append(
                {
                    "x_index": x_index,
                    "y_index": y_index,
                    "x_field_v": float(x_axis[x_index]),
                    "y_field_v": float(y_axis[y_index]),
                    "capture_time_unix_s": float(time_matrix[x_index, y_index]),
                    "pd_mean_v": float(mean_matrix[x_index, y_index]),
                    "point_repeat_std_v": float(point_std_matrix[x_index, y_index]),
                    "fitted_linear_drift_v": float(
                        fitted_drift_matrix[x_index, y_index]
                    ),
                    "drift_corrected_pd_mean_v": float(
                        drift_corrected_mean_matrix[x_index, y_index]
                    ),
                    "bloch_fitted_pd_mean_v": float(
                        bloch_fit_matrix[x_index, y_index]
                    ),
                    "bloch_total_fitted_pd_mean_v": float(
                        bloch_total_fit_matrix[x_index, y_index]
                    ),
                    "bloch_residual_v": float(
                        bloch_residual_matrix[x_index, y_index]
                    ),
                    "repeat_count": int(repeat_count_matrix[x_index, y_index]),
                }
            )

    csv_name = "xy_residual_calibration.csv"
    with (results_dir / csv_name).open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    npz_name = "calibration_results.npz"
    np.savez(
        results_dir / npz_name,
        x_field_v=x_axis,
        y_field_v=y_axis,
        pd_mean_v=mean_matrix,
        point_repeat_std_v=point_std_matrix,
        acquisition_time_unix_s=time_matrix,
        fitted_linear_drift_v=fitted_drift_matrix,
        drift_corrected_pd_mean_v=drift_corrected_mean_matrix,
        bloch_fit_surface_v=bloch_fit_matrix,
        bloch_total_fit_v=bloch_total_fit_matrix,
        bloch_residual_v=bloch_residual_matrix,
        best_x_index=np.int64(best_x_index),
        best_y_index=np.int64(best_y_index),
        best_x_field_v=np.float64(best_x_v),
        best_y_field_v=np.float64(best_y_v),
        fitted_x_balance_v=np.float64(bloch_fit.balance_x_v),
        fitted_y_balance_v=np.float64(bloch_fit.balance_y_v),
        bloch_offset_v=np.float64(bloch_fit.offset_v),
        bloch_amplitude_v=np.float64(bloch_fit.amplitude_v),
        bloch_width_x_v=np.float64(bloch_fit.width_x_v),
        bloch_width_y_v=np.float64(bloch_fit.width_y_v),
        bloch_linear_drift_v_per_s=np.float64(bloch_fit.linear_drift_v_per_s),
        bloch_time_origin_unix_s=np.float64(bloch_fit.time_origin_unix_s),
        bloch_rmse_v=np.float64(bloch_fit.rmse_v),
        bloch_r_squared=np.float64(bloch_fit.r_squared),
        dispersive_fit_valid=valid_fit_mask,
        dispersive_fit_offset_v=np.asarray([item.offset_v for item in dispersive_fits]),
        dispersive_fit_amplitude_v=np.asarray([item.amplitude_v for item in dispersive_fits]),
        dispersive_fit_center_y_v=np.asarray([item.center_y_v for item in dispersive_fits]),
        dispersive_fit_gamma_v=np.asarray([item.gamma_v for item in dispersive_fits]),
        dispersive_fit_linewidth_v=fit_linewidths,
        dispersive_fit_central_slope_v_per_v=fit_slopes,
        dispersive_fit_rmse_v=np.asarray([item.rmse_v for item in dispersive_fits]),
        dispersive_fit_r_squared=np.asarray([item.r_squared for item in dispersive_fits]),
        dispersive_crosscheck_x_index=np.int64(
            -1 if dispersive_best_x_index is None else dispersive_best_x_index
        ),
        dispersive_crosscheck_y_balance_v=np.float64(
            np.nan
            if dispersive_best_fit is None
            else dispersive_best_fit.center_y_v
        ),
    )

    quality_warnings: list[str] = []
    x_grid_step = float(np.median(np.diff(x_axis)))
    y_grid_step = float(np.median(np.diff(y_axis)))
    if bloch_fit.r_squared < 0.8:
        quality_warnings.append("bloch_r_squared_below_0.8")
    if bloch_fit.balance_x_near_boundary:
        quality_warnings.append("fitted_x_balance_near_scan_boundary")
    if bloch_fit.balance_y_near_boundary:
        quality_warnings.append("fitted_y_balance_near_scan_boundary")
    if bloch_fit.width_x_near_boundary:
        quality_warnings.append("fitted_x_width_near_fit_boundary")
    if bloch_fit.width_y_near_boundary:
        quality_warnings.append("fitted_y_width_near_fit_boundary")
    dispersive_crosscheck_x_v: float | None = None
    dispersive_crosscheck_y_v: float | None = None
    dispersive_x_difference_v: float | None = None
    dispersive_y_difference_v: float | None = None
    if dispersive_best_x_index is None or dispersive_best_fit is None:
        quality_warnings.append("no_valid_per_x_dispersive_crosscheck")
    else:
        dispersive_crosscheck_x_v = float(x_axis[dispersive_best_x_index])
        dispersive_crosscheck_y_v = float(dispersive_best_fit.center_y_v)
        dispersive_x_difference_v = (
            dispersive_crosscheck_x_v - bloch_fit.balance_x_v
        )
        dispersive_y_difference_v = (
            dispersive_crosscheck_y_v - bloch_fit.balance_y_v
        )
        if abs(dispersive_x_difference_v) > x_grid_step:
            quality_warnings.append("bloch_and_dispersive_x_differ_by_more_than_one_step")
        if abs(dispersive_y_difference_v) > y_grid_step:
            quality_warnings.append("bloch_and_dispersive_y_differ_by_more_than_one_step")

    summary = {
        "experiment_id": config.get("experiment_id", "mx-xy-residual-field-calibration"),
        "criterion": "global 2D steady-state Bloch fit of raw PD mean",
        "model": "pd_mean = offset + linear_drift*(t-t0) + amplitude*v/(1+u^2+v^2)",
        "reference_strategy": "none",
        "bz_assumption": "approximately_zero",
        "grid_order": "x_outer_y_forward",
        "fitted_x_balance_v": bloch_fit.balance_x_v,
        "fitted_y_balance_v": bloch_fit.balance_y_v,
        "best_x_field_v": best_x_v,
        "best_y_field_v": best_y_v,
        "best_x_index": int(best_x_index),
        "best_y_index": int(best_y_index),
        "best_grid_definition": "nearest measured point to the fitted 2D Bloch balance",
        "best_pd_mean_v": float(mean_matrix[best_x_index, best_y_index]),
        "best_drift_corrected_pd_mean_v": float(
            drift_corrected_mean_matrix[best_x_index, best_y_index]
        ),
        "bloch_offset_v": bloch_fit.offset_v,
        "bloch_amplitude_v": bloch_fit.amplitude_v,
        "bloch_width_x_v": bloch_fit.width_x_v,
        "bloch_width_y_v": bloch_fit.width_y_v,
        "bloch_linear_drift_v_per_s": bloch_fit.linear_drift_v_per_s,
        "bloch_time_origin_unix_s": bloch_fit.time_origin_unix_s,
        "bloch_fitted_drift_over_scan_v": float(
            np.ptp(fitted_drift_matrix)
        ),
        "bloch_rmse_v": bloch_fit.rmse_v,
        "bloch_r_squared": bloch_fit.r_squared,
        "bloch_balance_x_near_boundary": bloch_fit.balance_x_near_boundary,
        "bloch_balance_y_near_boundary": bloch_fit.balance_y_near_boundary,
        "bloch_width_x_near_boundary": bloch_fit.width_x_near_boundary,
        "bloch_width_y_near_boundary": bloch_fit.width_y_near_boundary,
        "valid_dispersive_fit_count": int(np.count_nonzero(valid_fit_mask)),
        "dispersive_crosscheck_x_field_v": dispersive_crosscheck_x_v,
        "dispersive_crosscheck_y_balance_v": dispersive_crosscheck_y_v,
        "dispersive_crosscheck_x_difference_from_bloch_v": dispersive_x_difference_v,
        "dispersive_crosscheck_y_difference_from_bloch_v": dispersive_y_difference_v,
        "dispersive_amplitude_max_x_field_v": (
            None
            if amplitude_best_x_index is None
            else float(x_axis[amplitude_best_x_index])
        ),
        "dispersive_linewidth_min_x_field_v": (
            None
            if linewidth_best_x_index is None
            else float(x_axis[linewidth_best_x_index])
        ),
        "dispersive_central_slope_max_x_field_v": dispersive_crosscheck_x_v,
        "dispersive_metric_extrema_agree": metric_extrema_agree,
        "grid_shape": [int(x_axis.size), int(y_axis.size)],
        "quality_warnings": quality_warnings,
        "files": [npz_name, csv_name, fit_csv_name, *plot_files],
    }
    write_state(results_dir / "xy_residual_fit.json", dict(summary=summary,
        bloch_fit=asdict(bloch_fit), dispersive_fits=[asdict(fit) for fit in dispersive_fits]))
    return summary
