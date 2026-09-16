"""Mx Z 最优控制 XY 补偿偏置 RF 灵敏度采集工作流。"""

from __future__ import annotations

from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import yaml

from ...common import WorkflowCancelled, find_project_root, load_mapping
from ...control_sources import (
    AppliedControlWaveform,
    TheoryControlSource,
    ZCalibrationSource,
    build_applied_control,
    corrected_control_contract,
    load_theory_control,
    load_z_calibration,
)
from ...current_feedback import load_corrected_control_waveform
from ...experiment_runtime import check_cancelled, load_runtime_params
from ...steps import (
    DeviceSession,
    configure_fixed_dc_field,
    create_run_directory,
    restore_main_field_state,
    save_corrected_control_source_snapshot,
    save_optimal_control_source_snapshot,
    snapshot_main_field_state,
    validate_z_trigger_mapping,
)
from ..mx_z_field_calibration.workflow import _initial_state_snapshot
from ..mx_z_optimal_control_rf_sensitivity.workflow import (
    _acquire_amplitude_scan,
    _acquire_noise,
    _acquire_phase_scan,
    _configure_common_outputs,
    _restore_y_rf_burst_sine,
    safe_shutdown,
)
from ..mx_z_optimal_control_rf_sensitivity.workflow import (
    _connect_control_devices as _base_connect_control_devices,
)
from ..mx_y_rf_sensitivity.workflow import (
    RPointQualityError,
    _configure_response_demodulator,
    _temperature_gated_acquire,
)
from .models import MxZOptimalControlXYRFSensitivityParams


EXPERIMENT_ID = "mx-z-optimal-control-xy-rf-sensitivity"
DATA_TYPE = "Mx_Z_Optimal_Control_XY_RF_Sensitivity"
EXECUTION_MODE = "typed_workflow"


def _connect_control_devices(
    mapping: dict[str, dict[str, Any]],
    session: DeviceSession,
) -> tuple[dict[str, Any], dict[str, int]]:
    """连接父实验所需设备并解析共同触发通道。"""
    devices, channels = _base_connect_control_devices(mapping, session)
    channels["trigger"] = validate_z_trigger_mapping(mapping)
    return devices, channels


def _load_control_sources(
    params: MxZOptimalControlXYRFSensitivityParams,
    root: Path,
) -> tuple[
    Any,
    TheoryControlSource,
    ZCalibrationSource | None,
    AppliedControlWaveform,
]:
    if params.control_waveform_source == "corrected_run":
        corrected = load_corrected_control_waveform(
            root,
            params.corrected_control_source_run,
        )
        theory, applied = corrected_control_contract(corrected)
        return corrected, theory, None, applied
    theory = load_theory_control(
        Path(params.control_results_root),
        params.control_version,
    )
    calibration = load_z_calibration(root, params.z_calibration_source_run)
    applied = build_applied_control(
        theory,
        calibration,
        params.control_scale,
        output_vpp=params.z_aw_output_vpp,
        output_offset_v=params.z_aw_output_offset_v,
    )
    return None, theory, calibration, applied


def _write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    """原子更新网格清单，保证取消后已完成点可离线分析。"""
    content = yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(content, encoding="utf-8")
    try:
        temporary.replace(path)
    except PermissionError:
        path.write_text(content, encoding="utf-8")
        temporary.unlink(missing_ok=True)


def _grid_points(
    params: MxZOptimalControlXYRFSensitivityParams,
) -> list[dict[str, Any]]:
    x_axis = params.x_axis()
    y_axis = params.y_axis()
    points: list[dict[str, Any]] = []
    sequence = 0
    for x_index, x_value in enumerate(x_axis):
        y_indices = range(y_axis.size)
        if x_index % 2:
            y_indices = reversed(range(y_axis.size))
        for y_index in y_indices:
            points.append(
                {
                    "key": f"x_{x_index:03d}_y_{y_index:03d}",
                    "x_index": int(x_index),
                    "y_index": int(y_index),
                    "x_field_v": float(x_value),
                    "y_field_v": float(y_axis[y_index]),
                    "sequence_index": sequence,
                }
            )
            sequence += 1
    return points


