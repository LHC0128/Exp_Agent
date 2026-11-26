"""Z 脉冲链公共编排读取的参数协议、任意波常量与电流换算。

协议只描述公共编排实际访问的字段，实验自己的参数模型在结构上满足它；
导入本模块不实例化参数、不读取配置、不连接设备。

驱动链路有两种：默认经 Aigtek 1200B 功放（历史行为），或功放物理旁路后
DG4000 直驱 Z 线圈。后者只在参数模型声明 ``z_drive_amplifier_enabled = False``
时启用，未声明该属性的实验保持原行为，换算结果与迁移前逐位一致。
"""
from __future__ import annotations

from typing import Protocol

# 任意波点数：与 pi_pulse_calibration 相同，DG4000 的上限正好是 16384 点。
ARB_POINTS = 16384
# DG4000 高阻档（负载 INF）的幅度上限，单位 Vpp；直驱模式的脉冲幅度受此约束。
DG_HIGH_Z_MAX_VPP = 20.0
# 无放大器时 DG4000 源阻抗的默认值，单位 Ω；DG4000 输出阻抗固定 50 Ω。
DEFAULT_DRIVE_SOURCE_IMPEDANCE_OHM = 50.0


class PulseChainParams(Protocol):
    """公共编排与相位写入需要的最小参数集合（单位见各实验的参数模型）。"""

    y_rf_freq_hz: float
    y_rf_vpp: float
    y_rf_phase_deg: float
    y_bias_v: float
    x_bias_v: float
    z_peak_current_ma: float
    z_pulse_vpp: float
    z_pulse_edge_us: float
    z_tail_comp_enabled: bool
    z_tail_s_f: float
    z_tail_tau_e_us: float
    amplifier_gain_db: float
    amplifier_input_impedance_ohm: float
    amplifier_output_impedance_ohm: float
    sense_resistor_ohm: float
    damping_resistor_ohm: float
    coil_resistance_ohm: float
    resistor_rated_w: float
    demod0_idx: int
    demod0_signal_range_v: float
    demod0_order: int
    demod0_tc_s: float
    demod0_rate_sa_s: float
    n_repeat: int
    scan_point_duration_s: float
    system_settle_s: float
    temp_group_size: int
    temp_switch_on_lag_s: float

    @property
    def z_tail_tau_c_us(self) -> float: ...

    @property
    def pulse_period_us(self) -> float: ...

    @property
    def pulse_vpp(self) -> float: ...

    @property
    def amps_per_drive_volt(self) -> float: ...

    def rf_phase_offset_us(self, phase_deg: float | None = None) -> float: ...

    def to_external(self) -> dict[str, object]: ...

    def validate(self, root) -> list[str]: ...


def amplifier_enabled(params) -> bool:
    """参数侧是否使用 1200B 功放；未声明该属性的实验保持历史行为（启用）。"""
    return bool(getattr(params, "z_drive_amplifier_enabled", True))


def drive_gain(params) -> float:
    """驱动链路电压增益：有放大器取 AMPLIFIER_GAIN_DB，直驱固定为 1。"""
    if not amplifier_enabled(params):
        return 1.0
    return 10.0 ** (params.amplifier_gain_db / 20.0)


def loop_resistance_ohm(params) -> float:
    """Z 回路总电阻：采样 + 阻尼 + 线圈 + 末级输出阻抗（直驱时为 DG 源阻抗）。"""
    total = params.sense_resistor_ohm + params.damping_resistor_ohm + params.coil_resistance_ohm
    if amplifier_enabled(params):
        return total + params.amplifier_output_impedance_ohm
    return total + float(getattr(params, "z_drive_source_impedance_ohm", DEFAULT_DRIVE_SOURCE_IMPEDANCE_OHM))


def amps_per_drive_volt(params) -> float:
    """每伏驱动电压对应的线圈电流（A/V）。"""
    return drive_gain(params) / loop_resistance_ohm(params)


def pulse_vpp(params) -> float:
    """Z 通道写入的信号源幅度：有放大器按目标峰值电流换算，直驱直接取电压设定。"""
    if not amplifier_enabled(params):
        return float(params.z_pulse_vpp)
    return 2.0 * params.z_peak_current_ma * 1e-3 / amps_per_drive_volt(params)


def estimated_peak_current_ma(params) -> float:
    """按已知驱动增益与回路电阻估算的线圈峰值电流（mA），不是直接测量值。"""
    if amplifier_enabled(params):
        return float(params.z_peak_current_ma)
    return pulse_vpp(params) / 2.0 * amps_per_drive_volt(params) * 1e3
