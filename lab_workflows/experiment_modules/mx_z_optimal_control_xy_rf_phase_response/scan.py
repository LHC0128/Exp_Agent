"""XY 网格与相位轴的确定性扫描顺序。"""

from __future__ import annotations

from collections.abc import Iterator

import numpy as np

from ..mx_z_optimal_control_rf_sensitivity.phase import paired_phase_order
from .models import MxZOptimalControlXYRFPhaseResponseParams


def build_axes(
    params: MxZOptimalControlXYRFPhaseResponseParams,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """返回 X、Y、配置相位轴和实际采集相位轴。"""
    x_axis = np.linspace(params.x_field_start_v, params.x_field_stop_v, params.x_field_points)
    y_axis = np.linspace(params.y_field_start_v, params.y_field_stop_v, params.y_field_points)
    phase_axis = params.phase_axis_deg()
    return x_axis, y_axis, phase_axis, paired_phase_order(phase_axis)


def iter_serpentine_grid(
    params: MxZOptimalControlXYRFPhaseResponseParams,
) -> Iterator[tuple[int, int, int, float, float]]:
    """按 X 外层、Y 蛇形顺序返回网格点。"""
    x_axis, y_axis, _, _ = build_axes(params)
    acquisition_index = 0
    for x_index, x_value in enumerate(x_axis):
        y_indices = range(y_axis.size) if x_index % 2 == 0 else range(y_axis.size - 1, -1, -1)
        for y_index in y_indices:
            yield acquisition_index, x_index, y_index, float(x_value), float(y_axis[y_index])
            acquisition_index += 1
