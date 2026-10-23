"""蛇形功率扫描；完整工作点复用原 R 响应/噪声协议与恢复后写盘。"""
from pathlib import Path
import io
import sys
from time import monotonic
import numpy as np
import yaml
from tqdm.auto import tqdm
from ...common import validate_safety_limit
from ...experiment_runtime import check_cancelled, report_runtime_progress
from ..mx_y_rf_sensitivity.acquisition_steps import RPointQualityError, acquire_mx_y_rf_point
from .scan import build_power_axes, iter_serpentine_grid


def _write_manifest(path, manifest):
    """原子替换失败时保留上一份可恢复清单，不回退到截断覆盖。"""
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_text(yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _initial_manifest(params, points):
    pump_axis, probe_axis = build_power_axes(params)
    return dict(schema_version=1, scan_order="pump_outer_probe_inner_serpentine",
        pump_power_v=pump_axis.tolist(), probe_power_v=probe_axis.tolist(),
        acquisition_order=[point.key for point in points],
        points={point.key: dict(pump_index=point.pump_index, probe_index=point.probe_index,
            pump_power_v=point.pump_power_v, probe_power_v=point.probe_power_v,
            status="pending", reason=None) for point in points})


def _set_power_point(point, devices, channels):
    validate_safety_limit("Pump_laser_power", point.pump_power_v)
    validate_safety_limit("Probe_laser_power", point.probe_power_v)
    laser = devices["laser"]
    laser.setup_dc(point.pump_power_v, channel=channels["pump_laser"])
    laser.set_output(True, channel=channels["pump_laser"])
    laser.setup_dc(point.probe_power_v, channel=channels["probe_laser"])
    laser.set_output(True, channel=channels["probe_laser"])


def _acquire_grid(params, run_dir, devices, channels, actual_response_rate, device_id):
    points = iter_serpentine_grid(params)
    pump_axis, probe_axis = build_power_axes(params)
    points_root = run_dir.raw / "points"
    points_root.mkdir(parents=True, exist_ok=True)
    manifest_path = run_dir.raw / "point_manifest.yaml"
    manifest = _initial_manifest(params, points)
    _write_manifest(manifest_path, manifest)
    np.savez(run_dir.raw / "power_grid.npz", pump_power_v=pump_axis, probe_power_v=probe_axis,
        acquisition_order=np.asarray([point.key for point in points]))
    completed = invalid = 0
    actual_response_rates, actual_noise_rates = [], []
    total, started = len(points), monotonic()
    interactive = bool(sys.stdout.isatty())
    progress = tqdm(total=total, desc="Pump/Probe 功率网格", unit="point",
        file=sys.stdout if interactive else io.StringIO(), dynamic_ncols=True)
    failure = None
    try:
        for sequence_index, point in enumerate(points):
            check_cancelled()
            record = manifest["points"][point.key]
            record.update(status="running", sequence_index=sequence_index)
            _write_manifest(manifest_path, manifest)
            progress.set_postfix_str(f"Pump={point.pump_power_v:.3f} V, Probe={point.probe_power_v:.3f} V")
            print(f"光功率点 {sequence_index + 1}/{total}: Pump={point.pump_power_v:.6g} V, Probe={point.probe_power_v:.6g} V")
            try:
                _set_power_point(point, devices, channels)
                point_result = acquire_mx_y_rf_point(params, points_root / point.key, devices, channels,
                    actual_response_rate, device_id)
            except RPointQualityError as exc:
                if getattr(exc, "safety_cleanup_errors", ()):
                    raise
                invalid += 1
                record.update(status="invalid_quality", reason=str(exc))
                print(f"  该光功率点无效，继续扫描: {exc}")
            else:
                completed += 1
                response_rate = float(point_result["actual_response_rate_sa_s"])
                noise_rate = float(point_result["actual_noise_rate_sa_s"])
                actual_response_rates.append(response_rate)
                actual_noise_rates.append(noise_rate)
                record.update(status="completed", reason=None, actual_response_rate_sa_s=response_rate,
                    actual_noise_rate_sa_s=noise_rate)
            _write_manifest(manifest_path, manifest)
            progress.update(1)
            if not interactive:
                print(str(progress), flush=True)
            count = sequence_index + 1
            report_runtime_progress("A08", f"光功率点 {count}/{total}", 100 * count / total,
                data={"completed_points": count, "total_points": total,
                    "estimated_remaining_seconds": (monotonic() - started) / count * (total - count)})
    except BaseException as exc:
        failure = exc
        if "record" in locals() and record["status"] == "running":
            record.update(status="failed", reason=str(exc))
            try:
                _write_manifest(manifest_path, manifest)
            except Exception as save_error:
                if hasattr(exc, "add_note"):
                    exc.add_note("点状态保存失败：" + str(save_error))
        raise
    finally:
        try:
            progress.close()
            report_runtime_progress("A08", "功率网格采集已退出", data={"estimated_remaining_seconds": None})
        except Exception:
            if failure is None:
                raise
    return dict(total_point_count=total, completed_point_count=completed,
        invalid_quality_point_count=invalid, actual_response_rates_sa_s=actual_response_rates,
        actual_noise_rates_sa_s=actual_noise_rates, manifest="raw/point_manifest.yaml", grid="raw/power_grid.npz")
