"""蛇形 XY 扫描；复用已验收的逐条温控门控与延迟保存。"""
from pathlib import Path
from types import SimpleNamespace
from typing import Any
import numpy as np
import yaml
from ...common import WorkflowCancelled
from ...experiment_runtime import check_cancelled
from ...steps import configure_fixed_dc_field
from ..mx_y_rf_sensitivity.acquisition_steps import _acquire_noise
from .models import MxZOptimalControlXYNoiseSpectrumParams

def _write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    """原子更新网格清单，保证取消后已完成点可分析。"""
    content = yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(content, encoding="utf-8")
    try:
        temporary.replace(path)
    finally:
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



def initialize_grid(params, run_dir):
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



def _acquire_grid(
    params: MxZOptimalControlXYNoiseSpectrumParams,
    run_dir: Any,
    devices: dict[str, Any],
    channels: dict[str, int],
    device_id: str,
) -> dict[str, Any]:
    points = _grid_points(params)
    manifest_path = Path(run_dir.raw) / "point_manifest.yaml"
    if not manifest_path.is_file():
        initialize_grid(params, run_dir)
    manifest = _initial_manifest(params, points)
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
        except BaseException as exc:
            record["status"] = "cancelled" if isinstance(exc, WorkflowCancelled) else "failed"
            record["reason"] = str(exc)
            try:
                _write_manifest(manifest_path, manifest)
            except Exception as error:
                notes = list(getattr(exc, "acquisition_cleanup_notes", []))
                notes.append("保存故障点清单失败：" + str(error))
                exc.acquisition_cleanup_notes = notes
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
