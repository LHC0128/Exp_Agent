"""真实校相、幅度与噪声采集；恢复温控后才写盘。"""
from __future__ import annotations
from dataclasses import replace
from pathlib import Path
from typing import Any
import numpy as np
import yaml
from ...experiment_runtime import check_cancelled
from ...steps import configure_fixed_dc_field
from .models import MxZOptimalControlRFParams
from ...steps.acquisition_window import DeferredAcquisitionWrites
from ...steps.dispersion import save_scan
from ..mx_y_rf_sensitivity.acquisition import acquire_r, acquire_rxy, summarize_r, summarize_rxy
from ..mx_y_rf_sensitivity.scan import build_amplitude_axis
from ..mx_y_rf_sensitivity.workflow import RPointQualityError, _acquire_noise, _acquire_valid_r_point, _temperature_gated_acquire
from .excitation import _validate_y_rf_envelope, _rearm_y_rf, _rearm_control
from .phase import detect_complex_phase_outliers, fit_phase_scan, fit_quadrature_phase_scan, paired_phase_order
from .phase_plot import plot_phase_calibration
def _save_rxy_point(
    path: Path,
    payload: dict[str, np.ndarray],
    metadata: dict[str, Any],
    actual_rate: float,
) -> dict[str, float]:
    """保存单个正交校相点的同步 R/X/Y 及质量统计。"""
    summary = summarize_rxy(payload)
    save_scan(path, dict(
        time_s=payload["time_s"],
        r_v=payload["r"],
        x_v=payload["x"],
        y_v=payload["y"],
        actual_rate_sa_s=payload.get("actual_rate_Sa_s", np.float64(actual_rate)),
        **metadata,
        **{
            key: np.float64(value) if isinstance(value, float) else value
            for key, value in summary.items()
        },
     **{key: value for key, value in payload.items() if key not in {"time_s", "r", "x", "y"}}))
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
    reader=None, gated_acquire=None,
) -> tuple[dict[str, float], int, str]:
    """采集一个 R/X/Y 点；复噪声超限时完整保存并重采。"""
    reader = reader or acquire_rxy
    gated_acquire = gated_acquire or _temperature_gated_acquire
    hf2 = devices["hf2"]
    for attempt in range(params.r_point_max_attempts):
        checker = devices.get("_compliance_checker")
        if checker is not None:
            checker(f"{file_stem} 采集前")
        with DeferredAcquisitionWrites() as pending:
            def acquire_and_buffer():
                payload = reader(
                        hf2,
                        device_id=device_id,
                        demod_idx=params.demod_idx,
                        actual_rate_sa_s=actual_rate, requested_rate_sa_s=params.response_rate_sa_s,
                        duration_s=duration_s,
                    )
                summary = summarize_rxy(payload)
                accepted = (
                    summary["complex_std_v"]
                    <= params.r_bad_point_std_threshold_v
                )
                filename = f"{file_stem}_attempt_{attempt:02d}.npz"
                pending.add(_save_rxy_point,
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
                return payload
            payload = gated_acquire(
                params,
                devices,
                channels,
                set_y_rf=set_y_rf,
                settle_time_s=settle_time_s,
                acquire=acquire_and_buffer,
            )
        summary = summarize_rxy(payload)
        if checker is not None:
            checker(f"{file_stem} 采集后")
        accepted = (
            summary["complex_std_v"]
            <= params.r_bad_point_std_threshold_v
        )
        filename = f"{file_stem}_attempt_{attempt:02d}.npz"
        print(
            f"  X/Y 质量 {attempt + 1}/{params.r_point_max_attempts}: "
            f"complex_std={summary['complex_std_v']:.6g} V, "
            f"{'接受' if accepted else '拒绝并重采'}"
        )
        if accepted:
            return {**summary, "actual_rate_sa_s": float(payload.get("actual_rate_Sa_s", actual_rate))}, attempt, filename
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
    point_acquirer=None,
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

        summary, attempt, filename = (point_acquirer or _acquire_valid_rxy_point)(
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
            settle_time_s=params.system_settle_s,
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
    *, r_point_acquirer=None, rxy_point_acquirer=None,
) -> tuple[float, dict[str, Any]]:
    """按校相幅度选择 Y RF 校相或无 RF 交流的 Z 控制相位扫描。"""
    r_point_acquirer = r_point_acquirer or _acquire_valid_r_point
    rxy_point_acquirer = rxy_point_acquirer or _acquire_valid_rxy_point
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
    failure = None
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
            _, _, dummy_file = rxy_point_acquirer(
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
                settle_time_s=params.system_settle_s,
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
                r_point_acquirer
                if residual_mode
                else rxy_point_acquirer
            )
            summary, attempt, filename = acquire_point(
                params,
                run_dir,
                devices,
                channels,
                file_stem=f"phase_{index:04d}",
                metadata=metadata,
                set_y_rf=set_phase,
                settle_time_s=params.system_settle_s,
                duration_s=params.response_duration_s,
                actual_rate=actual_rate,
                device_id=device_id,
            )
            summaries.append(summary)
            attempts.append(attempt)
            files.append(filename)
    except BaseException as exc:
        failure = exc
        raise
    finally:
        if residual_mode:
            assert control is not None
            restore_errors = []
            for action in (
                lambda: configure_fixed_dc_field(rf, channels["y_rf"], "Y_magnetic_field", params.y_rf_offset_v),
                lambda: _rearm_control(control, channels["z_field"], phase_deg=params.control_burst_phase_deg),
            ):
                try:
                    action()
                except Exception as exc:
                    restore_errors.append(str(exc))
            if restore_errors:
                message = "恢复剩磁扫描输出失败：" + "；".join(restore_errors)
                if failure is None:
                    raise RuntimeError(message)
                failure.acquisition_cleanup_notes = [*getattr(failure, "acquisition_cleanup_notes", ()), message]
                getattr(failure, "add_note", print)(message)

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
            point_acquirer=rxy_point_acquirer,
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
        "actual_rate_sa_s": np.float64(summaries[0].get("actual_rate_sa_s", actual_rate)),
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
    save_scan(run_dir.raw / "phase_scan.npz", phase_arrays)
    (run_dir.results / "phase_calibration.yaml").write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    plot_phase_calibration(run_dir.raw, run_dir.results, payload)
    if not residual_mode and not quadrature_fit.success:
        raise RPointQualityError(
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
    *, point_acquirer=None,
) -> None:
    """在选定触发相位上扫描带符号 Y RF 幅度。"""
    point_acquirer = point_acquirer or _acquire_valid_r_point
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
        summary, attempt, filename = point_acquirer(
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
            settle_time_s=params.system_settle_s,
            duration_s=params.response_duration_s,
            actual_rate=actual_rate,
            device_id=device_id,
        )
        summaries.append(summary)
        hardware.append(point_hardware)
        attempts.append(attempt)
        files.append(filename)
    save_scan(run_dir.raw / "amplitude_scan.npz", dict(
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
        actual_rate_sa_s=np.float64(summaries[0].get("actual_rate_sa_s", actual_rate)),
    ))


def _acquire_control_noise(
    params: MxZOptimalControlRFParams,
    run_dir: Any,
    devices: dict[str, Any],
    channels: dict[str, int],
    device_id: str,
    selected_phase_deg: float,
    *, noise_acquirer=None,
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

    return (noise_acquirer or _acquire_noise)(
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
        settle_time_s=params.system_settle_s if rf_enabled else 0.0,
    )
