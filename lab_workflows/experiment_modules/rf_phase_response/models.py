"""射频相位响应的强类型参数（schema 3）：MFLI MF-MOD 载波 + Demod 4 独立读出。

射频由 MFLI Signal Output 1 的 MF-MOD 无调制载波输出（Osc 2 载波），经串联大电阻接 Y 射频
线圈；解调读出固定 Demod 4（索引 3）、参考 Osc 2，与信号源共用 10 MHz 外部参考。Z 通道仍由
DG4000 直驱 ±π 脉冲（1200B 已物理旁路），X 静态偏置由 DG X 通道给出。``Y_RF_PHASE_START/
STOP/STEP_DEG`` 给出整周相位网格，共享扫描把 Demod 4 解调相位转到实测工作点。

旧键 ``Y_RF_PHASE_DEG``、``RF_DIRECTION``、``N_REPEAT``、``TEMP_GROUP_SIZE``、
``AMPLIFIER_*``、``Z_PEAK_CURRENT_MA`` 保留用于读取旧配置与历史快照；新采集不再使用它们。
``Z_PULSE_VPP`` 是 DG 高阻档写入幅度，线圈电流按已知回路电阻估算、不是直接测量值，
拖尾补偿默认关闭（只发原升余弦 ±脉冲）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, ClassVar

from ...experiment_params import ExperimentParams, parameter
from ..z_chain_common import params as chain_params
from ..z_chain_common.phase import phase_grid
from ..z_chain_common.rf_phase import MFLI_MAX_SIGOUT_V, PM_DEPTH_DEG

CARRIER_HZ = 5000.0
# 11-25 实机记录：0.5 Vpp 下读出饱和、0.05 Vpp 保持线性，默认取 0.05 Vpp。
CARRIER_VPP = 0.05
READOUT_DEMOD_IDX = 3


@dataclass
class RfPhaseResponseParams(ExperimentParams):
    schema_version: ClassVar[int] = 3
    # 本实验的 1200B 已物理旁路：DG4000 直驱 Z 线圈，不连接也不开关功放。
    z_drive_amplifier_enabled: ClassVar[bool] = False
    # 新实验没有历史短等待键；保留声明以便普通气室采集的迁移规则统一适用。
    system_settle_legacy_defaults: ClassVar[dict[str, float]] = {}
    removed_temperature_keys: ClassVar[tuple[str, ...]] = ()

    run_tag: str = parameter(default="rf_phase_response", external_name="RUN_TAG", label="运行标签", group="basic")

    # ---- MFLI MF-MOD 载波与静态偏置 ----
    y_rf_freq_hz: float = parameter(default=CARRIER_HZ, external_name="Y_RF_FREQ_HZ", label="Y 向射频频率",
        unit="Hz", minimum=1e-6, group="basic",
        description="MFLI MF-MOD Osc 2 载波频率，同时是 Z 双脉冲周期 1e6/f（5 kHz → 200 µs）与共同触发频率。")
    y_rf_vpp: float = parameter(default=CARRIER_VPP, external_name="Y_RF_VPP", label="Y 向射频幅度", unit="Vpp",
        minimum=0.001, maximum=20.0, group="basic",
        description="由 MFLI Signal Output 1（MF-MOD 载波）按峰值 Vpk = Vpp/2 输出；"
                    "11-25 实机记录 0.5 Vpp 饱和、0.05 Vpp 线性，默认取 0.05 Vpp。")
    y_bias_v: float = parameter(default=0.08, external_name="Y_BIAS_V", label="Y 静态偏置", unit="V",
        group="basic",
        description="叠加在 MFLI Signal Output 1 的直流偏置（sigouts/0/offset）。")
    x_bias_v: float = parameter(default=0.16, external_name="X_BIAS_V", label="X 静态偏置", unit="V",
        group="basic",
        description="DG X 通道的 DC 静态偏置，用来补掉残余横向场；0 表示不加偏置、通道保持关闭。")
    main_field_current_ma: float = parameter(default=-0.028, external_name="MAIN_FIELD_CURRENT_MA",
        label="主磁场电流", unit="mA", minimum=-20.0, maximum=20.0, group="basic",
        description="GS200 主磁场电流；0 表示关闭输出，非零时按该电流输出并回读，"
                    "收尾恢复运行前状态。按 main_magnetic_field 安全限值校验。")

    # ---- Z 向脉冲与驱动链路 ----
    z_peak_current_ma: float = parameter(default=190.0, external_name="Z_PEAK_CURRENT_MA", label="Z 脉冲峰值电流",
        unit="mA", minimum=0.1, group="basic",
        description="仅功放链路的历史目标值；DG 直驱时电流按 Z_PULSE_VPP 与回路电阻估算，本键只用于读旧配置。")
    z_pulse_vpp: float = parameter(default=20.0, external_name="Z_PULSE_VPP", label="Z 脉冲幅度",
        unit="Vpp", minimum=1e-6, maximum=20.0, group="basic",
        description="DG4000 直驱时写入 Z 通道的高阻档幅度；峰值为 Vpp/2，真实线圈电流按回路电阻估算。"
                    "高阻档上限 20 Vpp（峰峰值），预检超限即拒绝。")
    z_pulse_width_us: float = parameter(default=9.0, external_name="Z_PULSE_WIDTH_US", label="Z 单脉冲宽度",
        unit="µs", minimum=1e-3, group="basic",
        description="固定的单脉冲宽度；不能超过半个射频周期，且不小于两倍沿宽。")
    z_pulse_edge_us: float = parameter(default=1.0, external_name="Z_PULSE_EDGE_US", label="升/降沿宽度",
        unit="µs", minimum=0.05, description="升余弦沿；等效面积 = 宽度 − 沿宽")
    z_tail_comp_enabled: bool = parameter(default=False, external_name="Z_TAIL_COMP_ENABLED",
        label="Z 拖尾补偿", group="basic",
        description="开启后每个 ±π 脉冲后接一段反号指数尾，使屏蔽筒慢响应在脉冲后抵消；"
                    "尾幅按脉宽由 Z_TAIL_S_F、Z_TAIL_TAU_E_US 精确计算，τc = s_f·τe。"
                    "DG 直驱阶段默认关闭（只发原升余弦 ±脉冲）。")
    z_tail_s_f: float = parameter(default=0.5, external_name="Z_TAIL_S_F", label="屏蔽模型快分量 s_f",
        minimum=1e-3, maximum=0.999, active_when="Z_TAIL_COMP_ENABLED",
        description="原子处 z 场中随电流瞬时出现的比例；其余 1 − s_f 以 τe 延迟到达。")
    z_tail_tau_e_us: float = parameter(default=38.3, external_name="Z_TAIL_TAU_E_US",
        label="屏蔽模型时间常数 τe", unit="µs", minimum=1e-3, maximum=1000.0,
        active_when="Z_TAIL_COMP_ENABLED",
        description="慢分量的一阶时间常数；补偿尾的衰减时间 τc = s_f·τe。")
    # 以下 AMPLIFIER_* 只在旧配置/历史快照里保留；DG 直驱路径不写也不使用功放设置。
    amplifier_gain_db: float = parameter(default=20.0, external_name="AMPLIFIER_GAIN_DB", label="功放增益",
        unit="dB", minimum=0, maximum=20,
        description="历史功放链路的目标增益；DG 直驱时不使用，仅用于读旧配置。")
    amplifier_input_impedance_ohm: float = parameter(default=1_000_000.0,
        external_name="AMPLIFIER_INPUT_IMPEDANCE_OHM", label="功放输入阻抗", unit="Ω",
        options=((50.0, "50 Ω"), (1_000_000.0, "1 MΩ")),
        description="历史功放链路参数；DG 直驱时不使用。")
    amplifier_output_impedance_ohm: float = parameter(default=50.0,
        external_name="AMPLIFIER_OUTPUT_IMPEDANCE_OHM", label="功放输出阻抗", unit="Ω",
        options=((50.0, "50 Ω"), (1.0, "1 Ω")),
        description="历史功放链路参数；DG 直驱时不使用，回路电阻改计 Z_DRIVE_SOURCE_IMPEDANCE_OHM。")
    amplifier_write_settings: bool = parameter(default=True, external_name="AMPLIFIER_WRITE_SETTINGS",
        label="连接时写入功放设置",
        description="历史功放链路参数；DG 直驱时不使用。")
    z_drive_source_impedance_ohm: float = parameter(default=50.0,
        external_name="Z_DRIVE_SOURCE_IMPEDANCE_OHM", label="DG 源阻抗", unit="Ω",
        minimum=0.0, maximum=1_000_000.0,
        description="DG 直驱时计入回路的信号源输出阻抗；DG4000 固定 50 Ω，按已知回路电阻估算线圈电流。")
    sense_resistor_ohm: float = parameter(default=10.0, external_name="SENSE_RESISTOR_OHM", label="采样电阻",
        unit="Ω", minimum=1e-3)
    damping_resistor_ohm: float = parameter(default=10.0, external_name="DAMPING_RESISTOR_OHM", label="阻尼电阻",
        unit="Ω", minimum=0)
    coil_resistance_ohm: float = parameter(default=2.0, external_name="COIL_RESISTANCE_OHM", label="线圈直流电阻",
        unit="Ω", minimum=0)
    resistor_rated_w: float = parameter(default=2.0, external_name="RESISTOR_RATED_W", label="电阻额定功率",
        unit="W", minimum=0.01)

    # ---- 逐点采集与温控门控 ----
    static_point_duration_s: float = parameter(default=0.2, external_name="SCAN_POINT_DURATION_S",
        label="每点 MFLI 采集时长", unit="s", minimum=0.05, maximum=1.0, group="basic",
        description="共享整周扫描在每个相位点的连续采样时长；每点独立进出一次温控窗口。")
    system_settle_s: float = parameter(default=0.1, external_name="SYSTEM_SETTLE_S", label="系统稳定等待",
        unit="s", minimum=0.0, maximum=4.0, group="basic",
        description="关温控后、开始读数前的等待。")
    temp_switch_on_lag_s: float = parameter(default=2.0, external_name="TEMP_SWITCH_ON_LAG_S",
        label="恢复温控后等待", unit="s", minimum=0.1, maximum=30.0, group="basic",
        description="每点恢复温控（5 V ON）并回读后的固定等待，之后才落盘。")

    # ---- MFLI Demod 4 读出（Osc 2 参考） ----
    demod0_idx: int = parameter(default=READOUT_DEMOD_IDX, external_name="DEMOD0_IDX",
        label="MFLI 读出解调器索引", read_only=True,
        description="开启 MF-MOD 后 Demod 1–3 由调制器占用，原子读出固定使用 Demod 4（索引 3）。")
    demod0_signal_range_v: float = parameter(default=3.0, external_name="DEMOD0_SIGNAL_RANGE",
        label="MFLI 输入量程", unit="V", minimum=1e-3, maximum=3.0,
        description="Signal Input 1 的量程；按探测器信号幅度选择。")
    demod0_order: int = parameter(default=4, external_name="DEMOD0_ORDER", label="MFLI 滤波阶数",
        minimum=1, maximum=8)
    demod0_tc_s: float = parameter(default=0.001, external_name="DEMOD0_TC", label="MFLI 时间常数",
        unit="s", minimum=1e-6, maximum=0.1,
        description="整周相位扫描的静态读出滤波时间常数。")
    demod0_rate_sa_s: float = parameter(default=10000.0, external_name="DEMOD0_RATE",
        label="MFLI 解调传输率", unit="Sa/s", minimum=100.0, maximum=60000.0,
        description="整周相位扫描的连续流传输率；低于 70 kSa/s 阈值，保持 DAQ 同步采集。")

    # ---- 历史相位参数（新采集不再使用） ----
    rf_direction: str = parameter(default="Y", external_name="RF_DIRECTION", label="射频方向",
        options=(("Y", "Y 向（默认）"), ("X", "X 向")),
        description="历史键：旧采集按此选择射频 burst 所在横向线圈；新采集射频固定由 MFLI Out1 输出到 Y，"
                    "本键只用于读取旧配置。")
    y_rf_phase_deg: float = parameter(default=0.0, external_name="Y_RF_PHASE_DEG", label="历史射频参考相位",
        unit="°", minimum=0.0, maximum=360.0,
        description="历史键：旧采集 A02 起振时的 burst 相位；新采集载波相位由共享扫描逐点写入，本键只用于读旧配置。")
    y_rf_phase_start_deg: float = parameter(default=0.0, external_name="Y_RF_PHASE_START_DEG",
        label="相位扫描起点", unit="°", minimum=0.0, maximum=360.0, group="basic")
    y_rf_phase_stop_deg: float = parameter(default=360.0, external_name="Y_RF_PHASE_STOP_DEG",
        label="相位扫描终点", unit="°", minimum=0.0, maximum=360.0, group="basic",
        description="终点不写入：默认 0–360°、步进 10° 得到 36 个相位点，0° 与 360° 不重复。")
    y_rf_phase_step_deg: float = parameter(default=10.0, external_name="Y_RF_PHASE_STEP_DEG",
        label="相位扫描步进", unit="°", minimum=1e-3, group="basic")
    n_repeat: int = parameter(default=1, external_name="N_REPEAT", label="历史每点重复次数",
        minimum=1, maximum=200,
        description="历史键：旧采集每个相位的重复读数次数；新采集每点只取一帧、逐点独立温控，本键只用于读旧配置。")
    temp_group_size: int = parameter(default=2, external_name="TEMP_GROUP_SIZE", label="历史温控分组点数",
        minimum=1, maximum=200,
        description="历史键：旧采集每关闭温控一次连续采集的读数个数；新采集每点独立进出温控窗口，"
                    "本键只用于读旧配置。")
    set_tec_target: bool = parameter(default=False, external_name="SET_TEC_TARGET", label="下发 TEC 目标温度")
    target_temperature_c: float = parameter(default=90.0, external_name="TARGET_TEMPERATURE_C",
        label="TEC 目标温度", unit="°C", minimum=20.0, maximum=130.0, active_when="SET_TEC_TARGET",
        description="只下发目标；不启停 TEC、不判稳。未启用时保留现场设定。")

    # ---- 共享 Carrier 所需的派生量 ----
    @property
    def y_carrier_vpk(self) -> float:
        """MF-MOD 载波写入 ``mods/0/carrier/amplitude`` 的峰值电压。"""
        return self.y_rf_vpp / 2.0

    @property
    def y_pm_depth_rad(self) -> float:
        """MF-MOD 相干边带深度（2° 峰值）；本实验只输出无调制载波，该值仍需写入回读节点。"""
        return math.radians(PM_DEPTH_DEG)

    def y_output_envelope(self, *, pm_enabled: bool = True) -> tuple[float, float]:
        """返回 Signal Output 1 叠加偏置与调相边带后的理论瞬时电压包络 (min_v, max_v)。"""
        factor = 1.0 + self.y_pm_depth_rad if pm_enabled else 1.0
        half_span = factor * self.y_carrier_vpk
        return float(self.y_bias_v - half_span), float(self.y_bias_v + half_span)

    @property
    def sweep_order_hz(self) -> list[float]:
        """兼容共享 MF-MOD 载波接口的基带频率占位；本实验不启用相干边带 PM。"""
        return [float(self.y_rf_freq_hz)]

    @property
    def gain(self) -> float:
        """驱动链路电压增益：DG 直驱为 1，经功放为 10^(增益/20)。"""
        return chain_params.drive_gain(self)

    @property
    def loop_resistance_ohm(self) -> float:
        """Z 回路总电阻：采样 + 阻尼 + 线圈 + 末级输出阻抗（直驱时为 DG 源阻抗）。"""
        return chain_params.loop_resistance_ohm(self)

    @property
    def amps_per_drive_volt(self) -> float:
        return chain_params.amps_per_drive_volt(self)

    @property
    def pulse_vpp(self) -> float:
        """写入信号源的幅度：DG 直驱即 Z_PULSE_VPP，经功放时按目标峰值电流换算。"""
        return chain_params.pulse_vpp(self)

    @property
    def estimated_peak_current_ma(self) -> float:
        """按已知回路电阻估算的线圈峰值电流（mA），不是直接测量值。"""
        return chain_params.estimated_peak_current_ma(self)

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

    @classmethod
    def migrate_external(cls, values: dict[str, Any], schema_version: int) -> dict[str, Any]:
        """v1/v2 旧快照迁移：读出统一改到 Demod 4，并丢弃已停用的解调相位参数。"""
        migrated = super().migrate_external(values, schema_version)
        if schema_version < 3:
            migrated.pop("DEMOD0_PHASE_DEG", None)
            migrated["DEMOD0_IDX"] = READOUT_DEMOD_IDX
        return migrated

    def validate_model(self) -> list[str]:
        """读出解调器、MFLI 输出包络与 DG 直驱电压必须有明确边界。"""
        errors = []
        if self.demod0_idx != READOUT_DEMOD_IDX:
            errors.append("启用 MF-MOD 后 Demod 1–3 被调制器占用，原子读出须使用 Demod 4（索引 3）")
        used = (self.y_rf_freq_hz, self.y_rf_vpp, self.y_bias_v, self.x_bias_v,
                self.main_field_current_ma, self.z_pulse_width_us, self.z_pulse_edge_us,
                self.demod0_rate_sa_s, self.demod0_tc_s, self.demod0_signal_range_v,
                self.static_point_duration_s, self.system_settle_s, self.temp_switch_on_lag_s,
                *((self.z_tail_s_f, self.z_tail_tau_e_us) if self.z_tail_comp_enabled else ()),
                *((self.target_temperature_c,) if self.set_tec_target else ()))
        if any(not math.isfinite(value) for value in used):
            errors.append("实际使用的标量参数必须有限")
        if not math.isfinite(self.y_rf_freq_hz) or self.y_rf_freq_hz <= 0.0:
            errors.append("Y_RF_FREQ_HZ 必须是有限正值")
        if not math.isfinite(self.y_rf_vpp) or self.y_rf_vpp <= 0.0:
            errors.append("Y_RF_VPP 必须是有限正值")
        if not (2.0 * self.z_pulse_edge_us < self.z_pulse_width_us <= self.pulse_period_us / 2.0):
            errors.append("Z 单脉冲须宽于两倍沿宽且不超过半个周期")
        if max(abs(value) for value in self.y_output_envelope(pm_enabled=True)) > MFLI_MAX_SIGOUT_V:
            errors.append(f"Y 射频与偏置总包络超出 MFLI Signal Output 1 的 ±{MFLI_MAX_SIGOUT_V:g} V 安全范围")
        if not chain_params.amplifier_enabled(self):
            # DG 直驱：电压设定必须有限且不超过 DG4000 高阻档上限，旧功放键不再参与校验。
            if not math.isfinite(self.z_pulse_vpp) or self.z_pulse_vpp <= 0.0:
                errors.append("Z_PULSE_VPP 必须是有限正值")
            elif self.z_pulse_vpp > chain_params.DG_HIGH_Z_MAX_VPP:
                errors.append(f"无放大器时 Z_PULSE_VPP={self.z_pulse_vpp:g} Vpp 超过 DG4000 高阻档上限 "
                              f"{chain_params.DG_HIGH_Z_MAX_VPP:g} Vpp")
            if not math.isfinite(self.z_drive_source_impedance_ohm) or self.z_drive_source_impedance_ohm < 0.0:
                errors.append("Z_DRIVE_SOURCE_IMPEDANCE_OHM 必须是有限非负数")
        try:
            self.phase_angles_deg()
        except ValueError as exc:
            errors.append(str(exc))
        return errors
