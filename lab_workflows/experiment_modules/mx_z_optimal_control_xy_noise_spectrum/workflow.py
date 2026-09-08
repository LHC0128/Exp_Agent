"""Mx Z 最优控制 XY 偏置噪声谱采集工作流。"""

from __future__ import annotations

from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import yaml

from ...common import WorkflowCancelled, find_project_root, load_mapping
from ...experiment_runtime import check_cancelled, load_runtime_params
from ...steps import (
    DeviceSession,
    configure_fixed_dc_field,
    create_run_directory,
    restore_main_field_state,
    snapshot_main_field_state,
    validate_z_trigger_mapping,
)
from ..mx_z_field_calibration.workflow import _initial_state_snapshot
from ..mx_z_optimal_control_rf_sensitivity.sources import (
    AppliedControlWaveform,
    TheoryControlSource,
    ZCalibrationSource,
    build_applied_control,
    load_theory_control,
    load_z_calibration,
)
from ..mx_z_optimal_control_rf_sensitivity.workflow import (
    _acquire_noise,
    _configure_common_outputs,
    _corrected_control_contract,
    _save_corrected_source_snapshot,
    _save_source_snapshot,
    safe_shutdown,
    _connect_control_devices as _base_connect_control_devices,
)
from ...current_feedback import load_corrected_control_waveform
from .models import MxZOptimalControlXYNoiseSpectrumParams


EXPERIMENT_ID = "mx-z-optimal-control-xy-noise-spectrum"
DATA_TYPE = "Mx_Z_Optimal_Control_XY_Noise_Spectrum"
EXECUTION_MODE = "typed_workflow"


def _connect_control_devices(
    mapping: dict[str, dict[str, Any]],
    session: DeviceSession,
) -> tuple[dict[str, Any], dict[str, int]]:
    """连接最优控制实验设备并解析共同触发通道。"""
    devices, channels = _base_connect_control_devices(mapping, session)
    channels["trigger"] = validate_z_trigger_mapping(mapping)
    return devices, channels


def _load_control_sources(
    params: MxZOptimalControlXYNoiseSpectrumParams,
    root: Path,
) -> tuple[Any, TheoryControlSource, ZCalibrationSource | None, AppliedControlWaveform]:
    if params.control_waveform_source == "corrected_run":
        corrected = load_corrected_control_waveform(
            root,
            params.corrected_control_source_run,
        )
        theory, applied = _corrected_control_contract(corrected)
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


def _write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    """原子更新网格清单，保证取消后已完成点可分析。"""
    content = yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(content, encoding="utf-8")
    try:
        temporary.replace(path)
    except PermissionError:
        path.write_text(content, encoding="utf-8")
        temporary.unlink(missing_ok=True)


def _grid_points(params: MxZOptimalControlXYNoiseSpectrumParams) -> list[dict[str, Any]]:
    """构造 X 外层、Y 内层蛇形采集顺序。"""
    points: list[dict[str, Any]] = []
    sequence = 0
    for x_index, x_value in enumerate(params.x_axis()):
        y_indices = range(params.y_axis().size)
        if x_index % 2:
            y_indices = reversed(range(params.y_axis().size))
        for y_index in y_indices:
            points.append(
                {
                    "key": f"x_{x_index:03d}_y_{y_index:03d}",
                    "x_index": int(x_index),
                    "y_index": int(y_index),
                    "x_field_v": float(x_value),
                    "y_field_v": float(params.y_axis()[y_index]),
                    "sequence_index": sequence,
                }
            )
            sequence += 1
    return points


def _initial_manifest(
    params: MxZOptimalControlXYNoiseSpectrumParams,
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
                **{key: value for key, value in item.items() if key != "key"},
                "status": "pending",
                "reason": None,
            }
            for item in points
        },
    }


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


def _acquire_point(
    params: MxZOptimalControlXYNoiseSpectrumParams,
    point: dict[str, Any],
    run_dir: Any,
    devices: dict[str, Any],
    channels: dict[str, int],
    device_id: str,
) -> dict[str, Any]:
    """在一个 XY DC 偏置点采集多次 RF-off R 噪声。"""
    point_raw = Path(run_dir.raw) / "points" / str(point["key"])
    point_raw.mkdir(parents=True, exist_ok=True)
    point_run_dir = SimpleNamespace(raw=point_raw, results=run_dir.results)
    _set_point_bias(devices, channels, point)

    noise_rate = _acquire_noise(
        params,
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
        noise_metadata={
            "x_dc_field_v": np.float64(point["x_field_v"]),
            "y_dc_field_v": np.float64(point["y_field_v"]),
            "y_rf_waveform_mode": np.str_("dc"),
            "control_output_on": np.uint8(True),
            "temperature_gate_state": np.str_("off_during_acquire_then_on"),
        },
    )
    return {
        "actual_noise_rate_sa_s": float(noise_rate),
        "x_dc_field_v": float(point["x_field_v"]),
        "y_dc_field_v": float(point["y_field_v"]),
    }


