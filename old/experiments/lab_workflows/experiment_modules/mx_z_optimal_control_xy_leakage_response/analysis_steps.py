"""仅从明确目录读取聚合网格；保留原最小值和最早采集规则。"""
from pathlib import Path
import json
import numpy as np
import yaml
from ..mx_z_optimal_control_xy_rf_sensitivity.analysis_steps import plain as _builtin
from . import run_format
from .analysis_plotting import _plot_response


def _load_yaml(path):
    if not path.is_file():
        raise FileNotFoundError(f"缺少分析输入: {path}")
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"YAML 顶层必须是映射: {path}")
    return value


def _load_scan(path):
    if not path.is_file():
        raise FileNotFoundError(f"缺少二维扫描数据: {path}")
    with np.load(path, allow_pickle=False) as data:
        required = {"x_signed_amplitude_vpp", "y_signed_amplitude_vpp", "r_mean_v", "r_std_v",
            "acquisition_order", "actual_rate_sa_s"}
        if missing := required - set(data.files):
            raise ValueError("二维扫描数据缺少字段: " + ", ".join(sorted(missing)))
        return {key: np.asarray(data[key]) for key in data.files}


def _validate_scan(data):
    axes = [np.asarray(data[key], dtype=float) for key in ("x_signed_amplitude_vpp", "y_signed_amplitude_vpp")]
    if any(axis.ndim != 1 or axis.size == 0 or not np.all(np.isfinite(axis)) or not np.all(np.diff(axis) > 0) for axis in axes):
        raise ValueError("X/Y 扫描轴必须为非空、有限、严格递增的一维坐标")
    shape = tuple(axis.size for axis in axes)
    for key in ("r_mean_v", "r_std_v", "acquisition_order"):
        if data[key].shape != shape:
            raise ValueError(f"{key} 形状应为 {shape}，实际为 {data[key].shape}")
        if not np.all(np.isfinite(data[key])):
            raise ValueError(f"{key} 包含 NaN 或无穷值")
    if np.any(data["r_std_v"] < 0):
        raise ValueError("R 标准差不能为负数")
    order = data["acquisition_order"]
    if not np.array_equal(np.sort(order.reshape(-1)), np.arange(order.size)):
        raise ValueError("采集序号必须完整、唯一且从零递增")
    rate = np.asarray(data["actual_rate_sa_s"])
    if rate.shape != () or not np.isfinite(rate.item()) or float(rate) <= 0:
        raise ValueError("实际采样率必须为有限正标量")


def load_inputs(run_dir):
    config = _load_yaml(run_dir / "experiment_config.yaml")
    if reason := run_format.analysis_block_reason(config):
        raise ValueError(reason)
    data = _load_scan(run_dir / "raw/xy_leakage_scan.npz")
    _validate_scan(data)
    return config, data


def _select_measured_minimum(data):
    r_mean, order = np.asarray(data["r_mean_v"], dtype=float), np.asarray(data["acquisition_order"], dtype=int)
    candidates = np.argwhere(r_mean == float(np.min(r_mean)))
    return tuple(int(value) for value in min(candidates, key=lambda index: int(order[tuple(index)])))


def evaluate(run_dir):
    config, data = load_inputs(run_dir)
    results = run_dir / "results"
    results.mkdir(parents=True, exist_ok=True)
    best = _select_measured_minimum(data)
    x_axis, y_axis = data["x_signed_amplitude_vpp"], data["y_signed_amplitude_vpp"]
    r_mean, r_std, order = data["r_mean_v"], data["r_std_v"], data["acquisition_order"]
    plot_file = _plot_response(results, data, best)
    result = dict(experiment_id=run_format.EXPERIMENT_ID, run_dir=str(run_dir),
        scan_completed=config.get("completion_status") == "completed", scan_shape=[int(x_axis.size), int(y_axis.size)],
        scan_order="X outer, Y serpentine",
        measured_grid_minimum={"x_index": best[0], "y_index": best[1], "x_signed_amplitude_vpp": float(x_axis[best[0]]),
            "y_signed_amplitude_vpp": float(y_axis[best[1]]), "r_mean_v": float(r_mean[best]), "r_std_v": float(r_std[best]),
            "acquisition_index": int(order[best]), "combined_state_remeasured": False},
        response_summary={"minimum_r_v": float(np.min(r_mean)), "maximum_r_v": float(np.max(r_mean)),
            "mean_r_v": float(np.mean(r_mean)), "mean_point_std_v": float(np.mean(r_std)),
            "actual_rate_sa_s": float(data["actual_rate_sa_s"])},
        control_source=config.get("control_source", {}), z_calibration=config.get("z_calibration", {}),
        applied_control=config.get("applied_control", {}), trigger=config.get("trigger", {}),
        interpretation={"continuous_fit_performed": False, "xy_field_calibration_performed": False, "absolute_leakage_field_reported": False},
        warnings=["最佳 X/Y 组合未在扫描结束后额外复测 R。", "结果仅表示控制电压网格上的 R 响应，不是 X/Y 泄露磁场绝对标定。"],
        files=[plot_file])
    (results / "evaluation.json").write_text(json.dumps(_builtin(result), ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    return result


def write_report(run_dir):
    results = run_dir / "results"
    result = json.loads((results / "evaluation.json").read_text(encoding="utf-8"))
    (results / "analysis.yaml").write_text(yaml.safe_dump(result, allow_unicode=True, sort_keys=False), encoding="utf-8")
    (results / "analysis.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    return result
