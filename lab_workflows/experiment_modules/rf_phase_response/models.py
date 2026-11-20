"""射频相位响应的强类型参数；电流以 mA、时间以 µs/s 输入。

固定一个 Z 双脉冲宽度，按 ``Y_RF_PHASE_START/STOP/STEP_DEG`` 扫射频 burst 相位，
每个角度读 ``N_REPEAT`` 次 MFLI 第 0 路解调器的 R/θ。离线把复数读数分解为
``Z(φ) = c0 + c+1 e^{iφ} + c−1 e^{−iφ}``，给出三分量、模型残差与最佳读相位。

默认脉冲取自运行目录 pi_width_scan 的精确 π 波形：5 kHz、50 mVpp、190 mA、
8.03 µs，并接约 −21.9 mA / 25.5 µs 的反向指数尾（``Z_TAIL_COMP_ENABLED``）。
所有参数都可调；拖尾的尾幅与 τc 由 ``Z_TAIL_S_F``、``Z_TAIL_TAU_E_US`` 与脉宽推导。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import ClassVar

from ...experiment_params import ExperimentParams, parameter
from ..z_chain_common.params import ARB_POINTS
from ..z_chain_common.phase import phase_grid


@dataclass
class RfPhaseResponseParams(ExperimentParams):
    schema_version: ClassVar[int] = 1
    # 新实验没有历史短等待键；保留声明以便普通气室采集的迁移规则统一适用。
    system_settle_legacy_defaults: ClassVar[dict[str, float]] = {}
    removed_temperature_keys: ClassVar[tuple[str, ...]] = ()

    run_tag: str = parameter(default="rf_phase_response", external_name="RUN_TAG", label="运行标签", group="basic")

    # ---- 射频方向与幅度 ----
    rf_direction: str = parameter(default="Y", external_name="RF_DIRECTION", label="射频方向",
        group="basic", options=(("Y", "Y 向（默认）"), ("X", "X 向")),
        description="射频 burst 所在的横向线圈；该方向静态偏置作为正弦 offset 一起叠加，"
                    "另一方向只保留 DC 偏置。切换方向会改变相位参考，跨方向数据不能直接比较。")

    # ---- Y 向射频 ----
    y_rf_freq_hz: float = parameter(default=5000.0, external_name="Y_RF_FREQ_HZ", label="Y 向射频频率", unit="Hz",
        minimum=1e-6, group="basic",
        description="Y 向射频正弦频率；脉冲周期按 1e6/f 自动确定（5 kHz → 200 µs）。")
    y_rf_vpp: float = parameter(default=0.05, external_name="Y_RF_VPP", label="Y 向射频幅度", unit="Vpp",
        minimum=1e-9, maximum=20.0, group="basic")
    y_rf_phase_deg: float = parameter(default=0.0, external_name="Y_RF_PHASE_DEG", label="射频参考相位",
        unit="°", minimum=0.0, maximum=360.0, group="basic",
        description="A02 起振时的 burst 相位；相位扫描的起点由 Y_RF_PHASE_START_DEG 单独给定。")
    y_rf_phase_start_deg: float = parameter(default=0.0, external_name="Y_RF_PHASE_START_DEG",
        label="相位扫描起点", unit="°", minimum=0.0, maximum=360.0, group="basic")
    y_rf_phase_stop_deg: float = parameter(default=360.0, external_name="Y_RF_PHASE_STOP_DEG",
        label="相位扫描终点", unit="°", minimum=0.0, maximum=360.0, group="basic",
        description="终点不写入：默认 0–360°、步进 10° 得到 36 个相位点，0° 与 360° 不重复。")
    y_rf_phase_step_deg: float = parameter(default=10.0, external_name="Y_RF_PHASE_STEP_DEG",
        label="相位扫描步进", unit="°", minimum=1e-3, group="basic")

    # ---- Z 向脉冲与链路 ----
    z_peak_current_ma: float = parameter(default=190.0, external_name="Z_PEAK_CURRENT_MA", label="脉冲峰值电流",
        unit="mA", minimum=0.1, group="basic", description="相对基线的 +π 峰值；−π 取相反号")
    z_pulse_edge_us: float = parameter(default=1.0, external_name="Z_PULSE_EDGE_US", label="升/降沿宽度",
        unit="µs", minimum=0.05, description="升余弦沿；等效面积 = 宽度 − 沿宽")
    z_pulse_width_us: float = parameter(default=8.03, external_name="Z_PULSE_WIDTH_US", label="脉冲宽度",
        unit="µs", minimum=1e-3, group="basic",
        description="固定的单脉冲宽度；默认 8.03 µs 取自 pi_width_scan 的精确 π 波形。"
                    "不能超过半个射频周期，且不小于两倍沿宽。")
    z_tail_comp_enabled: bool = parameter(default=True, external_name="Z_TAIL_COMP_ENABLED",
        label="Z 拖尾补偿", group="basic",
        description="开启后每个 ±π 脉冲后接一段反号指数尾，使屏蔽筒慢响应在脉冲后抵消；"
                    "尾幅按脉宽由 Z_TAIL_S_F、Z_TAIL_TAU_E_US 精确计算，τc = s_f·τe。关闭时为原升余弦脉冲。")
    z_tail_s_f: float = parameter(default=0.544, external_name="Z_TAIL_S_F", label="屏蔽模型快分量 s_f",
        minimum=1e-3, maximum=0.999, active_when="Z_TAIL_COMP_ENABLED", group="basic",
        description="原子处 z 场中随电流瞬时出现的比例；其余 1 − s_f 以 τe 延迟到达。")
    z_tail_tau_e_us: float = parameter(default=46.9, external_name="Z_TAIL_TAU_E_US", label="屏蔽模型时间常数 τe",
        unit="µs", minimum=1e-3, maximum=1000.0, active_when="Z_TAIL_COMP_ENABLED", group="basic",
        description="慢分量的一阶时间常数；补偿尾的衰减时间 τc = s_f·τe。")

    # ---- 静态偏置 ----
    x_bias_v: float = parameter(default=-0.0409, external_name="X_BIAS_V", label="X 静态偏置",
        unit="V", group="basic",
        description="A02 在 X 通道加的静态偏置，用来补掉残余横向场；默认取运行目录 pi_width_scan "
                    "精确 π 波形下的重标值。0 表示不加偏置、通道保持关闭。")
    y_bias_v: float = parameter(default=-0.0069, external_name="Y_BIAS_V", label="Y 静态偏置",
        unit="V", group="basic",
        description="叠加在 Y 向射频正弦上的直流 offset；默认取同一运行的重标值。")
    main_field_current_ma: float = parameter(default=0.0, external_name="MAIN_FIELD_CURRENT_MA",
        label="主磁场电流", unit="mA", minimum=-20.0, maximum=20.0, group="basic",
        description="GS200 主磁场电流；0 表示关闭输出（原行为），非零时按该电流输出并回读，"
                    "收尾恢复运行前状态。按 main_magnetic_field 安全限值校验。")

    # ---- 链路 ----
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
    n_repeat: int = parameter(default=1, external_name="N_REPEAT", label="每点重复次数", minimum=1, maximum=200,
        group="basic", description="每个相位重复采集的 MFLI 读数次数；默认 1 次，逐点标准误为 0。")
    scan_point_duration_s: float = parameter(default=0.2, external_name="SCAN_POINT_DURATION_S", label="每点采样时长",
        unit="s", minimum=1e-6, group="basic", description="每次 MFLI 读数在温控关闭窗口内连续采集的时长。")
    system_settle_s: float = parameter(default=0.1, external_name="SYSTEM_SETTLE_S", label="系统稳定等待",
        unit="s", minimum=0, group="basic", description="改相位或关温控后、开始读数前的等待。")
    temp_group_size: int = parameter(default=2, external_name="TEMP_GROUP_SIZE", label="温控关闭分组点数",
        minimum=1, maximum=200, group="basic",
        description="每关闭温控一次连续采集的读数个数；组末恢复温控并等待 TEMP_SWITCH_ON_LAG_S 后再落盘。")
    temp_switch_on_lag_s: float = parameter(default=2.0, external_name="TEMP_SWITCH_ON_LAG_S", label="恢复温控后等待",
        unit="s", minimum=0, group="basic")

    # ---- MFLI 第 0 路解调器 ----
    demod0_idx: int = parameter(default=0, external_name="DEMOD0_IDX", label="解调器索引", read_only=True,
        description="固定为第 0 路解调器；本实验不占用其他解调器。")
    demod0_signal_range_v: float = parameter(default=3.0, external_name="DEMOD0_SIGNAL_RANGE", label="输入量程",
        unit="V", minimum=1e-6, group="basic", description="Signal Input 1 的量程；按探测器信号幅度选择。")
    demod0_order: int = parameter(default=4, external_name="DEMOD0_ORDER", label="解调器阶数", minimum=1, maximum=8)
    demod0_tc_s: float = parameter(default=0.001, external_name="DEMOD0_TC", label="解调器时间常数", unit="s",
        minimum=1e-9,
        description="第 0 路解调器低通时间常数；默认取运行目录 pi_width_scan 的配置。")
    demod0_rate_sa_s: float = parameter(default=10000.0, external_name="DEMOD0_RATE", label="解调器采样率",
        unit="Sa/s", minimum=1,
        description="第 0 路解调器数据速率；默认取运行目录 pi_width_scan 的配置。")

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
    def z_tail_tau_c_us(self) -> float:
        """补偿尾的衰减时间 τc = s_f·τe；未开启补偿时也给出声明值。"""
        return self.z_tail_s_f * self.z_tail_tau_e_us

    def rf_phase_offset_us(self, phase_deg: float | None = None) -> float:
        """射频相位对应的时间偏移（µs）；5 kHz 下 1° = 0.556 µs。"""
        phase = self.y_rf_phase_deg if phase_deg is None else float(phase_deg)
        return phase / 360.0 * self.pulse_period_us

    def phase_angles_deg(self) -> list[float]:
        """整周相位网格；终点不写入，避免 0° 与 360° 重复。"""
        return phase_grid(self.y_rf_phase_start_deg, self.y_rf_phase_stop_deg,
                          self.y_rf_phase_step_deg, include_stop=False)

    def widths_us(self) -> list[float]:
        """预检与功耗核算使用的脉宽列表（本实验只有一个固定宽度）。"""
        return [float(self.z_pulse_width_us)]