def _initial_manifest(
    params: MxZOptimalControlXYRFSensitivityParams,
    points: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "scan_order": "x_outer_y_inner_serpentine",
        "x_field_v": params.x_axis().tolist(),
        "y_field_v": params.y_axis().tolist(),
        "acquisition_order": [item["key"] for item in points],
        "points": {
            item["key"]: {
                key: value
                for key, value in item.items()
                if key != "key"
            }
            | {
                "status": "pending",
                "reason": None,
            }
            for item in points
        },
    }


def _point_params(
    params: MxZOptimalControlXYRFSensitivityParams,
    point: dict[str, Any],
) -> MxZOptimalControlXYRFSensitivityParams:
    """将网格点坐标注入父 RF 采集 helper 使用的固定字段。"""
    return replace(
        params,
        x_dc_field_v=float(point["x_field_v"]),
        y_rf_offset_v=float(point["y_field_v"]),
    )


def _set_point_bias(
    devices: dict[str, Any],
    channels: dict[str, int],
    point: dict[str, Any],
) -> None:
    xy = devices["xy_field"]
    configure_fixed_dc_field(
        xy,
        channels["x_field"],
        "X_magnetic_field",
        float(point["x_field_v"]),
    )
    configure_fixed_dc_field(
        xy,
        channels["y_rf"],
        "Y_magnetic_field",
        float(point["y_field_v"]),
    )


def _restore_xy_response_acquisition_state(
    params: MxZOptimalControlXYRFSensitivityParams,
    devices: dict[str, Any],
    channels: dict[str, int],
    selected_phase_deg: float,
) -> float:
    """恢复当前 XY 点的带偏置外触发 Burst 和 HF2 响应配置。

    Mx Y 的恢复步骤会关闭 Burst 并清零 offset，只适用于连续正弦实验；
    XY 网格的 Mx Z 扫描必须在进入幅度循环前显式恢复 Burst 状态。
    """
    initial_amplitude = max(
        abs(float(params.y_rf_amp_start_vpp)),
        abs(float(params.y_rf_amp_stop_vpp)),
        1e-6,
    )
    actual_rate = _temperature_gated_acquire(
        params,
        devices,
        channels,
        set_y_rf=lambda: _restore_y_rf_burst_sine(
            devices["xy_field"],
            channels["y_rf"],
            frequency_hz=params.y_rf_frequency_hz,
            amplitude_vpp=initial_amplitude,
            offset_v=params.y_rf_offset_v,
            phase_deg=float(selected_phase_deg),
        ),
        settle_time_s=0.0,
        acquire=lambda: _configure_response_demodulator(
            params,
            devices["hf2"],
        ),
    )
    return float(actual_rate)


def _acquire_point(
    params: MxZOptimalControlXYRFSensitivityParams,
    point: dict[str, Any],
    run_dir: Any,
    devices: dict[str, Any],
    channels: dict[str, int],
    device_id: str,
    expected_response_rate: float,
    selected_phase_deg: float,
) -> dict[str, Any]:
    """完成一个 XY 点的幅度响应和 RF-off 噪声采集。"""
    point_params = _point_params(params, point)
    point_raw = Path(run_dir.raw) / "points" / str(point["key"])
    point_raw.mkdir(parents=True, exist_ok=True)
    point_run_dir = SimpleNamespace(raw=point_raw, results=run_dir.results)
    _set_point_bias(devices, channels, point)

    response_rate = _restore_xy_response_acquisition_state(
        point_params,
        devices,
        channels,
        selected_phase_deg,
    )
    _acquire_amplitude_scan(
        point_params,
        point_run_dir,
        devices,
        channels,
        response_rate,
        device_id,
        float(selected_phase_deg),
    )
    noise_rate = _acquire_noise(
        point_params,
        point_run_dir,
        devices,
        channels,
        device_id,
        set_y_rf_off_state=lambda: configure_fixed_dc_field(
            devices["xy_field"],
            channels["y_rf"],
            "Y_magnetic_field",
            float(point["y_field_v"]),
        ),
        y_rf_dc_v=float(point["y_field_v"]),
        y_rf_output_on=float(point["y_field_v"]) != 0.0,
    )
    return {
        "actual_response_rate_sa_s": float(response_rate),
        "actual_noise_rate_sa_s": float(noise_rate),
        "selected_y_rf_phase_deg": float(selected_phase_deg),
    }


