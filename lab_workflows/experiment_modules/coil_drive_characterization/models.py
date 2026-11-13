"""线圈驱动链路电学特性的强类型参数；电流以 mA、脉宽以 µs 输入。"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import ClassVar

import numpy as np

from ...experiment_params import ExperimentParams, parameter


@dataclass
class CoilDriveCharacterizationParams(ExperimentParams):
    schema_version: ClassVar[int] = 2

    @classmethod
    def migrate_external(cls, values, schema_version):
        """v1 的 MAX_SAMPLE_RATE_SA_S 已由 AUTO 存储深度取代；新增的记录窗参数取默认值。"""
        migrated = super().migrate_external(values, schema_version)
        if schema_version < 2:
            migrated.pop("MAX_SAMPLE_RATE_SA_S", None)
        return migrated

    run_tag: str = parameter(default="coil_drive", external_name="RUN_TAG", label="运行标签", group="basic")
    amplifier_gain_db: float = parameter(default=20.0, external_name="AMPLIFIER_GAIN_DB", label="功放增益", unit="dB",
        minimum=0, maximum=20, group="basic",
        description="连接时写入 1200B（0–20 dB，0.5 dB 步进）；预测电流按此值计算，不再是面板声明值")
    amplifier_input_impedance_ohm: float = parameter(default=1_000_000, external_name="AMPLIFIER_INPUT_IMPEDANCE_OHM",
        label="功放输入阻抗", unit="Ω", group="basic", options=((50.0, "50 Ω"), (1_000_000.0, "1 MΩ")),
        description="连接时写入 1200B，并把信号源输出负载配成一致（1 MΩ→高阻，50 Ω→50 Ω），避免上游幅度按错负载标定")
    amplifier_output_impedance_ohm: float = parameter(default=50.0, external_name="AMPLIFIER_OUTPUT_IMPEDANCE_OHM",
        label="功放输出阻抗", unit="Ω", group="basic", options=((1.0, "1 Ω"), (50.0, "50 Ω")),
        description="连接时写入 1200B；50 Ω 与线圈回路串联，直接参与回路电阻与预测电流。"
                    "本机 1 Ω 档实测静默不出输出（面板已切换但无驱动），默认 50 Ω")
    amplifier_write_settings: bool = parameter(default=True, external_name="AMPLIFIER_WRITE_SETTINGS",
        label="连接时写入功放设置", group="basic",
        description="true 时按上面三项发送 1200B 命令（写入效果已由面板与 A03 双向核对确认）；"
                    "false 时只按声明值预测，要求面板与声明一致")
    sine_peak_current_ma: float = parameter(default=100.0, external_name="SINE_PEAK_CURRENT_MA", label="扫频电流峰值",
        unit="mA", minimum=0.1, group="basic", description="按声明增益与回路电阻换算信号源幅度")
    frequency_start_hz: float = parameter(default=10.0, external_name="FREQUENCY_START_HZ", label="扫频起始频率",
        unit="Hz", minimum=0.1, group="basic")
    frequency_stop_hz: float = parameter(default=500e3, external_name="FREQUENCY_STOP_HZ", label="扫频终止频率",
        unit="Hz", minimum=1, maximum=20e6, group="basic")
    points_per_decade: int = parameter(default=4, external_name="POINTS_PER_DECADE", label="每十倍频点数",
        minimum=1, maximum=50, group="basic")
    pulse_current_ma: float = parameter(default=100.0, external_name="PULSE_CURRENT_MA", label="脉冲电流",
        unit="mA", minimum=0.1, group="basic", description="0→I 单极性矩形脉冲的平台电流")
    pulse_widths_us: list[float] = parameter(default_factory=lambda: [10.0, 20.0, 50.0, 100.0, 200.0, 500.0, 1000.0],
        external_name="PULSE_WIDTHS_US", label="脉宽列表", unit="µs", group="basic")
    pulse_repetition_hz: float = parameter(default=50.0, external_name="PULSE_REPETITION_HZ", label="脉冲重复频率",
        unit="Hz", minimum=0.1, group="basic", description="最宽脉冲的占空比须 <50%")

    sense_resistor_ohm: float = parameter(default=10.0, external_name="SENSE_RESISTOR_OHM", label="采样电阻",
        unit="Ω", minimum=1e-3, description="I = V_sense / R_sense")
    damping_resistor_ohm: float = parameter(default=10.0, external_name="DAMPING_RESISTOR_OHM", label="阻尼电阻",
        unit="Ω", minimum=0)
    coil_resistance_ohm: float = parameter(default=0.0, external_name="COIL_RESISTANCE_OHM", label="线圈直流电阻",
        unit="Ω", minimum=0, description="用于预测电流；0 使预测偏大、偏保守")
    resistor_rated_w: float = parameter(default=2.0, external_name="RESISTOR_RATED_W", label="电阻额定功率",
        unit="W", minimum=0.01, description="平均功耗按额定一半校验")
    zero_frames: int = parameter(default=5, external_name="ZERO_FRAMES", label="零偏帧数", minimum=1, maximum=100)
    gain_check_current_ma: float = parameter(default=20.0, external_name="GAIN_CHECK_CURRENT_MA",
        label="增益核对直流电流", unit="mA", minimum=0.1)
    gain_tolerance: float = parameter(default=1.25, external_name="GAIN_TOLERANCE", label="增益上偏容许倍数",
        minimum=1.01, maximum=3, description="实测 I/V 超过预测值此倍数即中止")
    gain_lower_ratio: float = parameter(default=0.5, external_name="GAIN_LOWER_RATIO", label="增益下偏容许比例",
        minimum=0.05, maximum=0.99, description="实测 I/V 低于预测值此比例即中止，用于发现增益/阻抗未生效或回路断线")
    settle_s: float = parameter(default=0.3, external_name="SETTLE_S", label="输出切换后等待", unit="s", minimum=0)
    scope_acquire_delay_s: float = parameter(default=1.5, external_name="SCOPE_ACQUIRE_DELAY_S",
        label="示波器停采后等待", unit="s", minimum=0, maximum=30, legacy_default=0.0,
        description="整帧 STOP 确认后、停止并读取波形前的等待；真机固件在 STOP 置位后波形缓冲尚不可读，立即读取会返回空数据。0 表示不等待，仅作 A/B 对照")
    sine_cycles: float = parameter(default=10.0, external_name="SINE_CYCLES", label="每频记录周期数", minimum=1, maximum=1000)
    scope_current_channel: int = parameter(default=2, external_name="SCOPE_CURRENT_CHANNEL", label="采样电阻通道",
        minimum=1, maximum=4)
    scope_sync_channel: int = parameter(default=3, external_name="SCOPE_SYNC_CHANNEL", label="同步触发通道",
        minimum=1, maximum=4)
    sync_trigger_level_v: float = parameter(default=1.0, external_name="SYNC_TRIGGER_LEVEL_V", label="同步触发电平", unit="V")
    sync_scale_v_div: float = parameter(default=2.0, external_name="SYNC_SCALE_V_DIV", label="同步通道垂直档位",
        unit="V/div", minimum=0.0005, maximum=10)
    zero_window_s: float = parameter(default=0.02, external_name="ZERO_WINDOW_S", label="零偏/增益采样时间", unit="s",
        minimum=1e-4, maximum=10, legacy_default=0.01, description="零偏与增益核对每帧的记录窗口；取 50 Hz 工频周期 20 ms 的整数倍可平均掉工频干扰")
    pulse_window_multiple: float = parameter(default=5.0, external_name="PULSE_WINDOW_MULTIPLE", label="脉冲记录窗倍数",
        minimum=5, maximum=100, legacy_default=5.0, description="每个脉冲的记录窗口 = 倍数 × 脉宽；触发居中，下限 5 保证触发后窗口 ≥2.5 倍脉宽")
    max_memory_depth: int = parameter(default=1_000_000, external_name="MAX_MEMORY_DEPTH", label="最大存储深度上限", unit="点",
        options=tuple((n, f"{n:,}") for n in (1_000, 10_000, 100_000, 1_000_000, 5_000_000, 10_000_000)),
        description="示波器深度为 AUTO，由仪器按时基给出最高采样率；超过此点数时改用此上限深度。上限不是精确深度："
                    "部分时基下仪器只给得出更小的深度，以回读为准。实际采样率与点数随每帧保存")
    area_tolerance: float = parameter(default=0.02, external_name="AREA_TOLERANCE", label="脉冲面积/平台容差",
        minimum=0.001, maximum=0.5, description="离线判定最短可用脉宽；具体历史目录可独立覆盖")

    @property
    def gain(self) -> float:
        return 10.0 ** (self.amplifier_gain_db / 20.0)

    @property
    def loop_resistance_ohm(self) -> float:
        """线圈回路总电阻：采样电阻 + 阻尼电阻 + 线圈直流电阻 + 功放输出阻抗（50 Ω 档与回路串联）。"""
        return (self.sense_resistor_ohm + self.damping_resistor_ohm + self.coil_resistance_ohm
                + self.amplifier_output_impedance_ohm)

    @property
    def amps_per_drive_volt(self) -> float:
        """按写入的功放增益与回路总电阻预测的直流 I/V_drive。"""
        return self.gain / self.loop_resistance_ohm

    @property
    def pulse_widths_s(self) -> list[float]:
        return sorted(width * 1e-6 for width in self.pulse_widths_us)

    def frequencies(self) -> list[float]:
        decades = math.log10(self.frequency_stop_hz / self.frequency_start_hz)
        count = max(2, int(math.ceil(decades * self.points_per_decade - 1e-9)) + 1)
        return [float(f) for f in np.geomspace(self.frequency_start_hz, self.frequency_stop_hz, count)]

    def validate_model(self) -> list[str]:
        errors = []
        if self.frequency_start_hz >= self.frequency_stop_hz:
            errors.append("扫频起始频率必须低于终止频率")
        if not self.pulse_widths_us or any(not math.isfinite(w) or w <= 0 for w in self.pulse_widths_us):
            errors.append("PULSE_WIDTHS_US 必须为非空正数列表")
        elif len(set(self.pulse_widths_us)) != len(self.pulse_widths_us):
            errors.append("PULSE_WIDTHS_US 不能有重复脉宽")
        elif max(self.pulse_widths_s) * self.pulse_repetition_hz >= 0.5:
            errors.append("最宽脉冲占空比须 <50%（降低 PULSE_REPETITION_HZ）")
        if self.scope_current_channel == self.scope_sync_channel:
            errors.append("采样电阻通道与同步触发通道不能相同")
        if not 0 <= self.amplifier_gain_db <= 20 or not (round(self.amplifier_gain_db * 2) == self.amplifier_gain_db * 2):
            errors.append("AMPLIFIER_GAIN_DB 须为 0–20 dB、0.5 dB 步进（1200B 只接受这些档位）")
        if self.amplifier_input_impedance_ohm not in (50.0, 1_000_000.0):
            errors.append("AMPLIFIER_INPUT_IMPEDANCE_OHM 只能是 50 或 1000000 Ω")
        if self.amplifier_output_impedance_ohm not in (1.0, 50.0):
            errors.append("AMPLIFIER_OUTPUT_IMPEDANCE_OHM 只能是 1 或 50 Ω")
        if not self.run_tag.strip() or any(c in self.run_tag for c in ("/", "\\", ":", "\0")):
            errors.append("运行标签不能为空或包含路径分隔符")
        return errors

    @classmethod
    def derive_external(cls, values):
        p = cls.from_external(values)
        return dict(sine_drive_peak_v=p.sine_peak_current_ma * 1e-3 / p.amps_per_drive_volt,
                    pulse_drive_v=p.pulse_current_ma * 1e-3 / p.amps_per_drive_volt,
                    predicted_amps_per_volt=p.amps_per_drive_volt,
                    sine_points=len(p.frequencies()), pulse_count=len(p.pulse_widths_us))
