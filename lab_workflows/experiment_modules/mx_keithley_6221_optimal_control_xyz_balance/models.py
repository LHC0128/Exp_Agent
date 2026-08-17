"""Mx Keithley 6221 最优控制 XYZ 平衡场实验参数。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from ...common import find_project_root, validate_safety_limit
from ...experiment_params import ExperimentParams, parameter
from ...steps.keithley_6221 import (
    KEITHLEY_6221_CURRENT_RANGE_OPTIONS_MA,
    require_keithley_6221_current_range,
)
from ..mx_keithley_6221_optimal_control_rf_sensitivity.sources import (
    build_applied_current,
    load_keithley_calibration,
    load_theory_control,
)


@dataclass(slots=True)
class MxKeithley6221OptimalControlXYZBalanceParams(ExperimentParams):
    """6221 外部触发最优控制下扫描 XYZ 直流补偿场。"""

    schema_version = 1

    run_tag: str = parameter(
        default="mx_6221_optimal_control_xyz_balance",
        external_name="RUN_TAG",
        label="运行标签",
        group="basic",
    )
    x_field_start_v: float = parameter(
        default=-0.05,
        external_name="X_FIELD_START_V",
        label="X DC 起点",
        unit="V",
        group="basic",
        safety_key="X_magnetic_field",
    )
    x_field_stop_v: float = parameter(
        default=0.05,
        external_name="X_FIELD_STOP_V",
        label="X DC 终点",
        unit="V",
        group="basic",
        safety_key="X_magnetic_field",
    )
    x_field_points: int = parameter(
        default=11,
        external_name="X_FIELD_POINTS",
        label="X DC 点数",
        group="basic",
        minimum=1,
    )
    y_field_start_v: float = parameter(
        default=-0.05,
        external_name="Y_FIELD_START_V",
        label="Y DC 起点",
        unit="V",
        group="basic",
        safety_key="Y_magnetic_field",
    )
    y_field_stop_v: float = parameter(
        default=0.05,
        external_name="Y_FIELD_STOP_V",
        label="Y DC 终点",
        unit="V",
        group="basic",
        safety_key="Y_magnetic_field",
    )
    y_field_points: int = parameter(
        default=11,
        external_name="Y_FIELD_POINTS",
        label="Y DC 点数",
        group="basic",
        minimum=1,
    )
    z_field_start_ma: float = parameter(
        default=-0.02,
        external_name="Z_FIELD_START_MA",
        label="GS200 Z 起点",
        unit="mA",
        group="basic",
        safety_key="main_magnetic_field",
    )
    z_field_stop_ma: float = parameter(
        default=0.02,
        external_name="Z_FIELD_STOP_MA",
        label="GS200 Z 终点",
        unit="mA",
        group="basic",
        safety_key="main_magnetic_field",
    )
    z_field_points: int = parameter(
        default=5,
        external_name="Z_FIELD_POINTS",
        label="GS200 Z 点数",
        group="basic",
        minimum=1,
    )
    demod_frequency_hz: float = parameter(
        default=12000.0,
        external_name="DEMOD_FREQUENCY_HZ",
        label="HF2 Demod0 频率",
        unit="Hz",
        group="basic",
        minimum=0.001,
        read_only=True,
        description=(
            "等于理论 rf 频率，由 CONTROL_VERSION 自动计算，不能手动修改；"
            "GUI 在版本变化时自动刷新该只读显示。"
        ),
    )
    response_settle_time_s: float = parameter(
        default=0.1,
        external_name="RESPONSE_SETTLE_TIME_S",
        label="设场及温控关闭后等待",
        unit="s",
        group="basic",
        minimum=0.0,
    )
    response_duration_s: float = parameter(
        default=0.2,
        external_name="RESPONSE_DURATION_S",
        label="单点 R 采样时间",
        unit="s",
        group="basic",
        minimum=0.001,
    )
    r_bad_point_std_threshold_v: float = parameter(
        default=0.1,
        external_name="R_BAD_POINT_STD_THRESHOLD_V",
        label="R 点标准差拒绝门槛",
        unit="V",
        group="advanced",
        minimum=0.0,
    )
    r_point_max_attempts: int = parameter(
        default=3,
        external_name="R_POINT_MAX_ATTEMPTS",
        label="R 点最大采集次数",
        group="advanced",
        minimum=1,
    )
    demod_idx: int = parameter(
        default=0,
        external_name="DEMOD_IDX",
        label="HF2 解调器索引",
        visible=False,
        minimum=0,
    )
    demod_osc_idx: int = parameter(
        default=0,
        external_name="DEMOD_OSC_IDX",
        label="HF2 振荡器索引",
        visible=False,
        minimum=0,
    )
    hf2_signal_range_v: float = parameter(
        default=2.0,
        external_name="HF2_SIGNAL_RANGE_V",
        label="HF2 输入量程",
        unit="V",
        group="advanced",
        minimum=0.001,
    )
    response_rate_sa_s: float = parameter(
        default=1000.0,
        external_name="RESPONSE_RATE_SA_S",
        label="R 请求采样率",
        unit="Sa/s",
        group="advanced",
        minimum=1.0,
    )
    response_time_constant_s: float = parameter(
        default=0.001,
        external_name="RESPONSE_TIME_CONSTANT_S",
        label="R 解调时间常数",
        unit="s",
        group="advanced",
        minimum=0.0,
    )
    response_demod_order: int = parameter(
        default=4,
        external_name="RESPONSE_DEMOD_ORDER",
        label="R 解调滤波阶数",
        group="advanced",
        minimum=1,
    )
    temp_switch_off_lead_s: float = parameter(
        default=0.0,
        external_name="TEMP_SWITCH_OFF_LEAD_S",
        label="温控关闭附加等待",
        unit="s",
        group="advanced",
        minimum=0.0,
        description="与 RESPONSE_SETTLE_TIME_S 相加；默认不重复等待。",
    )
    temp_switch_on_lag_s: float = parameter(
        default=2.0,
        external_name="TEMP_SWITCH_ON_LAG_S",
        label="温控恢复后等待",
        unit="s",
        group="basic",
        minimum=0.0,
    )
    temperature_tolerance_c: float = parameter(
        default=1.0,
        external_name="TEMPERATURE_TOLERANCE_C",
        label="初始温度稳定容差",
        unit="°C",
        group="advanced",
        minimum=0.0,
    )
    temperature_stable_reads: int = parameter(
        default=1,
        external_name="TEMPERATURE_STABLE_READS",
        label="初始温度连续稳定读数",
        group="advanced",
        minimum=1,
    )
    temperature_poll_interval_s: float = parameter(
        default=5.0,
        external_name="TEMPERATURE_POLL_INTERVAL_S",
        label="温度轮询间隔",
        unit="s",
        group="advanced",
        minimum=0.1,
    )
    temperature_timeout_s: float = parameter(
        default=1200.0,
        external_name="TEMPERATURE_TIMEOUT_S",
        label="温度稳定超时",
        unit="s",
        group="advanced",
        minimum=1.0,
    )
    pump_carrier_frequency_hz: float = parameter(
        default=100000000.0,
        external_name="PUMP_CARRIER_FREQUENCY_HZ",
        label="Pump AOM 载波频率",
        unit="Hz",
        group="advanced",
        minimum=1.0,
    )
    pump_carrier_amplitude_vpp: float = parameter(
        default=0.18,
        external_name="PUMP_CARRIER_AMPLITUDE_VPP",
        label="Pump AOM 载波幅度",
        unit="Vpp",
        group="advanced",
        minimum=0.0,
        maximum=0.18,
        safety_key="Pump_modulation",
    )
    pump_gate_voltage_v: float = parameter(
        default=5.0,
        external_name="PUMP_GATE_VOLTAGE_V",
        label="Pump RF 开关常开电平",
        unit="V",
        group="advanced",
        minimum=5.0,
        maximum=5.0,
        safety_key="Time_sequence",
    )
    pump_laser_power_v: float = parameter(
        default=0.1,
        external_name="FIXED_PARAMS.Pump_laser_power",
        label="Pump 光功率",
        unit="V",
        group="basic",
        safety_key="Pump_laser_power",
    )
    probe_laser_power_v: float = parameter(
        default=0.1,
        external_name="FIXED_PARAMS.Probe_laser_power",
        label="Probe 光功率",
        unit="V",
        group="basic",
        safety_key="Probe_laser_power",
    )
    temperature_c: float = parameter(
        default=120.0,
        external_name="FIXED_PARAMS.temperature",
        label="气室温度",
        unit="°C",
        group="basic",
        safety_key="temperature",
    )
    control_version: str = parameter(
        default="v4",
        external_name="CONTROL_VERSION",
        label="最优控制版本",
        group="basic",
        description="按 vN 解析同版本的波形与理论参数文件；默认 v4（12 kHz 重复、12 kHz rf）。",
    )
    control_results_root: str = parameter(
        default=r"D:\Code\theory_agent\simulate\results\oc_sens",
        external_name="CONTROL_RESULTS_ROOT",
        label="最优控制结果根目录",
        group="advanced",
    )
    keithley_calibration_source_run: str = parameter(
        default="0813_183404_mx_6221_main_field_cal",
        external_name="KEITHLEY_CALIBRATION_SOURCE_RUN",
        label="6221 主场标定来源运行",
        group="basic",
    )
    control_scale: float = parameter(
        default=1.0,
        external_name="CONTROL_SCALE",
        label="控制幅度比例",
        group="basic",
        minimum=0.0,
    )
    keithley_current_range_ma: float = parameter(
        default=100.0,
        external_name="KEITHLEY_CURRENT_RANGE_MA",
        label="6221 电流源档位",
        unit="mA",
        group="basic",
        options=KEITHLEY_6221_CURRENT_RANGE_OPTIONS_MA,
        description=(
            "根据理论控制波形、6221 标定结果和 CONTROL_SCALE 计算完整电流包络；"
            "所选固定档位必须覆盖最大绝对控制电流。"
        ),
    )
    keithley_output_response: str = parameter(
        default="FAST",
        external_name="KEITHLEY_OUTPUT_RESPONSE",
        label="6221 输出响应",
        group="advanced",
        options=(("FAST", "FAST（适合高频任意波）"),),
        read_only=True,
    )
    keithley_compliance_v: float = parameter(
        default=15.0,
        external_name="KEITHLEY_COMPLIANCE_V",
        label="6221 Compliance",
        unit="V",
        group="advanced",
        minimum=0.1,
        maximum=105.0,
        read_only=True,
        description="固定 15 V；沿用 6221 RF 灵敏度实验的重复触发动态验收结果。",
    )
    keithley_trigger_line: int = parameter(
        default=1,
        external_name="KEITHLEY_TRIGGER_LINE",
        label="6221 Trigger Link 输入线",
        group="advanced",
        minimum=1,
        maximum=6,
        read_only=True,
    )
    trigger_frequency_hz: float = parameter(
        default=12000.0,
        external_name="TRIGGER_FREQUENCY_HZ",
        label="共同触发频率",
        unit="Hz",
        group="advanced",
        minimum=0.001,
        read_only=True,
        description=(
            "等于控制波形重复频率，由 CONTROL_VERSION 自动计算，不能手动修改；"
            "GUI 在版本变化时自动刷新该只读显示。"
        ),
    )
    trigger_amplitude_vpp: float = parameter(
        default=5.0,
        external_name="TRIGGER_AMPLITUDE_VPP",
        label="共同触发幅度",
        unit="Vpp",
        group="advanced",
        minimum=0.0,
        maximum=20.0,
        read_only=True,
    )
    trigger_offset_v: float = parameter(
        default=2.5,
        external_name="TRIGGER_OFFSET_V",
        label="共同触发偏置",
        unit="V",
        group="advanced",
        minimum=-10.0,
        maximum=10.0,
        read_only=True,
    )
    trigger_duty_percent: float = parameter(
        default=50.0,
        external_name="TRIGGER_DUTY_PERCENT",
        label="共同触发占空比",
        unit="%",
        group="advanced",
        minimum=0.1,
        maximum=99.9,
        read_only=True,
    )
    confirm_gs200_connected: bool = parameter(
        default=False,
        external_name="CONFIRM_GS200_PHYSICALLY_CONNECTED",
        label="确认 GS200 已接入 Z 主磁场线圈",
        group="basic",
        description="6221 接 Z 小磁场线圈，GS200 接 Z 主磁场线圈；必须明确确认接线正确。",
    )

    @property
    def main_magnetic_field_ma(self) -> float:
        """供共享初始化读取 GS200 Z 平衡场起点。"""
        return float(self.z_field_start_ma)

    @staticmethod
    def _validate_axis(
        name: str,
        start: float,
        stop: float,
        points: int,
    ) -> list[str]:
        if points == 1:
            if start != stop:
                return [f"{name} 点数为 1 时必须满足 START=STOP"]
            return []
        if start >= stop:
            return [f"{name} 点数大于 1 时必须满足 START<STOP"]
        return []

    def validate_model(self) -> list[str]:
        errors: list[str] = []
        if not self.run_tag.strip():
            errors.append("RUN_TAG 不能为空")
        for name, start, stop, points in (
            ("X", self.x_field_start_v, self.x_field_stop_v, self.x_field_points),
            ("Y", self.y_field_start_v, self.y_field_stop_v, self.y_field_points),
            ("Z", self.z_field_start_ma, self.z_field_stop_ma, self.z_field_points),
        ):
            errors.extend(self._validate_axis(name, start, stop, points))
        for safety_key, values in (
            ("X_magnetic_field", (self.x_field_start_v, self.x_field_stop_v)),
            ("Y_magnetic_field", (self.y_field_start_v, self.y_field_stop_v)),
            ("main_magnetic_field", (self.z_field_start_ma, self.z_field_stop_ma)),
        ):
            for value in values:
                try:
                    validate_safety_limit(safety_key, value)
                except ValueError as exc:
                    errors.append(str(exc))
        if self.control_scale <= 0:
            errors.append("CONTROL_SCALE 必须大于 0")
        if not self.confirm_gs200_connected:
            errors.append("必须确认 GS200 已接入 Z 主磁场线圈（6221 接 Z 小磁场线圈）")
        if self.keithley_output_response != "FAST":
            errors.append("6221 输出响应必须固定为 FAST")
        if self.keithley_compliance_v != 15.0:
            errors.append("6221 Compliance 必须固定为 15 V")
        if self.keithley_trigger_line != 1:
            errors.append("6221 外部触发必须固定使用 Trigger Link Line 1")
        if self.trigger_amplitude_vpp != 5.0 or self.trigger_offset_v != 2.5:
            errors.append("Time_sequence_2 触发必须固定为 5 Vpp、2.5 V offset")
        if self.trigger_duty_percent != 50.0:
            errors.append("Time_sequence_2 触发占空比必须固定为 50%")
        trigger_low = self.trigger_offset_v - self.trigger_amplitude_vpp / 2.0
        trigger_high = self.trigger_offset_v + self.trigger_amplitude_vpp / 2.0
        for value in (trigger_low, trigger_high):
            try:
                validate_safety_limit("Time_sequence_2", value)
            except ValueError as exc:
                errors.append(str(exc))
        root = find_project_root()
        try:
            theory = load_theory_control(
                Path(self.control_results_root),
                self.control_version,
            )
            calibration = load_keithley_calibration(
                root,
                self.keithley_calibration_source_run,
            )
            applied = build_applied_current(
                theory,
                calibration,
                self.control_scale,
            )
            try:
                require_keithley_6221_current_range(
                    self.keithley_current_range_ma,
                    applied.minimum_ma,
                    applied.maximum_ma,
                )
            except ValueError as exc:
                errors.append(str(exc))
            if not np.isclose(
                self.trigger_frequency_hz,
                theory.repeat_frequency_hz,
                rtol=1e-9,
                atol=1e-6,
            ):
                errors.append(
                    "TRIGGER_FREQUENCY_HZ 必须等于理论控制重复频率 "
                    f"{theory.repeat_frequency_hz:.9g} Hz"
                )
            if not np.isclose(
                self.demod_frequency_hz,
                theory.theory_rf_frequency_hz,
                rtol=1e-9,
                atol=1e-6,
            ):
                errors.append(
                    "DEMOD_FREQUENCY_HZ 必须等于理论 rf 频率 "
                    f"{theory.theory_rf_frequency_hz:.9g} Hz"
                )
        except (OSError, TypeError, ValueError, KeyError) as exc:
            errors.append(str(exc))
        return errors

    @classmethod
    def derive_external(cls, values: dict[str, Any]) -> dict[str, Any]:
        """按当前 CONTROL_VERSION 计算只读派生显示值（GUI 派生字段机制）。"""
        defaults = cls()
        root = Path(
            str(values.get("CONTROL_RESULTS_ROOT") or defaults.control_results_root)
        )
        version = str(values.get("CONTROL_VERSION") or defaults.control_version)
        try:
            theory = load_theory_control(root, version)
        except Exception:
            return {}
        return {
            "TRIGGER_FREQUENCY_HZ": theory.repeat_frequency_hz,
            "DEMOD_FREQUENCY_HZ": theory.theory_rf_frequency_hz,
        }
