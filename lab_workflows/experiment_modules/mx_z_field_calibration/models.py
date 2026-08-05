"""Mx 高主场 Z 磁场频率标定参数。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from ...common import validate_safety_limit
from ...experiment_params import ExperimentParams, parameter


PredictionPolarity = Literal[
    "unselected",
    "positive_increases_frequency",
    "positive_decreases_frequency",
]


@dataclass(slots=True)
class MxZFieldCalibrationParams(ExperimentParams):
    """主场/Pump 沿 Z、Probe 沿 X，以 Y RF 共振频率标定 Z DC。"""

    schema_version = 2

    run_tag: str = parameter(
        default="mx_z_cal",
        external_name="RUN_TAG",
        label="运行标签",
        group="basic",
    )
    zero_bias_center_frequency_hz: float = parameter(
        default=90000.0,
        external_name="ZERO_BIAS_CENTER_FREQUENCY_HZ",
        label="Z 零偏预测中心",
        unit="Hz",
        group="basic",
        minimum=0.0,
    )
    z_initial_hz_per_v: float = parameter(
        default=10621.690594509037,
        external_name="Z_INITIAL_HZ_PER_V",
        label="Z 场初始频率斜率",
        unit="Hz/V",
        group="basic",
        minimum=0.001,
        description="仅用于预测每个 Z 偏置的局部扫频中心，不作为最终标定结果。",
    )
    z_prediction_polarity: PredictionPolarity = parameter(
        default="unselected",
        external_name="Z_PREDICTION_POLARITY",
        label="Z 电压预测极性",
        group="basic",
        options=(
            ("unselected", "请选择"),
            ("positive_increases_frequency", "正电压提高共振频率"),
            ("positive_decreases_frequency", "正电压降低共振频率"),
        ),
    )
    z_bias_start_v: float = parameter(
        default=-3.0,
        external_name="Z_BIAS_START_V",
        label="Z 偏置起点",
        unit="V",
        group="basic",
        safety_key="Z_magnetic_field",
    )
    z_bias_stop_v: float = parameter(
        default=3.0,
        external_name="Z_BIAS_STOP_V",
        label="Z 偏置终点",
        unit="V",
        group="basic",
        safety_key="Z_magnetic_field",
    )
    z_bias_step_v: float = parameter(
        default=1.0,
        external_name="Z_BIAS_STEP_V",
        label="Z 偏置步进",
        unit="V",
        group="basic",
        minimum=0.001,
    )
    frequency_half_width_hz: float = parameter(
        default=4000.0,
        external_name="FREQUENCY_HALF_WIDTH_HZ",
        label="局部扫频半宽",
        unit="Hz",
        group="basic",
        minimum=0.001,
    )
    frequency_step_hz: float = parameter(
        default=100.0,
        external_name="FREQUENCY_STEP_HZ",
        label="局部扫频步进",
        unit="Hz",
        group="basic",
        minimum=0.001,
    )
    y_rf_amplitude_vpp: float = parameter(
        default=0.05,
        external_name="Y_RF_AMPLITUDE_VPP",
        label="Y RF 扫频幅度",
        unit="Vpp",
        group="basic",
        minimum=0.0,
        maximum=2.0,
        safety_key="rf_coil",
    )
    frequency_settle_time_s: float = parameter(
        default=0.05,
        external_name="FREQUENCY_SETTLE_TIME_S",
        label="频点稳定时间",
        unit="s",
        group="advanced",
        minimum=0.0,
    )
    frequency_duration_s: float = parameter(
        default=0.1,
        external_name="FREQUENCY_DURATION_S",
        label="频点采样时间",
        unit="s",
        group="advanced",
        minimum=0.001,
    )
    temp_switch_off_lead_s: float = parameter(
        default=0.1,
        external_name="TEMP_SWITCH_OFF_LEAD_S",
        label="温控关闭后等待",
        unit="s",
        group="advanced",
        minimum=0.0,
    )
    temp_switch_on_lag_s: float = parameter(
        default=1.0,
        external_name="TEMP_SWITCH_ON_LAG_S",
        label="每频点温控恢复等待",
        unit="s",
        group="basic",
        minimum=0.0,
    )
    r_bad_point_std_threshold_v: float = parameter(
        default=0.01,
        external_name="R_BAD_POINT_STD_THRESHOLD_V",
        label="R 坏点标准差阈值",
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

    demod_idx: int = parameter(default=0, external_name="DEMOD_IDX", label="HF2 Demod 索引", group="advanced", minimum=0)
    demod_osc_idx: int = parameter(default=0, external_name="DEMOD_OSC_IDX", label="HF2 振荡器索引", group="advanced", minimum=0)
    hf2_signal_range_v: float = parameter(default=2.0, external_name="HF2_SIGNAL_RANGE_V", label="HF2 Input1 量程", unit="V", group="advanced", minimum=0.001)
    response_rate_sa_s: float = parameter(default=1000.0, external_name="RESPONSE_RATE_SA_S", label="响应请求采样率", unit="Sa/s", group="advanced", minimum=1.0)
    response_time_constant_s: float = parameter(default=0.001, external_name="RESPONSE_TIME_CONSTANT_S", label="响应时间常数", unit="s", group="advanced", minimum=0.0)
    response_demod_order: int = parameter(default=4, external_name="RESPONSE_DEMOD_ORDER", label="响应解调阶数", group="advanced", minimum=1)

    temperature_tolerance_c: float = parameter(default=1.0, external_name="TEMPERATURE_TOLERANCE_C", label="初始温度稳定容差", unit="°C", group="advanced", minimum=0.0)
    temperature_stable_reads: int = parameter(default=1, external_name="TEMPERATURE_STABLE_READS", label="初始温度连续稳定读数", group="advanced", minimum=1)
    temperature_poll_interval_s: float = parameter(default=5.0, external_name="TEMPERATURE_POLL_INTERVAL_S", label="温度轮询间隔", unit="s", group="advanced", minimum=0.1)
    temperature_timeout_s: float = parameter(default=1200.0, external_name="TEMPERATURE_TIMEOUT_S", label="温度稳定超时", unit="s", group="advanced", minimum=1.0)

    pump_carrier_frequency_hz: float = parameter(default=100e6, external_name="PUMP_CARRIER_FREQUENCY_HZ", label="Pump AOM 载波频率", unit="Hz", group="advanced", minimum=1.0)
    pump_carrier_amplitude_vpp: float = parameter(default=0.18, external_name="PUMP_CARRIER_AMPLITUDE_VPP", label="Pump AOM 载波幅度", unit="Vpp", group="advanced", minimum=0.0, maximum=0.18, safety_key="Pump_modulation")
    pump_gate_voltage_v: float = parameter(default=5.0, external_name="PUMP_GATE_VOLTAGE_V", label="Pump RF 开关常开电平", unit="V", group="advanced", minimum=5.0, maximum=5.0, safety_key="Time_sequence")
    pump_laser_power_v: float = parameter(default=0.5, external_name="FIXED_PARAMS.Pump_laser_power", label="Pump 光功率", unit="V", group="basic", safety_key="Pump_laser_power")
    probe_laser_power_v: float = parameter(default=0.3, external_name="FIXED_PARAMS.Probe_laser_power", label="Probe 光功率", unit="V", group="basic", safety_key="Probe_laser_power")
    main_magnetic_field_ma: float = parameter(default=9.3, external_name="FIXED_PARAMS.main_magnetic_field", label="Z 主磁场电流", unit="mA", group="basic", safety_key="main_magnetic_field")
    temperature_c: float = parameter(default=120.0, external_name="FIXED_PARAMS.temperature", label="气室温度", unit="°C", group="basic", safety_key="temperature")

    linear_r_squared_min: float = parameter(default=0.99, external_name="LINEAR_R_SQUARED_MIN", label="线性标定最低 R²", group="advanced", minimum=0.0, maximum=1.0)

    @classmethod
    def migrate_external(
        cls,
        values: dict[str, object],
        schema_version: int,
    ) -> dict[str, object]:
        """移除 schema v1 中已废弃的拟合质量门槛。"""
        migrated = super(MxZFieldCalibrationParams, cls).migrate_external(
            values, schema_version
        )
        if schema_version < 2:
            for key in (
                "FIT_R_SQUARED_MIN",
                "FIT_CENTER_UNCERTAINTY_MAX_HZ",
                "FIT_RELATIVE_GAMMA_UNCERTAINTY_MAX",
                "MIN_VALID_Z_POINTS",
                "MIN_VALID_Z_SPAN_V",
            ):
                migrated.pop(key, None)
        return migrated

    def validate_model(self) -> list[str]:
        errors: list[str] = []
        if not self.run_tag.strip():
            errors.append("RUN_TAG 不能为空")
        if self.z_prediction_polarity == "unselected":
            errors.append("Z_PREDICTION_POLARITY 必须明确选择")
        if self.z_bias_start_v >= self.z_bias_stop_v:
            errors.append("Z_BIAS_START_V 必须小于 Z_BIAS_STOP_V")
        span = self.z_bias_stop_v - self.z_bias_start_v
        count = span / self.z_bias_step_v
        if not math.isclose(count, round(count), rel_tol=0.0, abs_tol=1e-9):
            errors.append("Z 偏置范围必须包含整数个步进")
        freq_count = 2.0 * self.frequency_half_width_hz / self.frequency_step_hz
        if not math.isclose(freq_count, round(freq_count), rel_tol=0.0, abs_tol=1e-9):
            errors.append("局部扫频宽度必须包含整数个频率步进")
        sign = 1.0 if self.z_prediction_polarity != "positive_decreases_frequency" else -1.0
        predictions = [
            self.zero_bias_center_frequency_hz + sign * self.z_initial_hz_per_v * value
            for value in (self.z_bias_start_v, self.z_bias_stop_v)
        ]
        if min(predictions) - self.frequency_half_width_hz <= 0:
            errors.append("预测扫频下限必须大于 0 Hz")
        if self.response_rate_sa_s * self.frequency_duration_s < 8:
            errors.append("频率点预计采样数不足 8")
        for name, value in (("Z_BIAS_START_V", self.z_bias_start_v), ("Z_BIAS_STOP_V", self.z_bias_stop_v)):
            try:
                validate_safety_limit("Z_magnetic_field", value)
            except ValueError as exc:
                errors.append(f"{name}: {exc}")
        return errors
