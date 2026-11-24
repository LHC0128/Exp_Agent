"""独立相位灵敏度实验的参数。基带频率、载波相位与锁相相位分别定义。"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import ClassVar

from ...experiment_params import ExperimentParams, parameter

CARRIER_HZ = 5000.0
CARRIER_VPP = 0.05
PM_DEPTH_DEG = 1.0


@dataclass
class RfPhaseSensitivityParams(ExperimentParams):
    schema_version: ClassVar[int] = 1
    system_settle_legacy_defaults: ClassVar[dict[str, float]] = {}
    removed_temperature_keys: ClassVar[tuple[str, ...]] = ()

    run_tag: str = parameter(default="phase_sensitivity", external_name="RUN_TAG", label="运行标签", group="basic")
    y_rf_freq_hz: float = parameter(default=CARRIER_HZ, external_name="Y_RF_FREQ_HZ", label="Y 射频载波频率",
                                   unit="Hz", read_only=True)
    y_rf_vpp: float = parameter(default=CARRIER_VPP, external_name="Y_RF_VPP", label="Y 射频幅度",
                                unit="Vpp", read_only=True)
    x_bias_v: float = parameter(default=0.0, external_name="X_BIAS_V", label="X 静态偏置", unit="V", group="basic")
    y_bias_v: float = parameter(default=0.0, external_name="Y_BIAS_V", label="Y 正弦直流偏置", unit="V", group="basic")
    main_field_current_ma: float = parameter(default=-0.0655, external_name="MAIN_FIELD_CURRENT_MA", label="主磁场电流",
                                             unit="mA", group="basic", description="0 表示关闭；会话结束恢复原状态。")
    z_peak_current_ma: float = parameter(default=190.0, external_name="Z_PEAK_CURRENT_MA", label="Z 脉冲峰值电流",
                                         unit="mA", minimum=0.1, group="basic")
    z_pulse_width_us: float = parameter(default=8.6, external_name="Z_PULSE_WIDTH_US", label="Z 单个 π 脉冲宽度",
                                        unit="µs", minimum=0.1, group="basic")
    z_pulse_edge_us: float = parameter(default=1.0, external_name="Z_PULSE_EDGE_US", label="Z 脉冲升降沿宽度",
                                       unit="µs", minimum=0.05)
    z_tail_comp_enabled: bool = parameter(default=True, external_name="Z_TAIL_COMP_ENABLED", label="Z 拖尾补偿", group="basic")
    z_tail_s_f: float = parameter(default=0.5, external_name="Z_TAIL_S_F", label="屏蔽模型快分量",
                                  minimum=0.001, maximum=0.999, active_when="Z_TAIL_COMP_ENABLED")
    z_tail_tau_e_us: float = parameter(default=38.3, external_name="Z_TAIL_TAU_E_US", label="屏蔽模型时间常数",
                                       unit="µs", minimum=0.001, maximum=1000, active_when="Z_TAIL_COMP_ENABLED")
    amplifier_gain_db: float = parameter(default=20.0, external_name="AMPLIFIER_GAIN_DB", label="功放增益",
                                         unit="dB", minimum=0, maximum=20)
    amplifier_input_impedance_ohm: float = parameter(default=1e6, external_name="AMPLIFIER_INPUT_IMPEDANCE_OHM",
        label="功放输入阻抗", unit="Ω", options=((50., "50 Ω"), (1e6, "1 MΩ")))
    amplifier_output_impedance_ohm: float = parameter(default=50., external_name="AMPLIFIER_OUTPUT_IMPEDANCE_OHM",
        label="功放输出阻抗", unit="Ω", options=((50., "50 Ω"), (1., "1 Ω")))
    sense_resistor_ohm: float = parameter(default=10., external_name="SENSE_RESISTOR_OHM", label="采样电阻", unit="Ω", minimum=0.001)
    damping_resistor_ohm: float = parameter(default=10., external_name="DAMPING_RESISTOR_OHM", label="阻尼电阻", unit="Ω", minimum=0)
    coil_resistance_ohm: float = parameter(default=2., external_name="COIL_RESISTANCE_OHM", label="Z 线圈直流电阻", unit="Ω", minimum=0)
    resistor_rated_w: float = parameter(default=2., external_name="RESISTOR_RATED_W", label="电阻额定功率", unit="W", minimum=0.01)

    demod0_idx: int = parameter(default=0, external_name="DEMOD0_IDX", label="MFLI 解调器索引", read_only=True)
    demod0_signal_range_v: float = parameter(default=3., external_name="DEMOD0_SIGNAL_RANGE", label="MFLI 输入量程",
                                            unit="V", minimum=0.001, maximum=3.)
    demod0_order: int = parameter(default=4, external_name="DEMOD0_ORDER", label="MFLI 滤波阶数", minimum=1, maximum=8)
    demod0_tc_s: float = parameter(default=0.001, external_name="DEMOD0_TC", label="MFLI 时间常数",
                                   unit="s", minimum=1e-6, maximum=0.1,
                                   description="频响与噪声保持同一解调滤波条件，不作二次滤波校正。")
    demod0_rate_sa_s: float = parameter(default=2000., external_name="DEMOD0_RATE", label="MFLI 解调传输率",
                                        unit="Sa/s", minimum=1000, maximum=10000)
    static_point_duration_s: float = parameter(default=0.20, external_name="STATIC_POINT_DURATION_S",
                                               label="静态点 MFLI 采集时长", unit="s", minimum=0.05, maximum=1.)
    local_span_deg: float = parameter(default=15., external_name="LOCAL_SPAN_DEG", label="局部标定外档角度",
                                      unit="°", minimum=3, maximum=30, description="在最佳工作点两侧等分细扫的对称偏移跨度。")
    pm_frequencies_hz: list[float] = parameter(default_factory=lambda: [5., 10., 20., 40., 80.],
        external_name="PM_FREQUENCIES_HZ", label="基带调相频率列表", unit="Hz", group="basic",
        description="严格递增，不改变 5 kHz 载波。先按电学验证过的 20 Hz 对准；包含 20 Hz 时用它夹测，否则用最低频点。")
    pm_tone_duration_s: float = parameter(default=2., external_name="PM_TONE_DURATION_S", label="每档 MFLI 音调采集时长",
                                         unit="s", minimum=0.5, maximum=12.)
    noise_duration_s: float = parameter(default=24., external_name="NOISE_DURATION_S", label="无调制噪声记录时长",
                                       unit="s", minimum=8., maximum=60., group="basic")
    welch_segment_s: float = parameter(default=4., external_name="WELCH_SEGMENT_S", label="Welch 分段时长",
                                       unit="s", minimum=1., maximum=10., group="basic")
    system_settle_s: float = parameter(default=0.3, external_name="SYSTEM_SETTLE_S", label="系统稳定等待",
                                       unit="s", minimum=0.1, maximum=4., group="basic")
    temp_switch_on_lag_s: float = parameter(default=2., external_name="TEMP_SWITCH_ON_LAG_S", label="温控恢复等待",
                                           unit="s", minimum=0.1, maximum=15.)
    set_tec_target: bool = parameter(default=False, external_name="SET_TEC_TARGET", label="下发 TEC 目标温度")
    target_temperature_c: float = parameter(default=90., external_name="TARGET_TEMPERATURE_C", label="TEC 目标温度",
        unit="°C", minimum=20., maximum=130., active_when="SET_TEC_TARGET",
        description="只下发目标；不启停 TEC、不判稳。未启用时保留现场设定。")

    # 公共 Z 链的固定协议，不由本实验参数覆盖。
    amplifier_write_settings: ClassVar[bool] = True
    y_rf_phase_deg: ClassVar[float] = 0.

    @property
    def loop_resistance_ohm(self) -> float:
        return self.sense_resistor_ohm + self.damping_resistor_ohm + self.coil_resistance_ohm + self.amplifier_output_impedance_ohm

    @property
    def amps_per_drive_volt(self) -> float:
        return 10 ** (self.amplifier_gain_db / 20) / self.loop_resistance_ohm

    @property
    def pulse_vpp(self) -> float:
        return 2e-3 * self.z_peak_current_ma / self.amps_per_drive_volt

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
        anchor = self.pm_anchor_hz
        return [anchor, *(f for f in self.pm_frequencies_hz if f != anchor), anchor]

    def validate_model(self) -> list[str]:
        errors = []
        if (self.y_rf_freq_hz, self.y_rf_vpp, self.demod0_idx) != (CARRIER_HZ, CARRIER_VPP, 0):
            errors.append("本方案只支持 Y 方向 5 kHz / 50 mVpp 和 MFLI Demod0")
        if not self.run_tag.strip() or any(c in self.run_tag for c in '/\\:*?"<>|\x00'):
            errors.append("RUN_TAG 必须是非空且可作目录名的标签")
        if any(not math.isfinite(v) for v in (self.x_bias_v, self.y_bias_v, self.main_field_current_ma,
                self.z_peak_current_ma, self.z_pulse_width_us, self.z_pulse_edge_us, self.amplifier_gain_db,
                self.sense_resistor_ohm, self.damping_resistor_ohm, self.coil_resistance_ohm, self.resistor_rated_w,
                self.demod0_rate_sa_s, self.demod0_tc_s, self.demod0_signal_range_v, self.local_span_deg,
                self.pm_tone_duration_s, self.static_point_duration_s, self.noise_duration_s,
                self.welch_segment_s, self.temp_switch_on_lag_s,
                *((self.z_tail_s_f, self.z_tail_tau_e_us) if self.z_tail_comp_enabled else ()),
                *((self.target_temperature_c,) if self.set_tec_target else ()))):
            errors.append("实际使用的标量参数必须有限")
        fm = self.pm_frequencies_hz
        if (not 2 <= len(fm) <= 12 or any(not math.isfinite(f) or not 1 <= f <= 200 for f in fm)
                or any(a >= b for a, b in zip(fm, fm[1:]))):
            errors.append("PM_FREQUENCIES_HZ 须含 2–12 个严格递增的 1–200 Hz 频点")
        elif self.pm_tone_duration_s * fm[0] < 10 or self.demod0_rate_sa_s < 10 * max(fm):
            errors.append("基带 MFLI 记录至少覆盖最低频点 10 个周期，采样率至少为最高频点的 10 倍")
        if self.system_settle_s < 10 * self.demod0_order * self.demod0_tc_s:
            errors.append("SYSTEM_SETTLE_S 必须覆盖至少 10 × 滤波阶数 × 时间常数")
        if self.noise_duration_s < 3 * self.welch_segment_s:
            errors.append("NOISE_DURATION_S 至少为 WELCH_SEGMENT_S 的 3 倍")
        if math.isfinite(self.amplifier_gain_db) and not math.isclose(
                self.amplifier_gain_db * 2, round(self.amplifier_gain_db * 2), abs_tol=1e-9):
            errors.append("1200B 增益须为 0.5 dB 的整数倍")
        return errors

    @classmethod
    def derive_external(cls, values):
        p = cls.from_external(values)
        return {} if p.validate_model() else dict(
            z_pulse_vpp=p.pulse_vpp, pulse_period_us=p.pulse_period_us,
            pm_depth_deg_peak=PM_DEPTH_DEG, pm_sweep_order_hz=p.sweep_order_hz,
            welch_resolution_hz=1 / p.welch_segment_s)
