"""Mx Z 最优控制 XY 泄露响应离线分析。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from ...experiment_runtime import runtime_run_dir
from ...plotting import format_axis, new_figure, save_figure, set_plot_style


EXPERIMENT_ID = "mx-z-optimal-control-xy-leakage-response"


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
        raise FileNotFoundError(f"缺少二维扫描数据: {path}")
    with np.load(path, allow_pickle=False) as data:
        required = {
            "x_signed_amplitude_vpp",
            "y_signed_amplitude_vpp",
            "r_mean_v",
            "r_std_v",
            "acquisition_order",
            "actual_rate_sa_s",
        }
        missing = sorted(required.difference(data.files))
        if missing:
            raise ValueError(f"二维扫描数据缺少字段: {', '.join(missing)}")
        return {key: np.asarray(data[key]) for key in data.files}


def _validate_scan(data: dict[str, np.ndarray]) -> None:
    x_axis = np.asarray(data["x_signed_amplitude_vpp"], dtype=float)
    y_axis = np.asarray(data["y_signed_amplitude_vpp"], dtype=float)
    expected_shape = (x_axis.size, y_axis.size)
    for key in ("r_mean_v", "r_std_v", "acquisition_order"):
        if np.asarray(data[key]).shape != expected_shape:
            raise ValueError(
                f"{key} 形状应为 {expected_shape}，实际为 "
                f"{np.asarray(data[key]).shape}"
            )
    if x_axis.size == 0 or y_axis.size == 0:
        raise ValueError("X/Y 扫描轴不能为空")
    if not np.all(np.isfinite(x_axis)) or not np.all(np.isfinite(y_axis)):
        raise ValueError("X/Y 扫描轴包含 NaN 或无穷值")
    if not np.all(np.isfinite(data["r_mean_v"])):
        raise ValueError("R 均值矩阵包含 NaN 或无穷值")
    if not np.all(np.isfinite(data["r_std_v"])):
        raise ValueError("R 标准差矩阵包含 NaN 或无穷值")


def _select_measured_minimum(
    data: dict[str, np.ndarray],
) -> tuple[int, int]:
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


def _plot_response(
    results_dir: Path,
    data: dict[str, np.ndarray],
    best_index: tuple[int, int],
) -> str:
    set_plot_style("paper")
    x_axis = np.asarray(data["x_signed_amplitude_vpp"], dtype=float)
    y_axis = np.asarray(data["y_signed_amplitude_vpp"], dtype=float)
    r_mean = np.asarray(data["r_mean_v"], dtype=float)
    r_std = np.asarray(data["r_std_v"], dtype=float)
    x_best = float(x_axis[best_index[0]])
    y_best = float(y_axis[best_index[1]])

    fig, axes = new_figure(
        nrows=1,
        ncols=2,
        kind="wide",
        constrained_layout=True,
    )
    mean_mesh = axes[0].pcolormesh(
        x_axis,
        y_axis,
        r_mean.T,
        shading="auto",
        cmap="viridis", rasterized=True)
    axes[0].plot(
        x_best,
        y_best,
        marker="x",
        color="white",
        markersize=7,
        markeredgewidth=1.4,
        label="Measured minimum",
    )
    format_axis(
        axes[0],
        xlabel="X control signed amplitude (Vpp)",
        ylabel="Y control signed amplitude (Vpp)",
    )
    axes[0].legend(loc="best")
    mean_colorbar = fig.colorbar(mean_mesh, ax=axes[0])
    mean_colorbar.set_label("Mean R (V)")

    std_mesh = axes[1].pcolormesh(
        x_axis,
        y_axis,
        r_std.T,
        shading="auto",
        cmap="magma", rasterized=True)
    format_axis(
        axes[1],
        xlabel="X control signed amplitude (Vpp)",
        ylabel="Y control signed amplitude (Vpp)",
    )
    std_colorbar = fig.colorbar(std_mesh, ax=axes[1])
    std_colorbar.set_label("R standard deviation (V)")
    filename = "xy_leakage_response.png"
    save_figure(fig, results_dir / filename)
    return filename


def analyze(run_dir: Path) -> dict[str, Any]:
    """汇总二维 R 响应并报告实测网格最小点。"""
    run_dir = Path(run_dir).resolve()
    results_dir = run_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    config = _load_yaml(run_dir / "experiment_config.yaml")
    data = _load_scan(run_dir / "raw" / "xy_leakage_scan.npz")
    _validate_scan(data)
    best_index = _select_measured_minimum(data)
    x_axis = np.asarray(data["x_signed_amplitude_vpp"], dtype=float)
    y_axis = np.asarray(data["y_signed_amplitude_vpp"], dtype=float)
    r_mean = np.asarray(data["r_mean_v"], dtype=float)
    r_std = np.asarray(data["r_std_v"], dtype=float)
    order = np.asarray(data["acquisition_order"], dtype=int)
    plot_file = _plot_response(results_dir, data, best_index)

    result = {
        "experiment_id": EXPERIMENT_ID,
        "run_dir": str(run_dir),
        "scan_completed": config.get("completion_status") == "completed",
        "scan_shape": [int(x_axis.size), int(y_axis.size)],
        "scan_order": "X outer, Y serpentine",
        "measured_grid_minimum": {
            "x_index": best_index[0],
            "y_index": best_index[1],
            "x_signed_amplitude_vpp": float(x_axis[best_index[0]]),
            "y_signed_amplitude_vpp": float(y_axis[best_index[1]]),
            "r_mean_v": float(r_mean[best_index]),
            "r_std_v": float(r_std[best_index]),
            "acquisition_index": int(order[best_index]),
            "combined_state_remeasured": False,
        },
        "response_summary": {
            "minimum_r_v": float(np.min(r_mean)),
            "maximum_r_v": float(np.max(r_mean)),
            "mean_r_v": float(np.mean(r_mean)),
            "mean_point_std_v": float(np.mean(r_std)),
            "actual_rate_sa_s": float(data["actual_rate_sa_s"]),
        },
        "control_source": config.get("control_source", {}),
        "z_calibration": config.get("z_calibration", {}),
        "applied_control": config.get("applied_control", {}),
        "trigger": config.get("trigger", {}),
        "interpretation": {
            "continuous_fit_performed": False,
            "xy_field_calibration_performed": False,
            "absolute_leakage_field_reported": False,
        },
        "warnings": [
            "最佳 X/Y 组合未在扫描结束后额外复测 R。",
            "结果仅表示控制电压网格上的 R 响应，不是 X/Y 泄露磁场绝对标定。",
        ],
        "files": [plot_file],
    }
    (results_dir / "analysis.yaml").write_text(
        yaml.safe_dump(_builtin(result), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    (results_dir / "analysis.json").write_text(
        json.dumps(_builtin(result), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def main() -> int:
    run_dir = runtime_run_dir()
    if run_dir is None:
        raise RuntimeError("未设置分析运行目录")
    result = analyze(run_dir)
    print(f"Mx Z 最优控制 XY 泄露响应分析完成: {result['run_dir']}")
    for warning in result["warnings"]:
        print(f"[WARN] {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
