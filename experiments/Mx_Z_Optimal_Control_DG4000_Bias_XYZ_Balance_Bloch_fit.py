"""Mx Z 最优控制 XYZ 扫描的复数响应拟合和简化 Bloch 对比。

该脚本直接读取 ``raw/grid_Z*_X*_Y*_attempt_*.npz``，因此支持扫描中断后
对已经完成的完整 Z 平面做离线分析。拟合模型是稳态 Bloch 横向响应的
二维推广：两个 Demod 分量共享平衡中心和线宽，增益矩阵、输出偏置独立。
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import numpy as np
import yaml


FILE_RE = re.compile(r"grid_Z(?P<z>\d+)_X(?P<x>\d+)_Y(?P<y>\d+)_attempt_(?P<a>\d+)")


def _latest_run(root: Path) -> Path:
    runs = sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.name)
    if not runs:
        raise FileNotFoundError(f"没有运行目录: {root}")
    return runs[-1]


def _read_points(run_dir: Path) -> dict[str, Any]:
    raw_dir = run_dir / "raw"
    selected: dict[tuple[int, int, int], tuple[int, Path]] = {}
    for path in raw_dir.glob("grid_Z*_X*_Y*_attempt_*.npz"):
        match = FILE_RE.fullmatch(path.stem)
        if match is None:
            continue
        zyx = tuple(int(match.group(name)) for name in ("z", "x", "y"))
        attempt = int(match.group("a"))
        with np.load(path, allow_pickle=False) as data:
            accepted = int(np.asarray(data["quality_accepted"]).item())
            complex_std = float(np.asarray(data["complex_std_v"]).item())
        score = (0 if accepted else 1, complex_std, attempt)
        old = selected.get(zyx)
        if old is None or score < old[0]:
            selected[zyx] = (score, path)
    if not selected:
        raise FileNotFoundError(f"没有单点数据: {raw_dir}")

    records: list[dict[str, float | int]] = []
    for (z_index, x_index, y_index), (_, path) in sorted(selected.items()):
        with np.load(path, allow_pickle=False) as data:
            records.append(
                {
                    "z_index": z_index,
                    "x_index": x_index,
                    "y_index": y_index,
                    "z_bias_v": float(np.asarray(data["z_bias_v"]).item()),
                    "z_bias_frequency_hz": float(np.asarray(data["z_bias_frequency_hz"]).item()),
                    "z_bias_absolute_bz_nt": float(np.asarray(data["z_bias_absolute_bz_nt"]).item()),
                    "x_field_v": float(np.asarray(data["x_field_v"]).item()),
                    "y_field_v": float(np.asarray(data["y_field_v"]).item()),
                    "r_mean_v": float(np.asarray(data["r_mean_v"]).item()),
                    "r_std_v": float(np.asarray(data["r_std_v"]).item()),
                    "x_mean_v": float(np.asarray(data["x_mean_v"]).item()),
                    "y_mean_v": float(np.asarray(data["y_mean_v"]).item()),
                    "complex_std_v": float(np.asarray(data["complex_std_v"]).item()),
                    "quality_accepted": int(np.asarray(data["quality_accepted"]).item()),
                    "acquisition_index": int(np.asarray(data["acquisition_index"]).item()),
                }
            )

    z_axis = np.asarray(sorted({float(r["z_bias_v"]) for r in records}))
    x_axis = np.asarray(sorted({float(r["x_field_v"]) for r in records}))
    y_axis = np.asarray(sorted({float(r["y_field_v"]) for r in records}))
    shape = (z_axis.size, x_axis.size, y_axis.size)
    arrays = {
        key: np.full(shape, np.nan, dtype=float)
        for key in ("r_mean_v", "r_std_v", "x_mean_v", "y_mean_v", "complex_std_v", "acquisition_index")
    }
    accepted = np.zeros(shape, dtype=bool)
    z_frequency = np.full(z_axis.size, np.nan)
    z_absolute_bz = np.full(z_axis.size, np.nan)
    for row in records:
        iz = int(np.argmin(np.abs(z_axis - float(row["z_bias_v"]))))
        ix = int(np.argmin(np.abs(x_axis - float(row["x_field_v"]))))
        iy = int(np.argmin(np.abs(y_axis - float(row["y_field_v"]))))
        for key in arrays:
            arrays[key][iz, ix, iy] = float(row[key])
        accepted[iz, ix, iy] = bool(row["quality_accepted"])
        z_frequency[iz] = float(row["z_bias_frequency_hz"])
        z_absolute_bz[iz] = float(row["z_bias_absolute_bz_nt"])
    arrays["acquisition_index"] = arrays["acquisition_index"].astype(float)
    return {
        "x_axis": x_axis,
        "y_axis": y_axis,
        "z_axis": z_axis,
        "z_frequency": z_frequency,
        "z_absolute_bz": z_absolute_bz,
        "accepted": accepted,
        **arrays,
    }


def _complex_surface(values: np.ndarray, x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """二维稳态 Bloch 复响应的带旋转增益模型。"""
    ox, oy, x0, y0, wx, wy, gxx, gxy, gyx, gyy = values
    u = (x - x0) / wx
    v = (y - y0) / wy
    denominator = 1.0 + u * u + v * v
    sx = ox + (gxx * u + gxy * v) / denominator
    sy = oy + (gyx * u + gyy * v) / denominator
    return sx, sy


def _fit_plane(x_axis: np.ndarray, y_axis: np.ndarray, sx: np.ndarray, sy: np.ndarray) -> dict[str, Any]:
    from scipy.optimize import least_squares, minimize

    x_grid, y_grid = np.meshgrid(x_axis, y_axis, indexing="ij")
    x_span = float(x_axis[-1] - x_axis[0])
    y_span = float(y_axis[-1] - y_axis[0])
    x_step = float(np.median(np.diff(x_axis)))
    y_step = float(np.median(np.diff(y_axis)))
    response = np.hypot(sx, sy)
    min_index = np.unravel_index(int(np.nanargmin(response)), response.shape)
    component_span = max(
        float(np.nanmax(sx) - np.nanmin(sx)),
        float(np.nanmax(sy) - np.nanmin(sy)),
        1e-6,
    )
    lower = np.asarray([
        np.nanmin(sx) - component_span,
        np.nanmin(sy) - component_span,
        x_axis[0], y_axis[0], max(0.5 * x_step, 1e-5), max(0.5 * y_step, 1e-5),
        -20 * component_span, -20 * component_span, -20 * component_span, -20 * component_span,
    ])
    upper = np.asarray([
        np.nanmax(sx) + component_span,
        np.nanmax(sy) + component_span,
        x_axis[-1], y_axis[-1], 2 * x_span, 2 * y_span,
        20 * component_span, 20 * component_span, 20 * component_span, 20 * component_span,
    ])
    initial_centers = [
        (float(x_axis[min_index[0]]), float(y_axis[min_index[1]])),
        (float(np.mean(x_axis)), float(np.mean(y_axis))),
        (float(np.clip(-0.004, x_axis[0], x_axis[-1])), float(np.clip(-0.0005, y_axis[0], y_axis[-1]))),
    ]
    best = None
    scale = max(0.05 * component_span, 1e-6)
    for x0, y0 in initial_centers:
        initial = np.asarray([
            float(np.nanmedian(sx)), float(np.nanmedian(sy)), x0, y0,
            max(x_span / 3, x_step), max(y_span / 3, y_step),
            component_span, 0.0, 0.0, component_span,
        ])
        fit = least_squares(
            lambda p: np.concatenate((_complex_surface(p, x_grid, y_grid)[0] - sx,
                                      _complex_surface(p, x_grid, y_grid)[1] - sy)).reshape(-1),
            initial, bounds=(lower, upper), loss="soft_l1", f_scale=scale, x_scale="jac", max_nfev=8000,
        )
        if best is None or fit.cost < best.cost:
            best = fit
    if best is None:
        raise RuntimeError("复数响应拟合失败")
    p = np.asarray(best.x, dtype=float)
    fit_sx, fit_sy = _complex_surface(p, x_grid, y_grid)
    residual = np.hypot(fit_sx - sx, fit_sy - sy)
    ss_res = float(np.sum((fit_sx - sx) ** 2 + (fit_sy - sy) ** 2))
    ss_tot = float(np.sum((sx - np.mean(sx)) ** 2 + (sy - np.mean(sy)) ** 2))
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0 else np.nan

    def objective(q: np.ndarray) -> float:
        model_sx, model_sy = _complex_surface(p, q[0], q[1])
        return float(model_sx * model_sx + model_sy * model_sy)

    minimum = minimize(
        objective,
        np.asarray([p[2], p[3]]),
        method="Nelder-Mead",
        options={"xatol": 1e-10, "fatol": 1e-14, "maxiter": 2000},
    )
    model_min_xy = np.asarray(minimum.x, dtype=float)
    model_min_xy[0] = np.clip(model_min_xy[0], x_axis[0], x_axis[-1])
    model_min_xy[1] = np.clip(model_min_xy[1], y_axis[0], y_axis[-1])
    model_min_sx, model_min_sy = _complex_surface(p, model_min_xy[0], model_min_xy[1])
    model_min_index = (
        int(np.argmin(np.abs(x_axis - model_min_xy[0]))),
        int(np.argmin(np.abs(y_axis - model_min_xy[1]))),
    )
    ix0, iy0 = model_min_index
    local_r = response[max(0, ix0 - 1):ix0 + 2, max(0, iy0 - 1):iy0 + 2]
    return {
        "parameters": p,
        "balance_xy_v": [float(p[2]), float(p[3])],
        "model_min_xy_v": [float(model_min_xy[0]), float(model_min_xy[1])],
        "model_min_r_v": float(np.hypot(model_min_sx, model_min_sy)),
        "model_min_nearest_grid_xy_v": [float(x_axis[ix0]), float(y_axis[iy0])],
        "model_min_nearest_grid_r_v": float(response[model_min_index]),
        "model_min_local_3x3_median_r_v": float(np.nanmedian(local_r)),
        "nearest_grid_xy_v": [float(x_axis[min_index[0]]), float(y_axis[min_index[1]])],
        "measured_min_r_v": float(response[min_index]),
        "measured_min_indices": [int(min_index[0]), int(min_index[1])],
        "rmse_complex_v": float(np.sqrt(np.mean(residual ** 2))),
        "r_squared_complex": float(r_squared),
        "fit_success": bool(best.success),
        "fit_message": str(best.message),
        "residual_matrix_v": residual,
    }


def _save_plot(path: Path, data: dict[str, Any], plane_results: list[dict[str, Any]]) -> None:
    import matplotlib.pyplot as plt
    from lab_workflows.plotting import new_figure, save_figure, set_plot_style
    set_plot_style("paper")

    z_axis = data["z_axis"]
    fig, axes = new_figure(nrows=2, ncols=2, kind="wide", height_mm=110, constrained_layout=True)
    centers = np.asarray([item["balance_xy_v"] for item in plane_results])
    minima = np.asarray([item["model_min_local_3x3_median_r_v"] for item in plane_results])
    axes[0, 0].plot(z_axis[: len(centers)], centers[:, 0] * 1e3, "o-", label="Fitted X center")
    axes[0, 0].plot(z_axis[: len(centers)], centers[:, 1] * 1e3, "s-", label="Fitted Y center")
    axes[0, 0].set(xlabel="Z bias (V)", ylabel="XY center (mV)")
    axes[0, 0].legend(fontsize=8)
    axes[0, 1].plot(z_axis[: len(minima)], minima * 1e3, "o-")
    axes[0, 1].set(xlabel="Z bias (V)", ylabel="Robust local R (mV)")
    for panel_index, item in enumerate((plane_results[0], plane_results[-1])):
        iz = int(item["z_index"])
        residual = item["residual_matrix_v"]
        im = axes[1, panel_index].imshow(residual.T * 1e3, origin="lower", aspect="auto",
                                     extent=[data["x_axis"][0], data["x_axis"][-1], data["y_axis"][0], data["y_axis"][-1]])
        axes[1, panel_index].set_title(f"Z={z_axis[iz]:+.3f} V residual")
        axes[1, panel_index].set(xlabel="X field (V)", ylabel="Y field (V)")
        fig.colorbar(im, ax=axes[1, panel_index], label="Complex residual (mV)")
    save_figure(fig, path, close=False)
    plt.close(fig)


def analyze(run_dir: Path, output_dir: Path | None = None) -> dict[str, Any]:
    data = _read_points(run_dir)
    config_path = run_dir / "experiment_config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) if config_path.is_file() else {}
    complete = np.all(np.isfinite(data["r_mean_v"]), axis=(1, 2))
    complete_indices = np.flatnonzero(complete)
    if complete_indices.size == 0:
        raise RuntimeError("没有完整 Z 平面")
    plane_results: list[dict[str, Any]] = []
    for iz in complete_indices:
        result = _fit_plane(data["x_axis"], data["y_axis"], data["x_mean_v"][iz], data["y_mean_v"][iz])
        result["z_index"] = int(iz)
        result["z_bias_v"] = float(data["z_axis"][iz])
        result["z_bias_frequency_hz"] = float(data["z_frequency"][iz])
        result["z_bias_absolute_bz_nt"] = float(data["z_absolute_bz"][iz])
        plane_results.append(result)
    centers = np.asarray([item["balance_xy_v"] for item in plane_results])
    robust_center = np.median(centers, axis=0)
    z_values = np.asarray([item["z_bias_v"] for item in plane_results])
    r_minima = np.asarray([item["model_min_local_3x3_median_r_v"] for item in plane_results])
    z_fit = np.polyfit(z_values, r_minima, deg=min(2, z_values.size - 1))
    z_dense = np.linspace(z_values.min(), z_values.max(), 2001)
    z_curve = np.polyval(z_fit, z_dense)
    z_opt = float(z_dense[int(np.argmin(z_curve))])
    calibration = config.get("z_control_calibration", {}) if isinstance(config, dict) else {}
    calibration_slope = float(calibration.get("slope_hz_per_v", np.nan))
    calibration_intercept = float(calibration.get("intercept_hz", np.nan))
    if not np.isfinite(calibration_slope) or not np.isfinite(calibration_intercept):
        calibration_slope = float(np.asarray(data["z_frequency"])[-1] - np.asarray(data["z_frequency"])[0]) / float(data["z_axis"][-1] - data["z_axis"][0])
        calibration_intercept = float(np.nanmedian(data["z_frequency"] - calibration_slope * data["z_axis"]))
    z_zero_v = -calibration_intercept / calibration_slope
    results_dir = output_dir if output_dir is not None else run_dir / "results"
    results_dir.mkdir(exist_ok=True)
    serializable = {
        "run_dir": str(run_dir),
        "scan_shape_available_zyx": [int(data["z_axis"].size), int(data["x_axis"].size), int(data["y_axis"].size)],
        "complete_z_indices": [int(i) for i in complete_indices],
        "incomplete_z_indices": [int(i) for i in np.flatnonzero(~complete)],
        "complete_plane_count": int(complete_indices.size),
        "selected_points": int(np.count_nonzero(np.isfinite(data["r_mean_v"]))),
        "robust_xy_center_v": [float(v) for v in robust_center],
        "z_minimum_quadratic_fit": {
            "z_bias_v": z_opt,
            "z_bias_absolute_bz_nt_interpolated": float(np.interp(z_opt, data["z_axis"], data["z_absolute_bz"])),
            "minimum_r_v_interpolated": float(np.min(z_curve)),
            "fit_degree": int(min(2, z_values.size - 1)),
            "identifiable_from_current_scan": bool(
                data["z_axis"].min() < z_zero_v < data["z_axis"].max()
                and z_values.min() < z_opt < z_values.max()
                and abs(z_opt - z_values.min()) > 1.1 * abs(z_values[1] - z_values[0])
                and abs(z_values.max() - z_opt) > 1.1 * abs(z_values[1] - z_values[0])
            ),
            "note": "二次趋势最小值位于已完成 Z 区间边界，只能作经验趋势参考，不能替代静态标定零场点。",
        },
        "z_static_zero_from_calibration": {
            "z_bias_v": float(z_zero_v),
            "frequency_hz": 0.0,
            "absolute_bz_nt": 0.0,
            "slope_hz_per_v": calibration_slope,
            "intercept_hz": calibration_intercept,
        },
        "model": "complex steady-state Bloch surface with shared XY balance center",
        "planes": [],
    }
    for item in plane_results:
        entry = {key: value for key, value in item.items() if key != "residual_matrix_v" and key != "parameters"}
        entry["parameters"] = [float(v) for v in item["parameters"]]
        serializable["planes"].append(entry)
    (results_dir / "bloch_fit_analysis.yaml").write_text(yaml.safe_dump(serializable, allow_unicode=True, sort_keys=False), encoding="utf-8")
    (results_dir / "bloch_fit_analysis.json").write_text(json.dumps(serializable, ensure_ascii=False, indent=2), encoding="utf-8")
    _save_plot(results_dir / "bloch_fit_summary.png", data, plane_results)
    np.savez(
        results_dir / "bloch_fit_analysis.npz",
        z_bias_v=z_values,
        z_bias_absolute_bz_nt=np.asarray([item["z_bias_absolute_bz_nt"] for item in plane_results]),
        balance_xy_v=centers,
        measured_min_r_v=r_minima,
        robust_xy_center_v=robust_center,
        z_quadratic_opt_v=np.float64(z_opt),
    )
    return serializable


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", nargs="?", type=Path)
    parser.add_argument("--root", type=Path, default=Path("data/Mx_Z_Optimal_Control_DG4000_Bias_XYZ_Balance"))
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    run_dir = args.run_dir.resolve() if args.run_dir else _latest_run(args.root.resolve())
    output_dir = args.output_dir.resolve() if args.output_dir else None
    result = analyze(run_dir, output_dir=output_dir)
    print(yaml.safe_dump(result, allow_unicode=True, sort_keys=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
