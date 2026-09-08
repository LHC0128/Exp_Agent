"""Mx Z 实际电流-耦合强度标定参数。"""

from __future__ import annotations

from dataclasses import dataclass

from ...current_feedback import validate_sense_resistor
from ...experiment_params import parameter
from ..mx_z_field_calibration.models import MxZFieldCalibrationParams


@dataclass(slots=True)
class MxZCurrentCouplingCalibrationParams(MxZFieldCalibrationParams):
    """在 Mx Z 共振标定中同步采集采样电阻电压。"""

    schema_version = 1

    run_tag: str = parameter(
        default="mx_z_current_coupling_calibration",
        external_name="RUN_TAG",
        label="运行标签",
        group="basic",
    )
    sense_resistor_ohm: float = parameter(
        default=0.0,
        external_name="SENSE_RESISTOR_OHM",
        label="采样电阻实测阻值",
        unit="ohm",
        group="basic",
        minimum=0.0,
    )
    sense_resistor_tolerance_percent: float = parameter(
        default=0.0,
        external_name="SENSE_RESISTOR_TOLERANCE_PERCENT",
        label="采样电阻误差",
        unit="%",
        group="advanced",
        minimum=0.0,
    )
    sense_resistor_power_rating_w: float = parameter(
        default=0.0,
        external_name="SENSE_RESISTOR_POWER_RATING_W",
        label="采样电阻额定功率",
        unit="W",
        group="basic",
        minimum=0.0,
    )
    sense_resistor_power_derating: float = parameter(
        default=0.5,
        external_name="SENSE_RESISTOR_POWER_DERATING",
        label="采样电阻功率降额系数",
        group="advanced",
        minimum=0.01,
        maximum=1.0,
    )
    maximum_current_a: float = parameter(
        default=0.0,
        external_name="MAXIMUM_CURRENT_A",
        label="线圈峰值电流安全上限",
        unit="A",
        group="basic",
        minimum=0.0,
    )
    sense_scope_channel: int = parameter(
        default=3,
        external_name="SENSE_SCOPE_CHANNEL",
        label="采样电阻示波器通道",
        visible=False,
        minimum=1,
        maximum=4,
    )
    sense_scope_sample_rate_sa_s: float = parameter(
        default=500000.0,
        external_name="SENSE_SCOPE_SAMPLE_RATE_SA_S",
        label="采样电阻采样率",
        unit="Sa/s",
        group="basic",
        minimum=100.0,
    )
    sense_scope_duration_s: float = parameter(
        default=0.02,
        external_name="SENSE_SCOPE_DURATION_S",
        label="采样电阻记录时长",
        unit="s",
        group="basic",
        minimum=0.001,
    )
    sense_scope_initial_scale_v_div: float = parameter(
        default=1.0,
        external_name="SENSE_SCOPE_INITIAL_SCALE_V_DIV",
        label="采样电阻初始量程",
        unit="V/div",
        group="basic",
        minimum=0.000001,
    )
    sense_scope_scale_min_v_div: float = parameter(
        default=0.01,
        external_name="SENSE_SCOPE_SCALE_MIN_V_DIV",
        label="采样电阻自动量程下限",
        unit="V/div",
        group="advanced",
        minimum=0.000001,
    )
    sense_scope_scale_max_v_div: float = parameter(
        default=10.0,
        external_name="SENSE_SCOPE_SCALE_MAX_V_DIV",
        label="采样电阻自动量程上限",
        unit="V/div",
        group="advanced",
        minimum=0.000001,
    )
    sense_scope_vertical_divisions: int = parameter(
        default=8,
        external_name="SENSE_SCOPE_VERTICAL_DIVISIONS",
        label="采样电阻示波器垂直总格数",
        group="advanced",
        minimum=1,
    )
    sense_scope_auto_range_low_fraction: float = parameter(
        default=0.4,
        external_name="SENSE_SCOPE_AUTO_RANGE_LOW_FRACTION",
        label="采样电阻自动量程缩小阈值",
        group="advanced",
        minimum=0.0,
        maximum=1.0,
    )
    sense_scope_auto_range_high_fraction: float = parameter(
        default=0.9,
        external_name="SENSE_SCOPE_AUTO_RANGE_HIGH_FRACTION",
        label="采样电阻自动量程放大阈值",
        group="advanced",
        minimum=0.0,
        maximum=1.0,
    )
    sense_scope_auto_offset_tolerance_fraction: float = parameter(
        default=0.05,
        external_name="SENSE_SCOPE_AUTO_OFFSET_TOLERANCE_FRACTION",
        label="采样电阻自动偏置居中死区",
        group="advanced",
        minimum=0.0,
        maximum=1.0,
    )
    sense_scope_auto_range_max_attempts: int = parameter(
        default=3,
        external_name="SENSE_SCOPE_AUTO_RANGE_MAX_ATTEMPTS",
        label="采样电阻自动量程最大尝试次数",
        group="advanced",
        minimum=1,
        maximum=10,
    )
    sense_scope_auto_range_allow_shrink: bool = parameter(
        default=False,
        external_name="SENSE_SCOPE_AUTO_RANGE_ALLOW_SHRINK",
        label="允许跨工作点缩小自动量程",
        group="advanced",
        description="直流扫描默认关闭，避免下一个工作点电压跳变时量程过小。",
    )
    bidirectional_scan: bool = parameter(
        default=True,
        external_name="BIDIRECTIONAL_SCAN",
        label="正反向电流扫描",
        group="basic",
    )
    current_settle_time_s: float = parameter(
        default=0.2,
        external_name="CURRENT_SETTLE_TIME_S",
        label="电流稳定等待",
        unit="s",
        group="advanced",
        minimum=0.0,
    )

    def validate_model(self) -> list[str]:
        errors = MxZFieldCalibrationParams.validate_model(self)
        if self.sense_scope_channel != 3:
            errors.append("SENSE_SCOPE_CHANNEL 必须固定为 CH3 采样电阻电压")
        try:
            validate_sense_resistor(
                self.sense_resistor_ohm,
                self.sense_resistor_power_rating_w,
                tolerance_percent=self.sense_resistor_tolerance_percent,
            )
        except ValueError as exc:
            errors.append(str(exc))
        if self.sense_scope_channel == 4:
            errors.append("SENSE_SCOPE_CHANNEL 不能占用 CH4 触发参考")
        if self.sense_scope_scale_min_v_div > self.sense_scope_scale_max_v_div:
            errors.append(
                "SENSE_SCOPE_SCALE_MIN_V_DIV 必须不大于 "
                "SENSE_SCOPE_SCALE_MAX_V_DIV"
            )
        if not (
            self.sense_scope_scale_min_v_div
            <= self.sense_scope_initial_scale_v_div
            <= self.sense_scope_scale_max_v_div
        ):
            errors.append(
                "SENSE_SCOPE_INITIAL_SCALE_V_DIV 必须位于示波器自动量程范围内"
            )
        if (
            self.sense_scope_auto_range_low_fraction
            >= self.sense_scope_auto_range_high_fraction
        ):
            errors.append(
                "SENSE_SCOPE_AUTO_RANGE_LOW_FRACTION 必须小于 "
                "SENSE_SCOPE_AUTO_RANGE_HIGH_FRACTION"
            )
        if self.maximum_current_a <= 0.0:
            errors.append("MAXIMUM_CURRENT_A 必须填写线圈峰值电流安全上限")
        return errors
