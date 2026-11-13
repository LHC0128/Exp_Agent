"""原科学分析的输入资格、拟合计算和独立结果汇总；不加载外部控制来源。"""
import json
from pathlib import Path
from typing import Any
import numpy as np
import yaml
from . import run_format
from .bloch_fit import GAMMA_HZ_PER_NT, FIT_MODEL, _builtin, select_measured_minimum, _fit_summary_result
from .analysis_plotting import _plot_planes, _plot_components, _plot_bloch_fit, _write_csv

EXPERIMENT_ID = run_format.EXPERIMENT_ID

def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"缺少分析输入: {path}")
    with path.open(encoding="utf-8") as stream:
        payload = yaml.safe_load(stream) or {}
    if not isinstance(payload, dict):
        raise TypeError(f"YAML 顶层必须是映射: {path}")
    return payload


def _load_scan(path: Path) -> dict[str, np.ndarray]:
    if not path.is_file():
        raise FileNotFoundError(f"缺少三维扫描数据: {path}")
    with np.load(path, allow_pickle=False) as data:
        required = {
            "x_field_v",
            "y_field_v",
            "z_bias_v",
            "r_mean_v",
            "r_std_v",
            "x_mean_v",
            "y_mean_v",
            "x_std_v",
            "y_std_v",
            "complex_std_v",
            "acquisition_order",
            "actual_rate_sa_s",
        }
        missing = sorted(required.difference(data.files))
        if missing:
            raise ValueError(f"三维扫描数据缺少字段: {', '.join(missing)}")
        return {key: np.asarray(data[key]) for key in data.files}


def _validate_scan(data: dict[str, np.ndarray]) -> None:
    x_axis = np.asarray(data["x_field_v"], dtype=float)
    y_axis = np.asarray(data["y_field_v"], dtype=float)
    z_axis = np.asarray(data["z_bias_v"], dtype=float)
    expected_shape = (z_axis.size, x_axis.size, y_axis.size)
    if min(expected_shape) < 1:
        raise ValueError("XYZ 扫描轴不能为空")
    for key in (
        "r_mean_v",
        "r_std_v",
        "x_mean_v",
        "y_mean_v",
        "x_std_v",
        "y_std_v",
        "complex_std_v",
        "acquisition_order",
    ):
        if np.asarray(data[key]).shape != expected_shape:
            raise ValueError(
                f"{key} 形状应为 {expected_shape}，实际为 "
                f"{np.asarray(data[key]).shape}"
            )
    for name, axis in (("X", x_axis), ("Y", y_axis), ("Z bias", z_axis)):
        if not np.all(np.isfinite(axis)):
            raise ValueError(f"{name} 扫描轴包含 NaN 或无穷值")
    for key in (
        "r_mean_v",
        "r_std_v",
        "x_mean_v",
        "y_mean_v",
        "x_std_v",
        "y_std_v",
        "complex_std_v",
    ):
        if not np.all(np.isfinite(data[key])):
            raise ValueError(f"{key} 包含 NaN 或无穷值")

    for name in ("x_field_v", "y_field_v", "z_bias_v"):
        axis = np.asarray(data[name], dtype=float)
        if axis.ndim != 1 or (axis.size > 1 and np.any(np.diff(axis) <= 0)):
            raise ValueError(f"{name} 必须是一维严格递增扫描轴")
    if x_axis.size < 2 or y_axis.size < 2:
        raise ValueError("Bloch 平面拟合至少需要两个 X 点和两个 Y 点")
    rate = np.asarray(data["actual_rate_sa_s"])
    if rate.ndim != 0 or not np.isfinite(rate) or float(rate) <= 0:
        raise ValueError("实际采样率必须是有限正标量")
    for key in ("r_std_v", "x_std_v", "y_std_v", "complex_std_v"):
        if np.any(np.asarray(data[key], dtype=float) < 0):
            raise ValueError(f"{key} 不能为负")
    order = np.asarray(data["acquisition_order"], dtype=float)
    if not np.isfinite(order).all() or not np.array_equal(np.sort(order.ravel()), np.arange(order.size)):
        raise ValueError("采集顺序必须是完整且无重复的整数序号")
    for key in ("z_bias_frequency_hz", "z_bias_absolute_bz_nt", "z_control_waveform_mean_bz_nt"):
        if key in data and (np.asarray(data[key]).shape != z_axis.shape or not np.isfinite(data[key]).all()):
            raise ValueError(f"{key} 必须与 Z 偏置轴同形且有限")


