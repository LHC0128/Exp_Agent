"""Mx Z 最优控制 RF 灵敏度采集工作流。"""

from __future__ import annotations

from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from ...common import find_project_root, load_mapping, validate_safety_limit
from ...control_sources import applied_control_from_corrected
from ...current_feedback import load_corrected_control_waveform
from ...experiment_runtime import check_cancelled, load_runtime_params
from ...steps import (
    DGChannelShutdown,
    DeviceSession,
    DisconnectTarget,
    OPTIMAL_CONTROL_BURST_TRIGGER_SLOPE,
    STANDARD_PRESERVED_OUTPUTS,
    SafetyShutdownReport,
    ShutdownAction,
    TemperatureSwitchRestore,
    configure_fixed_dc_field,
    configure_mx_z_optimal_control_workpoint,
    create_run_directory,
    restore_main_field_state,
    run_safety_shutdown,
    save_corrected_control_source_snapshot,
    snapshot_main_field_state,
    validate_z_trigger_mapping,
    wait_for_temperature_stable,
)
from ..mx_y_rf_sensitivity.acquisition import (
    acquire_r,
    acquire_rxy,
    summarize_r,
    summarize_rxy,
)
from ..mx_y_rf_sensitivity.scan import build_amplitude_axis
from ..mx_y_rf_sensitivity.workflow import (
    RPointQualityError,
    _acquire_noise,
    _acquire_valid_r_point,
    _set_pump_gate_on,
    _temperature_gated_acquire,
)
from ..mx_z_field_calibration.workflow import (
    _configure_reference_clocks,
    _connect_devices,
    _initial_state_snapshot,
)
from .models import MxZOptimalControlRFParams
from .phase import (
    detect_complex_phase_outliers,
    fit_phase_scan,
    fit_quadrature_phase_scan,
    paired_phase_order,
)
from .phase_plot import plot_phase_calibration


EXPERIMENT_ID = "mx-z-optimal-control-rf-sensitivity"
DATA_TYPE = "Mx_Z_Optimal_Control_RF_Sensitivity"
EXECUTION_MODE = "typed_workflow"


class _RuntimeCancellation:
    @staticmethod
    def raise_if_cancelled() -> None:
        check_cancelled()


def _connect_control_devices(
    mapping: dict[str, dict[str, Any]],
    session: DeviceSession,
) -> tuple[dict[str, Any], dict[str, int]]:
    """连接 Mx 控制实验设备，并声明 Z 源同机触发通道。"""
    devices, channels = _connect_devices(mapping, session)
    channels["trigger"] = validate_z_trigger_mapping(mapping)
    return devices, channels


def _configure_y_rf_output(
    params: MxZOptimalControlRFParams,
    device: Any,
    channel: int,
) -> None:
    amplitude_vpp = float(params.phase_cal_rf_amplitude_vpp)
    _validate_y_rf_envelope(params.y_rf_offset_v, amplitude_vpp)
    if amplitude_vpp == 0.0:
        configure_fixed_dc_field(
            device,
            channel,
            "Y_magnetic_field",
            params.y_rf_offset_v,
        )
        return
    device.set_output(False, channel=channel)
    device.set_burst_state(False, channel=channel)
    device.set_mod_state(False, channel=channel)
    device.setup_sine(
        params.y_rf_frequency_hz,
        amplitude_vpp,
        offset=params.y_rf_offset_v,
        phase=0.0,
        channel=channel,
    )
    device.set_burst_state(True, channel=channel)
    device.set_burst_mode("INFinity", channel=channel)
    device.set_burst_trigger_source("EXTernal", channel=channel)
    device.set_burst_trigger_slope(
        OPTIMAL_CONTROL_BURST_TRIGGER_SLOPE,
        channel=channel,
    )
    device.set_burst_phase(0.0, channel=channel)
    device.set_output(False, channel=channel)


def _validate_y_rf_envelope(offset_v: float, amplitude_vpp: float) -> None:
    """校验 Y RF 幅度及叠加 DC 偏置后的完整输出包络。"""
    amplitude = abs(float(amplitude_vpp))
    offset = float(validate_safety_limit("Y_magnetic_field", float(offset_v)))
    validate_safety_limit("rf_coil", amplitude)
    validate_safety_limit("Y_magnetic_field", offset - amplitude / 2.0)
    validate_safety_limit("Y_magnetic_field", offset + amplitude / 2.0)


def _restore_y_rf_burst_sine(
    device: Any,
    channel: int,
    *,
    frequency_hz: float,
    amplitude_vpp: float,
    offset_v: float,
    phase_deg: float,
) -> None:
    """从纯 DC 零点恢复带偏置的外触发 Burst 正弦。"""
    _validate_y_rf_envelope(offset_v, amplitude_vpp)
    device.set_output(False, channel=channel)
    device.set_burst_state(False, channel=channel)
    device.set_mod_state(False, channel=channel)
    device.setup_sine(
        float(frequency_hz),
        abs(float(amplitude_vpp)),
        offset=float(offset_v),
        phase=0.0,
        channel=channel,
    )
    device.set_burst_state(True, channel=channel)
    device.set_burst_mode("INFinity", channel=channel)
    device.set_burst_trigger_source("EXTernal", channel=channel)
    device.set_burst_trigger_slope(
        OPTIMAL_CONTROL_BURST_TRIGGER_SLOPE,
        channel=channel,
    )
    device.set_burst_phase(float(phase_deg) % 360.0, channel=channel)
    device.set_output(False, channel=channel)


