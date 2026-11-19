"""Z 脉冲链公共编排读取的参数协议与任意波常量。

协议只描述公共编排实际访问的字段，实验自己的参数模型在结构上满足它；
导入本模块不实例化参数、不读取配置、不连接设备。
"""
from __future__ import annotations

from typing import Protocol

# 任意波点数：与 pi_pulse_calibration 相同，DG4000 的上限正好是 16384 点。
ARB_POINTS = 16384


class PulseChainParams(Protocol):
    """公共编排与相位写入需要的最小参数集合（单位见各实验的参数模型）。"""

    y_rf_freq_hz: float
    y_rf_vpp: float
    y_rf_phase_deg: float
    y_bias_v: float
    x_bias_v: float
    z_peak_current_ma: float
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
