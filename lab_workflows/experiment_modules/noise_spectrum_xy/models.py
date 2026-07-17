"""XY DirectAW 噪声谱强类型参数。"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ...experiment_params import ExperimentParams, parameter


@dataclass(slots=True)
class NoiseSpectrumXYParams(ExperimentParams):
    run_tag: str = parameter(default="noise", external_name="RUN_TAG", label="运行标签", group="basic")
    target_noise_freq_start_hz: float = parameter(default=0.0, external_name="TARGET_NOISE_FREQ_START_HZ", label="目标噪声频率起点", unit="Hz", group="basic", minimum=0)
    target_noise_freq_stop_hz: float = parameter(default=50000.0, external_name="TARGET_NOISE_FREQ_STOP_HZ", label="目标噪声频率终点", unit="Hz", group="basic", minimum=0)
    target_noise_freq_points: int = parameter(default=500, external_name="TARGET_NOISE_FREQ_POINTS", label="目标频率扫描点数", group="basic", minimum=2)
    xy_settle_time: float = parameter(default=0.5, external_name="XY_SETTLE_TIME", label="每点稳定等待", unit="s", group="basic", minimum=0)
    pump_mod_freq: float = parameter(default=10000.0, external_name="PUMP_MOD_FREQ", label="Pump 调制频率", unit="Hz", group="basic", minimum=0.001)
    pump_mod_amplitude: float = parameter(default=0.18, external_name="PUMP_MOD_AMPLITUDE", label="Pump 载波幅度", unit="Vpp", group="basic", safety_key="Pump_modulation")
    pump_mod_duty: float = parameter(default=5.0, external_name="PUMP_MOD_DUTY", label="Pump 占空比", unit="%", group="basic", minimum=1, maximum=50)
    rf_gate_amplitude: float = parameter(default=5.0, external_name="RF_GATE_AMPLITUDE", label="RF 门控幅度", unit="Vpp", group="advanced", minimum=0)
    rf_gate_offset: float = parameter(default=2.5, external_name="RF_GATE_OFFSET", label="RF 门控偏置", unit="V", group="advanced")
    xy_ctrl_freq: float = parameter(default=10000.0, external_name="XY_CTRL_FREQ", label="XY 控制载波频率", unit="Hz", group="basic", minimum=0.001)
    xy_ctrl_phase: float = parameter(default=90.0, external_name="XY_CTRL_PHASE", label="XY 整体相位", unit="deg", group="advanced")
    xy_ctrl_quad: float = parameter(default=90.0, external_name="XY_CTRL_QUAD", label="XY 正交相位差", unit="deg", group="advanced")
    xy_calib_envelope_v: float = parameter(default=1.5, external_name="XY_CALIB_ENVELOPE_V", label="相位校准包络", unit="V", group="advanced", minimum=0)
    xy_phase_cal_tol_deg: float = parameter(default=1.0, external_name="XY_PHASE_CAL_TOL_DEG", label="XY 相位校准容差", unit="deg", group="advanced", minimum=0)
    xy_phase_cal_max_iter: int = parameter(default=10, external_name="XY_PHASE_CAL_MAX_ITER", label="XY 相位校准最大测量次数", group="advanced", minimum=1)
    xy_phase_cal_min_r_v: float = parameter(default=1e-12, external_name="XY_PHASE_CAL_MIN_R_V", label="XY 校相最低 R", unit="V", group="advanced", minimum=0)
    xy_phase_cal_min_r_ratio: float = parameter(default=0.1, external_name="XY_PHASE_CAL_MIN_R_RATIO", label="XY 校相最低 R 比例", group="advanced", minimum=0, maximum=1)
    xy_ctrl_k_hz_per_v: float = parameter(default=15075.784562638912, external_name="XY_CTRL_K_HZ_PER_V", label="DirectAW 标定斜率 K", unit="Hz/V", group="advanced", minimum=0.001)
    xy_ctrl_b_hz: float = parameter(default=-218.47506313463893, external_name="XY_CTRL_B_HZ", label="DirectAW 标定截距 B", unit="Hz", group="advanced")
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
    xy_trigger_rearm_guard_s: float = parameter(default=0.05, external_name="XY_TRIGGER_REARM_GUARD_S", label="触发重布置保护时间", unit="s", group="advanced", minimum=0)
    hf2_demod_idx: int = parameter(default=0, external_name="HF2_DEMOD_IDX", label="HF2 Demod 索引", group="advanced", minimum=0)
    hf2_osc_freq: float = parameter(default=10000.0, external_name="HF2_OSC_FREQ", label="HF2 振荡器频率", unit="Hz", group="advanced", minimum=0.001)
    hf2_signal_range: float = parameter(default=2.0, external_name="HF2_SIGNAL_RANGE", label="HF2 输入量程", unit="V", group="advanced", minimum=0.001)
    hf2_demod_order: int = parameter(default=8, external_name="HF2_DEMOD_ORDER", label="HF2 Demod 阶数", group="advanced", minimum=1)
    hf2_demod_tc: float = parameter(default=0.000692, external_name="HF2_DEMOD_TC", label="HF2 校相时间常数", unit="s", group="advanced", minimum=0)
    hf2_demod_rate: float = parameter(default=100000.0, external_name="HF2_DEMOD_RATE", label="HF2 Demod 采样率", unit="Sa/s", group="advanced", minimum=1)
    hf2_daq_duration: float = parameter(default=1.0, external_name="HF2_DAQ_DURATION", label="DAQ 单次采集时长", unit="s", group="basic", minimum=0.001)
    hf2_daq_tc: float = parameter(default=7.85e-7, external_name="HF2_DAQ_TC", label="DAQ 时间常数", unit="s", group="advanced", minimum=0)
    hf2_daq_rate: float = parameter(default=100000.0, external_name="HF2_DAQ_RATE", label="DAQ 采样率", unit="Sa/s", group="basic", minimum=1)
    hf2_nperseg: int = parameter(default=10000, external_name="HF2_NPERSEG", label="Welch 每段点数", group="advanced", minimum=2)
    pump_laser_power: float = parameter(default=0.1, external_name="FIXED_PARAMS.Pump_laser_power", label="Pump 光功率", unit="V", group="basic", safety_key="Pump_laser_power")
    probe_laser_power: float = parameter(default=0.1, external_name="FIXED_PARAMS.Probe_laser_power", label="Probe 光功率", unit="V", group="basic", safety_key="Probe_laser_power")
    temperature: float = parameter(default=100.0, external_name="FIXED_PARAMS.temperature", label="气室温度", unit="°C", group="basic", safety_key="temperature")
    temp_switch: float = parameter(default=5.0, external_name="FIXED_PARAMS.Temp_Switch", label="温控开关电平", unit="V", group="advanced", safety_key="Temp_Switch")
    main_magnetic_field: float = parameter(default=1.03, external_name="FIXED_PARAMS.main_magnetic_field", label="主磁场电流", unit="mA", group="basic", safety_key="main_magnetic_field")

    def validate_model(self) -> list[str]:
        errors: list[str] = []
        if not self.run_tag.strip():
            errors.append("RUN_TAG 不能为空")
        if self.target_noise_freq_start_hz >= self.target_noise_freq_stop_hz:
            errors.append("目标噪声谱范围必须满足起点 < 终点")
        if self.xy_calibration_min_envelope_v >= self.xy_calibration_max_envelope_v:
            errors.append("DirectAW 标定有效电压范围无效")
        if not self.xy_calibration_min_envelope_v <= self.xy_calib_envelope_v <= self.xy_calibration_max_envelope_v:
            errors.append("相位校准包络超出 DirectAW 标定有效范围")
        envelope_start = (self.target_noise_freq_start_hz - self.xy_ctrl_b_hz) / self.xy_ctrl_k_hz_per_v
        envelope_stop = (self.target_noise_freq_stop_hz - self.xy_ctrl_b_hz) / self.xy_ctrl_k_hz_per_v
        envelope_min, envelope_max = min(envelope_start, envelope_stop), max(envelope_start, envelope_stop)
        if envelope_min < 0:
            errors.append("目标频率反算得到负的 |V_env|，请修改目标频率范围或标定 B")
        if envelope_min < self.xy_calibration_min_envelope_v or envelope_max > self.xy_calibration_max_envelope_v:
            errors.append(
                f"目标频率反算得到 V_env=[{envelope_min:.6f}, {envelope_max:.6f}] V，超出标定有效范围"
            )
        output_low = self.xy_aw_output_offset_v - self.xy_aw_output_vpp / 2
        output_high = self.xy_aw_output_offset_v + self.xy_aw_output_vpp / 2
        if -max(envelope_max, self.xy_calib_envelope_v) < output_low or max(envelope_max, self.xy_calib_envelope_v) > output_high:
            errors.append(f"DirectAW 包络超出固定 AW 可表达范围 [{output_low}, {output_high}] V")
        if not math.isclose(self.xy_ctrl_freq / self.xy_aw_repeat_freq_hz, round(self.xy_ctrl_freq / self.xy_aw_repeat_freq_hz), abs_tol=1e-9):
            errors.append("单个 AW 周期必须包含整数个 X/Y 载波周期")
        if not math.isclose(self.xy_aw_repeat_freq_hz / self.xy_trigger_freq, round(self.xy_aw_repeat_freq_hz / self.xy_trigger_freq), abs_tol=1e-9):
            errors.append("AW 重复频率必须是外部触发频率的整数倍")
        if not math.isclose(self.xy_ctrl_freq, self.pump_mod_freq, rel_tol=0, abs_tol=1e-9):
            errors.append("XY 控制频率必须与 Pump 调制频率一致")
        if not math.isclose(self.hf2_osc_freq, self.pump_mod_freq, rel_tol=0, abs_tol=1e-9):
            errors.append("HF2 振荡器频率必须与 Pump 调制频率一致")
        if self.hf2_nperseg > int(self.hf2_daq_duration * self.hf2_daq_rate):
            errors.append("HF2_NPERSEG 不能超过单次 DAQ 采样点数")
        return errors
