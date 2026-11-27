"""独立相位灵敏度实验的参数。基带频率、载波相位与锁相相位分别定义。

1200B 功放已物理旁路，DG4000 直驱 Z 线圈：Z 脉冲按 ``Z_PULSE_VPP`` 设电压（默认 20 Vpp，
DG 高阻档上限），线圈峰值电流按已知回路电阻估算、不是直接测量值；``Z_PEAK_CURRENT_MA``
与 ``AMPLIFIER_*`` 只保留用于读取旧配置和历史快照。拖尾补偿默认关闭（只发原升余弦 ±脉冲）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, ClassVar

from ...experiment_params import ExperimentParams, parameter
from ..z_chain_common import params as chain_params
from ..z_chain_common.rf_phase import MFLI_MAX_SIGOUT_V, MOD_IDX, PM_DEPTH_DEG

# 统一 Demod n ↔ Osc n：载波由 Demod 1 / Osc 1 产生，边带用 Osc 2 / Osc 3，读出 Demod 4 / Osc 4。
MOD_CARRIER_OSC_IDX = 0
MOD_SIDEBAND_OSC_IDXS = (1, 2)
READOUT_OSC_IDX = 3

CARRIER_HZ = 5000.0
# 11-25 实机记录：0.5 Vpp 下读出饱和、0.05 Vpp 保持线性，默认取 0.05 Vpp。
CARRIER_VPP = 0.5
READOUT_DEMOD_IDX = 3


@dataclass
class RfPhaseSensitivityParams(ExperimentParams):
    schema_version: ClassVar[int] = 4
    # 本实验的 1200B 已物理旁路：DG4000 直驱 Z 线圈，不连接也不开关功放。
    z_drive_amplifier_enabled: ClassVar[bool] = False
    system_settle_legacy_defaults: ClassVar[dict[str, float]] = {}
    removed_temperature_keys: ClassVar[tuple[str, ...]] = ()

    run_tag: str = parameter(default="phase_sensitivity", external_name="RUN_TAG", label="运行标签", group="basic")
    y_rf_freq_hz: float = parameter(default=CARRIER_HZ, external_name="Y_RF_FREQ_HZ", label="Y 射频载波频率",
                                   unit="Hz", read_only=True)
    y_rf_vpp: float = parameter(default=CARRIER_VPP, external_name="Y_RF_VPP", label="Y 射频幅度",
                                unit="Vpp", minimum=0.001, maximum=20.0, group="basic",
                                description="由 MFLI Signal Output 1（MF-MOD 载波）按峰值 Vpk = Vpp / 2 输出；"
                                            "11-25 实机记录 0.5 Vpp 饱和、0.05 Vpp 线性，默认取 0.05 Vpp。")
    x_bias_v: float = parameter(default=0.0, external_name="X_BIAS_V", label="X 静态偏置", unit="V", group="basic")
    y_bias_v: float = parameter(default=0.0, external_name="Y_BIAS_V", label="Y 正弦直流偏置", unit="V", group="basic",
                                description="叠加在 MFLI Signal Output 1 的直流偏置（sigouts/0/offset）。")
    main_field_current_ma: float = parameter(default=-0.028, external_name="MAIN_FIELD_CURRENT_MA", label="主磁场电流",
                                             unit="mA", group="basic", description="0 表示关闭；会话结束恢复原状态。")
    z_peak_current_ma: float = parameter(default=190.0, external_name="Z_PEAK_CURRENT_MA", label="Z 脉冲峰值电流",
                                         unit="mA", minimum=0.1, group="basic",
                                         description="仅功放链路的历史目标值；DG 直驱时电流按 Z_PULSE_VPP 与"
                                                     "回路电阻估算，本键只用于读旧配置。")
    z_pulse_vpp: float = parameter(default=20.0, external_name="Z_PULSE_VPP", label="Z 脉冲幅度",
                                   unit="Vpp", minimum=1e-6, maximum=20.0, group="basic",
                                   description="DG4000 直驱时写入 Z 通道的高阻档幅度；峰值为 Vpp/2，"
                                               "真实线圈电流按回路电阻估算；高阻档上限 20 Vpp。")
    z_pulse_width_us: float = parameter(default=9.0, external_name="Z_PULSE_WIDTH_US", label="Z 单个 π 脉冲宽度",
                                        unit="µs", minimum=0.1, group="basic")
    z_pulse_edge_us: float = parameter(default=1.0, external_name="Z_PULSE_EDGE_US", label="Z 脉冲升降沿宽度",
                                       unit="µs", minimum=0.05)
    z_tail_comp_enabled: bool = parameter(default=False, external_name="Z_TAIL_COMP_ENABLED", label="Z 拖尾补偿",
                                          group="basic",
                                          description="DG 直驱阶段默认关闭（只发原升余弦 ±脉冲）；开启后按"
                                                      "Z_TAIL_S_F、Z_TAIL_TAU_E_US 计算反号指数尾。")
    z_tail_s_f: float = parameter(default=0.5, external_name="Z_TAIL_S_F", label="屏蔽模型快分量",
                                  minimum=0.001, maximum=0.999, active_when="Z_TAIL_COMP_ENABLED")
    z_tail_tau_e_us: float = parameter(default=38.3, external_name="Z_TAIL_TAU_E_US", label="屏蔽模型时间常数",
                                       unit="µs", minimum=0.001, maximum=1000, active_when="Z_TAIL_COMP_ENABLED")
    # 以下 AMPLIFIER_* 只在旧配置/历史快照里保留；DG 直驱路径不写也不使用功放设置。
    amplifier_gain_db: float = parameter(default=20.0, external_name="AMPLIFIER_GAIN_DB", label="功放增益",
                                         unit="dB", minimum=0, maximum=20,
                                         description="历史功放链路的目标增益；DG 直驱时不使用，仅用于读旧配置。")
    amplifier_input_impedance_ohm: float = parameter(default=1e6, external_name="AMPLIFIER_INPUT_IMPEDANCE_OHM",
        label="功放输入阻抗", unit="Ω", options=((50., "50 Ω"), (1e6, "1 MΩ")),
        description="历史功放链路参数；DG 直驱时不使用。")
    amplifier_output_impedance_ohm: float = parameter(default=50., external_name="AMPLIFIER_OUTPUT_IMPEDANCE_OHM",
        label="功放输出阻抗", unit="Ω", options=((50., "50 Ω"), (1., "1 Ω")),
        description="历史功放链路参数；DG 直驱时不使用，回路电阻改计 Z_DRIVE_SOURCE_IMPEDANCE_OHM。")
    z_drive_source_impedance_ohm: float = parameter(default=50.0, external_name="Z_DRIVE_SOURCE_IMPEDANCE_OHM",
        label="DG 源阻抗", unit="Ω", minimum=0.0, maximum=1_000_000.0,
        description="DG 直驱时计入回路的信号源输出阻抗；DG4000 固定 50 Ω，按已知回路电阻估算线圈电流。")
    sense_resistor_ohm: float = parameter(default=10., external_name="SENSE_RESISTOR_OHM", label="采样电阻", unit="Ω", minimum=0.001)
    damping_resistor_ohm: float = parameter(default=10., external_name="DAMPING_RESISTOR_OHM", label="阻尼电阻", unit="Ω", minimum=0)
    coil_resistance_ohm: float = parameter(default=2., external_name="COIL_RESISTANCE_OHM", label="Z 线圈直流电阻", unit="Ω", minimum=0)
    resistor_rated_w: float = parameter(default=2., external_name="RESISTOR_RATED_W", label="电阻额定功率", unit="W", minimum=0.01)

    demod0_idx: int = parameter(default=READOUT_DEMOD_IDX, external_name="DEMOD0_IDX", label="MFLI 读出解调器索引",
                                read_only=True,
                                description="Demod n ↔ Osc n：载波 Demod 1 / Osc 1，原子读出固定 Demod 4 / Osc 4（索引 3）。")
    demod0_signal_range_v: float = parameter(default=3., external_name="DEMOD0_SIGNAL_RANGE", label="MFLI 输入量程",
                                            unit="V", minimum=0.001, maximum=3.)
    demod0_order: int = parameter(default=4, external_name="DEMOD0_ORDER", label="MFLI 滤波阶数", minimum=1, maximum=8)
    demod0_tc_s: float = parameter(default=0.001, external_name="DEMOD0_TC", label="A03/A04 MFLI 时间常数",
                                   unit="s", minimum=1e-6, maximum=0.1,
                                   description="A03 整周扫描与 A04 局部斜率标定的静态读出滤波时间常数，保持不变。")
    demod0_rate_sa_s: float = parameter(default=5000., external_name="DEMOD0_RATE", label="A03/A04 MFLI 解调传输率",
                                        unit="Sa/s", minimum=1000, maximum=20000,
                                        description="A03/A04 静态相位采样的连续流传输率。")
    demod0_dynamic_tc_s: float = parameter(default=1e-6, external_name="DEMOD0_DYNAMIC_TC", label="A05/A06 MFLI 时间常数",
                                           unit="s", minimum=1e-6, maximum=0.1,
                                           description="仅对 A05 频响与 A06 噪声放宽的宽带时间常数（不改 A03/A04）；不再做软件滤波反卷积。")
    demod0_dynamic_rate_sa_s: float = parameter(default=50000., external_name="DEMOD0_DYNAMIC_RATE", label="A05/A06 MFLI 解调传输率",
                                                unit="Sa/s", minimum=1000, maximum=60000,
                                                description="A05/A06 高带宽连续流采样率（低于 70 kSa/s 阈值，保持 DAQ 连续采集）。")
    static_point_duration_s: float = parameter(default=0.20, external_name="STATIC_POINT_DURATION_S",
                                               label="静态点 MFLI 采集时长", unit="s", minimum=0.05, maximum=1.)
    local_span_deg: float = parameter(default=15., external_name="LOCAL_SPAN_DEG", label="局部标定外档角度",
                                      unit="°", minimum=3, maximum=30, description="在最佳工作点两侧等分细扫的对称偏移跨度。")
    pm_frequencies_hz: list[float] = parameter(
        default_factory=lambda: [float(f) for f in range(3, 204, 10)] + [float(f) for f in range(250, 1001, 50)],
        external_name="PM_FREQUENCIES_HZ", label="基带调相频率列表", unit="Hz", group="basic",
        description="严格递增，不改变 5 kHz 载波，按列表顺序单次扫描（默认 3–1000 Hz：3–203 Hz 每 10 Hz "
                    "共 21 点、250–1000 Hz 每 50 Hz 共 16 点，错开 50 Hz 工频）。")
    pm_tone_duration_s: float = parameter(default=4., external_name="PM_TONE_DURATION_S", label="每档 MFLI 音调采集时长",
                                         unit="s", minimum=0.5, maximum=12.)
    noise_duration_s: float = parameter(default=4., external_name="NOISE_DURATION_S", label="单段无调制噪声采集时长",
                                       unit="s", minimum=1., maximum=60., group="basic",
                                       description="A06 每次关闭温控窗口内连续采集无调制噪声的时长；推荐 4 s 以限制单次关温控温漂。")
    noise_n_avg: int = parameter(default=6, external_name="NOISE_N_AVG", label="无调制噪声分段次数",
                                 minimum=1, maximum=60, group="basic",
                                 description="A06 独立温控窗口的噪声采集分段数；每段先恢复温控等待再采下一段，离线对各段 Welch PSD 等权平均。")
    welch_segment_s: float = parameter(default=4., external_name="WELCH_SEGMENT_S", label="Welch 分段时长",
                                       unit="s", minimum=1., maximum=10., group="basic")
    system_settle_s: float = parameter(default=0.3, external_name="SYSTEM_SETTLE_S", label="系统稳定等待",
                                       unit="s", minimum=0.1, maximum=4., group="basic")
    temp_switch_on_lag_s: float = parameter(default=2., external_name="TEMP_SWITCH_ON_LAG_S", label="A03/A04 温控恢复等待",
                                            unit="s", minimum=0.1, maximum=30.,
                                            description="A03/A04 静态点每次恢复温控后的固定等待时间。")
    dynamic_temp_switch_on_lag_s: float = parameter(default=4., external_name="DYNAMIC_TEMP_SWITCH_ON_LAG_S",
                                                    label="A05/A06 温控恢复等待", unit="s", minimum=0.1, maximum=30.,
                                                    group="basic",
                                                    description="A05 频响单音与 A06 噪声每段恢复温控（5 V ON）后的固定等待时间。")
    set_tec_target: bool = parameter(default=False, external_name="SET_TEC_TARGET", label="下发 TEC 目标温度")
    target_temperature_c: float = parameter(default=90., external_name="TARGET_TEMPERATURE_C", label="TEC 目标温度",
        unit="°C", minimum=20., maximum=130., active_when="SET_TEC_TARGET",
        description="只下发目标；不启停 TEC、不判稳。未启用时保留现场设定。")

    # 公共 Z 链的固定协议，不由本实验参数覆盖。
    amplifier_write_settings: ClassVar[bool] = True
    y_rf_phase_deg: ClassVar[float] = 0.

    @property
    def y_carrier_vpk(self) -> float:
        return self.y_rf_vpp / 2.0

    @property
    def y_pm_depth_rad(self) -> float:
        return math.radians(PM_DEPTH_DEG)

    def y_output_envelope(self, *, pm_enabled: bool = True) -> tuple[float, float]:
        """返回 MFLI Signal Output 1 叠加偏置与调相边带后的理论瞬时电压包络 (min_v, max_v)。"""
        factor = 1.0 + self.y_pm_depth_rad if pm_enabled else 1.0
        half_span = factor * self.y_carrier_vpk
        return float(self.y_bias_v - half_span), float(self.y_bias_v + half_span)

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
        return 1e6 / self.y_rf_freq_hz

    @property
    def z_tail_tau_c_us(self) -> float:
        return self.z_tail_s_f * self.z_tail_tau_e_us

    def rf_phase_offset_us(self, phase_deg: float | None = None) -> float:
        return self.pulse_period_us * (self.y_rf_phase_deg if phase_deg is None else phase_deg) / 360

    @property
    def pm_anchor_hz(self) -> float:
        return 20. if 20. in self.pm_frequencies_hz else self.pm_frequencies_hz[0]

    @property
    def sweep_order_hz(self) -> list[float]:
        return list(self.pm_frequencies_hz)

    @classmethod
    def migrate_external(cls, values: dict[str, Any], schema_version: int) -> dict[str, Any]:
        migrated = super().migrate_external(values, schema_version)
        if schema_version < 2:
            migrated.setdefault("DEMOD0_DYNAMIC_TC", migrated.get("DEMOD0_TC", 0.001))
            migrated.setdefault("DEMOD0_DYNAMIC_RATE", migrated.get("DEMOD0_RATE", 2000.0))
        if schema_version < 3:
            migrated.setdefault("NOISE_N_AVG", 1)
            migrated.setdefault("DYNAMIC_TEMP_SWITCH_ON_LAG_S", migrated.get("TEMP_SWITCH_ON_LAG_S", 2.0))
        if schema_version < 4:
            # 4：功放物理旁路，新增 DG 直驱电压设定与源阻抗；旧快照按默认值补齐。
            migrated.setdefault("Z_PULSE_VPP", 20.0)
            migrated.setdefault("Z_DRIVE_SOURCE_IMPEDANCE_OHM", 50.0)
        return migrated

    def _noise_welch_segments(self) -> int:
        per_window = 1 + math.floor((self.noise_duration_s - self.welch_segment_s + 1e-12) / (0.5 * self.welch_segment_s))
        return int(self.noise_n_avg) * max(1, per_window)

    def validate_model(self) -> list[str]:
        errors = []
        if not math.isfinite(self.y_rf_freq_hz) or not math.isclose(self.y_rf_freq_hz, CARRIER_HZ, rel_tol=0.0, abs_tol=1e-9):
            errors.append("本方案只支持 Y 方向 5 kHz 载波")
        if not math.isfinite(self.y_rf_vpp) or self.y_rf_vpp <= 0:
            errors.append("Y_RF_VPP 必须为有限正值")
        if self.demod0_idx not in {0, READOUT_DEMOD_IDX}:
            errors.append("MFLI 读出解调器仅支持现用 Demod 4（索引 3）或历史 Demod 1（索引 0）")
        if not self.run_tag.strip() or any(c in self.run_tag for c in '/\\:*?"<>|\x00'):
            errors.append("RUN_TAG 必须是非空且可作目录名的标签")
        if type(self.noise_n_avg) is not int or self.noise_n_avg < 1:
            errors.append("NOISE_N_AVG 必须为不少于 1 的整数")
        if chain_params.amplifier_enabled(self):
            amplifier_values = (self.z_peak_current_ma, self.amplifier_gain_db)
        else:
            # DG 直驱：电压设定必须有限且不超过 DG4000 高阻档上限，旧功放键不再参与校验。
            amplifier_values = ()
            if not math.isfinite(self.z_pulse_vpp) or self.z_pulse_vpp <= 0.0:
                errors.append("Z_PULSE_VPP 必须是有限正值")
            elif self.z_pulse_vpp > chain_params.DG_HIGH_Z_MAX_VPP:
                errors.append(f"无放大器时 Z_PULSE_VPP={self.z_pulse_vpp:g} Vpp 超过 DG4000 高阻档上限 "
                              f"{chain_params.DG_HIGH_Z_MAX_VPP:g} Vpp")
            if not math.isfinite(self.z_drive_source_impedance_ohm) or self.z_drive_source_impedance_ohm < 0.0:
                errors.append("Z_DRIVE_SOURCE_IMPEDANCE_OHM 必须是有限非负数")
        if any(not math.isfinite(v) for v in (self.x_bias_v, self.y_bias_v, self.main_field_current_ma,
                self.z_pulse_width_us, self.z_pulse_edge_us, *amplifier_values,
                self.sense_resistor_ohm, self.damping_resistor_ohm, self.coil_resistance_ohm, self.resistor_rated_w,
                self.demod0_rate_sa_s, self.demod0_tc_s, self.demod0_dynamic_rate_sa_s, self.demod0_dynamic_tc_s,
                self.demod0_signal_range_v, self.local_span_deg,
                self.pm_tone_duration_s, self.static_point_duration_s, self.noise_duration_s,
                self.welch_segment_s, self.temp_switch_on_lag_s, self.dynamic_temp_switch_on_lag_s,
                *((self.z_tail_s_f, self.z_tail_tau_e_us) if self.z_tail_comp_enabled else ()),
                *((self.target_temperature_c,) if self.set_tec_target else ()))):
            errors.append("实际使用的标量参数必须有限")
        fm = self.pm_frequencies_hz
        if (not 2 <= len(fm) <= 40 or any(not math.isfinite(f) or not 1 <= f <= 1000 for f in fm)
                or any(a >= b for a, b in zip(fm, fm[1:]))):
            errors.append("PM_FREQUENCIES_HZ 须含 2–40 个严格递增的 1–1000 Hz 频点")
        elif self.pm_tone_duration_s * fm[0] < 10 or self.demod0_dynamic_rate_sa_s < 10 * max(fm):
            errors.append("基带 MFLI 记录至少覆盖最低频点 10 个周期，A05/A06 采样率至少为最高频点的 10 倍")
        if self.system_settle_s < 10 * self.demod0_order * max(self.demod0_tc_s, self.demod0_dynamic_tc_s):
            errors.append("SYSTEM_SETTLE_S 必须覆盖至少 10 × 滤波阶数 × 时间常数")
        if (math.isfinite(self.noise_duration_s) and math.isfinite(self.welch_segment_s)
                and self.welch_segment_s > 0 and self.noise_duration_s < self.welch_segment_s):
            errors.append("单段 NOISE_DURATION_S 不能小于 WELCH_SEGMENT_S")
        elif (type(self.noise_n_avg) is int and self.noise_n_avg >= 1
                and math.isfinite(self.noise_duration_s) and math.isfinite(self.welch_segment_s)
                and self.welch_segment_s > 0 and self._noise_welch_segments() < 2):
            errors.append("总 Welch 分段数不足 2 段，请增大 NOISE_N_AVG 或 NOISE_DURATION_S")
        if chain_params.amplifier_enabled(self) and math.isfinite(self.amplifier_gain_db) and not math.isclose(
                self.amplifier_gain_db * 2, round(self.amplifier_gain_db * 2), abs_tol=1e-9):
            errors.append("1200B 增益须为 0.5 dB 的整数倍")
        return errors

    @classmethod
    def derive_external(cls, values):
        p = cls.from_external(values)
        return {} if p.validate_model() else dict(
            y_rf_vpk=p.y_carrier_vpk, z_pulse_vpp=p.pulse_vpp, pulse_period_us=p.pulse_period_us,
            pm_depth_deg_peak=PM_DEPTH_DEG, pm_sweep_order_hz=p.sweep_order_hz,
            welch_resolution_hz=1 / p.welch_segment_s,
            total_noise_duration_s=p.noise_n_avg * p.noise_duration_s,
            noise_welch_segments=p._noise_welch_segments())