def _acquire_grid(
    params: MxZOptimalControlXYRFSensitivityParams,
    run_dir: Any,
    devices: dict[str, Any],
    channels: dict[str, int],
    device_id: str,
    expected_response_rate: float,
    selected_phase_deg: float,
) -> dict[str, Any]:
    points = _grid_points(params)
    manifest_path = Path(run_dir.raw) / "point_manifest.yaml"
    manifest = _initial_manifest(params, points)
    _write_manifest(manifest_path, manifest)
    np.savez(
        Path(run_dir.raw) / "xy_bias_grid.npz",
        x_field_v=params.x_axis(),
        y_field_v=params.y_axis(),
        acquisition_order=np.asarray([item["key"] for item in points]),
    )

    completed = 0
    invalid_quality = 0
    response_rates: list[float] = []
    noise_rates: list[float] = []
    for point in points:
        check_cancelled()
        key = str(point["key"])
        record = manifest["points"][key]
        record["status"] = "running"
        _write_manifest(manifest_path, manifest)
        print(
            f"XY 补偿偏置点 {int(point['sequence_index']) + 1}/{len(points)}: "
            f"X={float(point['x_field_v']):+.6f} V, "
            f"Y={float(point['y_field_v']):+.6f} V"
        )
        try:
            result = _acquire_point(
                params,
                point,
                run_dir,
                devices,
                channels,
                device_id,
                expected_response_rate,
                selected_phase_deg,
            )
        except RPointQualityError as exc:
            invalid_quality += 1
            record["status"] = "invalid_quality"
            record["reason"] = str(exc)
            _write_manifest(manifest_path, manifest)
            print(f"  该 XY 点无效，继续扫描: {exc}")
            continue
        record.update(result)
        record["status"] = "completed"
        record["reason"] = None
        completed += 1
        response_rates.append(result["actual_response_rate_sa_s"])
        noise_rates.append(result["actual_noise_rate_sa_s"])
        _write_manifest(manifest_path, manifest)

    return {
        "total_point_count": len(points),
        "completed_point_count": completed,
        "invalid_quality_point_count": invalid_quality,
        "actual_response_rates_sa_s": response_rates,
        "actual_noise_rates_sa_s": noise_rates,
        "manifest": "raw/point_manifest.yaml",
        "grid": "raw/xy_bias_grid.npz",
    }


