"""Mx XY 剩磁二维校准参数。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from ...experiment_params import ExperimentParams, parameter


@dataclass(slots=True)
class MxXYResidualFieldCalibrationParams(ExperimentParams):
    """直接拟合 PD 均值二维曲面以校准 X/Y 剩磁补偿。"""

    schema_version = 2

    run_tag: str = parameter(default="mx_xy_residual", external_name="RUN_TAG", label="运行标签", group="basic")
    scan_main_field_ma: float = parameter(default=0.0, external_name="SCAN_MAIN_FIELD_MA", label="扫描主场电流", unit="mA", group="basic", safety_key="main_magnetic_field")
    x_field_start_v: float = parameter(default=-0.05, external_name="X_FIELD_START_V", label="X 场起点", unit="V", group="basic", safety_key="X_magnetic_field")
    x_field_stop_v: float = parameter(default=0.05, external_name="X_FIELD_STOP_V", label="X 场终点", unit="V", group="basic", safety_key="X_magnetic_field")
    x_field_points: int = parameter(default=21, external_name="X_FIELD_POINTS", label="X 场点数", group="basic", minimum=3)
    y_field_start_v: float = parameter(default=-0.05, external_name="Y_FIELD_START_V", label="Y 场起点", unit="V", group="basic", safety_key="Y_magnetic_field")
    y_field_stop_v: float = parameter(default=0.05, external_name="Y_FIELD_STOP_V", label="Y 场终点", unit="V", group="basic", safety_key="Y_magnetic_field")
    y_field_points: int = parameter(default=21, external_name="Y_FIELD_POINTS", label="Y 场点数", group="basic", minimum=7)
    point_repeats: int = parameter(default=1, external_name="POINT_REPEATS", label="网格点重复次数", group="basic", minimum=1)

    scope_sample_rate_sa_s: float = parameter(default=20000.0, external_name="SCOPE_SAMPLE_RATE", label="示波器请求采样率", unit="Sa/s", group="basic", minimum=1.0)
    scope_duration_s: float = parameter(default=0.5, external_name="SCOPE_DURATION", label="每次记录时长", unit="s", group="basic", minimum=0.001)
    scope_pd_channel: int = parameter(default=1, external_name="SCOPE_PD_CHANNEL", label="PD 示波器通道", visible=False, minimum=1, maximum=4)
    scope_trigger_mode: Literal["AUTO"] = parameter(default="AUTO", external_name="SCOPE_TRIGGER_MODE", label="示波器触发模式", visible=False)
    scope_initial_scale_v_div: float = parameter(default=0.5, external_name="SCOPE_INITIAL_SCALE", label="示波器初始量程", unit="V/div", group="advanced", minimum=0.000001)
    scope_offset_v: float = parameter(default=0.0, external_name="SCOPE_OFFSET", label="示波器通道偏置", visible=False)
    scope_vertical_divisions: int = parameter(default=8, external_name="SCOPE_VERTICAL_DIVISIONS", label="示波器垂直总格数", visible=False, minimum=1)
    scope_scale_min_v_div: float = parameter(default=0.01, external_name="SCOPE_SCALE_MIN", label="自动量程下限", unit="V/div", visible=False, minimum=0.000001)
    scope_scale_max_v_div: float = parameter(default=10.0, external_name="SCOPE_SCALE_MAX", label="自动量程上限", unit="V/div", visible=False, minimum=0.000001)
    scope_auto_range_low_fraction: float = parameter(default=0.4, external_name="SCOPE_AUTO_RANGE_LOW_FRACTION", label="自动量程缩小阈值", visible=False, minimum=0.0, maximum=1.0)
    scope_auto_range_high_fraction: float = parameter(default=0.9, external_name="SCOPE_AUTO_RANGE_HIGH_FRACTION", label="自动量程放大阈值", visible=False, minimum=0.0, maximum=1.0)
    scope_auto_offset_tolerance_fraction: float = parameter(default=0.05, external_name="SCOPE_AUTO_OFFSET_TOLERANCE_FRACTION", label="自动偏置居中死区", visible=False, minimum=0.0, maximum=1.0)
    scope_auto_range_max_attempts: int = parameter(default=3, external_name="SCOPE_AUTO_RANGE_MAX_ATTEMPTS", label="自动量程最大尝试次数", visible=False, minimum=1)
    scope_minimum_points: int = parameter(default=8, external_name="SCOPE_MINIMUM_POINTS", label="完整记录最低点数", visible=False, minimum=2)

    temp_switch_off_settle_s: float = parameter(default=0.3, external_name="TEMP_SWITCH_OFF_SETTLE_S", label="温控关闭后等待", unit="s", group="basic", minimum=0.0)
    temp_switch_on_settle_s: float = parameter(default=1.0, external_name="TEMP_SWITCH_ON_SETTLE_S", label="温控恢复后等待", unit="s", group="basic", minimum=0.0)
    temperature_tolerance_c: float = parameter(default=1.0, external_name="TEMPERATURE_TOLERANCE_C", label="初始温度稳定容差", unit="°C", group="advanced", minimum=0.0)
    temperature_stable_reads: int = parameter(default=1, external_name="TEMPERATURE_STABLE_READS", label="初始温度连续稳定读数", group="advanced", minimum=1)
    temperature_poll_interval_s: float = parameter(default=5.0, external_name="TEMPERATURE_POLL_INTERVAL_S", label="温度轮询间隔", unit="s", group="advanced", minimum=0.1)
    temperature_timeout_s: float = parameter(default=1200.0, external_name="TEMPERATURE_TIMEOUT_S", label="温度稳定超时", unit="s", group="advanced", minimum=1.0)

    pump_carrier_frequency_hz: float = parameter(default=100e6, external_name="PUMP_CARRIER_FREQUENCY_HZ", label="Pump AOM 载波频率", unit="Hz", group="advanced", minimum=1.0)
    pump_carrier_amplitude_vpp: float = parameter(default=0.18, external_name="PUMP_CARRIER_AMPLITUDE_VPP", label="Pump AOM 载波幅度", unit="Vpp", group="advanced", minimum=0.0, maximum=0.18, safety_key="Pump_modulation")
    pump_gate_voltage_v: float = parameter(default=5.0, external_name="PUMP_GATE_VOLTAGE_V", label="Pump RF 开关常开电平", unit="V", group="advanced", minimum=5.0, maximum=5.0, safety_key="Time_sequence")
    pump_laser_power_v: float = parameter(default=0.5, external_name="FIXED_PARAMS.Pump_laser_power", label="Pump 光功率", unit="V", group="basic", safety_key="Pump_laser_power")
    probe_laser_power_v: float = parameter(default=0.3, external_name="FIXED_PARAMS.Probe_laser_power", label="Probe 光功率", unit="V", group="basic", safety_key="Probe_laser_power")
    temperature_c: float = parameter(default=120.0, external_name="FIXED_PARAMS.temperature", label="气室温度", unit="°C", group="basic", safety_key="temperature")

    @property
    def scope_requested_points(self) -> int:
        return int(self.scope_sample_rate_sa_s * self.scope_duration_s)

    @property
    def grid_points(self) -> int:
        return self.x_field_points * self.y_field_points

    @classmethod
    def migrate_external(
        cls, values: dict[str, Any], schema_version: int
    ) -> dict[str, Any]:
        if schema_version > cls.schema_version:
            raise ValueError(
                f"配置 schema_version={schema_version} 高于程序支持版本 "
                f"{cls.schema_version}"
            )
        migrated = dict(values)
        if schema_version <= 1:
            migrated.pop("REFERENCE_MAIN_FIELD_MA", None)
            migrated.pop("REFERENCE_REPEATS", None)
        return migrated

    def validate_model(self) -> list[str]:
        errors: list[str] = []
        if self.scope_offset_v != 0.0:
            errors.append("SCOPE_OFFSET 必须固定为 0 V")
        if not self.run_tag.strip():
            errors.append("RUN_TAG 不能为空")
        if self.x_field_start_v >= self.x_field_stop_v:
            errors.append("X 场范围必须满足起点 < 终点")
        if self.y_field_start_v >= self.y_field_stop_v:
            errors.append("Y 场范围必须满足起点 < 终点")
        if self.scope_requested_points < self.scope_minimum_points:
            errors.append(
                f"示波器请求点数 {self.scope_requested_points} 少于最低要求 "
                f"{self.scope_minimum_points}"
            )
        if self.scope_scale_min_v_div >= self.scope_scale_max_v_div:
            errors.append("示波器自动量程范围必须满足下限 < 上限")
        if self.scope_auto_range_low_fraction >= self.scope_auto_range_high_fraction:
            errors.append("自动量程缩小阈值必须小于放大阈值")
        return errors
