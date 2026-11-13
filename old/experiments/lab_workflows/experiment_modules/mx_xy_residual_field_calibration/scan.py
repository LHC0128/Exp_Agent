"""Mx XY 剩磁二维校准扫描轴。"""

from __future__ import annotations

import numpy as np

from .models import MxXYResidualFieldCalibrationParams


def build_xy_axes(
    params: MxXYResidualFieldCalibrationParams,
) -> tuple[np.ndarray, np.ndarray]:
    """返回均包含端点的 X/Y 电压轴。"""
    return (
        np.linspace(params.x_field_start_v, params.x_field_stop_v, params.x_field_points),
        np.linspace(params.y_field_start_v, params.y_field_stop_v, params.y_field_points),
    )


def iter_grid(
    params: MxXYResidualFieldCalibrationParams,
):
    """按 X 外层、Y 始终正向的固定顺序遍历网格。"""
    x_axis, y_axis = build_xy_axes(params)
    for x_index, x_v in enumerate(x_axis):
        for y_index, y_v in enumerate(y_axis):
            yield x_index, y_index, float(x_v), float(y_v)
