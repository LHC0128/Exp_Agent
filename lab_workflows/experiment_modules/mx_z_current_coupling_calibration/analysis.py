"""Mx Z 实际电流-耦合强度标定分析。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from ...plotting import format_axis, new_figure, save_figure, set_plot_style
from ..mx_z_field_calibration.analysis import (
    _analyze_curve,
    _builtin,
    _load_scan_index,
    _weighted_linear_fit,
)
from ..mx_y_rf_sensitivity.analysis_core import fit_lorentzian_response
from .models import MxZCurrentCouplingCalibrationParams


EXPERIMENT_ID = "mx-z-current-coupling-calibration"


def _load_params(run_dir: Path) -> tuple[MxZCurrentCouplingCalibrationParams, dict[str, Any]]:
    with (run_dir / "experiment_config.yaml").open(encoding="utf-8") as stream:
        config = yaml.safe_load(stream) or {}
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


def _plot_calibration(results_dir: Path, curves: list[dict[str, Any]], fit: dict[str, Any]) -> str:
    set_plot_style("paper")
    current = np.asarray([curve["current_a"] for curve in curves], dtype=float)
    centers = np.asarray([curve["fit"]["parameters"][2] for curve in curves], dtype=float)
    sigma = np.asarray([curve["fit"]["center_uncertainty_hz"] for curve in curves], dtype=float)
    accepted = np.asarray([curve["fit"]["success"] for curve in curves], dtype=bool)
    figure, axis = new_figure()
    axis.errorbar(current[accepted], centers[accepted], yerr=sigma[accepted], fmt="o", capsize=2, label="Accepted centers")
    if fit["success"]:
        grid = np.linspace(float(np.min(current)), float(np.max(current)), 400)
        axis.plot(
            grid,
            fit["slope_hz_per_v"] * grid + fit["intercept_hz"],
            ls="--",
            label=f"Linear fit, R2={fit['r_squared']:.5f}",
        )
    format_axis(axis, xlabel="Measured coil current (A)", ylabel="Resonance center (Hz)")
    axis.grid(True, alpha=0.25)
    axis.legend(loc="best")
    filename = "current_coupling_calibration.png"
    save_figure(figure, results_dir / filename)
    return filename


def analyze(run_dir: Path) -> dict[str, Any]:
    """按实际电流拟合 Mx 共振中心并保存电流耦合标定。"""
    run_dir = Path(run_dir).resolve()
    params, config = _load_params(run_dir)
    entries = _load_scan_index(run_dir / "raw")
    curves = [_analyze_current_curve(params, run_dir, entry) for entry in entries]
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
    results_dir = run_dir / "results"
    results_dir.mkdir(exist_ok=True)
    plot = _plot_calibration(results_dir, curves, linear_current)
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
            "curves": [
                {
                    "current_a": curve["current_a"],
                    "current_std_a": curve["current_std_a"],
                    "sense_voltage_v": curve["sense_voltage_v"],
                    "z_bias_v": curve["z_bias_v"],
                    "scan_direction": curve["scan_direction"],
                    "display_edge_fraction": curve["display_edge_fraction"],
                    "fit": curve["fit"],
                }
                for curve in curves
            ],
            "files": [plot, "calibration_results.npz"],
        }
    )
    (results_dir / "analysis.yaml").write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    (results_dir / "analysis.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    np.savez(
        results_dir / "calibration_results.npz",
        current_a=current,
        sense_voltage_v=sense_voltage,
        fitted_center_hz=centers,
        center_uncertainty_hz=center_sigma,
        accepted=accepted,
        K_Z_Hz_per_A=np.float64(linear_current["slope_hz_per_v"]),
        K_Z_Hz_per_Vsense=np.float64(linear_vsense["slope_hz_per_v"]),
        f_0_at_zero_current_hz=np.float64(linear_current["intercept_hz"]),
        linear_r_squared=np.float64(linear_current["r_squared"]),
    )
    return payload


def main() -> int:
    from ...experiment_runtime import runtime_run_dir

    analyze(runtime_run_dir())
    return 0
