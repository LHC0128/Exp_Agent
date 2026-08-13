"""Mx Keithley 6221 主磁场频率标定参数。"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ...common import validate_safety_limit
from ...experiment_params import ExperimentParams, parameter
from ...steps.keithley_6221 import (
    KEITHLEY_6221_CURRENT_RANGE_OPTIONS_MA,
    require_keithley_6221_current_range,
)


@dataclass(slots=True)
class MxKeithley6221MainFieldCalibrationParams(ExperimentParams):
    """以 Keithley 6221 扫描 Z 主线圈并标定共振频率。"""

    schema_version = 1

    run_tag: str = parameter(
        default="mx_6221_main_field_cal",
        external_name="RUN_TAG",
        label="运行标签",
        group="basic",
    )
    confirm_gs200_disconnected: bool = parameter(
        default=False,
        external_name="CONFIRM_GS200_PHYSICALLY_DISCONNECTED",
        label="确认 GS200 已从 Z 主线圈物理断开",
        group="basic",
        description="必须明确确认；设置为 0 mA 或关闭输出不能替代物理断开。",
    )
    keithley_current_start_ma: float = parameter(
        default=5.0,
        external_name="KEITHLEY_CURRENT_START_MA",
        label="6221 电流起点",
        unit="mA",
        group="basic",
        safety_key="keithley_6221_main_field",
    )
    keithley_current_stop_ma: float = parameter(
        default=9.0,
        external_name="KEITHLEY_CURRENT_STOP_MA",
        label="6221 电流终点",
        unit="mA",
        group="basic",
        safety_key="keithley_6221_main_field",
    )
    keithley_current_step_ma: float = parameter(
        default=1.0,
        external_name="KEITHLEY_CURRENT_STEP_MA",
        label="6221 电流步进",
        unit="mA",
        group="basic",
        minimum=0.001,
    )
    keithley_current_range_ma: float = parameter(
        default=20.0,
        external_name="KEITHLEY_CURRENT_RANGE_MA",
        label="6221 电流源档位",
        unit="mA",
        group="basic",
        options=KEITHLEY_6221_CURRENT_RANGE_OPTIONS_MA,
        description="关闭自动量程后使用的固定电流档位，必须覆盖完整扫描电流范围。",
    )
    keithley_reference_current_ma: float = parameter(
        default=0.0,
        external_name="KEITHLEY_REFERENCE_CURRENT_MA",
        label="预测参考电流",
        unit="mA",
        group="basic",
        safety_key="keithley_6221_main_field",
    )
    keithley_reference_frequency_hz: float = parameter(
        default=0.0,
        external_name="KEITHLEY_REFERENCE_FREQUENCY_HZ",
        label="预测参考频率",
        unit="Hz",
        group="basic",
        minimum=0.0,
    )
    keithley_initial_hz_per_ma: float = parameter(
        default=10000.0,
        external_name="KEITHLEY_INITIAL_HZ_PER_MA",
        label="6221 初始频率斜率",
        unit="Hz/mA",
        group="basic",
        minimum=0.001,
        description="仅用于预测局部扫频中心，不作为最终标定结果。",
    )
    frequency_half_width_hz: float = parameter(
        default=10000.0,
        external_name="FREQUENCY_HALF_WIDTH_HZ",
        label="局部扫频半宽",
        unit="Hz",
        group="basic",
        minimum=0.001,
    )
    frequency_step_hz: float = parameter(
        default=500.0,
        external_name="FREQUENCY_STEP_HZ",
        label="局部扫频步进",
        unit="Hz",
        group="basic",
        minimum=0.001,
    )
    keithley_compliance_v: float = parameter(
        default=1.0,
        external_name="KEITHLEY_COMPLIANCE_V",
        label="6221 Compliance",
        unit="V",
        group="basic",
        minimum=1.0,
        maximum=1.0,
    )
    keithley_current_settle_time_s: float = parameter(
        default=0.5,
        external_name="KEITHLEY_CURRENT_SETTLE_TIME_S",
        label="6221 电流稳定时间",
        unit="s",
        group="basic",
        minimum=0.0,
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
    frequency_settle_time_s: float = parameter(default=0.05, external_name="FREQUENCY_SETTLE_TIME_S", label="频点稳定时间", unit="s", group="advanced", minimum=0.0)
    frequency_duration_s: float = parameter(default=0.1, external_name="FREQUENCY_DURATION_S", label="频点采样时间", unit="s", group="basic", minimum=0.001)
    temp_switch_off_lead_s: float = parameter(default=0.1, external_name="TEMP_SWITCH_OFF_LEAD_S", label="温控关闭后等待", unit="s", group="advanced", minimum=0.0)
    temp_switch_on_lag_s: float = parameter(default=1.0, external_name="TEMP_SWITCH_ON_LAG_S", label="每频点温控恢复等待", unit="s", group="basic", minimum=0.0)
    r_bad_point_std_threshold_v: float = parameter(default=0.01, external_name="R_BAD_POINT_STD_THRESHOLD_V", label="R 坏点标准差阈值", unit="V", group="advanced", minimum=0.0)
    r_point_max_attempts: int = parameter(default=3, external_name="R_POINT_MAX_ATTEMPTS", label="R 点最大采集次数", group="advanced", minimum=1)

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
    main_magnetic_field_ma: float = parameter(default=0.0, external_name="FIXED_PARAMS.main_magnetic_field", label="GS200 Z 主磁场电流", unit="mA", group="basic", minimum=0.0, maximum=0.0, safety_key="main_magnetic_field")
    temperature_c: float = parameter(default=120.0, external_name="FIXED_PARAMS.temperature", label="气室温度", unit="°C", group="basic", safety_key="temperature")
    gyromagnetic_ratio_hz_per_nt: float = parameter(default=7.0, external_name="GYROMAGNETIC_RATIO_HZ_PER_NT", label="频率-磁场换算系数", unit="Hz/nT", group="advanced", minimum=0.001)
    linear_r_squared_min: float = parameter(default=0.99, external_name="LINEAR_R_SQUARED_MIN", label="线性标定最低 R²", group="advanced", minimum=0.0, maximum=1.0)

    def validate_model(self) -> list[str]:
        errors: list[str] = []
        if not self.run_tag.strip():
            errors.append("RUN_TAG 不能为空")
        if not self.confirm_gs200_disconnected:
            errors.append("必须确认 GS200 已从 Z 主线圈物理断开")
        if self.main_magnetic_field_ma != 0.0:
            errors.append("FIXED_PARAMS.main_magnetic_field 必须严格为 0 mA")
        if self.keithley_current_start_ma >= self.keithley_current_stop_ma:
            errors.append("KEITHLEY_CURRENT_START_MA 必须小于 KEITHLEY_CURRENT_STOP_MA")
        try:
            require_keithley_6221_current_range(
                self.keithley_current_range_ma,
                self.keithley_current_start_ma,
                self.keithley_current_stop_ma,
            )
        except ValueError as exc:
            errors.append(str(exc))
        count = (self.keithley_current_stop_ma - self.keithley_current_start_ma) / self.keithley_current_step_ma
        if not math.isclose(count, round(count), rel_tol=0.0, abs_tol=1e-9):
            errors.append("6221 电流范围必须包含整数个步进")
        freq_count = 2.0 * self.frequency_half_width_hz / self.frequency_step_hz
        if not math.isclose(freq_count, round(freq_count), rel_tol=0.0, abs_tol=1e-9):
            errors.append("局部扫频宽度必须包含整数个频率步进")
        predictions = [
            self.keithley_reference_frequency_hz
            + self.keithley_initial_hz_per_ma * (value - self.keithley_reference_current_ma)
            for value in (self.keithley_current_start_ma, self.keithley_current_stop_ma)
        ]
        if min(predictions) - self.frequency_half_width_hz <= 0:
            errors.append("预测扫频下限必须大于 0 Hz")
        if max(predictions) + self.frequency_half_width_hz > 100000.0:
            errors.append("预测扫频上限不能超过 100 kHz")
        if self.response_rate_sa_s * self.frequency_duration_s < 8:
            errors.append("频率点预计采样数不足 8")
        for name, value in (
            ("KEITHLEY_CURRENT_START_MA", self.keithley_current_start_ma),
            ("KEITHLEY_CURRENT_STOP_MA", self.keithley_current_stop_ma),
            ("KEITHLEY_REFERENCE_CURRENT_MA", self.keithley_reference_current_ma),
        ):
            try:
                validate_safety_limit("keithley_6221_main_field", value)
            except ValueError as exc:
                errors.append(f"{name}: {exc}")
        return errors
