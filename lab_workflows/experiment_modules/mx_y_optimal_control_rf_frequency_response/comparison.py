"""常数 Z 控制对照：标定读取、全局线性拟合与恒定电压反解。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from ...common import validate_safety_limit
from ...current_feedback import (
    CurrentCouplingCalibration,
    load_current_coupling_calibration,
)


@dataclass(frozen=True, slots=True)
class ConstantControlPlan:
    """常数 Z 控制对照的完整反解结果与可追溯元数据。"""

    calibration_run: str
    calibration_sha256: str
    calibration_slope_hz_per_a: float
    calibration_intercept_hz: float
    target_larmor_frequency_hz: float
    target_current_a: float
    target_voltage_v: float
    gain_a_per_v: float
    intercept_a: float
    support_voltage_min_v: float
    support_voltage_max_v: float
    extrapolated: bool
    point_count: int

    def to_metadata(self) -> dict[str, Any]:
        """展开为写入运行配置的元数据。"""
        return {
            "enabled": True,
            "calibration_run": self.calibration_run,
            "calibration_analysis_sha256": self.calibration_sha256,
            "calibration_slope_hz_per_a": self.calibration_slope_hz_per_a,
            "calibration_intercept_hz": self.calibration_intercept_hz,
            "target_larmor_frequency_hz": self.target_larmor_frequency_hz,
            "target_current_a": self.target_current_a,
            "target_voltage_v": self.target_voltage_v,
            "voltage_to_current_fit": {
                "model": "current_a = gain_a_per_v * z_bias_v + intercept_a",
                "gain_a_per_v": self.gain_a_per_v,
                "intercept_a": self.intercept_a,
                "point_count": self.point_count,
            },
            "calibration_support": {
                "voltage_min_v": self.support_voltage_min_v,
                "voltage_max_v": self.support_voltage_max_v,
            },
            "extrapolated": self.extrapolated,
        }


def fit_voltage_to_current(
    calibration: CurrentCouplingCalibration,
) -> tuple[float, float, int, float, float]:
    """用标定中全部成功曲线点做电压—电流全局线性拟合。

    返回 (gain_a_per_v, intercept_a, point_count, voltage_min_v, voltage_max_v)。
    """
    points = [
        curve
        for curve in calibration.payload.get("curves", [])
        if isinstance(curve.get("fit"), dict) and curve["fit"].get("success") is True
    ]
    if len(points) < 2:
        raise ValueError(
            f"标定 {calibration.run_name} 的可用曲线点不足 2 个，无法拟合电压—电流关系"
        )
    voltage = np.asarray([curve["z_bias_v"] for curve in points], dtype=float)
    current = np.asarray([curve["current_a"] for curve in points], dtype=float)
    if not (np.all(np.isfinite(voltage)) and np.all(np.isfinite(current))):
        raise ValueError(f"标定 {calibration.run_name} 的曲线点包含非有限值")
    gain, intercept = np.polyfit(voltage, current, 1)
    gain = float(gain)
    if not np.isfinite(gain) or gain == 0.0:
        raise ValueError(f"标定 {calibration.run_name} 的电压—电流拟合斜率无效")
    return (
        gain,
        float(intercept),
        len(points),
        float(np.min(voltage)),
        float(np.max(voltage)),
    )


def build_constant_control_plan(
    project_root: Path,
    *,
    calibration_run: str,
    target_larmor_frequency_hz: float,
) -> ConstantControlPlan:
    """按标定把目标 Larmor 频率反解为恒定 Z 电压，并允许范围外推。"""
    if not np.isfinite(target_larmor_frequency_hz):
        raise ValueError("CONSTANT_CONTROL_LARMOR_FREQUENCY_HZ 必须是有限数值")
    calibration = load_current_coupling_calibration(project_root, calibration_run)
    target_current_a = (
        target_larmor_frequency_hz - calibration.intercept_hz
    ) / calibration.slope_hz_per_a
    if not np.isfinite(target_current_a):
        raise ValueError("按标定反解的目标线圈电流不是有限数值")
    (
        gain,
        intercept,
        point_count,
        support_min,
        support_max,
    ) = fit_voltage_to_current(calibration)
    target_voltage_v = (target_current_a - intercept) / gain
    if not np.isfinite(target_voltage_v):
        raise ValueError("按标定反解的目标恒定 Z 电压不是有限数值")
    validate_safety_limit("Z_magnetic_field", target_voltage_v)
    return ConstantControlPlan(
        calibration_run=calibration.run_name,
        calibration_sha256=calibration.analysis_sha256,
        calibration_slope_hz_per_a=calibration.slope_hz_per_a,
        calibration_intercept_hz=calibration.intercept_hz,
        target_larmor_frequency_hz=float(target_larmor_frequency_hz),
        target_current_a=float(target_current_a),
        target_voltage_v=float(target_voltage_v),
        gain_a_per_v=gain,
        intercept_a=intercept,
        support_voltage_min_v=support_min,
        support_voltage_max_v=support_max,
        extrapolated=bool(
            target_voltage_v < support_min or target_voltage_v > support_max
        ),
        point_count=point_count,
    )
