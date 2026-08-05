"""Mx Z 最优控制 XY 泄露响应扫描轴。"""

from __future__ import annotations

from collections.abc import Iterator

import numpy as np

from .models import MxZOptimalControlXYLeakageParams


def build_xy_axes(
    params: MxZOptimalControlXYLeakageParams,
) -> tuple[np.ndarray, np.ndarray]:
    """构造包含端点的 X/Y 带符号 Vpp 轴。"""
    return (
        np.linspace(
            params.x_control_amp_start_vpp,
            params.x_control_amp_stop_vpp,
            params.x_control_amp_points,
            dtype=float,
        ),
        np.linspace(
            params.y_control_amp_start_vpp,
            params.y_control_amp_stop_vpp,
            params.y_control_amp_points,
            dtype=float,
        ),
    )


def iter_serpentine_grid(
    params: MxZOptimalControlXYLeakageParams,
) -> Iterator[tuple[int, int, int, float, float]]:
    """按 X 外层、Y 蛇形顺序返回采集序号和规范矩阵索引。"""
    x_axis, y_axis = build_xy_axes(params)
    acquisition_index = 0
    for x_index, x_vpp in enumerate(x_axis):
        y_indices = (
            range(y_axis.size)
            if x_index % 2 == 0
            else range(y_axis.size - 1, -1, -1)
        )
        for y_index in y_indices:
            yield (
                acquisition_index,
                x_index,
                y_index,
                float(x_vpp),
                float(y_axis[y_index]),
            )
            acquisition_index += 1


def signed_control_hardware(
    signed_amplitude_vpp: float,
    base_phase_deg: float,
) -> tuple[float, float, bool]:
    """将带符号幅度转换为绝对 Vpp、Burst 相位和输出状态。"""
    value = float(signed_amplitude_vpp)
    base_phase = float(base_phase_deg) % 360.0
    if np.isclose(value, 0.0, rtol=0.0, atol=1e-15):
        return 0.0, base_phase, False
    phase = base_phase if value > 0 else (base_phase + 180.0) % 360.0
    return abs(value), phase, True


def first_acquired_minimum(
    records: list[dict[str, float | int | bool | str]],
) -> dict[str, float | int | bool | str]:
    """按 R 最小、采集序号最先的规则选择实测最佳点。"""
    if not records:
        raise ValueError("二维扫描没有可用于选择最佳点的记录")
    finite = [
        item for item in records if np.isfinite(float(item["r_mean_v"]))
    ]
    if not finite:
        raise ValueError("二维扫描没有有限的 R 均值")
    return min(
        finite,
        key=lambda item: (
            float(item["r_mean_v"]),
            int(item["acquisition_index"]),
        ),
    )
