"""Mx Z 最优控制 XY 补偿偏置 RF 灵敏度离线分析。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from ...experiment_runtime import check_cancelled
from ..mx_y_rf_grid_analysis import (
    GridAnalysisSpec,
    GridAxisSpec,
    analyze_mx_y_rf_grid,
)
from ..mx_y_rf_sensitivity.analysis import _plot_full_analysis
from ..mx_y_rf_sensitivity.point_analysis import evaluate_mx_y_rf_point
from .models import MxZOptimalControlXYRFSensitivityParams
from . import run_format
from ..mx_y_rf_sensitivity.analysis_steps import plain, AnalysisQualityError
import numpy as np

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
    serializable = plain(result)
    (results_dir / "analysis.yaml").write_text(
        yaml.safe_dump(serializable, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    (results_dir / "analysis.json").write_text(
        json.dumps(serializable, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )


def evaluate(run_dir: Path) -> dict[str, Any]:
    """只读取运行目录，生成 XY 灵敏度矩阵和最佳点汇总。"""
    run_dir = Path(run_dir).resolve()
    config = _load_config(run_dir)
    try:
        result = analyze_mx_y_rf_grid(
            run_dir,
            params_type=MxZOptimalControlXYRFSensitivityParams,
            spec=GRID_SPEC,
            evaluate_point=_evaluate_xy_point,
            plot_full_analysis=checked_plot,
        )
    except RuntimeError as exc:
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
            if payload.get("success") is False:
                normalize_optimization(run_dir)
                raise AnalysisQualityError(str(exc)) from exc
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
    normalize_optimization(run_dir)
    return plain(result)




def checked_plot(**kwargs):
    check_cancelled()
    return _plot_full_analysis(**kwargs)


def normalize_optimization(run_dir):
    path = Path(run_dir) / "results/optimization.yaml"
    payload = plain(yaml.safe_load(path.read_text(encoding="utf-8")))
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    path.with_suffix(".json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def write_report(run_dir):
    config = _load_config(run_dir)
    payload = yaml.safe_load((Path(run_dir) / "results/optimization.yaml").read_text(encoding="utf-8"))
    payload.update(experiment_id=EXPERIMENT_ID, measurement_mode="xy_bias_grid_rf_sensitivity",
        **{key: config.get(key) for key in ("phase_calibration_reference", "selected_y_rf_phase_deg", "phase_calibration",
            "control_source", "z_calibration", "warnings")})
    _write_summary(Path(run_dir), payload)
    return plain(payload)


def load_inputs(run_dir):
    run_dir = Path(run_dir)
    config = _load_config(run_dir)
    if reason := run_format.analysis_block_reason(config):
        raise ValueError(reason)
    params = MxZOptimalControlXYRFSensitivityParams.from_external(config["parameters"], schema_version=config.get("schema_version", 1))
    manifest = yaml.safe_load((run_dir / "raw/point_manifest.yaml").read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or not isinstance(manifest.get("points"), dict) or not manifest["points"]:
        raise ValueError("缺少有效 XY 点状态清单")
    axes = [np.asarray(manifest[key], dtype=float) for key in ("x_field_v", "y_field_v")]
    if any(axis.ndim != 1 or not axis.size or not np.all(np.isfinite(axis)) for axis in axes):
        raise ValueError("XY 轴必须是非空有限一维数组")
    seen = set()
    for key, record in manifest["points"].items():
        check_cancelled()
        i, j = record["x_index"], record["y_index"]
        if (type(i) is not int or type(j) is not int or not 0 <= i < len(axes[0]) or not 0 <= j < len(axes[1])
                or key != f"x_{i:03d}_y_{j:03d}" or (i, j) in seen
                or not np.isclose(record["x_field_v"], axes[0][i]) or not np.isclose(record["y_field_v"], axes[1][j])):
            raise ValueError("XY 点索引、路径或坐标与扫描轴不一致")
        seen.add((i, j))
        point = run_dir / "raw/points" / key
        response = point / "amplitude_scan.npz"
        if response.is_file():
            with np.load(response, allow_pickle=False) as data:
                axis = np.asarray(data["signed_amplitude_vpp"])
                r_key = "r_mean_v" if "r_mean_v" in data else "r_scalar_mean_v"
                if axis.ndim != 1 or axis.size < 5 or any(np.asarray(data[name]).shape != axis.shape for name in (r_key, "r_std_v")):
                    raise ValueError("幅度响应数组形状不匹配或点数不足")
        for index in range(params.noise_n_avg):
            path = point / f"noise_{index:03d}.npz"
            if path.is_file():
                with np.load(path, allow_pickle=False) as data:
                    waveform, rate = np.asarray(data["r_v"]), float(data["actual_rate_sa_s"])
                    if waveform.ndim != 1 or waveform.size < 8 or not np.all(np.isfinite(waveform)) or not np.isfinite(rate) or rate <= 0:
                        raise ValueError("噪声波形或实际采样率无效")
    return params, config
