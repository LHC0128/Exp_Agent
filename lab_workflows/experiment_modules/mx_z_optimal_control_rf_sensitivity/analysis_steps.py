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


def report(run_dir: Path, evaluation=None) -> dict[str, Any]:
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
        evaluation=evaluation,
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




from . import run_format
from ..mx_y_rf_sensitivity.analysis_steps import plain, _encode, _decode, AnalysisQualityError
from ..mx_y_rf_sensitivity.point_analysis import evaluate_mx_y_rf_point
from ..mx_y_rf_sensitivity.analysis_plotting import _plot_measured_amplitude_response


def load_inputs(run_dir):
    run_dir = Path(run_dir)
    params, config = _load_params(run_dir, MxZOptimalControlRFParams)
    if reason := run_format.analysis_block_reason(config):
        raise ValueError(reason)
    phase = _load_yaml(run_dir / "results/phase_calibration.yaml")
    residual = params.phase_cal_rf_amplitude_vpp == 0.0
    expected = "residual_control_phase_scan" if residual else "rf_sensitivity"
    if config.get("measurement_mode", expected) != expected:
        raise ValueError("测量模式与采集参数不一致")
    with np.load(run_dir / "raw/phase_scan.npz", allow_pickle=False) as data:
        phase_key = "scanned_phase_deg" if "scanned_phase_deg" in data else ("control_burst_phase_deg" if residual else "y_rf_burst_phase_deg")
        axis = np.asarray(data[phase_key], dtype=float)
        for key in ("r_mean_v", "r_std_v", *(() if residual else ("x_mean_v", "y_mean_v"))):
            values = np.asarray(data[key], dtype=float)
            if axis.ndim != 1 or not axis.size or values.shape != axis.shape or not np.all(np.isfinite(axis)) or not np.all(np.isfinite(values)):
                raise ValueError("相位轴或 R/X/Y 汇总不是有效同形数组")
    if residual:
        _analyze_residual_control_phase_scan(run_dir, config, phase, "phase_calibration.png")
    else:
        if phase.get("success") is not True:
            raise AnalysisQualityError("在线 Y RF 正交校相未通过，拒绝正式灵敏度分析")
        if not np.isfinite(float(phase["selected_y_rf_phase_deg"])):
            raise ValueError("校准相位必须是有限数值")
        with np.load(run_dir / "raw/amplitude_scan.npz", allow_pickle=False) as data:
            axis = np.asarray(data["signed_amplitude_vpp"], dtype=float)
            r_key = "r_mean_v" if "r_mean_v" in data else "r_scalar_mean_v"
            if axis.ndim != 1 or axis.size < 5 or any(np.asarray(data[key]).shape != axis.shape for key in (r_key, "r_std_v")):
                raise ValueError("幅度响应数组形状不匹配或点数不足")
        for index in range(params.noise_n_avg):
            with np.load(run_dir / "raw" / f"noise_{index:03d}.npz", allow_pickle=False) as data:
                waveform = np.asarray(data["r_v"], dtype=float)
                rate = float(data["actual_rate_sa_s"])
                if waveform.ndim != 1 or waveform.size < 8 or not np.all(np.isfinite(waveform)) or not np.isfinite(rate) or rate <= 0:
                    raise ValueError("噪声波形或实际采样率无效")
    return params, config


def evaluate(run_dir):
    run_dir = Path(run_dir)
    params, config = load_inputs(run_dir)
    phase = _load_yaml(run_dir / "results/phase_calibration.yaml")
    if params.phase_cal_rf_amplitude_vpp == 0.0:
        value = _analyze_residual_control_phase_scan(run_dir, config, phase, "phase_calibration.png")
        diagnostics = {"mode": "residual_control_phase_scan", "scan_completed": value["scan_completed"],
            "fit_accepted": value["fit_accepted"], "response_summary": value["response_summary"]}
    else:
        value = evaluate_mx_y_rf_point(run_dir / "raw", params,
            ignore_relative_gamma_uncertainty=True, initial_center=0.0)
        diagnostics = {key: value.get(key) for key in ("valid", "invalid_reasons", "warnings", "response_result", "linewidth_result", "zero_point_invalid_reasons")}
    results = run_dir / "results"
    (results / "evaluation.json").write_text(json.dumps(_encode(value), ensure_ascii=False), encoding="utf-8")
    (results / "evaluation_diagnostics.yaml").write_text(yaml.safe_dump(plain(diagnostics), allow_unicode=True, sort_keys=False), encoding="utf-8")
    if params.phase_cal_rf_amplitude_vpp != 0.0 and not value["response_fit"].success:
        _plot_measured_amplitude_response(results_dir=results, amplitude_vpp=value["amplitude_vpp"],
            r_mean_v=value["r_mean_v"], r_std_v=value["r_std_v"], bad_point_mask=value["bad_point_mask"])
        (results / "analysis.yaml").write_text(yaml.safe_dump(plain(dict(success=False,
            response_fit=value["response_result"], plot_profile="paper", files=["amplitude_response.png"])), allow_unicode=True), encoding="utf-8")
        raise AnalysisQualityError("幅度色散拟合质量不合格：" + "；".join(value["response_fit"].rejection_reasons))
    return plain(diagnostics)


def render_report(run_dir):
    value = _decode(json.loads((Path(run_dir) / "results/evaluation.json").read_text(encoding="utf-8")))
    return report(Path(run_dir), value)
