"""π 脉冲宽度原子域定标的强类型参数；电流以 mA、时间以 µs/s 输入。

本实验在原子域读回 MFLI 第 0 路解调器的 R：Y 向 5 kHz 射频持续激励，Z 向由
``Time_sequence_2`` 触发无限 burst、连续打 +π 与 −π 两个脉冲，扫单脉冲宽度，用
``R(τ) = R_max |sin(π(τ − τ0) / (2 τ_π))|`` 的峰位给出 π 宽度。脉冲周期由射频频率
反算（5 kHz → 200 µs），两个脉冲相隔半周期；脉冲串相对射频的相位由
``Z_PULSE_PHASE_DEG`` 按任意波数组循环移位设定。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import ClassVar

from ...experiment_params import ExperimentParams, parameter

# 脉冲周期由 Y 向射频频率反算；脉冲宽度列表由 start/stop/step 生成。
_WIDTH_ROUND_DIGITS = 9
# 任意波点数：与 pi_pulse_calibration 相同，DG4000 的上限正好是 16384 点。
# 相位偏移按这个点数换算：一点对应 脉冲周期 / ARB_POINTS 的时间。
ARB_POINTS = 16384


def phase_shift_points(phase_deg: float) -> int:
    """相位对应的任意波循环移位数；360° 与 0° 等价。"""
    return int(round((phase_deg % 360.0) / 360.0 * ARB_POINTS)) % ARB_POINTS


def phase_effective_deg(phase_deg: float) -> float:
    """取整到整点后实际生效的相位。"""
    return phase_shift_points(phase_deg) / ARB_POINTS * 360.0


@dataclass
class PiWidthScanParams(ExperimentParams):
    schema_version: ClassVar[int] = 1
    # 新实验没有历史短等待键；保留声明以便普通气室采集的迁移规则统一适用。
    system_settle_legacy_defaults: ClassVar[dict[str, float]] = {}
    removed_temperature_keys: ClassVar[tuple[str, ...]] = ()

    run_tag: str = parameter(default="pi_width_scan", external_name="RUN_TAG", label="运行标签", group="basic")

    # ---- Y 向射频 ----
    y_rf_freq_hz: float = parameter(default=5000.0, external_name="Y_RF_FREQ_HZ", label="Y 向射频频率", unit="Hz",
        minimum=1e-6, group="basic",
        description="Y 向射频正弦频率；脉冲周期按 1e6/f 自动确定（5 kHz → 200 µs），"
                    "+π 与 −π 相隔半个周期。")
    y_rf_vpp: float = parameter(default=0.05, external_name="Y_RF_VPP", label="Y 向射频幅度", unit="Vpp",
        minimum=1e-9, maximum=20.0, group="basic")

    # ---- Z 向脉冲与链路 ----
    z_peak_current_ma: float = parameter(default=190.0, external_name="Z_PEAK_CURRENT_MA", label="脉冲峰值电流",
        unit="mA", minimum=0.1, group="basic", description="相对基线的 +π 峰值；−π 取相反号")
    z_pulse_edge_us: float = parameter(default=1.0, external_name="Z_PULSE_EDGE_US", label="升/降沿宽度",
        unit="µs", minimum=0.05, description="升余弦沿；等效面积 = 宽度 − 沿宽")
    z_pulse_width_start_us: float = parameter(default=4.0, external_name="Z_PULSE_WIDTH_START_US",
        label="脉宽起点", unit="µs", minimum=1e-3, group="basic")
    z_pulse_width_stop_us: float = parameter(default=7.0, external_name="Z_PULSE_WIDTH_STOP_US",
        label="脉宽终点", unit="µs", minimum=1e-3, group="basic")
    z_pulse_width_step_us: float = parameter(default=0.5, external_name="Z_PULSE_WIDTH_STEP_US",
        label="脉宽步进", unit="µs", minimum=1e-6, group="basic")
    z_pulse_phase_deg: float = parameter(default=90.0, external_name="Z_PULSE_PHASE_DEG", label="脉冲相位",
        unit="°", minimum=0.0, maximum=360.0, group="basic",
        description="脉冲串相对射频（即共同触发周期起点）的相位；按任意波数组循环移位实现，"
                    "等效时间偏移 = 相位/360 × 脉冲周期。读出的幅度按 |cos(φ − ψ0)| 变化，"
                    "ψ0 = 90° − 180° × 占空比（2.5% 时约 85.5°）：默认 90° 对应幅度最大，"
                    "0° 附近是零点，判据峰位不受相位影响但信噪比差约 5 倍。")
    phase_check_enabled: bool = parameter(default=True, external_name="PHASE_CHECK_ENABLED", label="相位自检",
        group="basic",
        description="扫描前先测当前相位与其 +90° 两点的 R，取较大者用于扫描；"
                    "关闭则直接用 Z_PULSE_PHASE_DEG。")
    amplifier_gain_db: float = parameter(default=20.0, external_name="AMPLIFIER_GAIN_DB", label="功放增益", unit="dB",
        minimum=0, maximum=20, group="basic",
        description="连接时写入 1200B（0–20 dB，0.5 dB 步进）；预测电流按此值计算")
    amplifier_input_impedance_ohm: float = parameter(default=1_000_000.0, external_name="AMPLIFIER_INPUT_IMPEDANCE_OHM",
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

    # ---- 逐点采集与温控分组 ----
    n_repeat: int = parameter(default=5, external_name="N_REPEAT", label="每点重复次数", minimum=1, maximum=200,
        group="basic", description="每个脉宽重复采集的 MFLI 读数次数；逐点均值与标准误由这些读数给出。")
    scan_point_duration_s: float = parameter(default=0.2, external_name="SCAN_POINT_DURATION_S", label="每点采样时长",
        unit="s", minimum=1e-6, group="basic", description="每次 MFLI 读数在温控关闭窗口内连续采集的时长。")
    system_settle_s: float = parameter(default=0.5, external_name="SYSTEM_SETTLE_S", label="系统稳定等待",
        unit="s", minimum=0, group="basic", description="温控关闭并配置脉宽后、开始读数前的等待。")
    temp_group_size: int = parameter(default=5, external_name="TEMP_GROUP_SIZE", label="温控关闭分组点数",
        minimum=1, maximum=200, group="basic",
        description="每关闭温控一次连续采集的读数个数；组末恢复温控并等待 TEMP_SWITCH_ON_LAG_S 后再落盘。")
    temp_switch_on_lag_s: float = parameter(default=0.5, external_name="TEMP_SWITCH_ON_LAG_S", label="恢复温控后等待",
        unit="s", minimum=0, group="basic")

    # ---- MFLI 第 0 路解调器 ----
    demod0_idx: int = parameter(default=0, external_name="DEMOD0_IDX", label="解调器索引", read_only=True,
        description="固定为第 0 路解调器；本实验不占用其他解调器。")
    demod0_signal_range_v: float = parameter(default=2.0, external_name="DEMOD0_SIGNAL_RANGE", label="输入量程",
        unit="V", minimum=1e-6, group="basic", description="Signal Input 1 的量程；按探测器信号幅度选择。")
    demod0_order: int = parameter(default=4, external_name="DEMOD0_ORDER", label="解调器阶数", minimum=1, maximum=8)
    demod0_tc_s: float = parameter(default=0.001, external_name="DEMOD0_TC", label="解调器时间常数", unit="s",
        minimum=1e-9,
        description="第 0 路解调器低通时间常数；照 DirectAW 校相档（1 ms / 100 kSa/s）。")
    demod0_rate_sa_s: float = parameter(default=100000.0, external_name="DEMOD0_RATE", label="解调器采样率",
        unit="Sa/s", minimum=1,
        description="第 0 路解调器数据速率；照 DirectAW 校相档（1 ms / 100 kSa/s）。")

    # ---- 物理换算 ----
    coil_constant_hz_per_ma: float = parameter(default=653.79, external_name="COIL_CONSTANT_HZ_PER_MA",
        label="Z 线圈常数", unit="Hz/mA", minimum=1e-6,
        description="只用于预测宽度与事后对照；判定峰位不经该常数。")
    target_angle_pi: float = parameter(default=1.0, external_name="TARGET_ANGLE_PI", label="目标转角",
        unit="π", minimum=0.05, group="basic",
        description="理论核对时的目标转角，默认 π；拟合本身不受该值影响。")
    reference_pi_width_us: float = parameter(default=5.122, external_name="REFERENCE_PI_WIDTH_US",
        label="电学参考 π 宽度", unit="µs", minimum=1e-9, group="basic",
        description="电学域参考值，来自 pi-pulse-calibration 在 Z 链路的内插结果；"
                    "只用于 ratio_to_electrical 对照并写入摘要，不参与拟合。")

    # ---- 派生量 ----
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
        return 2.0 * self.z_peak_current_ma * 1e-3 / self.amps_per_drive_volt

    @property
    def pulse_period_us(self) -> float:
        """双脉冲周期由射频频率反算；5 kHz → 200 µs。"""
        return 1e6 / self.y_rf_freq_hz

    @property
    def pulse_half_period_us(self) -> float:
        return self.pulse_period_us / 2.0

    def pulse_phase_shift_points(self, phase_deg: float | None = None) -> int:
        """任意波循环移位数；缺省用 ``Z_PULSE_PHASE_DEG``。"""
        return phase_shift_points(self.z_pulse_phase_deg if phase_deg is None else phase_deg)

    @property
    def pulse_phase_effective_deg(self) -> float:
        """``Z_PULSE_PHASE_DEG`` 实际生效的相位（整点取整后）。"""
        return phase_effective_deg(self.z_pulse_phase_deg)

    def pulse_phase_offset_us(self, phase_deg: float | None = None) -> float:
        """相位对应的时间偏移（µs）；缺省用 ``Z_PULSE_PHASE_DEG``。"""
        phase = self.pulse_phase_effective_deg if phase_deg is None else phase_effective_deg(phase_deg)
        return phase / 360.0 * self.pulse_period_us

    def phase_check_width_us(self) -> float:
        """相位自检所用的脉宽：扫描网格中最接近预测 π 宽度的一点。"""
        widths = self.widths_us()
        target = self.predicted_width_us()
        return min(widths, key=lambda width: abs(width - target))

    def phase_check_candidates_deg(self) -> list[float]:
        """相位自检的两个候选相位：``Z_PULSE_PHASE_DEG`` 与其 +90°。"""
        return [phase_effective_deg(self.z_pulse_phase_deg),
                phase_effective_deg(self.z_pulse_phase_deg + 90.0)]

    def widths_us(self) -> list[float]:
        """由 start/stop/step 生成脉宽列表；终点按步进向下取整。"""
        start, stop, step = self.z_pulse_width_start_us, self.z_pulse_width_stop_us, self.z_pulse_width_step_us
        count = int(math.floor((stop - start) / step + 1e-9)) + 1
        return [round(start + index * step, _WIDTH_ROUND_DIGITS) for index in range(count)]

    def predicted_width_us(self, angle_pi: float | None = None) -> float:
        """按线圈常数预测实现目标转角所需的脉冲宽度（含沿宽）。

        θ/π = 2·K[Hz/mA]·1000·I[A]·τ[s]，I 以 mA 计时简化为 2·K·I[mA]·τ。
        """
        angle = self.target_angle_pi if angle_pi is None else angle_pi
        area_us = angle / (2.0 * self.coil_constant_hz_per_ma * self.z_peak_current_ma) * 1e6
        return area_us + self.z_pulse_edge_us

    def samples_per_point(self) -> float:
        return self.demod0_rate_sa_s * self.scan_point_duration_s

    def validate_model(self) -> list[str]:
        errors = []
        if not self.run_tag.strip() or any(c in self.run_tag for c in ("/", "\\", ":", "\0")):
            errors.append("RUN_TAG 不能为空或包含路径分隔符")
        if self.demod0_idx != 0:
            errors.append("DEMOD0_IDX 固定为 0")
        if not 0 <= self.amplifier_gain_db <= 20 or round(self.amplifier_gain_db * 2) != self.amplifier_gain_db * 2:
            errors.append("AMPLIFIER_GAIN_DB 须为 0–20 dB、0.5 dB 步进")
        if self.y_rf_freq_hz <= 0 or not math.isfinite(self.y_rf_freq_hz):
            errors.append("Y_RF_FREQ_HZ 必须为正的有限数")
        if not math.isfinite(self.y_rf_vpp) or self.y_rf_vpp <= 0:
            errors.append("Y_RF_VPP 必须为正的有限数")
        if self.z_pulse_width_step_us <= 0 or not math.isfinite(self.z_pulse_width_step_us):
            errors.append("Z_PULSE_WIDTH_STEP_US 必须为正的有限数")
        if self.z_pulse_width_stop_us < self.z_pulse_width_start_us:
            errors.append("Z_PULSE_WIDTH_STOP_US 不能小于 Z_PULSE_WIDTH_START_US")
        widths = self.widths_us()
        if not widths:
            errors.append("脉宽列表为空，请检查起点、终点与步进")
        if any(not math.isfinite(w) or w <= 0 for w in widths):
            errors.append("脉宽列表必须为正的有限数")
        if any(w > self.pulse_half_period_us for w in widths):
            errors.append(f"脉宽不能超过半个脉冲周期 {self.pulse_half_period_us:g} µs（+π 与 −π 相隔半周期）")
        if any(w < 2 * self.z_pulse_edge_us for w in widths):
            errors.append("每个脉宽至少为两倍沿宽")
        if self.z_pulse_edge_us >= self.pulse_half_period_us / 2:
            errors.append("Z_PULSE_EDGE_US 必须明显小于半个周期")
        if self.z_peak_current_ma <= 0 or not math.isfinite(self.z_peak_current_ma):
            errors.append("Z_PEAK_CURRENT_MA 必须为正")
        if not math.isfinite(self.z_pulse_phase_deg) or not 0.0 <= self.z_pulse_phase_deg <= 360.0:
            errors.append("Z_PULSE_PHASE_DEG 必须在 0–360° 之间")
        if self.coil_constant_hz_per_ma <= 0:
            errors.append("COIL_CONSTANT_HZ_PER_MA 必须为正")
        if self.target_angle_pi <= 0:
            errors.append("TARGET_ANGLE_PI 必须为正")
        if not math.isfinite(self.reference_pi_width_us) or self.reference_pi_width_us <= 0:
            errors.append("REFERENCE_PI_WIDTH_US 必须为正的有限数")
        if self.n_repeat < 1:
            errors.append("N_REPEAT 至少为 1")
        if self.temp_group_size < 1:
            errors.append("TEMP_GROUP_SIZE 至少为 1")
        if not math.isfinite(self.scan_point_duration_s) or self.scan_point_duration_s <= 0:
            errors.append("SCAN_POINT_DURATION_S 必须为正")
        elif self.samples_per_point() < 2:
            errors.append("SCAN_POINT_DURATION_S × DEMOD0_RATE 至少要有 2 个样本")
        if not math.isfinite(self.system_settle_s) or self.system_settle_s < 0:
            errors.append("SYSTEM_SETTLE_S 必须是非负有限秒数")
        if not math.isfinite(self.temp_switch_on_lag_s) or self.temp_switch_on_lag_s < 0:
            errors.append("TEMP_SWITCH_ON_LAG_S 必须是非负有限秒数")
        return errors

    @classmethod
    def derive_external(cls, values):
        p = cls.from_external(values)
        widths = p.widths_us()
        return dict(predicted_amps_per_volt=p.amps_per_drive_volt,
                    loop_resistance_ohm=p.loop_resistance_ohm,
                    pulse_vpp=p.pulse_vpp,
                    pulse_period_us=p.pulse_period_us,
                    pulse_half_period_us=p.pulse_half_period_us,
                    widths_us=widths,
                    width_count=len(widths),
                    acquisition_count=len(widths) * p.n_repeat,
                    samples_per_point=p.samples_per_point(),
                    predicted_width_us=p.predicted_width_us(),
                    pulse_phase_deg=p.pulse_phase_effective_deg,
                    pulse_phase_offset_us=p.pulse_phase_offset_us())
