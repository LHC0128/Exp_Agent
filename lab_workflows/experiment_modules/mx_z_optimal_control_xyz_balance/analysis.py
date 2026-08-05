"""Mx Z 最优控制 XYZ 平衡场离线分析。"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from ...experiment_runtime import runtime_run_dir
from ...plotting import (
    PAPER_WIDE,
    format_axis,
    new_figure,
    save_figure,
    set_plot_style,
)


EXPERIMENT_ID = "mx-z-optimal-control-xyz-balance"


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
        raise FileNotFoundError(f"缺少三维扫描数据: {path}")
    with np.load(path, allow_pickle=False) as data:
        required = {
            "x_field_v",
            "y_field_v",
            "z_field_ma",
            "r_mean_v",
            "r_std_v",
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
    z_axis = np.asarray(data["z_field_ma"], dtype=float)
    expected_shape = (z_axis.size, x_axis.size, y_axis.size)
    if min(expected_shape) < 1:
        raise ValueError("XYZ 扫描轴不能为空")
    for key in ("r_mean_v", "r_std_v", "acquisition_order"):
        if np.asarray(data[key]).shape != expected_shape:
            raise ValueError(
                f"{key} 形状应为 {expected_shape}，实际为 "
                f"{np.asarray(data[key]).shape}"
            )
    for name, axis in (("X", x_axis), ("Y", y_axis), ("Z", z_axis)):
        if not np.all(np.isfinite(axis)):
            raise ValueError(f"{name} 扫描轴包含 NaN 或无穷值")
    for key in ("r_mean_v", "r_std_v"):
        if not np.all(np.isfinite(data[key])):
            raise ValueError(f"{key} 包含 NaN 或无穷值")


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


def _axis_edges(axis: np.ndarray) -> np.ndarray:
    axis = np.asarray(axis, dtype=float)
    if axis.size == 1:
        half_width = max(abs(float(axis[0])) * 0.05, 1e-6)
        return np.asarray([axis[0] - half_width, axis[0] + half_width])
    midpoint = (axis[:-1] + axis[1:]) / 2.0
    return np.concatenate(
        (
            [axis[0] - (midpoint[0] - axis[0])],
            midpoint,
            [axis[-1] + (axis[-1] - midpoint[-1])],
        )
    )


def _plot_xy_planes(
    results_dir: Path,
    data: dict[str, np.ndarray],
    best_index: tuple[int, int, int],
) -> str:
    set_plot_style("paper")
    best_z_index, best_x_index, best_y_index = best_index
    x_axis = np.asarray(data["x_field_v"], dtype=float)
    y_axis = np.asarray(data["y_field_v"], dtype=float)
    z_axis = np.asarray(data["z_field_ma"], dtype=float)
    r_mean = np.asarray(data["r_mean_v"], dtype=float)
    vmin = float(np.min(r_mean))
    vmax = float(np.max(r_mean))

    columns = min(3, int(z_axis.size))
    rows = int(np.ceil(z_axis.size / columns))
    figure_width = min(PAPER_WIDE[0], 2.25 * columns + 0.75)
    fig, axes = new_figure(
        figsize=(figure_width, 2.4 * rows),
        nrows=rows,
        ncols=columns,
        squeeze=False,
        sharex=True,
        sharey=True,
        constrained_layout=True,
    )
    x_edges = _axis_edges(x_axis)
    y_edges = _axis_edges(y_axis)
    mesh = None
    plane_axes = []
    for z_index, axis in enumerate(axes.flat[: z_axis.size]):
        plane_axes.append(axis)
        mesh = axis.pcolormesh(
            x_edges,
            y_edges,
            r_mean[z_index, :, :].T,
            shading="flat",
            cmap="viridis",
            vmin=vmin,
            vmax=vmax,
        )
        if z_index == best_z_index:
            axis.plot(
                float(x_axis[best_x_index]),
                float(y_axis[best_y_index]),
                marker="x",
                color="white",
                markersize=7,
                markeredgewidth=1.4,
                label="Measured minimum",
            )
        format_axis(
            axis,
            xlabel="X field setting (V)",
            ylabel="Y field setting (V)",
        )
        axis.set_title(
            f"Z current = {z_axis[z_index]:+.6g} mA",
            fontsize=8.5,
        )
    for axis in axes.flat[z_axis.size :]:
        axis.set_visible(False)
    assert mesh is not None
    colorbar = fig.colorbar(mesh, ax=plane_axes)
    colorbar.set_label("Mean R (V)")
    filename = "xyz_balance_slices.png"
    save_figure(fig, results_dir / filename)
    return filename


def _write_csv(results_dir: Path, data: dict[str, np.ndarray]) -> str:
    filename = "xyz_balance_grid.csv"
    x_axis = np.asarray(data["x_field_v"], dtype=float)
    y_axis = np.asarray(data["y_field_v"], dtype=float)
    z_axis = np.asarray(data["z_field_ma"], dtype=float)
    r_mean = np.asarray(data["r_mean_v"], dtype=float)
    r_std = np.asarray(data["r_std_v"], dtype=float)
    order = np.asarray(data["acquisition_order"], dtype=int)
    with (results_dir / filename).open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            (
                "acquisition_index",
                "z_index",
                "x_index",
                "y_index",
                "z_field_ma",
                "x_field_v",
                "y_field_v",
                "r_mean_v",
                "r_std_v",
            )
        )
        for z_index, x_index, y_index in np.ndindex(r_mean.shape):
            writer.writerow(
                (
                    int(order[z_index, x_index, y_index]),
                    z_index,
                    x_index,
                    y_index,
                    float(z_axis[z_index]),
                    float(x_axis[x_index]),
                    float(y_axis[y_index]),
                    float(r_mean[z_index, x_index, y_index]),
                    float(r_std[z_index, x_index, y_index]),
                )
            )
    return filename


def analyze(run_dir: Path) -> dict[str, Any]:
    """汇总三维 R 响应并报告实测网格最小点。"""
    run_dir = Path(run_dir).resolve()
    results_dir = run_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    config = _load_yaml(run_dir / "experiment_config.yaml")
    data = _load_scan(run_dir / "raw" / "xyz_balance_scan.npz")
    _validate_scan(data)
    best_index = select_measured_minimum(data)
    z_index, x_index, y_index = best_index
    x_axis = np.asarray(data["x_field_v"], dtype=float)
    y_axis = np.asarray(data["y_field_v"], dtype=float)
    z_axis = np.asarray(data["z_field_ma"], dtype=float)
    r_mean = np.asarray(data["r_mean_v"], dtype=float)
    r_std = np.asarray(data["r_std_v"], dtype=float)
    order = np.asarray(data["acquisition_order"], dtype=int)

    warnings = [
        "最佳 XYZ 组合未在扫描结束后额外复测。",
        "结果是 X/Y 电压与 GS200 电流设定值，未换算为绝对磁场。",
    ]
    for name, index, axis in (
        ("X", x_index, x_axis),
        ("Y", y_index, y_axis),
        ("Z", z_index, z_axis),
    ):
        if axis.size > 1 and index in {0, axis.size - 1}:
            warnings.append(f"{name} 最小点位于扫描边界。")

    plot_file = _plot_xy_planes(results_dir, data, best_index)
    csv_file = _write_csv(results_dir, data)
    npz_file = "balance_results.npz"
    np.savez(
        results_dir / npz_file,
        best_z_index=np.int64(z_index),
        best_x_index=np.int64(x_index),
        best_y_index=np.int64(y_index),
        best_z_field_ma=np.float64(z_axis[z_index]),
        best_x_field_v=np.float64(x_axis[x_index]),
        best_y_field_v=np.float64(y_axis[y_index]),
        best_r_mean_v=np.float64(r_mean[best_index]),
        best_r_std_v=np.float64(r_std[best_index]),
        best_acquisition_index=np.int64(order[best_index]),
    )

    result = {
        "experiment_id": EXPERIMENT_ID,
        "run_dir": str(run_dir),
        "scan_completed": config.get("completion_status") == "completed",
        "scan_shape_zyx": [
            int(z_axis.size),
            int(x_axis.size),
            int(y_axis.size),
        ],
        "scan_order": "Z outer, continuous X/Y serpentine",
        "criterion": "minimum measured arithmetic mean of HF2 Demod0 R",
        "measured_grid_minimum": {
            "z_index": z_index,
            "x_index": x_index,
            "y_index": y_index,
            "z_field_ma": float(z_axis[z_index]),
            "x_field_v": float(x_axis[x_index]),
            "y_field_v": float(y_axis[y_index]),
            "r_mean_v": float(r_mean[best_index]),
            "r_std_v": float(r_std[best_index]),
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
        "control_source": config.get("control_source", {}),
        "applied_control": config.get("applied_control", {}),
        "interpretation": {
            "continuous_fit_performed": False,
            "field_calibration_performed": False,
            "absolute_field_reported": False,
        },
        "warnings": warnings,
        "files": [npz_file, csv_file, plot_file],
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
    best = result["measured_grid_minimum"]
    print(
        "Mx Z 最优控制 XYZ 平衡场分析完成: "
        f"X={best['x_field_v']:+.6g} V, "
        f"Y={best['y_field_v']:+.6g} V, "
        f"Z={best['z_field_ma']:+.6g} mA"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
