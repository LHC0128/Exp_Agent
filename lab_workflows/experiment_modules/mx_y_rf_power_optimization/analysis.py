"""Mx Y RF 光功率灵敏度优化离线分析。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import matplotlib
import numpy as np
import yaml

from ...experiment_runtime import runtime_run_dir
from ...plotting import (
    format_axis,
    new_figure,
    save_figure,
    set_plot_style,
)
from ..mx_y_rf_sensitivity.analysis import _plot_full_analysis
from ..mx_y_rf_sensitivity.point_analysis import evaluate_mx_y_rf_point
from .models import MxYRFPowerOptimizationParams

matplotlib.use(os.environ.get("MPLBACKEND", "Agg"))


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
) -> tuple[MxYRFPowerOptimizationParams, dict[str, Any]]:
    config_path = run_dir / "experiment_config.yaml"
    if not config_path.exists():
        raise FileNotFoundError(f"未找到实验配置: {config_path}")
    with config_path.open(encoding="utf-8") as stream:
        config = yaml.safe_load(stream) or {}
    params = MxYRFPowerOptimizationParams.from_external(
        config.get("parameters", {}),
        schema_version=int(config.get("schema_version", 1)),
    )
    return params, config


def _plot_heatmap(
    path: Path,
    *,
    probe_axis: np.ndarray,
    pump_axis: np.ndarray,
    values: np.ndarray,
    colorbar_label: str,
) -> None:
    set_plot_style("paper")
    fig, ax = new_figure()
    masked = np.ma.masked_invalid(values)
    image = ax.pcolormesh(
        probe_axis,
        pump_axis,
        masked,
        shading="nearest",
        cmap="viridis",
    )
    colorbar = fig.colorbar(image, ax=ax)
    colorbar.set_label(colorbar_label)
    format_axis(
        ax,
        xlabel="Probe power control voltage (V)",
        ylabel="Pump power control voltage (V)",
    )
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
    evaluation: dict[str, Any] | None,
    analysis_error: str | None,
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "key": key,
        "pump_index": int(record["pump_index"]),
        "probe_index": int(record["probe_index"]),
        "pump_power_v": float(record["pump_power_v"]),
        "probe_power_v": float(record["probe_power_v"]),
        "acquisition_status": record.get("status"),
        "valid": False,
        "invalid_reasons": [],
        "warnings": [],
    }
    if analysis_error is not None:
        summary["invalid_reasons"] = [analysis_error]
        return summary
    assert evaluation is not None
    summary["valid"] = bool(evaluation["valid"])
    summary["invalid_reasons"] = list(evaluation["invalid_reasons"])
    summary["warnings"] = list(evaluation["warnings"])
    response_fit = evaluation["response_fit"]
    summary["response_fit"] = evaluation["response_result"]
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


def analyze(run_dir: Path) -> dict[str, Any]:
    run_dir = Path(run_dir).resolve()
    raw_dir = run_dir / "raw"
    results_dir = run_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    point_results_dir = results_dir / "points"
    point_results_dir.mkdir(parents=True, exist_ok=True)
    params, config = _load_params(run_dir)

    manifest_path = raw_dir / "point_manifest.yaml"
    if not manifest_path.exists():
        raise FileNotFoundError(f"缺少点状态清单: {manifest_path}")
    with manifest_path.open(encoding="utf-8") as stream:
        manifest = yaml.safe_load(stream) or {}
    pump_axis = np.asarray(manifest["pump_power_v"], dtype=float)
    probe_axis = np.asarray(manifest["probe_power_v"], dtype=float)
    shape = (len(pump_axis), len(probe_axis))
    sensitivity_matrix = np.full(shape, np.nan, dtype=float)
    slope_matrix = np.full(shape, np.nan, dtype=float)
    hwhm_matrix = np.full(shape, np.nan, dtype=float)
    valid_matrix = np.zeros(shape, dtype=bool)
    status_matrix = np.full(shape, "pending", dtype="<U32")

    summaries: list[dict[str, Any]] = []
    evaluations: dict[str, dict[str, Any]] = {}
    point_full_analysis_files: list[str] = []
    for key, record in manifest.get("points", {}).items():
        pump_index = int(record["pump_index"])
        probe_index = int(record["probe_index"])
        status = str(record.get("status", "pending"))
        status_matrix[pump_index, probe_index] = status
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
                evaluation = evaluate_mx_y_rf_point(
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
            point_plot = _plot_full_analysis(
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
                    f"Pump {summary['pump_power_v']:.3f} V, "
                    f"Probe {summary['probe_power_v']:.3f} V | "
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
        slope_matrix[pump_index, probe_index] = float(
            evaluation["primary_slope"]
        )
        if evaluation["hwhm_hz"] is not None:
            hwhm_matrix[pump_index, probe_index] = float(
                evaluation["hwhm_hz"]
            )
        if evaluation["valid"]:
            value = float(
                evaluation["sensitivity"]["flat_median_ft_per_sqrt_hz"]
            )
            if np.isfinite(value):
                sensitivity_matrix[pump_index, probe_index] = value
                valid_matrix[pump_index, probe_index] = True

    ranking = sorted(
        (
            {
                "key": item["key"],
                "pump_power_v": item["pump_power_v"],
                "probe_power_v": item["probe_power_v"],
                "flat_median_ft_per_sqrt_hz": item[
                    "flat_median_ft_per_sqrt_hz"
                ],
            }
            for item in summaries
            if item["valid"]
            and item.get("flat_median_ft_per_sqrt_hz") is not None
        ),
        key=lambda item: item["flat_median_ft_per_sqrt_hz"],
    )
    best = ranking[0] if ranking else None

    np.savez(
        results_dir / "optimization.npz",
        pump_power_v=pump_axis,
        probe_power_v=probe_axis,
        flat_median_ft_per_sqrt_hz=sensitivity_matrix,
        primary_slope_v_per_vpp=slope_matrix,
        amplitude_equivalent_hwhm_hz=hwhm_matrix,
        valid_mask=valid_matrix,
        acquisition_status=status_matrix,
    )
    _plot_heatmap(
        results_dir / "sensitivity_heatmap.png",
        probe_axis=probe_axis,
        pump_axis=pump_axis,
        values=sensitivity_matrix,
        colorbar_label="Sensitivity (fT/√Hz)",
    )
    _plot_heatmap(
        results_dir / "slope_heatmap.png",
        probe_axis=probe_axis,
        pump_axis=pump_axis,
        values=slope_matrix,
        colorbar_label="Response slope (V/Vpp)",
    )
    _plot_heatmap(
        results_dir / "hwhm_heatmap.png",
        probe_axis=probe_axis,
        pump_axis=pump_axis,
        values=hwhm_matrix,
        colorbar_label="Amplitude-equivalent HWHM (Hz)",
    )

    best_plot = None
    if best is not None:
        evaluation = evaluations[best["key"]]
        best_plot = _plot_full_analysis(
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

    result = {
        "success": best is not None,
        "experiment_id": config.get(
            "experiment_id",
            "mx-y-rf-power-optimization",
        ),
        "run_dir": str(run_dir),
        "objective": "minimize flat_median_ft_per_sqrt_hz",
        "valid_point_count": int(np.count_nonzero(valid_matrix)),
        "total_point_count": int(valid_matrix.size),
        "best_point": best,
        "ranking": ranking,
        "points": summaries,
        "point_full_analysis_files": point_full_analysis_files,
        "plot_profile": "paper",
        "files": [
            "optimization.npz",
            "sensitivity_heatmap.png",
            "slope_heatmap.png",
            "hwhm_heatmap.png",
            *point_full_analysis_files,
            *([best_plot] if best_plot else []),
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
        raise RuntimeError("未找到可用最优点；请查看 optimization.yaml 中的无效原因")
    return result


def main() -> int:
    analyze(runtime_run_dir())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
