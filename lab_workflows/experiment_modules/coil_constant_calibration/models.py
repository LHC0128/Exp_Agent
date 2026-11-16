"""Z 线圈常数标定的强类型参数；电流以 mA、频率以 Hz、脉宽以 s 输入。

主磁场固定（GS200 电流或 OFF）后，用主磁场对应的拉莫尔频率作参考锚点，
逐档扫描 Z 线圈电流并测每个电流下的 Y RF 共振中心。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import ClassVar, Literal

from ...common import validate_safety_limit
from ...experiment_params import ExperimentParams, parameter

PredictionPolarity = Literal[
    "unselected",
    "positive_increases_frequency",
    "positive_decreases_frequency",
]

# 主磁场电流限值（A）：GS200 主线圈的安全上限，参数校验与收尾共用。
MAIN_FIELD_LIMIT_A = 0.02
# 1200B 输出阻抗档位与输入阻抗档位；本机 1 Ω 档实测不出力，默认 50 Ω。
AMPLIFIER_OUTPUT_IMPEDANCE_OPTIONS = ((1.0, "1 Ω"), (50.0, "50 Ω"))
AMPLIFIER_INPUT_IMPEDANCE_OPTIONS = ((50.0, "50 Ω"), (1_000_000.0, "1 MΩ"))
# 150 kHz 对应的 GS200 主场电流（按 9.643 kHz/mA 估算），作为默认主场设置。
DEFAULT_MAIN_FIELD_MA = 15.555
DEFAULT_MAIN_FIELD_CENTER_HZ = 150_000.0


@dataclass(slots=True)
class CoilConstantCalibrationParams(ExperimentParams):
    """固定主磁场后扫 Z 线圈电流，用每个电流下的 Mx 共振中心拟合线圈常数。"""

    schema_version: ClassVar[int] = 1
    # 普通气室只有一个 SYSTEM_SETTLE_S；旧快照的温控关闭等待按此迁移。
    system_settle_legacy_defaults: ClassVar[dict[str, float]] = {"TEMP_SWITCH_OFF_DELAY_S": 0.1}
    removed_temperature_keys: ClassVar[tuple[str, ...]] = ()

    @classmethod
    def migrate_external(cls, values, schema_version):
        """历史快照缺 ``MAIN_FIELD_ENABLED`` 时按“电流非零即开启”推断，其余键不变。"""
        migrated = super(CoilConstantCalibrationParams, cls).migrate_external(values, schema_version)
        if "MAIN_FIELD_ENABLED" not in migrated and "MAIN_FIELD_MA" in migrated:
            raw = migrated["MAIN_FIELD_MA"]
            migrated["MAIN_FIELD_ENABLED"] = bool(
                isinstance(raw, (int, float)) and not isinstance(raw, bool) and float(raw) != 0.0)
        return migrated

    # ---- 基础 ----
    run_tag: str = parameter(default="coil_const", external_name="RUN_TAG", label="运行标签", group="basic")
    system_settle_s: float = parameter(default=0.1, external_name="SYSTEM_SETTLE_S", label="系统稳定时间",
        unit="s", group="basic", minimum=0,
        description="配置和温控门控共同稳定的采集前等待；组内每次换点或换态后重新等待。")

    # ---- 线圈驱动链路 ----
    amplifier_gain_db: float = parameter(default=10.0, external_name="AMPLIFIER_GAIN_DB", label="功放增益",
        unit="dB", group="basic", minimum=0, maximum=20,
        description="连接时写入 1200B（0–20 dB，0.5 dB 步进）；预测电流按此值计算")
    amplifier_input_impedance_ohm: float = parameter(default=1_000_000, external_name="AMPLIFIER_INPUT_IMPEDANCE_OHM",
        label="功放输入阻抗", unit="Ω", group="basic", options=AMPLIFIER_INPUT_IMPEDANCE_OPTIONS,
        description="连接时写入 1200B，并把信号源输出负载配成一致（1 MΩ→高阻，50 Ω→50 Ω）")
    amplifier_output_impedance_ohm: float = parameter(default=50.0, external_name="AMPLIFIER_OUTPUT_IMPEDANCE_OHM",
        label="功放输出阻抗", unit="Ω", group="basic", options=AMPLIFIER_OUTPUT_IMPEDANCE_OPTIONS,
        description="连接时写入 1200B；与线圈回路串联，直接参与回路电阻与预测电流")
    amplifier_write_settings: bool = parameter(default=True, external_name="AMPLIFIER_WRITE_SETTINGS",
        label="连接时写入功放设置", group="basic",
        description="true 时按上面三项发送 1200B 命令；false 时只按声明值预测，要求面板与声明一致")
    sense_resistor_ohm: float = parameter(default=10.0, external_name="SENSE_RESISTOR_OHM", label="采样电阻",
        unit="Ω", group="basic", minimum=1e-3, description="I = V_sense / R_sense；填实测阻值")
    sense_resistor_tolerance_percent: float = parameter(default=1.0, external_name="SENSE_RESISTOR_TOLERANCE_PERCENT",
        label="采样电阻误差", unit="%", group="advanced", minimum=0,
        description="仅进入系统误差说明，不改变电流换算")
    damping_resistor_ohm: float = parameter(default=10.0, external_name="DAMPING_RESISTOR_OHM", label="阻尼电阻",
        unit="Ω", group="advanced", minimum=0)
    coil_resistance_ohm: float = parameter(default=0.0, external_name="COIL_RESISTANCE_OHM", label="线圈直流电阻",
        unit="Ω", group="advanced", minimum=0, description="用于预测电流；0 使预测偏大、偏保守")
    resistor_rated_w: float = parameter(default=2.0, external_name="RESISTOR_RATED_W", label="电阻额定功率",
        unit="W", group="advanced", minimum=0.01, description="平均功耗按额定一半校验")
    zero_frames: int = parameter(default=5, external_name="ZERO_FRAMES", label="零偏帧数", group="advanced",
        minimum=1, maximum=100)
    zero_window_s: float = parameter(default=0.02, external_name="ZERO_WINDOW_S", label="零偏/直流采样时间",
        unit="s", group="advanced", minimum=1e-4, maximum=10, legacy_default=0.01,
        description="取 50 Hz 工频周期 20 ms 的整数倍可平均掉工频干扰")
    settle_s: float = parameter(default=0.3, external_name="SETTLE_S", label="输出切换后等待", unit="s",
        group="advanced", minimum=0)
    dc_check_currents_ma: list[float] = parameter(default_factory=lambda: [-50.0, 0.0, 50.0],
        external_name="DC_CHECK_CURRENTS_MA", label="直流 I–V 核对电流", unit="mA", group="advanced",
        description="A04 用这些目标电流实测驱动电压→电流关系（只记录，不设判据）；至少 3 点")
    max_memory_depth: int = parameter(default=1_000_000, external_name="MAX_MEMORY_DEPTH", label="最大存储深度上限",
        unit="点", group="advanced",
        options=tuple((n, f"{n:,}") for n in (1_000, 10_000, 100_000, 1_000_000, 5_000_000, 10_000_000)),
        description="示波器深度为 AUTO；超过此点数时改用此上限深度，实际采样率与点数随帧保存")
    scope_acquire_delay_s: float = parameter(default=0.0, external_name="SCOPE_ACQUIRE_DELAY_S",
        label="示波器停采后等待", unit="s", group="advanced", minimum=0, maximum=30, legacy_default=0.0,
        description="直流帧整帧 STOP 确认后、读取前的等待；默认 0，采集完成即可读")
    scope_current_channel: int = parameter(default=2, external_name="SCOPE_CURRENT_CHANNEL", label="采样电阻通道",
        group="advanced", minimum=1, maximum=4)

    # ---- 主磁场与参考锚点 ----
    main_field_enabled: bool = parameter(default=True, external_name="MAIN_FIELD_ENABLED",
        label="开启主磁场", group="basic",
        description="false 时只关闭 GS200 输出；true 时按下面的电流设主场并开输出")
    main_field_ma: float = parameter(default=DEFAULT_MAIN_FIELD_MA, external_name="MAIN_FIELD_MA",
        label="主场电流", unit="mA", group="basic", safety_key="main_magnetic_field",
        active_when="MAIN_FIELD_ENABLED",
        description="150 kHz 约对应 15.555 mA（按 9.643 kHz/mA）；按本机实测值手填")
    main_field_center_hz: float = parameter(default=DEFAULT_MAIN_FIELD_CENTER_HZ,
        external_name="MAIN_FIELD_CENTER_HZ", label="主场对应的拉莫尔频率", unit="Hz", group="basic",
        minimum=0.0,
        description="主磁场开启时 Z 电流为 0 处的共振中心（手填）；关闭时作为残余场中心的初始猜测")

    # ---- 电流轴 ----
    current_start_ma: float = parameter(default=-200.0, external_name="CURRENT_START_MA", label="电流起点",
        unit="mA", group="basic")
    current_stop_ma: float = parameter(default=200.0, external_name="CURRENT_STOP_MA", label="电流终点",
        unit="mA", group="basic")
    current_step_ma: float = parameter(default=50.0, external_name="CURRENT_STEP_MA", label="电流步进",
        unit="mA", group="basic", minimum=0.001)
    bidirectional_scan: bool = parameter(default=True, external_name="BIDIRECTIONAL_SCAN", label="正反向电流扫描",
        group="basic", description="正向到端点后反向返回，不重复端点")
    current_settle_time_s: float = parameter(default=0.2, external_name="CURRENT_SETTLE_TIME_S",
        label="电流稳定等待", unit="s", group="advanced", minimum=0)

    # ---- 频率预测与扫描 ----
    initial_hz_per_ma: float = parameter(default=602.0, external_name="INITIAL_HZ_PER_MA",
        label="预测频率斜率", unit="Hz/mA", group="basic", minimum=0.001,
        description="用于逐点窗口中心预测（86 nT/mA × 7 Hz/nT），不作为最终标定结果")
    prediction_polarity: PredictionPolarity = parameter(default="positive_increases_frequency",
        external_name="PREDICTION_POLARITY", label="电流极性预测", group="basic",
        options=(("unselected", "请选择"),
                 ("positive_increases_frequency", "正电流提高共振频率"),
                 ("positive_decreases_frequency", "正电流降低共振频率")),
        description="用于窗口中心预测；选反时粗扫峰落在窗口边缘会明确报错，翻转后重跑即可")
    coarse_half_width_hz: float = parameter(default=20_000.0, external_name="COARSE_HALF_WIDTH_HZ",
        label="粗扫半宽", unit="Hz", group="basic", minimum=0.001,
        description="每个电流点先在预测中心 ±该半宽做粗扫找峰，再在峰附近细扫")
    coarse_step_hz: float = parameter(default=1_000.0, external_name="COARSE_STEP_HZ",
        label="粗扫步进", unit="Hz", group="basic", minimum=0.001)
    frequency_half_width_hz: float = parameter(default=4_000.0, external_name="FREQUENCY_HALF_WIDTH_HZ",
        label="细扫半宽", unit="Hz", group="basic", minimum=0.001)
    frequency_step_hz: float = parameter(default=100.0, external_name="FREQUENCY_STEP_HZ",
        label="细扫步进", unit="Hz", group="basic", minimum=0.001)
    near_zero_scan_floor_hz: float = parameter(default=8_000.0, external_name="NEAR_ZERO_SCAN_FLOOR_HZ",
        label="零场不扫频下限", unit="Hz", group="basic", minimum=0.0,
        description="预测 |中心| 低于此值的电流点只测电流、不扫 Y RF（主磁场关闭且接近过零时）")
    near_zero_fit_floor_hz: float = parameter(default=4_000.0, external_name="NEAR_ZERO_FIT_FLOOR_HZ",
        label="零场不拟合下限", unit="Hz", group="advanced", minimum=0.0,
        description="拟合中心绝对值低于此值的曲线剔除，不进入线性标定")

    # ---- Mx 条件 ----
    y_rf_amplitude_vpp: float = parameter(default=0.05, external_name="Y_RF_AMPLITUDE_VPP",
        label="Y RF 扫频幅度", unit="Vpp", group="basic", minimum=0.0, safety_key="rf_coil")
    frequency_duration_s: float = parameter(default=0.1, external_name="FREQUENCY_DURATION_S",
        label="频点采样时间", unit="s", group="basic", minimum=0.001)
    temp_switch_on_lag_s: float = parameter(default=1.0, external_name="TEMP_SWITCH_ON_LAG_S",
        label="每频点温控恢复等待", unit="s", group="basic", minimum=0.0)
    r_bad_point_std_threshold_v: float = parameter(default=0.01, external_name="R_BAD_POINT_STD_THRESHOLD_V",
        label="R 坏点标准差阈值", unit="V", group="advanced", minimum=0.0)
    r_point_max_attempts: int = parameter(default=3, external_name="R_POINT_MAX_ATTEMPTS",
        label="R 点最大采集次数", group="advanced", minimum=1)
    demod_idx: int = parameter(default=0, external_name="DEMOD_IDX", label="锁相放大器 Demod 索引",
        group="advanced", minimum=0)
    demod_osc_idx: int = parameter(default=0, external_name="DEMOD_OSC_IDX", label="锁相放大器 振荡器索引",
        group="advanced", minimum=0)
    hf2_signal_range_v: float = parameter(default=2.0, external_name="HF2_SIGNAL_RANGE_V",
        label="锁相放大器 Input1 量程", unit="V", group="advanced", minimum=0.001)
    response_rate_sa_s: float = parameter(default=1000.0, external_name="RESPONSE_RATE_SA_S",
        label="响应请求采样率", unit="Sa/s", group="advanced", minimum=1.0)
    response_time_constant_s: float = parameter(default=0.001, external_name="RESPONSE_TIME_CONSTANT_S",
        label="响应时间常数", unit="s", group="advanced", minimum=0.0)
    response_demod_order: int = parameter(default=4, external_name="RESPONSE_DEMOD_ORDER",
        label="响应解调阶数", group="advanced", minimum=1)
    pump_carrier_frequency_hz: float = parameter(default=100e6, external_name="PUMP_CARRIER_FREQUENCY_HZ",
        label="Pump AOM 载波频率", unit="Hz", group="advanced", minimum=1.0)
    pump_carrier_amplitude_vpp: float = parameter(default=0.18, external_name="PUMP_CARRIER_AMPLITUDE_VPP",
        label="Pump AOM 载波幅度", unit="Vpp", group="advanced", minimum=0.0, safety_key="Pump_modulation")
    pump_gate_voltage_v: float = parameter(default=5.0, external_name="PUMP_GATE_VOLTAGE_V",
        label="Pump RF 开关常开电平", unit="V", group="advanced", minimum=5.0, maximum=5.0,
        safety_key="Time_sequence")
    pump_laser_power_v: float = parameter(default=0.5, external_name="FIXED_PARAMS.Pump_laser_power",
        label="Pump 光功率", unit="V", group="basic", safety_key="Pump_laser_power")
    probe_laser_power_v: float = parameter(default=0.3, external_name="FIXED_PARAMS.Probe_laser_power",
        label="Probe 光功率", unit="V", group="basic", safety_key="Probe_laser_power")
    temperature_c: float = parameter(default=120.0, external_name="FIXED_PARAMS.temperature",
        label="气室温度", unit="°C", group="basic", safety_key="temperature")

    # ---- π 脉冲设计预览 ----
    expected_k_nt_per_ma: float = parameter(default=86.0, external_name="EXPECTED_K_NT_PER_MA",
        label="预估线圈常数", unit="nT/mA", group="advanced", minimum=0.001,
        description="只用于 π 工作点与标定范围预览，不参与拟合")
    pulse_width_s: float = parameter(default=5e-6, external_name="PULSE_WIDTH_S", label="π 脉冲宽度",
        unit="s", group="advanced", minimum=1e-9)
    pulse_repetition_hz: float = parameter(default=20000.0, external_name="PULSE_REPETITION_HZ",
        label="π 脉冲重复率", unit="Hz", group="advanced", minimum=1e-6)
    pulse_field_margin: float = parameter(default=1.2, external_name="PULSE_FIELD_MARGIN",
        label="π 电流覆盖余量", group="advanced", minimum=1.0, maximum=10.0)

    # ---- 分析 ----
    linear_r_squared_min: float = parameter(default=0.99, external_name="LINEAR_R_SQUARED_MIN",
        label="线性标定最低 R²", group="advanced", minimum=0.0, maximum=1.0)
    gyromagnetic_ratio_hz_per_nt: float = parameter(default=7.0, external_name="GYROMAGNETIC_RATIO_HZ_PER_NT",
        label="频率-磁场换算系数", unit="Hz/nT", group="advanced", minimum=0.001,
        description="作为精确配置常数使用，不额外传播其系统不确定度")
    min_accepted_points: int = parameter(default=4, external_name="MIN_ACCEPTED_POINTS",
        label="最少有效电流点", group="advanced", minimum=2)
    min_branch_points: int = parameter(default=2, external_name="MIN_BRANCH_POINTS",
        label="过零两侧最少点数", group="advanced", minimum=1)
    branch_slope_tolerance: float = parameter(default=0.05, external_name="BRANCH_SLOPE_TOLERANCE",
        label="两支斜率一致性容许", group="advanced", minimum=0.001, maximum=0.5)

    # ---- 派生量 ----
    @property
    def gain(self) -> float:
        return 10.0 ** (self.amplifier_gain_db / 20.0)

    @property
    def loop_resistance_ohm(self) -> float:
        """线圈回路总电阻：采样电阻 + 阻尼电阻 + 线圈直流电阻 + 功放输出阻抗。"""
        return (self.sense_resistor_ohm + self.damping_resistor_ohm + self.coil_resistance_ohm
                + self.amplifier_output_impedance_ohm)

    @property
    def amps_per_drive_volt(self) -> float:
        """按写入的功放增益与回路总电阻预测的直流 I/V_drive。"""
        return self.gain / self.loop_resistance_ohm

    @property
    def polarity_sign(self) -> float:
        return 1.0 if self.prediction_polarity == "positive_increases_frequency" else -1.0

    @property
    def expected_k_hz_per_ma(self) -> float:
        return self.expected_k_nt_per_ma * self.gyromagnetic_ratio_hz_per_nt

    def forward_currents_ma(self) -> list[float]:
        span = self.current_stop_ma - self.current_start_ma
        count = int(round(span / self.current_step_ma))
        return [float(self.current_start_ma + index * self.current_step_ma) for index in range(count + 1)]

    def current_targets_ma(self) -> list[float]:
        forward = self.forward_currents_ma()
        if self.bidirectional_scan and len(forward) > 1:
            return [*forward, *forward[-2::-1]]
        return forward

    def current_directions(self) -> list[str]:
        forward = self.forward_currents_ma()
        if self.bidirectional_scan and len(forward) > 1:
            return ["forward"] * len(forward) + ["reverse"] * (len(forward) - 1)
        return ["forward"] * len(forward)

    def signed_center_hz(self, current_ma: float, *, center_hz: float | None = None,
                         k_hz_per_ma: float | None = None) -> float:
        """预测的有符号共振中心：``center + 极性·斜率·I``。"""
        center = self.main_field_center_hz if center_hz is None else float(center_hz)
        slope = self.initial_hz_per_ma if k_hz_per_ma is None else float(k_hz_per_ma)
        return center + self.polarity_sign * slope * float(current_ma)

    def predicted_center_hz(self, current_ma: float, *, center_hz: float | None = None,
                            k_hz_per_ma: float | None = None) -> float:
        return abs(self.signed_center_hz(current_ma, center_hz=center_hz, k_hz_per_ma=k_hz_per_ma))

    def crossing_current_ma(self, *, center_hz: float | None = None,
                            k_hz_per_ma: float | None = None) -> float:
        center = self.main_field_center_hz if center_hz is None else float(center_hz)
        slope = self.initial_hz_per_ma if k_hz_per_ma is None else float(k_hz_per_ma)
        return -self.polarity_sign * center / slope

    def frequency_axis(self, center_hz: float) -> list[float]:
        count = int(round(2.0 * self.frequency_half_width_hz / self.frequency_step_hz))
        return [float(center_hz - self.frequency_half_width_hz + index * self.frequency_step_hz)
                for index in range(count + 1)]

    def coarse_frequency_axis(self, center_hz: float) -> list[float]:
        count = int(round(2.0 * self.coarse_half_width_hz / self.coarse_step_hz))
        return [float(center_hz - self.coarse_half_width_hz + index * self.coarse_step_hz)
                for index in range(count + 1)]

    def branch_counts(self, *, center_hz: float | None = None, k_hz_per_ma: float | None = None) -> dict:
        """按预测过零点统计电流轴两侧的目标点数与几何判定。"""
        crossing = self.crossing_current_ma(center_hz=center_hz, k_hz_per_ma=k_hz_per_ma)
        targets = self.current_targets_ma()
        low = [value for value in targets if value < crossing]
        high = [value for value in targets if value > crossing]
        inside = self.current_start_ma < crossing < self.current_stop_ma
        return {
            "crossing_current_ma": crossing,
            "crossing_inside": bool(inside),
            "branch_counts": {"below": len(low), "above": len(high)},
            "linear_case": not inside,
        }

    def pulse_design(self) -> dict:
        """按预估常数给出 π 脉冲所需的场/电流与覆盖、RWA 余量预览。"""
        f1_hz = 1.0 / (2.0 * self.pulse_width_s)
        b1_nt = f1_hz / self.gyromagnetic_ratio_hz_per_nt
        i_pi_ma = b1_nt / self.expected_k_nt_per_ma
        i_required_ma = self.pulse_field_margin * i_pi_ma
        duty = self.pulse_width_s * self.pulse_repetition_hz
        return {
            "pulse_width_s": float(self.pulse_width_s),
            "rabi_frequency_hz": f1_hz,
            "b1_nt": b1_nt,
            "i_pi_ma": i_pi_ma,
            "i_required_ma": i_required_ma,
            "current_covered": bool(self.current_start_ma <= i_required_ma <= self.current_stop_ma),
            "larmor_at_zero_hz": self.main_field_center_hz,
            "rwa_margin": self.main_field_center_hz * self.pulse_width_s,
            "duty": duty,
        }

    def validate_model(self) -> list[str]:
        errors: list[str] = []
        if not self.run_tag.strip() or any(character in self.run_tag for character in ("/", "\\", ":", "\0")):
            errors.append("RUN_TAG 不能为空或包含路径分隔符")
        if not (0 <= self.amplifier_gain_db <= 20) or round(self.amplifier_gain_db * 2) != self.amplifier_gain_db * 2:
            errors.append("AMPLIFIER_GAIN_DB 须为 0–20 dB、0.5 dB 步进（1200B 只接受这些档位）")
        if self.amplifier_input_impedance_ohm not in (50.0, 1_000_000.0):
            errors.append("AMPLIFIER_INPUT_IMPEDANCE_OHM 只能是 50 或 1000000 Ω")
        if self.amplifier_output_impedance_ohm not in (1.0, 50.0):
            errors.append("AMPLIFIER_OUTPUT_IMPEDANCE_OHM 只能是 1 或 50 Ω")
        if self.frequency_step_hz <= 0 or self.frequency_half_width_hz <= 0:
            errors.append("细扫半宽与步进必须为正")
        else:
            count = 2.0 * self.frequency_half_width_hz / self.frequency_step_hz
            if not math.isclose(count, round(count), rel_tol=0.0, abs_tol=1e-9):
                errors.append("细扫宽度必须包含整数个 FREQUENCY_STEP_HZ")
        if self.coarse_step_hz <= 0 or self.coarse_half_width_hz <= 0:
            errors.append("粗扫半宽与步进必须为正")
        else:
            count = 2.0 * self.coarse_half_width_hz / self.coarse_step_hz
            if not math.isclose(count, round(count), rel_tol=0.0, abs_tol=1e-9):
                errors.append("粗扫宽度必须包含整数个 COARSE_STEP_HZ")
            if self.coarse_half_width_hz < self.frequency_half_width_hz:
                errors.append("COARSE_HALF_WIDTH_HZ 必须不小于 FREQUENCY_HALF_WIDTH_HZ")
        if self.near_zero_scan_floor_hz < self.frequency_half_width_hz:
            errors.append("NEAR_ZERO_SCAN_FLOOR_HZ 必须不小于 FREQUENCY_HALF_WIDTH_HZ，保证扫频下限为正")
        if self.prediction_polarity == "unselected":
            errors.append("PREDICTION_POLARITY 必须明确选择")
        if self.main_field_enabled:
            try:
                validate_safety_limit("main_magnetic_field", float(self.main_field_ma))
            except ValueError as exc:
                errors.append(f"MAIN_FIELD_MA: {exc}")
            if not math.isfinite(self.main_field_center_hz) or self.main_field_center_hz <= self.coarse_half_width_hz:
                errors.append("主磁场开启时 MAIN_FIELD_CENTER_HZ 必须大于 COARSE_HALF_WIDTH_HZ，保证窗口下限为正")
        elif self.main_field_center_hz < 0 or not math.isfinite(self.main_field_center_hz):
            errors.append("主磁场关闭时 MAIN_FIELD_CENTER_HZ 是残余场中心猜测，必须为有限非负数")
        if self.current_start_ma >= self.current_stop_ma:
            errors.append("CURRENT_START_MA 必须小于 CURRENT_STOP_MA")
        span = self.current_stop_ma - self.current_start_ma
        if self.current_step_ma <= 0:
            errors.append("CURRENT_STEP_MA 必须为正")
            count = float("nan")
        else:
            count = span / self.current_step_ma
        if math.isfinite(count) and not math.isclose(count, round(count), rel_tol=0.0, abs_tol=1e-9):
            errors.append("电流范围必须包含整数个步进")
        if not self.dc_check_currents_ma or len(self.dc_check_currents_ma) < 3:
            errors.append("DC_CHECK_CURRENTS_MA 至少需要 3 个电流点")
        else:
            for current_ma in self.dc_check_currents_ma:
                try:
                    validate_safety_limit("z_pulse_coil_current", current_ma * 1e-3)
                except ValueError as exc:
                    errors.append(f"DC_CHECK_CURRENTS_MA: {exc}")
        drive = self.amps_per_drive_volt
        targets = self.current_targets_ma() if self.current_step_ma > 0 and self.current_start_ma < self.current_stop_ma else []
        for current_ma in targets:
            try:
                validate_safety_limit("z_pulse_coil_current", current_ma * 1e-3)
            except ValueError as exc:
                errors.append(f"电流轴: {exc}")
                break
        for current_ma in (self.current_start_ma, self.current_stop_ma):
            try:
                validate_safety_limit("Z_magnetic_field", current_ma * 1e-3 / drive)
            except ValueError as exc:
                errors.append(f"预测驱动电压: {exc}")
        if targets:
            peak_a = max(abs(value) for value in targets) * 1e-3
            resistor = max(self.sense_resistor_ohm, self.damping_resistor_ohm)
            if peak_a**2 * resistor > self.resistor_rated_w / 2:
                errors.append("峰值电流在采样/阻尼电阻上的功耗超过额定功率一半")
        for current_ma in targets:
            predicted = self.predicted_center_hz(current_ma)
            if predicted < self.near_zero_scan_floor_hz:
                continue
            if predicted - self.coarse_half_width_hz <= 0:
                errors.append(f"电流 {current_ma:g} mA 的预测粗扫下限不大于 0 Hz")
                break
        if self.pulse_width_s <= 0 or self.pulse_repetition_hz <= 0:
            errors.append("PULSE_WIDTH_S 与 PULSE_REPETITION_HZ 必须为正")
        elif self.pulse_width_s * self.pulse_repetition_hz > 0.5:
            errors.append("PULSE_WIDTH_S × PULSE_REPETITION_HZ 不得超过 0.5（占空比过大）")
        if self.expected_k_nt_per_ma <= 0 or self.gyromagnetic_ratio_hz_per_nt <= 0:
            errors.append("EXPECTED_K_NT_PER_MA 与 GYROMAGNETIC_RATIO_HZ_PER_NT 必须为正")
        if self.min_accepted_points < 2:
            errors.append("MIN_ACCEPTED_POINTS 至少为 2")
        if self.min_branch_points < 1:
            errors.append("MIN_BRANCH_POINTS 至少为 1")
        if not 0 < self.branch_slope_tolerance <= 0.5:
            errors.append("BRANCH_SLOPE_TOLERANCE 必须在 (0, 0.5] 内")
        return errors

    @classmethod
    def derive_external(cls, values):
        params = cls.from_external(values)
        geometry = params.branch_counts()
        design = params.pulse_design()
        targets = params.current_targets_ma()
        return dict(
            predicted_amps_per_volt=params.amps_per_drive_volt,
            loop_resistance_ohm=params.loop_resistance_ohm,
            gain=params.gain,
            target_currents_ma=targets,
            predicted_centers_hz=[params.signed_center_hz(value) for value in targets],
            anchor_center_hz=params.main_field_center_hz,
            crossing_current_ma=geometry["crossing_current_ma"],
            crossing_inside=geometry["crossing_inside"],
            branch_counts=geometry["branch_counts"],
            linear_case=geometry["linear_case"],
            pulse_design=design,
        )
