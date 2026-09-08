"""Mx Z 最优控制 XY 补偿偏置 RF 灵敏度参数。"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ...common import validate_safety_limit
from ...experiment_params import parameter
from ..mx_z_optimal_control_rf_sensitivity.models import (
    MxZOptimalControlRFParams,
)


@dataclass(slots=True)
class MxZOptimalControlXYRFSensitivityParams(MxZOptimalControlRFParams):
    """固定一次 RF 校相，并在 XY DC 补偿网格上测量 RF 灵敏度。"""

    schema_version = 1

    noise_rf_enabled: bool = parameter(
        default=False, external_name="NOISE_RF_ENABLED",
        label="未使用噪声 RF 开关", visible=False,
    )
    noise_rf_amplitude_vpp: float = parameter(
        default=0.002, external_name="NOISE_RF_AMPLITUDE_VPP",
        label="未使用噪声 RF 幅值", visible=False,
    )

    # 父类字段保留为历史兼容键；实际校相点由扫描轴中心决定。
    x_dc_field_v: float = parameter(
        default=0.0,
        external_name="FIXED_PARAMS.X_magnetic_field",
        label="历史 X 补偿值（未使用）",
        unit="V",
        visible=False,
        safety_key="X_magnetic_field",
    )
    y_rf_offset_v: float = parameter(
        default=0.0,
        external_name="FIXED_PARAMS.Y_magnetic_field",
        label="历史 Y 补偿值（未使用）",
        unit="V",
        visible=False,
        safety_key="Y_magnetic_field",
    )

    x_field_start_v: float = parameter(
        default=-0.05,
        external_name="X_FIELD_START_V",
        label="X 补偿偏置起点",
        unit="V",
        group="basic",
        safety_key="X_magnetic_field",
    )
    x_field_stop_v: float = parameter(
        default=0.05,
        external_name="X_FIELD_STOP_V",
        label="X 补偿偏置终点",
        unit="V",
        group="basic",
        safety_key="X_magnetic_field",
    )
    x_field_points: int = parameter(
        default=11,
        external_name="X_FIELD_POINTS",
        label="X 补偿偏置点数",
        group="basic",
        minimum=1,
    )
    y_field_start_v: float = parameter(
        default=-0.05,
        external_name="Y_FIELD_START_V",
        label="Y 补偿偏置起点",
        unit="V",
        group="basic",
        safety_key="Y_magnetic_field",
    )
    y_field_stop_v: float = parameter(
        default=0.05,
        external_name="Y_FIELD_STOP_V",
        label="Y 补偿偏置终点",
        unit="V",
        group="basic",
        safety_key="Y_magnetic_field",
    )
    y_field_points: int = parameter(
        default=11,
        external_name="Y_FIELD_POINTS",
        label="Y 补偿偏置点数",
        group="basic",
        minimum=1,
    )

    def x_axis(self) -> np.ndarray:
        return np.linspace(
            self.x_field_start_v,
            self.x_field_stop_v,
            self.x_field_points,
            dtype=float,
        )

    def y_axis(self) -> np.ndarray:
        return np.linspace(
            self.y_field_start_v,
            self.y_field_stop_v,
            self.y_field_points,
            dtype=float,
        )

    def phase_calibration_point(self) -> tuple[float, float]:
        """返回扫描轴的算术中心，用于唯一一次 RF 校相。"""
        return (
            0.5 * (self.x_field_start_v + self.x_field_stop_v),
            0.5 * (self.y_field_start_v + self.y_field_stop_v),
        )

    @staticmethod
    def _validate_axis(
        label: str,
        start: float,
        stop: float,
        points: int,
    ) -> list[str]:
        if points < 1:
            return [f"{label} 点数必须至少为 1"]
        if points == 1:
            return (
                []
                if np.isclose(start, stop)
                else [f"{label} 点数为 1 时必须满足 START=STOP"]
            )
        if not start < stop:
            return [f"{label} 点数大于 1 时必须满足 START<STOP"]
        return []

    def validate_model(self) -> list[str]:
        errors = MxZOptimalControlRFParams.validate_model(self)
        errors.extend(
            self._validate_axis(
                "X 补偿偏置",
                self.x_field_start_v,
                self.x_field_stop_v,
                self.x_field_points,
            )
        )
        errors.extend(
            self._validate_axis(
                "Y 补偿偏置",
                self.y_field_start_v,
                self.y_field_stop_v,
                self.y_field_points,
            )
        )

        for key, values in (
            (
                "X_magnetic_field",
                (self.x_field_start_v, self.x_field_stop_v),
            ),
            (
                "Y_magnetic_field",
                (self.y_field_start_v, self.y_field_stop_v),
            ),
        ):
            for value in values:
                try:
                    validate_safety_limit(key, float(value))
                except ValueError as exc:
                    errors.append(str(exc))

        maximum_rf_amplitude = max(
            abs(float(self.y_rf_amp_start_vpp)),
            abs(float(self.y_rf_amp_stop_vpp)),
            abs(float(self.phase_cal_rf_amplitude_vpp)),
        )
        try:
            validate_safety_limit("rf_coil", maximum_rf_amplitude)
        except ValueError as exc:
            errors.append(str(exc))
        for offset in (self.y_field_start_v, self.y_field_stop_v):
            for value in (
                float(offset) - maximum_rf_amplitude / 2.0,
                float(offset) + maximum_rf_amplitude / 2.0,
            ):
                try:
                    validate_safety_limit("Y_magnetic_field", value)
                except ValueError as exc:
                    errors.append(f"Y RF 输出包络: {exc}")

        return errors
