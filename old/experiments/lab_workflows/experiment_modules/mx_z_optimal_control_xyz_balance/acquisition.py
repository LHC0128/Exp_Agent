"""蛇形 XYZ 网格采集、R 质量重试与聚合保存。"""

from typing import Any

import numpy as np

from ...experiment_runtime import check_cancelled, report_runtime_progress
from ..mx_y_rf_sensitivity.workflow import _acquire_valid_r_point
from .excitation import _XYZFieldController
from .models import MxZOptimalControlXYZBalanceParams
from .scan import build_xyz_axes, first_acquired_minimum, iter_serpentine_grid


def _save_scan_aggregate(
    run_dir: Any,
    params: MxZOptimalControlXYZBalanceParams,
    records: list[dict[str, Any]],
    actual_rate: float,
    best: dict[str, Any],
) -> None:
    x_axis, y_axis, z_axis = build_xyz_axes(params)
    shape = (z_axis.size, x_axis.size, y_axis.size)
    r_mean = np.full(shape, np.nan, dtype=float)
    r_scalar_mean = np.full(shape, np.nan, dtype=float)
    r_std = np.full(shape, np.nan, dtype=float)
    accepted_attempt = np.full(shape, -1, dtype=int)
    acquisition_order = np.full(shape, -1, dtype=int)
    accepted_file = np.full(shape, "", dtype="<U160")
    for record in records:
        index = (
            int(record["z_index"]),
            int(record["x_index"]),
            int(record["y_index"]),
        )
        r_mean[index] = float(record["r_mean_v"])
        r_scalar_mean[index] = float(record["r_scalar_mean_v"])
        r_std[index] = float(record["r_std_v"])
        accepted_attempt[index] = int(record["accepted_attempt_index"])
        acquisition_order[index] = int(record["acquisition_index"])
        accepted_file[index] = str(record["accepted_file"])
    np.savez(
        run_dir.raw / "xyz_balance_scan.npz",
        x_field_v=x_axis,
        y_field_v=y_axis,
        z_field_ma=z_axis,
        r_mean_v=r_mean,
        r_scalar_mean=r_scalar_mean,
        r_std_v=r_std,
        accepted_attempt_index=accepted_attempt,
        acquisition_order=acquisition_order,
        accepted_file=accepted_file,
        actual_rate_sa_s=np.float64(records[0].get("actual_rate_sa_s", actual_rate)),
        best_z_index=np.int64(best["z_index"]),
        best_x_index=np.int64(best["x_index"]),
        best_y_index=np.int64(best["y_index"]),
        best_z_field_ma=np.float64(best["z_field_ma"]),
        best_x_field_v=np.float64(best["x_field_v"]),
        best_y_field_v=np.float64(best["y_field_v"]),
        best_r_mean_v=np.float64(best["r_mean_v"]),
        best_r_std_v=np.float64(best["r_std_v"]),
        best_combination_remeasured=np.uint8(False),
    )


def _acquire_grid(
    params: MxZOptimalControlXYZBalanceParams,
    run_dir: Any,
    devices: dict[str, Any],
    channels: dict[str, int],
    controller: _XYZFieldController,
    actual_rate: float,
    device_id: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    records: list[dict[str, Any]] = []
    total = params.x_field_points * params.y_field_points * params.z_field_points
    for (
        acquisition_index,
        z_index,
        x_index,
        y_index,
        z_ma,
        x_v,
        y_v,
    ) in iter_serpentine_grid(params):
        check_cancelled()
        print(
            f"XYZ 网格 {acquisition_index + 1}/{total}: "
            f"X={x_v:+.6f} V, Y={y_v:+.6f} V, Z={z_ma:+.6f} mA"
        )
        metadata = {
            "acquisition_index": np.int64(acquisition_index),
            "z_index": np.int64(z_index),
            "x_index": np.int64(x_index),
            "y_index": np.int64(y_index),
            "z_field_ma": np.float64(z_ma),
            "x_field_v": np.float64(x_v),
            "y_field_v": np.float64(y_v),
            "x_output_on": np.uint8(True),
            "y_output_on": np.uint8(True),
            "gs200_output_on": np.uint8(True),
        }
        summary, attempt, filename = _acquire_valid_r_point(
            params,
            run_dir,
            devices,
            channels,
            file_stem=(
                f"grid_Z{z_index:03d}_X{x_index:03d}_Y{y_index:03d}"
            ),
            metadata=metadata,
            set_y_rf=lambda x=x_v, y=y_v, z=z_ma: controller.apply(x, y, z),
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
                "z_field_ma": z_ma,
                "x_field_v": x_v,
                "y_field_v": y_v,
                **summary,
                "accepted_attempt_index": attempt,
                "accepted_file": filename,
            }
        )
        report_runtime_progress("A04", "XYZ 网格点采集完成",
            100.0 * (acquisition_index + 1) / total,
            data={"acquisition_index": acquisition_index, "total_point_count": total})
    best = dict(first_acquired_minimum(records))
    _save_scan_aggregate(run_dir, params, records, actual_rate, best)
    return records, best
