"""电流耦合标定的原始数据加载、共振拟合与加权标定。"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from ...experiment_runtime import check_cancelled
from ..mx_z_field_calibration.analysis_steps import _analyze_curve, _builtin, _load_scan_index, _weighted_linear_fit
from lab_workflows.workflow_state import write_state
from .analysis_plotting import _plot_calibration, _plot_frequency_responses
from .models import MxZCurrentCouplingCalibrationParams
from .run_format import EXPERIMENT_ID, analysis_block_reason


def _load_params(run_dir: Path) -> tuple[MxZCurrentCouplingCalibrationParams, dict[str, Any]]:
    with (run_dir / "experiment_config.yaml").open(encoding="utf-8") as stream:
        config = yaml.safe_load(stream) or {}
    if reason := analysis_block_reason(config):
        raise ValueError(reason)
    params = MxZCurrentCouplingCalibrationParams.from_external(
        config.get("parameters", {}), schema_version=int(config.get("schema_version", 1))
    )
    return params, config


def _analyze_current_curve(
    params: MxZCurrentCouplingCalibrationParams,
    run_dir: Path,
    entry: dict[str, Any],
) -> dict[str, Any]:
    curve = _analyze_curve(params, run_dir, entry)
    path = run_dir / str(entry["summary_file"])
    with np.load(path) as data:
        current_a = float(np.asarray(data["current_mean_a"]).reshape(()))
        current_std_a = float(np.asarray(data["current_std_a"]).reshape(()))
        sense_voltage_v = float(np.asarray(data["sense_voltage_mean_v"]).reshape(()))
        sense_voltage_std_v = float(np.asarray(data["sense_voltage_std_v"]).reshape(()))
        display_edge_fraction = float(
            np.asarray(data["sense_display_edge_fraction"]).reshape(())
            if "sense_display_edge_fraction" in data.files
            else np.nan
        )
        scan_direction = (
            str(np.asarray(data["scan_direction"]).reshape(()))
            if "scan_direction" in data.files
            else "forward"
        )
    curve.update(
        current_a=current_a,
        current_std_a=current_std_a,
        sense_voltage_v=sense_voltage_v,
        sense_voltage_std_v=sense_voltage_std_v,
        scan_direction=scan_direction,
        display_edge_fraction=display_edge_fraction,
    )
    return curve


def plain(value):
    """JSON 与会话使用 null，科学计算和 NPZ 继续保留 NaN。"""
    if isinstance(value, dict):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    if isinstance(value, np.ndarray):
        return plain(value.tolist())
    if isinstance(value, np.generic):
        return plain(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def load_inputs(run_dir, config):
    if reason := analysis_block_reason(config):
        raise ValueError(reason)
    raw = (Path(run_dir) / "raw").resolve()
    entries = _load_scan_index(raw)
    if not entries:
        raise ValueError("没有已完成的 Z 频率扫描")
    seen = set()
    for entry in entries:
        path = (Path(run_dir) / entry["summary_file"]).resolve()
        if not path.is_relative_to(raw) or not path.is_file() or path in seen:
            raise ValueError("扫描索引引用了无效或重复的原始数据路径")
        seen.add(path)
        with np.load(path, allow_pickle=False) as data:
            required = {"frequency_hz", "r_std_v", "current_mean_a", "current_std_a",
                        "sense_voltage_mean_v", "sense_voltage_std_v"}
            if missing := required - set(data.files):
                raise ValueError("原始频扫缺少字段：" + "、".join(sorted(missing)))
            response = "r_mean_v" if "r_mean_v" in data else "r_scalar_mean_v"
            if response not in data:
                raise ValueError("原始频扫缺少 R 响应")
            arrays = [np.asarray(data[key], dtype=float) for key in ("frequency_hz", response, "r_std_v")]
            if any(array.ndim != 1 or not array.size for array in arrays) or len({array.size for array in arrays}) != 1:
                raise ValueError("原始频扫 R 数组必须非空、一维且长度一致")
            for key in required - {"frequency_hz", "r_std_v"}:
                if np.asarray(data[key], dtype=float).size != 1:
                    raise ValueError("原始频扫字段必须为标量：" + key)
    return entries


def fit_curves(run_dir, params, config):
    curves = []
    try:
        for entry in load_inputs(run_dir, config):
            check_cancelled()
            curves.append(_analyze_current_curve(params, run_dir, entry))
    except BaseException:
        if curves:
            try:
                write_state(run_dir / "results/curve_fits.json", plain(curves))
            except Exception:
                pass
        raise
    write_state(run_dir / "results/curve_fits.json", plain(curves))
    return dict(total_curve_count=len(curves),
                accepted_curve_count=sum(curve["fit"]["success"] for curve in curves))


def read_curves(run_dir):
    def numbers(value):
        if isinstance(value, dict):
            return {key: numbers(item) for key, item in value.items()}
        if isinstance(value, list):
            return [numbers(item) for item in value]
        return np.nan if value is None else value

    curves = numbers(json.loads((run_dir / "results/curve_fits.json").read_text(encoding="utf-8")))
    for curve in curves:
        for key in ("frequency_hz", "r_mean_v", "r_std_v"):
            curve[key] = np.asarray(curve[key], dtype=float)
    return curves


def evaluate_curves(params, run_dir, config, curves):
    """保留实际电流、电阻电压、正反向磁滞和重复性的原标定算法。"""
    accepted = np.asarray([curve["fit"]["success"] for curve in curves], dtype=bool)
    centers = np.asarray([curve["fit"]["parameters"][2] for curve in curves], dtype=float)
    center_sigma = np.asarray([curve["fit"]["center_uncertainty_hz"] for curve in curves], dtype=float)
    current = np.asarray([curve["current_a"] for curve in curves], dtype=float)
    sense_voltage = np.asarray([curve["sense_voltage_v"] for curve in curves], dtype=float)
    linear_current = _weighted_linear_fit(current[accepted], centers[accepted], center_sigma[accepted])
    linear_vsense = _weighted_linear_fit(sense_voltage[accepted], centers[accepted], center_sigma[accepted])
    reasons = list(linear_current["rejection_reasons"])
    directions = np.asarray([curve["scan_direction"] for curve in curves], dtype=str)
    if np.any(~np.isfinite(current[accepted])):
        reasons.append("有效标定点包含非有限实际电流")
    if any(
        np.isfinite(curve["display_edge_fraction"])
        and curve["display_edge_fraction"] >= 0.98
        for curve in curves
    ):
        reasons.append("采样电阻电压存在示波器贴边或饱和")
    forward_mask = accepted & (directions == "forward")
    reverse_mask = accepted & (directions == "reverse")
    forward_fit = _weighted_linear_fit(
        current[forward_mask], centers[forward_mask], center_sigma[forward_mask]
    )
    reverse_fit = _weighted_linear_fit(
        current[reverse_mask], centers[reverse_mask], center_sigma[reverse_mask]
    )
    hysteresis: dict[str, Any] = {
        "available": bool(forward_fit["success"] and reverse_fit["success"]),
        "forward_fit": forward_fit,
        "reverse_fit": reverse_fit,
    }
    if hysteresis["available"]:
        forward_slope = float(forward_fit["slope_hz_per_v"])
        reverse_slope = float(reverse_fit["slope_hz_per_v"])
        slope_scale = max(abs(forward_slope), abs(reverse_slope), 1e-15)
        hysteresis.update(
            slope_relative_difference=abs(forward_slope - reverse_slope) / slope_scale,
            zero_current_center_difference_hz=abs(
                float(forward_fit["intercept_hz"])
                - float(reverse_fit["intercept_hz"])
            ),
        )
    else:
        hysteresis["reason"] = "正向或反向有效拟合点不足"
    if not np.isfinite(linear_current["r_squared"]) or linear_current["r_squared"] < params.linear_r_squared_min:
        reasons.append(f"电流线性 R2={linear_current['r_squared']:.6g} < {params.linear_r_squared_min:.6g}")
    if not np.isfinite(linear_vsense["r_squared"]):
        reasons.append("采样电阻电压线性拟合失败")
    success = not reasons
    payload = _builtin(
        {
            "success": bool(success),
            "experiment_id": EXPERIMENT_ID,
            "run_dir": str(run_dir),
            "K_Z_Hz_per_A": linear_current["slope_hz_per_v"],
            "K_Z_Hz_per_Vsense": linear_vsense["slope_hz_per_v"],
            "f_0_at_zero_current_hz": linear_current["intercept_hz"],
            "sense_resistor_ohm": params.sense_resistor_ohm,
            "linearity": {
                "r_squared": linear_current["r_squared"],
                "fit": linear_current,
                "voltage_fit": linear_vsense,
            },
            "hysteresis": hysteresis,
            "repeatability": {
                "current_std_a_mean": float(np.nanmean([curve["current_std_a"] for curve in curves])),
                "sense_voltage_std_v_mean": float(np.nanmean([curve["sense_voltage_std_v"] for curve in curves])),
            },
            "operating_point": {
                "geometry": config.get("geometry", {}),
                "main_field_ma": params.main_magnetic_field_ma,
                "temperature_c": params.temperature_c,
                "pump_laser_power_v": params.pump_laser_power_v,
                "probe_laser_power_v": params.probe_laser_power_v,
                "sense_resistor_ohm": params.sense_resistor_ohm,
                "sense_scope_channel": params.sense_scope_channel,
                "sense_scope_sample_rate_sa_s": params.sense_scope_sample_rate_sa_s,
                "sense_scope_duration_s": params.sense_scope_duration_s,
            },
            "rejection_reasons": reasons,
            "accepted_curve_count": int(np.count_nonzero(accepted)),
            "total_curve_count": len(curves),
            "curves": [
                {
                    "current_a": curve["current_a"],
                    "current_std_a": curve["current_std_a"],
                    "sense_voltage_v": curve["sense_voltage_v"],
                    "z_bias_v": curve["z_bias_v"],
                    "summary_file": curve["summary_file"],
                    "scan_direction": curve["scan_direction"],
                    "display_edge_fraction": curve["display_edge_fraction"],
                    "fit": curve["fit"],
                }
                for curve in curves
            ],
            "files": ["current_coupling_calibration.png", "frequency_response_fits.png", "calibration_results.npz"],
        }
    )
    return payload


def evaluate(run_dir, params, config):
    payload = evaluate_curves(params, run_dir, config, read_curves(run_dir))
    write_state(run_dir / "results/calibration_evaluation.json", plain(payload))
    return {key: payload[key] for key in ("success", "K_Z_Hz_per_A", "K_Z_Hz_per_Vsense",
                                         "f_0_at_zero_current_hz", "rejection_reasons")}


def write_report(run_dir):
    curves = read_curves(run_dir)
    results_dir = run_dir / "results"
    payload = json.loads((results_dir / "calibration_evaluation.json").read_text(encoding="utf-8"))
    _plot_calibration(results_dir, curves, payload["linearity"]["fit"])
    _plot_frequency_responses(results_dir, curves)
    (results_dir / "analysis.yaml").write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    write_state(results_dir / "analysis.json", payload)
    np.savez(results_dir / "calibration_results.npz",
        current_a=np.asarray([curve["current_a"] for curve in curves], dtype=float),
        sense_voltage_v=np.asarray([curve["sense_voltage_v"] for curve in curves], dtype=float),
        fitted_center_hz=np.asarray([curve["fit"]["parameters"][2] for curve in curves], dtype=float),
        center_uncertainty_hz=np.asarray([curve["fit"]["center_uncertainty_hz"] for curve in curves], dtype=float),
        accepted=np.asarray([curve["fit"]["success"] for curve in curves], dtype=bool),
        K_Z_Hz_per_A=np.float64(payload["K_Z_Hz_per_A"]),
        K_Z_Hz_per_Vsense=np.float64(payload["K_Z_Hz_per_Vsense"]),
        f_0_at_zero_current_hz=np.float64(payload["f_0_at_zero_current_hz"]),
        linear_r_squared=np.float64(payload["linearity"]["r_squared"]))
    coefficient = np.float64(payload["K_Z_Hz_per_A"])
    print(f"Mx Z 电流标定分析完成: success={payload['success']}, "
          f"有效频扫={payload['accepted_curve_count']}/{payload['total_curve_count']}, K={coefficient:.6g} Hz/A")
    if payload["rejection_reasons"]:
        print("标定质量警告: " + "；".join(payload["rejection_reasons"]))
    for curve in curves:
        if not curve["fit"]["success"]:
            print(f"排除 {curve['summary_file']} ({curve['scan_direction']}): "
                  + "；".join(curve["fit"]["rejection_reasons"]))
    return payload