def load_inputs(run_dir):
    run_dir = Path(run_dir)
    config = _load_yaml(run_dir / "experiment_config.yaml")
    if reason := run_format.analysis_block_reason(config):
        raise ValueError(reason)
    data = _load_scan(run_dir / "raw/xyz_balance_scan.npz")
    _validate_scan(data)
    calibration = config.get("z_control_calibration", {})
    if not isinstance(calibration, dict):
        raise ValueError("缺少有效的独立 Z 静态标定")
    try:
        gamma = float(calibration.get("gamma_hz_per_nt", GAMMA_HZ_PER_NT))
        slope, intercept = float(calibration["slope_hz_per_v"]), float(calibration["intercept_hz"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("缺少有效的独立 Z 静态标定") from exc
    if not np.isfinite([gamma, slope, intercept]).all() or gamma <= 0 or slope <= 0:
        raise ValueError("Z 标定斜率、截距和换算系数无效")
    return config, data


def evaluate(run_dir: Path) -> dict[str, Any]:
    """汇总三维 R/X/Y 响应并报告稳健复数拟合平衡点。"""
    run_dir = Path(run_dir).resolve()
    results_dir = run_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    config, data = load_inputs(run_dir)
    best_index = select_measured_minimum(data)
    z_index, x_index, y_index = best_index
    x_axis = np.asarray(data["x_field_v"], dtype=float)
    y_axis = np.asarray(data["y_field_v"], dtype=float)
    z_axis = np.asarray(data["z_bias_v"], dtype=float)
    r_mean = np.asarray(data["r_mean_v"], dtype=float)
    r_std = np.asarray(data["r_std_v"], dtype=float)
    order = np.asarray(data["acquisition_order"], dtype=int)
    calibration = config.get("z_control_calibration", {})
    gamma = float(calibration.get("gamma_hz_per_nt", GAMMA_HZ_PER_NT))
    if "z_bias_frequency_hz" not in data:
        slope = float(calibration.get("slope_hz_per_v", 0.0))
        intercept = float(calibration.get("intercept_hz", 0.0))
        data["z_bias_frequency_hz"] = slope * z_axis
    if "z_bias_absolute_bz_nt" not in data:
        intercept = float(calibration.get("intercept_hz", 0.0))
        data["z_bias_absolute_bz_nt"] = (
            intercept + np.asarray(data["z_bias_frequency_hz"], dtype=float)
        ) / gamma
    if "z_control_waveform_mean_bz_nt" not in data:
        data["z_control_waveform_mean_bz_nt"] = data["z_bias_absolute_bz_nt"]

    bloch_fit, plane_results = _fit_summary_result(data, config)
    robust_center = np.asarray(bloch_fit["robust_xy_center_v"], dtype=float)
    z_zero = bloch_fit["z_static_zero_from_calibration"]
    nearest_z_index = int(np.argmin(np.abs(z_axis - float(z_zero["z_bias_v"]))))
    nearest_plane = next(
        item for item in plane_results if int(item["z_index"]) == nearest_z_index
    )
    nearest_xy_index = (
        int(np.argmin(np.abs(x_axis - robust_center[0]))),
        int(np.argmin(np.abs(y_axis - robust_center[1]))),
    )
    fitted_point = {
        "x_field_v": float(robust_center[0]),
        "y_field_v": float(robust_center[1]),
        "z_bias_v": float(z_zero["z_bias_v"]),
        "z_bias_frequency_hz": 0.0,
        "z_bias_absolute_bz_nt": 0.0,
        "nearest_measured_z_bias_v": float(z_axis[nearest_z_index]),
        "nearest_measured_z_index": nearest_z_index,
        "nearest_measured_xy_v": [
            float(x_axis[nearest_xy_index[0]]),
            float(y_axis[nearest_xy_index[1]]),
        ],
        "nearest_measured_xy_r_v": float(
            np.asarray(data["r_mean_v"])[
                nearest_z_index, nearest_xy_index[0], nearest_xy_index[1]
            ]
        ),
        "nearest_fit_plane_r_squared": float(nearest_plane["r_squared_complex"]),
    }
    plot_file = _plot_planes(results_dir, data, best_index, plane_results)
    component_plot_file = _plot_components(results_dir, data, best_index, robust_center)
    fit_plot_file = _plot_bloch_fit(results_dir, data, plane_results)
    csv_file = _write_csv(results_dir, data)
    npz_file = "balance_results.npz"
    np.savez(
        results_dir / npz_file,
        best_z_index=np.int64(z_index),
        best_x_index=np.int64(x_index),
        best_y_index=np.int64(y_index),
        best_z_bias_v=np.float64(z_axis[z_index]),
        best_z_bias_frequency_hz=np.float64(data["z_bias_frequency_hz"][z_index]),
        best_z_bias_absolute_bz_nt=np.float64(data["z_bias_absolute_bz_nt"][z_index]),
        best_z_control_waveform_mean_bz_nt=np.float64(
            data["z_control_waveform_mean_bz_nt"][z_index]
        ),
        best_x_field_v=np.float64(x_axis[x_index]),
        best_y_field_v=np.float64(y_axis[y_index]),
        best_r_mean_v=np.float64(r_mean[best_index]),
        best_r_std_v=np.float64(r_std[best_index]),
        best_x_mean_v=np.float64(data["x_mean_v"][best_index]),
        best_y_mean_v=np.float64(data["y_mean_v"][best_index]),
        best_acquisition_index=np.int64(order[best_index]),
        fitted_x_field_v=np.float64(robust_center[0]),
        fitted_y_field_v=np.float64(robust_center[1]),
        fitted_z_bias_v=np.float64(z_zero["z_bias_v"]),
        fitted_z_bias_absolute_bz_nt=np.float64(0.0),
    )
    fit_npz_file = "bloch_fit_analysis.npz"
    z_fit_values = np.asarray([item["z_bias_v"] for item in plane_results], dtype=float)
    np.savez(
        results_dir / fit_npz_file,
        z_bias_v=z_fit_values,
        z_bias_absolute_bz_nt=np.asarray(
            [item["z_bias_absolute_bz_nt"] for item in plane_results], dtype=float
        ),
        balance_xy_v=np.asarray(
            [item["balance_xy_v"] for item in plane_results], dtype=float
        ),
        model_min_xy_v=np.asarray(
            [item["model_min_xy_v"] for item in plane_results], dtype=float
        ),
        model_min_r_v=np.asarray(
            [item["model_min_r_v"] for item in plane_results], dtype=float
        ),
        model_min_local_3x3_median_r_v=np.asarray(
            [item["model_min_local_3x3_median_r_v"] for item in plane_results],
            dtype=float,
        ),
        r_squared_complex=np.asarray(
            [item["r_squared_complex"] for item in plane_results], dtype=float
        ),
        rmse_complex_v=np.asarray(
            [item["rmse_complex_v"] for item in plane_results], dtype=float
        ),
        fit_parameters=np.asarray(
            [item["parameters"] for item in plane_results], dtype=float
        ),
        robust_xy_center_v=robust_center,
        z_static_zero_v=np.float64(z_zero["z_bias_v"]),
    )
    warnings = [
        "实测网格 R 最小点仅作为诊断保留，主结果来自 Demod X/Y 复数稳健拟合。",
        "当前拟合是稳态 Bloch 型二维响应面，不是周期最优控制的严格 Bloch/Floquet 解。",
        "偏置到 Bz 的换算只使用静态 K_Z 标定，不能替代线圈动态传递函数。",
        "最佳 XYZ 组合未在扫描结束后额外复测。",
    ]
    for name, index, axis in (("X", x_index, x_axis), ("Y", y_index, y_axis)):
        if axis.size > 1 and index in {0, axis.size - 1}:
            warnings.append(f"{name} 最小点位于扫描边界。")
    if np.isfinite(z_zero["z_bias_v"]) and (
        z_zero["z_bias_v"] < z_axis[0] or z_zero["z_bias_v"] > z_axis[-1]
    ):
        warnings.append("静态标定得到的 Z 零场位于当前扫描区间之外。")
    result = {
        "experiment_id": EXPERIMENT_ID,
        "run_dir": str(run_dir),
        "scan_completed": config.get("completion_status") == "completed",
        "scan_shape_zyx": [int(z_axis.size), int(x_axis.size), int(y_axis.size)],
        "scan_order": "Z bias outer, continuous X/Y serpentine",
        "criterion": "robust complex Demod X/Y Bloch fit",
        "measured_grid_minimum": {
            "z_index": z_index,
            "x_index": x_index,
            "y_index": y_index,
            "z_bias_v": float(z_axis[z_index]),
            "z_bias_frequency_hz": float(data["z_bias_frequency_hz"][z_index]),
            "z_bias_absolute_bz_nt": float(data["z_bias_absolute_bz_nt"][z_index]),
            "z_control_waveform_mean_bz_nt": float(
                data["z_control_waveform_mean_bz_nt"][z_index]
            ),
            "x_field_v": float(x_axis[x_index]),
            "y_field_v": float(y_axis[y_index]),
            "r_mean_v": float(r_mean[best_index]),
            "r_std_v": float(r_std[best_index]),
            "x_mean_v": float(data["x_mean_v"][best_index]),
            "y_mean_v": float(data["y_mean_v"][best_index]),
            "acquisition_index": int(order[best_index]),
            "remeasured": False,
        },
        "response_summary": {
            "minimum_r_v": float(np.min(r_mean)),
            "maximum_r_v": float(np.max(r_mean)),
            "mean_r_v": float(np.mean(r_mean)),
            "mean_point_std_v": float(np.mean(r_std)),
            "actual_rate_sa_s": float(data["actual_rate_sa_s"]),
        },
        "fitted_balance_point": fitted_point,
        "bloch_fit": bloch_fit,
        "control_source": config.get("control_source", {}),
        "applied_control": config.get("applied_control", {}),
        "bz_conversion": {
            "gamma_hz_per_nt": gamma,
            "slope_hz_per_v": float(
                config.get("z_control_calibration", {}).get(
                    "slope_hz_per_v", np.nan
                )
            ),
            "intercept_hz": float(
                config.get("z_control_calibration", {}).get(
                    "intercept_hz", np.nan
                )
            ),
        },
        "interpretation": {
            "bloch_fit_available": True,
            "model": FIT_MODEL,
            "model_scope": "Demod X/Y 复数稳态响应；Z 方向采用静态 K_Z 标定零场",
            "periodic_floquet_fit_available": False,
            "absolute_field_reported": True,
        },
        "warnings": warnings,
        "files": [
            npz_file,
            fit_npz_file,
            "bloch_fit_analysis.yaml",
            "bloch_fit_analysis.json",
            csv_file,
            plot_file,
            component_plot_file,
            fit_plot_file,
        ],
    }
    fit_yaml_file = results_dir / "bloch_fit_analysis.yaml"
    fit_json_file = results_dir / "bloch_fit_analysis.json"
    fit_yaml_file.write_text(
        yaml.safe_dump(_builtin(bloch_fit), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    fit_json_file.write_text(
        json.dumps(_builtin(bloch_fit), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (results_dir / "evaluation.json").write_text(
        json.dumps(_builtin(result), ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def write_report(run_dir):
    results = Path(run_dir) / "results"
    result = json.loads((results / "evaluation.json").read_text(encoding="utf-8"))
    (results / "analysis.yaml").write_text(
        yaml.safe_dump(result, allow_unicode=True, sort_keys=False), encoding="utf-8")
    (results / "analysis.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
