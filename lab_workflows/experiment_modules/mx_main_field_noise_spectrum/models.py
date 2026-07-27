"""Mx 主磁场控制噪声谱参数。"""

from __future__ import annotations

from dataclasses import dataclass

from ...common import validate_safety_limit
from ...experiment_params import ExperimentParams, parameter


@dataclass(slots=True)
class MxMainFieldNoiseSpectrumParams(ExperimentParams):
    """固定 HF2 参考频率，扫描 GS200 主场并采集 Demod0 R。"""

    schema_version = 1

    run_tag: str = parameter(default="mx_main_field_noise", external_name="RUN_TAG", label="运行标签", group="basic")
    control_frequency_start_hz: float = parameter(default=0.0, external_name="CONTROL_FREQUENCY_START_HZ", label="控制频率起点", unit="Hz", group="basic", minimum=0.0)
    control_frequency_stop_hz: float = parameter(default=50000.0, external_name="CONTROL_FREQUENCY_STOP_HZ", label="控制频率终点", unit="Hz", group="basic", minimum=0.0)
    control_frequency_points: int = parameter(default=500, external_name="CONTROL_FREQUENCY_POINTS", label="控制频率点数", group="basic", minimum=10)
    hf2_reference_frequency_hz: float = parameter(default=90000.0, external_name="HF2_REFERENCE_FREQUENCY_HZ", label="HF2 固定参考频率", unit="Hz", group="basic", minimum=0.001)
    main_field_calibration_hz_per_ma: float = parameter(default=9671.91741380711, external_name="MAIN_FIELD_CALIBRATION_HZ_PER_MA", label="主场标定斜率", unit="Hz/mA", group="basic", minimum=0.001)
    main_field_calibration_intercept_hz: float = parameter(default=196.65637261343872, external_name="MAIN_FIELD_CALIBRATION_INTERCEPT_HZ", label="主场标定截距", unit="Hz", group="basic")
    main_field_calibration_source_run: str = parameter(default="0720_124208_mx_main_field_cal", external_name="MAIN_FIELD_CALIBRATION_SOURCE_RUN", label="主场标定来源运行", group="advanced")
    main_field_calibration_min_ma: float = parameter(default=3.0, external_name="MAIN_FIELD_CALIBRATION_MIN_MA", label="标定有效电流下限", unit="mA", group="advanced", safety_key="main_magnetic_field")
    main_field_calibration_max_ma: float = parameter(default=10.0, external_name="MAIN_FIELD_CALIBRATION_MAX_MA", label="标定有效电流上限", unit="mA", group="advanced", safety_key="main_magnetic_field")

    acquisition_duration_s: float = parameter(default=1.0, external_name="ACQUISITION_DURATION_S", label="每点采集时长", unit="s", group="basic", minimum=0.001)
    requested_rate_sa_s: float = parameter(default=100000.0, external_name="REQUESTED_RATE_SA_S", label="HF2 请求采样率", unit="Sa/s", group="basic", minimum=1.0)
    welch_nperseg: int = parameter(default=10000, external_name="WELCH_NPERSEG", label="Welch 每段点数", group="advanced", minimum=8)
    temp_switch_off_settle_s: float = parameter(default=0.3, external_name="TEMP_SWITCH_OFF_SETTLE_S", label="温控关闭后统一等待", unit="s", group="basic", minimum=0.0)
    temp_switch_on_settle_s: float = parameter(default=1.0, external_name="TEMP_SWITCH_ON_SETTLE_S", label="温控恢复后等待", unit="s", group="basic", minimum=0.0)

    demod_idx: int = parameter(default=0, external_name="DEMOD_IDX", label="HF2 Demod 索引", group="advanced", minimum=0)
    demod_osc_idx: int = parameter(default=0, external_name="DEMOD_OSC_IDX", label="HF2 振荡器索引", group="advanced", minimum=0)
    hf2_signal_range_v: float = parameter(default=2.0, external_name="HF2_SIGNAL_RANGE_V", label="HF2 Input1 量程", unit="V", group="advanced", minimum=0.001)
    demod_time_constant_s: float = parameter(default=1.0e-6, external_name="DEMOD_TIME_CONSTANT_S", label="噪声解调时间常数", unit="s", group="advanced", minimum=0.0)
    demod_order: int = parameter(default=4, external_name="DEMOD_ORDER", label="噪声解调阶数", group="advanced", minimum=1)

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

    fit_peak_margin_hz: float = parameter(default=10000.0, external_name="FIT_PEAK_MARGIN_HZ", label="拟合脊线半窗口", unit="Hz", group="advanced", minimum=0.001)
    fit_gamma_guess_hz: float = parameter(default=300.0, external_name="FIT_GAMMA_GUESS_HZ", label="拟合线宽初值", unit="Hz", group="advanced", minimum=0.001)

    def current_for_control_frequency(self, control_frequency_hz: float) -> float:
        """按实测主场标定把正控制频率换算为 GS200 电流。"""
        resonance_hz = self.hf2_reference_frequency_hz - float(control_frequency_hz)
        return (
            resonance_hz - self.main_field_calibration_intercept_hz
        ) / self.main_field_calibration_hz_per_ma

    def validate_model(self) -> list[str]:
        errors: list[str] = []
        if not self.run_tag.strip():
            errors.append("RUN_TAG 不能为空")
        if not self.main_field_calibration_source_run.strip():
            errors.append("MAIN_FIELD_CALIBRATION_SOURCE_RUN 不能为空")
        if self.control_frequency_start_hz >= self.control_frequency_stop_hz:
            errors.append("控制频率范围必须满足起点 < 终点")
        if self.control_frequency_stop_hz >= self.hf2_reference_frequency_hz:
            errors.append("控制频率终点必须小于 HF2 固定参考频率")
        if self.control_frequency_stop_hz > self.requested_rate_sa_s / 2.0:
            errors.append("控制频率终点不能超过请求采样率的 Nyquist 频率")
        if self.welch_nperseg > int(self.acquisition_duration_s * self.requested_rate_sa_s):
            errors.append("WELCH_NPERSEG 不能超过请求采样点数")
        if self.main_field_calibration_min_ma >= self.main_field_calibration_max_ma:
            errors.append("主场标定有效电流范围必须满足下限 < 上限")
        for name, control_frequency in (
            ("CONTROL_FREQUENCY_START_HZ", self.control_frequency_start_hz),
            ("CONTROL_FREQUENCY_STOP_HZ", self.control_frequency_stop_hz),
        ):
            current_ma = self.current_for_control_frequency(control_frequency)
            try:
                validate_safety_limit("main_magnetic_field", current_ma)
            except ValueError as exc:
                errors.append(f"{name}: {exc}")
            if not (
                self.main_field_calibration_min_ma
                <= current_ma
                <= self.main_field_calibration_max_ma
            ):
                errors.append(
                    f"{name}: 反算电流 {current_ma:.9g} mA 超出标定有效范围 "
                    f"[{self.main_field_calibration_min_ma}, "
                    f"{self.main_field_calibration_max_ma}] mA"
                )
        return errors
