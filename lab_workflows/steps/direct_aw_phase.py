"""可复用的 DirectAW 相位反馈校准。"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Callable, Mapping

from ..common import ProgressCallback, emit


@dataclass(slots=True)
class DirectAWPhaseCalibrationConfig:
    """DirectAW 相位反馈配置。"""

    tolerance_deg: float = 1.0
    max_measurements: int = 8
    minimum_r_v: float = 1e-12
    minimum_r_ratio: float = 0.1
    update_gain: float = 1.0
    update_sign: float = -1.0

    def validate(self) -> None:
        if self.tolerance_deg < 0:
            raise ValueError("DirectAW 相位校准容差不能小于 0")
        if self.max_measurements < 1:
            raise ValueError("DirectAW 相位校准测量次数必须至少为 1")
        if self.minimum_r_v < 0:
            raise ValueError("DirectAW 相位校准最低 R 不能小于 0")
        if not 0 <= self.minimum_r_ratio <= 1:
            raise ValueError("DirectAW 相位校准最低 R 比例必须位于 [0, 1]")
        if self.update_gain <= 0:
            raise ValueError("DirectAW 相位校准更新增益必须大于 0")
        if self.update_sign == 0:
            raise ValueError("DirectAW 相位校准更新方向不能为 0")


@dataclass(slots=True)
class DirectAWPhaseMeasurement:
    """一次实际完成的 DirectAW 相位测量。"""

    iteration: int
    phase_deg: float
    theta_deg: float
    r_v: float
    minimum_r_v: float
    accepted: bool
    mode: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class DirectAWPhaseCalibrationResult:
    """DirectAW 相位反馈结果。"""

    initial_phase_deg: float
    final_phase_deg: float
    final_theta_deg: float
    converged: bool
    best_r_v: float
    history: list[DirectAWPhaseMeasurement]

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["history"] = [item.to_dict() for item in self.history]
        return result


def wrap_phase_deg(angle_deg: float) -> float:
    """把角度压到 [-180, 180) 区间。"""
    return (float(angle_deg) + 180.0) % 360.0 - 180.0


def calibrate_direct_aw_phase(
    initial_phase_deg: float,
    apply_and_measure: Callable[[float], Mapping[str, Any]],
    config: DirectAWPhaseCalibrationConfig,
    *,
    cancellation_check: Callable[[], None] | None = None,
    progress: ProgressCallback | None = None,
) -> DirectAWPhaseCalibrationResult:
    """迭代 DirectAW 波形相位，并拒绝幅度过低的 theta。

    ``apply_and_measure`` 必须把传入相位真正写入仪器并返回包含 ``theta``
    （弧度）和 ``r``（V）的 Demod 样本。共享步骤不连接设备，也不处理温控、
    触发源或异常恢复。
    """
    config.validate()
    initial_phase = float(initial_phase_deg) % 360.0
    phase_deg = initial_phase
    best_r_v = 0.0
    history: list[DirectAWPhaseMeasurement] = []

    for iteration in range(config.max_measurements):
        if cancellation_check is not None:
            cancellation_check()
        sample = apply_and_measure(phase_deg)
        theta_deg = wrap_phase_deg(math.degrees(float(sample["theta"])))
        r_v = float(sample["r"])
        if not math.isfinite(theta_deg) or not math.isfinite(r_v):
            raise RuntimeError("DirectAW 相位校准读取到非有限 theta/R")

        best_r_v = max(best_r_v, r_v)
        minimum_r_v = max(
            float(config.minimum_r_v),
            float(config.minimum_r_ratio) * best_r_v,
        )
        accepted = r_v >= minimum_r_v
        mode = "initial" if iteration == 0 else "iterate"
        if not accepted:
            mode = "low_r_rejected"
        measurement = DirectAWPhaseMeasurement(
            iteration=iteration,
            phase_deg=float(phase_deg),
            theta_deg=float(theta_deg),
            r_v=r_v,
            minimum_r_v=float(minimum_r_v),
            accepted=accepted,
            mode=mode,
        )
        history.append(measurement)
        emit(
            progress,
            "direct_aw_phase",
            (
                f"DirectAW 校相 {iteration + 1}/{config.max_measurements}: "
                f"phase={phase_deg:.4f}°, theta={theta_deg:+.4f}°, "
                f"R={r_v:.4e} V"
                + ("" if accepted else f"，低于阈值 {minimum_r_v:.4e} V，拒绝更新")
            ),
        )

        if accepted and abs(theta_deg) < config.tolerance_deg:
            return DirectAWPhaseCalibrationResult(
                initial_phase_deg=initial_phase,
                final_phase_deg=float(phase_deg),
                final_theta_deg=float(theta_deg),
                converged=True,
                best_r_v=float(best_r_v),
                history=history,
            )

        # 最后一轮不再生成一个未经实际测量的新相位。
        if accepted and iteration + 1 < config.max_measurements:
            phase_deg = (
                phase_deg
                + config.update_sign * config.update_gain * theta_deg
            ) % 360.0

    return DirectAWPhaseCalibrationResult(
        initial_phase_deg=initial_phase,
        final_phase_deg=float(history[-1].phase_deg),
        final_theta_deg=float(history[-1].theta_deg),
        converged=False,
        best_r_v=float(best_r_v),
        history=history,
    )