def _rearm_y_rf(
    device: Any,
    channel: int,
    *,
    amplitude_vpp: float,
    phase_deg: float,
    frequency_hz: float | None = None,
    offset_v: float = 0.0,
    waveform_state: dict[str, str] | None = None,
) -> dict[str, Any]:
    """设置 Y RF；零幅度切为纯 DC，非零点用 OFF→ON 等待触发。"""
    amplitude = abs(float(amplitude_vpp))
    phase = float(phase_deg) % 360.0
    _validate_y_rf_envelope(offset_v, amplitude)
    device.set_output(False, channel=channel)
    if amplitude == 0.0:
        configure_fixed_dc_field(
            device,
            channel,
            "Y_magnetic_field",
            offset_v,
        )
        if waveform_state is not None:
            waveform_state["mode"] = "dc"
        return {
            "hardware_amplitude_vpp": 0.0,
            "hardware_phase_deg": phase,
            "dc_offset_v": float(offset_v),
            "waveform_mode": "dc",
            "output_on": bool(offset_v != 0.0),
        }
    if waveform_state is not None and waveform_state.get("mode") == "dc":
        if frequency_hz is None:
            raise ValueError("从 Y RF 纯 DC 恢复正弦时必须提供 frequency_hz")
        _restore_y_rf_burst_sine(
            device,
            channel,
            frequency_hz=frequency_hz,
            amplitude_vpp=amplitude,
            offset_v=offset_v,
            phase_deg=phase,
        )
    else:
        device.set_burst_phase(phase, channel=channel)
        device.set_amplitude(amplitude, channel=channel)
    device.set_output(True, channel=channel)
    if waveform_state is not None:
        waveform_state["mode"] = "burst_sine"
    return {
        "hardware_amplitude_vpp": amplitude,
        "hardware_phase_deg": phase,
        "dc_offset_v": float(offset_v),
        "waveform_mode": "burst_sine",
        "output_on": True,
    }


def _rearm_control(
    device: Any,
    channel: int,
    *,
    phase_deg: float,
) -> dict[str, Any]:
    """设置 Z 控制触发相位，并用 OFF→ON 重新等待外部触发。"""
    phase = float(phase_deg) % 360.0
    device.set_output(False, channel=channel)
    device.set_burst_phase(phase, channel=channel)
    device.set_output(True, channel=channel)
    return {
        "control_hardware_phase_deg": phase,
        "control_output_on": True,
    }


def _configure_common_outputs(
    params: MxZOptimalControlRFParams,
    devices: dict[str, Any],
    channels: dict[str, int],
    mapping: dict[str, dict[str, Any]],
    control_source: Any,
    applied: Any,
) -> tuple[float, float | None, dict[str, dict[str, str]], dict[str, Any]]:
    """配置可选 GS200 主场的 Mx 工作点并启动连续 Z 控制。

    ``control_source`` 只需提供 ``repeat_frequency_hz``，本实验传入
    闭环冻结波形，仍支持理论来源的实验传入 ``TheoryControlSource``。
    """
    def configure_xy_outputs() -> None:
        xy_field = devices["xy_field"]
        configure_fixed_dc_field(
            xy_field,
            channels["x_field"],
            "X_magnetic_field",
            params.x_dc_field_v,
        )
        _configure_y_rf_output(params, xy_field, channels["y_rf"])

    return configure_mx_z_optimal_control_workpoint(
        params,
        devices,
        channels,
        mapping,
        control_source,
        applied,
        demod_frequency_hz=params.y_rf_frequency_hz,
        cancellation=_RuntimeCancellation(),
        xy_output_configurator=configure_xy_outputs,
        reference_clock_configurator=_configure_reference_clocks,
        pump_gate_setter=_set_pump_gate_on,
        temperature_stability_waiter=wait_for_temperature_stable,
    )


def _save_rxy_point(
    path: Path,
    payload: dict[str, np.ndarray],
    metadata: dict[str, Any],
    actual_rate: float,
) -> dict[str, float]:
    """保存单个正交校相点的同步 R/X/Y 及质量统计。"""
    summary = summarize_rxy(payload)
    np.savez(
        path,
        time_s=payload["time_s"],
        r_v=payload["r"],
        x_v=payload["x"],
        y_v=payload["y"],
        actual_rate_sa_s=np.float64(actual_rate),
        **metadata,
        **{
            key: np.float64(value) if isinstance(value, float) else value
            for key, value in summary.items()
        },
    )
    return summary


def _acquire_valid_rxy_point(
    params: MxZOptimalControlRFParams,
    run_dir: Any,
    devices: dict[str, Any],
    channels: dict[str, int],
    *,
    file_stem: str,
    metadata: dict[str, Any],
    set_y_rf: Any,
    settle_time_s: float,
    duration_s: float,
    actual_rate: float,
    device_id: str,
) -> tuple[dict[str, float], int, str]:
    """采集一个 R/X/Y 点；复噪声超限时完整保存并重采。"""
    hf2 = devices["hf2"]
    for attempt in range(params.r_point_max_attempts):
        checker = devices.get("_compliance_checker")
        if checker is not None:
            checker(f"{file_stem} 采集前")
        payload = _temperature_gated_acquire(
            params,
            devices,
            channels,
            set_y_rf=set_y_rf,
            settle_time_s=settle_time_s,
            acquire=lambda: acquire_rxy(
                hf2,
                device_id=device_id,
                demod_idx=params.demod_idx,
                actual_rate_sa_s=actual_rate,
                duration_s=duration_s,
            ),
        )
        summary = summarize_rxy(payload)
        if checker is not None:
            checker(f"{file_stem} 采集后")
        accepted = (
            summary["complex_std_v"]
            <= params.r_bad_point_std_threshold_v
        )
        filename = f"{file_stem}_attempt_{attempt:02d}.npz"
        _save_rxy_point(
            run_dir.raw / filename,
            payload,
            {
                **metadata,
                "attempt_index": np.int64(attempt),
                "quality_accepted": np.uint8(accepted),
                "complex_std_threshold_v": np.float64(
                    params.r_bad_point_std_threshold_v
                ),
            },
            actual_rate,
        )
        print(
            f"  X/Y 质量 {attempt + 1}/{params.r_point_max_attempts}: "
            f"complex_std={summary['complex_std_v']:.6g} V, "
            f"{'接受' if accepted else '拒绝并重采'}"
        )
        if accepted:
            return summary, attempt, filename
    raise RPointQualityError(
        f"{file_stem} 连续 {params.r_point_max_attempts} 次 "
        f"复噪声超过 {params.r_bad_point_std_threshold_v:.6g} V"
    )


