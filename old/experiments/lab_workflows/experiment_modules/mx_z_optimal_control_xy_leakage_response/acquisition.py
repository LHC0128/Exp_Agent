"""蛇形扫描、逐点 R 质量重试与恢复温控后的原始聚合。"""
import time
import numpy as np
from ...experiment_runtime import check_cancelled, report_runtime_progress
from ..mx_y_rf_sensitivity.workflow import _acquire_valid_r_point
from .scan import build_xy_axes, iter_serpentine_grid, signed_control_hardware, first_acquired_minimum


def _save_scan_aggregate(run_dir, params, records, actual_rate, best):
    x_axis, y_axis = build_xy_axes(params)
    shape = (x_axis.size, y_axis.size)
    matrices = {key: np.full(shape, np.nan) for key in (
        "r_mean_v", "r_scalar_mean_v", "r_std_v", "x_hardware_phase_deg", "y_hardware_phase_deg")}
    matrices.update({key: np.full(shape, -1, dtype=int) for key in ("accepted_attempt_index", "acquisition_order")})
    matrices.update({key: np.zeros(shape, dtype=np.uint8) for key in ("x_output_on", "y_output_on")})
    matrices["accepted_file"] = np.full(shape, "", dtype="<U128")
    for record in records:
        index = (int(record["x_index"]), int(record["y_index"]))
        for key, matrix in matrices.items():
            matrix[index] = record["acquisition_index" if key == "acquisition_order" else key]
    np.savez(run_dir.raw / "xy_leakage_scan.npz", x_signed_amplitude_vpp=x_axis,
        y_signed_amplitude_vpp=y_axis, **matrices,
        x_hardware_amplitude_vpp=np.abs(x_axis)[:, None] * np.ones((1, y_axis.size)),
        y_hardware_amplitude_vpp=np.ones((x_axis.size, 1)) * np.abs(y_axis)[None, :],
        actual_rate_sa_s=np.float64(records[0].get("actual_rate_sa_s", actual_rate)),
        best_x_index=np.int64(best["x_index"]), best_y_index=np.int64(best["y_index"]),
        best_x_signed_amplitude_vpp=np.float64(best["x_signed_vpp"]),
        best_y_signed_amplitude_vpp=np.float64(best["y_signed_vpp"]),
        best_r_mean_v=np.float64(best["r_mean_v"]), best_r_std_v=np.float64(best["r_std_v"]),
        best_combination_remeasured=np.uint8(False))


def _acquire_grid(params, run_dir, devices, channels, controller, actual_rate, device_id):
    records = []
    total = params.x_control_amp_points * params.y_control_amp_points
    started = time.monotonic()
    for acquisition_index, x_index, y_index, x_vpp, y_vpp in iter_serpentine_grid(params):
        check_cancelled()
        hardware = {}
        for axis, value in (("x", x_vpp), ("y", y_vpp)):
            amplitude, phase, output_on = signed_control_hardware(value, params.control_burst_phase_deg)
            hardware.update({f"{axis}_hardware_amplitude_vpp": amplitude, f"{axis}_hardware_phase_deg": phase,
                f"{axis}_output_on": output_on})
        print(f"XY 网格 {acquisition_index + 1}/{total}: X={x_vpp:+.6f} Vpp, Y={y_vpp:+.6f} Vpp")
        summary, attempt, filename = _acquire_valid_r_point(params, run_dir, devices, channels,
            file_stem=f"grid_X{x_index:03d}_Y{y_index:03d}",
            metadata={"acquisition_index": np.int64(acquisition_index), "x_index": np.int64(x_index),
                "y_index": np.int64(y_index), "x_signed_amplitude_vpp": np.float64(x_vpp),
                "y_signed_amplitude_vpp": np.float64(y_vpp),
                **{key: np.uint8(value) if isinstance(value, bool) else np.float64(value) for key, value in hardware.items()}},
            set_y_rf=lambda x=x_vpp, y=y_vpp: controller.apply(x, y), settle_time_s=params.system_settle_s,
            duration_s=params.response_duration_s, actual_rate=actual_rate, device_id=device_id)
        records.append(dict(acquisition_index=acquisition_index, x_index=x_index, y_index=y_index,
            x_signed_vpp=x_vpp, y_signed_vpp=y_vpp, **hardware, **summary,
            accepted_attempt_index=attempt, accepted_file=filename))
        elapsed = time.monotonic() - started
        report_runtime_progress("A04", f"XY 网格 {len(records)}/{total}", 100 * len(records) / total,
            data={"estimated_remaining_seconds": elapsed / len(records) * (total - len(records))})
    best = dict(first_acquired_minimum(records))
    _save_scan_aggregate(run_dir, params, records, actual_rate, best)
    return records, best
