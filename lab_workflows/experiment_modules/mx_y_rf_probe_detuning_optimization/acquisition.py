"""Probe/PZT 蛇形网格、行稳定、逐点温控门控与恢复后保存。"""
from __future__ import annotations
import io
from pathlib import Path
import sys
import time
from typing import Any
import numpy as np
import yaml
from tqdm.auto import tqdm

from ...common import WorkflowCancelled, validate_safety_limit
from ...experiment_runtime import check_cancelled, report_runtime_progress
from ..mx_y_rf_sensitivity.acquisition_steps import RPointQualityError, acquire_mx_y_rf_point
from .models import MxYRFProbeDetuningOptimizationParams
from .scan import ProbeDetuningGridPoint, build_probe_detuning_axes, iter_probe_detuning_grid
from .preparation import _sleep_cancellable

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


def _set_probe_power(
    point: ProbeDetuningGridPoint,
    devices: dict[str, Any],
    channels: dict[str, int],
) -> None:
    """设置一个 Probe 功率点并保持输出开启。"""
    validate_safety_limit("Probe_laser_power", point.probe_power_v)
    laser = devices["laser"]
    laser.setup_dc(
        point.probe_power_v,
        channel=channels["probe_laser"],
    )
    laser.set_output(True, channel=channels["probe_laser"])


def _set_pzt_row(
    params: MxYRFProbeDetuningOptimizationParams,
    instrument: Any,
    pzt_voltage_v: float,
) -> dict[str, float]:
    """设置一个静态 PZT 行，等待稳定并返回回读。"""
    validate_safety_limit("probe_laser_pzt_voltage", pzt_voltage_v)
    setpoint_readback = float(
        instrument.set_pzt_voltage_v(pzt_voltage_v)
    )
    _sleep_cancellable(params.pzt_settle_time_s)
    return {
        "pzt_voltage_v": setpoint_readback,
        "pzt_actual_v": float(instrument.get_pzt_voltage_actual_v()),
    }


def _read_pzt_point(
    instrument: Any,
    requested_pzt_voltage_v: float,
) -> dict[str, float]:
    """为每个完整灵敏度点保存 PZT setpoint 与实际电压。"""
    setpoint_readback = float(instrument.get_pzt_voltage_v())
    actual = float(instrument.get_pzt_voltage_actual_v())
    validate_safety_limit(
        "probe_laser_pzt_voltage",
        setpoint_readback,
    )
    return {
        "pzt_voltage_setpoint_v": float(requested_pzt_voltage_v),
        "pzt_voltage_readback_v": setpoint_readback,
        "pzt_actual_v": actual,
        "pzt_setpoint_error_v": (
            setpoint_readback - float(requested_pzt_voltage_v)
        ),
    }


def _initial_manifest(
    params: MxYRFProbeDetuningOptimizationParams,
    points: list[ProbeDetuningGridPoint],
) -> dict[str, Any]:
    pzt_axis, probe_axis = build_probe_detuning_axes(params)
    return {
        "schema_version": 1,
        "scan_order": "pzt_outer_probe_inner_serpentine",
        "pzt_voltage_v": pzt_axis.tolist(),
        "probe_power_v": probe_axis.tolist(),
        "pzt_settle_time_s": params.pzt_settle_time_s,
        "acquisition_order": [point.key for point in points],
        "pzt_rows": {},
        "points": {
            point.key: {
                "pzt_index": point.pzt_index,
                "probe_index": point.probe_index,
                "pzt_voltage_v": point.pzt_voltage_v,
                "probe_power_v": point.probe_power_v,
                "status": "pending",
                "reason": None,
            }
            for point in points
        },
    }


def _acquire_grid(
    params: MxYRFProbeDetuningOptimizationParams,
    run_dir: Any,
    devices: dict[str, Any],
    channels: dict[str, int],
    actual_response_rate: float,
    device_id: str,
    *, point_reader=None, set_probe=None, set_pzt_row=None, read_pzt=None,
) -> dict[str, Any]:
    point_reader = point_reader or acquire_mx_y_rf_point
    set_probe = set_probe or _set_probe_power
    set_pzt_row = set_pzt_row or _set_pzt_row
    read_pzt = read_pzt or _read_pzt_point
    points = iter_probe_detuning_grid(params)
    pzt_axis, probe_axis = build_probe_detuning_axes(params)
    points_root = run_dir.raw / "points"
    points_root.mkdir(parents=True, exist_ok=True)
    manifest_path = run_dir.raw / "point_manifest.yaml"
    manifest = _initial_manifest(params, points)
    _write_manifest(manifest_path, manifest)
    np.savez(
        run_dir.raw / "probe_detuning_grid.npz",
        pzt_voltage_v=pzt_axis,
        probe_power_v=probe_axis,
        acquisition_order=np.asarray([point.key for point in points]),
    )

    completed = 0
    invalid = 0
    actual_response_rates: list[float] = []
    actual_noise_rates: list[float] = []
    total = len(points)
    current_pzt_index: int | None = None
    pzt_controller = devices["probe_laser_controller"]
    interactive_progress = bool(sys.stdout.isatty())
    progress_stream = sys.stdout if interactive_progress else io.StringIO()
    progress = tqdm(
        total=total,
        desc="PZT/Probe 二维网格",
        unit="point",
        file=progress_stream,
        dynamic_ncols=True,
    )
    started = time.monotonic()
    try:
        for sequence_index, point in enumerate(points):
            check_cancelled()
            if point.pzt_index != current_pzt_index:
                row_readback = set_pzt_row(
                    params,
                    pzt_controller,
                    point.pzt_voltage_v,
                )
                manifest["pzt_rows"][f"pzt_{point.pzt_index:03d}"] = {
                    "pzt_index": point.pzt_index,
                    "requested_pzt_voltage_v": point.pzt_voltage_v,
                    **row_readback,
                }
                current_pzt_index = point.pzt_index
                _write_manifest(manifest_path, manifest)

            record = manifest["points"][point.key]
            record["status"] = "running"
            record["sequence_index"] = sequence_index
            _write_manifest(manifest_path, manifest)
            progress.set_postfix_str(
                f"PZT={point.pzt_voltage_v:.3f} V, "
                f"Probe={point.probe_power_v:.3f} V"
            )
            print(
                f"Probe/PZT 点 {sequence_index + 1}/{total}: "
                f"PZT={point.pzt_voltage_v:.6g} V, "
                f"Probe={point.probe_power_v:.6g} V"
            )
            set_probe(point, devices, channels)
            record.update(
                read_pzt(
                    pzt_controller,
                    point.pzt_voltage_v,
                )
            )
            _write_manifest(manifest_path, manifest)
            point_raw = points_root / point.key
            try:
                point_result = point_reader(
                    params,
                    point_raw,
                    devices,
                    channels,
                    actual_response_rate,
                    device_id,
                )
            except RPointQualityError as exc:
                if getattr(exc, "safety_cleanup_errors", ()):
                    raise
                invalid += 1
                record["status"] = "invalid_quality"
                record["reason"] = str(exc)
                _write_manifest(manifest_path, manifest)
                print(f"  该 Probe/PZT 点无效，继续扫描: {exc}")
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
            report_runtime_progress("A07", f"完成 Probe/PZT 点 {sequence_index + 1}/{total}",
                100.0 * (sequence_index + 1) / total,
                data={"estimated_remaining_seconds": (time.monotonic() - started) / (sequence_index + 1)
                    * (total - sequence_index - 1)})
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
        "grid": "raw/probe_detuning_grid.npz",
    }


