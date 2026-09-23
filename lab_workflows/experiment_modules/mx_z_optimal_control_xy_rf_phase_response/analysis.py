"""Mx Z 最优控制 XY 平衡场 RF 相位响应离线分析。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from matplotlib import cm, colors
from matplotlib.lines import Line2D
import numpy as np
import yaml

from ...experiment_runtime import runtime_run_dir
from ...plotting import format_axis, new_figure, save_figure, set_plot_style
from ..mx_keithley_6221_optimal_control_rf_sensitivity.phase import (
    DISPERSION_MODEL_NAME,
    dispersion_phase_response,
    fit_dispersion_phase_scan,
)
from ..mx_z_optimal_control_rf_sensitivity.phase import paired_phase_order


EXPERIMENT_ID = "mx-z-optimal-control-xy-rf-phase-response"
DEFAULT_PHASE_RF_AMPLITUDE_VPP = 0.01
DEFAULT_PHASE_FIT_R_SQUARED_MIN = 0.1
DEFAULT_PHASE_FIT_AMPLITUDE_SIGMA_MIN = 3.0
NONLINEAR_FIT_MAX_STARTS = 48


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


def _load_scan(path: Path) -> dict[str, np.ndarray]:
    if not path.is_file():
        raise FileNotFoundError(f"缺少 XY RF 相位扫描数据: {path}")
    with np.load(path, allow_pickle=False) as data:
        required = {
            "x_field_v",
            "y_field_v",
            "configured_phase_deg",
            "acquisition_phase_deg",
            "r_mean_v",
            "r_std_v",
            "acquisition_order",
            "fit_accepted",
            "fit_rejection_reason",
            "baseline_v",
            "amplitude_v",
            "phase_zero_deg",
            "selected_phase_deg",
            "r_squared",
            "actual_rate_sa_s",
            "diagnostic_fit_accepted",
            "diagnostic_fit_rejection_reason",
            "diagnostic_model",
            "diagnostic_baseline_v",
            "diagnostic_amplitude_v",
            "diagnostic_phase_zero_deg",
            "diagnostic_baseline_uncertainty_v",
            "diagnostic_amplitude_uncertainty_v",
            "diagnostic_phase_zero_uncertainty_deg",
            "diagnostic_selected_phase_deg",
            "diagnostic_observed_max_phase_deg",
            "diagnostic_r_squared",
        }
        missing = sorted(required.difference(data.files))
        if missing:
            raise ValueError(f"扫描数据缺少字段: {', '.join(missing)}")
        return {key: np.asarray(data[key]) for key in data.files}


def _reacquisition_summary(data: dict[str, np.ndarray]) -> dict[str, Any]:
    """汇总采集期跨相位异常重采；兼容没有该字段的历史运行目录。"""
    reacquired = np.asarray(
        data.get("cross_phase_reacquired", np.zeros_like(data["r_mean_v"], dtype=bool)),
        dtype=bool,
    )
    if reacquired.shape != np.asarray(data["r_mean_v"]).shape:
        raise ValueError("cross_phase_reacquired 形状必须与 r_mean_v 一致")
    points = np.any(reacquired, axis=-1)
    return {
        "reacquired_sample_count": int(np.count_nonzero(reacquired)),
        "reacquired_point_count": int(np.count_nonzero(points)),
        "reacquired_phase_mask": _builtin(reacquired),
    }


def _validate_scan(data: dict[str, np.ndarray]) -> None:
    x_axis = np.asarray(data["x_field_v"], dtype=float)
    y_axis = np.asarray(data["y_field_v"], dtype=float)
    configured_phase_axis = np.asarray(data["configured_phase_deg"], dtype=float)
    phase_axis = np.asarray(data["acquisition_phase_deg"], dtype=float)
    expected_grid = (x_axis.size, y_axis.size)
    expected_phase = (*expected_grid, phase_axis.size)
    if min((*expected_grid, phase_axis.size)) < 1:
        raise ValueError("XY 或相位扫描轴不能为空")
    for name, axis in (
        ("X", x_axis),
        ("Y", y_axis),
        ("configured phase", configured_phase_axis),
        ("phase", phase_axis),
    ):
        if not np.all(np.isfinite(axis)):
            raise ValueError(f"{name} 扫描轴包含 NaN 或无穷值")
    if configured_phase_axis.size != phase_axis.size:
        raise ValueError("配置相位轴与实际采集相位轴长度不一致")
    wrapped_configured = np.mod(configured_phase_axis, 360.0)
    if np.unique(np.round(wrapped_configured, decimals=9)).size != phase_axis.size:
        raise ValueError("配置相位轴包含重复点")
    try:
        expected_acquisition_phase = paired_phase_order(configured_phase_axis)
    except ValueError as exc:
        raise ValueError(f"配置相位轴不满足 180° 配对规则: {exc}") from exc
    if not np.allclose(phase_axis, expected_acquisition_phase, atol=1e-9, rtol=0.0):
        raise ValueError("实际采集相位轴不符合规范 180° 配对顺序")
    for key in ("r_mean_v", "r_std_v"):
        if np.asarray(data[key]).shape != expected_phase:
            raise ValueError(f"{key} 形状应为 {expected_phase}")
        if not np.all(np.isfinite(data[key])):
            raise ValueError(f"{key} 包含 NaN 或无穷值")
    for key in (
        "acquisition_order",
        "fit_accepted",
        "baseline_v",
        "amplitude_v",
        "phase_zero_deg",
        "selected_phase_deg",
        "r_squared",
    ):
        if np.asarray(data[key]).shape != expected_grid:
            raise ValueError(f"{key} 形状应为 {expected_grid}")
    for key in (
        "diagnostic_fit_accepted",
        "diagnostic_baseline_v",
        "diagnostic_amplitude_v",
        "diagnostic_phase_zero_deg",
        "diagnostic_baseline_uncertainty_v",
        "diagnostic_amplitude_uncertainty_v",
        "diagnostic_phase_zero_uncertainty_deg",
        "diagnostic_selected_phase_deg",
        "diagnostic_observed_max_phase_deg",
        "diagnostic_r_squared",
    ):
        if np.asarray(data[key]).shape != expected_grid:
            raise ValueError(f"{key} 形状应为 {expected_grid}")
    if np.asarray(data["diagnostic_model"]).shape != expected_grid:
        raise ValueError(f"diagnostic_model 形状应为 {expected_grid}")
    for key in ("fit_rejection_reason", "diagnostic_fit_rejection_reason"):
        if np.asarray(data[key]).shape != expected_grid:
            raise ValueError(f"{key} 形状应为 {expected_grid}")
    if not np.all(np.isfinite(data["acquisition_order"])):
        raise ValueError("采集顺序包含无效值")


def _parameter_value(
    config: dict[str, Any],
    key: str,
    default: float,
) -> float:
    """读取运行配置中的数值参数，兼容旧运行目录。"""
    parameters = config.get("parameters", {})
    if not isinstance(parameters, dict):
        return float(default)
    value = parameters.get(key, default)
    try:
        value = float(value)
    except (TypeError, ValueError):
        return float(default)
    return value if np.isfinite(value) else float(default)


def _nonlinear_fit_grid(
    data: dict[str, np.ndarray],
    config: dict[str, Any],
) -> dict[str, Any]:
    """只用 Demod0 R 对每个 XY 点做非线性色散拟合。

    RF 幅度是实验设定值，不需要 ``kx``、``ky`` 或 ``rf`` 的先验标定。
    X/Y 电压仅作为网格坐标保存，拟合参数是响应空间中的等效量。
    """
    x_axis = np.asarray(data["x_field_v"], dtype=float)
    y_axis = np.asarray(data["y_field_v"], dtype=float)
    phase = np.asarray(data["acquisition_phase_deg"], dtype=float)
    r_mean = np.asarray(data["r_mean_v"], dtype=float)
    r_std = np.asarray(data["r_std_v"], dtype=float)
    shape = (x_axis.size, y_axis.size)
    rf_amplitude = _parameter_value(
        config,
        "PHASE_CAL_RF_AMPLITUDE_VPP",
        DEFAULT_PHASE_RF_AMPLITUDE_VPP,
    )
    r_squared_min = _parameter_value(
        config,
        "PHASE_FIT_R_SQUARED_MIN",
        DEFAULT_PHASE_FIT_R_SQUARED_MIN,
    )
    amplitude_sigma_min = _parameter_value(
        config,
        "PHASE_FIT_AMPLITUDE_SIGMA_MIN",
        DEFAULT_PHASE_FIT_AMPLITUDE_SIGMA_MIN,
    )
    if rf_amplitude <= 0.0:
        raise ValueError(
            "PHASE_CAL_RF_AMPLITUDE_VPP 必须为正值，才能进行非线性色散拟合"
        )

    parameter_names = (
        "scale",
        "resonance_amplitude_vpp",
        "width_vpp",
        "residual_amplitude_vpp",
        "residual_phase_deg",
    )
    parameters = {
        name: np.full(shape, np.nan, dtype=float) for name in parameter_names
    }
    uncertainties = {
        f"{name}_uncertainty": np.full(shape, np.nan, dtype=float)
        for name in parameter_names
    }
    covariance = np.full((*shape, len(parameter_names), len(parameter_names)), np.nan)
    selected_phase = np.full(shape, np.nan, dtype=float)
    observed_max_phase = np.full(shape, np.nan, dtype=float)
    peak_to_peak = np.full(shape, np.nan, dtype=float)
    noise_median = np.full(shape, np.nan, dtype=float)
    signal_to_noise = np.full(shape, np.nan, dtype=float)
    r_squared = np.full(shape, np.nan, dtype=float)
    rmse = np.full(shape, np.nan, dtype=float)
    accepted = np.zeros(shape, dtype=bool)
    reasons = np.full(shape, "", dtype="U2000")

    for x_index in range(x_axis.size):
        for y_index in range(y_axis.size):
            try:
                fit = fit_dispersion_phase_scan(
                    phase,
                    r_mean[x_index, y_index],
                    r_std[x_index, y_index],
                    y_rf_amplitude_vpp=rf_amplitude,
                    r_squared_min=r_squared_min,
                    amplitude_sigma_min=amplitude_sigma_min,
                    max_starts=NONLINEAR_FIT_MAX_STARTS,
                )
                fit_parameters = np.asarray(fit.parameters, dtype=float)
                fit_uncertainties = np.asarray(fit.uncertainties, dtype=float)
                fit_covariance = np.asarray(fit.covariance, dtype=float)
                for parameter_index, name in enumerate(parameter_names):
                    parameters[name][x_index, y_index] = fit_parameters[parameter_index]
                    uncertainties[f"{name}_uncertainty"][x_index, y_index] = (
                        fit_uncertainties[parameter_index]
                    )
                if fit_covariance.shape == (len(parameter_names), len(parameter_names)):
                    covariance[x_index, y_index] = fit_covariance
                selected_phase[x_index, y_index] = fit.selected_phase_deg
                observed_max_phase[x_index, y_index] = fit.observed_max_phase_deg
                peak_to_peak[x_index, y_index] = fit.peak_to_peak_v
                noise_median[x_index, y_index] = fit.noise_median_v
                signal_to_noise[x_index, y_index] = fit.signal_to_noise
                r_squared[x_index, y_index] = fit.r_squared
                fitted = dispersion_phase_response(
                    phase,
                    *fit.parameters,
                    rf_amplitude,
                )
                rmse[x_index, y_index] = float(
                    np.sqrt(np.mean((r_mean[x_index, y_index] - fitted) ** 2))
                )
                accepted[x_index, y_index] = bool(fit.success)
                reasons[x_index, y_index] = ";".join(fit.rejection_reasons)
            except (RuntimeError, ValueError, FloatingPointError, TypeError) as exc:
                reasons[x_index, y_index] = f"非线性色散拟合异常: {exc}"

    return {
        "model": DISPERSION_MODEL_NAME,
        "input_signal": "Demod0 R",
        "rf_amplitude_vpp": float(rf_amplitude),
        "r_squared_min": float(r_squared_min),
        "amplitude_sigma_min": float(amplitude_sigma_min),
        "parameters": parameters,
        "uncertainties": uncertainties,
        "covariance": covariance,
        "selected_phase_deg": selected_phase,
        "observed_max_phase_deg": observed_max_phase,
        "peak_to_peak_v": peak_to_peak,
        "noise_median_v": noise_median,
        "signal_to_noise": signal_to_noise,
        "r_squared": r_squared,
        "rmse_v": rmse,
        "fit_accepted": accepted,
        "fit_rejection_reason": reasons,
    }


def _invalid_points(
    data: dict[str, np.ndarray],
    accepted: np.ndarray,
    reasons: np.ndarray,
) -> list[dict[str, Any]]:
    """将无效网格点转换为可序列化的列表。"""
    return [
        {
            "x_index": int(x_index),
            "y_index": int(y_index),
            "x_field_v": float(data["x_field_v"][x_index]),
            "y_field_v": float(data["y_field_v"][y_index]),
            "reason": str(reasons[x_index, y_index]),
        }
        for x_index, y_index in np.argwhere(~accepted)
    ]


def _plot_nonlinear_maps(
    results_dir: Path,
    data: dict[str, np.ndarray],
    fit: dict[str, Any],
) -> str:
    """绘制非线性色散模型的 XY 参数地图。"""
    set_plot_style("paper")
    x_axis = np.asarray(data["x_field_v"], dtype=float)
    y_axis = np.asarray(data["y_field_v"], dtype=float)
    accepted = np.asarray(fit["fit_accepted"], dtype=bool)
    fields = (
        ("selected_phase_deg", "Selected RF phase (deg)", "twilight"),
        ("residual_amplitude_vpp", "Residual RF amplitude (Vpp)", "plasma"),
        ("resonance_amplitude_vpp", "Resonance amplitude (Vpp)", "cividis"),
        ("width_vpp", "Dispersion width (Vpp)", "magma"),
        ("r_squared", "Nonlinear fit R squared", "inferno"),
        ("fit_accepted", "Nonlinear fit accepted", "gray"),
    )
    fig, axes = new_figure(nrows=3, ncols=2, constrained_layout=True, width_mm=177.8, height_mm=165)
    axes = np.asarray(axes, dtype=object).reshape(3, 2)
    for axis, (key, label, cmap) in zip(axes.flat, fields):
        if key == "fit_accepted":
            values = accepted.astype(float)
        elif key == "selected_phase_deg":
            values = np.asarray(fit[key], dtype=float)
        else:
            values = np.asarray(
                fit["parameters"].get(key, fit.get(key)),
                dtype=float,
            )
        if key != "fit_accepted":
            values = np.where(accepted, values, np.nan)
        mesh = axis.pcolormesh(
            x_axis,
            y_axis,
            values.T,
            shading="auto",
            cmap=cmap, rasterized=True,
            vmin=0 if key in {"selected_phase_deg", "fit_accepted"} else None,
            vmax=360 if key == "selected_phase_deg" else (1 if key == "fit_accepted" else None))
        format_axis(
            axis,
            xlabel="X field setting (V)",
            ylabel="Y field / RF offset (V)",
        )
        axis.set_title(label)
        fig.colorbar(mesh, ax=axis, shrink=0.9)
    filename = "xy_rf_phase_response_nonlinear_maps.png"
    save_figure(fig, results_dir / filename)
    return filename


def _plot_maps(results_dir: Path, data: dict[str, np.ndarray]) -> str:
    set_plot_style("paper")
    x_axis = np.asarray(data["x_field_v"], dtype=float)
    y_axis = np.asarray(data["y_field_v"], dtype=float)
    accepted = np.asarray(data["fit_accepted"], dtype=bool)
    fields = (
        ("selected_phase_deg", "Selected RF phase (deg)", "twilight"),
        ("amplitude_v", "Primary amplitude (V)", "plasma"),
        ("baseline_v", "Primary baseline (V)", "viridis"),
        ("r_squared", "Primary fit R squared", "magma"),
        ("fit_accepted", "Fit accepted", "gray"),
    )
    fig, axes = new_figure(nrows=3, ncols=2, constrained_layout=True, width_mm=177.8, height_mm=165)
    axes = np.asarray(axes, dtype=object).reshape(3, 2)
    for axis, (key, label, cmap) in zip(axes.flat, fields):
        values = accepted.astype(float) if key == "fit_accepted" else np.asarray(data[key], dtype=float)
        if key != "fit_accepted":
            values = np.where(accepted, values, np.nan)
        mesh = axis.pcolormesh(x_axis, y_axis, values.T, shading="auto", cmap=cmap, rasterized=True,
            vmin=0 if key in {"selected_phase_deg", "fit_accepted"} else None,
            vmax=360 if key == "selected_phase_deg" else (1 if key == "fit_accepted" else None))
        format_axis(axis, xlabel="X field setting (V)", ylabel="Y field / RF offset (V)")
        axis.set_title(label)
        fig.colorbar(mesh, ax=axis, shrink=0.9)
    axes[2, 1].axis("off")
    filename = "xy_rf_phase_response_maps.png"
    save_figure(fig, results_dir / filename)
    return filename


def _plot_phase_curves(results_dir: Path, data: dict[str, np.ndarray]) -> str:
    """绘制所有 XY 偏置下的 RF 相位响应曲线。

    聚合文件按实际触发顺序保存相位；绘图时按相位数值重排，避免把
    180° 配对采集顺序误画成相位曲线。正式拟合拒绝的网格点使用虚线，
    但仍保留原始曲线，便于定位异常相位点。
    """
    set_plot_style("paper")
    x_axis = np.asarray(data["x_field_v"], dtype=float)
    y_axis = np.asarray(data["y_field_v"], dtype=float)
    phase_raw = np.asarray(data["acquisition_phase_deg"], dtype=float)
    phase_order = np.argsort(np.mod(phase_raw, 360.0), kind="stable")
    phase = np.mod(phase_raw[phase_order], 360.0)
    r_mean = np.asarray(data["r_mean_v"], dtype=float)[..., phase_order]
    accepted = np.asarray(data["fit_accepted"], dtype=bool)

    columns = min(3, x_axis.size)
    rows = int(np.ceil(x_axis.size / columns))
    fig, axes = new_figure(nrows=rows, ncols=columns, sharex=True, sharey=True,
                           squeeze=False, constrained_layout=False, width_mm=177.8,
                           height_mm=max(75, 55 * rows))
    y_min = float(np.nanmin(r_mean))
    y_max = float(np.nanmax(r_mean))
    y_padding = max(0.06 * (y_max - y_min), 0.01)
    y_min -= y_padding
    y_max += y_padding
    y_low = float(np.min(y_axis))
    y_high = float(np.max(y_axis))
    if np.isclose(y_low, y_high):
        y_low -= 0.5
        y_high += 0.5
    norm = colors.Normalize(vmin=y_low, vmax=y_high)

    for panel, x_index in enumerate(range(x_axis.size)):
        row, column = divmod(panel, columns)
        axis = axes[row, column]
        for y_index, y_value in enumerate(y_axis):
            axis.plot(
                phase,
                r_mean[x_index, y_index],
                color=cm.viridis(norm(float(y_value))),
                linestyle="-" if accepted[x_index, y_index] else "--",
                marker="o",
                markersize=2.0,
                linewidth=1.1,
                alpha=0.9 if accepted[x_index, y_index] else 0.7,
            )
        rejected_count = int(np.count_nonzero(~accepted[x_index]))
        title = f"X = {x_axis[x_index]:+.2f} V"
        if rejected_count:
            title += f"\nRejected: {rejected_count}"
        axis.set_title(title)
        axis.set_xlim(0.0, 360.0)
        axis.set_ylim(y_min, y_max)
        axis.set_xticks(np.arange(0.0, 361.0, 120.0))
        axis.grid(True, alpha=0.22, linewidth=0.5)
        format_axis(
            axis,
            xlabel="RF phase (deg)" if row == rows - 1 else "",
            ylabel="Demod0 R (V)" if column == 0 else "",
        )

    for axis in axes.flat[x_axis.size:]:
        axis.axis("off")

    fig.subplots_adjust(
        left=0.06,
        right=0.87,
        top=0.95,
        bottom=0.09,
        wspace=0.16,
        hspace=0.28,
    )
    scalar_mappable = cm.ScalarMappable(norm=norm, cmap="viridis")
    scalar_mappable.set_array(y_axis)
    colorbar_axis = fig.add_axes((0.895, 0.16, 0.018, 0.70))
    colorbar = fig.colorbar(
        scalar_mappable,
        cax=colorbar_axis,
    )
    colorbar.set_label("Y offset / RF DC offset (V)")
    colorbar.set_ticks(y_axis)
    colorbar.ax.set_yticklabels([f"{value:+.2f}" for value in y_axis])
    handles = [
        Line2D([0], [0], color="0.25", linestyle="-", linewidth=1.4, label="Fit accepted"),
        Line2D([0], [0], color="0.25", linestyle="--", linewidth=1.4, label="Fit rejected"),
    ]
    fig.legend(
        handles=handles,
        loc="lower center",
        bbox_to_anchor=(0.5, 0.012),
        ncol=2,
        frameon=False,
    )
    fig.suptitle("RF phase response across XY bias", y=0.995)
    filename = "xy_rf_phase_response_curves.png"
    save_figure(fig, results_dir / filename)
    return filename


def analyze(run_dir: Path) -> dict[str, Any]:
    """读取二维相位响应网格并生成参数地图。"""
    run_dir = Path(run_dir).resolve()
    results_dir = run_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    config = _load_yaml(run_dir / "experiment_config.yaml")
    data = _load_scan(run_dir / "raw" / "xy_rf_phase_response_scan.npz")
    _validate_scan(data)
    plot_file = _plot_maps(results_dir, data)
    curve_file = _plot_phase_curves(results_dir, data)
    nonlinear_fit = _nonlinear_fit_grid(data, config)
    nonlinear_plot_file = _plot_nonlinear_maps(
        results_dir,
        data,
        nonlinear_fit,
    )
    accepted = np.asarray(data["fit_accepted"], dtype=bool)
    reacquisition = _reacquisition_summary(data)
    invalid = _invalid_points(data, accepted, data["fit_rejection_reason"])
    nonlinear_accepted = np.asarray(
        nonlinear_fit["fit_accepted"],
        dtype=bool,
    )
    nonlinear_invalid = _invalid_points(
        data,
        nonlinear_accepted,
        nonlinear_fit["fit_rejection_reason"],
    )
    nonlinear_parameter_maps = {
        **{
            key: _builtin(value)
            for key, value in nonlinear_fit["parameters"].items()
        },
        **{
            key: _builtin(value)
            for key, value in nonlinear_fit["uncertainties"].items()
        },
        "covariance": _builtin(nonlinear_fit["covariance"]),
        "selected_phase_deg": _builtin(nonlinear_fit["selected_phase_deg"]),
        "observed_max_phase_deg": _builtin(
            nonlinear_fit["observed_max_phase_deg"]
        ),
        "peak_to_peak_v": _builtin(nonlinear_fit["peak_to_peak_v"]),
        "noise_median_v": _builtin(nonlinear_fit["noise_median_v"]),
        "signal_to_noise": _builtin(nonlinear_fit["signal_to_noise"]),
        "r_squared": _builtin(nonlinear_fit["r_squared"]),
        "rmse_v": _builtin(nonlinear_fit["rmse_v"]),
        "fit_accepted": _builtin(nonlinear_accepted),
    }
    config_parameters = config.get("parameters", {})
    use_nonlinear_primary = (
        isinstance(config_parameters, dict)
        and "PHASE_CAL_RF_AMPLITUDE_VPP" in config_parameters
    )
    primary_accepted = nonlinear_accepted if use_nonlinear_primary else accepted
    primary_invalid = nonlinear_invalid if use_nonlinear_primary else invalid
    primary_parameter_maps = (
        nonlinear_parameter_maps
        if use_nonlinear_primary
        else {
            "selected_phase_deg": _builtin(data["selected_phase_deg"]),
            "amplitude_v": _builtin(data["amplitude_v"]),
            "baseline_v": _builtin(data["baseline_v"]),
            "phase_zero_deg": _builtin(data["phase_zero_deg"]),
            "r_squared": _builtin(data["r_squared"]),
            "fit_accepted": _builtin(accepted),
        }
    )
    result = {
        "experiment_id": EXPERIMENT_ID,
        "run_dir": str(run_dir),
        "scan_completed": config.get("completion_status") == "completed",
        "scan_shape": [int(data["x_field_v"].size), int(data["y_field_v"].size)],
        "phase_points": int(data["acquisition_phase_deg"].size),
        "scan_order": "X outer, Y serpentine; phase paired phi then phi+180",
        "fit_model": (
            DISPERSION_MODEL_NAME
            if use_nonlinear_primary
            else "abs(C + A*sin(phi-phi0))"
        ),
        "fit_input": "Demod0 R only",
        "calibration_required": False,
        "calibration_note": (
            "kx/ky/rf 未参与拟合；X/Y 电压仅作为网格坐标，"
            "非线性模型参数表示等效响应量。"
        ),
        "nonlinear_fit_settings": {
            "rf_amplitude_vpp": nonlinear_fit["rf_amplitude_vpp"],
            "r_squared_min": nonlinear_fit["r_squared_min"],
            "amplitude_sigma_min": nonlinear_fit["amplitude_sigma_min"],
            "max_starts": NONLINEAR_FIT_MAX_STARTS,
        },
        # 保留采集时旧正弦拟合字段，便于和历史 raw 文件逐点对照。
        "legacy_fit_model": "abs(C + A*sin(phi-phi0))",
        "fit_accepted_count": int(np.count_nonzero(primary_accepted)),
        "fit_rejected_count": int(np.count_nonzero(~primary_accepted)),
        "invalid_points": primary_invalid,
        "nonlinear_fit_accepted_count": int(
            np.count_nonzero(nonlinear_accepted)
        ),
        "nonlinear_fit_rejected_count": int(
            np.count_nonzero(~nonlinear_accepted)
        ),
        "nonlinear_invalid_points": nonlinear_invalid,
        "parameter_maps": primary_parameter_maps,
        "legacy_parameter_maps": {
            "selected_phase_deg": _builtin(data["selected_phase_deg"]),
            "amplitude_v": _builtin(data["amplitude_v"]),
            "baseline_v": _builtin(data["baseline_v"]),
            "phase_zero_deg": _builtin(data["phase_zero_deg"]),
            "r_squared": _builtin(data["r_squared"]),
            "fit_accepted": _builtin(accepted),
        },
        "nonlinear_parameter_maps": nonlinear_parameter_maps,
        "diagnostic_parameter_maps": {
            "baseline_v": _builtin(data["diagnostic_baseline_v"]),
            "amplitude_v": _builtin(data["diagnostic_amplitude_v"]),
            "phase_zero_deg": _builtin(data["diagnostic_phase_zero_deg"]),
            "baseline_uncertainty_v": _builtin(
                data["diagnostic_baseline_uncertainty_v"]
            ),
            "amplitude_uncertainty_v": _builtin(
                data["diagnostic_amplitude_uncertainty_v"]
            ),
            "phase_zero_uncertainty_deg": _builtin(
                data["diagnostic_phase_zero_uncertainty_deg"]
            ),
            "selected_phase_deg": _builtin(
                data["diagnostic_selected_phase_deg"]
            ),
            "observed_max_phase_deg": _builtin(
                data["diagnostic_observed_max_phase_deg"]
            ),
            "r_squared": _builtin(data["diagnostic_r_squared"]),
            "fit_accepted": _builtin(data["diagnostic_fit_accepted"]),
        },
        "control_source": config.get("control_source", {}),
        "z_calibration": config.get("z_calibration", {}),
        "applied_control": config.get("applied_control", {}),
        "scan_axes": config.get("scan_axes", {}),
        "response_summary": {
            "actual_rate_sa_s": float(data["actual_rate_sa_s"]),
            "mean_r_v": float(np.mean(data["r_mean_v"])),
            "mean_point_std_v": float(np.mean(data["r_std_v"])),
            "cross_phase_reacquisition": reacquisition,
        },
        "warnings": [
            "本实验只报告 XY 网格上的相位拟合参数，不自动选择最佳平衡点。",
            "拟合失败网格点保留原始数据并在参数地图中标记为无效。",
            "Demod0 X/Y 未作为本次拟合输入；只用 Demod0 R 无法唯一反推出 kx、ky 或 rf 标定。",
        ],
        "files": [plot_file, curve_file, nonlinear_plot_file],
    }
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
    result = analyze(run_dir)
    print(f"Mx Z 最优控制 XY 平衡场 RF 相位响应分析完成: {result['run_dir']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
