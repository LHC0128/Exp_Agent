"""XY 蛇形网格、逐点响应及 RF-off 噪声；复用已验收的采集与恢复。"""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
import numpy as np
import yaml
from ...common import WorkflowCancelled
from ...experiment_runtime import check_cancelled, report_runtime_progress
from ...steps import configure_fixed_dc_field
from ..mx_z_optimal_control_rf_sensitivity.workflow import _acquire_amplitude_scan, _acquire_noise
from ...steps.mx_z_optimal_control import restore_y_rf_burst_sine as _restore_y_rf_burst_sine
from ..mx_y_rf_sensitivity.workflow import RPointQualityError, _configure_response_demodulator
from .models import MxZOptimalControlXYRFSensitivityParams

def _write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    """原子更新网格清单，保证取消后已完成点可离线分析。"""
    content = yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


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
    """在温控开启时完成响应采集配置。"""
    _restore_y_rf_burst_sine(devices['xy_field'], channels['y_rf'], frequency_hz=params.y_rf_frequency_hz, amplitude_vpp=abs(params.phase_cal_rf_amplitude_vpp), offset_v=params.y_rf_offset_v, phase_deg=float(selected_phase_deg))
    return float(_configure_response_demodulator(params, devices['hf2']))


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
        except BaseException as exc:
            record.update(status="cancelled" if isinstance(exc, WorkflowCancelled) else "failed", reason=str(exc))
            try:
                _write_manifest(manifest_path, manifest)
            except Exception as save_error:
                exc.acquisition_cleanup_notes = [*getattr(exc, "acquisition_cleanup_notes", []),
                    "保存点状态失败：" + str(save_error)]
            raise
        report_runtime_progress("A05", "XY 网格点采集完成",
            100 * (int(point["sequence_index"]) + 1) / len(points),
            data={"point_key": key, "total_point_count": len(points)})
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
