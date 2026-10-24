"""ConstXY 扫频强类型参数；外部键与原脚本兼容。"""
from dataclasses import dataclass
from typing import ClassVar, Literal
import math
import numpy as np
from ...experiment_params import ExperimentParams, parameter


@dataclass(slots=True)
class RFConstXYFrequencyParams(ExperimentParams):
    schema_version: ClassVar[int] = 2
    system_settle_legacy_defaults: ClassVar[dict[str, float]] = {"FREQ_SETTLE_TIME": 0.5, "TEMP_SWITCH_OFF_LEAD": 0.1}
    removed_temperature_keys: ClassVar[tuple[str, ...]] = ("MAX_TEMP_WAIT_S", "TEMP_TOLERANCE_C")
    run_tag: str = parameter(default="constxy_freq", external_name="RUN_TAG", label="运行标签", group="basic")
    system_settle_s: float = parameter(default=0.5, external_name="SYSTEM_SETTLE_S", label="系统稳定时间", unit="s", group="basic", minimum=0)
    acquisition_mode: Literal["daq_fft_y", "demod_r", "both"] = parameter(default="both", external_name="ACQUISITION_MODE", label="采集模式", group="basic", options=(("daq_fft_y", "Y 时域与 FFT"), ("demod_r", "硬件 Demod R"), ("both", "两者同时")))
    larmor_freq_hz: float = parameter(default=7000, external_name="LARMOR_FREQ_HZ", label="XY 等效 Larmor 频率", unit="Hz", group="basic", minimum=0)
    a_env_k: float = parameter(default=16319, external_name="A_ENV_K", label="DC 包络标定斜率", unit="Hz/V", group="basic")
    a_env_b: float = parameter(default=2121, external_name="A_ENV_B", label="DC 包络标定截距", unit="Hz", group="basic")
    z_rf_freq_start: float = parameter(default=0, external_name="Z_RF_FREQ_START", label="Z RF 起始频率", unit="Hz", group="basic", minimum=0)
    z_rf_freq_stop: float = parameter(default=10000, external_name="Z_RF_FREQ_STOP", label="Z RF 终止频率", unit="Hz", group="basic", minimum=0)
    z_rf_freq_points: int = parameter(default=101, external_name="Z_RF_FREQ_POINTS", label="频率点数", group="basic", minimum=2)
    z_rf_freq_log_spaced: bool = parameter(default=False, external_name="Z_RF_FREQ_LOG_SPACED", label="对数频率轴", group="advanced")
    z_rf_amplitude: float = parameter(default=0.01, external_name="Z_RF_AMPLITUDE", label="Z RF 幅度", unit="Vpp", group="basic", minimum=0)
    point_duration_s: float = parameter(default=1, external_name="POINT_DURATION_s", label="每频点 Y 采集时长", unit="s", group="basic", minimum=0.000001)
    temp_switch_on_lag: float = parameter(default=1, external_name="TEMP_SWITCH_ON_LAG", label="温控恢复等待", unit="s", group="advanced", minimum=0)
    hf2_demod0_idx: int = parameter(default=0, external_name="HF2_DEMOD0_IDX", label="主解调器索引", group="advanced", minimum=0, maximum=5)
    hf2_demod0_osc_idx: int = parameter(default=0, external_name="HF2_DEMOD0_OSC_IDX", label="主振荡器索引", group="advanced", minimum=0, maximum=5)
    hf2_demod0_rate: float = parameter(default=50000, external_name="HF2_DEMOD0_RATE", label="Y 请求采样率", unit="Sa/s", group="advanced", minimum=1)
    hf2_demod0_tc: float = parameter(default=1e-6, external_name="HF2_DEMOD0_TC", label="主解调测量时间常数", unit="s", group="advanced", minimum=1e-9)
    hf2_demod0_tc_calib: float = parameter(default=1e-3, external_name="HF2_DEMOD0_TC_CALIB", label="主解调校相时间常数", unit="s", group="advanced", minimum=1e-9)
    hf2_demod0_order: int = parameter(default=4, external_name="HF2_DEMOD0_ORDER", label="主解调滤波阶数", group="advanced", minimum=1, maximum=8)
    hf2_demod0_signal_range: float = parameter(default=2, external_name="HF2_DEMOD0_SIGNAL_RANGE", label="主输入量程", unit="V", group="advanced", minimum=0.001)
    hf2_demod0_osc_freq: float = parameter(default=90000, external_name="HF2_DEMOD0_OSC_FREQ", label="主解调参考频率", unit="Hz", group="advanced", minimum=0.001)
    demod3_idx: int = parameter(default=3, external_name="DEMOD3_IDX", label="RF 解调器索引", group="advanced", minimum=0, maximum=5)
    demod3_osc_idx: int = parameter(default=1, external_name="DEMOD3_OSC_IDX", label="RF 振荡器索引", group="advanced", minimum=0, maximum=5)
    demod3_rate: float = parameter(default=4800, external_name="DEMOD3_RATE", label="RF 解调采样率", unit="Sa/s", group="advanced", minimum=1)
    demod3_tc_min_s: float = parameter(default=0.01, external_name="DEMOD3_TC_MIN_s", label="RF 时间常数下限", unit="s", group="advanced", minimum=1e-9)
    demod3_tc_period_fraction: float = parameter(default=0.05, external_name="DEMOD3_TC_PERIOD_FRACTION", label="RF 时间常数周期比例", group="advanced", minimum=0.000001)
    demod3_order: int = parameter(default=8, external_name="DEMOD3_ORDER", label="RF 滤波阶数", group="advanced", minimum=1, maximum=8)
    demod3_n_avg: int = parameter(default=50, external_name="DEMOD3_N_AVG", label="RF 样本平均次数", group="advanced", minimum=1)
    demod3_avg_interval_s: float = parameter(default=0.01, external_name="DEMOD3_AVG_INTERVAL_s", label="RF 样本读取间隔", unit="s", group="advanced", minimum=0)
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
    xy_carrier_freq: float = parameter(default=90000, external_name="XY_CARRIER_FREQ", label="XY 载波频率", unit="Hz", group="basic", minimum=0.001)
    x_carrier_amplitude: float = parameter(default=6, external_name="X_CARRIER_AMPLITUDE", label="X 载波幅度", unit="Vpp", group="advanced", minimum=0)
    y_carrier_amplitude: float = parameter(default=6, external_name="Y_CARRIER_AMPLITUDE", label="Y 载波幅度", unit="Vpp", group="advanced", minimum=0)
    x_carrier_phase: float = parameter(default=0, external_name="X_CARRIER_PHASE", label="X 载波初始／固定相位", unit="deg", group="advanced")
    y_carrier_phase: float = parameter(default=0, external_name="Y_CARRIER_PHASE", label="Y 载波固定相位", unit="deg", group="advanced", description="仅关闭 XY 校相时使用；启用校相时 Y 从原协议 90° 开始。")
    x_am_depth: float = parameter(default=100, external_name="X_AM_DEPTH", label="X AM 深度", unit="%", group="advanced", minimum=0, maximum=120)
    y_am_depth: float = parameter(default=100, external_name="Y_AM_DEPTH", label="Y AM 深度", unit="%", group="advanced", minimum=0, maximum=120)
    enable_xy_carrier_phase_cal: bool = parameter(default=True, external_name="ENABLE_XY_CARRIER_PHASE_CAL", label="启用 XY 载波校相", group="basic")
    xy_phase_cal_max_iter_x: int = parameter(default=5, external_name="XY_PHASE_CAL_MAX_ITER_X", label="X 校相最大迭代", group="advanced", minimum=1)
    xy_phase_cal_max_iter_y: int = parameter(default=15, external_name="XY_PHASE_CAL_MAX_ITER_Y", label="Y 校相最大迭代", group="advanced", minimum=1)
    xy_phase_cal_tol_deg: float = parameter(default=0.5, external_name="XY_PHASE_CAL_TOL_DEG", label="XY 校相容差", unit="deg", group="advanced", minimum=0.000001)
    pump_mod_freq: float = parameter(default=90000, external_name="PUMP_MOD_FREQ", label="Pump 门控频率", unit="Hz", group="basic", minimum=0.001)
    pump_mod_amplitude: float = parameter(default=0.18, external_name="PUMP_MOD_AMPLITUDE", label="Pump 载波幅度", unit="Vpp", group="basic", minimum=0)
    pump_mod_duty: float = parameter(default=5, external_name="PUMP_MOD_DUTY", label="Pump 门控占空比", unit="%", group="basic", minimum=1, maximum=50)
    rf_gate_amplitude: float = parameter(default=5, external_name="RF_GATE_AMPLITUDE", label="Pump 门控幅度", unit="Vpp", group="advanced", minimum=0)
    rf_gate_offset: float = parameter(default=2.5, external_name="RF_GATE_OFFSET", label="Pump 门控偏置", unit="V", group="advanced")
    pump_laser_power: float = parameter(default=0.2, external_name="FIXED_PARAMS.Pump_laser_power", label="Pump 光功率", unit="V", group="basic")
    probe_laser_power: float = parameter(default=0.1, external_name="FIXED_PARAMS.Probe_laser_power", label="Probe 光功率", unit="V", group="basic")
    main_magnetic_field: float = parameter(default=9.31, external_name="FIXED_PARAMS.main_magnetic_field", label="主场电流", unit="mA", group="basic")
    temperature: float = parameter(default=100, external_name="FIXED_PARAMS.temperature", label="TEC 目标温度", unit="°C", group="basic")
    temp_switch: float = parameter(default=5, external_name="FIXED_PARAMS.Temp_Switch", label="温控开关固定电平", visible=False)
    time_sequence: float = parameter(default=5, external_name="FIXED_PARAMS.Time_sequence", label="旧时序记录值（不控制输出）", visible=False)
    time_sequence_2: float = parameter(default=0, external_name="FIXED_PARAMS.Time_sequence_2", label="旧时序2记录值（不控制输出）", visible=False)
    daq_response_method: Literal["nearest_fft_bin", "welch_nearest_bin", "welch_local_peak"] = parameter(default="nearest_fft_bin", external_name="DAQ_RESPONSE_METHOD", label="FFT 响应提取方法", group="advanced", options=(("nearest_fft_bin", "整段最近 FFT bin"), ("welch_nearest_bin", "Welch 最近 bin"), ("welch_local_peak", "Welch 局部峰（诊断）")))
    local_peak_rel_window: float = parameter(default=0.01, external_name="LOCAL_PEAK_REL_WINDOW", label="局部峰相对窗口", group="advanced", minimum=0)
    max_pick_freq_error_hz: float = parameter(default=20, external_name="MAX_PICK_FREQ_ERROR_HZ", label="频率拾取误差门槛", unit="Hz", group="advanced", minimum=0)
    exclude_suspicious_for_bandwidth: bool = parameter(default=True, external_name="EXCLUDE_SUSPICIOUS_FOR_BANDWIDTH", label="带宽排除可疑频点", group="advanced")
    offline_lockin_use_integer_cycles: bool = parameter(default=True, external_name="OFFLINE_LOCKIN_USE_INTEGER_CYCLES", label="软件锁相仅用整数周期", group="advanced")
    enable_lorentzian_fit: bool = parameter(default=True, external_name="ENABLE_LORENTZIAN_FIT", label="启用响应拟合", group="basic")
    fit_model: Literal["single_lorentzian", "double_lorentzian"] = parameter(default="double_lorentzian", external_name="FIT_MODEL", label="响应拟合模型", group="basic", options=(("single_lorentzian", "单 Lorentzian"), ("double_lorentzian", "双 Lorentzian／Bloch")))
    lorentzian_fit_max_hwhm_hz: float | None = parameter(default=None, external_name="LORENTZIAN_FIT_MAX_HWHM_HZ", label="单峰 HWHM 上限（空为自动）", unit="Hz", group="advanced", minimum=0.000001)
    lorentzian_fit_min_points: int = parameter(default=6, external_name="LORENTZIAN_FIT_MIN_POINTS", label="单峰最少拟合点", group="advanced", minimum=3)
    lorentzian_fit_use_suspicious: bool = parameter(default=False, external_name="LORENTZIAN_FIT_USE_SUSPICIOUS", label="单峰拟合使用可疑点", group="advanced")
    dlz_gamma_min_hz: float = parameter(default=0.001, external_name="DLZ_GAMMA_MIN_HZ", label="Bloch Γ 下限", unit="Hz", group="advanced", minimum=1e-9)
    dlz_gamma_max_hz: float | None = parameter(default=None, external_name="DLZ_GAMMA_MAX_HZ", label="Bloch Γ 上限（空为自动）", unit="Hz", group="advanced", minimum=0.000001)
    dlz_omega_init_hz: float | None = parameter(default=None, external_name="DLZ_OMEGA_INIT_HZ", label="Bloch Ω 初值（空为峰位）", unit="Hz", group="advanced", minimum=0.000001)
    dlz_omega_min_hz: float = parameter(default=1, external_name="DLZ_OMEGA_MIN_HZ", label="Bloch Ω 下限", unit="Hz", group="advanced", minimum=1e-9)
    dlz_omega_max_hz: float | None = parameter(default=None, external_name="DLZ_OMEGA_MAX_HZ", label="Bloch Ω 上限（空为自动）", unit="Hz", group="advanced", minimum=0.000001)
    dlz_offset_init_v: float = parameter(default=0, external_name="DLZ_OFFSET_INIT_V", label="Bloch 基线初值", unit="V", group="advanced")
    dlz_include_offset: bool = parameter(default=True, external_name="DLZ_INCLUDE_OFFSET", label="Bloch 拟合基线", group="advanced")
    dlz_min_points: int = parameter(default=6, external_name="DLZ_MIN_POINTS", label="Bloch 最少拟合点", group="advanced", minimum=5)
    dlz_use_suspicious: bool = parameter(default=False, external_name="DLZ_USE_SUSPICIOUS", label="Bloch 拟合使用可疑点", group="advanced")

    def __post_init__(self):
        self.sigin2_ac_coupling, self.sigin2_impedance = False, 1000000

    @property
    def xy_dc_voltage(self):
        return (self.larmor_freq_hz - self.a_env_b) / self.a_env_k

    def frequency_axis(self):
        if self.z_rf_freq_log_spaced:
            return np.geomspace(self.z_rf_freq_start, self.z_rf_freq_stop, self.z_rf_freq_points)
        return np.linspace(self.z_rf_freq_start, self.z_rf_freq_stop, self.z_rf_freq_points)

    @classmethod
    def derive_external(cls, values):
        p = cls.from_external(values)
        return {"XY_DC_VOLTAGE": p.xy_dc_voltage, "频率点数": p.z_rf_freq_points,
                "基线条件": "Z OFF；XY 保持开启" if p.z_rf_freq_start == 0 else "扫描不含 0 Hz 基线"}

    def validate_analysis(self):
        errors = []
        for key, value in self.to_external().items():
            if isinstance(value, (int, float)) and not isinstance(value, bool) and not math.isfinite(value):
                errors.append(f"{key} 必须为有限数值")
        for lo, hi, name in ((self.dlz_gamma_min_hz, self.dlz_gamma_max_hz, "Γ"), (self.dlz_omega_min_hz, self.dlz_omega_max_hz, "Ω")):
            if hi is not None and hi <= lo:
                errors.append(f"Bloch {name} 上限必须大于下限")
        return errors

    def validate_model(self):
        errors = self.validate_analysis()
        if not self.run_tag.strip() or any(c in self.run_tag for c in '/\\:*?"<>|') or self.run_tag in (".", ".."):
            errors.append("运行标签不能为空或包含路径字符")
        if self.a_env_k == 0:
            errors.append("A_ENV_K 不能为零")
        if self.z_rf_freq_start >= self.z_rf_freq_stop or (self.z_rf_freq_log_spaced and self.z_rf_freq_start <= 0):
            errors.append("频率范围须递增，对数轴起点须为正")
        if self.hf2_demod0_idx == self.demod3_idx or self.hf2_demod0_osc_idx == self.demod3_osc_idx:
            errors.append("主解调与 RF 解调必须使用不同解调器和振荡器")
        if self.auxout_source_demod_idx != self.hf2_demod0_idx or self.auxout_source_select != 1 or self.sigin2_index != 1:
            errors.append("物理回环必须为主解调 Y → Aux → Signal Input 2")
        if self.temp_switch != 5:
            errors.append("温控恢复必须为 5 V DC + ON")
        if self.pump_mod_freq != self.hf2_demod0_osc_freq or self.xy_carrier_freq != self.hf2_demod0_osc_freq:
            errors.append("Pump 门控、XY 载波与主解调参考频率必须一致")
        if self.acquisition_mode in ("daq_fft_y", "both") and self.hf2_demod0_rate * self.point_duration_s < 4:
            errors.append("Y 记录至少需要 4 个样本")
        return errors
