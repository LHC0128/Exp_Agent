"""Bell Bloom Z 标定参数与无硬件副作用的扫描轴。"""

from dataclasses import dataclass
import math

import numpy as np

from ...experiment_params import ExperimentParams, parameter


@dataclass(slots=True)
class BellBloomZFieldCalibrationParams(ExperimentParams):
    schema_version = 1

    run_tag: str = parameter(default="bb_z_cal", external_name="RUN_TAG", label="运行标签", group="basic")
    z_bias_start_v: float = parameter(default=-0.2, external_name="Z_BIAS_START_V", label="Z 偏置起点", unit="V", group="basic", safety_key="Z_magnetic_field")
    z_bias_stop_v: float = parameter(default=0.2, external_name="Z_BIAS_STOP_V", label="Z 偏置终点", unit="V", group="basic", safety_key="Z_magnetic_field")
    z_bias_step_v: float = parameter(default=0.1, external_name="Z_BIAS_STEP_V", label="Z 偏置步进", unit="V", group="basic", minimum=0.000001)
    zero_bias_center_frequency_hz: float = parameter(default=90000.0, external_name="ZERO_BIAS_CENTER_FREQUENCY_HZ", label="零偏参考频率", unit="Hz", group="basic", minimum=1)
    z_initial_hz_per_v: float = parameter(default=26000.0, external_name="Z_INITIAL_HZ_PER_V", label="有符号预测斜率", unit="Hz/V", group="basic", description="仅定位扫频窗口；正值表示正电压提高频率，不作为标定结果。")
    frequency_half_width_hz: float = parameter(default=10000.0, external_name="FREQUENCY_HALF_WIDTH_HZ", label="扫频半宽", unit="Hz", group="basic", minimum=0.001)
    frequency_step_hz: float = parameter(default=500.0, external_name="FREQUENCY_STEP_HZ", label="扫频步进", unit="Hz", group="basic", minimum=0.001)
    main_magnetic_field_ma: float = parameter(default=9.275, external_name="FIXED_PARAMS.main_magnetic_field", label="主场电流", unit="mA", group="basic", safety_key="main_magnetic_field")
    pump_laser_power_v: float = parameter(default=0.1, external_name="FIXED_PARAMS.Pump_laser_power", label="Pump 光功率", unit="V", group="basic", safety_key="Pump_laser_power")
    probe_laser_power_v: float = parameter(default=0.1, external_name="FIXED_PARAMS.Probe_laser_power", label="Probe 光功率", unit="V", group="basic", safety_key="Probe_laser_power")
    temperature_c: float = parameter(default=100.0, external_name="FIXED_PARAMS.temperature", label="气室温度", unit="°C", group="basic", safety_key="temperature")
    pump_carrier_amplitude_vpp: float = parameter(default=0.18, external_name="PUMP_CARRIER_AMPLITUDE_VPP", label="100 MHz 载波幅度", unit="Vpp", group="basic", minimum=0, safety_key="Pump_modulation")
    pump_mod_duty: float = parameter(default=5.0, external_name="PUMP_MOD_DUTY", label="Pump 占空比", unit="%", group="basic", safety_key="PUMP_MOD_DUTY")
    frequency_settle_time_s: float = parameter(default=0.05, external_name="FREQUENCY_SETTLE_TIME_S", label="频点稳定时间", unit="s", minimum=0)
    frequency_duration_s: float = parameter(default=0.1, external_name="FREQUENCY_DURATION_S", label="频点采样时间", unit="s", minimum=0.001)
    temp_switch_off_lead_s: float = parameter(default=0.1, external_name="TEMP_SWITCH_OFF_LEAD_S", label="关温控后等待", unit="s", minimum=0)
    temp_switch_on_lag_s: float = parameter(default=2.0, external_name="TEMP_SWITCH_ON_LAG_S", label="恢复温控后等待", unit="s", minimum=0)
    r_bad_point_std_threshold_v: float = parameter(default=0.1, external_name="R_BAD_POINT_STD_THRESHOLD_V", label="R 标准差阈值", unit="V", minimum=0)
    r_point_max_attempts: int = parameter(default=3, external_name="R_POINT_MAX_ATTEMPTS", label="频点最大采集次数", minimum=1)
    hf2_signal_range_v: float = parameter(default=2.0, external_name="HF2_SIGNAL_RANGE_V", label="HF2 Input1 量程", unit="V", minimum=0.001)
    response_rate_sa_s: float = parameter(default=1000.0, external_name="RESPONSE_RATE_SA_S", label="HF2 请求采样率", unit="Sa/s", minimum=1)
    response_time_constant_s: float = parameter(default=0.001, external_name="RESPONSE_TIME_CONSTANT_S", label="HF2 时间常数", unit="s", minimum=0.000001)
    response_demod_order: int = parameter(default=4, external_name="RESPONSE_DEMOD_ORDER", label="HF2 解调阶数", minimum=1, maximum=8)
    temperature_tolerance_c: float = parameter(default=1.0, external_name="TEMPERATURE_TOLERANCE_C", label="温度稳定容差", unit="°C", minimum=0)
    temperature_stable_reads: int = parameter(default=3, external_name="TEMPERATURE_STABLE_READS", label="温度连续稳定读数", minimum=1)
    temperature_poll_interval_s: float = parameter(default=5.0, external_name="TEMPERATURE_POLL_INTERVAL_S", label="温度轮询间隔", unit="s", minimum=0.1)
    temperature_timeout_s: float = parameter(default=1200.0, external_name="TEMPERATURE_TIMEOUT_S", label="温度稳定超时", unit="s", minimum=1)
    linear_r_squared_min: float = parameter(default=0.8, external_name="LINEAR_R_SQUARED_MIN", label="线性标定最低 R²", minimum=0, maximum=1)

    def validate_model(self) -> list[str]:
        errors = []
        if not self.run_tag.strip() or any(char in self.run_tag for char in '/\\:*?"<>|') or self.run_tag in {".", ".."}:
            errors.append("运行标签不能为空或包含路径字符")
        for label, span, step in (
            ("Z 偏置", self.z_bias_stop_v - self.z_bias_start_v, self.z_bias_step_v),
            ("扫频", 2 * self.frequency_half_width_hz, self.frequency_step_hz),
        ):
            if span <= 0 or step <= 0:
                errors.append(f"{label}跨度和步长必须为正")
            elif not math.isclose(span / step, round(span / step), abs_tol=1e-9, rel_tol=0):
                errors.append(f"{label}跨度必须包含整数个步进")
        if self.frequency_step_hz > 0 and 2 * self.frequency_half_width_hz / self.frequency_step_hz < 4:
            errors.append("每条频扫至少需要 5 个点")
        centers = [self.predicted_center(v) for v in (self.z_bias_start_v, self.z_bias_stop_v)]
        if min(centers) - self.frequency_half_width_hz <= 0:
            errors.append("全部扫频点必须大于 0 Hz")
        if self.response_rate_sa_s * self.frequency_duration_s < 8:
            errors.append("每频点预计采样数不足 8")
        return errors

    def z_axis(self) -> np.ndarray:
        count = round((self.z_bias_stop_v - self.z_bias_start_v) / self.z_bias_step_v)
        return np.linspace(self.z_bias_start_v, self.z_bias_stop_v, count + 1)

    def predicted_center(self, voltage: float) -> float:
        return self.zero_bias_center_frequency_hz + self.z_initial_hz_per_v * voltage

    def frequency_axis(self, voltage: float) -> np.ndarray:
        center = self.predicted_center(voltage)
        count = round(2 * self.frequency_half_width_hz / self.frequency_step_hz)
        return np.linspace(center - self.frequency_half_width_hz, center + self.frequency_half_width_hz, count + 1)
