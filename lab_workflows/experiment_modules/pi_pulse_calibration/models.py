"""π 脉冲面积标定（电学域）的强类型参数；电流以 mA、时间以 µs/s 输入。

本实验只用电学链路：固定峰值、扫单脉冲宽度，从每帧原始波形算 θ = 2πK∫(I−I_base)dt，
线性内插出 θ = π 的宽度，并与按线圈常数预测的宽度对比。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import ClassVar

from ...experiment_params import ExperimentParams, parameter


@dataclass
class PiPulseCalibrationParams(ExperimentParams):
    schema_version: ClassVar[int] = 1

    run_tag: str = parameter(default="pi_pulse_calibration", external_name="RUN_TAG", label="运行标签", group="basic")

    # ---- 链路与功放 ----
    amplifier_gain_db: float = parameter(default=20.0, external_name="AMPLIFIER_GAIN_DB", label="功放增益", unit="dB",
        minimum=0, maximum=20, group="basic",
        description="连接时写入 1200B（0–20 dB，0.5 dB 步进）；预测电流按此值计算")
    amplifier_input_impedance_ohm: float = parameter(default=1_000_000, external_name="AMPLIFIER_INPUT_IMPEDANCE_OHM",
        label="功放输入阻抗", unit="Ω", group="basic", options=((50.0, "50 Ω"), (1_000_000.0, "1 MΩ")))
    amplifier_output_impedance_ohm: float = parameter(default=50.0, external_name="AMPLIFIER_OUTPUT_IMPEDANCE_OHM",
        label="功放输出阻抗", unit="Ω", group="basic", options=((50.0, "50 Ω"), (1.0, "1 Ω")))
    amplifier_write_settings: bool = parameter(default=True, external_name="AMPLIFIER_WRITE_SETTINGS",
        label="连接时写入功放设置", group="basic")
    sense_resistor_ohm: float = parameter(default=10.0, external_name="SENSE_RESISTOR_OHM", label="采样电阻",
        unit="Ω", minimum=1e-3)
    damping_resistor_ohm: float = parameter(default=10.0, external_name="DAMPING_RESISTOR_OHM", label="阻尼电阻",
        unit="Ω", minimum=0)
    coil_resistance_ohm: float = parameter(default=2.0, external_name="COIL_RESISTANCE_OHM", label="线圈直流电阻",
        unit="Ω", minimum=0)
    resistor_rated_w: float = parameter(default=2.0, external_name="RESISTOR_RATED_W", label="电阻额定功率",
        unit="W", minimum=0.01)

    # ---- 示波器 ----
    settle_s: float = parameter(default=0.3, external_name="SETTLE_S", label="输出切换后等待", unit="s", minimum=0)
    scope_acquire_delay_s: float = parameter(default=1.5, external_name="SCOPE_ACQUIRE_DELAY_S",
        label="示波器停采后等待", unit="s", minimum=0, maximum=30)
    scope_current_channel: int = parameter(default=2, external_name="SCOPE_CURRENT_CHANNEL", label="采样电阻通道",
        minimum=1, maximum=4)
    scope_sync_channel: int = parameter(default=3, external_name="SCOPE_SYNC_CHANNEL", label="同步触发通道",
        minimum=1, maximum=4)
    sync_trigger_level_v: float = parameter(default=1.0, external_name="SYNC_TRIGGER_LEVEL_V", label="同步触发电平", unit="V")
    sync_scale_v_div: float = parameter(default=2.0, external_name="SYNC_SCALE_V_DIV", label="同步通道垂直档位",
        unit="V/div", minimum=0.0005, maximum=10)
    max_memory_depth: int = parameter(default=10_000, external_name="MAX_MEMORY_DEPTH", label="最大存储深度上限",
        unit="点", options=tuple((n, f"{n:,}") for n in (1_000, 10_000, 100_000, 1_000_000)))

    # ---- 扫描 ----
    pulse_period_us: float = parameter(default=100.0, external_name="PULSE_PERIOD_US", label="双脉冲周期",
        unit="µs", minimum=10, description="+π 与 −π 成对的一个周期；脉冲间隔为半个周期")
    pulse_widths_us: list[float] = parameter(default_factory=lambda: [4.6, 4.8, 5.0, 5.2, 5.4],
        external_name="PULSE_WIDTHS_US", label="扫描的单脉冲宽度", unit="µs",
        description="从这几个宽度里找 θ = π；每个宽度内部再重复 SHOTS_PER_WIDTH 次")
    pulse_edge_us: float = parameter(default=1.0, external_name="PULSE_EDGE_US", label="升/降沿宽度", unit="µs",
        minimum=0.05, description="升余弦沿；等效面积 = 宽度 − 沿宽")
    pulse_peak_current_ma: float = parameter(default=190.0, external_name="PULSE_PEAK_CURRENT_MA",
        label="脉冲峰值电流", unit="mA", minimum=0.1,
        description="相对基线的 +π 峰值；−π 取相反号")
    pulse_baseline_v: float = parameter(default=0.0, external_name="PULSE_BASELINE_V", label="脉冲直流偏置",
        unit="V", group="basic", description="脉冲所骑的 DG 直流偏置；0 V 表示不写偏置")
    shots_per_width: int = parameter(default=8, external_name="SHOTS_PER_WIDTH", label="每个宽度的捕获次数",
        minimum=2, maximum=100)
    capture_interval_s: float = parameter(default=0.5, external_name="CAPTURE_INTERVAL_S",
        label="相邻捕获的最小间隔", unit="s", minimum=0)
    capture_periods: int = parameter(default=4, external_name="CAPTURE_PERIODS", label="每次捕获覆盖周期数",
        minimum=2, maximum=20)
    pulse_tail_window_us: float = parameter(default=20.0, external_name="PULSE_TAIL_WINDOW_US",
        label="单脉冲积分窗", unit="µs", minimum=1,
        description="从脉冲起点到积分结束；须覆盖 RL 衰减尾与退出沿")
    coil_constant_hz_per_ma: float = parameter(default=653.79, external_name="COIL_CONSTANT_HZ_PER_MA",
        label="Z 线圈常数", unit="Hz/mA", minimum=1e-6)
    target_angle_pi: float = parameter(default=1.0, external_name="TARGET_ANGLE_PI", label="目标转角",
        unit="π", minimum=0.05, group="basic", description="内插求这个角度对应的宽度，默认 π")

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
    def pulse_vpp(self) -> float:
        return 2.0 * self.pulse_peak_current_ma * 1e-3 / self.amps_per_drive_volt

    @property
    def capture_window_s(self) -> float:
        return self.capture_periods * self.pulse_period_us * 1e-6

    def predicted_width_us(self, angle_pi: float | None = None) -> float:
        """按线圈常数预测实现目标转角所需的脉冲宽度（含沿宽）。

        θ/π = 2·K[Hz/mA]·1000·I[A]·τ[s]，I 以 mA 计时简化为 2·K·I[mA]·τ。
        """
        angle = self.target_angle_pi if angle_pi is None else angle_pi
        area_us = angle / (2.0 * self.coil_constant_hz_per_ma * self.pulse_peak_current_ma) * 1e6
        return area_us + self.pulse_edge_us

    def validate_model(self) -> list[str]:
        errors = []
        if not self.run_tag.strip() or any(c in self.run_tag for c in ("/", "\\", ":", "\0")):
            errors.append("RUN_TAG 不能为空或包含路径分隔符")
        if self.scope_current_channel == self.scope_sync_channel:
            errors.append("采样电阻通道与同步触发通道不能相同")
        if not 0 <= self.amplifier_gain_db <= 20 or round(self.amplifier_gain_db * 2) != self.amplifier_gain_db * 2:
            errors.append("AMPLIFIER_GAIN_DB 须为 0–20 dB、0.5 dB 步进")
        if not self.pulse_widths_us or len(self.pulse_widths_us) < 2:
            errors.append("PULSE_WIDTHS_US 至少需要两个宽度")
        if any(not math.isfinite(w) or w <= 0 for w in self.pulse_widths_us):
            errors.append("PULSE_WIDTHS_US 必须为正的有限数")
        if self.pulse_period_us <= 2 * max(self.pulse_widths_us or [0.0]):
            errors.append("PULSE_PERIOD_US 必须大于两倍最大脉冲宽度")
        if any(w < 2 * self.pulse_edge_us for w in self.pulse_widths_us or []):
            errors.append("每个脉冲宽度至少为两倍沿宽")
        if self.pulse_tail_window_us < max(self.pulse_widths_us or [0.0]):
            errors.append("PULSE_TAIL_WINDOW_US 不能短于最大脉冲宽度")
        if self.pulse_tail_window_us >= self.pulse_period_us / 2:
            errors.append("PULSE_TAIL_WINDOW_US 必须小于半个周期")
        if self.pulse_peak_current_ma <= 0 or not math.isfinite(self.pulse_peak_current_ma):
            errors.append("PULSE_PEAK_CURRENT_MA 必须为正")
        if self.coil_constant_hz_per_ma <= 0:
            errors.append("COIL_CONSTANT_HZ_PER_MA 必须为正")
        if not math.isfinite(self.pulse_baseline_v):
            errors.append("PULSE_BASELINE_V 必须为有限数")
        if self.target_angle_pi <= 0:
            errors.append("TARGET_ANGLE_PI 必须为正")
        return errors

    @classmethod
    def derive_external(cls, values):
        p = cls.from_external(values)
        return dict(predicted_amps_per_volt=p.amps_per_drive_volt,
                    loop_resistance_ohm=p.loop_resistance_ohm,
                    pulse_vpp=p.pulse_vpp,
                    capture_window_us=p.capture_window_s * 1e6,
                    predicted_width_us=p.predicted_width_us(),
                    widths=p.pulse_widths_us)
