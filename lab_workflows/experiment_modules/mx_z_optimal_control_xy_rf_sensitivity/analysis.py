"""Mx Z 最优控制 XY 补偿偏置 RF 灵敏度离线分析。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from ...experiment_runtime import runtime_run_dir
from ..mx_y_rf_grid_analysis import (
    GridAnalysisSpec,
    GridAxisSpec,
    analyze_mx_y_rf_grid,
)
from ..mx_y_rf_sensitivity.analysis import _plot_full_analysis
from ..mx_y_rf_sensitivity.point_analysis import evaluate_mx_y_rf_point
from .models import MxZOptimalControlXYRFSensitivityParams

EXPERIMENT_ID = "mx-z-optimal-control-xy-rf-sensitivity"


def _evaluate_xy_point(
    raw_dir: Path,
    params: MxZOptimalControlXYRFSensitivityParams,
    *,
    include_rejected_fit_diagnostics: bool = False,
) -> dict[str, Any]:
    """使用 Mx Z 单点实验的幅度拟合约定评估一个 XY 点。

    Mx Z 单点分析固定从物理中心 ``V0=0`` 开始拟合，并保留线宽相对
    不确定度作为诊断量而不是硬拒绝条件。XY 网格必须复用这一约定，
    否则端点低值可能被误认为拟合中心，导致优化落到扫描边界。
    """
    return evaluate_mx_y_rf_point(
        raw_dir,
        params,
        include_rejected_fit_diagnostics=include_rejected_fit_diagnostics,
        ignore_relative_gamma_uncertainty=True,
        initial_center=0.0,
    )

GRID_SPEC = GridAnalysisSpec(
    experiment_id="mx-z-optimal-control-xy-rf-sensitivity",
    outer=GridAxisSpec(
        manifest_key="x_field_v",
        index_key="x_index",
        coordinate_key="x_field_v",
        axis_label="X compensation bias (V)",
        point_label="X",
    ),
    inner=GridAxisSpec(
        manifest_key="y_field_v",
        index_key="y_index",
        coordinate_key="y_field_v",
        axis_label="Y compensation bias (V)",
        point_label="Y",
    ),
    point_record_fields=(
        "sequence_index",
        "actual_response_rate_sa_s",
        "actual_noise_rate_sa_s",
        "selected_y_rf_phase_deg",
    ),
)


def _load_config(run_dir: Path) -> dict[str, Any]:
    with (run_dir / "experiment_config.yaml").open(encoding="utf-8") as stream:
        payload = yaml.safe_load(stream) or {}
    if not isinstance(payload, dict):
        raise TypeError("experiment_config.yaml 顶层必须是映射")
    return payload


def _write_summary(run_dir: Path, result: dict[str, Any]) -> None:
    results_dir = run_dir / "results"
    serializable = result
    (results_dir / "analysis.yaml").write_text(
        yaml.safe_dump(serializable, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    (results_dir / "analysis.json").write_text(
        json.dumps(serializable, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def analyze(run_dir: Path) -> dict[str, Any]:
    """只读取运行目录，生成 XY 灵敏度矩阵和最佳点汇总。"""
    run_dir = Path(run_dir).resolve()
    config = _load_config(run_dir)
    try:
        result = analyze_mx_y_rf_grid(
            run_dir,
            params_type=MxZOptimalControlXYRFSensitivityParams,
            spec=GRID_SPEC,
            evaluate_point=_evaluate_xy_point,
            plot_full_analysis=_plot_full_analysis,
        )
    except RuntimeError:
        # 公共网格分析器在无有效点时会先写 optimization.yaml 再抛错；
        # 这里补齐本实验约定的总分析文件，便于 GUI 和人工排障。
        optimization_path = run_dir / "results" / "optimization.yaml"
        if optimization_path.is_file():
            payload = yaml.safe_load(
                optimization_path.read_text(encoding="utf-8")
            ) or {}
            payload.update(
                {
                    "experiment_id": EXPERIMENT_ID,
                    "measurement_mode": "xy_bias_grid_rf_sensitivity",
                    "phase_calibration_reference": config.get(
                        "phase_calibration_reference", {}
                    ),
                    "selected_y_rf_phase_deg": config.get(
                        "selected_y_rf_phase_deg"
                    ),
                }
            )
            _write_summary(run_dir, payload)
        raise
    result.update(
        {
            "experiment_id": EXPERIMENT_ID,
            "measurement_mode": "xy_bias_grid_rf_sensitivity",
            "phase_calibration_reference": config.get(
                "phase_calibration_reference", {}
            ),
            "selected_y_rf_phase_deg": config.get(
                "selected_y_rf_phase_deg"
            ),
            "phase_calibration": config.get("phase_calibration", {}),
            "control_source": config.get("control_source", {}),
            "z_calibration": config.get("z_calibration", {}),
            "warnings": config.get("warnings", []),
        }
    )
    _write_summary(run_dir, result)
    return result


def main() -> int:
    run_dir = runtime_run_dir()
    if run_dir is None:
        raise RuntimeError("未设置分析运行目录")
    result = analyze(run_dir)
    print(f"Mx Z 最优控制 XY 补偿偏置 RF 灵敏度分析完成: {result['run_dir']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
