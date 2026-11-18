"""π 脉冲宽度原子域定标的强类型参数；电流以 mA、时间以 µs/s 输入。

本实验在原子域读回 MFLI 第 0 路解调器的 R/θ：Y 向 5 kHz 射频持续激励，Z 向由
``Time_sequence_2`` 触发无限 burst、连续打 +π 与 −π 两个脉冲，扫单脉冲宽度。脉冲周期由
射频频率反算（5 kHz → 200 µs），两个脉冲相隔半周期。

多相位模式（``SCAN_RF_PHASE_COUNT`` = N ≥ 3）：每个脉宽在 ``Y_RF_PHASE_DEG + k·360°/N``
上各读一次，复读数分解为 ``Z(φ) = c0 + c+1 e^{iφ} + c−1 e^{−iφ}``，c−1 只由脉冲产生，
其模量的峰位即 π 宽度。单相位模式（N = 1）沿用 ``R(τ) = R_max |sin(π(τ − τ0)/(2τ_π))|``
拟合；该模式下 R 的峰位受与射频无关的背景 c0 影响，会随射频相位移动。

A03 的 Z 脉冲宽度由 ``PHASE_CHECK_WIDTH_US`` 单独给定，与 A04 的扫描网格无关；多相位模式下
A03 跳过。

``Z_TAIL_COMP_ENABLED`` 开启时，每个脉宽 w 的 ±π 脉冲后各接一段反号指数尾 −A(w)·exp(−(t−w)/τc)：
按屏蔽筒单时间常数模型 B = s_f·I + (1−s_f)·LP_τe[I]，A(w) 使脉冲后原子处 z 场为零，τc = s_f·τe。
波形由 ``bperp_null.z_waveform`` 的同一组生成函数计算，最大绝对值对应 ``Z_PEAK_CURRENT_MA``。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import ClassVar

from ...experiment_params import ExperimentParams, parameter
from .phase_scan import phase_grid

# 脉冲周期由 Y 向射频频率反算；脉冲宽度列表由 start/stop/step 生成。
_WIDTH_ROUND_DIGITS = 9
# 任意波点数：与 pi_pulse_calibration 相同，DG4000 的上限正好是 16384 点。
ARB_POINTS = 16384


@dataclass
class PiWidthScanParams(ExperimentParams):
    # 2：新增 SCAN_RF_PHASE_COUNT；版本 1 的快照按单相位（1）迁移，保持历史运行的原意。
    schema_version: ClassVar[int] = 2
    # 新实验没有历史短等待键；保留声明以便普通气室采集的迁移规则统一适用。
    system_settle_legacy_defaults: ClassVar[dict[str, float]] = {}
    # Y_RF_PHASE_REPEAT 并入 N_REPEAT：A03 与 A04 共用「每点重复次数」。
    removed_temperature_keys: ClassVar[tuple[str, ...]] = ("Y_RF_PHASE_REPEAT",)

    run_tag: str = parameter(default="pi_width_scan", external_name="RUN_TAG", label="运行标签", group="basic")

    # ---- Y 向射频 ----
    y_rf_freq_hz: float = parameter(default=5000.0, external_name="Y_RF_FREQ_HZ", label="Y 向射频频率", unit="Hz",
        minimum=1e-6, group="basic",
        description="Y 向射频正弦频率；脉冲周期按 1e6/f 自动确定（5 kHz → 200 µs），"
                    "+π 与 −π 相隔半个周期。")
    y_rf_vpp: float = parameter(default=0.05, external_name="Y_RF_VPP", label="Y 向射频幅度", unit="Vpp",
        minimum=1e-9, maximum=20.0, group="basic")
    y_rf_phase_deg: float = parameter(default=90.0, external_name="Y_RF_PHASE_DEG", label="射频相位",
        unit="°", minimum=0.0, maximum=360.0, group="basic",
        description="多相位模式下是 A04 相位组的起点；单相位模式下是 A03 未运行或关闭时 A04 使用的"
                    "固定射频相位。按 DG burst 相位写入。")
    scan_rf_phase_count: int = parameter(default=4, external_name="SCAN_RF_PHASE_COUNT", label="每个脉宽的射频相位数",
        minimum=1, maximum=12, group="basic",
        description="A04 在每个脉宽上依次测的射频相位个数，从 Y_RF_PHASE_DEG 起等间隔 360°/N。"
                    "N ≥ 3 时 B01 解出 c0/c+1/c−1，用 |c−1| 峰位定 π，A03 跳过；"
                    "N = 1 时为单相位 R 拟合（R 峰会随相位移动，仅作兼容）；N = 2 不足以分解，预检拒绝。")

    # ---- Z 向脉冲与链路 ----
    z_peak_current_ma: float = parameter(default=190.0, external_name="Z_PEAK_CURRENT_MA", label="脉冲峰值电流",
        unit="mA", minimum=0.1, group="basic", description="相对基线的 +π 峰值；−π 取相反号")
    z_pulse_edge_us: float = parameter(default=1.0, external_name="Z_PULSE_EDGE_US", label="升/降沿宽度",
        unit="µs", minimum=0.05, description="升余弦沿；等效面积 = 宽度 − 沿宽")
    z_pulse_width_start_us: float = parameter(default=2.0, external_name="Z_PULSE_WIDTH_START_US",
        label="脉宽起点", unit="µs", minimum=1e-3, group="basic")
    z_pulse_width_stop_us: float = parameter(default=14.0, external_name="Z_PULSE_WIDTH_STOP_US",
        label="脉宽终点", unit="µs", minimum=1e-3, group="basic")
    z_pulse_width_step_us: float = parameter(default=0.5, external_name="Z_PULSE_WIDTH_STEP_US",
        label="脉宽步进", unit="µs", minimum=1e-6, group="basic")
    z_tail_comp_enabled: bool = parameter(default=False, external_name="Z_TAIL_COMP_ENABLED",
        label="Z 拖尾补偿", group="basic",
        description="开启后每个脉宽的 ±π 脉冲后接一段反号指数尾，使屏蔽筒慢响应在脉冲后抵消；"
                    "尾幅按每个脉宽由 Z_TAIL_S_F、Z_TAIL_TAU_E_US 精确计算，τc = s_f·τe。关闭时为原升余弦脉冲。")
    z_tail_s_f: float = parameter(default=0.544, external_name="Z_TAIL_S_F", label="屏蔽模型快分量 s_f",
        minimum=1e-3, maximum=0.999, active_when="Z_TAIL_COMP_ENABLED", group="basic",
        description="原子处 z 场中随电流瞬时出现的比例；其余 1 − s_f 以 τe 延迟到达。")
    z_tail_tau_e_us: float = parameter(default=46.9, external_name="Z_TAIL_TAU_E_US", label="屏蔽模型时间常数 τe",
        unit="µs", minimum=1e-3, maximum=1000.0, active_when="Z_TAIL_COMP_ENABLED", group="basic",
        description="慢分量的一阶时间常数；补偿尾的衰减时间 τc = s_f·τe。")

    # ---- 射频相位粗扫（A03）----
    phase_check_enabled: bool = parameter(default=True, external_name="PHASE_CHECK_ENABLED", label="相位粗扫",
        group="basic",
        description="A03 按起点/终点/步进扫射频相位、取 R 峰作为 A04 的射频相位；"
                    "关闭则 A03 跳过，A04 直接用 Y_RF_PHASE_DEG。只在单相位模式（SCAN_RF_PHASE_COUNT=1）生效，"
                    "多相位模式下 A03 一律跳过。")
    y_rf_phase_start_deg: float = parameter(default=0.0, external_name="Y_RF_PHASE_START_DEG",
        label="相位粗扫起点", unit="°", minimum=0.0, maximum=360.0, group="basic",
        description="射频相位粗扫的起点；只扫 0–180°，因为 |cos(φ − ψ0)| 的周期是 180°。")
    y_rf_phase_stop_deg: float = parameter(default=180.0, external_name="Y_RF_PHASE_STOP_DEG",
        label="相位粗扫终点", unit="°", minimum=0.0, maximum=360.0, group="basic")
    y_rf_phase_step_deg: float = parameter(default=22.5, external_name="Y_RF_PHASE_STEP_DEG",
        label="相位粗扫步进", unit="°", minimum=1e-3, group="basic",
        description="默认 0–180°、步进 22.5°，共 9 个角度；峰不在端点时按相邻三点做抛物线精化。")
    x_bias_v: float = parameter(default=0.0, external_name="X_BIAS_V", label="X 静态偏置",
        unit="V", group="basic",
        description="A02 在 X 通道加的静态偏置；用来补掉残余横向场，把脉冲轴与自旋拉回 z。"
                    "默认 0 表示不加偏置、通道保持关闭。本机实测 -0.02 V 可把 5 kHz 残留从 12.7 压到 3.9 mV。")
    y_bias_v: float = parameter(default=0.0, external_name="Y_BIAS_V", label="Y 静态偏置",
        unit="V", group="basic",
        description="叠加在 Y 向射频正弦上的直流 offset，与 X_BIAS_V 一起补掉残余横向场；"
                    "与 bperp-null 的 Y 偏置是同一通道（Y_magnetic_field）、同一电压。默认 0 与原行为相同。")
    phase_check_width_us: float = parameter(default=5.0, external_name="PHASE_CHECK_WIDTH_US",
        label="相位粗扫脉宽", unit="µs", minimum=1e-3, group="basic",
        description="A03 期间 Z 双脉冲串的单脉冲宽度；默认 5 µs 取电学参考 π 宽度附近，"
                    "粗扫只用它把脉冲串打响，不改动 A04 的扫描网格。")

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

    def rf_phase_offset_us(self, phase_deg: float | None = None) -> float:
        """射频相位对应的时间偏移（µs）；5 kHz 下 1° = 0.556 µs。"""
        phase = self.y_rf_phase_deg if phase_deg is None else float(phase_deg)
        return phase / 360.0 * self.pulse_period_us

    def rf_phase_angles_deg(self) -> list[float]:
        """A03 粗扫的角度列表（含两端）。"""
        return phase_grid(self.y_rf_phase_start_deg, self.y_rf_phase_stop_deg, self.y_rf_phase_step_deg)

    @property
    def multi_phase(self) -> bool:
        """A04 是否按多相位分解（N ≥ 3）；N = 1 为单相位兼容模式。"""
        return self.scan_rf_phase_count >= 3

    def scan_phases_deg(self) -> list[float]:
        """多相位模式下 A04 每个脉宽依次写入的射频相位：起点 Y_RF_PHASE_DEG、间隔 360°/N，折回 [0, 360)。"""
        count = self.scan_rf_phase_count
        if count < 1:
            return []
        return [round((self.y_rf_phase_deg + index * 360.0 / count) % 360.0, _WIDTH_ROUND_DIGITS)
                for index in range(count)]

    @classmethod
    def migrate_external(cls, values, schema_version):
        migrated = super().migrate_external(values, schema_version)
        if schema_version < 2:
            migrated.setdefault("SCAN_RF_PHASE_COUNT", 1)
        return migrated

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

    @property
    def z_tail_tau_c_us(self) -> float:
        """补偿尾的衰减时间常数 τc = s_f·τe。"""
        return self.z_tail_s_f * self.z_tail_tau_e_us

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
        if self.z_tail_comp_enabled and not (math.isfinite(self.z_tail_s_f) and 0.0 < self.z_tail_s_f < 1.0):
            errors.append("Z_TAIL_S_F 须在 (0, 1) 内")
        if self.z_tail_comp_enabled and not (math.isfinite(self.z_tail_tau_e_us) and self.z_tail_tau_e_us > 0):
            errors.append("Z_TAIL_TAU_E_US 必须为正的有限数")
        if not math.isfinite(self.x_bias_v):
            errors.append("X_BIAS_V 必须是有限数")
        if not math.isfinite(self.y_bias_v):
            errors.append("Y_BIAS_V 必须是有限数")
        if not math.isfinite(self.phase_check_width_us) or self.phase_check_width_us <= 0:
            errors.append("PHASE_CHECK_WIDTH_US 必须为正的有限数")
        elif self.phase_check_width_us > self.pulse_half_period_us:
            errors.append(f"PHASE_CHECK_WIDTH_US 不能超过半个脉冲周期 {self.pulse_half_period_us:g} µs"
                          "（+π 与 −π 相隔半周期）")
        elif self.phase_check_width_us < 2 * self.z_pulse_edge_us:
            errors.append("PHASE_CHECK_WIDTH_US 至少为两倍沿宽")
        if self.z_peak_current_ma <= 0 or not math.isfinite(self.z_peak_current_ma):
            errors.append("Z_PEAK_CURRENT_MA 必须为正")
        if not math.isfinite(self.y_rf_phase_deg) or not 0.0 <= self.y_rf_phase_deg <= 360.0:
            errors.append("Y_RF_PHASE_DEG 必须在 0–360° 之间")
        try:
            self.rf_phase_angles_deg()
        except ValueError as exc:
            errors.append(str(exc))
        if self.scan_rf_phase_count == 2 or not 1 <= self.scan_rf_phase_count <= 12:
            errors.append("SCAN_RF_PHASE_COUNT 取 1（单相位）或 3–12：两个相位解不出 c0、c+1、c−1 三个分量")
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
                    scan_phases_deg=p.scan_phases_deg(),
                    acquisition_count=len(widths) * p.n_repeat * p.scan_rf_phase_count,
                    samples_per_point=p.samples_per_point(),
                    predicted_width_us=p.predicted_width_us(),
                    phase_check_width_us=p.phase_check_width_us,
                    rf_phase_deg=p.y_rf_phase_deg,
                    rf_phase_offset_us=p.rf_phase_offset_us(),
                    rf_phase_angles_deg=p.rf_phase_angles_deg(),
                    z_tail_comp_enabled=p.z_tail_comp_enabled,
                    z_tail_tau_c_us=p.z_tail_tau_c_us if p.z_tail_comp_enabled else None)
