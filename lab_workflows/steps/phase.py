"""可复用的 HF2 解调器相位校准与干扰源保护。"""

from __future__ import annotations

import math
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from typing import Any, Callable, Iterator

from lockin_amplifier import demod

from ..common import CancellationToken, ProgressCallback, emit
from .state import StateGuard


@dataclass(slots=True)
class PhaseCalibrationConfig:
    demod_idx: int = 0
    tolerance_deg: float = 1.0
    max_attempts: int = 5
    settle_time: float = 0.2
    minimum_r: float = 1e-12


@dataclass(slots=True)
class PhaseCalibrationResult:
    demod_idx: int
    before_sample: dict[str, float]
    after_sample: dict[str, float]
    phase_shift_deg: float
    attempts: int
    converged: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def calibrate_demod_phase(
    hf2: Any,
    config: PhaseCalibrationConfig,
    *,
    cancellation: CancellationToken | None = None,
    progress: ProgressCallback | None = None,
) -> PhaseCalibrationResult:
    """仅校准指定 Demod；设备连接和干扰源处理由调用方负责。"""
    base = hf2.demod_path(config.demod_idx)
    before = demod.read_demod_sample(hf2, config.demod_idx)
    after = before
    for attempt in range(1, config.max_attempts + 1):
        if cancellation:
            cancellation.raise_if_cancelled()
        time.sleep(max(0.0, config.settle_time))
        sample = demod.read_demod_sample(hf2, config.demod_idx)
        theta_deg = math.degrees(float(sample["theta"]))
        if float(sample["r"]) < config.minimum_r:
            raise RuntimeError(
                f"Demod{config.demod_idx} 输出幅度过小，无法可靠校相"
            )
        emit(
            progress,
            "phase",
            f"Demod{config.demod_idx} 校相 {attempt}/{config.max_attempts}: {theta_deg:.4f}°",
        )
        after = sample
        if abs(theta_deg) < config.tolerance_deg:
            return PhaseCalibrationResult(
                config.demod_idx,
                before,
                after,
                float(hf2.get_double(f"{base}/phaseshift")),
                attempt,
                True,
            )
        current = float(hf2.get_double(f"{base}/phaseshift"))
        hf2.set_double(f"{base}/phaseshift", current + theta_deg)
        hf2.sync()
    raise RuntimeError(
        f"Demod{config.demod_idx} 相位校准未在 {config.max_attempts} 次内收敛"
    )


@contextmanager
def phase_calibration_guard(
    preparations: list[tuple[str, Callable[[], Callable[[], None]]]],
) -> Iterator[StateGuard]:
    """执行干扰源准备，并按逆序恢复。

    每个 preparation 返回一个无参恢复函数，使不同实验可声明自己的 Z 场、
    温控或其他干扰源策略。
    """
    guard = StateGuard()
    try:
        for label, prepare in preparations:
            guard.add(label, prepare())
        yield guard
    finally:
        guard.restore()
