"""DG4000 Z 偏置 XYZ 扫描轴。"""

from __future__ import annotations

from collections.abc import Iterator

import numpy as np

from .models import MxZOptimalControlDG4000BiasXYZBalanceParams


def _build_axis(start: float, stop: float, points: int, name: str) -> np.ndarray:
    if points < 1:
        raise ValueError(f"{name} 点数必须至少为 1")
    if points == 1:
        if start != stop:
            raise ValueError(f"{name} 点数为 1 时必须满足 START=STOP")
        return np.asarray([start], dtype=float)
    if start >= stop:
        raise ValueError(f"{name} 点数大于 1 时必须满足 START<STOP")
    return np.linspace(start, stop, points, dtype=float)


def build_xyz_axes(
    params: MxZOptimalControlDG4000BiasXYZBalanceParams,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """构造 X(V)、Y(V)、Z 偏置(V) 扫描轴。"""
    return (
        _build_axis(
            params.x_field_start_v,
            params.x_field_stop_v,
            params.x_field_points,
            "X",
        ),
        _build_axis(
            params.y_field_start_v,
            params.y_field_stop_v,
            params.y_field_points,
            "Y",
        ),
        _build_axis(
            params.z_bias_start_v,
            params.z_bias_stop_v,
            params.z_bias_points,
            "Z 偏置",
        ),
    )


def iter_serpentine_grid(
    params: MxZOptimalControlDG4000BiasXYZBalanceParams,
) -> Iterator[tuple[int, int, int, int, float, float, float]]:
    """按 Z 偏置外层、X/Y 蛇形顺序返回规范 Z/X/Y 索引。"""
    x_axis, y_axis, z_axis = build_xyz_axes(params)
    acquisition_index = 0
    row_index = 0
    for z_index, z_bias_v in enumerate(z_axis):
        x_indices = (
            range(x_axis.size)
            if z_index % 2 == 0
            else range(x_axis.size - 1, -1, -1)
        )
        for x_index in x_indices:
            y_indices = (
                range(y_axis.size)
                if row_index % 2 == 0
                else range(y_axis.size - 1, -1, -1)
            )
            for y_index in y_indices:
                yield (
                    acquisition_index,
                    z_index,
                    x_index,
                    y_index,
                    float(z_bias_v),
                    float(x_axis[x_index]),
                    float(y_axis[y_index]),
                )
                acquisition_index += 1
            row_index += 1


def first_acquired_minimum(
    records: list[dict[str, float | int | str]],
) -> dict[str, float | int | str]:
    """按 R 最小、采集序号最先选择实测最佳点。"""
    finite = [
        record
        for record in records
        if np.isfinite(float(record["r_mean_v"]))
    ]
    if not finite:
        raise ValueError("三维扫描没有有限的 R 均值")
    return min(
        finite,
        key=lambda record: (
            float(record["r_mean_v"]),
            int(record["acquisition_index"]),
        ),
    )
