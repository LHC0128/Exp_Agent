"""Mx Y RF 光功率灵敏度优化采集工作流。"""

from __future__ import annotations

import io
from pathlib import Path
import sys
from typing import Any

import numpy as np
import yaml
from tqdm.auto import tqdm

from ...common import (
    WorkflowCancelled,
    find_project_root,
    load_mapping,
    validate_safety_limit,
)
from ...experiment_runtime import check_cancelled, load_runtime_params
from ...steps import DeviceSession, create_run_directory
from ..mx_y_rf_sensitivity.workflow import (
    RPointQualityError,
    acquire_mx_y_rf_point,
    configure_mx_y_rf_outputs,
    connect_mx_y_rf_devices,
    restore_mx_y_rf_laser_powers,
    safe_shutdown,
    snapshot_mx_y_rf_state,
)
from .models import MxYRFPowerOptimizationParams
from .scan import PowerGridPoint, build_power_axes, iter_serpentine_grid


EXPERIMENT_ID = "mx-y-rf-power-optimization"
DATA_TYPE = "Mx_Y_RF_Power_Optimization"
EXECUTION_MODE = "typed_workflow"


def _write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    """更新可恢复的网格采集清单，并兼容 Windows 覆盖限制。"""
    content = yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(content, encoding="utf-8")
    try:
        temporary.replace(path)
    except PermissionError:
        path.write_text(content, encoding="utf-8")
        temporary.unlink(missing_ok=True)


def _initial_manifest(
    params: MxYRFPowerOptimizationParams,
    points: list[PowerGridPoint],
) -> dict[str, Any]:
    pump_axis, probe_axis = build_power_axes(params)
    return {
        "schema_version": 1,
        "scan_order": "pump_outer_probe_inner_serpentine",
        "pump_power_v": pump_axis.tolist(),
        "probe_power_v": probe_axis.tolist(),
        "acquisition_order": [point.key for point in points],
        "points": {
            point.key: {
                "pump_index": point.pump_index,
                "probe_index": point.probe_index,
                "pump_power_v": point.pump_power_v,
                "probe_power_v": point.probe_power_v,
                "status": "pending",
                "reason": None,
            }
            for point in points
        },
    }


def _set_power_point(
    point: PowerGridPoint,
    devices: dict[str, Any],
    channels: dict[str, int],
) -> None:
    """设置一个 Pump/Probe 功率点，不增加额外等待。"""
    validate_safety_limit("Pump_laser_power", point.pump_power_v)
    validate_safety_limit("Probe_laser_power", point.probe_power_v)
    laser = devices["laser"]
    laser.setup_dc(point.pump_power_v, channel=channels["pump_laser"])
    laser.set_output(True, channel=channels["pump_laser"])
    laser.setup_dc(point.probe_power_v, channel=channels["probe_laser"])
    laser.set_output(True, channel=channels["probe_laser"])


def _acquire_grid(
    params: MxYRFPowerOptimizationParams,
    run_dir: Any,
    devices: dict[str, Any],
    channels: dict[str, int],
    actual_response_rate: float,
    device_id: str,
) -> dict[str, Any]:
    points = iter_serpentine_grid(params)
    pump_axis, probe_axis = build_power_axes(params)
    points_root = run_dir.raw / "points"
    points_root.mkdir(parents=True, exist_ok=True)
    manifest_path = run_dir.raw / "point_manifest.yaml"
    manifest = _initial_manifest(params, points)
    _write_manifest(manifest_path, manifest)
    np.savez(
        run_dir.raw / "power_grid.npz",
        pump_power_v=pump_axis,
        probe_power_v=probe_axis,
        acquisition_order=np.asarray([point.key for point in points]),
    )

    completed = 0
    invalid = 0
    actual_response_rates: list[float] = []
    actual_noise_rates: list[float] = []
    total = len(points)
    interactive_progress = bool(sys.stdout.isatty())
    progress_stream = sys.stdout if interactive_progress else io.StringIO()
    progress = tqdm(
        total=total,
        desc="Pump/Probe 功率网格",
        unit="point",
        file=progress_stream,
        dynamic_ncols=True,
    )
    try:
        point_iterator = enumerate(points)
        for sequence_index, point in point_iterator:
            check_cancelled()
            record = manifest["points"][point.key]
            record["status"] = "running"
            record["sequence_index"] = sequence_index
            _write_manifest(manifest_path, manifest)
            progress.set_postfix_str(
                f"Pump={point.pump_power_v:.3f} V, "
                f"Probe={point.probe_power_v:.3f} V"
            )
            print(
                f"光功率点 {sequence_index + 1}/{total}: "
                f"Pump={point.pump_power_v:.6g} V, "
                f"Probe={point.probe_power_v:.6g} V"
            )
            _set_power_point(point, devices, channels)
            point_raw = points_root / point.key
            try:
                point_result = acquire_mx_y_rf_point(
                    params,
                    point_raw,
                    devices,
                    channels,
                    actual_response_rate,
                    device_id,
                )
            except RPointQualityError as exc:
                invalid += 1
                record["status"] = "invalid_quality"
                record["reason"] = str(exc)
                _write_manifest(manifest_path, manifest)
                print(f"  该光功率点无效，继续扫描: {exc}")
            else:
                completed += 1
                response_rate = float(
                    point_result["actual_response_rate_sa_s"]
                )
                noise_rate = float(
                    point_result["actual_noise_rate_sa_s"]
                )
                actual_response_rates.append(response_rate)
                actual_noise_rates.append(noise_rate)
                record["status"] = "completed"
                record["reason"] = None
                record["actual_response_rate_sa_s"] = response_rate
                record["actual_noise_rate_sa_s"] = noise_rate
                _write_manifest(manifest_path, manifest)
            progress.update(1)
            if not interactive_progress:
                print(str(progress), flush=True)
    finally:
        progress.close()

    return {
        "total_point_count": total,
        "completed_point_count": completed,
        "invalid_quality_point_count": invalid,
        "actual_response_rates_sa_s": actual_response_rates,
        "actual_noise_rates_sa_s": actual_noise_rates,
        "manifest": "raw/point_manifest.yaml",
        "grid": "raw/power_grid.npz",
    }