def _rxy_summary_arrays(
    summaries: list[dict[str, float]],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """提取跨相位检测所需的 X/Y 均值和复噪声。"""
    return (
        np.asarray([item["x_mean_v"] for item in summaries], dtype=float),
        np.asarray([item["y_mean_v"] for item in summaries], dtype=float),
        np.asarray(
            [item["complex_std_v"] for item in summaries],
            dtype=float,
        ),
    )


def _reacquire_phase_outliers_once(
    params: MxZOptimalControlRFParams,
    run_dir: Any,
    devices: dict[str, Any],
    channels: dict[str, int],
    axis: np.ndarray,
    summaries: list[dict[str, float]],
    attempts: list[int],
    files: list[str],
    *,
    actual_rate: float,
    device_id: str,
) -> tuple[dict[str, Any], list[int], list[str], np.ndarray]:
    """识别跨相位异常点，并对每个具体异常相位定点重采一次。"""
    x_mean, y_mean, complex_std = _rxy_summary_arrays(summaries)
    initial = detect_complex_phase_outliers(
        axis,
        x_mean,
        y_mean,
        complex_std,
        sigma_threshold=params.phase_outlier_sigma_threshold,
    )
    initial_attempts = list(attempts)
    initial_files = list(files)
    reacquired = np.zeros(axis.size, dtype=bool)
    replacement_files: list[str] = []
    too_many = (
        len(initial.outlier_indices)
        > params.phase_outlier_max_reacquire_points
    )
    indices_to_reacquire = (
        ()
        if too_many
        else initial.outlier_indices
    )
    rf = devices["xy_field"]
    for index in indices_to_reacquire:
        check_cancelled()
        phase_deg = float(axis[index])
        original_file = files[index]
        print(
            "跨相位异常点重采 "
            f"{phase_deg:.3f} deg: residual="
            f"{initial.residual_v[index]:.6g} V > "
            f"{initial.threshold_v:.6g} V"
        )

        def set_phase(
            phase_deg: float = phase_deg,
        ) -> None:
            _rearm_y_rf(
                rf,
                channels["y_rf"],
                amplitude_vpp=params.phase_cal_rf_amplitude_vpp,
                phase_deg=phase_deg,
                offset_v=params.y_rf_offset_v,
            )

        summary, attempt, filename = _acquire_valid_rxy_point(
            params,
            run_dir,
            devices,
            channels,
            file_stem=f"phase_{index:04d}_cross_retry",
            metadata={
                "scanned_phase_deg": np.float64(phase_deg),
                "y_rf_burst_phase_deg": np.float64(phase_deg),
                "y_rf_amplitude_vpp": np.float64(
                    params.phase_cal_rf_amplitude_vpp
                ),
                "y_rf_output_on": np.uint8(True),
                "cross_phase_reacquisition": np.uint8(True),
                "replaced_file": np.asarray(original_file),
                "initial_cross_phase_residual_v": np.float64(
                    initial.residual_v[index]
                ),
                "cross_phase_threshold_v": np.float64(
                    initial.threshold_v
                ),
            },
            set_y_rf=set_phase,
            settle_time_s=params.response_settle_time_s,
            duration_s=params.response_duration_s,
            actual_rate=actual_rate,
            device_id=device_id,
        )
        summaries[index] = summary
        attempts[index] = attempt
        files[index] = filename
        reacquired[index] = True
        replacement_files.append(filename)

    x_mean, y_mean, complex_std = _rxy_summary_arrays(summaries)
    final = detect_complex_phase_outliers(
        axis,
        x_mean,
        y_mean,
        complex_std,
        sigma_threshold=params.phase_outlier_sigma_threshold,
    )
    report = {
        "enabled": True,
        "passes": 1,
        "max_reacquire_points": (
            params.phase_outlier_max_reacquire_points
        ),
        "too_many_initial_outliers": too_many,
        "initial_detection": initial.to_dict(),
        "reacquired_indices": [
            int(index) for index in np.flatnonzero(reacquired)
        ],
        "reacquired_phase_deg": [
            float(axis[index]) for index in np.flatnonzero(reacquired)
        ],
        "original_files": [
            initial_files[index] for index in np.flatnonzero(reacquired)
        ],
        "replacement_files": replacement_files,
        "final_detection": final.to_dict(),
    }
    return report, initial_attempts, initial_files, reacquired


def _acquire_phase_scan(
    params: MxZOptimalControlRFParams,
    run_dir: Any,
    devices: dict[str, Any],
    channels: dict[str, int],
    actual_rate: float,
    device_id: str,
) -> tuple[float, dict[str, Any]]:
    """按校相幅度选择 Y RF 校相或无 RF 交流的 Z 控制相位扫描。"""
    residual_mode = params.phase_cal_rf_amplitude_vpp == 0.0
    configured_axis = params.phase_axis_deg()
    axis = (
        configured_axis
        if residual_mode
        else paired_phase_order(configured_axis)
    )
    mode = (
        "residual_control_phase_scan"
        if residual_mode
        else "y_rf_quadrature_phase_calibration"
    )
    phase_target = "control_burst" if residual_mode else "y_rf_burst"
    summaries: list[dict[str, float]] = []
    attempts: list[int] = []
    files: list[str] = []
    dummy_file: str | None = None
    rf = devices["xy_field"]
    control = devices.get("z_field")
    if residual_mode:
        if control is None:
            raise KeyError("剩磁响应模式缺少 Z 控制设备")
        configure_fixed_dc_field(
            rf,
            channels["y_rf"],
            "Y_magnetic_field",
            params.y_rf_offset_v,
        )
    try:
        if not residual_mode:
            dummy_phase = float(axis[0])

            def set_dummy_phase() -> None:
                _rearm_y_rf(
                    rf,
                    channels["y_rf"],
                    amplitude_vpp=params.phase_cal_rf_amplitude_vpp,
                    phase_deg=dummy_phase,
                    offset_v=params.y_rf_offset_v,
                )

            print(
                "Y RF 正交校相 dummy 点: "
                f"phase={dummy_phase:.3f} deg（采集后丢弃）"
            )
            _, _, dummy_file = _acquire_valid_rxy_point(
                params,
                run_dir,
                devices,
                channels,
                file_stem="phase_dummy",
                metadata={
                    "scanned_phase_deg": np.float64(dummy_phase),
                    "y_rf_burst_phase_deg": np.float64(dummy_phase),
                    "y_rf_amplitude_vpp": np.float64(
                        params.phase_cal_rf_amplitude_vpp
                    ),
                    "y_rf_dc_offset_v": np.float64(params.y_rf_offset_v),
                    "y_rf_output_on": np.uint8(True),
                    "discarded_dummy": np.uint8(True),
                },
                set_y_rf=set_dummy_phase,
                settle_time_s=params.response_settle_time_s,
                duration_s=params.response_duration_s,
                actual_rate=actual_rate,
                device_id=device_id,
            )

        for index, phase_deg in enumerate(axis):
            check_cancelled()
            target_label = (
                "Z 控制剩磁响应"
                if residual_mode
                else "Y RF 正交校相"
            )
            print(
                f"{target_label} {index + 1}/{axis.size}: "
                f"phase={phase_deg:.3f} deg"
            )
            if residual_mode:
                assert control is not None
                metadata = {
                    "scanned_phase_deg": np.float64(phase_deg),
                    "control_burst_phase_deg": np.float64(phase_deg),
                    "y_rf_amplitude_vpp": np.float64(0.0),
                    "y_rf_dc_offset_v": np.float64(params.y_rf_offset_v),
                    "y_rf_output_on": np.uint8(params.y_rf_offset_v != 0.0),
                }

                def set_phase(
                    phase_deg: float = float(phase_deg),
                ) -> None:
                    configure_fixed_dc_field(
                        rf,
                        channels["y_rf"],
                        "Y_magnetic_field",
                        params.y_rf_offset_v,
                    )
                    _rearm_control(
                        control,
                        channels["z_field"],
                        phase_deg=phase_deg,
                    )
            else:
                metadata = {
                    "scanned_phase_deg": np.float64(phase_deg),
                    "y_rf_burst_phase_deg": np.float64(phase_deg),
                    "y_rf_amplitude_vpp": np.float64(
                        params.phase_cal_rf_amplitude_vpp
                    ),
                    "y_rf_dc_offset_v": np.float64(params.y_rf_offset_v),
                    "y_rf_output_on": np.uint8(True),
                }

                def set_phase(
                    phase_deg: float = float(phase_deg),
                ) -> None:
                    _rearm_y_rf(
                        rf,
                        channels["y_rf"],
                        amplitude_vpp=params.phase_cal_rf_amplitude_vpp,
                        phase_deg=phase_deg,
                        offset_v=params.y_rf_offset_v,
                    )

            acquire_point = (
                _acquire_valid_r_point
                if residual_mode
                else _acquire_valid_rxy_point
            )
            summary, attempt, filename = acquire_point(
                params,
                run_dir,
                devices,
                channels,
                file_stem=f"phase_{index:04d}",
                metadata=metadata,
                set_y_rf=set_phase,
                settle_time_s=params.response_settle_time_s,
                duration_s=params.response_duration_s,
                actual_rate=actual_rate,
                device_id=device_id,
            )
            summaries.append(summary)
            attempts.append(attempt)
            files.append(filename)
    finally:
        if residual_mode:
            assert control is not None
            configure_fixed_dc_field(
                rf,
                channels["y_rf"],
                "Y_magnetic_field",
                params.y_rf_offset_v,
            )
            _rearm_control(
                control,
                channels["z_field"],
                phase_deg=params.control_burst_phase_deg,
            )

    cross_phase_report: dict[str, Any] | None = None
    initial_attempts = list(attempts)
    initial_files = list(files)
    cross_phase_reacquired = np.zeros(axis.size, dtype=bool)
    if not residual_mode:
        (
            cross_phase_report,
            initial_attempts,
            initial_files,
            cross_phase_reacquired,
        ) = _reacquire_phase_outliers_once(
            params,
            run_dir,
            devices,
            channels,
            axis,
            summaries,
            attempts,
            files,
            actual_rate=actual_rate,
            device_id=device_id,
        )

    r_mean = np.asarray(
        [item["r_scalar_mean_v"] for item in summaries],
        dtype=float,
    )
    r_std = np.asarray([item["r_std_v"] for item in summaries], dtype=float)
    phase_arrays: dict[str, Any] = {
        "scanned_phase_deg": axis,
        (
            "control_burst_phase_deg"
            if residual_mode
            else "y_rf_burst_phase_deg"
        ): axis,
        "r_mean_v": r_mean,
        "r_std_v": r_std,
        "accepted_attempt_index": np.asarray(attempts, dtype=int),
        "accepted_file": np.asarray(files),
        "initial_accepted_attempt_index": np.asarray(
            initial_attempts,
            dtype=int,
        ),
        "initial_accepted_file": np.asarray(initial_files),
        "cross_phase_reacquired": cross_phase_reacquired,
        "actual_rate_sa_s": np.float64(actual_rate),
        "x_dc_field_v": np.float64(params.x_dc_field_v),
        "y_rf_dc_offset_v": np.float64(params.y_rf_offset_v),
    }
    if residual_mode:
        primary, diagnostic = fit_phase_scan(
            axis,
            r_mean,
            r_squared_min=params.phase_fit_r_squared_min,
            amplitude_sigma_min=params.phase_fit_amplitude_sigma_min,
        )
        payload = {
            "mode": mode,
            "phase_target": phase_target,
            "success": primary.success,
            "scan_completed": True,
            "fit_accepted": primary.success,
            "selected_phase_deg": primary.selected_phase_deg,
            "primary_fit": primary.to_dict(),
            "diagnostic_fit": diagnostic.to_dict(),
            "phase_scan": {
                "start_deg": params.phase_scan_start_deg,
                "stop_deg": params.phase_scan_stop_deg,
                "step_deg": params.phase_scan_step_deg,
                "points": int(axis.size),
                "rf_amplitude_vpp": 0.0,
                "rf_dc_offset_v": params.y_rf_offset_v,
                "rf_output_on": params.y_rf_offset_v != 0.0,
                "control_restored_phase_deg": (
                    params.control_burst_phase_deg
                ),
                "point_value": "mean(R)",
            },
        }
        payload["candidate_control_phase_deg"] = primary.selected_phase_deg
        payload["selected_control_phase_deg"] = (
            primary.selected_phase_deg if primary.success else None
        )
    else:
        x_mean, y_mean, complex_std = _rxy_summary_arrays(summaries)
        x_std = np.asarray(
            [item["x_std_v"] for item in summaries],
            dtype=float,
        )
        y_std = np.asarray(
            [item["y_std_v"] for item in summaries],
            dtype=float,
        )
        quadrature_fit, quadrature_arrays = fit_quadrature_phase_scan(
            axis,
            x_mean,
            y_mean,
            r_squared_min=params.phase_fit_r_squared_min,
            amplitude_sigma_min=params.phase_fit_amplitude_sigma_min,
        )
        assert cross_phase_report is not None
        final_outlier_phases = cross_phase_report["final_detection"][
            "outlier_phase_deg"
        ]
        if final_outlier_phases:
            reason = (
                "单次重采后仍存在跨相位异常点: "
                + ", ".join(
                    f"{float(value):.6g}°"
                    for value in final_outlier_phases
                )
            )
            quadrature_fit = replace(
                quadrature_fit,
                success=False,
                rejection_reasons=(
                    *quadrature_fit.rejection_reasons,
                    reason,
                ),
            )
        phase_arrays.update(
            {
                "x_mean_v": x_mean,
                "x_std_v": x_std,
                "y_mean_v": y_mean,
                "y_std_v": y_std,
                "complex_std_v": complex_std,
                "cross_phase_initial_residual_v": np.asarray(
                    cross_phase_report["initial_detection"][
                        "residual_v"
                    ],
                    dtype=float,
                ),
                "cross_phase_final_residual_v": np.asarray(
                    cross_phase_report["final_detection"]["residual_v"],
                    dtype=float,
                ),
                **quadrature_arrays,
            }
        )
        payload = {
            "mode": mode,
            "phase_target": phase_target,
            "success": quadrature_fit.success,
            "scan_completed": True,
            "fit_accepted": quadrature_fit.success,
            "selected_phase_deg": quadrature_fit.selected_phase_deg,
            "selected_y_rf_phase_deg": (
                quadrature_fit.selected_phase_deg
            ),
            "quadrature_fit": quadrature_fit.to_dict(),
            "cross_phase_reacquisition": cross_phase_report,
            "phase_scan": {
                "start_deg": params.phase_scan_start_deg,
                "stop_deg": params.phase_scan_stop_deg,
                "step_deg": params.phase_scan_step_deg,
                "points": int(axis.size),
                "pair_count": quadrature_fit.pair_count,
                "rf_amplitude_vpp": params.phase_cal_rf_amplitude_vpp,
                "rf_dc_offset_v": params.y_rf_offset_v,
                "rf_output_on": True,
                "control_restored_phase_deg": None,
                "point_value": "mean(R/X/Y)",
                "acquisition_order": "phi_then_phi_plus_180",
                "discarded_dummy_file": dummy_file,
            },
        }
    np.savez(run_dir.raw / "phase_scan.npz", **phase_arrays)
    (run_dir.results / "phase_calibration.yaml").write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    plot_phase_calibration(run_dir.raw, run_dir.results, payload)
    if not residual_mode and not quadrature_fit.success:
        raise RuntimeError(
            "Y RF 正交相位拟合不合格: "
            + "；".join(quadrature_fit.rejection_reasons)
        )
    if residual_mode and not primary.success:
        return float("nan"), payload
    selected_phase = (
        primary.selected_phase_deg
        if residual_mode
        else quadrature_fit.selected_phase_deg
    )
    return float(selected_phase), payload


def _acquire_amplitude_scan(
    params: MxZOptimalControlRFParams,
    run_dir: Any,
    devices: dict[str, Any],
    channels: dict[str, int],
    actual_rate: float,
    device_id: str,
    selected_phase_deg: float,
) -> None:
    """在选定触发相位上扫描带符号 Y RF 幅度。"""
    axis = build_amplitude_axis(params)
    summaries: list[dict[str, float]] = []
    hardware: list[dict[str, Any]] = []
    attempts: list[int] = []
    files: list[str] = []
    rf = devices["xy_field"]
    waveform_state = {"mode": "burst_sine"}
    for index, signed_amplitude in enumerate(axis):
        check_cancelled()
        phase_deg = (
            selected_phase_deg
            if signed_amplitude >= 0
            else (selected_phase_deg + 180.0) % 360.0
        )
        print(
            f"Y RF 幅度 {index + 1}/{axis.size}: "
            f"{signed_amplitude:+.6f} Vpp, phase={phase_deg:.3f} deg"
        )
        point_hardware = {
            "hardware_amplitude_vpp": abs(float(signed_amplitude)),
            "hardware_phase_deg": float(phase_deg),
            "dc_offset_v": float(params.y_rf_offset_v),
            "waveform_mode": (
                "dc" if signed_amplitude == 0.0 else "burst_sine"
            ),
            "output_on": bool(
                signed_amplitude != 0.0 or params.y_rf_offset_v != 0.0
            ),
        }
        summary, attempt, filename = _acquire_valid_r_point(
            params,
            run_dir,
            devices,
            channels,
            file_stem=f"amplitude_{index:04d}",
            metadata={
                "signed_amplitude_vpp": np.float64(signed_amplitude),
                **point_hardware,
            },
            set_y_rf=lambda signed_amplitude=signed_amplitude, phase_deg=phase_deg: _rearm_y_rf(
                rf,
                channels["y_rf"],
                amplitude_vpp=float(signed_amplitude),
                phase_deg=float(phase_deg),
                frequency_hz=params.y_rf_frequency_hz,
                offset_v=params.y_rf_offset_v,
                waveform_state=waveform_state,
            ),
            settle_time_s=params.response_settle_time_s,
            duration_s=params.response_duration_s,
            actual_rate=actual_rate,
            device_id=device_id,
        )
        summaries.append(summary)
        hardware.append(point_hardware)
        attempts.append(attempt)
        files.append(filename)
    np.savez(
        run_dir.raw / "amplitude_scan.npz",
        signed_amplitude_vpp=axis,
        hardware_amplitude_vpp=[
            item["hardware_amplitude_vpp"] for item in hardware
        ],
        hardware_phase_deg=[
            item["hardware_phase_deg"] for item in hardware
        ],
        output_on=[item["output_on"] for item in hardware],
        waveform_mode=[item["waveform_mode"] for item in hardware],
        y_rf_dc_offset_v=np.float64(params.y_rf_offset_v),
        x_dc_field_v=np.float64(params.x_dc_field_v),
        r_mean_v=[item["r_mean_v"] for item in summaries],
        r_scalar_mean_v=[
            item["r_scalar_mean_v"] for item in summaries
        ],
        r_std_v=[item["r_std_v"] for item in summaries],
        accepted_attempt_index=attempts,
        accepted_file=files,
        selected_y_rf_phase_deg=np.float64(selected_phase_deg),
        actual_rate_sa_s=np.float64(actual_rate),
    )


def _acquire_control_noise(
    params: MxZOptimalControlRFParams,
    run_dir: Any,
    devices: dict[str, Any],
    channels: dict[str, int],
    device_id: str,
    selected_phase_deg: float,
) -> float:
    """在幅度扫描后，按配置采集带 RF 或纯 DC 补偿下的噪声。"""
    amplitude_vpp = (
        float(params.noise_rf_amplitude_vpp) if params.noise_rf_enabled else 0.0
    )
    if not np.isfinite(amplitude_vpp) or amplitude_vpp < 0.0:
        raise ValueError("NOISE_RF_AMPLITUDE_VPP 必须是非负有限数值")
    _validate_y_rf_envelope(params.y_rf_offset_v, amplitude_vpp)
    rf_enabled = amplitude_vpp > 0.0
    phase_deg = float(selected_phase_deg) % 360.0
    # 幅度扫描的末点决定当前是否需要从纯 DC 恢复 Burst 正弦。
    waveform_state = {
        "mode": "dc" if params.y_rf_amp_stop_vpp == 0.0 else "burst_sine"
    }
    metadata = {
        "y_rf_enabled": rf_enabled,
        "y_rf_amplitude_vpp": amplitude_vpp,
        "y_rf_frequency_hz": float(params.y_rf_frequency_hz),
        "y_rf_burst_phase_deg": phase_deg,
        "y_rf_dc_v": float(params.y_rf_offset_v),
        "y_rf_output_on": bool(rf_enabled or params.y_rf_offset_v != 0.0),
    }
    run_dir.update_config(noise_rf=metadata)

    def set_noise_rf() -> None:
        _rearm_y_rf(
            devices["xy_field"],
            channels["y_rf"],
            amplitude_vpp=amplitude_vpp,
            phase_deg=phase_deg,
            frequency_hz=params.y_rf_frequency_hz,
            offset_v=params.y_rf_offset_v,
            waveform_state=waveform_state,
        )

    return _acquire_noise(
        params,
        run_dir,
        devices,
        channels,
        device_id,
        set_y_rf_off_state=set_noise_rf,
        y_rf_dc_v=params.y_rf_offset_v,
        y_rf_output_on=metadata["y_rf_output_on"],
        noise_metadata=metadata,
        noise_label=(
            f"Y RF 开启噪声（{amplitude_vpp:g} Vpp）"
            if rf_enabled else "零 Y RF 噪声"
        ),
        settle_time_s=params.response_settle_time_s if rf_enabled else 0.0,
    )


def safe_shutdown(
    devices: dict[str, Any],
    channels: dict[str, int],
    params: MxZOptimalControlRFParams,
) -> SafetyShutdownReport:
    """保持 Z 控制输出，关闭共同触发和横向场。"""
    dg_channels: list[DGChannelShutdown] = []
    if devices.get("z_field") is not None and "trigger" in channels:
        dg_channels.append(
            DGChannelShutdown(
                devices["z_field"],
                channels["trigger"],
                "Time_sequence_2",
                "共同触发",
            )
        )
    if devices.get("xy_field") is not None:
        for channel_name, safety_key, label in (
            ("x_field", "X_magnetic_field", "X 磁场"),
            ("y_rf", "rf_coil", "Y RF 场"),
        ):
            if channel_name in channels:
                dg_channels.append(
                    DGChannelShutdown(
                        devices["xy_field"],
                        channels[channel_name],
                        safety_key,
                        label,
                    )
                )

    extra_actions: list[ShutdownAction] = []
    pump_rf = devices.get("pump_rf")
    if pump_rf is not None and "pump_gate" in channels:
        extra_actions.append(
            ShutdownAction(
                "保持 Pump RF 开关 5V 常开失败",
                lambda: _set_pump_gate_on(
                    pump_rf,
                    channels["pump_gate"],
                    params.pump_gate_voltage_v,
                ),
            )
        )
    if pump_rf is not None and "pump_carrier" in channels:
        def keep_pump_carrier_on() -> None:
            validate_safety_limit(
                "Pump_modulation",
                params.pump_carrier_amplitude_vpp,
            )
            pump_rf.setup_sine(
                params.pump_carrier_frequency_hz,
                params.pump_carrier_amplitude_vpp,
                offset=0.0,
                phase=0.0,
                channel=channels["pump_carrier"],
            )
            pump_rf.set_output(True, channel=channels["pump_carrier"])

        extra_actions.append(
            ShutdownAction(
                "保持 Pump 100MHz 载波开启失败",
                keep_pump_carrier_on,
            )
        )

    temperature_restore = None
    if devices.get("temp_switch") is not None and "temp_switch" in channels:
        temperature_restore = TemperatureSwitchRestore(
            devices["temp_switch"],
            channels["temp_switch"],
        )
    return run_safety_shutdown(
        dg_channels=dg_channels,
        temperature_switch=temperature_restore,
        extra_actions=extra_actions,
        disconnect_targets=(DisconnectTarget("TEC", devices.get("tec")),),
        preserved_outputs=(
            *STANDARD_PRESERVED_OUTPUTS,
            "Time_sequence",
            "Z_magnetic_field",
        ),
    )


def run(params: MxZOptimalControlRFParams) -> Path:
    """执行 Z 最优控制开启状态下的 RF 灵敏度测量。"""
    root = find_project_root()
    mapping = load_mapping(root)
    residual_mode = params.phase_cal_rf_amplitude_vpp == 0.0
    measurement_mode = (
        "residual_control_phase_scan"
        if residual_mode
        else "rf_sensitivity"
    )
    control = load_corrected_control_waveform(
        root,
        params.corrected_control_source_run,
    )
    applied = applied_control_from_corrected(control)
    warnings: list[str] = []
    if not residual_mode and not np.isclose(
        params.y_rf_frequency_hz,
        control.repeat_frequency_hz,
        rtol=1e-9,
        atol=1e-9,
    ):
        warnings.append(
            "Y RF/HF2 频率与冻结控制波形重复频率不同；"
            "共同触发只固定采集起始相位，采集期间相对相位按频差演化"
        )
    for warning in warnings:
        print(f"[WARN] {warning}")

    run_dir = create_run_directory(
        DATA_TYPE,
        params.run_tag,
        params.to_external(),
        schema_version=params.schema_version,
        project_root=root,
    )
    source_files = save_corrected_control_source_snapshot(
        run_dir.raw,
        control,
        applied,
    )
    run_dir.update_config(
        experiment_id=EXPERIMENT_ID,
        data_type=DATA_TYPE,
        execution_mode=EXECUTION_MODE,
        measurement_mode=measurement_mode,
        geometry={
            "gs200_main_field": (
                "zero_and_output_off_during_measurement"
                if params.main_magnetic_field_ma == 0.0
                else "configured_current_and_output_on_during_measurement"
            ),
            "gs200_main_field_current_ma": params.main_magnetic_field_ma,
            "control_field": "Z arbitrary waveform",
            "rf_field": (
                "Y DC residual-field compensation only"
                if residual_mode
                else "Y triggered sine with DC residual-field compensation"
            ),
            "pump": "Z",
            "probe": "X",
            "x_field_output": (
                "OFF" if params.x_dc_field_v == 0.0 else "fixed DC"
            ),
            "x_dc_field_v": params.x_dc_field_v,
            "y_rf_dc_offset_v": params.y_rf_offset_v,
        },
        residual_field_compensation={
            "x_source": "X_magnetic_field DC",
            "x_voltage_v": params.x_dc_field_v,
            "y_source": "rf_coil waveform DC offset",
            "y_voltage_v": params.y_rf_offset_v,
            "y_rf_zero_behavior": "pure DC at Y compensation voltage",
        },
        control_source={
            "mode": "corrected_run",
            "corrected_run": control.run_name,
            "waveform_sha256": control.waveform_sha256,
            "repeat_frequency_hz": control.repeat_frequency_hz,
            "burst_phase_deg": params.control_burst_phase_deg,
        },
        current_feedback_calibrations={
            "current_coupling_run": control.coupling_calibration_run,
            "current_coupling_sha256": control.coupling_calibration_sha256,
            "frequency_response_run": control.frequency_response_run,
            "frequency_response_sha256": control.frequency_response_sha256,
        },
        applied_control={
            "minimum_v": applied.minimum_v,
            "maximum_v": applied.maximum_v,
            "amplitude_vpp": applied.amplitude_vpp,
            "offset_v": applied.offset_v,
            "output_minimum_v": applied.output_minimum_v,
            "output_maximum_v": applied.output_maximum_v,
            "max_abs_normalized": applied.max_abs_normalized,
            "points": int(control.time_s.size),
        },
        trigger={
            "source": "Time_sequence_2",
            "frequency_hz": params.trigger_frequency_hz,
            "amplitude_vpp": params.trigger_amplitude_vpp,
            "offset_v": params.trigger_offset_v,
            "duty_percent": params.trigger_duty_percent,
            "slope": OPTIMAL_CONTROL_BURST_TRIGGER_SLOPE,
            "wiring": (
                "Time_sequence_2 CH2 to Z-control DG4000 Ext Trig; "
                "Y-RF AC burst disabled while Y DC compensation remains applied"
                if residual_mode
                else "Time_sequence_2 CH2 split to Z-control and Y-RF DG4000 Ext Trig"
            ),
            "physical_wiring_verified_by_software": False,
        },
        warnings=warnings,
        acquisition_signals=(
            ["Demod0 R"]
            if residual_mode
            else [
                "Demod0 R/X/Y during phase calibration",
                "Demod0 R during amplitude/noise acquisition",
            ]
        ),
        linewidth_mode="amplitude_equivalent",
    )

    session = DeviceSession()
    devices: dict[str, Any] = {}
    channels: dict[str, int] = {}
    main_field_state = None
    completion_status = "failed"
    failure_reason: str | None = None
    try:
        check_cancelled()
        try:
            devices, channels = _connect_control_devices(mapping, session)
        except Exception:
            session.cleanup_connection_failure()
            raise
        main_field_state = snapshot_main_field_state(devices["gs200"])
        snapshot = _initial_state_snapshot(devices, channels)
        snapshot["main_field_before_configuration"] = asdict(
            main_field_state
        )
        run_dir.update_config(device_snapshot=snapshot)

        (
            actual_response_rate,
            actual_temperature,
            clock_sources,
            hf2_snapshot,
        ) = _configure_common_outputs(
            params,
            devices,
            channels,
            mapping,
            control,
            applied,
        )
        run_dir.update_config(
            clock_sources=clock_sources,
            hf2_configuration=hf2_snapshot,
            initial_temperature_c=actual_temperature,
        )
        device_id = str(mapping["lockin_r"]["device_id"])
        selected_phase, phase_payload = _acquire_phase_scan(
            params,
            run_dir,
            devices,
            channels,
            actual_response_rate,
            device_id,
        )
        if residual_mode:
            run_dir.update_config(
                residual_control_phase_scan=phase_payload,
                selected_control_phase_deg=(
                    selected_phase if np.isfinite(selected_phase) else None
                ),
                restored_control_phase_deg=params.control_burst_phase_deg,
                y_rf_output_during_scan=(
                    "DC_BIAS_ON"
                    if params.y_rf_offset_v != 0.0
                    else "DC_ZERO_OUTPUT_OFF"
                ),
            )
            completion_status = "completed"
            run_dir.update_config(
                completion_status=completion_status,
                failure_reason=None,
                actual_rates={"response_sa_s": actual_response_rate},
                data_files=[
                    *source_files,
                    "raw/phase_scan.npz",
                ],
            )
            print(
                "Mx Z 最优控制剩磁响应相位扫描完成: "
                f"{run_dir.root}"
            )
            return run_dir.root

        run_dir.update_config(
            phase_calibration=phase_payload,
            selected_y_rf_phase_deg=selected_phase,
        )
        _acquire_amplitude_scan(
            params,
            run_dir,
            devices,
            channels,
            actual_response_rate,
            device_id,
            selected_phase,
        )
        actual_noise_rate = _acquire_control_noise(
            params,
            run_dir,
            devices,
            channels,
            device_id,
            selected_phase,
        )
        completion_status = "completed"
        run_dir.update_config(
            completion_status=completion_status,
            failure_reason=None,
            actual_rates={
                "response_sa_s": actual_response_rate,
                "noise_sa_s": actual_noise_rate,
            },
            data_files=[
                *source_files,
                "raw/phase_scan.npz",
                "raw/amplitude_scan.npz",
                *[
                    f"raw/noise_{index:03d}.npz"
                    for index in range(params.noise_n_avg)
                ],
            ],
        )
        print(f"Mx Z 最优控制 RF 灵敏度采集完成: {run_dir.root}")
        return run_dir.root
    except Exception as exc:
        failure_reason = str(exc)
        if run_dir.config_path.exists():
            run_dir.update_config(
                completion_status="failed",
                failure_reason=failure_reason,
            )
        raise
    finally:
        shutdown_report = safe_shutdown(devices, channels, params)
        main_field_restore_errors: list[str] = []
        if main_field_state is not None and devices.get("gs200") is not None:
            try:
                restore_main_field_state(
                    devices["gs200"],
                    main_field_state,
                )
            except Exception as exc:
                main_field_restore_errors.append(str(exc))
        if shutdown_report.errors or main_field_restore_errors:
            print(
                "安全恢复警告: "
                + "；".join(
                    [*shutdown_report.errors, *main_field_restore_errors]
                )
            )
        if run_dir.config_path.exists():
            run_dir.update_config(
                completion_status=completion_status,
                failure_reason=failure_reason,
                safety_shutdown=shutdown_report.to_dict(),
                main_field_restore={
                    "attempted": main_field_state is not None,
                    "success": not main_field_restore_errors,
                    "errors": main_field_restore_errors,
                },
                device_disconnect={
                    "tec_disconnected": not shutdown_report.disconnect_errors,
                    "other_devices_preserved": True,
                    "errors": list(shutdown_report.disconnect_errors),
                },
            )


def main() -> int:
    params = load_runtime_params(MxZOptimalControlRFParams)
    run(params)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
