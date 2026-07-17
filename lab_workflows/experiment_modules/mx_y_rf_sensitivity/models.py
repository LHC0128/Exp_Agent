"""Mx 构型 Y 向 RF 场灵敏度实验参数。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from ...common import validate_safety_limit
from ...experiment_params import ExperimentParams, parameter


LinewidthMode = Literal["frequency_sweep", "amplitude_equivalent"]


@dataclass(slots=True)
class MxYRFParams(ExperimentParams):
    """主场/Pump 沿 Z、Probe 沿 X、待测 RF 沿 Y。"""

    schema_version = 2

    run_tag: str = parameter(default="mx_y_rf", external_name="RUN_TAG", label="运行标签", group="basic")
    linewidth_mode: LinewidthMode = parameter(
        default="amplitude_equivalent",
        external_name="LINEWIDTH_MODE",
        label="线宽模式",
        group="basic",
        options=(("frequency_sweep", "模式1：RF 扫频 HWHM"), ("amplitude_equivalent", "模式2：幅度等效 HWHM")),
    )
    y_rf_frequency_hz: float = parameter(default=10000.0, external_name="Y_RF_FREQUENCY_HZ", label="Y RF 固定频率", unit="Hz", group="basic", minimum=0.001)
    y_rf_amp_start_vpp: float = parameter(default=-0.2, external_name="Y_RF_AMP_START_VPP", label="Y RF 起始带符号幅度", unit="Vpp", group="basic", minimum=-2.0, maximum=2.0)
    y_rf_amp_stop_vpp: float = parameter(default=0.2, external_name="Y_RF_AMP_STOP_VPP", label="Y RF 终止带符号幅度", unit="Vpp", group="basic", minimum=-2.0, maximum=2.0)
    y_rf_amp_points: int = parameter(default=81, external_name="Y_RF_AMP_POINTS", label="Y RF 幅度点数", group="basic", minimum=5)
    response_settle_time_s: float = parameter(default=0.3, external_name="RESPONSE_SETTLE_TIME_S", label="幅度点稳定时间", unit="s", group="basic", minimum=0)
    response_duration_s: float = parameter(default=0.2, external_name="RESPONSE_DURATION_S", label="幅度点采样时间", unit="s", group="basic", minimum=0.001)
    y_rf_nt_per_vpp: float = parameter(default=0.0, external_name="Y_RF_NT_PER_VPP", label="Y RF 线圈标定系数", unit="nT/Vpp", group="basic", minimum=0)

    r_bad_point_std_threshold_v: float = parameter(default=0.01, external_name="R_BAD_POINT_STD_THRESHOLD_V", label="R 坏点标准差阈值", unit="V", group="advanced", minimum=0)
    r_point_max_attempts: int = parameter(default=3, external_name="R_POINT_MAX_ATTEMPTS", label="R 点最大采集次数", group="advanced", minimum=1)

    frequency_start_hz: float = parameter(default=8000.0, external_name="FREQUENCY_START_HZ", label="模式1扫频起点", unit="Hz", group="basic", minimum=0.001)
    frequency_stop_hz: float = parameter(default=12000.0, external_name="FREQUENCY_STOP_HZ", label="模式1扫频终点", unit="Hz", group="basic", minimum=0.001)
    frequency_points: int = parameter(default=201, external_name="FREQUENCY_POINTS", label="模式1扫频点数", group="basic", minimum=5)
    frequency_rf_amplitude_vpp: float = parameter(default=0.01, external_name="FREQUENCY_RF_AMPLITUDE_VPP", label="模式1扫频 RF 幅度", unit="Vpp", group="basic", minimum=0, maximum=2.0, safety_key="rf_coil")
    frequency_settle_time_s: float = parameter(default=0.05, external_name="FREQUENCY_SETTLE_TIME_S", label="模式1频点稳定时间", unit="s", group="advanced", minimum=0)
    frequency_duration_s: float = parameter(default=0.1, external_name="FREQUENCY_DURATION_S", label="模式1频点采样时间", unit="s", group="advanced", minimum=0.001)

    noise_n_avg: int = parameter(default=10, external_name="NOISE_N_AVG", label="噪声平均次数", group="basic", minimum=1)
    noise_duration_s: float = parameter(default=1.0, external_name="NOISE_DURATION_S", label="单次噪声时长", unit="s", group="basic", minimum=0.01)
    noise_rate_sa_s: float = parameter(default=50000.0, external_name="NOISE_RATE_SA_S", label="噪声请求采样率", unit="Sa/s", group="advanced", minimum=1)
    noise_time_constant_s: float = parameter(default=1e-6, external_name="NOISE_TIME_CONSTANT_S", label="噪声时间常数", unit="s", group="advanced", minimum=0)
    noise_demod_order: int = parameter(default=4, external_name="NOISE_DEMOD_ORDER", label="噪声解调阶数", group="advanced", minimum=1)
    low_freq_skip_hz: float = parameter(default=3.0, external_name="LOW_FREQ_SKIP_HZ", label="灵敏度低频跳过", unit="Hz", group="advanced", minimum=0)

    demod_idx: int = parameter(default=0, external_name="DEMOD_IDX", label="HF2 Demod 索引", group="advanced", minimum=0)
    demod_osc_idx: int = parameter(default=0, external_name="DEMOD_OSC_IDX", label="HF2 振荡器索引", group="advanced", minimum=0)
    hf2_signal_range_v: float = parameter(default=2.0, external_name="HF2_SIGNAL_RANGE_V", label="HF2 Input1 量程", unit="V", group="advanced", minimum=0.001)
    response_rate_sa_s: float = parameter(default=1000.0, external_name="RESPONSE_RATE_SA_S", label="响应请求采样率", unit="Sa/s", group="advanced", minimum=1)
    response_time_constant_s: float = parameter(default=0.001, external_name="RESPONSE_TIME_CONSTANT_S", label="响应时间常数", unit="s", group="advanced", minimum=0)
    response_demod_order: int = parameter(default=4, external_name="RESPONSE_DEMOD_ORDER", label="响应解调阶数", group="advanced", minimum=1)

    temp_switch_off_lead_s: float = parameter(default=0.1, external_name="TEMP_SWITCH_OFF_LEAD_S", label="温控关闭后等待", unit="s", group="advanced", minimum=0)
    temp_switch_on_lag_s: float = parameter(default=1.0, external_name="TEMP_SWITCH_ON_LAG_S", label="温控恢复后等待", unit="s", group="advanced", minimum=0)
    temperature_tolerance_c: float = parameter(default=1.0, external_name="TEMPERATURE_TOLERANCE_C", label="温度稳定容差", unit="°C", group="advanced", minimum=0)
    temperature_stable_reads: int = parameter(default=1, external_name="TEMPERATURE_STABLE_READS", label="温度连续稳定读数", group="advanced", minimum=1)
    temperature_poll_interval_s: float = parameter(default=5.0, external_name="TEMPERATURE_POLL_INTERVAL_S", label="温度轮询间隔", unit="s", group="advanced", minimum=0.1)
    temperature_timeout_s: float = parameter(default=1200.0, external_name="TEMPERATURE_TIMEOUT_S", label="温度稳定超时", unit="s", group="advanced", minimum=1)

    pump_carrier_frequency_hz: float = parameter(default=100e6, external_name="PUMP_CARRIER_FREQUENCY_HZ", label="Pump AOM 载波频率", unit="Hz", group="advanced", minimum=1)
    pump_carrier_amplitude_vpp: float = parameter(default=0.18, external_name="PUMP_CARRIER_AMPLITUDE_VPP", label="Pump AOM 载波幅度", unit="Vpp", group="advanced", minimum=0, maximum=0.18, safety_key="Pump_modulation")
    pump_gate_voltage_v: float = parameter(default=5.0, external_name="PUMP_GATE_VOLTAGE_V", label="Pump RF 开关常开电平", unit="V", group="advanced", minimum=5.0, maximum=5.0, safety_key="Time_sequence")
    pump_laser_power_v: float = parameter(default=0.5, external_name="FIXED_PARAMS.Pump_laser_power", label="Pump 光功率", unit="V", group="basic", safety_key="Pump_laser_power")
    probe_laser_power_v: float = parameter(default=0.2, external_name="FIXED_PARAMS.Probe_laser_power", label="Probe 光功率", unit="V", group="basic", safety_key="Probe_laser_power")
    main_magnetic_field_ma: float = parameter(default=1.03, external_name="FIXED_PARAMS.main_magnetic_field", label="Z 主磁场电流", unit="mA", group="basic", safety_key="main_magnetic_field")
    temperature_c: float = parameter(default=100.0, external_name="FIXED_PARAMS.temperature", label="气室温度", unit="°C", group="basic", safety_key="temperature")

    fit_r_squared_min: float = parameter(default=0.85, external_name="FIT_R_SQUARED_MIN", label="拟合最低 R²", group="advanced", minimum=0, maximum=1)
    fit_relative_gamma_uncertainty_max: float = parameter(default=0.5, external_name="FIT_RELATIVE_GAMMA_UNCERTAINTY_MAX", label="线宽最大相对不确定度", group="advanced", minimum=0)
    linear_check_gamma_fraction: float = parameter(default=0.25, external_name="LINEAR_CHECK_GAMMA_FRACTION", label="中心线性检查范围", unit="γ", group="advanced", minimum=0.01, maximum=1)
    slope_agreement_tolerance: float = parameter(default=0.2, external_name="SLOPE_AGREEMENT_TOLERANCE", label="两种斜率差异告警阈值", group="advanced", minimum=0)

    @classmethod
    def migrate_external(
        cls,
        values: dict[str, object],
        schema_version: int,
    ) -> dict[str, object]:
        migrated = super(MxYRFParams, cls).migrate_external(values, schema_version)
        if schema_version < 2:
            for key in (
                "PHASE_CAL_AMPLITUDE_VPP",
                "PHASE_CAL_SETTLE_TIME_S",
                "PHASE_CAL_DURATION_S",
                "PHASE_CAL_TOLERANCE_DEG",
                "PHASE_CAL_MAX_ATTEMPTS",
                "PHASE_CAL_MIN_DELTA_V",
            ):
                migrated.pop(key, None)
        return migrated

    def validate_model(self) -> list[str]:
        errors: list[str] = []
        if not self.run_tag.strip():
            errors.append("RUN_TAG 不能为空")
        if not self.y_rf_amp_start_vpp < 0 < self.y_rf_amp_stop_vpp:
            errors.append("Y RF 幅度范围必须跨过 0")
        if not math.isclose(abs(self.y_rf_amp_start_vpp), abs(self.y_rf_amp_stop_vpp), rel_tol=0, abs_tol=1e-12):
            errors.append("Y RF 幅度范围必须关于 0 对称")
        if self.y_rf_amp_points % 2 == 0:
            errors.append("Y RF 幅度点数必须为奇数以包含 0")
        for name, value in (
            ("Y_RF_AMP_START_VPP", abs(self.y_rf_amp_start_vpp)),
            ("Y_RF_AMP_STOP_VPP", abs(self.y_rf_amp_stop_vpp)),
        ):
            try:
                validate_safety_limit("rf_coil", value)
            except ValueError as exc:
                errors.append(f"{name}: {exc}")
        if self.frequency_start_hz >= self.frequency_stop_hz:
            errors.append("模式1扫频起点必须小于终点")
        if self.linewidth_mode == "frequency_sweep" and not self.frequency_start_hz < self.y_rf_frequency_hz < self.frequency_stop_hz:
            errors.append("模式1扫频范围必须包含 GUI 手动 Y RF 频率")
        if self.response_rate_sa_s * self.response_duration_s < 8:
            errors.append("幅度点预计采样数不足 8")
        if self.response_rate_sa_s * self.frequency_duration_s < 8:
            errors.append("频率点预计采样数不足 8")
        if self.noise_rate_sa_s * self.noise_duration_s < 8:
            errors.append("噪声记录预计采样数不足 8")
        return errors