def run(params: MxZOptimalControlXYRFSensitivityParams) -> Path:
    """执行中心一次校相和 XY 补偿偏置网格灵敏度采集。"""
    root = find_project_root()
    mapping = load_mapping(root)
    corrected, theory, calibration, applied = _load_control_sources(params, root)
    x_axis = params.x_axis()
    y_axis = params.y_axis()
    center_x, center_y = params.phase_calibration_point()
    calibration_params = replace(
        params,
        x_dc_field_v=center_x,
        y_rf_offset_v=center_y,
    )

    warnings: list[str] = []
    if not np.isclose(
        params.y_rf_frequency_hz,
        theory.theory_rf_frequency_hz,
        rtol=1e-9,
        atol=1e-9,
    ):
        warnings.append(
            "Y RF/HF2 频率与理论控制频率不同；相位校准结果按共同触发起始相位复用"
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
    source_files = (
        save_corrected_control_source_snapshot(run_dir.raw, corrected, applied)
        if corrected is not None
        else save_optimal_control_source_snapshot(
            run_dir.raw,
            theory,
            calibration,
            applied,
        )
    )
    run_dir.update_config(
        experiment_id=EXPERIMENT_ID,
        data_type=DATA_TYPE,
        execution_mode=EXECUTION_MODE,
        measurement_mode="xy_bias_grid_rf_sensitivity",
        scan_mode="nested_scan",
        scan_order="x_outer_y_inner_serpentine",
        objective="minimize flat_median_ft_per_sqrt_hz",
        geometry={
            "gs200_main_field_current_ma": params.main_magnetic_field_ma,
            "control_field": "Z arbitrary waveform",
            "rf_field": "Y triggered sine with per-point DC compensation",
            "x_field_output": "X_magnetic_field DC",
            "x_points": int(x_axis.size),
            "y_points": int(y_axis.size),
        },
        residual_field_compensation={
            "x_source": "X_magnetic_field DC",
            "y_source": "rf_coil waveform DC offset",
            "units": "V",
            "phase_reference": "arithmetic center of scan axes",
        },
        phase_calibration_reference={
            "x_field_v": center_x,
            "y_field_v": center_y,
            "selected_phase_reused_for_all_points": True,
        },
        control_source={
            "mode": params.control_waveform_source,
            "version": theory.version,
            "waveform_sha256": theory.waveform_sha256,
            "parameter_sha256": theory.parameter_sha256,
            "repeat_frequency_hz": theory.repeat_frequency_hz,
            "theory_rf_frequency_hz": theory.theory_rf_frequency_hz,
            "scale": params.control_scale if corrected is None else None,
            "burst_phase_deg": params.control_burst_phase_deg,
            "corrected_run": corrected.run_name if corrected is not None else None,
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
        scan_axes={
            "x_field_v": x_axis.tolist(),
            "y_field_v": y_axis.tolist(),
            "order": "X outer, Y serpentine",
        },
        acquisition_signals=[
            "Demod0 R/X/Y at the single phase calibration point",
            "Demod0 R during each XY amplitude/noise point",
        ],
        linewidth_mode="amplitude_equivalent",
        warnings=warnings,
    )

    session = DeviceSession()
    devices: dict[str, Any] = {}
    channels: dict[str, int] = {}
    main_field_state = None
    completion_status = "failed"
    failure_reason: str | None = None
    grid_summary: dict[str, Any] | None = None
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

        (
            actual_response_rate,
            actual_temperature,
            clock_sources,
            hf2_snapshot,
        ) = _configure_common_outputs(
            calibration_params,
            devices,
            channels,
            mapping,
            theory,
            applied,
        )
        run_dir.update_config(
            clock_sources=clock_sources,
            hf2_configuration=hf2_snapshot,
            initial_temperature_c=actual_temperature,
        )
        device_id = str(mapping["lockin_r"]["device_id"])
        selected_phase, phase_payload = _acquire_phase_scan(
            calibration_params,
            run_dir,
            devices,
            channels,
            actual_response_rate,
            device_id,
        )
        if not np.isfinite(selected_phase):
            raise RuntimeError("中心 XY 补偿点 RF 相位校准未得到有效相位")
        run_dir.update_config(
            phase_calibration=phase_payload,
            selected_y_rf_phase_deg=float(selected_phase),
        )
        grid_summary = _acquire_grid(
            params,
            run_dir,
            devices,
            channels,
            device_id,
            actual_response_rate,
            float(selected_phase),
        )
        completion_status = "completed"
        run_dir.update_config(
            completion_status=completion_status,
            failure_reason=None,
            actual_rates={"response_sa_s": actual_response_rate},
            grid_summary=grid_summary,
            data_files=[
                *source_files,
                "raw/phase_scan.npz",
                "raw/point_manifest.yaml",
                "raw/xy_bias_grid.npz",
            ],
        )
        print(f"Mx Z 最优控制 XY 补偿偏置 RF 灵敏度采集完成: {run_dir.root}")
        return run_dir.root
    except WorkflowCancelled as exc:
        completion_status = "cancelled"
        failure_reason = str(exc)
        if run_dir.config_path.exists():
            run_dir.update_config(
                completion_status=completion_status,
                failure_reason=failure_reason,
                grid_summary=grid_summary,
            )
        raise
    except Exception as exc:
        failure_reason = str(exc)
        if run_dir.config_path.exists():
            run_dir.update_config(
                completion_status="failed",
                failure_reason=failure_reason,
                grid_summary=grid_summary,
            )
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
                device_disconnect={
                    "tec_disconnected": not shutdown_report.disconnect_errors,
                    "other_devices_preserved": True,
                    "errors": list(shutdown_report.disconnect_errors),
                },
            )


def main() -> int:
    run(load_runtime_params(MxZOptimalControlXYRFSensitivityParams))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
