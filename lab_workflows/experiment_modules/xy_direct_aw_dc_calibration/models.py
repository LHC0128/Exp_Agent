"""XY DirectAW DC 标定强类型参数。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from ...experiment_params import ExperimentParams, parameter


@dataclass(slots=True)
class XYDirectAWDCCalibrationParams(ExperimentParams):
    run_tag: str = parameter(default="direct_aw_dc_cal", external_name="RUN_TAG", label="运行标签", group="basic")
    xy_env_voltage_start_v: float = parameter(default=-4.0, external_name="XY_ENV_VOLTAGE_START_V", label="DirectAW 扫描起始值", unit="V", group="basic")
    xy_env_voltage_stop_v: float = parameter(default=4.0, external_name="XY_ENV_VOLTAGE_STOP_V", label="DirectAW 扫描终止值", unit="V", group="basic")
    xy_env_voltage_points: int = parameter(default=17, external_name="XY_ENV_VOLTAGE_POINTS", label="DirectAW 扫描点数", group="basic", minimum=2)
    z_rf_freq_start_hz: float = parameter(default=500.0, external_name="Z_RF_FREQ_START_Hz", label="Z 射频扫描起始频率", unit="Hz", group="basic", minimum=0)
    z_rf_freq_stop_hz: float = parameter(default=35000.0, external_name="Z_RF_FREQ_STOP_Hz", label="Z 射频扫描终止频率", unit="Hz", group="basic", minimum=0)
    z_rf_freq_step_hz: float = parameter(default=500.0, external_name="Z_RF_FREQ_STEP_Hz", label="Z 射频扫描步进", unit="Hz", group="basic", minimum=0.001)
    xy_aw_output_vpp: float = parameter(default=4.0, external_name="XY_AW_OUTPUT_VPP", label="固定 AW 输出幅度", unit="Vpp", group="basic", minimum=0.001)
    xy_aw_output_offset_v: float = parameter(default=0.0, external_name="XY_AW_OUTPUT_OFFSET_V", label="固定 AW 输出偏置", unit="V", group="advanced")
    xy_aw_points: int = parameter(default=10000, external_name="XY_AW_POINTS", label="AW 波形点数", group="advanced", minimum=2)
    xy_trigger_offset_v: float = parameter(default=2.5, external_name="XY_TRIGGER_OFFSET_V", label="外触发偏置", unit="V", group="advanced")
    enable_xy_phase_cal: bool = parameter(default=True, external_name="ENABLE_XY_PHASE_CAL", label="启用 XY 相位校准", group="basic")
    xy_phase_cal_envelope_v: float = parameter(default=0.5, external_name="XY_PHASE_CAL_ENVELOPE_V", label="相位校准包络", unit="V", group="advanced")
    xy_phase_cal_tol_deg: float = parameter(default=0.5, external_name="XY_PHASE_CAL_TOL_deg", label="相位校准容差", unit="deg", group="advanced", minimum=0)
    xy_phase_cal_max_iter: int = parameter(default=8, external_name="XY_PHASE_CAL_MAX_ITER", label="相位校准最大迭代", group="advanced", minimum=1)
    xy_phase_cal_min_r_v: float = parameter(default=1e-12, external_name="XY_PHASE_CAL_MIN_R_V", label="相位校准最低 R", unit="V", group="advanced", minimum=0)
    xy_phase_cal_min_r_ratio: float = parameter(default=0.1, external_name="XY_PHASE_CAL_MIN_R_RATIO", label="相位校准最低 R 比例", group="advanced", minimum=0, maximum=1)
    acquisition_mode: Literal["daq_y", "demod_rxy", "both"] = parameter(
        default="demod_rxy",
        external_name="ACQUISITION_MODE",
        label="采集模式",
        group="basic",
        options=(("daq_y", "DAQ Y 时域信号"), ("demod_rxy", "Demod 3 X/Y/R"), ("both", "两者同时采集")),
    )
    pump_mod_freq_hz: float = parameter(
        default=10000.0,
        external_name="PUMP_MOD_FREQ_Hz",
        label="Pump 调制频率",
        unit="Hz",
        group="basic",
        minimum=0.001,
        description="同时作为 Pump 门控频率、HF2 Demod0 参考频率和 DirectAW 载波频率",
    )
    demod0_idx: int = parameter(default=0, external_name="DEMOD0_IDX", label="Demod 0 索引", group="advanced", minimum=0)
    demod0_osc_idx: int = parameter(default=0, external_name="DEMOD0_OSC_IDX", label="Demod 0 振荡器", group="advanced", minimum=0)
    demod0_signal_range_v: float = parameter(default=2.0, external_name="DEMOD0_SIGNAL_RANGE_V", label="Demod 0 输入量程", unit="V", group="advanced", minimum=0.001)
    demod0_order: int = parameter(default=4, external_name="DEMOD0_ORDER", label="Demod 0 阶数", group="advanced", minimum=1)
    demod3_idx: int = parameter(default=3, external_name="DEMOD3_IDX", label="Demod 3 索引", group="advanced", minimum=0)
    demod3_osc_idx: int = parameter(default=1, external_name="DEMOD3_OSC_IDX", label="Demod 3 振荡器", group="advanced", minimum=0)
    demod3_adc_select: int = parameter(default=1, external_name="DEMOD3_ADC_SELECT", label="Demod 3 输入选择", group="advanced", minimum=0)
    demod3_order: int = parameter(default=8, external_name="DEMOD3_ORDER", label="Demod 3 阶数", group="advanced", minimum=1)
    demod3_tc_period_fraction: float = parameter(default=0.05, external_name="DEMOD3_TC_PERIOD_FRACTION", label="Demod 3 时间常数周期比例", group="advanced", minimum=0.000001)
    demod3_n_avg: int = parameter(default=20, external_name="DEMOD3_N_AVG", label="Demod 3 平均次数", group="advanced", minimum=1)
    auxout_index: int = parameter(default=1, external_name="AUXOUT_INDEX", label="AuxOut 索引", group="advanced", minimum=0)
    auxout_source_demod_idx: int = parameter(default=0, external_name="AUXOUT_SOURCE_DEMOD_IDX", label="AuxOut Demod 来源", group="advanced", minimum=0)
    auxout_source_select: int = parameter(default=1, external_name="AUXOUT_SOURCE_SELECT", label="AuxOut 信号选择", group="advanced", minimum=0, maximum=3)
    auxout_scale: float = parameter(default=1.0, external_name="AUXOUT_SCALE", label="AuxOut 比例", group="advanced")
    auxout_offset_v: float = parameter(default=0.0, external_name="AUXOUT_OFFSET_V", label="AuxOut 偏置", unit="V", group="advanced")
    sigin2_index: int = parameter(default=1, external_name="SIGIN2_INDEX", label="Signal Input 2 索引", group="advanced", minimum=0)
    sigin2_range_v: float = parameter(default=1.0, external_name="SIGIN2_RANGE_V", label="Signal Input 2 量程", unit="V", group="advanced", minimum=0.001)
    sigin2_ac_coupling: bool = parameter(default=False, external_name="SIGIN2_AC_COUPLING", label="Signal Input 2 AC 耦合", group="advanced")
    sigin2_impedance_ohm: int = parameter(default=50, external_name="SIGIN2_IMPEDANCE_OHM", label="Signal Input 2 阻抗", unit="Ω", group="advanced", options=((50, "50 Ω"), (1000000, "1 MΩ")))
    pump_laser_power: float = parameter(default=0.5, external_name="FIXED_PARAMS.Pump_laser_power", label="Pump 光功率", unit="V", group="basic", safety_key="Pump_laser_power")
    probe_laser_power: float = parameter(default=0.3, external_name="FIXED_PARAMS.Probe_laser_power", label="Probe 光功率", unit="V", group="basic", safety_key="Probe_laser_power")
    main_magnetic_field: float = parameter(default=1.03, external_name="FIXED_PARAMS.main_magnetic_field", label="主磁场电流", unit="mA", group="basic", safety_key="main_magnetic_field")
    temperature: float = parameter(default=100.0, external_name="FIXED_PARAMS.temperature", label="气室温度", unit="°C", group="basic", safety_key="temperature")
    temp_switch: float = parameter(default=5.0, external_name="FIXED_PARAMS.Temp_Switch", label="温控开关电平", unit="V", group="advanced", safety_key="Temp_Switch")

    def validate_model(self) -> list[str]:
        errors: list[str] = []
        if not self.run_tag.strip():
            errors.append("RUN_TAG 不能为空")
        if self.xy_env_voltage_start_v >= self.xy_env_voltage_stop_v:
            errors.append("DirectAW 扫描起始电压必须小于终止电压")
        if self.z_rf_freq_start_hz >= self.z_rf_freq_stop_hz:
            errors.append("Z 射频扫描起始频率必须小于终止频率")
        aw_low = self.xy_aw_output_offset_v - self.xy_aw_output_vpp / 2
        aw_high = self.xy_aw_output_offset_v + self.xy_aw_output_vpp / 2
        if self.xy_env_voltage_start_v < aw_low or self.xy_env_voltage_stop_v > aw_high:
            errors.append(
                f"DirectAW 扫描范围超出固定 AW 可表达范围 [{aw_low}, {aw_high}] V"
            )
        if abs(self.xy_phase_cal_envelope_v) > max(abs(aw_low), abs(aw_high)):
            errors.append("相位校准包络超出固定 AW 可表达范围")
        span = self.z_rf_freq_stop_hz - self.z_rf_freq_start_hz
        if not math.isclose(span / self.z_rf_freq_step_hz, round(span / self.z_rf_freq_step_hz), abs_tol=1e-9):
            errors.append("Z 射频扫描范围必须包含整数个步进")
        carrier_cycles = self.pump_mod_freq_hz / 500.0
        if not math.isclose(carrier_cycles, round(carrier_cycles), abs_tol=1e-9):
            errors.append("PUMP_MOD_FREQ_Hz 必须是 500 Hz AW 重复频率的整数倍")
        return errors
