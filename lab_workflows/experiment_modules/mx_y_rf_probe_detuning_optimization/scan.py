"""Mx Y RF Probe 光功率与 PZT 失谐二维扫描轴。"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .models import MxYRFProbeDetuningOptimizationParams


@dataclass(frozen=True, slots=True)
class ProbeDetuningGridPoint:
    pzt_index: int
    probe_index: int
    pzt_voltage_v: float
    probe_power_v: float

    @property
    def key(self) -> str:
        return f"pzt_{self.pzt_index:03d}_probe_{self.probe_index:03d}"


def build_probe_detuning_axes(
    params: MxYRFProbeDetuningOptimizationParams,
) -> tuple[np.ndarray, np.ndarray]:
    """返回升序 PZT 电压和 Probe 功率轴。"""
    pzt_axis = np.linspace(
        params.pzt_voltage_start_v,
        params.pzt_voltage_stop_v,
        params.pzt_voltage_points,
        dtype=float,
    )
    probe_axis = np.linspace(
        params.probe_power_start_v,
        params.probe_power_stop_v,
        params.probe_power_points,
        dtype=float,
    )
    return pzt_axis, probe_axis


def iter_probe_detuning_grid(
    params: MxYRFProbeDetuningOptimizationParams,
) -> list[ProbeDetuningGridPoint]:
    """以 PZT 为外层、Probe 为内层生成蛇形采集顺序。"""
    pzt_axis, probe_axis = build_probe_detuning_axes(params)
    points: list[ProbeDetuningGridPoint] = []
    for pzt_index, pzt_voltage in enumerate(pzt_axis):
        probe_indices = range(len(probe_axis))
        if pzt_index % 2:
            probe_indices = reversed(range(len(probe_axis)))
        for probe_index in probe_indices:
            points.append(
                ProbeDetuningGridPoint(
                    pzt_index=pzt_index,
                    probe_index=probe_index,
                    pzt_voltage_v=float(pzt_voltage),
                    probe_power_v=float(probe_axis[probe_index]),
                )
            )
    return points
