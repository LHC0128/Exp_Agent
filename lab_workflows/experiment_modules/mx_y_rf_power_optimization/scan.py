"""Mx Y RF 光功率二维扫描轴。"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .models import MxYRFPowerOptimizationParams


@dataclass(frozen=True, slots=True)
class PowerGridPoint:
    pump_index: int
    probe_index: int
    pump_power_v: float
    probe_power_v: float

    @property
    def key(self) -> str:
        return f"pump_{self.pump_index:03d}_probe_{self.probe_index:03d}"


def build_power_axes(
    params: MxYRFPowerOptimizationParams,
) -> tuple[np.ndarray, np.ndarray]:
    """返回升序 Pump 和 Probe 功率轴。"""
    pump_axis = np.linspace(
        params.pump_power_start_v,
        params.pump_power_stop_v,
        params.pump_power_points,
        dtype=float,
    )
    probe_axis = np.linspace(
        params.probe_power_start_v,
        params.probe_power_stop_v,
        params.probe_power_points,
        dtype=float,
    )
    return pump_axis, probe_axis


def iter_serpentine_grid(
    params: MxYRFPowerOptimizationParams,
) -> list[PowerGridPoint]:
    """以 Pump 为外层、Probe 为内层生成蛇形采集顺序。"""
    pump_axis, probe_axis = build_power_axes(params)
    points: list[PowerGridPoint] = []
    for pump_index, pump_power in enumerate(pump_axis):
        probe_indices = range(len(probe_axis))
        if pump_index % 2:
            probe_indices = reversed(range(len(probe_axis)))
        for probe_index in probe_indices:
            points.append(
                PowerGridPoint(
                    pump_index=pump_index,
                    probe_index=probe_index,
                    pump_power_v=float(pump_power),
                    probe_power_v=float(probe_axis[probe_index]),
                )
            )
    return points