def run(params: MxYRFPowerOptimizationParams) -> Path:
    root = find_project_root()
    mapping = load_mapping(root)
    run_dir = create_run_directory(
        DATA_TYPE,
        params.run_tag,
        params.to_external(),
        schema_version=params.schema_version,
        project_root=root,
    )
    run_dir.update_config(
        experiment_id=EXPERIMENT_ID,
        data_type=DATA_TYPE,
        execution_mode=EXECUTION_MODE,
        scan_mode="nested_scan",
        scan_order="pump_outer_probe_inner_serpentine",
        objective="minimize flat_median_ft_per_sqrt_hz",
        geometry={
            "main_field": "Z",
            "pump": "Z",
            "probe": "X",
            "rf_field": "Y",
            "x_field_output": "OFF",
        },
        linewidth_mode="amplitude_equivalent",
        compatibility={
            "reference_experiment_id": "mx-y-rf-sensitivity",
            "schema_version": params.schema_version,
        },
        acquisition_signals=["R"],
        power_restore={
            "pump_power_v": params.pump_laser_power_v,
            "probe_power_v": params.probe_laser_power_v,
            "outputs_on": True,
        },
    )
    session = DeviceSession()
    devices: dict[str, Any] = {}
    channels: dict[str, int] = {}
    completion_status = "failed"
    failure_reason: str | None = None
    grid_summary: dict[str, Any] | None = None
    try:
        check_cancelled()
        try:
            devices, channels = connect_mx_y_rf_devices(mapping, session)
        except Exception:
            session.cleanup_connection_failure()
            raise
        run_dir.update_config(
            device_snapshot=snapshot_mx_y_rf_state(devices, channels)
        )
        actual_response_rate, clock_sources = configure_mx_y_rf_outputs(
            params,
            devices,
            channels,
            mapping,
        )
        run_dir.update_config(clock_sources=clock_sources)
        device_id = str(mapping["lockin_r"]["device_id"])
        grid_summary = _acquire_grid(
            params,
            run_dir,
            devices,
            channels,
            actual_response_rate,
            device_id,
        )
        completion_status = "completed"
        run_dir.update_config(
            completion_status=completion_status,
            actual_rates={"response_sa_s": actual_response_rate},
            grid_summary=grid_summary,
            data_files=["raw/power_grid.npz", "raw/point_manifest.yaml"],
        )
        print(f"Mx Y RF 光功率灵敏度优化采集完成: {run_dir.root}")
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
        restore_error = None
        try:
            restore_mx_y_rf_laser_powers(
                devices,
                channels,
                pump_power_v=params.pump_laser_power_v,
                probe_power_v=params.probe_laser_power_v,
            )
        except Exception as exc:
            restore_error = str(exc)
            print(f"光功率基准恢复警告: {restore_error}")
        if shutdown_report.errors:
            print("安全关闭警告: " + "；".join(shutdown_report.errors))
        if run_dir.config_path.exists():
            run_dir.update_config(
                completion_status=completion_status,
                failure_reason=failure_reason,
                safety_shutdown=shutdown_report.to_dict(),
                laser_power_restore={
                    "pump_power_v": params.pump_laser_power_v,
                    "probe_power_v": params.probe_laser_power_v,
                    "outputs_on": True,
                    "success": restore_error is None,
                    "error": restore_error,
                },
                device_disconnect={
                    "tec_disconnected": not shutdown_report.disconnect_errors,
                    "other_devices_preserved": True,
                    "errors": list(shutdown_report.disconnect_errors),
                },
            )


def main() -> int:
    params = load_runtime_params(MxYRFPowerOptimizationParams)
    run(params)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
