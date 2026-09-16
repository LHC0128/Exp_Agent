"""Z 线圈电感效应频率响应实验参数。"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ...common import validate_safety_limit
from ...experiment_params import ExperimentParams, parameter


@dataclass(slots=True)
class ZCoilInductanceFrequencyResponseParams(ExperimentParams):
    """固定幅度正弦扫频并采集 Z 线圈端电压。"""

    schema_version = 1

    run_tag: str = parameter(
        default="z_coil_inductance_frequency_response",
        external_name="RUN_TAG",
        label="运行标签",
        group="basic",
    )
    frequency_start_hz: float = parameter(
        default=10.0,
        external_name="FREQUENCY_START_HZ",
        label="起始频率",
        unit="Hz",
        group="basic",
        minimum=0.001,
    )
    frequency_stop_hz: float = parameter(
        default=10000.0,
        external_name="FREQUENCY_STOP_HZ",
        label="终止频率",
        unit="Hz",
        group="basic",
        minimum=0.001,
        maximum=100000.0,
    )
    frequency_points: int = parameter(
        default=31,
        external_name="FREQUENCY_POINTS",
        label="频率点数",
        group="basic",
        minimum=2,
        maximum=1001,
    )
    drive_amplitude_vpp: float = parameter(
        default=1.0,
        external_name="DRIVE_AMPLITUDE_VPP",
        label="Z 线圈正弦幅度",
        unit="Vpp",
        group="basic",
        minimum=0.001,
    )
    drive_offset_v: float = parameter(
        default=0.0,
        external_name="DRIVE_OFFSET_V",
        label="Z 线圈正弦偏置",
        unit="V",
        group="advanced",
    )
    frequency_settle_s: float = parameter(
        default=0.5,
        external_name="FREQUENCY_SETTLE_S",
        label="切频稳定等待",
        unit="s",
        group="advanced",
        minimum=0.0,
    )
    scope_cycles: int = parameter(
        default=3,
        external_name="SCOPE_CYCLES",
        label="每次采集周期数",
        group="basic",
        minimum=2,
        maximum=100,
    )
    scope_repeats: int = parameter(
        default=5,
        external_name="SCOPE_REPEATS",
        label="每个频率重复次数",
        group="basic",
        minimum=1,
        maximum=100,
    )
    trigger_frequency_hz: float = parameter(
        default=100.0,
        external_name="TRIGGER_FREQUENCY_HZ",
        label="共同触发频率",
        unit="Hz",
        group="basic",
        minimum=0.001,
    )
    trigger_amplitude_vpp: float = parameter(
        default=5.0,
        external_name="TRIGGER_AMPLITUDE_VPP",
        label="共同触发幅度",
        unit="Vpp",
        group="basic",
        minimum=0.001,
        maximum=20.0,
    )
    trigger_offset_v: float = parameter(
        default=2.5,
        external_name="TRIGGER_OFFSET_V",
        label="共同触发偏置",
        unit="V",
        group="advanced",
        minimum=-10.0,
        maximum=10.0,
    )
    trigger_duty_percent: float = parameter(
        default=50.0,
        external_name="TRIGGER_DUTY_PERCENT",
        label="共同触发占空比",
        unit="%",
        group="advanced",
        minimum=0.1,
        maximum=99.9,
    )

    scope_sample_rate_sa_s: float = parameter(
        default=500000.0,
        external_name="SCOPE_SAMPLE_RATE_SA_S",
        label="示波器请求采样率",
        unit="Sa/s",
        group="basic",
        minimum=1000.0,
        description="用于每个频率点的 SDS 波形采集",
    )
    scope_initial_scale_v_div: float = parameter(
        default=1.0,
        external_name="SCOPE_INITIAL_SCALE",
        label="CH3 初始垂直量程",
        unit="V/div",
        group="basic",
        minimum=0.000001,
        description=(
            "仅用于本次运行开始；切频继承上一频点量程和偏置，采集中继续自动调整。"
        ),
    )
    scope_measured_channel: int = parameter(
        default=3,
        external_name="SCOPE_MEASURED_CHANNEL",
        label="线圈测量通道",
        visible=False,
        minimum=1,
        maximum=4,
    )
    scope_trigger_channel: int = parameter(
        default=4,
        external_name="SCOPE_TRIGGER_CHANNEL",
        label="触发参考通道",
        visible=False,
        minimum=1,
        maximum=4,
    )
    scope_scale_min_v_div: float = parameter(
        default=0.01,
        external_name="SCOPE_SCALE_MIN",
        label="示波器量程下限",
        unit="V/div",
        visible=False,
        minimum=0.000001,
    )
    scope_scale_max_v_div: float = parameter(
        default=10.0,
        external_name="SCOPE_SCALE_MAX",
        label="示波器量程上限",
        unit="V/div",
        visible=False,
        minimum=0.000001,
    )
    scope_vertical_divisions: int = parameter(
        default=8,
        external_name="SCOPE_VERTICAL_DIVISIONS",
        label="示波器垂直总格数",
        visible=False,
        minimum=1,
    )
    scope_auto_range_low_fraction: float = parameter(
        default=0.4,
        external_name="SCOPE_AUTO_RANGE_LOW_FRACTION",
        label="量程缩小阈值",
        visible=False,
        minimum=0.0,
        maximum=1.0,
    )
    scope_auto_range_high_fraction: float = parameter(
        default=0.9,
        external_name="SCOPE_AUTO_RANGE_HIGH_FRACTION",
        label="量程放大阈值",
        visible=False,
        minimum=0.0,
        maximum=1.0,
    )
    scope_auto_range_max_attempts: int = parameter(
        default=3,
        external_name="SCOPE_AUTO_RANGE_MAX_ATTEMPTS",
        label="自动量程最大尝试次数",
        visible=False,
        minimum=1,
        maximum=10,
    )
    scope_trigger_level_v: float = parameter(
        default=2.5,
        external_name="SCOPE_TRIGGER_LEVEL_V",
        label="示波器触发电平",
        unit="V",
        visible=False,
    )

    def validate_model(self) -> list[str]:
        errors: list[str] = []
        if not self.run_tag.strip():
            errors.append("RUN_TAG 不能为空")
        if not self.frequency_start_hz < self.frequency_stop_hz:
            errors.append("FREQUENCY_START_HZ 必须小于 FREQUENCY_STOP_HZ")
        if self.scope_measured_channel == self.scope_trigger_channel:
            errors.append("SCOPE_MEASURED_CHANNEL 与 SCOPE_TRIGGER_CHANNEL 必须不同")
        if self.scope_scale_min_v_div > self.scope_scale_max_v_div:
            errors.append("SCOPE_SCALE_MIN 必须小于等于 SCOPE_SCALE_MAX")
        if not self.scope_auto_range_low_fraction < self.scope_auto_range_high_fraction:
            errors.append(
                "SCOPE_AUTO_RANGE_LOW_FRACTION 必须小于 SCOPE_AUTO_RANGE_HIGH_FRACTION"
            )
        if not math.isclose(self.scope_trigger_level_v, 2.5, abs_tol=1e-12):
            errors.append("SCOPE_TRIGGER_LEVEL_V 必须为 2.5 V")

        try:
            validate_safety_limit(
                "Z_magnetic_field",
                self.drive_offset_v - self.drive_amplitude_vpp / 2.0,
            )
            validate_safety_limit(
                "Z_magnetic_field",
                self.drive_offset_v + self.drive_amplitude_vpp / 2.0,
            )
            trigger_low = self.trigger_offset_v - self.trigger_amplitude_vpp / 2.0
            trigger_high = self.trigger_offset_v + self.trigger_amplitude_vpp / 2.0
            validate_safety_limit("Time_sequence_2", trigger_low)
            validate_safety_limit("Time_sequence_2", trigger_high)
            validate_safety_limit("Time_sequence_2", self.scope_trigger_level_v)
        except ValueError as exc:
            errors.append(str(exc))
        return errors
