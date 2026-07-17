"""任意波上传与不同 XY 控制链路策略。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np


@dataclass(slots=True)
class ArbitraryWaveformSpec:
    values: np.ndarray
    frequency: float
    amplitude: float
    offset: float = 0.0
    phase: float = 0.0
    channel: int = 1
    output: bool = False

    def validated_values(self) -> np.ndarray:
        values = np.asarray(self.values, dtype=float).reshape(-1)
        if values.size < 2:
            raise ValueError("任意波至少需要 2 个采样点")
        if not np.all(np.isfinite(values)):
            raise ValueError("任意波包含 NaN 或无穷值")
        if np.max(np.abs(values)) > 1.0 + 1e-12:
            raise ValueError("任意波必须归一化到 [-1, 1]")
        if self.frequency <= 0 or self.amplitude < 0:
            raise ValueError("任意波频率必须为正，幅度不能为负")
        return values


def upload_arbitrary(device: Any, spec: ArbitraryWaveformSpec) -> None:
    values = spec.validated_values()
    device.set_burst_state(False, channel=spec.channel)
    device.set_mod_state(False, channel=spec.channel)
    device.setup_arbitrary(
        values,
        freq=float(spec.frequency),
        amplitude=float(spec.amplitude),
        offset=float(spec.offset),
        phase=float(spec.phase),
        channel=spec.channel,
        output=False,
    )
    device.set_output(bool(spec.output), channel=spec.channel)


def load_control_waveform(path: Path) -> np.ndarray:
    values = np.loadtxt(path, delimiter=",")
    values = np.asarray(values, dtype=float).reshape(-1)
    if not np.all(np.isfinite(values)):
        raise ValueError(f"控制波形包含无效值: {path}")
    peak = float(np.max(np.abs(values)))
    if peak > 1.0 + 1e-12:
        raise ValueError(f"控制波形未归一化到 [-1, 1]: peak={peak}")
    return values


@dataclass(slots=True)
class DirectAWStrategy:
    points: int = 16384

    def configure_xy(
        self,
        device: Any,
        *,
        frequency: float,
        envelope: float,
        amplitude_vpp: float,
        phase_deg: float = 0.0,
        quadrature_deg: float = 90.0,
        output: bool = True,
    ) -> None:
        phase = np.deg2rad(phase_deg)
        quadrature = np.deg2rad(quadrature_deg)
        angle = np.linspace(0, 2 * np.pi, self.points, endpoint=False)
        scale = 0.0 if amplitude_vpp == 0 else 2.0 * envelope / amplitude_vpp
        x = scale * np.cos(angle + phase)
        y = scale * np.cos(angle + phase + quadrature)
        upload_arbitrary(device, ArbitraryWaveformSpec(x, frequency, amplitude_vpp, channel=1, output=False))
        upload_arbitrary(device, ArbitraryWaveformSpec(y, frequency, amplitude_vpp, channel=2, output=False))
        device.set_output(output, channel=1)
        device.set_output(output, channel=2)


@dataclass(slots=True)
class ExternalAMStrategy:
    def configure_xy(
        self,
        envelope_device: Any,
        carrier_device: Any,
        *,
        x_values: np.ndarray,
        y_values: np.ndarray,
        envelope_frequency: float,
        envelope_amplitude: float,
        carrier_frequency: float,
        carrier_amplitude: float,
        x_phase: float = 0.0,
        y_phase: float = 90.0,
        am_depth_x: float = 100.0,
        am_depth_y: float = 100.0,
    ) -> None:
        upload_arbitrary(envelope_device, ArbitraryWaveformSpec(x_values, envelope_frequency, envelope_amplitude, channel=1, output=False))
        upload_arbitrary(envelope_device, ArbitraryWaveformSpec(y_values, envelope_frequency, envelope_amplitude, channel=2, output=False))
        for channel, phase, depth in ((1, x_phase, am_depth_x), (2, y_phase, am_depth_y)):
            carrier_device.set_burst_state(False, channel=channel)
            carrier_device.set_mod_state(False, channel=channel)
            carrier_device.setup_sine(carrier_frequency, carrier_amplitude, phase=phase, channel=channel)
            carrier_device.set_mod_type("AM", channel=channel)
            carrier_device.set_mod_source("EXTernal", channel=channel)
            carrier_device.set_mod_am_depth(depth, channel=channel)
            carrier_device.set_mod_state(True, channel=channel)
        envelope_device.set_output(True, channel=1)
        envelope_device.set_output(True, channel=2)


@dataclass(slots=True)
class AWScaleStrategy:
    points: int = 2048

    def configure_constant(
        self,
        device: Any,
        *,
        normalized_value: float,
        frequency: float,
        amplitude_vpp: float,
        channel: int,
        output: bool = True,
    ) -> None:
        if not -1.0 <= normalized_value <= 1.0:
            raise ValueError("AWScale 常数必须位于 [-1, 1]")
        values = np.full(self.points, float(normalized_value))
        upload_arbitrary(
            device,
            ArbitraryWaveformSpec(values, frequency, amplitude_vpp, 0.0, 0.0, channel, output),
        )
