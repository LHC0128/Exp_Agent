"""沿用原始 soft-L1 多初值复数 Bloch 平面拟合；不替代周期 Floquet 模型。"""
from typing import Any
import numpy as np
from scipy.optimize import least_squares, minimize

GAMMA_HZ_PER_NT = 7.0
FIT_MODEL = "complex steady-state Bloch surface with shared XY balance center"

def _builtin(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _builtin(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_builtin(item) for item in value]
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    return value

def select_measured_minimum(
    data: dict[str, np.ndarray],
) -> tuple[int, int, int]:
    """按 R 最小、采集顺序最先选择规范 Z/X/Y 索引。"""
    r_mean = np.asarray(data["r_mean_v"], dtype=float)
    order = np.asarray(data["acquisition_order"], dtype=int)
    minimum = float(np.min(r_mean))
    candidates = np.argwhere(r_mean == minimum)
    return tuple(
        int(value)
        for value in min(
            candidates,
            key=lambda index: int(order[tuple(index)]),
        )
    )


def _complex_surface(
    parameters: np.ndarray,
    x: np.ndarray | float,
    y: np.ndarray | float,
) -> tuple[np.ndarray | float, np.ndarray | float]:
    """二维稳态 Bloch 型复响应面。

    参数依次为输出偏置、XY 平衡中心、两个方向的线宽和 2x2 增益矩阵。
    该模型用于拟合每个 Z 平面的稳态复响应，不把周期最优控制近似成严格的
    Floquet 解。
    """
    ox, oy, x0, y0, wx, wy, gxx, gxy, gyx, gyy = parameters
    u = (np.asarray(x) - x0) / wx
    v = (np.asarray(y) - y0) / wy
    denominator = 1.0 + u * u + v * v
    sx = ox + (gxx * u + gxy * v) / denominator
    sy = oy + (gyx * u + gyy * v) / denominator
    return sx, sy


def _fit_bloch_plane(
    x_axis: np.ndarray,
    y_axis: np.ndarray,
    x_response: np.ndarray,
    y_response: np.ndarray,
) -> dict[str, Any]:
    """对一个 Z 平面的 Demod X/Y 网格进行稳健复数拟合。"""
    x_axis = np.asarray(x_axis, dtype=float)
    y_axis = np.asarray(y_axis, dtype=float)
    sx = np.asarray(x_response, dtype=float)
    sy = np.asarray(y_response, dtype=float)
    expected_shape = (x_axis.size, y_axis.size)
    if sx.shape != expected_shape or sy.shape != expected_shape:
        raise ValueError(
            f"复数响应形状应为 {expected_shape}，实际为 {sx.shape}/{sy.shape}"
        )
    if x_axis.size < 2 or y_axis.size < 2:
        raise ValueError("Bloch 平面拟合至少需要两个 X 点和两个 Y 点")
    if not np.all(np.isfinite(sx)) or not np.all(np.isfinite(sy)):
        raise ValueError("Bloch 平面拟合输入包含 NaN 或无穷值")

    x_grid, y_grid = np.meshgrid(x_axis, y_axis, indexing="ij")
    response = np.hypot(sx, sy)
    min_index = np.unravel_index(int(np.argmin(response)), response.shape)
    x_span = max(float(x_axis[-1] - x_axis[0]), 1e-9)
    y_span = max(float(y_axis[-1] - y_axis[0]), 1e-9)
    x_step = max(float(np.median(np.diff(x_axis))), 1e-9)
    y_step = max(float(np.median(np.diff(y_axis))), 1e-9)
    component_span = max(
        float(max(np.ptp(sx), np.ptp(sy))),
        1e-9,
    )
    lower = np.asarray(
        [
            np.min(sx) - component_span,
            np.min(sy) - component_span,
            x_axis[0],
            y_axis[0],
            0.5 * x_step,
            0.5 * y_step,
            *([-20.0 * component_span] * 4),
        ],
        dtype=float,
    )
    upper = np.asarray(
        [
            np.max(sx) + component_span,
            np.max(sy) + component_span,
            x_axis[-1],
            y_axis[-1],
            2.0 * x_span,
            2.0 * y_span,
            *([20.0 * component_span] * 4),
        ],
        dtype=float,
    )
    initial_centers = (
        (float(x_axis[min_index[0]]), float(y_axis[min_index[1]])),
        (float(np.mean(x_axis)), float(np.mean(y_axis))),
        (
            float(np.clip(-0.004, x_axis[0], x_axis[-1])),
            float(np.clip(-0.0005, y_axis[0], y_axis[-1])),
        ),
    )
    scale = max(0.05 * component_span, 1e-9)
    best = None
    for x0, y0 in initial_centers:
        initial = np.asarray(
            [
                float(np.median(sx)),
                float(np.median(sy)),
                x0,
                y0,
                max(x_span / 3.0, x_step),
                max(y_span / 3.0, y_step),
                component_span,
                0.0,
                0.0,
                component_span,
            ],
            dtype=float,
        )

        def residual(parameters: np.ndarray) -> np.ndarray:
            fit_sx, fit_sy = _complex_surface(parameters, x_grid, y_grid)
            return np.concatenate(
                ((np.asarray(fit_sx) - sx).ravel(), (np.asarray(fit_sy) - sy).ravel())
            )

        fit = least_squares(
            residual,
            initial,
            bounds=(lower, upper),
            loss="soft_l1",
            f_scale=scale,
            x_scale="jac",
            max_nfev=8000,
        )
        if best is None or fit.cost < best.cost:
            best = fit
    if best is None:
        raise RuntimeError("复数响应拟合失败")

    parameters = np.asarray(best.x, dtype=float)
    fit_sx, fit_sy = _complex_surface(parameters, x_grid, y_grid)
    residual_matrix = np.hypot(np.asarray(fit_sx) - sx, np.asarray(fit_sy) - sy)
    ss_res = float(np.sum((np.asarray(fit_sx) - sx) ** 2 + (np.asarray(fit_sy) - sy) ** 2))
    ss_tot = float(np.sum((sx - np.mean(sx)) ** 2 + (sy - np.mean(sy)) ** 2))
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0.0 else np.nan

    def objective(point: np.ndarray) -> float:
        model_sx, model_sy = _complex_surface(parameters, point[0], point[1])
        return float(model_sx * model_sx + model_sy * model_sy)

    model_minimum = minimize(
        objective,
        np.asarray([parameters[2], parameters[3]], dtype=float),
        method="Nelder-Mead",
        options={"xatol": 1e-10, "fatol": 1e-14, "maxiter": 2000},
    )
    model_min_xy = np.asarray(model_minimum.x, dtype=float)
    model_min_xy[0] = np.clip(model_min_xy[0], x_axis[0], x_axis[-1])
    model_min_xy[1] = np.clip(model_min_xy[1], y_axis[0], y_axis[-1])
    model_min_sx, model_min_sy = _complex_surface(
        parameters, model_min_xy[0], model_min_xy[1]
    )
    model_min_index = (
        int(np.argmin(np.abs(x_axis - model_min_xy[0]))),
        int(np.argmin(np.abs(y_axis - model_min_xy[1]))),
    )
    ix0, iy0 = model_min_index
    local_r = response[max(0, ix0 - 1) : ix0 + 2, max(0, iy0 - 1) : iy0 + 2]
    return {
        "parameters": parameters,
        "balance_xy_v": [float(parameters[2]), float(parameters[3])],
        "model_min_xy_v": [float(model_min_xy[0]), float(model_min_xy[1])],
        "model_min_r_v": float(np.hypot(model_min_sx, model_min_sy)),
        "model_min_nearest_grid_xy_v": [
            float(x_axis[ix0]),
            float(y_axis[iy0]),
        ],
        "model_min_nearest_grid_r_v": float(response[model_min_index]),
        "model_min_local_3x3_median_r_v": float(np.median(local_r)),
        "nearest_grid_xy_v": [
            float(x_axis[min_index[0]]),
            float(y_axis[min_index[1]]),
        ],
        "measured_min_r_v": float(response[min_index]),
        "measured_min_indices": [int(min_index[0]), int(min_index[1])],
        "rmse_complex_v": float(np.sqrt(np.mean(residual_matrix**2))),
        "r_squared_complex": float(r_squared),
        "fit_success": bool(best.success),
        "fit_message": str(best.message),
        "residual_matrix_v": residual_matrix,
    }


def _fit_summary_result(
    data: dict[str, np.ndarray],
    config: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """拟合所有完整 Z 平面，并确定 XY 中心和静态 Z 零场。"""
    complete = np.all(np.isfinite(data["r_mean_v"]), axis=(1, 2))
    complete_indices = np.flatnonzero(complete)
    if complete_indices.size == 0:
        raise RuntimeError("没有完整 Z 平面可用于拟合")
    plane_results: list[dict[str, Any]] = []
    x_axis = np.asarray(data["x_field_v"], dtype=float)
    y_axis = np.asarray(data["y_field_v"], dtype=float)
    for z_index in complete_indices:
        fitted = _fit_bloch_plane(
            x_axis,
            y_axis,
            np.asarray(data["x_mean_v"])[z_index],
            np.asarray(data["y_mean_v"])[z_index],
        )
        fitted["z_index"] = int(z_index)
        fitted["z_bias_v"] = float(np.asarray(data["z_bias_v"])[z_index])
        fitted["z_bias_frequency_hz"] = float(
            np.asarray(data["z_bias_frequency_hz"])[z_index]
        )
        fitted["z_bias_absolute_bz_nt"] = float(
            np.asarray(data["z_bias_absolute_bz_nt"])[z_index]
        )
        plane_results.append(fitted)

    centers = np.asarray([item["balance_xy_v"] for item in plane_results], dtype=float)
    robust_center = np.median(centers, axis=0)
    calibration = config.get("z_control_calibration", {})
    slope = float(calibration.get("slope_hz_per_v", np.nan))
    intercept = float(calibration.get("intercept_hz", np.nan))
    z_axis = np.asarray(data["z_bias_v"], dtype=float)
    frequencies = np.asarray(data["z_bias_frequency_hz"], dtype=float)
    if not np.isfinite(slope) or not np.isfinite(intercept):
        if z_axis.size < 2 or not np.all(np.isfinite(frequencies)):
            slope = np.nan
            intercept = np.nan
        else:
            slope = float((frequencies[-1] - frequencies[0]) / (z_axis[-1] - z_axis[0]))
            intercept = float(np.nanmedian(frequencies - slope * z_axis))
    z_zero_v = float(-intercept / slope) if np.isfinite(slope) and slope != 0 else np.nan
    nearest_z_index = (
        int(np.argmin(np.abs(z_axis - z_zero_v))) if np.isfinite(z_zero_v) else None
    )
    fit_summary = {
        "model": FIT_MODEL,
        "plane_count": int(len(plane_results)),
        "complete_z_indices": [int(index) for index in complete_indices],
        "incomplete_z_indices": [
            int(index) for index in np.flatnonzero(~complete)
        ],
        "robust_xy_center_v": [float(value) for value in robust_center],
        "center_std_v": [float(value) for value in np.std(centers, axis=0)],
        "center_range_v": [
            float(value) for value in (np.max(centers, axis=0) - np.min(centers, axis=0))
        ],
        "median_r_squared": float(np.nanmedian([item["r_squared_complex"] for item in plane_results])),
        "min_r_squared": float(np.nanmin([item["r_squared_complex"] for item in plane_results])),
        "max_r_squared": float(np.nanmax([item["r_squared_complex"] for item in plane_results])),
        "median_complex_rmse_v": float(np.nanmedian([item["rmse_complex_v"] for item in plane_results])),
        "min_complex_rmse_v": float(np.nanmin([item["rmse_complex_v"] for item in plane_results])),
        "max_complex_rmse_v": float(np.nanmax([item["rmse_complex_v"] for item in plane_results])),
        "z_static_zero_from_calibration": {
            "z_bias_v": z_zero_v,
            "frequency_hz": 0.0,
            "absolute_bz_nt": 0.0,
            "slope_hz_per_v": slope,
            "intercept_hz": intercept,
            "nearest_measured_z_bias_v": (
                float(z_axis[nearest_z_index]) if nearest_z_index is not None else None
            ),
        },
        "planes": [],
    }
    for item in plane_results:
        fit_summary["planes"].append(
            {
                key: _builtin(value)
                for key, value in item.items()
                if key not in {"residual_matrix_v"}
            }
        )
    return fit_summary, plane_results
