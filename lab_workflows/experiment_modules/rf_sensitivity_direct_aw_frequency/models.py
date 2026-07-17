"""RF DirectAW 频率响应强类型参数。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np

from ...common import find_project_root
from ...experiment_params import ExperimentParams, parameter


@dataclass(slots=True)
class RFDirectAWFrequencyParams(ExperimentParams):
    run_tag: str = parameter(default="freq_resp_direct_aw", external_name="RUN_TAG", label="运行标签", group="basic")
    acquisition_mode: Literal["yfft", "demod_rxy", "both"] = parameter(default="demod_rxy", external_name="ACQUISITION_MODE", label="采集模式", group="basic", options=(("yfft", "DAQ Y + FFT"), ("demod_rxy", "Demod 3 X/Y/R"), ("both", "两者同时采集")))
    z_rf_freq_start: float = parameter(default=0.0, external_name="Z_RF_FREQ_START", label="Z RF 起始频率", unit="Hz", group="basic", minimum=0)
    z_rf_freq_stop: float = parameter(default=20000.0, external_name="Z_RF_FREQ_STOP", label="Z RF 终止频率", unit="Hz", group="basic", minimum=0)
    z_rf_freq_points: int = parameter(default=201, external_name="Z_RF_FREQ_POINTS", label="Z RF 频率点数", group="basic", minimum=2)
    z_rf_freq_log_spaced: bool = parameter(default=False, external_name="Z_RF_FREQ_LOG_SPACED", label="对数频率间隔", group="advanced")
    z_rf_freq_offset_hz: float = parameter(default=50.0, external_name="Z_RF_FREQ_OFFSET_HZ", label="线性扫描频率偏移", unit="Hz", group="advanced", description="仅在线性扫描时加到每个频点；保留旧脚本的 +50 Hz 行为")
    z_rf_amplitude: float = parameter(default=0.05, external_name="Z_RF_AMPLITUDE", label="Z RF 固定幅度", unit="Vpp", group="basic", safety_key="Z_magnetic_field")
    z_rf_drive_mode: Literal["continuous_sine_no_burst"] = parameter(default="continuous_sine_no_burst", external_name="Z_RF_DRIVE_MODE", label="Z RF 驱动模式", group="advanced", options=(("continuous_sine_no_burst", "连续正弦（Burst OFF）"),))
    freq_settle_time: float = parameter(default=0.1, external_name="FREQ_SETTLE_TIME", label="频率稳定等待", unit="s", group="basic", minimum=0)
    phase_start: float = parameter(default=0.0, external_name="PHASE_START", label="相位扫描起点", unit="deg", group="basic")
    phase_stop: float = parameter(default=360.0, external_name="PHASE_STOP", label="相位扫描终点", unit="deg", group="basic")
    phase_points: int = parameter(default=10, external_name="PHASE_POINTS", label="相位扫描点数", group="basic", minimum=1)
    phase_settle_time: float = parameter(default=0.1, external_name="PHASE_SETTLE_TIME", label="相位稳定等待", unit="s", group="advanced", minimum=0)
    arb_waveform_file: str = parameter(default="optimal_control_waveform.csv", external_name="ARB_WAVEFORM_FILE", label="任意波波形文件", group="basic", options_from_directory="experiments", options_pattern="*.csv")
    a_env_freq: float = parameter(default=250.0, external_name="A_ENV_FREQ", label="包络重复频率", unit="Hz", group="basic", minimum=0.001)
    xy_ctrl_k_hz_per_v: float = parameter(default=15076.0, external_name="XY_CTRL_K_HZ_PER_V", label="DirectAW 标定斜率 K", unit="Hz/V", group="advanced", minimum=0.001)
    xy_ctrl_b_hz: float = parameter(default=-218.0, external_name="XY_CTRL_B_HZ", label="DirectAW 标定截距 B", unit="Hz", group="advanced")
    xy_aw_output_vpp: float = parameter(default=4.0, external_name="XY_AW_OUTPUT_VPP", label="固定 AW 输出幅度", unit="Vpp", group="basic", minimum=0.001)
    xy_aw_output_offset: float = parameter(default=0.0, external_name="XY_AW_OUTPUT_OFFSET", label="固定 AW 输出偏置", unit="V", group="advanced")
    xy_ctrl_phase: float = parameter(default=0.0, external_name="XY_CTRL_PHASE", label="XY 整体相位", unit="deg", group="advanced")
    xy_ctrl_quad: float = parameter(default=90.0, external_name="XY_CTRL_QUAD", label="XY 正交相位差", unit="deg", group="advanced")
    enable_xy_ctrl_phase_cal: bool = parameter(default=True, external_name="ENABLE_XY_CTRL_PHASE_CAL", label="启用 XY 相位校准", group="basic")
    xy_ctrl_phase_tol_deg: float = parameter(default=0.5, external_name="XY_CTRL_PHASE_TOL_DEG", label="XY 相位校准容差", unit="deg", group="advanced", minimum=0)
    xy_ctrl_phase_max_iter: int = parameter(default=8, external_name="XY_CTRL_PHASE_MAX_ITER", label="XY 相位校准最大迭代", group="advanced", minimum=1)
    xy_ctrl_phase_min_r_v: float = parameter(default=1e-12, external_name="XY_CTRL_PHASE_MIN_R_V", label="XY 校相最低 R", unit="V", group="advanced", minimum=0)
    xy_ctrl_phase_min_r_ratio: float = parameter(default=0.1, external_name="XY_CTRL_PHASE_MIN_R_RATIO", label="XY 校相最低 R 比例", group="advanced", minimum=0, maximum=1)
    xy_trigger_freq: float = parameter(default=100.0, external_name="XY_TRIGGER_FREQ", label="XY 外触发频率", unit="Hz", group="advanced", minimum=0.001)
    xy_trigger_amplitude: float = parameter(default=5.0, external_name="XY_TRIGGER_AMPLITUDE", label="XY 外触发幅度", unit="Vpp", group="advanced", minimum=0)
    xy_trigger_offset: float = parameter(default=2.5, external_name="XY_TRIGGER_OFFSET", label="XY 外触发偏置", unit="V", group="advanced")
    xy_trigger_duty: float = parameter(default=50.0, external_name="XY_TRIGGER_DUTY", label="XY 外触发占空比", unit="%", group="advanced", minimum=0.001, maximum=100)
    xy_trigger_phase: float = parameter(default=0.0, external_name="XY_TRIGGER_PHASE", label="XY 外触发相位", unit="deg", group="advanced")
    pump_mod_freq: float = parameter(default=10000.0, external_name="PUMP_MOD_FREQ", label="Pump 调制频率", unit="Hz", group="basic", minimum=0.001)
    pump_mod_duty: float = parameter(default=5.0, external_name="PUMP_MOD_DUTY", label="Pump 占空比", unit="%", group="basic", minimum=1, maximum=50)
    pump_mod_amplitude: float = parameter(default=0.18, external_name="PUMP_MOD_AMPLITUDE", label="Pump 载波幅度", unit="Vpp", group="basic", safety_key="Pump_modulation")
    demod0_idx: int = parameter(default=0, external_name="DEMOD0_IDX", label="Demod 0 索引", group="advanced", minimum=0)
    demod0_osc_idx: int = parameter(default=0, external_name="DEMOD0_OSC_IDX", label="Demod 0 振荡器", group="advanced", minimum=0)
    demod0_signal_range: float = parameter(default=2.0, external_name="DEMOD0_SIGNAL_RANGE", label="Demod 0 输入量程", unit="V", group="advanced", minimum=0.001)
    demod0_order: int = parameter(default=4, external_name="DEMOD0_ORDER", label="Demod 0 阶数", group="advanced", minimum=1)
    demod0_tc_calib: float = parameter(default=0.001, external_name="DEMOD0_TC_CALIB", label="Demod 0 校相时间常数", unit="s", group="advanced", minimum=0)
    demod0_tc_meas: float = parameter(default=1e-6, external_name="DEMOD0_TC_MEAS", label="Demod 0 测量时间常数", unit="s", group="advanced", minimum=0)
    demod0_rate: float = parameter(default=100000.0, external_name="DEMOD0_RATE", label="Demod 0 采样率", unit="Sa/s", group="advanced", minimum=1)
    demod3_idx: int = parameter(default=3, external_name="DEMOD3_IDX", label="Demod 3 索引", group="advanced", minimum=0)
    demod3_osc_idx: int = parameter(default=1, external_name="DEMOD3_OSC_IDX", label="Demod 3 振荡器", group="advanced", minimum=0)
    demod3_tc_period_fraction: float = parameter(default=0.05, external_name="DEMOD3_TC_PERIOD_FRACTION", label="Demod 3 时间常数周期比例", group="advanced", minimum=0.000001)
    demod3_order: int = parameter(default=8, external_name="DEMOD3_ORDER", label="Demod 3 阶数", group="advanced", minimum=1)
    demod3_rate: float = parameter(default=4800.0, external_name="DEMOD3_RATE", label="Demod 3 采样率", unit="Sa/s", group="advanced", minimum=1)
    demod3_n_avg: int = parameter(default=20, external_name="DEMOD3_N_AVG", label="Demod 3 平均次数", group="advanced", minimum=1)
    configure_auxout2_to_demod0_y: bool = parameter(default=True, external_name="CONFIGURE_AUXOUT2_TO_DEMOD0_Y", label="配置 Demod0 Y 物理回环", group="advanced")
    auxout_index: int = parameter(default=1, external_name="AUXOUT_INDEX", label="AuxOut 索引", group="advanced", minimum=0)
    auxout_source_demod_idx: int = parameter(default=0, external_name="AUXOUT_SOURCE_DEMOD_IDX", label="AuxOut Demod 来源", group="advanced", minimum=0)
    auxout_source_select: int = parameter(default=1, external_name="AUXOUT_SOURCE_SELECT", label="AuxOut 信号选择", group="advanced", minimum=0, maximum=3)
    auxout_scale: float = parameter(default=1.0, external_name="AUXOUT_SCALE", label="AuxOut 比例", group="advanced")
    auxout_offset: float = parameter(default=0.0, external_name="AUXOUT_OFFSET", label="AuxOut 偏置", unit="V", group="advanced")
    sigin2_index: int = parameter(default=1, external_name="SIGIN2_INDEX", label="Signal Input 2 索引", group="advanced", minimum=0)
    sigin2_range: float = parameter(default=1.0, external_name="SIGIN2_RANGE", label="Signal Input 2 量程", unit="V", group="advanced", minimum=0.001)
    sigin2_ac_coupling: bool = parameter(default=False, external_name="SIGIN2_AC_COUPLING", label="Signal Input 2 AC 耦合", group="advanced")
    sigin2_impedance: int = parameter(default=50, external_name="SIGIN2_IMPEDANCE", label="Signal Input 2 阻抗", unit="Ω", group="advanced", options=((50, "50 Ω"), (1000000, "1 MΩ")))
    temp_switch_off_lead: float = parameter(default=0.1, external_name="TEMP_SWITCH_OFF_LEAD", label="温控关闭前置等待", unit="s", group="advanced", minimum=0)
    temp_switch_on_lag: float = parameter(default=1.0, external_name="TEMP_SWITCH_ON_LAG", label="温控恢复等待", unit="s", group="advanced", minimum=0)
    pump_laser_power: float = parameter(default=0.5, external_name="FIXED_PARAMS.Pump_laser_power", label="Pump 光功率", unit="V", group="basic", safety_key="Pump_laser_power")
    probe_laser_power: float = parameter(default=0.3, external_name="FIXED_PARAMS.Probe_laser_power", label="Probe 光功率", unit="V", group="basic", safety_key="Probe_laser_power")
    main_magnetic_field: float = parameter(default=1.03, external_name="FIXED_PARAMS.main_magnetic_field", label="主磁场电流", unit="mA", group="basic", safety_key="main_magnetic_field")
    temperature: float = parameter(default=100.0, external_name="FIXED_PARAMS.temperature", label="气室温度", unit="°C", group="basic", safety_key="temperature")
    temp_switch: float = parameter(default=5.0, external_name="FIXED_PARAMS.Temp_Switch", label="温控开关电平", unit="V", group="advanced", safety_key="Temp_Switch")

    def validate_model(self) -> list[str]:
        errors: list[str] = []
        if not self.run_tag.strip():
            errors.append("RUN_TAG 不能为空")
        if self.z_rf_freq_start >= self.z_rf_freq_stop:
            errors.append("Z RF 起始频率必须小于终止频率")
        if not self.z_rf_freq_log_spaced and self.z_rf_freq_start + self.z_rf_freq_offset_hz < 0:
            errors.append("Z RF 线性扫描的实际起始频率不能小于 0 Hz")
        if self.phase_start > self.phase_stop:
            errors.append("相位扫描起点不能大于终点")
        trigger_low = self.xy_trigger_offset - self.xy_trigger_amplitude / 2
        trigger_high = self.xy_trigger_offset + self.xy_trigger_amplitude / 2
        if trigger_low < -10 or trigger_high > 10:
            errors.append(f"XY 外触发电平 [{trigger_low}, {trigger_high}] V 超出 ±10 V")
        path = find_project_root() / "experiments" / self.arb_waveform_file
        if not path.is_file():
            errors.append(f"任意波文件不存在: {path}")
        else:
            try:
                data = np.loadtxt(path, delimiter=",", skiprows=1)
                if data.ndim != 2 or data.shape[1] < 2:
                    errors.append(f"任意波文件至少需要两列: {path.name}")
                elif data.size == 0 or not np.all(np.isfinite(data)):
                    errors.append(f"任意波文件包含无效数据: {path.name}")
            except (OSError, ValueError) as exc:
                errors.append(f"任意波文件无法读取: {path.name}: {exc}")
        return errors