def _acquire_grid(
    params: MxZOptimalControlXYNoiseSpectrumParams,
    run_dir: Any,
    devices: dict[str, Any],
    channels: dict[str, int],
    device_id: str,
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
    noise_rates: list[float] = []
    for point in points:
        check_cancelled()
        record = manifest["points"][point["key"]]
        record["status"] = "running"
        _write_manifest(manifest_path, manifest)
        print(
            f"XY 噪声点 {int(point['sequence_index']) + 1}/{len(points)}: "
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
            )
        except WorkflowCancelled:
            record["status"] = "cancelled"
            record["reason"] = "收到 GUI 安全停止请求"
            _write_manifest(manifest_path, manifest)
            raise
        except Exception as exc:
            record["status"] = "failed"
            record["reason"] = str(exc)
            _write_manifest(manifest_path, manifest)
            raise
        record.update(result)
        record["status"] = "completed"
        record["reason"] = None
        completed += 1
        noise_rates.append(result["actual_noise_rate_sa_s"])
        _write_manifest(manifest_path, manifest)
    return {
        "total_point_count": len(points),
        "completed_point_count": completed,
        "actual_noise_rates_sa_s": noise_rates,
        "manifest": "raw/point_manifest.yaml",
        "grid": "raw/xy_bias_grid.npz",
    }


def run(params: MxZOptimalControlXYNoiseSpectrumParams) -> Path:
    """执行 Z 控制开启下的 XY DC 偏置噪声网格采集。"""
    root = find_project_root()
    mapping = load_mapping(root)
    corrected, theory, calibration, applied = _load_control_sources(params, root)
    source_files = None
    run_dir = create_run_directory(
        DATA_TYPE,
        params.run_tag,
        params.to_external(),
        schema_version=params.schema_version,
        project_root=root,
    )
    source_files = (
        _save_corrected_source_snapshot(run_dir.raw, corrected, theory, applied)
        if corrected is not None
        else _save_source_snapshot(run_dir.raw, theory, calibration, applied)
    )
    x_axis = params.x_axis()
    y_axis = params.y_axis()
    run_dir.update_config(
        experiment_id=EXPERIMENT_ID,
        data_type=DATA_TYPE,
        execution_mode=EXECUTION_MODE,
        measurement_mode="xy_bias_noise_spectrum",
        scan_mode="nested_scan",
        scan_order="x_outer_y_inner_serpentine",
        objective="minimize_noise_median_r_asd_v_per_sqrt_hz",
        geometry={
            "control_field": "Z arbitrary waveform",
            "rf_field": "Y RF AC OFF with Y DC compensation",
            "x_field_output": "X_magnetic_field DC",
            "x_points": int(x_axis.size),
            "y_points": int(y_axis.size),
            "bias_units": "V",
        },
        noise_metric={
            "signal": "Demod0 R",
            "quantity": "ASD",
            "aggregation": "median",
            "psd_aggregation": "mean_psd_then_sqrt",
            "band_hz": [params.low_freq_skip_hz, params.noise_band_max_hz],
            "narrowband_lines_included": True,
        },
        scan_axes={
            "x_field_v": x_axis.tolist(),
            "y_field_v": y_axis.tolist(),
            "order": "X outer, Y serpentine",
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
        acquisition_signals=["Demod0 R during each XY bias noise point"],
        rf_state_during_noise="burst_off_modulation_off_y_dc_on_if_nonzero",
        warnings=[],
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

        # 用零偏置配置公共工作点，之后每个网格点只改写 X/Y DC。
        common_params = replace(params, x_dc_field_v=0.0, y_rf_offset_v=0.0)
        actual_response_rate, actual_temperature, clock_sources, hf2_snapshot = _configure_common_outputs(
            common_params,
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
            actual_rates={"initial_response_sa_s": actual_response_rate},
        )
        device_id = str(mapping["lockin_r"]["device_id"])
        grid_summary = _acquire_grid(params, run_dir, devices, channels, device_id)
        completion_status = "completed"
        run_dir.update_config(
            completion_status=completion_status,
            failure_reason=None,
            grid_summary=grid_summary,
            data_files=[
                *source_files,
                "raw/point_manifest.yaml",
                "raw/xy_bias_grid.npz",
            ],
        )
        print(f"Mx Z 最优控制 XY 噪声谱采集完成: {run_dir.root}")
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
        restore_errors: list[str] = []
        if main_field_state is not None and devices.get("gs200") is not None:
            try:
                restore_main_field_state(devices["gs200"], main_field_state)
            except Exception as exc:
                restore_errors.append(str(exc))
        if run_dir.config_path.exists():
            run_dir.update_config(
                completion_status=completion_status,
                failure_reason=failure_reason,
                safety_shutdown=shutdown_report.to_dict(),
                main_field_restore={
                    "attempted": main_field_state is not None,
                    "success": not restore_errors,
                    "errors": restore_errors,
                },
                device_disconnect={
                    "tec_disconnected": not shutdown_report.disconnect_errors,
                    "other_devices_preserved": True,
                    "errors": list(shutdown_report.disconnect_errors),
                },
            )


def main() -> int:
    run(load_runtime_params(MxZOptimalControlXYNoiseSpectrumParams))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
