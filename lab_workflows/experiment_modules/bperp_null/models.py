"""π 脉冲模式下补平横向静态场的强类型参数；电压以 V、偏移以 mV、时间以 µs/s 输入。

判据：关射频，Z 向连续打 +π/−π（周期由 PULSE_FREQ_HZ 反算），MFLI 用两个解调器分别读
5 kHz（谐波 1）与 10 kHz（谐波 2）的复振幅；10 kHz 复振幅 c2 = 0 即 B⊥ = 0。
本实验只扫 X、Y 信号源的直流电压，不做线圈常数或磁场换算。

A03 在 ``X_BIAS_MV``/``Y_BIAS_MV`` 附近按偏移网格扫 X、Y；补偿点本身由 B01 的复平面拟合给出，
下一轮运行把拟合结果填回 ``X_BIAS_MV``/``Y_BIAS_MV`` 即可。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import ClassVar

from ...experiment_params import ExperimentParams, parameter

# 任意波点数：与 pi_pulse_calibration / pi_width_scan 一致，DG4000 上限 16384 点。
ARB_POINTS = 16384
_ROUND_DIGITS = 9


def _grid(start: float, stop: float, step: float) -> list[float]:
    """由起点/终点/步进生成含两端（终点按步进向下取整）的偏移列表。"""
    if not all(math.isfinite(value) for value in (start, stop, step)) or step <= 0 or stop < start:
        return []
    count = int(math.floor((stop - start) / step + 1e-9)) + 1
    return [round(start + index * step, _ROUND_DIGITS) for index in range(count)]


@dataclass
class BperpNullParams(ExperimentParams):
    # 2：X/Y 补偿电压的 UI 单位由 V 改为 mV；版本 1 的快照按 ×1000 迁移。
    schema_version: ClassVar[int] = 2
    # 新实验没有历史短等待键；保留声明以便普通气室采集的迁移规则统一适用。
    system_settle_legacy_defaults: ClassVar[dict[str, float]] = {}
    removed_temperature_keys: ClassVar[tuple[str, ...]] = ()

    run_tag: str = parameter(default="bperp_null", external_name="RUN_TAG", label="运行标签", group="basic")

    # ---- 补偿点（本轮要验证的直流电压）----
    x_bias_mv: float = parameter(default=0.0, external_name="X_BIAS_MV", label="X 补偿电压",
        unit="mV", minimum=-20000.0, maximum=20000.0, group="basic",
        description="A02 在 X 通道加的直流电压（mV，后端换算成 V 写仪器），也是 A03 扫描网格的中心；"
                    "B01 把拟合零点写回这里供下一轮使用。")
    y_bias_mv: float = parameter(default=0.0, external_name="Y_BIAS_MV", label="Y 补偿电压",
        unit="mV", minimum=-20000.0, maximum=20000.0, group="basic",
        description="A02 在 Y 通道加的直流电压（mV；关射频，Y 线圈只出 DC），也是 A03 扫描网格的中心。")

    # ---- X/Y 扫描网格（相对补偿点的偏移）----
    x_scan_start_mv: float = parameter(default=-40.0, external_name="X_SCAN_START_MV", label="X 扫描起点",
        unit="mV", group="basic", description="相对 X_BIAS_MV 的偏移；实际写入电压 = (X_BIAS_MV + 偏移)/1000。")
    x_scan_stop_mv: float = parameter(default=40.0, external_name="X_SCAN_STOP_MV", label="X 扫描终点",
        unit="mV", group="basic")
    x_scan_step_mv: float = parameter(default=20.0, external_name="X_SCAN_STEP_MV", label="X 扫描步进",
        unit="mV", minimum=1e-6, group="basic")
    y_scan_start_mv: float = parameter(default=-40.0, external_name="Y_SCAN_START_MV", label="Y 扫描起点",
        unit="mV", group="basic", description="相对 Y_BIAS_MV 的偏移；实际写入电压 = (Y_BIAS_MV + 偏移)/1000。")
    y_scan_stop_mv: float = parameter(default=40.0, external_name="Y_SCAN_STOP_MV", label="Y 扫描终点",
        unit="mV", group="basic")
    y_scan_step_mv: float = parameter(default=20.0, external_name="Y_SCAN_STEP_MV", label="Y 扫描步进",
        unit="mV", minimum=1e-6, group="basic")

    # ---- Z 向脉冲与链路 ----
    pulse_freq_hz: float = parameter(default=5000.0, external_name="PULSE_FREQ_HZ", label="脉冲重复频率",
        unit="Hz", minimum=1e-6, group="basic",
        description="触发方波与 ±π 双脉冲的重复频率；脉冲周期 = 1e6/f（5 kHz → 200 µs）。")
    z_peak_current_ma: float = parameter(default=190.0, external_name="Z_PEAK_CURRENT_MA", label="脉冲峰值电流",
        unit="mA", minimum=0.1, group="basic", description="相对基线的 +π 峰值；−π 取相反号。")
    z_pulse_edge_us: float = parameter(default=1.0, external_name="Z_PULSE_EDGE_US", label="升/降沿宽度",
        unit="µs", minimum=0.05, description="升余弦沿；等效面积 = 宽度 − 沿宽。")
    z_pulse_width_us: float = parameter(default=6.6, external_name="Z_PULSE_WIDTH_US", label="脉冲宽度",
        unit="µs", minimum=1e-3, group="basic",
        description="本轮 Z 向单个脉冲的宽度；默认 6.6 µs 是当前装置的工作点，判据不要求严格 π。"
                    "脉宽对照通过不同轮次完成。使用波形文件时须与文件注释里的 width_us 一致。")
    z_waveform_from_file: bool = parameter(default=False, external_name="Z_WAVEFORM_FROM_FILE",
        label="Z 使用波形文件", group="basic",
        description="关闭时 Z 输出内置 ±π 升余弦脉冲；开启时上传 Z_WAVEFORM_FILE 选定的一个周期任意波"
                    "（例如带拖尾补偿尾的脉冲），峰值仍为 Z_PEAK_CURRENT_MA。")
    z_waveform_file: str = parameter(default="", external_name="Z_WAVEFORM_FILE", label="Z 波形文件",
        group="basic", options_from_directory="data/z_waveforms", options_pattern="*.csv",
        active_when="Z_WAVEFORM_FROM_FILE",
        description="data/z_waveforms/ 下的 .csv：注释行含 period_us（须等于脉冲周期），"
                    "其后 16384 行归一化电流，最大绝对值 1 对应 Z_PEAK_CURRENT_MA；可用 tools/make_z_waveform.py 生成。")

    # ---- X/Y 横向拖尾补偿 ----
    xy_tail_comp_enabled: bool = parameter(default=False, external_name="XY_TAIL_COMP_ENABLED",
        label="X/Y 横向拖尾补偿", group="basic",
        description="关闭时 X/Y 只输出直流偏置（以前的行为）；开启时 A03 在直流偏置上叠加与 Z 同步的补偿波形 "
                    "A·x̂(t)，x̂ 为上传的 Z 波形经 τe 低通、归一化到峰值 1，X/Y 由时序信号 2 外触发、无限 burst。")
    xy_tail_x_mv: float = parameter(default=0.0, external_name="XY_TAIL_X_MV", label="X 补偿波形峰值",
        unit="mV", minimum=-500.0, maximum=500.0, active_when="XY_TAIL_COMP_ENABLED", group="basic",
        description="X 通道补偿波形的带符号峰值（mV）；0 表示 X 仍只出直流。")
    xy_tail_y_mv: float = parameter(default=0.0, external_name="XY_TAIL_Y_MV", label="Y 补偿波形峰值",
        unit="mV", minimum=-500.0, maximum=500.0, active_when="XY_TAIL_COMP_ENABLED", group="basic",
        description="Y 通道补偿波形的带符号峰值（mV）；0 表示 Y 仍只出直流。")
    xy_tail_tau_e_us: float = parameter(default=46.9, external_name="XY_TAIL_TAU_E_US", label="补偿形状时间常数",
        unit="µs", minimum=1.0, maximum=1000.0, active_when="XY_TAIL_COMP_ENABLED",
        description="生成 x̂(t) 的屏蔽时间常数 τe；46.9 µs 为模型值，2026-11-18 示波器时域确认。")

    # ---- GS200 主磁场（提高残余 Bz）----
    main_field_enabled: bool = parameter(default=False, external_name="MAIN_FIELD_ENABLED",
        label="主磁场设定电流", group="basic",
        description="关闭时 A02 关断 GS200 输出，残余 Bz 保持原样（以前的行为）；开启时 A02 按 MAIN_FIELD_CURRENT_MA "
                    "设电流并打开输出，提高 |Bz| 以恢复 2f 判据较弱方向的灵敏度。A04 恢复 GS200 运行前状态。")
    main_field_current_ma: float = parameter(default=0.0, external_name="MAIN_FIELD_CURRENT_MA", label="主磁场电流",
        unit="mA", minimum=-20.0, maximum=20.0, safety_key="main_magnetic_field", active_when="MAIN_FIELD_ENABLED",
        group="basic",
        description="GS200 直流电流（mA，带符号）。约 11 kHz/mA（8.07 mA ≈ 90 kHz）；残余 Bz ≈ 1.4 kHz 方向未知，"
                    "±0.40 mA 两种符号下合成 |Bz| 约 3.0 或 5.9 kHz。")

    # ---- MFLI 两个解调器 ----
    demod0_idx: int = parameter(default=0, external_name="DEMOD0_IDX", label="1f 解调器索引",
        read_only=True, description="固定为第 0 路，读 5 kHz（谐波 1）。")
    demod1_idx: int = parameter(default=1, external_name="DEMOD1_IDX", label="2f 解调器索引",
        minimum=1, maximum=7, group="basic", description="读 10 kHz（与第 0 路共用 5 kHz 振荡器、谐波 2）。")
    demod_harmonic_2f: int = parameter(default=2, external_name="DEMOD_HARMONIC_2F", label="2f 解调谐波",
        read_only=True, description="固定为 2：与 1f 共用 5 kHz 振荡器，得到 10 kHz 参考。")
    demod_phase_deg: float = parameter(default=0.0, external_name="DEMOD_PHASE_DEG", label="解调相位",
        unit="°", minimum=0.0, maximum=360.0, group="basic",
        description="两路解调器都写这个相位偏移；复零点与相位无关，默认 0。")
    demod_tc_s: float = parameter(default=3e-3, external_name="DEMOD_TC", label="解调器时间常数", unit="s",
        minimum=1e-9, group="basic", description="解调器低通时间常数；3 ms 约覆盖 15 个脉冲周期。")
    demod_order: int = parameter(default=4, external_name="DEMOD0_ORDER", label="解调器阶数", minimum=1, maximum=8)
    demod_rate_sa_s: float = parameter(default=10000.0, external_name="DEMOD_RATE", label="解调器采样率",
        unit="Sa/s", minimum=1, group="basic", description="两路共用；低于 70 kSa/s 时走 DAQ 模块。")
    demod_signal_range_v: float = parameter(default=2.0, external_name="DEMOD0_SIGNAL_RANGE", label="输入量程",
        unit="V", minimum=1e-6, group="basic", description="Signal Input 1 的量程；按探测器信号幅度选择。")

    # ---- 逐点采集与温控分组 ----
    n_repeat: int = parameter(default=5, external_name="N_REPEAT", label="每点重复次数", minimum=1, maximum=200,
        group="basic", description="每个网格点重复读数的次数；逐点均值与标准误由这些读数给出。")
    scan_point_duration_s: float = parameter(default=0.3, external_name="SCAN_POINT_DURATION_S", label="每点采样时长",
        unit="s", minimum=1e-6, group="basic", description="每次 MFLI 读数在温控关闭窗口内连续采集的时长。")
    system_settle_s: float = parameter(default=0.5, external_name="SYSTEM_SETTLE_S", label="系统稳定等待",
        unit="s", minimum=0, group="basic", description="写入偏置并关闭温控后、开始读数前的等待。")
    temp_group_size: int = parameter(default=5, external_name="TEMP_GROUP_SIZE", label="温控关闭分组点数",
        minimum=1, maximum=200, group="basic",
        description="每关闭温控一次连续采集的读数个数；组末恢复温控并等待 TEMP_SWITCH_ON_LAG_S 后再落盘。")
    temp_switch_on_lag_s: float = parameter(default=0.5, external_name="TEMP_SWITCH_ON_LAG_S", label="恢复温控后等待",
        unit="s", minimum=0, group="basic")

    # ---- 链路与物理换算（只用于预测与记录）----
    amplifier_gain_db: float = parameter(default=20.0, external_name="AMPLIFIER_GAIN_DB", label="功放增益", unit="dB",
        minimum=0, maximum=20, group="basic", description="连接时写入 1200B（0–20 dB，0.5 dB 步进）。")
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
    coil_constant_hz_per_ma: float = parameter(default=653.79, external_name="COIL_CONSTANT_HZ_PER_MA",
        label="Z 线圈常数", unit="Hz/mA", minimum=1e-6,
        description="只用于预测与记录；判据不经该常数。")

    # ---- 派生量 ----
    @property
    def x_bias_v(self) -> float:
        """写入 X 通道的直流电压（V）；UI 以 mV 输入。"""
        return self.x_bias_mv / 1000.0

    @property
    def y_bias_v(self) -> float:
        """写入 Y 通道的直流电压（V）；UI 以 mV 输入。"""
        return self.y_bias_mv / 1000.0

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
        return 1e6 / self.pulse_freq_hz

    @property
    def pulse_half_period_us(self) -> float:
        return self.pulse_period_us / 2.0

    @property
    def samples_per_point(self) -> float:
        return self.demod_rate_sa_s * self.scan_point_duration_s

    def x_offsets_mv(self) -> list[float]:
        return _grid(self.x_scan_start_mv, self.x_scan_stop_mv, self.x_scan_step_mv)

    def y_offsets_mv(self) -> list[float]:
        return _grid(self.y_scan_start_mv, self.y_scan_stop_mv, self.y_scan_step_mv)

    def scan_voltages(self) -> list[tuple[float, float, float, float]]:
        """(x_offset_mv, y_offset_mv, x_voltage_v, y_voltage_v) 的笛卡尔网格。"""
        return [(x, y, self.x_bias_v + x / 1000.0, self.y_bias_v + y / 1000.0)
                for x in self.x_offsets_mv() for y in self.y_offsets_mv()]

    def xy_tail_amplitudes_v(self) -> dict[str, float]:
        """开启补偿时各轴的带符号峰值（V）；未开启或为 0 的轴不出现。"""
        if not self.xy_tail_comp_enabled:
            return {}
        return {axis: value / 1000.0 for axis, value in (("x", self.xy_tail_x_mv), ("y", self.xy_tail_y_mv)) if value}

    def predicted_width_us(self, angle_pi: float = 1.0) -> float:
        area_us = angle_pi / (2.0 * self.coil_constant_hz_per_ma * self.z_peak_current_ma) * 1e6
        return area_us + self.z_pulse_edge_us

    def validate_model(self) -> list[str]:
        errors = []
        if not self.run_tag.strip() or any(c in self.run_tag for c in ("/", "\\\\", ":", "\0")):
            errors.append("RUN_TAG 不能为空或包含路径分隔符")
        if self.demod0_idx != 0:
            errors.append("DEMOD0_IDX 固定为 0")
        if self.demod1_idx == self.demod0_idx:
            errors.append("DEMOD1_IDX 不能与 DEMOD0_IDX 相同")
        if self.demod_harmonic_2f != 2:
            errors.append("DEMOD_HARMONIC_2F 固定为 2")
        if not 0 <= self.amplifier_gain_db <= 20 or round(self.amplifier_gain_db * 2) != self.amplifier_gain_db * 2:
            errors.append("AMPLIFIER_GAIN_DB 须为 0–20 dB、0.5 dB 步进")
        if not math.isfinite(self.pulse_freq_hz) or self.pulse_freq_hz <= 0:
            errors.append("PULSE_FREQ_HZ 必须为正的有限数")
        if not math.isfinite(self.z_pulse_width_us) or self.z_pulse_width_us <= 0:
            errors.append("Z_PULSE_WIDTH_US 必须为正的有限数")
        elif self.z_pulse_width_us > self.pulse_half_period_us:
            errors.append(f"Z_PULSE_WIDTH_US 不能超过半个脉冲周期 {self.pulse_half_period_us:g} µs")
        elif self.z_pulse_width_us < 2 * self.z_pulse_edge_us:
            errors.append("Z_PULSE_WIDTH_US 至少为两倍沿宽")
        if self.xy_tail_comp_enabled:
            if not all(math.isfinite(v) for v in (self.xy_tail_x_mv, self.xy_tail_y_mv, self.xy_tail_tau_e_us)):
                errors.append("XY_TAIL_X_MV / XY_TAIL_Y_MV / XY_TAIL_TAU_E_US 必须是有限数")
            elif self.xy_tail_x_mv == 0.0 and self.xy_tail_y_mv == 0.0:
                errors.append("XY_TAIL_COMP_ENABLED 开启时 XY_TAIL_X_MV 与 XY_TAIL_Y_MV 不能都为 0")
        if self.main_field_enabled and (not math.isfinite(self.main_field_current_ma) or self.main_field_current_ma == 0.0):
            errors.append("MAIN_FIELD_ENABLED 开启时 MAIN_FIELD_CURRENT_MA 必须是非零有限值")
        if self.z_waveform_from_file and not self.z_waveform_file.strip():
            errors.append("Z_WAVEFORM_FROM_FILE 开启时必须选择 Z_WAVEFORM_FILE")
        if self.z_peak_current_ma <= 0 or not math.isfinite(self.z_peak_current_ma):
            errors.append("Z_PEAK_CURRENT_MA 必须为正")
        for name, values in (("X", (self.x_bias_mv, self.x_scan_start_mv, self.x_scan_stop_mv, self.x_scan_step_mv)),
                             ("Y", (self.y_bias_mv, self.y_scan_start_mv, self.y_scan_stop_mv, self.y_scan_step_mv))):
            if any(not math.isfinite(value) for value in values):
                errors.append(f"{name} 偏置或扫描参数必须是有限数")
        if not self.x_offsets_mv():
            errors.append("X 扫描网格为空，请检查起点、终点与步进")
        if not self.y_offsets_mv():
            errors.append("Y 扫描网格为空，请检查起点、终点与步进")
        if not math.isfinite(self.scan_point_duration_s) or self.scan_point_duration_s <= 0:
            errors.append("SCAN_POINT_DURATION_S 必须为正")
        elif self.samples_per_point < 2:
            errors.append("SCAN_POINT_DURATION_S × DEMOD_RATE 至少要有 2 个样本")
        if self.n_repeat < 1:
            errors.append("N_REPEAT 至少为 1")
        if self.temp_group_size < 1:
            errors.append("TEMP_GROUP_SIZE 至少为 1")
        if not math.isfinite(self.system_settle_s) or self.system_settle_s < 0:
            errors.append("SYSTEM_SETTLE_S 必须是非负有限秒数")
        if not math.isfinite(self.temp_switch_on_lag_s) or self.temp_switch_on_lag_s < 0:
            errors.append("TEMP_SWITCH_ON_LAG_S 必须是非负有限秒数")
        if not math.isfinite(self.demod_tc_s) or self.demod_tc_s <= 0:
            errors.append("DEMOD_TC 必须为正")
        if not math.isfinite(self.demod_rate_sa_s) or self.demod_rate_sa_s <= 0:
            errors.append("DEMOD_RATE 必须为正")
        return errors

    @classmethod
    def migrate_external(cls, values, schema_version):
        migrated = super().migrate_external(values, schema_version)
        if schema_version < 2:
            for old, new in (("X_BIAS_V", "X_BIAS_MV"), ("Y_BIAS_V", "Y_BIAS_MV")):
                if old in migrated:
                    migrated.setdefault(new, float(migrated.pop(old)) * 1000.0)
        return migrated

    @classmethod
    def derive_external(cls, values):
        p = cls.from_external(values)
        grid = p.scan_voltages()
        return dict(predicted_amps_per_volt=p.amps_per_drive_volt,
                    loop_resistance_ohm=p.loop_resistance_ohm,
                    pulse_vpp=p.pulse_vpp,
                    pulse_period_us=p.pulse_period_us,
                    pulse_half_period_us=p.pulse_half_period_us,
                    predicted_width_us=p.predicted_width_us(),
                    x_offsets_mv=p.x_offsets_mv(),
                    y_offsets_mv=p.y_offsets_mv(),
                    grid_points=len(grid),
                    acquisition_count=len(grid) * p.n_repeat,
                    samples_per_point=p.samples_per_point,
                    x_voltages=[item[2] for item in grid],
                    y_voltages=[item[3] for item in grid])
