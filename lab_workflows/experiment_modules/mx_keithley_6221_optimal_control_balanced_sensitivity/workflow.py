"""Mx Keithley 6221 最优控制 平衡-灵敏度一体化采集工作流。

编排顺序：加载控制波形 → 连接设备 → 时钟/温控/触发/6221/HF2 配置 →
XYZ 平衡粗扫 → 逐层拟合决策 → 可选细扫 → 以拟合工作点配置 X/Y/GS200 →
Y RF 相位校准 → 带符号幅度扫描 → 零 Y RF 噪声采集。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from ...common import find_project_root, load_mapping
from ...experiment_runtime import check_cancelled, load_runtime_params
from ...steps import (
    DeviceSession,
    configure_fixed_dc_field,
    configure_temperature_control,
    create_run_directory,
    set_temperature_switch,
    wait_for_temperature_stable,
)
from ..mx_keithley_6221_optimal_control_rf_sensitivity.sources import (
    build_applied_current,
    load_keithley_calibration,
    load_theory_control,
)
from ..mx_keithley_6221_optimal_control_rf_sensitivity.workflow import (
    _Cancellation,
    _acquire_phase_scan,
    _configure_6221,
    _configure_clocks,
    _configure_gs200,
    _configure_hf2,
    _configure_trigger,
    _connect_devices,
    _install_compliance_checker,
    _source_snapshot,
    safe_shutdown,
)
from ..mx_keithley_6221_optimal_control_xyz_balance.workflow import (
    _XYZFieldController,
    _configure_xy_idle,
)
from ..mx_y_rf_sensitivity.workflow import (
    _acquire_noise,
    _acquire_valid_r_point,
)
from ..mx_z_optimal_control_rf_sensitivity.workflow import (
    _acquire_amplitude_scan,
    _configure_y_rf_output,
)
from ..mx_z_optimal_control_xyz_balance.analysis import (
    analyze_xyz_linear_fit,
)
from .models import MxKeithley6221OptimalControlBalancedSensitivityParams

EXPERIMENT_ID = "mx-keithley-6221-optimal-control-balanced-sensitivity"
DATA_TYPE = "Mx_Keithley_6221_Optimal_Control_Balanced_Sensitivity"
EXECUTION_MODE = "typed_workflow"


def _axis(start: float, stop: float, points: int) -> np.ndarray:
    return np.linspace(float(start), float(stop), int(points))


def _save_balance_npz(
    run_dir: Any,
    filename: str,
    data: dict[str, np.ndarray],
) -> None:
    np.savez(
        run_dir.raw / filename,
        x_field_v=data["x_field_v"],
        y_field_v=data["y_field_v"],
        z_field_ma=data["z_field_ma"],
        r_mean_v=data["r_mean_v"],
        r_std_v=data["r_std_v"],
        acquisition_order=data["acquisition_order"],
        actual_rate_sa_s=np.float64(data["actual_rate_sa_s"]),
    )


def _run_balance_scan(
    params: MxKeithley6221OptimalControlBalancedSensitivityParams,
    run_dir: Any,
    devices: dict[str, Any],
    channels: dict[str, int],
    actual_rate: float,
    device_id: str,
    x_axis: np.ndarray,
    y_axis: np.ndarray,
    z_axis: np.ndarray,
    round_label: str,
) -> dict[str, np.ndarray]:
    """三维网格扫描（Z 外层、X/Y 蛇形），逐点温控门控采集 R。"""
    controller = _XYZFieldController(
        devices["xy_field"],
        devices["gs200"],
        {"x_field": channels["x_field"], "y_rf": channels["y_rf"]},
    )
    nz, nx, ny = len(z_axis), len(x_axis), len(y_axis)
    r_mean = np.full((nz, nx, ny), np.nan)
    r_std = np.full((nz, nx, ny), np.nan)
    order = np.zeros((nz, nx, ny), dtype=int)
    counter = 0
    for zi, z in enumerate(z_axis):
        for xi, x in enumerate(x_axis):
            y_indices = range(ny) if xi % 2 == 0 else range(ny - 1, -1, -1)
            for yi in y_indices:
                y = float(y_axis[yi])
                check_cancelled()
                print(
                    f"平衡 {round_label} {counter + 1}/{nz * nx * ny}: "
                    f"X={x:+.6f} V, Y={y:+.6f} V, Z={z:+.6f} mA"
                )
                controller.apply(float(x), y, float(z))
                summary, _, _ = _acquire_valid_r_point(
                    params,
                    run_dir,
                    devices,
                    channels,
                    file_stem=(
                        f"balance_{round_label}_{zi:02d}_{xi:02d}_{yi:02d}"
                    ),
                    metadata={
                        "x_field_v": np.float64(x),
                        "y_field_v": np.float64(y),
                        "z_field_ma": np.float64(z),
                    },
                    set_y_rf=lambda: None,
                    settle_time_s=params.response_settle_time_s,
                    duration_s=params.response_duration_s,
                    actual_rate=actual_rate,
                    device_id=device_id,
                )
                r_mean[zi, xi, yi] = float(summary["r_mean_v"])
                r_std[zi, xi, yi] = float(summary["r_std_v"])
                order[zi, xi, yi] = counter
                counter += 1
    return {
        "x_field_v": x_axis,
        "y_field_v": y_axis,
        "z_field_ma": z_axis,
        "r_mean_v": r_mean,
        "r_std_v": r_std,
        "acquisition_order": order,
        "actual_rate_sa_s": float(actual_rate),
    }


def _fit_round(
    run_dir: Any,
    data: dict[str, np.ndarray],
    coupling_min_r_squared: float,
) -> dict[str, Any]:
    """进程内运行逐层复线性模拟合，返回 linear_fit 片段。"""
    result = analyze_xyz_linear_fit(
        data,
        run_dir.results,
        coupling_min_r_squared=coupling_min_r_squared,
    )
    return result["linear_fit"]


def _fine_axes(
    params: MxKeithley6221OptimalControlBalancedSensitivityParams,
    fit: dict[str, Any],
) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    """按粗扫拟合工作点构造细扫轴；工作点缺失时返回 None。"""
    if not params.balance_fine_enabled:
        return None
    workpoint = fit.get("fitted_workpoint", {})
    x0 = workpoint.get("x_field_v")
    y0 = workpoint.get("y_field_v")
    z0 = workpoint.get("z_field_ma")
    if x0 is None or y0 is None or z0 is None:
        return None
    span = params.balance_fine_span_v
    return (
        _axis(x0 - span, x0 + span, params.balance_fine_points),
        _axis(y0 - span, y0 + span, params.balance_fine_points),
        _axis(
            z0 + params.balance_fine_z_start_ma,
            z0 + params.balance_fine_z_stop_ma,
            params.balance_fine_z_points,
        ),
    )


def _best_workpoint(fit: dict[str, Any], fallback: MxKeithley6221OptimalControlBalancedSensitivityParams) -> tuple[float, float, float]:
    """取拟合工作点；分量缺失时回退父类固定补偿参数。"""
    workpoint = fit.get("fitted_workpoint", {})
    x0 = workpoint.get("x_field_v")
    y0 = workpoint.get("y_field_v")
    z0 = workpoint.get("z_field_ma")
    return (
        float(x0) if x0 is not None else float(fallback.x_dc_field_v),
        float(y0) if y0 is not None else float(fallback.y_rf_offset_v),
        float(z0) if z0 is not None else float(fallback.main_magnetic_field_ma),
    )


def run(params: MxKeithley6221OptimalControlBalancedSensitivityParams) -> Path:
    root = find_project_root()
    if not params.confirm_gs200_connected:
        raise ValueError("必须确认 GS200 已接入 Z 主磁场线圈（6221 接 Z 小磁场线圈）")
    mapping = load_mapping(root)
    theory = load_theory_control(
        Path(params.control_results_root), params.control_version
    )
    calibration = load_keithley_calibration(
        root, params.keithley_calibration_source_run
    )
    applied = build_applied_current(theory, calibration, params.control_scale)
    from ...steps.keithley_6221 import require_keithley_6221_current_range
    current_range = require_keithley_6221_current_range(
        params.keithley_current_range_ma,
        applied.minimum_ma,
        applied.maximum_ma,
    )
    warnings: list[str] = []
    if not np.isclose(
        params.y_rf_frequency_hz,
        theory.theory_rf_frequency_hz,
        rtol=1e-9,
        atol=1e-6,
    ):
        warning = (
            "Y RF/HF2 频率与理论 rf 频率不同；共同触发只固定采集起始相位，"
            "采集期间相对相位按频差演化"
        )
        warnings.append(warning)
        print(f"[WARN] {warning}")
    run_dir = create_run_directory(
        DATA_TYPE,
        params.run_tag,
        params.to_external(),
        schema_version=params.schema_version,
        project_root=root,
    )
    source_files = _source_snapshot(
        run_dir, theory, calibration, applied, current_range
    )
    inactive_normalized = float(-applied.offset_ma / applied.amplitude_peak_ma)
    run_dir.update_config(
        experiment_id=EXPERIMENT_ID,
        data_type=DATA_TYPE,
        execution_mode=EXECUTION_MODE,
        measurement_mode="balanced_sensitivity",
        geometry={
            "control_field": "Keithley 6221 Z small-field coil",
            "main_field": "GS200 Z main-field coil",
            "gs200_physical_connection_confirmed": params.confirm_gs200_connected,
            "control_current_min_ma": applied.minimum_ma,
            "control_current_max_ma": applied.maximum_ma,
        },
        control_source={
            "version": theory.version,
            "repeat_frequency_hz": theory.repeat_frequency_hz,
            "theory_rf_frequency_hz": theory.theory_rf_frequency_hz,
            "rf_periods_per_waveform": theory.rf_periods_per_waveform,
            "y_rf_frequency_hz": params.y_rf_frequency_hz,
            "formula": "I_mA(t) = CONTROL_SCALE * (Omega_ctrl_Hz(t) - f_0mA_Hz) / K_f_Hz_per_mA",
        },
        keithley_calibration={
            "source_run": calibration.run_name,
            "slope_hz_per_ma": calibration.slope_hz_per_ma,
            "intercept_hz": calibration.intercept_hz,
            "r_squared": calibration.r_squared,
        },
        keithley_source_configuration={
            "coil": "Z small-field coil",
            "range_ma": params.keithley_current_range_ma,
            "range_a": params.keithley_current_range_ma / 1000.0,
            "autorange": False,
            "response": params.keithley_output_response,
            "analog_filter": False,
            "compliance_v": params.keithley_compliance_v,
        },
        trigger={
            "source": "Time_sequence_2",
            "frequency_hz": theory.repeat_frequency_hz,
            "slope": "NEGative",
            "keithley_line": 1,
            "ignore": "OFF",
            "inactive_value_normalized": inactive_normalized,
            "inactive_current_ma": 0.0,
            "wiring": (
                "Time_sequence_2 CH2 split to 6221 Line 1 and Y RF DG4000 "
                "Ext Trig; 6221 Line 2 unused"
            ),
        },
        applied_control={
            "minimum_ma": applied.minimum_ma,
            "maximum_ma": applied.maximum_ma,
            "amplitude_peak_ma": applied.amplitude_peak_ma,
            "offset_ma": applied.offset_ma,
            "points": int(applied.current_ma.size),
            "current_range_check": {
                "selected_range_ma": current_range.selected_range_ma,
                "minimum_current_ma": current_range.minimum_current_ma,
                "maximum_current_ma": current_range.maximum_current_ma,
                "required_peak_ma": current_range.required_peak_ma,
                "margin_ma": current_range.margin_ma,
                "utilization_fraction": current_range.utilization_fraction,
                "satisfies": current_range.satisfies,
            },
        },
    )

    session = DeviceSession()
    devices: dict[str, Any] = {}
    channels: dict[str, int] = {}
    completion_status = "failed"
    failure_reason: str | None = None
    connection_complete = False
    try:
        _connect_devices(mapping, session, devices, channels)
        connection_complete = True
        _install_compliance_checker(devices)
        _configure_trigger(
            params,
            devices["trigger"],
            channels["trigger"],
            theory.repeat_frequency_hz,
            output=False,
        )
        _configure_xy_idle(devices, channels)
        laser = devices["laser"]
        laser.setup_dc(params.pump_laser_power_v, channel=channels["pump_laser"])
        laser.set_output(True, channel=channels["pump_laser"])
        laser.setup_dc(params.probe_laser_power_v, channel=channels["probe_laser"])
        laser.set_output(True, channel=channels["probe_laser"])
        pump = devices["pump_rf"]
        pump.setup_sine(
            params.pump_carrier_frequency_hz,
            params.pump_carrier_amplitude_vpp,
            offset=0.0,
            phase=0.0,
            channel=channels["pump_carrier"],
        )
        pump.set_output(True, channel=channels["pump_carrier"])
        from ..mx_keithley_6221_optimal_control_rf_sensitivity.workflow import _set_pump_gate_on
        _set_pump_gate_on(pump, channels["pump_gate"], params.pump_gate_voltage_v)
        set_temperature_switch(devices["temp_switch"], True, channel=channels["temp_switch"])
        configure_temperature_control(
            devices.get("tec"),
            params.temperature_c,
            channel=1,
            tolerance_c=params.temperature_tolerance_c,
            stable_reads=params.temperature_stable_reads,
            poll_interval_s=params.temperature_poll_interval_s,
            timeout_s=params.temperature_timeout_s,
            cancellation=_Cancellation(),
            stability_waiter=wait_for_temperature_stable,
        )
        clocks = _configure_clocks(devices, mapping)
        configured_inactive = _configure_6221(
            devices["keithley"],
            theory,
            applied,
            current_range_ma=params.keithley_current_range_ma,
            compliance_v=params.keithley_compliance_v,
        )
        _configure_trigger(
            params,
            devices["trigger"],
            channels["trigger"],
            theory.repeat_frequency_hz,
            output=True,
        )
        actual_rate, hf2_snapshot = _configure_hf2(
            params, devices, params.y_rf_frequency_hz
        )
        device_id = str(mapping["lockin_r"]["device_id"])
        run_dir.update_config(
            clock_sources=clocks,
            hf2_configuration=hf2_snapshot,
            trigger_inactive_value_normalized=configured_inactive,
            trigger_inactive_current_ma=0.0,
        )

        # ---------- 平衡阶段 ----------
        balance_rounds: list[dict[str, Any]] = []
        coarse_axes = (
            _axis(
                params.balance_coarse_x_start_v,
                params.balance_coarse_x_stop_v,
                params.balance_coarse_x_points,
            ),
            _axis(
                params.balance_coarse_y_start_v,
                params.balance_coarse_y_stop_v,
                params.balance_coarse_y_points,
            ),
            _axis(
                params.balance_coarse_z_start_ma,
                params.balance_coarse_z_stop_ma,
                params.balance_coarse_z_points,
            ),
        )
        coarse_data = _run_balance_scan(
            params, run_dir, devices, channels, actual_rate, device_id,
            coarse_axes[0], coarse_axes[1], coarse_axes[2], "coarse",
        )
        _save_balance_npz(run_dir, "balance_round_0_coarse_scan.npz", coarse_data)
        coarse_fit = _fit_round(
            run_dir, coarse_data, params.balance_coupling_min_r_squared
        )
        balance_rounds.append(
            {
                "label": "coarse",
                "scan_shape_zyx": [
                    len(coarse_axes[2]), len(coarse_axes[0]), len(coarse_axes[1]),
                ],
                "fitted_workpoint": coarse_fit.get("fitted_workpoint"),
                "measured_grid_minimum": {
                    "z_field_ma": float(coarse_data["z_field_ma"][0]),
                },
                "extrapolated": coarse_fit.get("fitted_workpoint", {}).get(
                    "extrapolated"
                ),
                "next_scan_suggestion": coarse_fit.get("next_scan_suggestion"),
            }
        )
        final_data = coarse_data
        final_fit = coarse_fit
        fine_axes = _fine_axes(params, coarse_fit)
        if fine_axes is not None:
            fine_data = _run_balance_scan(
                params, run_dir, devices, channels, actual_rate, device_id,
                fine_axes[0], fine_axes[1], fine_axes[2], "fine",
            )
            _save_balance_npz(run_dir, "balance_round_1_fine_scan.npz", fine_data)
            fine_fit = _fit_round(
                run_dir, fine_data, params.balance_coupling_min_r_squared
            )
            balance_rounds.append(
                {
                    "label": "fine",
                    "scan_shape_zyx": [
                        len(fine_axes[2]), len(fine_axes[0]), len(fine_axes[1]),
                    ],
                    "fitted_workpoint": fine_fit.get("fitted_workpoint"),
                    "extrapolated": fine_fit.get("fitted_workpoint", {}).get(
                        "extrapolated"
                    ),
                    "next_scan_suggestion": fine_fit.get("next_scan_suggestion"),
                }
            )
            final_data = fine_data
            final_fit = fine_fit
        # 最终轮另存为兼容文件名，供离线平衡分析直接复用
        _save_balance_npz(run_dir, "xyz_balance_scan.npz", final_data)
        x0, y0, z0 = _best_workpoint(final_fit, params)
        if final_fit.get("fitted_workpoint", {}).get("extrapolated"):
            warnings.append(
                "平衡拟合工作点位于扫描窗口之外，RF 灵敏度使用外推平衡点，"
                "结果可能偏差；建议扩大平衡扫描范围后重跑。"
            )
            print(f"[WARN] {warnings[-1]}")
        run_dir.update_config(
            balance={
                "rounds": balance_rounds,
                "final_workpoint": {
                    "x_field_v": x0,
                    "y_field_v": y0,
                    "z_field_ma": z0,
                },
                "final_round_label": "fine" if fine_axes is not None else "coarse",
            }
        )

        # ---------- RF 灵敏度阶段（平衡点注入固定参数） ----------
        params.x_dc_field_v = x0
        params.y_rf_offset_v = y0
        params.main_magnetic_field_ma = z0
        xy = devices["xy_field"]
        configure_fixed_dc_field(
            xy, channels["x_field"], "X_magnetic_field", params.x_dc_field_v
        )
        _configure_y_rf_output(params, xy, channels["y_rf"])
        actual_gs200_ma = _configure_gs200(devices["gs200"], mapping, z0)
        run_dir.update_config(actual_gs200_main_field_ma=actual_gs200_ma)
        selected_phase, phase_payload = _acquire_phase_scan(
            params, run_dir, devices, channels, actual_rate, device_id
        )
        run_dir.update_config(
            phase_calibration=phase_payload,
            selected_y_rf_phase_deg=selected_phase,
        )
        _acquire_amplitude_scan(
            params, run_dir, devices, channels, actual_rate, device_id,
            selected_phase,
        )
        actual_noise_rate = _acquire_noise(
            params,
            run_dir,
            devices,
            channels,
            device_id,
            set_y_rf_off_state=lambda: configure_fixed_dc_field(
                xy, channels["y_rf"], "Y_magnetic_field", params.y_rf_offset_v
            ),
            y_rf_dc_v=params.y_rf_offset_v,
            y_rf_output_on=params.y_rf_offset_v != 0.0,
        )
        completion_status = "completed"
        run_dir.update_config(
            completion_status=completion_status,
            failure_reason=None,
            warnings=warnings,
            actual_rates={
                "response_sa_s": actual_rate,
                "noise_sa_s": actual_noise_rate,
            },
            data_files=[
                *source_files,
                "raw/balance_round_0_coarse_scan.npz",
                *(
                    ["raw/balance_round_1_fine_scan.npz"]
                    if fine_axes is not None
                    else []
                ),
                "raw/xyz_balance_scan.npz",
                "raw/phase_scan.npz",
                "raw/amplitude_scan.npz",
                *[f"raw/noise_{i:03d}.npz" for i in range(params.noise_n_avg)],
            ],
        )
        return run_dir.root
    except Exception as exc:
        failure_reason = str(exc)
        if run_dir.config_path.exists():
            run_dir.update_config(
                completion_status="failed", failure_reason=failure_reason
            )
        raise
    finally:
        report = safe_shutdown(devices, channels, params)
        if not connection_complete:
            session.cleanup_connection_failure()
        shutdown_errors = report.errors
        final_failure_reason = failure_reason
        if shutdown_errors:
            suffix = "安全关断错误: " + "；".join(shutdown_errors)
            final_failure_reason = (
                f"{failure_reason}；{suffix}" if failure_reason else suffix
            )
        final_status = "failed" if shutdown_errors else completion_status
        if run_dir.config_path.exists():
            run_dir.update_config(
                completion_status=final_status,
                failure_reason=final_failure_reason,
                safety_shutdown=report.to_dict(),
            )


def main() -> int:
    run(load_runtime_params(MxKeithley6221OptimalControlBalancedSensitivityParams))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
