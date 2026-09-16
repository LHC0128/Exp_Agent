"""Mx Z 最优控制 XY 平衡场 RF 相位响应采集工作流。"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np

from ...common import WorkflowCancelled, find_project_root, load_mapping, validate_safety_limit
from ...current_feedback import CorrectedControlWaveform, load_corrected_control_waveform
from ...experiment_runtime import check_cancelled, load_runtime_params
from ...steps import (
    DeviceSession,
    configure_fixed_dc_field,
    configure_mx_z_optimal_control_workpoint,
    create_run_directory,
    restore_main_field_state,
    run_safety_shutdown,
    save_corrected_control_source_snapshot,
    save_optimal_control_source_snapshot,
    snapshot_main_field_state,
    validate_z_trigger_mapping,
    wait_for_temperature_stable,
)
from ...steps.mx_z_optimal_control import OPTIMAL_CONTROL_BURST_TRIGGER_SLOPE
from ..mx_y_rf_sensitivity.workflow import _acquire_valid_r_point, _set_pump_gate_on
from ..mx_z_field_calibration.workflow import _configure_reference_clocks, _connect_devices, _initial_state_snapshot
from ..mx_keithley_6221_optimal_control_rf_sensitivity.phase import (
    detect_dispersion_phase_outliers,
    dispersion_phase_response,
    fit_dispersion_phase_scan,
)
from ..mx_z_optimal_control_rf_sensitivity.phase import fit_phase_scan
from ...control_sources import (
    AppliedControlWaveform,
    TheoryControlSource,
    ZCalibrationSource,
    build_applied_control,
    corrected_control_contract,
    load_theory_control,
    load_z_calibration,
)
from ..mx_z_optimal_control_rf_sensitivity.workflow import (
    _validate_y_rf_envelope,
)
from .models import MxZOptimalControlXYRFPhaseResponseParams
from .scan import build_axes, iter_serpentine_grid


EXPERIMENT_ID = "mx-z-optimal-control-xy-rf-phase-response"
DATA_TYPE = "Mx_Z_Optimal_Control_XY_RF_Phase_Response"
EXECUTION_MODE = "typed_workflow"
NONLINEAR_FIT_MAX_STARTS = 48


class _RuntimeCancellation:
    @staticmethod
    def raise_if_cancelled() -> None:
        check_cancelled()


def _connect_control_devices(
    mapping: dict[str, dict[str, Any]],
    session: DeviceSession,
) -> tuple[dict[str, Any], dict[str, int]]:
    devices, channels = _connect_devices(mapping, session)
    channels["trigger"] = validate_z_trigger_mapping(mapping)
    return devices, channels


def _load_control_sources(
    params: MxZOptimalControlXYRFPhaseResponseParams,
    root: Path,
) -> tuple[
    CorrectedControlWaveform | None,
    TheoryControlSource,
    ZCalibrationSource | None,
    AppliedControlWaveform,
]:
    if params.control_waveform_source == "corrected_run":
        corrected = load_corrected_control_waveform(root, params.corrected_control_source_run)
        theory, applied = corrected_control_contract(corrected)
        return corrected, theory, None, applied
    theory = load_theory_control(Path(params.control_results_root), params.control_version)
    calibration = load_z_calibration(root, params.z_calibration_source_run)
    applied = build_applied_control(
        theory,
        calibration,
        params.control_scale,
        output_vpp=params.z_aw_output_vpp,
        output_offset_v=params.z_aw_output_offset_v,
    )
    return None, theory, calibration, applied


def _configure_y_rf_at_offset(
    params: MxZOptimalControlXYRFPhaseResponseParams,
    device: Any,
    channel: int,
    offset_v: float,
) -> None:
    _validate_y_rf_envelope(offset_v, params.phase_cal_rf_amplitude_vpp)
    device.set_output(False, channel=channel)
    device.set_burst_state(False, channel=channel)
    device.set_mod_state(False, channel=channel)
    device.setup_sine(
        params.y_rf_frequency_hz,
        params.phase_cal_rf_amplitude_vpp,
        offset=offset_v,
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


def _rearm_y_rf_at_offset(
    params: MxZOptimalControlXYRFPhaseResponseParams,
    device: Any,
    channel: int,
    *,
    offset_v: float,
    phase_deg: float,
) -> None:
    """用 OFF→设置 offset/phase→ON 重新等待下一次共同触发。"""
    _validate_y_rf_envelope(offset_v, params.phase_cal_rf_amplitude_vpp)
    device.set_output(False, channel=channel)
    device.set_burst_phase(float(phase_deg) % 360.0, channel=channel)
    device.set_amplitude(params.phase_cal_rf_amplitude_vpp, channel=channel)
    device.set_offset(float(offset_v), channel=channel)
    device.set_output(True, channel=channel)


def _configure_common_outputs(
    params: MxZOptimalControlXYRFPhaseResponseParams,
    devices: dict[str, Any],
    channels: dict[str, int],
    mapping: dict[str, dict[str, Any]],
    theory: TheoryControlSource,
    applied: AppliedControlWaveform,
    initial_x_v: float,
    initial_y_v: float,
) -> tuple[float, float | None, dict[str, dict[str, str]], dict[str, Any]]:
    def configure_xy_outputs() -> None:
        xy_field = devices["xy_field"]
        configure_fixed_dc_field(
            xy_field,
            channels["x_field"],
            "X_magnetic_field",
            initial_x_v,
        )
        _configure_y_rf_at_offset(
            params,
            xy_field,
            channels["y_rf"],
            initial_y_v,
        )

    return configure_mx_z_optimal_control_workpoint(
        params,
        devices,
        channels,
        mapping,
        theory,
        applied,
        demod_frequency_hz=params.y_rf_frequency_hz,
        cancellation=_RuntimeCancellation(),
        xy_output_configurator=configure_xy_outputs,
        reference_clock_configurator=_configure_reference_clocks,
        pump_gate_setter=_set_pump_gate_on,
        temperature_stability_waiter=wait_for_temperature_stable,
    )


def _fit_payload(
    phase_deg: np.ndarray,
    r_mean_v: np.ndarray,
    params: MxZOptimalControlXYRFPhaseResponseParams,
    *,
    r_std_v: np.ndarray | None = None,
    final_cross_phase_outliers: tuple[float, ...] = (),
) -> tuple[dict[str, Any], dict[str, float]]:
    noise = (
        np.zeros_like(r_mean_v, dtype=float)
        if r_std_v is None
        else np.asarray(r_std_v, dtype=float)
    )
    nonlinear = fit_dispersion_phase_scan(
        phase_deg,
        r_mean_v,
        noise,
        y_rf_amplitude_vpp=params.phase_cal_rf_amplitude_vpp,
        r_squared_min=params.phase_fit_r_squared_min,
        amplitude_sigma_min=params.phase_fit_amplitude_sigma_min,
        max_starts=NONLINEAR_FIT_MAX_STARTS,
    )
    legacy_primary, diagnostic = fit_phase_scan(
        phase_deg,
        r_mean_v,
        r_squared_min=params.phase_fit_r_squared_min,
        amplitude_sigma_min=params.phase_fit_amplitude_sigma_min,
    )
    rejection_reasons = list(nonlinear.rejection_reasons)
    if final_cross_phase_outliers:
        rejection_reasons.append(
            "单次跨相位重采后仍存在异常点: "
            + ", ".join(
                f"{float(value):.6g}°"
                for value in final_cross_phase_outliers
            )
        )
    primary_payload = nonlinear.to_dict()
    if rejection_reasons != list(nonlinear.rejection_reasons):
        primary_payload["success"] = False
        primary_payload["rejection_reasons"] = rejection_reasons
    payload = {
        "primary_fit": primary_payload,
        "diagnostic_fit": diagnostic.to_dict(),
        "legacy_primary_fit": legacy_primary.to_dict(),
        "fit_accepted": bool(nonlinear.success and not final_cross_phase_outliers),
    }
    summary = {
        # 这些字段保留旧 raw schema，实际正式判定来自非线性模型。
        "baseline_v": float(legacy_primary.parameters[0]),
        "amplitude_v": float(legacy_primary.parameters[1]),
        "phase_zero_deg": float(legacy_primary.parameters[2]),
        "baseline_uncertainty_v": float(legacy_primary.uncertainties[0]),
        "amplitude_uncertainty_v": float(legacy_primary.uncertainties[1]),
        "phase_zero_uncertainty_deg": float(legacy_primary.uncertainties[2]),
        "selected_phase_deg": float(legacy_primary.selected_phase_deg),
        "r_squared": float(legacy_primary.r_squared),
        "nonlinear_scale": float(nonlinear.parameters[0]),
        "nonlinear_resonance_amplitude_vpp": float(nonlinear.parameters[1]),
        "nonlinear_width_vpp": float(nonlinear.parameters[2]),
        "nonlinear_residual_amplitude_vpp": float(nonlinear.parameters[3]),
        "nonlinear_residual_phase_deg": float(nonlinear.parameters[4]),
        "nonlinear_scale_uncertainty": float(nonlinear.uncertainties[0]),
        "nonlinear_resonance_amplitude_vpp_uncertainty": float(nonlinear.uncertainties[1]),
        "nonlinear_width_vpp_uncertainty": float(nonlinear.uncertainties[2]),
        "nonlinear_residual_amplitude_vpp_uncertainty": float(nonlinear.uncertainties[3]),
        "nonlinear_residual_phase_deg_uncertainty": float(nonlinear.uncertainties[4]),
        "nonlinear_selected_phase_deg": float(nonlinear.selected_phase_deg),
        "nonlinear_observed_max_phase_deg": float(nonlinear.observed_max_phase_deg),
        "nonlinear_peak_to_peak_v": float(nonlinear.peak_to_peak_v),
        "nonlinear_noise_median_v": float(nonlinear.noise_median_v),
        "nonlinear_signal_to_noise": float(nonlinear.signal_to_noise),
        "nonlinear_r_squared": float(nonlinear.r_squared),
        "nonlinear_rmse_v": float(
            np.sqrt(
                np.mean(
                    (
                        np.asarray(r_mean_v, dtype=float)
                        - dispersion_phase_response(
                            phase_deg,
                            *nonlinear.parameters,
                            params.phase_cal_rf_amplitude_vpp,
                        )
                    )
                    ** 2
                )
            )
        ),
        "fit_accepted": bool(nonlinear.success and not final_cross_phase_outliers),
    }
    return payload, summary


def _reacquire_phase_outliers_once(
    params: MxZOptimalControlXYRFPhaseResponseParams,
    run_dir: Any,
    devices: dict[str, Any],
    channels: dict[str, int],
    *,
    x_index: int,
    y_index: int,
    acquisition_index: int,
    x_v: float,
    y_v: float,
    phase_axis: np.ndarray,
    summaries: list[dict[str, float]],
    attempts: list[int],
    files: list[str],
    actual_rate: float,
    device_id: str,
) -> tuple[dict[str, Any], np.ndarray, np.ndarray, np.ndarray, list[int], list[str]]:
    """识别跨相位异常点，并对每个异常相位定点重采一次。

    与 RF 灵敏度实验保持相同的单轮策略：先完成一整轮相位采集，
    再用稳健一阶谐波残差识别异常；异常数量超过上限时保留原始数据，
    不盲目重采整点。重采结果替换进入本 XY 点的最终拟合，同时保留
    初始文件和检测报告。
    """
    phase = np.asarray(phase_axis, dtype=float)
    r_mean = np.asarray([item["r_mean_v"] for item in summaries], dtype=float)
    r_std = np.asarray([item["r_std_v"] for item in summaries], dtype=float)
    initial = detect_dispersion_phase_outliers(
        phase,
        r_mean,
        r_std,
        y_rf_amplitude_vpp=params.phase_cal_rf_amplitude_vpp,
        sigma_threshold=params.phase_outlier_sigma_threshold,
    )
    initial_attempts = list(attempts)
    initial_files = list(files)
    reacquired = np.zeros(phase.size, dtype=bool)
    indices = initial.outlier_indices
    too_many = len(indices) > params.phase_outlier_max_reacquire_points
    if too_many:
        indices = ()

    xy = devices["xy_field"]
    replacement_files: list[str] = []
    for phase_index in indices:
        check_cancelled()
        phase_deg = float(phase[phase_index])
        print(
            "跨相位异常点重采 "
            f"X={x_v:+.6f} V, Y={y_v:+.6f} V, "
            f"phase={phase_deg:.3f} deg: residual="
            f"{initial.residual_v[phase_index]:.6g} V > "
            f"{initial.threshold_v:.6g} V"
        )

        def set_phase(phase_deg: float = phase_deg) -> None:
            _rearm_y_rf_at_offset(
                params,
                xy,
                channels["y_rf"],
                offset_v=y_v,
                phase_deg=phase_deg,
            )

        summary, attempt, filename = _acquire_valid_r_point(
            params,
            run_dir,
            devices,
            channels,
            file_stem=(
                f"grid_X{x_index:03d}_Y{y_index:03d}_phase_"
                f"{phase_index:03d}_cross_retry"
            ),
            metadata={
                "acquisition_index": np.int64(acquisition_index),
                "x_index": np.int64(x_index),
                "y_index": np.int64(y_index),
                "x_field_v": np.float64(x_v),
                "y_field_v": np.float64(y_v),
                "scanned_phase_deg": np.float64(phase_deg),
                "y_rf_burst_phase_deg": np.float64(phase_deg),
                "y_rf_amplitude_vpp": np.float64(
                    params.phase_cal_rf_amplitude_vpp
                ),
                "y_rf_dc_offset_v": np.float64(y_v),
                "y_rf_output_on": np.uint8(True),
                "cross_phase_reacquisition": np.uint8(True),
                "replaced_file": np.asarray(initial_files[phase_index]),
                "initial_cross_phase_residual_v": np.float64(
                    initial.residual_v[phase_index]
                ),
                "cross_phase_threshold_v": np.float64(initial.threshold_v),
            },
            set_y_rf=set_phase,
            settle_time_s=params.response_settle_time_s,
            duration_s=params.response_duration_s,
            actual_rate=actual_rate,
            device_id=device_id,
        )
        summaries[phase_index] = summary
        attempts[phase_index] = attempt
        files[phase_index] = filename
        reacquired[phase_index] = True
        replacement_files.append(filename)

    final_mean = np.asarray(
        [item["r_mean_v"] for item in summaries], dtype=float
    )
    final_std = np.asarray(
        [item["r_std_v"] for item in summaries], dtype=float
    )
    final = detect_dispersion_phase_outliers(
        phase,
        final_mean,
        final_std,
        y_rf_amplitude_vpp=params.phase_cal_rf_amplitude_vpp,
        sigma_threshold=params.phase_outlier_sigma_threshold,
    )
    report = {
        "enabled": True,
        "passes": 1,
        "max_reacquire_points": params.phase_outlier_max_reacquire_points,
        "too_many_initial_outliers": too_many,
        "initial_detection": initial.to_dict(),
        "reacquired_indices": [
            int(index) for index in np.flatnonzero(reacquired)
        ],
        "reacquired_phase_deg": [
            float(phase[index]) for index in np.flatnonzero(reacquired)
        ],
        "original_files": [
            initial_files[index] for index in np.flatnonzero(reacquired)
        ],
        "replacement_files": replacement_files,
        "final_detection": final.to_dict(),
    }
    return (
        report,
        reacquired,
        np.asarray(initial.residual_v, dtype=float),
        np.asarray(final.residual_v, dtype=float),
        initial_attempts,
        initial_files,
    )


def _save_grid_aggregate(
    run_dir: Any,
    params: MxZOptimalControlXYRFPhaseResponseParams,
    records: list[dict[str, Any]],
    actual_rate: float,
) -> None:
    x_axis, y_axis, configured_phase, acquisition_phase = build_axes(params)
    shape = (x_axis.size, y_axis.size)
    phase_shape = (*shape, acquisition_phase.size)
    r_mean = np.full(phase_shape, np.nan, dtype=float)
    r_std = np.full(phase_shape, np.nan, dtype=float)
    accepted_attempt = np.full(phase_shape, -1, dtype=int)
    accepted_file = np.full(phase_shape, "", dtype="<U240")
    initial_accepted_attempt = np.full(phase_shape, -1, dtype=int)
    initial_accepted_file = np.full(phase_shape, "", dtype="<U240")
    cross_phase_reacquired = np.zeros(phase_shape, dtype=bool)
    cross_phase_initial_residual = np.full(phase_shape, np.nan, dtype=float)
    cross_phase_final_residual = np.full(phase_shape, np.nan, dtype=float)
    acquisition_order = np.full(shape, -1, dtype=int)
    fit_names = (
        "baseline_v",
        "amplitude_v",
        "phase_zero_deg",
        "baseline_uncertainty_v",
        "amplitude_uncertainty_v",
        "phase_zero_uncertainty_deg",
        "selected_phase_deg",
        "r_squared",
        "nonlinear_scale",
        "nonlinear_resonance_amplitude_vpp",
        "nonlinear_width_vpp",
        "nonlinear_residual_amplitude_vpp",
        "nonlinear_residual_phase_deg",
        "nonlinear_scale_uncertainty",
        "nonlinear_resonance_amplitude_vpp_uncertainty",
        "nonlinear_width_vpp_uncertainty",
        "nonlinear_residual_amplitude_vpp_uncertainty",
        "nonlinear_residual_phase_deg_uncertainty",
        "nonlinear_selected_phase_deg",
        "nonlinear_observed_max_phase_deg",
        "nonlinear_peak_to_peak_v",
        "nonlinear_noise_median_v",
        "nonlinear_signal_to_noise",
        "nonlinear_r_squared",
        "nonlinear_rmse_v",
    )
    fit_arrays = {name: np.full(shape, np.nan, dtype=float) for name in fit_names}
    fit_accepted = np.zeros(shape, dtype=bool)
    fit_rejection = np.full(shape, "", dtype="<U1000")
    diagnostic_fit_names = (
        "baseline_v",
        "amplitude_v",
        "phase_zero_deg",
        "baseline_uncertainty_v",
        "amplitude_uncertainty_v",
        "phase_zero_uncertainty_deg",
        "selected_phase_deg",
        "observed_max_phase_deg",
        "r_squared",
    )
    diagnostic_arrays = {
        f"diagnostic_{name}": np.full(shape, np.nan, dtype=float)
        for name in diagnostic_fit_names
    }
    diagnostic_fit_accepted = np.zeros(shape, dtype=bool)
    diagnostic_fit_rejection = np.full(shape, "", dtype="<U1000")
    diagnostic_model = np.full(shape, "", dtype="<U64")
    for record in records:
        index = (record["x_index"], record["y_index"])
        acquisition_order[index] = record["acquisition_index"]
        r_mean[index] = record["r_mean_v"]
        r_std[index] = record["r_std_v"]
        accepted_attempt[index] = record["accepted_attempt_index"]
        accepted_file[index] = record["accepted_file"]
        initial_accepted_attempt[index] = record["initial_accepted_attempt_index"]
        initial_accepted_file[index] = record["initial_accepted_file"]
        cross_phase_reacquired[index] = record["cross_phase_reacquired"]
        cross_phase_initial_residual[index] = record["cross_phase_initial_residual_v"]
        cross_phase_final_residual[index] = record["cross_phase_final_residual_v"]
        for name in fit_names:
            fit_arrays[name][index] = record["fit_summary"][name]
        fit_accepted[index] = record["fit_summary"]["fit_accepted"]
        fit_rejection[index] = ";".join(record["fit_payload"]["primary_fit"]["rejection_reasons"])
        diagnostic = record["fit_payload"]["diagnostic_fit"]
        diagnostic_parameters = diagnostic["parameters"]
        diagnostic_uncertainties = diagnostic["uncertainties"]
        diagnostic_arrays["diagnostic_baseline_v"][index] = diagnostic_parameters[0]
        diagnostic_arrays["diagnostic_amplitude_v"][index] = diagnostic_parameters[1]
        diagnostic_arrays["diagnostic_phase_zero_deg"][index] = diagnostic_parameters[2]
        diagnostic_arrays["diagnostic_baseline_uncertainty_v"][index] = diagnostic_uncertainties[0]
        diagnostic_arrays["diagnostic_amplitude_uncertainty_v"][index] = diagnostic_uncertainties[1]
        diagnostic_arrays["diagnostic_phase_zero_uncertainty_deg"][index] = diagnostic_uncertainties[2]
        diagnostic_arrays["diagnostic_selected_phase_deg"][index] = diagnostic["selected_phase_deg"]
        diagnostic_arrays["diagnostic_observed_max_phase_deg"][index] = diagnostic["observed_max_phase_deg"]
        diagnostic_arrays["diagnostic_r_squared"][index] = diagnostic["r_squared"]
        diagnostic_fit_accepted[index] = diagnostic["success"]
        diagnostic_fit_rejection[index] = ";".join(diagnostic["rejection_reasons"])
        diagnostic_model[index] = diagnostic["model"]
    np.savez(
        run_dir.raw / "xy_rf_phase_response_scan.npz",
        x_field_v=x_axis,
        y_field_v=y_axis,
        configured_phase_deg=configured_phase,
        acquisition_phase_deg=acquisition_phase,
        r_mean_v=r_mean,
        r_std_v=r_std,
        accepted_attempt_index=accepted_attempt,
        accepted_file=accepted_file,
        initial_accepted_attempt_index=initial_accepted_attempt,
        initial_accepted_file=initial_accepted_file,
        cross_phase_reacquired=cross_phase_reacquired,
        cross_phase_initial_residual_v=cross_phase_initial_residual,
        cross_phase_final_residual_v=cross_phase_final_residual,
        acquisition_order=acquisition_order,
        actual_rate_sa_s=np.float64(actual_rate),
        fit_accepted=fit_accepted,
        fit_rejection_reason=fit_rejection,
        diagnostic_model=diagnostic_model,
        diagnostic_fit_accepted=diagnostic_fit_accepted,
        diagnostic_fit_rejection_reason=diagnostic_fit_rejection,
        **diagnostic_arrays,
        **{name: values for name, values in fit_arrays.items()},
    )


def _acquire_phase_curve(
    params: MxZOptimalControlXYRFPhaseResponseParams,
    run_dir: Any,
    devices: dict[str, Any],
    channels: dict[str, int],
    *,
    x_index: int,
    y_index: int,
    acquisition_index: int,
    x_v: float,
    y_v: float,
    phase_axis: np.ndarray,
    actual_rate: float,
    device_id: str,
) -> dict[str, Any]:
    xy = devices["xy_field"]
    configure_fixed_dc_field(xy, channels["x_field"], "X_magnetic_field", x_v)
    summaries: list[dict[str, float]] = []
    attempts: list[int] = []
    files: list[str] = []
    for phase_index, phase_deg in enumerate(phase_axis):
        check_cancelled()
        file_stem = f"grid_X{x_index:03d}_Y{y_index:03d}_phase_{phase_index:03d}"
        summary, attempt, filename = _acquire_valid_r_point(
            params,
            run_dir,
            devices,
            channels,
            file_stem=file_stem,
            metadata={
                "acquisition_index": np.int64(acquisition_index),
                "x_index": np.int64(x_index),
                "y_index": np.int64(y_index),
                "x_field_v": np.float64(x_v),
                "y_field_v": np.float64(y_v),
                "scanned_phase_deg": np.float64(phase_deg),
                "y_rf_burst_phase_deg": np.float64(phase_deg),
                "y_rf_amplitude_vpp": np.float64(params.phase_cal_rf_amplitude_vpp),
                "y_rf_dc_offset_v": np.float64(y_v),
                "y_rf_output_on": np.uint8(True),
            },
            set_y_rf=lambda phase=phase_deg: _rearm_y_rf_at_offset(
                params,
                xy,
                channels["y_rf"],
                offset_v=y_v,
                phase_deg=float(phase),
            ),
            settle_time_s=params.response_settle_time_s,
            duration_s=params.response_duration_s,
            actual_rate=actual_rate,
            device_id=device_id,
        )
        summaries.append(summary)
        attempts.append(attempt)
        files.append(filename)
    r_mean = np.asarray([item["r_mean_v"] for item in summaries], dtype=float)
    r_std = np.asarray([item["r_std_v"] for item in summaries], dtype=float)
    (
        cross_phase_report,
        cross_phase_reacquired,
        cross_phase_initial_residual,
        cross_phase_final_residual,
        initial_attempts,
        initial_files,
    ) = _reacquire_phase_outliers_once(
        params,
        run_dir,
        devices,
        channels,
        x_index=x_index,
        y_index=y_index,
        acquisition_index=acquisition_index,
        x_v=x_v,
        y_v=y_v,
        phase_axis=phase_axis,
        summaries=summaries,
        attempts=attempts,
        files=files,
        actual_rate=actual_rate,
        device_id=device_id,
    )
    r_mean = np.asarray([item["r_mean_v"] for item in summaries], dtype=float)
    r_std = np.asarray([item["r_std_v"] for item in summaries], dtype=float)
    final_outlier_phases = tuple(
        float(value)
        for value in cross_phase_report["final_detection"]["outlier_phase_deg"]
    )
    fit_payload, fit_summary = _fit_payload(
        phase_axis,
        r_mean,
        params,
        r_std_v=r_std,
        final_cross_phase_outliers=final_outlier_phases,
    )
    fit_payload["cross_phase_reacquisition"] = cross_phase_report
    return {
        "acquisition_index": acquisition_index,
        "x_index": x_index,
        "y_index": y_index,
        "x_field_v": x_v,
        "y_field_v": y_v,
        "r_mean_v": r_mean,
        "r_std_v": r_std,
        "accepted_attempt_index": np.asarray(attempts, dtype=int),
        "accepted_file": np.asarray(files),
        "initial_accepted_attempt_index": np.asarray(initial_attempts, dtype=int),
        "initial_accepted_file": np.asarray(initial_files),
        "cross_phase_reacquired": cross_phase_reacquired,
        "cross_phase_initial_residual_v": cross_phase_initial_residual,
        "cross_phase_final_residual_v": cross_phase_final_residual,
        "fit_payload": fit_payload,
        "fit_summary": fit_summary,
    }


def _acquire_grid(
    params: MxZOptimalControlXYRFPhaseResponseParams,
    run_dir: Any,
    devices: dict[str, Any],
    channels: dict[str, int],
    actual_rate: float,
    device_id: str,
) -> list[dict[str, Any]]:
    _, _, _, phase_axis = build_axes(params)
    records: list[dict[str, Any]] = []
    total = params.x_field_points * params.y_field_points
    for acquisition_index, x_index, y_index, x_v, y_v in iter_serpentine_grid(params):
        check_cancelled()
        print(
            f"XY RF 相位网格 {acquisition_index + 1}/{total}: "
            f"X={x_v:+.6f} V, Y={y_v:+.6f} V"
        )
        records.append(
            _acquire_phase_curve(
                params,
                run_dir,
                devices,
                channels,
                x_index=x_index,
                y_index=y_index,
                acquisition_index=acquisition_index,
                x_v=x_v,
                y_v=y_v,
                phase_axis=phase_axis,
                actual_rate=actual_rate,
                device_id=device_id,
            )
        )
        _save_grid_aggregate(run_dir, params, records, actual_rate)
    return records


def safe_shutdown(
    devices: dict[str, Any],
    channels: dict[str, int],
    params: MxZOptimalControlXYRFPhaseResponseParams,
):
    """复用 RF 灵敏度实验的安全结束策略。"""
    from ..mx_z_optimal_control_rf_sensitivity.workflow import safe_shutdown as _safe_shutdown

    return _safe_shutdown(devices, channels, params)


def run(params: MxZOptimalControlXYRFPhaseResponseParams) -> Path:
    """执行 XY 网格上的 RF 相位响应采集。"""
    root = find_project_root()
    mapping = load_mapping(root)
    corrected, theory, calibration, applied = _load_control_sources(params, root)
    x_axis, y_axis, configured_phase, acquisition_phase = build_axes(params)
    run_dir = create_run_directory(
        DATA_TYPE,
        params.run_tag,
        params.to_external(),
        schema_version=params.schema_version,
        project_root=root,
    )
    source_files = (
        save_corrected_control_source_snapshot(run_dir.raw, corrected, applied)
        if corrected is not None
        else save_optimal_control_source_snapshot(run_dir.raw, theory, calibration, applied)
    )
    run_dir.update_config(
        experiment_id=EXPERIMENT_ID,
        data_type=DATA_TYPE,
        execution_mode=EXECUTION_MODE,
        measurement_mode="xy_grid_rf_phase_response",
        geometry={
            "x_field": "X_magnetic_field DC scan",
            "y_field": "rf_coil DC offset scan",
            "rf_phase_target": "Y RF Burst phase",
            "x_points": int(x_axis.size),
            "y_points": int(y_axis.size),
            "phase_points": int(acquisition_phase.size),
            "main_field_ma": params.main_magnetic_field_ma,
        },
        control_source={
            "mode": params.control_waveform_source,
            "version": theory.version,
            "waveform_sha256": theory.waveform_sha256,
            "parameter_sha256": theory.parameter_sha256,
            "repeat_frequency_hz": theory.repeat_frequency_hz,
            "theory_rf_frequency_hz": theory.theory_rf_frequency_hz,
            "scale": params.control_scale if corrected is None else None,
            "corrected_run": corrected.run_name if corrected is not None else None,
            "burst_phase_deg": params.control_burst_phase_deg,
        },
        z_calibration=(
            {
                "source_run": calibration.run_name,
                "slope_hz_per_v": calibration.slope_hz_per_v,
                "intercept_hz": calibration.intercept_hz,
                "r_squared": calibration.r_squared,
                "analysis_sha256": calibration.analysis_sha256,
            }
            if calibration is not None
            else None
        ),
        applied_control={
            "minimum_v": applied.minimum_v,
            "maximum_v": applied.maximum_v,
            "amplitude_vpp": applied.amplitude_vpp,
            "offset_v": applied.offset_v,
            "points": int(theory.time_s.size),
        },
        scan_axes={
            "x_field_v": x_axis.tolist(),
            "y_field_v": y_axis.tolist(),
            "configured_phase_deg": configured_phase.tolist(),
            "acquisition_phase_deg": acquisition_phase.tolist(),
            "order": "X outer, Y serpentine; phase paired phi then phi+180",
        },
        fixed_conditions={
            "main_magnetic_field_ma": params.main_magnetic_field_ma,
            "pump_laser_power_v": params.pump_laser_power_v,
            "probe_laser_power_v": params.probe_laser_power_v,
            "temperature_c": params.temperature_c,
            "y_rf_frequency_hz": params.y_rf_frequency_hz,
            "phase_cal_rf_amplitude_vpp": params.phase_cal_rf_amplitude_vpp,
            "demod_signal": "HF2 Demod0 R",
        },
        acquisition_signals=["Demod0 R"],
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
        snapshot["main_field_before_configuration"] = asdict(main_field_state)
        run_dir.update_config(device_snapshot=snapshot)
        actual_rate, actual_temperature, clock_sources, hf2_snapshot = _configure_common_outputs(
            params,
            devices,
            channels,
            mapping,
            theory,
            applied,
            float(x_axis[0]),
            float(y_axis[0]),
        )
        run_dir.update_config(
            clock_sources=clock_sources,
            hf2_configuration=hf2_snapshot,
            initial_temperature_c=actual_temperature,
            actual_rates={"response_sa_s": actual_rate},
        )
        device_id = str(mapping["lockin_r"]["device_id"])
        records = _acquire_grid(params, run_dir, devices, channels, actual_rate, device_id)
        completion_status = "completed"
        run_dir.update_config(
            completion_status=completion_status,
            failure_reason=None,
            data_files=[*source_files, "raw/xy_rf_phase_response_scan.npz"],
            grid_points=len(records),
        )
        print(f"Mx Z 最优控制 XY 平衡场 RF 相位响应采集完成: {run_dir.root}")
        return run_dir.root
    except WorkflowCancelled as exc:
        failure_reason = str(exc)
        completion_status = "cancelled"
        if run_dir.config_path.exists():
            run_dir.update_config(completion_status=completion_status, failure_reason=failure_reason)
        raise
    except Exception as exc:
        failure_reason = str(exc)
        if run_dir.config_path.exists():
            run_dir.update_config(completion_status="failed", failure_reason=failure_reason)
        raise
    finally:
        shutdown_report = safe_shutdown(devices, channels, params)
        main_field_restore_errors: list[str] = []
        if main_field_state is not None and devices.get("gs200") is not None:
            try:
                restore_main_field_state(devices["gs200"], main_field_state)
            except Exception as exc:
                main_field_restore_errors.append(str(exc))
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
            )


def main() -> int:
    params = load_runtime_params(MxZOptimalControlXYRFPhaseResponseParams)
    run(params)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
