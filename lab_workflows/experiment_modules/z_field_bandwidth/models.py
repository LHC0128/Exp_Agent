"""Z 场带宽 扫频强类型参数；外部键与原脚本兼容。"""
from dataclasses import dataclass
from typing import ClassVar, Literal
import math
import numpy as np
from ...experiment_params import ExperimentParams, parameter


@dataclass(slots=True)
class ZFieldBandwidthParams(ExperimentParams):
    schema_version: ClassVar[int] = 2
    system_settle_legacy_defaults: ClassVar[dict[str, float]] = {"FREQ_SETTLE_TIME": 0.5, "TEMP_SWITCH_OFF_LEAD": 0.1, "DEMOD_R_READ_SETTLE_TIME": 0.2}
    removed_temperature_keys: ClassVar[tuple[str, ...]] = ("MAX_TEMP_WAIT_S", "TEMP_TOLERANCE_C")
    run_tag: str = parameter(default="z_bw", external_name="RUN_TAG", label="运行标签", group="basic")
    system_settle_s: float = parameter(default=0.5, external_name="SYSTEM_SETTLE_S", label="系统稳定时间", unit="s", group="basic", minimum=0)
    acquisition_mode: Literal["daq_fft_y", "demod_r", "both"] = parameter(default="both", external_name="ACQUISITION_MODE", label="采集模式", group="basic", options=(("daq_fft_y", "Y 时域与 FFT"), ("demod_r", "硬件 Demod R"), ("both", "两者同时")))
    z_freq_start: float = parameter(default=0, external_name="Z_FREQ_START", label="Z RF 起始频率", unit="Hz", group="basic", minimum=0)
    z_freq_stop: float = parameter(default=1000, external_name="Z_FREQ_STOP", label="Z RF 终止频率", unit="Hz", group="basic", minimum=0)
    z_freq_points: int = parameter(default=101, external_name="Z_FREQ_POINTS", label="频率点数", group="basic", minimum=2)
    z_drive_amplitude: float = parameter(default=0.01, external_name="Z_DRIVE_AMPLITUDE", label="Z RF 幅度", unit="Vpp", group="basic", minimum=0)
    point_duration_s: float = parameter(default=1, external_name="POINT_DURATION", label="每频点 Y 采集时长", unit="s", group="basic", minimum=0.000001)
    temp_switch_on_lag: float = parameter(default=1, external_name="TEMP_SWITCH_ON_LAG", label="温控恢复等待", unit="s", group="advanced", minimum=0)
    hf2_demod0_idx: int = parameter(default=0, external_name="HF2_DEMOD0_IDX", label="主解调器索引", group="advanced", minimum=0, maximum=5)
    hf2_demod0_osc_idx: int = parameter(default=0, external_name="HF2_DEMOD0_OSC_IDX", label="主振荡器索引", group="advanced", minimum=0, maximum=1)
    hf2_demod0_rate: float = parameter(default=50000, external_name="HF2_DEMOD0_RATE", label="Y 请求采样率", unit="Sa/s", group="advanced", minimum=1)
    hf2_demod0_tc: float = parameter(default=1e-6, external_name="HF2_DEMOD0_TC", label="主解调测量时间常数", unit="s", group="advanced", minimum=1e-9)
    hf2_demod0_tc_calib: float = parameter(default=1e-3, external_name="HF2_DEMOD0_TC_CALIB", label="主解调校相时间常数", unit="s", group="advanced", minimum=1e-9)
    hf2_demod0_order: int = parameter(default=4, external_name="HF2_DEMOD0_ORDER", label="主解调滤波阶数", group="advanced", minimum=1, maximum=8)
    hf2_demod0_signal_range: float = parameter(default=2, external_name="HF2_DEMOD0_SIGNAL_RANGE", label="主输入量程", unit="V", group="advanced", minimum=0.001)
    hf2_demod0_osc_freq: float = parameter(default=90000, external_name="HF2_DEMOD0_OSC_FREQ", label="主解调参考频率", unit="Hz", group="advanced", minimum=0.001)
    demod_r_idx: int = parameter(default=3, external_name="DEMOD_R_IDX", label="RF 解调器索引", group="advanced", minimum=0, maximum=5)
    demod_r_osc_idx: int = parameter(default=1, external_name="DEMOD_R_OSC_IDX", label="RF 振荡器索引", group="advanced", minimum=0, maximum=1)
    demod_r_rate: float = parameter(default=4800, external_name="DEMOD_R_RATE", label="RF 解调采样率", unit="Sa/s", group="advanced", minimum=1)
    demod_r_tc_min: float = parameter(default=0.01, external_name="DEMOD_R_TC_MIN", label="RF 时间常数下限", unit="s", group="advanced", minimum=1e-9)
    demod_r_tc_per_period: float = parameter(default=0.05, external_name="DEMOD_R_TC_PER_PERIOD", label="RF 时间常数周期比例", group="advanced", minimum=0.000001)
    demod_r_order: int = parameter(default=8, external_name="DEMOD_R_ORDER", label="RF 滤波阶数", group="advanced", minimum=1, maximum=8)
    demod_r_n_avg: int = parameter(default=50, external_name="DEMOD_R_N_AVG", label="RF 样本平均次数", group="advanced", minimum=1)
    demod_r_avg_interval: float = parameter(default=0.01, external_name="DEMOD_R_AVG_INTERVAL", label="RF 样本读取间隔", unit="s", group="advanced", minimum=0)
    configure_auxout2_to_demod0_y: bool = parameter(default=True, external_name="CONFIGURE_AUXOUT2_TO_DEMOD0_Y", label="写入 Aux 回环路由", group="advanced", description="关闭时不写 Aux，但仍严格回读并要求现有路由一致。")
    auxout_index: int = parameter(default=1, external_name="AUXOUT_INDEX", label="Aux 输出索引", group="advanced", minimum=0, maximum=3)
    auxout_source_demod_idx: int = parameter(default=0, external_name="AUXOUT_SOURCE_DEMOD_IDX", label="Aux 来源解调器", group="advanced", minimum=0, maximum=5)
    auxout_source_select: int = parameter(default=1, external_name="AUXOUT_SOURCE_SELECT", label="Aux 来源分量", group="advanced", options=((1, "Y"),))
    auxout_scale: float = parameter(default=1, external_name="AUXOUT_SCALE", label="Aux 输出缩放", group="advanced")
    auxout_offset: float = parameter(default=0, external_name="AUXOUT_OFFSET", label="Aux 输出偏置", unit="V", group="advanced")
    sigin2_index: int = parameter(default=1, external_name="SIGIN2_INDEX", label="物理回环输入索引", group="advanced", options=((1, "Signal Input 2"),))
    sigin2_range: float = parameter(default=1, external_name="SIGIN2_RANGE", label="回环输入量程", unit="V", group="advanced", minimum=0.001)
    sigin2_ac_coupling: bool = parameter(default=False, external_name="SIGIN2_AC_COUPLING", label="旧 AC 耦合键", visible=False)
    sigin2_impedance: int = parameter(default=1000000, external_name="SIGIN2_IMPEDANCE", label="旧阻抗键", visible=False)
    pump_mod_freq: float = parameter(default=90000, external_name="PUMP_MOD_FREQ", label="Pump 门控频率", unit="Hz", group="basic", minimum=0.001)
    pump_mod_amplitude: float = parameter(default=0.18, external_name="PUMP_MOD_AMPLITUDE", label="Pump 载波幅度", unit="Vpp", group="basic", minimum=0)
    pump_mod_duty: float = parameter(default=5, external_name="PUMP_MOD_DUTY", label="Pump 门控占空比", unit="%", group="basic", minimum=1, maximum=50)
    rf_gate_amplitude: float = parameter(default=5, external_name="RF_GATE_AMPLITUDE", label="Pump 门控幅度", unit="Vpp", group="advanced", minimum=0)
    rf_gate_offset: float = parameter(default=2.5, external_name="RF_GATE_OFFSET", label="Pump 门控偏置", unit="V", group="advanced")
    pump_laser_power: float = parameter(default=0.1, external_name="FIXED_PARAMS.Pump_laser_power", label="Pump 光功率", unit="V", group="basic")
    probe_laser_power: float = parameter(default=0.1, external_name="FIXED_PARAMS.Probe_laser_power", label="Probe 光功率", unit="V", group="basic")
    main_magnetic_field: float = parameter(default=9.31, external_name="FIXED_PARAMS.main_magnetic_field", label="主场电流", unit="mA", group="basic")
    temperature: float = parameter(default=100, external_name="FIXED_PARAMS.temperature", label="TEC 目标温度", unit="°C", group="basic")
    temp_switch: float = parameter(default=5, external_name="FIXED_PARAMS.Temp_Switch", label="温控开关固定电平", visible=False)
    time_sequence: float = parameter(default=10, external_name="FIXED_PARAMS.Time_sequence", label="时序2方波幅度", unit="Vpp", group="advanced", minimum=0, description="保留旧键；实际写入 Time_sequence_2 映射的 10 Hz、零偏置方波。")
    time_sequence_2: float = parameter(default=0, external_name="FIXED_PARAMS.Time_sequence_2", label="旧时序2记录值（不控制输出）", visible=False)
    daq_response_method: Literal["nearest_fft_bin", "welch_nearest_bin", "welch_local_peak"] = parameter(default="nearest_fft_bin", external_name="DAQ_RESPONSE_METHOD", label="FFT 响应提取方法", group="advanced", options=(("nearest_fft_bin", "整段最近 FFT bin"), ("welch_nearest_bin", "Welch 最近 bin"), ("welch_local_peak", "Welch 局部峰（诊断）")))
    local_peak_rel_window: float = parameter(default=0.01, external_name="LOCAL_PEAK_REL_WINDOW", label="局部峰相对窗口", group="advanced", minimum=0)
    max_pick_freq_error_hz: float = parameter(default=20, external_name="MAX_PICK_FREQ_ERROR_HZ", label="频率拾取误差门槛", unit="Hz", group="advanced", minimum=0)
    exclude_suspicious_for_bandwidth: bool = parameter(default=True, external_name="EXCLUDE_SUSPICIOUS_FOR_BANDWIDTH", label="带宽排除可疑频点", group="advanced")
    offline_lockin_use_integer_cycles: bool = parameter(default=True, external_name="OFFLINE_LOCKIN_USE_INTEGER_CYCLES", label="软件锁相仅用整数周期", group="advanced")

    def __post_init__(self):
        self.sigin2_ac_coupling, self.sigin2_impedance = False, 1000000

    demod_r_adc_select: int = parameter(default=1, external_name="DEMOD_R_ADC_SELECT", label="硬件 R 输入选择", visible=False)
    rf_gate_delay: float = parameter(default=0, external_name="RF_GATE_DELAY", label="旧门控延迟记录值（原协议未下发）", visible=False)
    x_magnetic_field: float = parameter(default=0, external_name="FIXED_PARAMS.X_magnetic_field", label="X 补偿预设（输出保持关闭）", unit="V", group="advanced")
    y_magnetic_field: float = parameter(default=0, external_name="FIXED_PARAMS.Y_magnetic_field", label="Y 补偿预设（输出保持关闭）", unit="V", group="advanced")
    z_v_to_nt: float = parameter(default=3517, external_name="Z_V_TO_NT", label="历史磁场换算记录值", visible=False)

    def frequency_axis(self):
        return np.linspace(self.z_freq_start, self.z_freq_stop, self.z_freq_points)

    @classmethod
    def derive_external(cls, values):
        p = cls.from_external(values)
        return {"频率点数": p.z_freq_points, "Y 记录样本数（请求）": int(p.hf2_demod0_rate*p.point_duration_s),
                "基线条件": "0 Hz 为 Z OFF 背景，不参与带宽" if p.z_freq_start == 0 else "扫描不含 0 Hz 背景"}

    def validate_analysis(self):
        return [f"{key} 必须为有限数值" for key, value in self.to_external().items()
                if isinstance(value, (int, float)) and not isinstance(value, bool) and not math.isfinite(value)]

    def validate_model(self):
        errors = self.validate_analysis()
        if not self.run_tag.strip() or any(c in self.run_tag for c in '/\\:*?"<>|') or self.run_tag in (".", ".."):
            errors.append("运行标签不能为空或包含路径字符")
        if self.z_freq_start >= self.z_freq_stop:
            errors.append("频率范围须递增")
        if self.hf2_demod0_idx == self.demod_r_idx or self.hf2_demod0_osc_idx == self.demod_r_osc_idx:
            errors.append("主解调与硬件 R 必须使用不同解调器和振荡器")
        if self.auxout_source_demod_idx != self.hf2_demod0_idx or self.auxout_source_select != 1 or self.sigin2_index != 1 or self.demod_r_adc_select != self.sigin2_index:
            errors.append("物理回环必须为主解调 Y → Aux → Signal Input 2")
        if self.temp_switch != 5:
            errors.append("温控恢复必须为 5 V DC + ON")
        if self.pump_mod_freq != self.hf2_demod0_osc_freq:
            errors.append("Pump 门控与主解调参考频率必须一致")
        if self.acquisition_mode in ("daq_fft_y", "both") and self.hf2_demod0_rate * self.point_duration_s < 4:
            errors.append("Y 记录至少需要 4 个样本")
        return errors
