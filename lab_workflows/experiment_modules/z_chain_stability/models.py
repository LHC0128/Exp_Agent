"""Z 链路零点与脉冲角稳定性的强类型参数；电流以 mA、时间以 µs/s 输入。

本实验只测电学链路，不连接原子、TEC 或 GS200：
A03 在 DG 0 V 下记录原始零点，A04 在可设定基线下记录工作零点，A05 连续打 ±π 双脉冲并统计脉冲角。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import ClassVar

from ...experiment_params import ExperimentParams, parameter


def ten_division_window(window_s: float) -> float:
    """按十格 1-2-5 时基规则返回实际采集窗口；与 ``capture_config`` 的换算一致。"""
    exponent = math.floor(math.log10(window_s / 10.0))
    for mantissa in (1.0, 2.0, 5.0, 10.0):
        candidate = mantissa * 10.0 ** exponent
        if candidate >= window_s / 10.0 * (1 - 1e-12):
            return candidate * 10.0
    raise AssertionError("unreachable")


@dataclass
class ZChainStabilityParams(ExperimentParams):
    schema_version: ClassVar[int] = 4

    @classmethod
    def migrate_external(cls, values, schema_version):
        """v1 的 I–V 拟合与电压模式、v2 的快速段已删除；旧配置加载时丢弃对应键。"""
        migrated = super().migrate_external(values, schema_version)
        if schema_version < 2:
            for key in ("BASELINE_MODE", "BASELINE_VOLTAGE_V", "IV_CHECK_VOLTAGES_V", "IV_CHECK_FRAMES"):
                migrated.pop(key, None)
        if schema_version < 3:
            for key in ("ZERO_FAST_INTERVAL_S", "ZERO_FAST_DURATION_S"):
                migrated.pop(key, None)
        return migrated

    run_tag: str = parameter(default="z_chain_stability", external_name="RUN_TAG", label="运行标签", group="basic")

    # ---- 链路与功放（与 coil-drive-characterization 同口径） ----
    amplifier_gain_db: float = parameter(default=20.0, external_name="AMPLIFIER_GAIN_DB", label="功放增益", unit="dB",
        minimum=0, maximum=20, group="basic",
        description="连接时写入 1200B（0–20 dB，0.5 dB 步进）；预测电流按此值计算")
    amplifier_input_impedance_ohm: float = parameter(default=1_000_000, external_name="AMPLIFIER_INPUT_IMPEDANCE_OHM",
        label="功放输入阻抗", unit="Ω", group="basic", options=((50.0, "50 Ω"), (1_000_000.0, "1 MΩ")),
        description="连接时写入 1200B，并把信号源输出负载配成一致")
    amplifier_output_impedance_ohm: float = parameter(default=50.0, external_name="AMPLIFIER_OUTPUT_IMPEDANCE_OHM",
        label="功放输出阻抗", unit="Ω", group="basic", options=((50.0, "50 Ω"), (1.0, "1 Ω")),
        description="50 Ω 与线圈回路串联；本机 1 Ω 档实测不出输出，默认 50 Ω")
    amplifier_write_settings: bool = parameter(default=True, external_name="AMPLIFIER_WRITE_SETTINGS",
        label="连接时写入功放设置", group="basic")
    sense_resistor_ohm: float = parameter(default=10.0, external_name="SENSE_RESISTOR_OHM", label="采样电阻",
        unit="Ω", minimum=1e-3, description="I = V_sense / R_sense")
    damping_resistor_ohm: float = parameter(default=10.0, external_name="DAMPING_RESISTOR_OHM", label="阻尼电阻",
        unit="Ω", minimum=0)
    coil_resistance_ohm: float = parameter(default=0.0, external_name="COIL_RESISTANCE_OHM", label="线圈直流电阻",
        unit="Ω", minimum=0, description="用于预测电流；0 使预测偏大、偏保守")
    resistor_rated_w: float = parameter(default=2.0, external_name="RESISTOR_RATED_W", label="电阻额定功率",
        unit="W", minimum=0.01, description="平均功耗按额定一半校验")

    # ---- 示波器与采集（复用线圈驱动实验的 AUTO 深度与居中标定口径） ----
    settle_s: float = parameter(default=0.3, external_name="SETTLE_S", label="输出切换后等待", unit="s", minimum=0)
    scope_acquire_delay_s: float = parameter(default=1.5, external_name="SCOPE_ACQUIRE_DELAY_S",
        label="示波器停采后等待", unit="s", minimum=0, maximum=30,
        description="STOP 置位后波形缓冲尚不可读；立即读取会返回空数据")
    scope_current_channel: int = parameter(default=2, external_name="SCOPE_CURRENT_CHANNEL", label="采样电阻通道",
        minimum=1, maximum=4)
    scope_sync_channel: int = parameter(default=3, external_name="SCOPE_SYNC_CHANNEL", label="同步触发通道",
        minimum=1, maximum=4)
    sync_trigger_level_v: float = parameter(default=1.0, external_name="SYNC_TRIGGER_LEVEL_V", label="同步触发电平", unit="V")
    sync_scale_v_div: float = parameter(default=2.0, external_name="SYNC_SCALE_V_DIV", label="同步通道垂直档位",
        unit="V/div", minimum=0.0005, maximum=10)
    max_memory_depth: int = parameter(default=10_000, external_name="MAX_MEMORY_DEPTH", label="最大存储深度上限",
        unit="点", options=tuple((n, f"{n:,}") for n in (1_000, 10_000, 100_000, 1_000_000)),
        description="示波器深度为 AUTO，由仪器按时基给出采样率；超过此点数时改用此上限。上限不是精确深度，以回读为准")
    zero_window_s: float = parameter(default=0.02, external_name="ZERO_WINDOW_S", label="零点/直采窗口", unit="s",
        minimum=1e-4, maximum=10, description="取 50 Hz 工频周期 20 ms 的整数倍可平均掉工频干扰")

    # ---- A03：DG 0 V 零点 ----
    zero_frames: int = parameter(default=5, external_name="ZERO_FRAMES", label="首尾零点帧数", minimum=1, maximum=100)
    zero_log_interval_s: float = parameter(default=10.0, external_name="ZERO_LOG_INTERVAL_S", label="零点记录间隔",
        unit="s", minimum=0.05, maximum=600,
        description="相邻两帧的目标间隔；单帧至少占用约 2 s（示波器停采等待与读帧），短于此值时为背靠背采集")
    zero_log_duration_s: float = parameter(default=3600.0, external_name="ZERO_LOG_DURATION_S", label="零点记录时长",
        unit="s", minimum=1, maximum=7 * 24 * 3600)
    zero_save_all_frames: bool = parameter(default=True, external_name="ZERO_SAVE_ALL_FRAMES",
        label="保存每帧原始波形",
        description="开启时每测一帧都存 NPZ，可离线看 20 ms 窗口内的噪声；关闭后按「零点原始帧保存间隔」稀疏保存")
    zero_raw_save_interval_s: float = parameter(default=600.0, external_name="ZERO_RAW_SAVE_INTERVAL_S",
        label="零点原始帧保存间隔", unit="s", minimum=0,
        description="仅在关闭「保存每帧原始波形」时生效；0 表示只存均值，不存原始帧")

    # ---- A04：目标电流 + 手动 I–V 换算 ----
    baseline_current_ma: float = parameter(default=0.0, external_name="BASELINE_CURRENT_MA", label="基线目标电流",
        unit="mA", group="basic", description="目标线圈电流；0 即零流基线")
    conversion_amps_per_volt: float = parameter(default=0.137027, external_name="CONVERSION_AMPS_PER_VOLT",
        label="电流/电压换算", unit="A/V", group="basic", minimum=1e-9,
        description="I = k·V + 偏置；默认取线圈常数标定 1116_173127 的 k_dc_a_per_v=0.137027 A/V")
    conversion_offset_ma: float = parameter(default=0.0, external_name="CONVERSION_OFFSET_MA",
        label="换算偏置电流", unit="mA", group="basic",
        description="DG 0 V 时的功放零偏电流（可填 A03 的 I_off）；V_set = (I_target − I_offset) / k")

    # ---- A05：±π 双脉冲与脉冲角 ----
    pulse_period_us: float = parameter(default=100.0, external_name="PULSE_PERIOD_US", label="双脉冲周期",
        unit="µs", minimum=10, description="+π 与 −π 成对的一个周期；脉冲事件间隔为周期的一半")
    pulse_width_us: float = parameter(default=4.0, external_name="PULSE_WIDTH_US", label="单脉冲宽度",
        unit="µs", minimum=0.2, description="含升降沿的总宽度")
    pulse_edge_us: float = parameter(default=1.0, external_name="PULSE_EDGE_US", label="升/降沿宽度",
        unit="µs", minimum=0.05, description="升余弦沿；单脉冲面积 = (宽度 − 沿宽) × 峰值")
    pulse_peak_current_ma: float = parameter(default=255.0, external_name="PULSE_PEAK_CURRENT_MA",
        label="脉冲峰值电流", unit="mA", minimum=0.1,
        description="相对基线的 +π 峰值；−π 取相反号。默认按 K=653.79 Hz/mA 估为约 1.0π")
    pulse_baseline_v: float = parameter(default=0.0, external_name="PULSE_BASELINE_V",
        label="脉冲直流偏置", unit="V", group="basic",
        description="±π 脉冲所骑的 DG 直流偏置，与 A04 的基线目标无关；0 V 表示不写偏置，"
                    "此时实测电流不为 0（功放零偏）")
    pulse_capture_interval_s: float = parameter(default=60.0, external_name="PULSE_CAPTURE_INTERVAL_S",
        label="脉冲捕获间隔", unit="s", minimum=1)
    pulse_capture_periods: int = parameter(default=4, external_name="PULSE_CAPTURE_PERIODS",
        label="每次捕获周期数", minimum=2, maximum=20)
    pulse_duration_s: float = parameter(default=3600.0, external_name="PULSE_DURATION_S", label="脉冲列持续时长",
        unit="s", minimum=1, maximum=7 * 24 * 3600)
    pulse_raw_save_interval_s: float = parameter(default=600.0, external_name="PULSE_RAW_SAVE_INTERVAL_S",
        label="脉冲原始帧保存间隔", unit="s", minimum=0)
    pulse_tail_window_us: float = parameter(default=20.0, external_name="PULSE_TAIL_WINDOW_US",
        label="单脉冲积分窗", unit="µs", minimum=1,
        description="从脉冲起点到积分结束；须覆盖 RL 衰减尾与退出沿")
    coil_constant_hz_per_ma: float = parameter(default=653.79, external_name="COIL_CONSTANT_HZ_PER_MA",
        label="Z 线圈常数", unit="Hz/mA", minimum=1e-6,
        description="由 Z 线圈常数标定给出；脉冲角 θ = 2π·K·∫I dt")

    @property
    def gain(self) -> float:
        return 10.0 ** (self.amplifier_gain_db / 20.0)

    @property
    def loop_resistance_ohm(self) -> float:
        return (self.sense_resistor_ohm + self.damping_resistor_ohm + self.coil_resistance_ohm
                + self.amplifier_output_impedance_ohm)

    @property
    def amps_per_drive_volt(self) -> float:
        return self.gain / self.loop_resistance_ohm

    @property
    def baseline_setpoint_voltage(self) -> float:
        """由目标电流与手动 I–V 换算得到 DG 电压：V = (I_target − I_offset) / k。"""
        return (self.baseline_current_ma - self.conversion_offset_ma) * 1e-3 / self.conversion_amps_per_volt

    @property
    def pulse_baseline_voltage(self) -> float:
        """±π 脉冲的直流偏置电压；0 V 表示不写偏置，与 A04 的基线目标无关。"""
        return self.pulse_baseline_v

    @property
    def pulse_area_s(self) -> float:
        """升余弦沿的单脉冲等效面积：(宽度 − 沿宽) × 峰值。"""
        return (self.pulse_width_us - self.pulse_edge_us) * 1e-6

    def pulse_angle_pi(self, current_ma: float | None = None) -> float:
        """按线圈常数预计的单脉冲转角（单位 π）。"""
        current = self.pulse_peak_current_ma if current_ma is None else current_ma
        return 2.0 * self.coil_constant_hz_per_ma * current * self.pulse_area_s

    @property
    def pulse_vpp(self) -> float:
        """双极任意波的峰峰值：+peak 与 −peak 相对基线。"""
        return 2.0 * self.pulse_peak_current_ma * 1e-3 / self.amps_per_drive_volt

    @property
    def pulse_capture_window_s(self) -> float:
        return self.pulse_capture_periods * self.pulse_period_us * 1e-6

    @property
    def zero_log_points(self) -> int:
        return max(1, int(self.zero_log_duration_s / self.zero_log_interval_s))

    def validate_model(self) -> list[str]:
        errors = []
        if not self.run_tag.strip() or any(c in self.run_tag for c in ("/", "\\", ":", "\0")):
            errors.append("RUN_TAG 不能为空或包含路径分隔符")
        if self.scope_current_channel == self.scope_sync_channel:
            errors.append("采样电阻通道与同步触发通道不能相同")
        if not 0 <= self.amplifier_gain_db <= 20 or round(self.amplifier_gain_db * 2) != self.amplifier_gain_db * 2:
            errors.append("AMPLIFIER_GAIN_DB 须为 0–20 dB、0.5 dB 步进")
        if self.amplifier_input_impedance_ohm not in (50.0, 1_000_000.0):
            errors.append("AMPLIFIER_INPUT_IMPEDANCE_OHM 只能是 50 或 1000000 Ω")
        if self.amplifier_output_impedance_ohm not in (1.0, 50.0):
            errors.append("AMPLIFIER_OUTPUT_IMPEDANCE_OHM 只能是 1 或 50 Ω")
        if self.zero_log_interval_s <= 0:
            errors.append("ZERO_LOG_INTERVAL_S 必须为正")
        if self.zero_log_duration_s < self.zero_log_interval_s:
            errors.append("ZERO_LOG_DURATION_S 不能短于记录间隔")
        if self.zero_window_s <= 0:
            errors.append("ZERO_WINDOW_S 必须为正")
        else:
            actual_window = ten_division_window(self.zero_window_s)
            if abs(actual_window - self.zero_window_s) > 1e-9 * max(1.0, actual_window):
                errors.append(f"ZERO_WINDOW_S 须落在十格 1-2-5 时基上（如 0.02、0.05、0.1、0.2），"
                              f"否则实际窗口会变成 {actual_window:g} s")
        if not math.isfinite(self.baseline_current_ma):
            errors.append("BASELINE_CURRENT_MA 必须为有限数")
        if not math.isfinite(self.conversion_amps_per_volt) or self.conversion_amps_per_volt <= 0:
            errors.append("CONVERSION_AMPS_PER_VOLT 必须为正的有限数")
        if not math.isfinite(self.conversion_offset_ma):
            errors.append("CONVERSION_OFFSET_MA 必须为有限数")
        if self.pulse_period_us <= 2 * self.pulse_width_us:
            errors.append("PULSE_PERIOD_US 必须大于两倍 PULSE_WIDTH_US（一个周期内 +/− 两个脉冲）")
        if self.pulse_width_us < 2 * self.pulse_edge_us:
            errors.append("PULSE_WIDTH_US 至少为两倍 PULSE_EDGE_US")
        if self.pulse_tail_window_us < self.pulse_width_us:
            errors.append("PULSE_TAIL_WINDOW_US 不能短于脉冲宽度")
        if self.pulse_tail_window_us >= self.pulse_period_us / 2:
            errors.append("PULSE_TAIL_WINDOW_US 必须小于半个周期，避免跨到下一个脉冲")
        if self.pulse_peak_current_ma <= 0 or not math.isfinite(self.pulse_peak_current_ma):
            errors.append("PULSE_PEAK_CURRENT_MA 必须为正")
        if not math.isfinite(self.pulse_baseline_v):
            errors.append("PULSE_BASELINE_V 必须为有限数")
        if self.coil_constant_hz_per_ma <= 0:
            errors.append("COIL_CONSTANT_HZ_PER_MA 必须为正")
        if self.pulse_capture_periods < 2:
            errors.append("PULSE_CAPTURE_PERIODS 至少为 2，才能同时覆盖 + 与 − 脉冲")
        return errors

    @classmethod
    def derive_external(cls, values):
        p = cls.from_external(values)
        return dict(predicted_amps_per_volt=p.amps_per_drive_volt,
                    loop_resistance_ohm=p.loop_resistance_ohm,
                    pulse_area_us=p.pulse_area_s * 1e6,
                    pulse_angle_pi=p.pulse_angle_pi(),
                    pulse_vpp=p.pulse_vpp,
                    capture_window_us=p.pulse_capture_window_s * 1e6,
                    zero_points=p.zero_log_points,
                    baseline_nominal_voltage_v=p.baseline_setpoint_voltage)
