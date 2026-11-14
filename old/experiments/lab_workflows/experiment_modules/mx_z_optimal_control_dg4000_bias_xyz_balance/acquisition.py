"""真实同步 R/X/Y 网格采集、质量重试与恢复温控后的保存。"""
from typing import Any
import time
import numpy as np
from ...experiment_runtime import check_cancelled, report_runtime_progress
from ..mx_z_optimal_control_rf_sensitivity.workflow import _acquire_valid_rxy_point
from .excitation import _XYZBiasController
from .models import MxZOptimalControlDG4000BiasXYZBalanceParams
from .scan import build_xyz_axes, first_acquired_minimum, iter_serpentine_grid

def _save_scan_aggregate(
    run_dir: Any,
    params: MxZOptimalControlDG4000BiasXYZBalanceParams,
    records: list[dict[str, Any]],
    actual_rate: float,
    best: dict[str, Any],
    calibration: Any,
    applied: Any,
) -> None:
    x_axis, y_axis, z_axis = build_xyz_axes(params)
    shape = (z_axis.size, x_axis.size, y_axis.size)
    arrays = {
        name: np.full(shape, np.nan, dtype=float)
        for name in (
            "r_mean_v",
            "x_mean_v",
            "y_mean_v",
            "r_std_v",
            "x_std_v",
            "y_std_v",
            "complex_std_v",
        )
    }
    accepted_attempt = np.full(shape, -1, dtype=int)
    acquisition_order = np.full(shape, -1, dtype=int)
    accepted_file = np.full(shape, "", dtype="<U200")
    for record in records:
        index = (
            int(record["z_index"]),
            int(record["x_index"]),
            int(record["y_index"]),
        )
        for name, values in arrays.items():
            values[index] = float(record[name])
        accepted_attempt[index] = int(record["accepted_attempt_index"])
        acquisition_order[index] = int(record["acquisition_index"])
        accepted_file[index] = str(record["accepted_file"])
    z_bias_frequency_hz = calibration.slope_hz_per_v * z_axis
    z_bias_absolute_bz_nt = (
        calibration.intercept_hz + z_bias_frequency_hz
    ) / params.bz_gamma_hz_per_nt
    z_control_waveform_mean_frequency_hz = (
        calibration.intercept_hz
        + calibration.slope_hz_per_v
        * (float(np.mean(applied.voltage_v)) + z_axis)
    )
    z_control_waveform_mean_bz_nt = (
        z_control_waveform_mean_frequency_hz / params.bz_gamma_hz_per_nt
    )
    np.savez(
        run_dir.raw / "xyz_balance_scan.npz",
        x_field_v=x_axis,
        y_field_v=y_axis,
        z_bias_v=z_axis,
        z_bias_frequency_hz=z_bias_frequency_hz,
        z_bias_absolute_bz_nt=z_bias_absolute_bz_nt,
        z_control_waveform_mean_bz_nt=z_control_waveform_mean_bz_nt,
        **arrays,
        accepted_attempt_index=accepted_attempt,
        acquisition_order=acquisition_order,
        accepted_file=accepted_file,
        actual_rate_sa_s=np.float64(records[0].get("actual_rate_sa_s", actual_rate)),
        z_control_waveform_mean_v=np.float64(
            float(np.mean(applied.voltage_v))
        ),
        z_control_waveform_min_v=z_axis + np.float64(applied.minimum_v),
        z_control_waveform_max_v=z_axis + np.float64(applied.maximum_v),
        best_z_index=np.int64(best["z_index"]),
        best_x_index=np.int64(best["x_index"]),
        best_y_index=np.int64(best["y_index"]),
        best_z_bias_v=np.float64(best["z_bias_v"]),
        best_x_field_v=np.float64(best["x_field_v"]),
        best_y_field_v=np.float64(best["y_field_v"]),
        best_r_mean_v=np.float64(best["r_mean_v"]),
        best_r_std_v=np.float64(best["r_std_v"]),
        best_combination_remeasured=np.uint8(False),
    )


def _acquire_grid(
    params: MxZOptimalControlDG4000BiasXYZBalanceParams,
    run_dir: Any,
    devices: dict[str, Any],
    channels: dict[str, int],
    controller: _XYZBiasController,
    actual_rate: float,
    device_id: str,
    calibration: Any,
    applied: Any,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    records: list[dict[str, Any]] = []
    started = time.monotonic()
    total = params.x_field_points * params.y_field_points * params.z_bias_points
    for (
        acquisition_index,
        z_index,
        x_index,
        y_index,
        bias_v,
        x_v,
        y_v,
    ) in iter_serpentine_grid(params):
        check_cancelled()
        print(
            f"XYZ 偏置网格 {acquisition_index + 1}/{total}: "
            f"X={x_v:+.6f} V, Y={y_v:+.6f} V, Z bias={bias_v:+.6f} V"
        )
        metadata = {
            "acquisition_index": np.int64(acquisition_index),
            "z_index": np.int64(z_index),
            "x_index": np.int64(x_index),
            "y_index": np.int64(y_index),
            "z_bias_v": np.float64(bias_v),
            "z_bias_frequency_hz": np.float64(
                calibration.slope_hz_per_v * bias_v
            ),
            "z_bias_absolute_bz_nt": np.float64(
                (calibration.intercept_hz + calibration.slope_hz_per_v * bias_v)
                / params.bz_gamma_hz_per_nt
            ),
            "z_control_waveform_mean_bz_nt": np.float64(
                (
                    calibration.intercept_hz
                    + calibration.slope_hz_per_v
                    * (float(np.mean(applied.voltage_v)) + bias_v)
                )
                / params.bz_gamma_hz_per_nt
            ),
            "x_field_v": np.float64(x_v),
            "y_field_v": np.float64(y_v),
            "z_output_offset_v": np.float64(
                applied.offset_v + bias_v
            ),
            "x_output_on": np.uint8(True),
            "y_output_on": np.uint8(True),
            "gs200_used": np.uint8(False),
        }
        summary, attempt, filename = _acquire_valid_rxy_point(
            params,
            run_dir,
            devices,
            channels,
            file_stem=(
                f"grid_Z{z_index:03d}_X{x_index:03d}_Y{y_index:03d}"
            ),
            metadata=metadata,
            set_y_rf=lambda x=x_v, y=y_v, bias=bias_v: controller.apply(
                x,
                y,
                bias,
            ),
            settle_time_s=params.system_settle_s,
            duration_s=params.response_duration_s,
            actual_rate=actual_rate,
            device_id=device_id,
        )
        records.append(
            {
                "acquisition_index": acquisition_index,
                "z_index": z_index,
                "x_index": x_index,
                "y_index": y_index,
                "z_bias_v": bias_v,
                "x_field_v": x_v,
                "y_field_v": y_v,
                **summary,
                "accepted_attempt_index": attempt,
                "accepted_file": filename,
            }
        )
        report_runtime_progress("A04", "XYZ 偏置网格点采集完成",
            100.0 * (acquisition_index + 1) / total,
            data={"acquisition_index": acquisition_index, "total_point_count": total,
                "estimated_remaining_seconds": (time.monotonic() - started) / (acquisition_index + 1)
                    * (total - acquisition_index - 1)})
    best = dict(first_acquired_minimum(records))
    _save_scan_aggregate(
        run_dir,
        params,
        records,
        actual_rate,
        best,
        calibration,
        applied,
    )
    return records, best
