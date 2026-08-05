"""Mx Y RF 二维工作点优化的轴参数化离线分析。"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any, Callable, TypeVar

import matplotlib
import numpy as np
import yaml

from ..experiment_params import ExperimentParams
from ..plotting import format_axis, new_figure, save_figure, set_plot_style

matplotlib.use(os.environ.get("MPLBACKEND", "Agg"))


ParamsT = TypeVar("ParamsT", bound=ExperimentParams)


@dataclass(frozen=True, slots=True)
class GridAxisSpec:
    """二维结果矩阵一条轴的外部数据契约。"""

    manifest_key: str
    index_key: str
    coordinate_key: str
    axis_label: str
    point_label: str


@dataclass(frozen=True, slots=True)
class GridAnalysisSpec:
    """一个 Mx Y RF 二维优化实验的分析契约。"""

    experiment_id: str
    outer: GridAxisSpec
    inner: GridAxisSpec
    point_record_fields: tuple[str, ...] = ()


def _builtin(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _builtin(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_builtin(item) for item in value]
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    return value


def _load_params(
    run_dir: Path,
    params_type: type[ParamsT],
) -> tuple[ParamsT, dict[str, Any]]:
    config_path = run_dir / "experiment_config.yaml"
    if not config_path.exists():
        raise FileNotFoundError(f"未找到实验配置: {config_path}")
    with config_path.open(encoding="utf-8") as stream:
        config = yaml.safe_load(stream) or {}
    params = params_type.from_external(
        config.get("parameters", {}),
        schema_version=int(config.get("schema_version", 1)),
    )
    return params, config


def _plot_heatmap(
    path: Path,
    *,
    inner_axis: np.ndarray,
    outer_axis: np.ndarray,
    values: np.ndarray,
    inner_label: str,
    outer_label: str,
    colorbar_label: str,
) -> None:
    set_plot_style("paper")
    fig, ax = new_figure()
    masked = np.ma.masked_invalid(values)
    image = ax.pcolormesh(
        inner_axis,
        outer_axis,
        masked,
        shading="nearest",
        cmap="viridis",
    )
    colorbar = fig.colorbar(image, ax=ax)
    colorbar.set_label(colorbar_label)
    format_axis(ax, xlabel=inner_label, ylabel=outer_label)
    if not np.any(np.isfinite(values)):
        ax.text(
            0.5,
            0.5,
            "No valid data",
            transform=ax.transAxes,
            ha="center",
            va="center",
        )
    save_figure(fig, path)


def _point_summary(
    *,
    key: str,
    record: dict[str, Any],
    spec: GridAnalysisSpec,
    evaluation: dict[str, Any] | None,
    analysis_error: str | None,
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "key": key,
        spec.outer.index_key: int(record[spec.outer.index_key]),
        spec.inner.index_key: int(record[spec.inner.index_key]),
        spec.outer.coordinate_key: float(
            record[spec.outer.coordinate_key]
        ),
        spec.inner.coordinate_key: float(
            record[spec.inner.coordinate_key]
        ),
        "acquisition_status": record.get("status"),
        "valid": False,
        "invalid_reasons": [],
        "warnings": [],
    }
    for field in spec.point_record_fields:
        if record.get(field) is not None:
            summary[field] = _builtin(record[field])
    if analysis_error is not None:
        summary["invalid_reasons"] = [analysis_error]
        return summary
    assert evaluation is not None
    summary["valid"] = bool(evaluation["valid"])
    summary["invalid_reasons"] = list(evaluation["invalid_reasons"])
    summary["warnings"] = list(evaluation["warnings"])
    response_fit = evaluation["response_fit"]
    summary["response_fit"] = evaluation["response_result"]
    zero_point_linear = evaluation.get("zero_point_linear")
    if zero_point_linear is not None:
        summary["zero_point_local_linear_fit"] = {
            name: value
            for name, value in zero_point_linear.items()
            if name != "mask"
        }
        summary["zero_point_valid"] = bool(
            evaluation["zero_point_valid"]
        )
        summary["zero_point_invalid_reasons"] = list(
            evaluation["zero_point_invalid_reasons"]
        )
        if zero_point_linear["success"]:
            summary["zero_point_slope_v_per_vpp"] = float(
                evaluation["zero_point_slope"]
            )
        zero_point_sensitivity = evaluation.get(
            "zero_point_sensitivity"
        )
        if zero_point_sensitivity is not None:
            zero_flat_success = bool(
                zero_point_sensitivity["flat_detection_success"]
            )
            summary["zero_point_flat_detection"] = {
                "success": zero_flat_success,
                "reason": str(
                    zero_point_sensitivity["flat_detection_reason"]
                ),
                "flat_band_hz": (
                    _builtin(zero_point_sensitivity["flat_band_hz"])
                    if zero_flat_success
                    else None
                ),
            }
            summary[
                "zero_point_flat_median_ft_per_sqrt_hz"
            ] = (
                float(
                    zero_point_sensitivity[
                        "flat_median_ft_per_sqrt_hz"
                    ]
                )
                if evaluation["zero_point_valid"]
                else None
            )
    if response_fit.success:
        summary["primary_slope_v_per_vpp"] = float(
            evaluation["primary_slope"]
        )
        summary["amplitude_equivalent_hwhm_hz"] = (
            float(evaluation["hwhm_hz"])
            if evaluation["hwhm_hz"] is not None
            else None
        )
        sensitivity = evaluation["sensitivity"]
        summary["flat_detection"] = {
            "success": bool(sensitivity["flat_detection_success"]),
            "reason": str(sensitivity["flat_detection_reason"]),
            "flat_band_hz": (
                _builtin(sensitivity["flat_band_hz"])
                if bool(sensitivity["flat_detection_success"])
                else None
            ),
        }
        summary["flat_median_ft_per_sqrt_hz"] = (
            float(sensitivity["flat_median_ft_per_sqrt_hz"])
            if evaluation["valid"]
            else None
        )
    return summary


def _ranking_item(
    summary: dict[str, Any],
    spec: GridAnalysisSpec,
    *,
    zero_point: bool,
) -> dict[str, Any]:
    item = {
        "key": summary["key"],
        spec.outer.coordinate_key: summary[spec.outer.coordinate_key],
        spec.inner.coordinate_key: summary[spec.inner.coordinate_key],
    }
    for field in spec.point_record_fields:
        if field in summary:
            item[field] = summary[field]
    if zero_point:
        item["zero_point_slope_v_per_vpp"] = summary[
            "zero_point_slope_v_per_vpp"
        ]
        item["flat_median_ft_per_sqrt_hz"] = summary[
            "zero_point_flat_median_ft_per_sqrt_hz"
        ]
    else:
        item["flat_median_ft_per_sqrt_hz"] = summary[
            "flat_median_ft_per_sqrt_hz"
        ]
    return item


def analyze_mx_y_rf_grid(
    run_dir: Path,
    *,
    params_type: type[ParamsT],
    spec: GridAnalysisSpec,
    evaluate_point: Callable[..., dict[str, Any]],
    plot_full_analysis: Callable[..., str | None],
) -> dict[str, Any]:
    """分析任意两条控制轴组成的 Mx Y RF 完整灵敏度网格。"""
    run_dir = Path(run_dir).resolve()
    raw_dir = run_dir / "raw"
    results_dir = run_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    point_results_dir = results_dir / "points"
    point_results_dir.mkdir(parents=True, exist_ok=True)
    params, config = _load_params(run_dir, params_type)

    manifest_path = raw_dir / "point_manifest.yaml"
    if not manifest_path.exists():
        raise FileNotFoundError(f"缺少点状态清单: {manifest_path}")
    with manifest_path.open(encoding="utf-8") as stream:
        manifest = yaml.safe_load(stream) or {}
    outer_axis = np.asarray(
        manifest[spec.outer.manifest_key],
        dtype=float,
    )
    inner_axis = np.asarray(
        manifest[spec.inner.manifest_key],
        dtype=float,
    )
    shape = (len(outer_axis), len(inner_axis))
    sensitivity_matrix = np.full(shape, np.nan, dtype=float)
    slope_matrix = np.full(shape, np.nan, dtype=float)
    zero_point_sensitivity_matrix = np.full(shape, np.nan, dtype=float)
    zero_point_slope_matrix = np.full(shape, np.nan, dtype=float)
    hwhm_matrix = np.full(shape, np.nan, dtype=float)
    valid_matrix = np.zeros(shape, dtype=bool)
    zero_point_valid_matrix = np.zeros(shape, dtype=bool)
    status_matrix = np.full(shape, "pending", dtype="<U32")
    extra_matrices = {
        field: np.full(shape, np.nan, dtype=float)
        for field in spec.point_record_fields
    }

    summaries: list[dict[str, Any]] = []
    evaluations: dict[str, dict[str, Any]] = {}
    point_full_analysis_files: list[str] = []
    for key, record in manifest.get("points", {}).items():
        outer_index = int(record[spec.outer.index_key])
        inner_index = int(record[spec.inner.index_key])
        status = str(record.get("status", "pending"))
        status_matrix[outer_index, inner_index] = status
        for field, matrix in extra_matrices.items():
            value = record.get(field)
            if isinstance(value, (int, float)):
                matrix[outer_index, inner_index] = float(value)
        point_raw = raw_dir / "points" / key
        has_complete_files = (
            (point_raw / "amplitude_scan.npz").exists()
            and all(
                (point_raw / f"noise_{index:03d}.npz").exists()
                for index in range(params.noise_n_avg)
            )
        )
        evaluation = None
        analysis_error = None
        if status == "completed" or has_complete_files:
            try:
                evaluation = evaluate_point(
                    point_raw,
                    params,
                    include_rejected_fit_diagnostics=True,
                )
                evaluations[key] = evaluation
            except Exception as exc:
                analysis_error = f"单点分析失败: {exc}"
        else:
            analysis_error = (
                str(record.get("reason"))
                if record.get("reason")
                else f"采集状态为 {status}"
            )
        summary = _point_summary(
            key=key,
            record=record,
            spec=spec,
            evaluation=evaluation,
            analysis_error=analysis_error,
        )
        summaries.append(summary)
        point_dir = point_results_dir / key
        point_dir.mkdir(parents=True, exist_ok=True)
        if (
            evaluation is not None
            and "sensitivity" in evaluation
            and "corrected_ft_per_sqrt_hz" in evaluation["sensitivity"]
        ):
            quality_label = (
                "VALID"
                if evaluation["valid"]
                else "DIAGNOSTIC ONLY - INVALID"
            )
            point_plot = plot_full_analysis(
                results_dir=point_dir,
                params=params,
                amplitude_vpp=evaluation["amplitude_vpp"],
                r_mean_v=evaluation["r_mean_v"],
                fit_mask=evaluation["fit_mask"],
                bad_point_mask=evaluation["bad_point_mask"],
                response_fit=evaluation["response_fit"],
                primary_slope=evaluation["primary_slope"],
                frequency_hz=evaluation["frequency_hz"],
                sensitivity=evaluation["sensitivity"],
                hwhm_hz=evaluation["hwhm_hz"],
                filename="full_analysis.png",
                title=(
                    f"{spec.outer.point_label} "
                    f"{summary[spec.outer.coordinate_key]:.3f} V, "
                    f"{spec.inner.point_label} "
                    f"{summary[spec.inner.coordinate_key]:.3f} V | "
                    f"{quality_label}"
                ),
            )
            if point_plot:
                relative_plot = f"points/{key}/{point_plot}"
                summary["full_analysis_file"] = relative_plot
                point_full_analysis_files.append(relative_plot)
        (point_dir / "analysis.yaml").write_text(
            yaml.safe_dump(
                _builtin(summary),
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        if evaluation is None or not evaluation["response_fit"].success:
            continue
        slope_matrix[outer_index, inner_index] = float(
            evaluation["primary_slope"]
        )
        if evaluation["hwhm_hz"] is not None:
            hwhm_matrix[outer_index, inner_index] = float(
                evaluation["hwhm_hz"]
            )
        if evaluation["valid"]:
            value = float(
                evaluation["sensitivity"]["flat_median_ft_per_sqrt_hz"]
            )
            if np.isfinite(value):
                sensitivity_matrix[outer_index, inner_index] = value
                valid_matrix[outer_index, inner_index] = True
        zero_point_linear = evaluation.get("zero_point_linear")
        if zero_point_linear and zero_point_linear["success"]:
            zero_point_slope_matrix[outer_index, inner_index] = float(
                evaluation["zero_point_slope"]
            )
        if evaluation.get("zero_point_valid"):
            zero_point_value = float(
                evaluation["zero_point_sensitivity"][
                    "flat_median_ft_per_sqrt_hz"
                ]
            )
            if np.isfinite(zero_point_value):
                zero_point_sensitivity_matrix[
                    outer_index,
                    inner_index,
                ] = zero_point_value
                zero_point_valid_matrix[outer_index, inner_index] = True

    ranking = sorted(
        (
            _ranking_item(item, spec, zero_point=False)
            for item in summaries
            if item["valid"]
            and item.get("flat_median_ft_per_sqrt_hz") is not None
        ),
        key=lambda item: item["flat_median_ft_per_sqrt_hz"],
    )
    best = ranking[0] if ranking else None
    zero_point_ranking = sorted(
        (
            _ranking_item(item, spec, zero_point=True)
            for item in summaries
            if item.get("zero_point_valid")
            and item.get(
                "zero_point_flat_median_ft_per_sqrt_hz"
            ) is not None
        ),
        key=lambda item: item["flat_median_ft_per_sqrt_hz"],
    )
    zero_point_best = zero_point_ranking[0] if zero_point_ranking else None

    np.savez(
        results_dir / "optimization.npz",
        **{
            spec.outer.manifest_key: outer_axis,
            spec.inner.manifest_key: inner_axis,
            "flat_median_ft_per_sqrt_hz": sensitivity_matrix,
            "primary_slope_v_per_vpp": slope_matrix,
            "zero_point_flat_median_ft_per_sqrt_hz": (
                zero_point_sensitivity_matrix
            ),
            "zero_point_slope_v_per_vpp": zero_point_slope_matrix,
            "amplitude_equivalent_hwhm_hz": hwhm_matrix,
            "valid_mask": valid_matrix,
            "zero_point_valid_mask": zero_point_valid_matrix,
            "acquisition_status": status_matrix,
            **extra_matrices,
        },
    )
    heatmaps = (
        (
            "sensitivity_heatmap.png",
            sensitivity_matrix,
            "Sensitivity (fT/√Hz)",
        ),
        (
            "slope_heatmap.png",
            slope_matrix,
            "Response slope (V/Vpp)",
        ),
        (
            "zero_point_sensitivity_heatmap.png",
            zero_point_sensitivity_matrix,
            "Zero-point sensitivity (fT/√Hz)",
        ),
        (
            "zero_point_slope_heatmap.png",
            zero_point_slope_matrix,
            "Zero-point data slope (V/Vpp)",
        ),
        (
            "hwhm_heatmap.png",
            hwhm_matrix,
            "Amplitude-equivalent HWHM (Hz)",
        ),
    )
    for filename, values, colorbar_label in heatmaps:
        _plot_heatmap(
            results_dir / filename,
            inner_axis=inner_axis,
            outer_axis=outer_axis,
            values=values,
            inner_label=spec.inner.axis_label,
            outer_label=spec.outer.axis_label,
            colorbar_label=colorbar_label,
        )

    best_plot = None
    if best is not None:
        evaluation = evaluations[best["key"]]
        best_plot = plot_full_analysis(
            results_dir=results_dir,
            params=params,
            amplitude_vpp=evaluation["amplitude_vpp"],
            r_mean_v=evaluation["r_mean_v"],
            fit_mask=evaluation["fit_mask"],
            bad_point_mask=evaluation["bad_point_mask"],
            response_fit=evaluation["response_fit"],
            primary_slope=evaluation["primary_slope"],
            frequency_hz=evaluation["frequency_hz"],
            sensitivity=evaluation["sensitivity"],
            hwhm_hz=evaluation["hwhm_hz"],
            filename="best_point_full_analysis.png",
        )
    zero_point_best_plot = None
    if zero_point_best is not None:
        evaluation = evaluations[zero_point_best["key"]]
        zero_point_best_plot = plot_full_analysis(
            results_dir=results_dir,
            params=params,
            amplitude_vpp=evaluation["amplitude_vpp"],
            r_mean_v=evaluation["r_mean_v"],
            fit_mask=evaluation["fit_mask"],
            bad_point_mask=evaluation["bad_point_mask"],
            response_fit=evaluation["response_fit"],
            primary_slope=evaluation["zero_point_slope"],
            frequency_hz=evaluation["frequency_hz"],
            sensitivity=evaluation["zero_point_sensitivity"],
            hwhm_hz=evaluation["hwhm_hz"],
            filename="best_zero_point_full_analysis.png",
            title="Best point | Zero-point measured-data slope method",
            slope_method_label="Zero-point slope",
            local_slope_fit=evaluation["zero_point_linear"],
        )

    result = {
        "success": best is not None,
        "experiment_id": config.get(
            "experiment_id",
            spec.experiment_id,
        ),
        "run_dir": str(run_dir),
        "objective": "minimize flat_median_ft_per_sqrt_hz",
        "valid_point_count": int(np.count_nonzero(valid_matrix)),
        "total_point_count": int(valid_matrix.size),
        "best_point": best,
        "ranking": ranking,
        "zero_point_method": {
            "name": "adaptive_zero_point_absolute_linear",
            "description": (
                "Global dispersive fit locates V0; the slope is regressed "
                "from at least five nearest measured R-versus-|V-V0| points "
                "with at least two points on each side."
            ),
            "objective": "minimize flat_median_ft_per_sqrt_hz",
            "success": zero_point_best is not None,
            "valid_point_count": int(
                np.count_nonzero(zero_point_valid_matrix)
            ),
            "best_point": zero_point_best,
            "ranking": zero_point_ranking,
        },
        "points": summaries,
        "point_full_analysis_files": point_full_analysis_files,
        "plot_profile": "paper",
        "files": [
            "optimization.npz",
            *(filename for filename, _, _ in heatmaps),
            *point_full_analysis_files,
            *([best_plot] if best_plot else []),
            *(
                [zero_point_best_plot]
                if zero_point_best_plot
                else []
            ),
        ],
    }
    serializable = _builtin(result)
    (results_dir / "optimization.yaml").write_text(
        yaml.safe_dump(serializable, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    (results_dir / "optimization.json").write_text(
        json.dumps(serializable, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if best is None:
        raise RuntimeError(
            "未找到可用最优点；请查看 optimization.yaml 中的无效原因"
        )
    return result
