"""Mx Z 最优控制 RF 灵敏度离线分析。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from ...experiment_runtime import runtime_run_dir
from ...plotting import set_plot_style
from ..mx_y_rf_sensitivity.analysis import _load_params, analyze_core
from .models import MxZOptimalControlRFParams
from .phase_plot import plot_phase_calibration


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


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"缺少分析输入: {path}")
    with path.open(encoding="utf-8") as stream:
        payload = yaml.safe_load(stream) or {}
    if not isinstance(payload, dict):
        raise TypeError(f"YAML 顶层必须是映射: {path}")
    return payload


def _finite_or_none(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if np.isfinite(number) else None


def _main_field_measurement_summary(config: dict[str, Any]) -> str:
    """从运行配置还原采集期间的 GS200 电流和输出状态。"""
    geometry = config.get("geometry", {})
    parameters = config.get("parameters", {})
    current_ma = _finite_or_none(
        geometry.get(
            "gs200_main_field_current_ma",
            parameters.get("FIXED_PARAMS.main_magnetic_field", 0.0),
        )
    )
    if current_ma is None:
        raise ValueError("运行配置中的 GS200 主场电流不是有限数值")
    output_state = "OFF" if current_ma == 0.0 else "ON"
    return f"{current_ma:g} mA, output {output_state}"


def _analyze_residual_control_phase_scan(
    run_dir: Path,
    config: dict[str, Any],
    phase_payload: dict[str, Any],
    phase_plot: str,
) -> dict[str, Any]:
    """汇总 RFY 交流关闭时的 Z 控制相位响应，不要求拟合显著。"""
    with np.load(run_dir / "raw" / "phase_scan.npz") as data:
        if "scanned_phase_deg" in data:
            phase_deg = np.asarray(data["scanned_phase_deg"], dtype=float)
        else:
            phase_deg = np.asarray(
                data["control_burst_phase_deg"],
                dtype=float,
            )
        r_mean_v = np.asarray(data["r_mean_v"], dtype=float)
        r_std_v = np.asarray(data["r_std_v"], dtype=float)
        actual_rate = _finite_or_none(data["actual_rate_sa_s"])

    if phase_deg.size == 0 or r_mean_v.shape != phase_deg.shape:
        raise ValueError("Z 控制相位扫描数据为空或数组长度不一致")
    if r_std_v.shape != phase_deg.shape:
        raise ValueError("Z 控制相位扫描的 R 标准差数组长度不一致")
    if not np.all(np.isfinite(phase_deg)) or not np.all(np.isfinite(r_mean_v)):
        raise ValueError("Z 控制相位扫描包含 NaN 或无穷值")

    maximum_index = int(np.argmax(r_mean_v))
    minimum_index = int(np.argmin(r_mean_v))
    fit_accepted = phase_payload.get("fit_accepted") is True
    rejection_reasons = list(
        phase_payload.get("primary_fit", {}).get(
            "rejection_reasons",
            [],
        )
    )
    warnings = []
    if not fit_accepted:
        warnings.append(
            "剩磁响应相位拟合未达到门槛；扫描仍有效并已正常完成。"
        )

    result = {
        "experiment_id": "mx-z-optimal-control-rf-sensitivity",
        "run_dir": str(run_dir),
        "measurement_mode": "residual_control_phase_scan",
        "control_enabled": True,
        "gs200_main_field_during_measurement": (
            _main_field_measurement_summary(config)
        ),
        "y_rf_output_during_scan": config.get(
            "y_rf_output_during_scan",
            (
                "DC_BIAS_ON"
                if float(
                    config.get("parameters", {}).get(
                        "FIXED_PARAMS.Y_magnetic_field",
                        0.0,
                    )
                )
                != 0.0
                else "DC_ZERO_OUTPUT_OFF"
            ),
        ),
        "x_dc_field_v": float(
            config.get("parameters", {}).get(
                "FIXED_PARAMS.X_magnetic_field",
                0.0,
            )
        ),
        "y_rf_dc_offset_v": float(
            config.get("parameters", {}).get(
                "FIXED_PARAMS.Y_magnetic_field",
                0.0,
            )
        ),
        "scan_completed": phase_payload.get("scan_completed") is True,
        "fit_accepted": fit_accepted,
        "fit_rejection_reasons": rejection_reasons,
        "selected_control_phase_deg": _finite_or_none(
            phase_payload.get("selected_control_phase_deg")
        ),
        "restored_control_phase_deg": _finite_or_none(
            phase_payload.get("phase_scan", {}).get(
                "control_restored_phase_deg"
            )
        ),
        "response_summary": {
            "points": int(phase_deg.size),
            "mean_r_v": float(np.mean(r_mean_v)),
            "minimum_r_v": float(r_mean_v[minimum_index]),
            "minimum_phase_deg": float(phase_deg[minimum_index]),
            "maximum_r_v": float(r_mean_v[maximum_index]),
            "maximum_phase_deg": float(phase_deg[maximum_index]),
            "peak_to_peak_r_v": float(np.ptp(r_mean_v)),
            "mean_point_std_v": float(np.mean(r_std_v)),
            "actual_rate_sa_s": actual_rate,
        },
        "phase_response": phase_payload,
        "control_source": config.get("control_source", {}),
        "applied_control": config.get("applied_control", {}),
        "trigger": config.get("trigger", {}),
        "warnings": warnings,
        "files": ["phase_calibration.yaml", phase_plot],
    }
    return result


def analyze(run_dir: Path) -> dict[str, Any]:
    """复用 Mx Y RF 分析并追加控制与校相结果。"""
    run_dir = Path(run_dir).resolve()
    # 旧 schema 在创建或覆盖任何结果之前拒绝，已有文件保持不变。
    params, config = _load_params(run_dir, MxZOptimalControlRFParams)
    raw_dir = run_dir / "raw"
    results_dir = run_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    phase_payload = _load_yaml(results_dir / "phase_calibration.yaml")
    set_plot_style("paper")
    phase_plot = plot_phase_calibration(
        raw_dir,
        results_dir,
        phase_payload,
    )
    residual_mode = (
        config.get("measurement_mode") == "residual_control_phase_scan"
        or phase_payload.get("mode") == "residual_control_phase_scan"
    )
    if residual_mode:
        result = _analyze_residual_control_phase_scan(
            run_dir,
            config,
            phase_payload,
            phase_plot,
        )
        (results_dir / "analysis.yaml").write_text(
            yaml.safe_dump(
                _builtin(result),
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        (results_dir / "analysis.json").write_text(
            json.dumps(_builtin(result), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return result

    if phase_payload.get("success") is not True:
        raise RuntimeError("在线 Y RF 相位校准未成功，拒绝正式灵敏度分析")

    result = analyze_core(
        run_dir,
        params,
        config,
        ignore_relative_gamma_uncertainty=True,
        initial_center=0.0,
    )
    result.update(
        {
            "experiment_id": "mx-z-optimal-control-rf-sensitivity",
            "control_enabled": True,
            "gs200_main_field_during_measurement": (
                _main_field_measurement_summary(config)
            ),
            "phase_calibration": phase_payload,
            "selected_y_rf_phase_deg": float(
                phase_payload["selected_y_rf_phase_deg"]
            ),
            "noise_rf": config.get("noise_rf", {"y_rf_enabled": False}),
            "control_source": config.get("control_source", {}),
            "applied_control": config.get("applied_control", {}),
            "trigger": config.get("trigger", {}),
        }
    )
    files = list(result.get("files", []))
    if phase_plot not in files:
        files.append(phase_plot)
    if "phase_calibration.yaml" not in files:
        files.append("phase_calibration.yaml")
    result["files"] = files
    (results_dir / "analysis.yaml").write_text(
        yaml.safe_dump(_builtin(result), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    (results_dir / "analysis.json").write_text(
        json.dumps(_builtin(result), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def main() -> int:
    run_dir = runtime_run_dir()
    if run_dir is None:
        raise RuntimeError("未设置分析运行目录")
    result = analyze(run_dir)
    print(f"Mx Z 最优控制 RF 灵敏度分析完成: {result['run_dir']}")
    for warning in result.get("warnings", []):
        print(f"[WARN] {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
