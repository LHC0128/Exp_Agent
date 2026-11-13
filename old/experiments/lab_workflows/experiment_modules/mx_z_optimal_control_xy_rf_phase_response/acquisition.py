"""蛇形网格、180° 相位顺序、逐点质量重试及单轮异常重采。"""
from typing import Any
import time
import numpy as np
from ...experiment_runtime import check_cancelled, report_runtime_progress
from ...steps import configure_fixed_dc_field
from ..mx_y_rf_sensitivity.workflow import _acquire_valid_r_point
from ..mx_keithley_6221_optimal_control_rf_sensitivity.phase import detect_dispersion_phase_outliers
from .models import MxZOptimalControlXYRFPhaseResponseParams
from .scan import build_axes, iter_serpentine_grid
from .excitation import _rearm_y_rf_at_offset
from .phase import _fit_payload


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
    再用非线性色散模型的稳健残差识别异常；异常数量超过上限时保留原始数据，
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
            settle_time_s=params.system_settle_s,
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
        actual_rate_sa_s=np.float64(records[0].get("actual_rate_sa_s", actual_rate)),
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
            settle_time_s=params.system_settle_s,
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
        "actual_rate_sa_s": summaries[0].get("actual_rate_sa_s", actual_rate),
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
    started = time.monotonic()
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
        elapsed = time.monotonic() - started
        report_runtime_progress("A04", f"XY 相位网格 {len(records)}/{total}", 100 * len(records) / total,
            data={"estimated_remaining_seconds": elapsed / len(records) * (total - len(records))})
    return records
