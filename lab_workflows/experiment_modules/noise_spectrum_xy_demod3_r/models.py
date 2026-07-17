"""XY DirectAW Demod3 R 噪声谱强类型参数。"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ...experiment_params import ExperimentParams, parameter


@dataclass(slots=True)
class NoiseSpectrumXYDemod3RParams(ExperimentParams):
    run_tag: str = parameter(default="demod3_r", external_name="RUN_TAG", label="运行标签", group="basic")
    control_freq_start_hz: float = parameter(default=200.0, external_name="CONTROL_FREQ_START_HZ", label="控制频率起点", unit="Hz", group="basic", minimum=0)
    control_freq_stop_hz: float = parameter(default=20000.0, external_name="CONTROL_FREQ_STOP_HZ", label="控制频率终点", unit="Hz", group="basic", minimum=0)
    control_freq_points: int = parameter(default=200, external_name="CONTROL_FREQ_POINTS", label="控制频率点数", group="basic", minimum=2)
    demod3_freq_start_hz: float = parameter(default=200.0, external_name="DEMOD3_FREQ_START_HZ", label="Demod3 解调频率起点", unit="Hz", group="basic", minimum=0.001)
    demod3_freq_stop_hz: float = parameter(default=20000.0, external_name="DEMOD3_FREQ_STOP_HZ", label="Demod3 解调频率终点", unit="Hz", group="basic", minimum=0.001)
    demod3_freq_points: int = parameter(default=200, external_name="DEMOD3_FREQ_POINTS", label="Demod3 解调频率点数", group="basic", minimum=2)
    demod3_freq_settle_time_s: float = parameter(default=0.2, external_name="DEMOD3_FREQ_SETTLE_TIME_S", label="Demod3 切频稳定时间", unit="s", group="basic", minimum=0)
    demod3_acquisition_duration_s: float = parameter(default=0.1, external_name="DEMOD3_ACQUISITION_DURATION_S", label="Demod3 连续采集时长", unit="s", group="basic", minimum=0.001)
    temp_recovery_batch_points: int = parameter(default=25, external_name="TEMP_RECOVERY_BATCH_POINTS", label="每批 Demod3 点数", group="basic", minimum=1)
    temp_recovery_time_s: float = parameter(default=2.0, external_name="TEMP_RECOVERY_TIME_S", label="每批温控恢复时间", unit="s", group="basic", minimum=0)

    pump_mod_freq: float = parameter(default=10000.0, external_name="PUMP_MOD_FREQ", label="Pump 调制频率", unit="Hz", group="basic", minimum=0.001)
    pump_mod_amplitude: float = parameter(default=0.18, external_name="PUMP_MOD_AMPLITUDE", label="Pump 载波幅度", unit="Vpp", group="basic", safety_key="Pump_modulation")
    pump_mod_duty: float = parameter(default=5.0, external_name="PUMP_MOD_DUTY", label="Pump 占空比", unit="%", group="basic", minimum=1, maximum=50)
    rf_gate_amplitude: float = parameter(default=5.0, external_name="RF_GATE_AMPLITUDE", label="RF 门控幅度", unit="Vpp", group="advanced", minimum=0)
    rf_gate_offset: float = parameter(default=2.5, external_name="RF_GATE_OFFSET", label="RF 门控偏置", unit="V", group="advanced")

    xy_ctrl_freq: float = parameter(default=10000.0, external_name="XY_CTRL_FREQ", label="XY 控制载波频率", unit="Hz", group="basic", minimum=0.001)
    xy_ctrl_phase: float = parameter(default=90.0, external_name="XY_CTRL_PHASE", label="XY 整体相位", unit="deg", group="advanced")
    xy_ctrl_quad: float = parameter(default=90.0, external_name="XY_CTRL_QUAD", label="XY 正交相位差", unit="deg", group="advanced")
    xy_calib_envelope_v: float = parameter(default=1.0, external_name="XY_CALIB_ENVELOPE_V", label="DirectAW 校相包络", unit="V", group="advanced", minimum=0)
    xy_phase_cal_tol_deg: float = parameter(default=1.0, external_name="XY_PHASE_CAL_TOL_DEG", label="DirectAW 校相容差", unit="deg", group="advanced", minimum=0)
    xy_phase_cal_max_iter: int = parameter(default=10, external_name="XY_PHASE_CAL_MAX_ITER", label="DirectAW 校相最大测量次数", group="advanced", minimum=1)
    xy_phase_cal_min_r_v: float = parameter(default=1e-12, external_name="XY_PHASE_CAL_MIN_R_V", label="DirectAW 校相最低 R", unit="V", group="advanced", minimum=0)
    xy_phase_cal_min_r_ratio: float = parameter(default=0.1, external_name="XY_PHASE_CAL_MIN_R_RATIO", label="DirectAW 校相最低 R 比例", group="advanced", minimum=0, maximum=1)
    xy_ctrl_k_hz_per_v: float = parameter(default=15661.042, external_name="XY_CTRL_K_HZ_PER_V", label="DirectAW 标定斜率 K", unit="Hz/V", group="advanced", minimum=0.001)
    xy_ctrl_b_hz: float = parameter(default=-162.38, external_name="XY_CTRL_B_HZ", label="DirectAW 标定截距 B", unit="Hz", group="advanced")
    xy_calibration_min_envelope_v: float = parameter(default=0.0, external_name="XY_CALIBRATION_MIN_ENVELOPE_V", label="标定有效最小包络", unit="V", group="advanced", minimum=0)
    xy_calibration_max_envelope_v: float = parameter(default=4.0, external_name="XY_CALIBRATION_MAX_ENVELOPE_V", label="标定有效最大包络", unit="V", group="advanced", minimum=0)
    xy_aw_output_vpp: float = parameter(default=8.0, external_name="XY_AW_OUTPUT_VPP", label="固定 AW 输出幅度", unit="Vpp", group="basic", minimum=0.001)
    xy_aw_output_offset_v: float = parameter(default=0.0, external_name="XY_AW_OUTPUT_OFFSET_V", label="固定 AW 输出偏置", unit="V", group="basic")
    xy_aw_repeat_freq_hz: float = parameter(default=500.0, external_name="XY_AW_REPEAT_FREQ_HZ", label="AW 重复频率", unit="Hz", group="advanced", minimum=0.001)
    xy_aw_points: int = parameter(default=10000, external_name="XY_AW_POINTS", label="AW 波形点数", group="advanced", minimum=2, maximum=16384)
    xy_trigger_freq: float = parameter(default=100.0, external_name="XY_TRIGGER_FREQ", label="XY 外触发频率", unit="Hz", group="advanced", minimum=0.001)
    xy_trigger_amplitude: float = parameter(default=5.0, external_name="XY_TRIGGER_AMPLITUDE", label="XY 外触发幅度", unit="Vpp", group="advanced", minimum=0)
    xy_trigger_offset: float = parameter(default=2.5, external_name="XY_TRIGGER_OFFSET", label="XY 外触发偏置", unit="V", group="advanced")
    xy_trigger_duty: float = parameter(default=50.0, external_name="XY_TRIGGER_DUTY", label="XY 外触发占空比", unit="%", group="advanced", minimum=0.001, maximum=100)
    xy_trigger_phase: float = parameter(default=0.0, external_name="XY_TRIGGER_PHASE", label="XY 外触发相位", unit="deg", group="advanced")

    demod0_idx: int = parameter(default=0, external_name="DEMOD0_IDX", label="Demod0 索引", group="advanced", minimum=0)
    demod0_osc_idx: int = parameter(default=0, external_name="DEMOD0_OSC_IDX", label="Demod0 振荡器", group="advanced", minimum=0)
    demod0_signal_range_v: float = parameter(default=2.0, external_name="DEMOD0_SIGNAL_RANGE_V", label="Demod0 输入量程", unit="V", group="advanced", minimum=0.001)
    demod0_order: int = parameter(default=8, external_name="DEMOD0_ORDER", label="Demod0 阶数", group="advanced", minimum=1)
    demod0_tc_calib_s: float = parameter(default=0.000692, external_name="DEMOD0_TC_CALIB_S", label="Demod0 校相时间常数", unit="s", group="advanced", minimum=0)
    demod0_rate_sa_s: float = parameter(default=100000.0, external_name="DEMOD0_RATE_SA_S", label="Demod0 采样率", unit="Sa/s", group="advanced", minimum=1)

    demod3_idx: int = parameter(default=3, external_name="DEMOD3_IDX", label="Demod3 索引", group="advanced", minimum=0)
    demod3_osc_idx: int = parameter(default=1, external_name="DEMOD3_OSC_IDX", label="Demod3 振荡器", group="advanced", minimum=0)
    demod3_adc_select: int = parameter(default=1, external_name="DEMOD3_ADC_SELECT", label="Demod3 输入选择", group="advanced", minimum=0)
    demod3_order: int = parameter(default=8, external_name="DEMOD3_ORDER", label="Demod3 阶数", group="advanced", minimum=1)
    demod3_rate_sa_s: float = parameter(default=4800.0, external_name="DEMOD3_RATE_SA_S", label="Demod3 采样率", unit="Sa/s", group="advanced", minimum=1)
    demod3_tc_min_s: float = parameter(default=0.01, external_name="DEMOD3_TC_MIN_S", label="Demod3 最小时间常数", unit="s", group="advanced", minimum=0)
    demod3_tc_period_fraction: float = parameter(default=0.05, external_name="DEMOD3_TC_PERIOD_FRACTION", label="Demod3 时间常数周期比例", group="advanced", minimum=0)

    auxout_index: int = parameter(default=1, external_name="AUXOUT_INDEX", label="AuxOut 索引", group="advanced", minimum=0)
    auxout_source_demod_idx: int = parameter(default=0, external_name="AUXOUT_SOURCE_DEMOD_IDX", label="AuxOut Demod 来源", group="advanced", minimum=0)
    auxout_source_select: int = parameter(default=1, external_name="AUXOUT_SOURCE_SELECT", label="AuxOut 信号选择", group="advanced", minimum=0, maximum=3)
    auxout_scale: float = parameter(default=1.0, external_name="AUXOUT_SCALE", label="AuxOut 比例", group="advanced")
    auxout_offset_v: float = parameter(default=0.0, external_name="AUXOUT_OFFSET_V", label="AuxOut 偏置", unit="V", group="advanced")
    sigin2_index: int = parameter(default=1, external_name="SIGIN2_INDEX", label="Signal Input 2 索引", group="advanced", minimum=0)
    sigin2_range_v: float = parameter(default=1.0, external_name="SIGIN2_RANGE_V", label="Signal Input 2 量程", unit="V", group="advanced", minimum=0.001)
    sigin2_ac_coupling: bool = parameter(default=False, external_name="SIGIN2_AC_COUPLING", label="Signal Input 2 AC 耦合", group="advanced")
    sigin2_impedance_ohm: int = parameter(default=50, external_name="SIGIN2_IMPEDANCE_OHM", label="Signal Input 2 阻抗", unit="Ω", group="advanced", minimum=1)

    temperature_tolerance_c: float = parameter(default=1.0, external_name="TEMPERATURE_TOLERANCE_C", label="温度稳定容差", unit="°C", group="advanced", minimum=0)
    temperature_stable_reads: int = parameter(default=1, external_name="TEMPERATURE_STABLE_READS", label="温度连续稳定读数", group="advanced", minimum=1)
    temperature_poll_interval_s: float = parameter(default=5.0, external_name="TEMPERATURE_POLL_INTERVAL_S", label="温度轮询间隔", unit="s", group="advanced", minimum=0.1)
    temperature_timeout_s: float = parameter(default=1200.0, external_name="TEMPERATURE_TIMEOUT_S", label="温度稳定超时", unit="s", group="advanced", minimum=1)
    pump_laser_power: float = parameter(default=0.1, external_name="FIXED_PARAMS.Pump_laser_power", label="Pump 光功率", unit="V", group="basic", safety_key="Pump_laser_power")
    probe_laser_power: float = parameter(default=0.1, external_name="FIXED_PARAMS.Probe_laser_power", label="Probe 光功率", unit="V", group="basic", safety_key="Probe_laser_power")
    temperature: float = parameter(default=100.0, external_name="FIXED_PARAMS.temperature", label="气室温度", unit="°C", group="basic", safety_key="temperature")
    temp_switch: float = parameter(default=5.0, external_name="FIXED_PARAMS.Temp_Switch", label="温控开关电平", unit="V", group="advanced", safety_key="Temp_Switch")
    main_magnetic_field: float = parameter(default=1.03, external_name="FIXED_PARAMS.main_magnetic_field", label="主磁场电流", unit="mA", group="basic", safety_key="main_magnetic_field")

    def validate_model(self) -> list[str]:
        errors: list[str] = []
        if not self.run_tag.strip():
            errors.append("RUN_TAG 不能为空")
        if self.control_freq_start_hz >= self.control_freq_stop_hz:
            errors.append("控制频率范围必须满足起点 < 终点")
        if self.demod3_freq_start_hz >= self.demod3_freq_stop_hz:
            errors.append("Demod3 解调频率范围必须满足起点 < 终点")
        if self.temp_recovery_batch_points > self.demod3_freq_points:
            errors.append("TEMP_RECOVERY_BATCH_POINTS 不能超过 Demod3 频率点数")
        if self.demod3_acquisition_duration_s * self.demod3_rate_sa_s < 1:
            errors.append("Demod3 单次连续采集预计不足 1 个样本")
        if self.xy_calibration_min_envelope_v >= self.xy_calibration_max_envelope_v:
            errors.append("DirectAW 标定有效电压范围无效")
        envelope = [
            (self.control_freq_start_hz - self.xy_ctrl_b_hz) / self.xy_ctrl_k_hz_per_v,
            (self.control_freq_stop_hz - self.xy_ctrl_b_hz) / self.xy_ctrl_k_hz_per_v,
        ]
        envelope_min, envelope_max = min(envelope), max(envelope)
        if envelope_min < 0:
            errors.append("控制频率反算得到负的 |V_env|")
        if envelope_min < self.xy_calibration_min_envelope_v or envelope_max > self.xy_calibration_max_envelope_v:
            errors.append(
                f"控制频率反算得到 V_env=[{envelope_min:.6f}, {envelope_max:.6f}] V，超出标定有效范围"
            )
        output_low = self.xy_aw_output_offset_v - self.xy_aw_output_vpp / 2
        output_high = self.xy_aw_output_offset_v + self.xy_aw_output_vpp / 2
        required = max(envelope_max, self.xy_calib_envelope_v)
        if -required < output_low or required > output_high:
            errors.append(f"DirectAW 包络超出固定 AW 可表达范围 [{output_low}, {output_high}] V")
        if not self.xy_calibration_min_envelope_v <= self.xy_calib_envelope_v <= self.xy_calibration_max_envelope_v:
            errors.append("DirectAW 校相包络超出标定有效范围")
        if not math.isclose(self.xy_ctrl_freq, self.pump_mod_freq, rel_tol=0, abs_tol=1e-9):
            errors.append("XY 控制载波频率必须与 Pump 调制频率一致")
        if not math.isclose(self.xy_ctrl_freq / self.xy_aw_repeat_freq_hz, round(self.xy_ctrl_freq / self.xy_aw_repeat_freq_hz), abs_tol=1e-9):
            errors.append("单个 AW 周期必须包含整数个 X/Y 载波周期")
        if not math.isclose(self.xy_aw_repeat_freq_hz / self.xy_trigger_freq, round(self.xy_aw_repeat_freq_hz / self.xy_trigger_freq), abs_tol=1e-9):
            errors.append("AW 重复频率必须是外部触发频率的整数倍")
        return errors
