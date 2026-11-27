"""标准 Mx（不打脉冲）射频相位图与噪声路径实验的强类型参数。

标准 Mx 构型：MFLI Out1 连续 5 kHz 载波、MF-MOD 只开载波（PM 边带全程关闭）、
Z 双脉冲链关闭并回读、GS200 常量主场、读出锁 Demod 4 静态配置。

主磁场电流只由主磁场标定式反解，不提供手填电流：

    I_mA = (Y_RF_FREQ_HZ - CAL_B_HZ) / CAL_K_HZ_PER_MA

CAL_K_HZ_PER_MA 与 CAL_B_HZ 是界面参数，反算值在使用前按 main_magnetic_field
安全限值校验。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import ClassVar

from ...experiment_params import ExperimentParams, parameter
from ..z_chain_common.rf_phase import MFLI_MAX_SIGOUT_V

# 统一 Demod n ↔ Osc n：载波由 Demod 1 / Osc 1 产生，上下边带用 Osc 2 / Osc 3（标准 Mx 全程关闭），
# 原子读出固定 Demod 4 / Osc 4。索引均为 0 起。
MOD_CARRIER_OSC_IDX = 0
MOD_SIDEBAND_OSC_IDXS = (1, 2)
READOUT_OSC_IDX = 3
READOUT_DEMOD_IDX = 3

CARRIER_HZ = 5000.0
CARRIER_VPP = 0.5
# 主磁场标定：11-27 实测 K_f = 11089.0 ± 9.8 Hz/mA（R² = 0.999996）、f0 = 219.7 Hz。
CAL_K_HZ_PER_MA = 11089.0
CAL_B_HZ = 219.7
# 整周相位网格步长（度）：360 / 步长必须取整。
PHASE_GRID_STEP_DEG = 15.0
# 工作点局部细扫半宽与步长（度）：量 |dy/dphi|，与圆模型 R|cos phi| 互校。
FINE_SPAN_DEG = 5.0
FINE_STEP_DEG = 1.0


@dataclass(slots=True)
class MxStandardRfPhaseSensitivityParams(ExperimentParams):
    schema_version: ClassVar[int] = 1
    system_settle_legacy_defaults: ClassVar[dict[str, float]] = {}
    # 本实验从无旧温度键迁移；保留声明供共享采集契约核对。
    removed_temperature_keys: ClassVar[tuple[str, ...]] = ()
    # 本实验的 Z 双脉冲链整体关闭：1200B 功放已物理旁路，DG 直驱 Z 通道且输出保持 OFF。
    z_drive_amplifier_enabled: ClassVar[bool] = False
    # 公共 Z 链协议字段；标准 Mx 不打脉冲，这些值只为共享步骤的结构兼容，不参与写入。
    amplifier_write_settings: ClassVar[bool] = False
    y_rf_phase_deg: ClassVar[float] = 0.0

    run_tag: str = parameter(default="mx_std_phase_noise", external_name="RUN_TAG", label="运行标签", group="basic")
    y_rf_freq_hz: float = parameter(
        default=CARRIER_HZ, external_name="Y_RF_FREQ_HZ", label="载波频率", unit="Hz",
        group="basic", minimum=1000.0, maximum=20000.0,
        description="MFLI Out1（MF-MOD 载波）连续输出的射频频率，同时决定 GS200 主场电流。")
    y_rf_vpp: float = parameter(
        default=CARRIER_VPP, external_name="Y_RF_VPP", label="载波幅度", unit="Vpp",
        group="basic", minimum=0.001, maximum=20.0,
        description="按峰值 Vpk = Vpp / 2 写入 MF-MOD 载波幅度；叠加偏置后须落在 MFLI Signal Output 1 "
                    "±10 V 安全包络内。")
    x_bias_v: float = parameter(default=0.0, external_name="X_BIAS_V", label="X 静态偏置", unit="V",
                                group="basic", minimum=-10.0, maximum=10.0)
    y_bias_v: float = parameter(
        default=0.0, external_name="Y_BIAS_V", label="载波直流偏置", unit="V", group="basic",
        minimum=-10.0, maximum=10.0,
        description="叠加在 MFLI Signal Output 1 的直流偏置（sigouts/0/offset）。")
    cal_k_hz_per_ma: float = parameter(
        default=CAL_K_HZ_PER_MA, external_name="CAL_K_HZ_PER_MA", label="主磁场标定斜率 K",
        unit="Hz/mA", group="basic", minimum=1.0, maximum=1.0e6,
        description="主磁场标定式 f = K·I + b 的斜率；GS200 电流只由该式反算，不提供手填电流。")
    cal_b_hz: float = parameter(
        default=CAL_B_HZ, external_name="CAL_B_HZ", label="主磁场标定截距 b", unit="Hz",
        group="basic", minimum=-1.0e6, maximum=1.0e6,
        description="主磁场标定式的截距；K、b 变化会改变反算电流，运行前按安全限值校验。")
    phase_grid_step_deg: float = parameter(
        default=PHASE_GRID_STEP_DEG, external_name="PHASE_GRID_STEP_DEG", label="相位网格步长",
        unit="°", group="basic", minimum=5.0, maximum=60.0,
        description="整周扫描步长；360 / 步长必须取整且至少 6 点（默认 15° 对应 24 点），最后一个点不重复首点。")
    point_duration_s: float = parameter(
        default=0.5, external_name="POINT_DURATION_S", label="单点采集时长", unit="s",
        group="basic", minimum=0.05, maximum=1.0,
        description="每个相图点、细扫点与稳定等待读数的 XY 采集时长。")
    noise_segments: int = parameter(
        default=4, external_name="NOISE_SEGMENTS", label="噪声分段数", group="basic",
        minimum=1, maximum=60,
        description="A05 独立温控窗口的噪声段数；每段恢复温控并等待后采集下一段。")
    noise_duration_s: float = parameter(
        default=4.0, external_name="NOISE_DURATION_S", label="单段噪声时长", unit="s",
        group="basic", minimum=1.0, maximum=60.0,
        description="每段关闭温控窗口内的连续采集时长；不得短于 Welch 段长。")
    welch_segment_s: float = parameter(
        default=4.0, external_name="WELCH_SEGMENT_S", label="Welch 段长", unit="s",
        group="basic", minimum=1.0, maximum=10.0)
    demod_idx: int = parameter(
        default=READOUT_DEMOD_IDX, external_name="DEMOD_IDX", label="MFLI 读出解调器索引",
        read_only=True,
        description="标准 Mx 载波占用 MF-MOD，原子读出固定 Demod 4（索引 3）与 Osc 4。")
    demod_signal_range_v: float = parameter(
        default=3.0, external_name="DEMOD_SIGNAL_RANGE", label="MFLI 输入量程", unit="V",
        minimum=0.001, maximum=3.0)
    demod_order: int = parameter(default=4, external_name="DEMOD_ORDER", label="MFLI 滤波阶数",
                                 minimum=1, maximum=8)
    demod_tc_s: float = parameter(
        default=0.001, external_name="DEMOD_TC", label="读出时间常数", unit="s",
        minimum=1.0e-6, maximum=0.1,
        description="分辨率带宽由该时间常数决定；标准 Mx 噪声全程使用同一静态配置，不切高带宽。")
    demod_rate_sa_s: float = parameter(
        default=5000.0, external_name="DEMOD_RATE", label="读出传输率", unit="Sa/s",
        minimum=1000.0, maximum=20000.0,
        description="Demod 4 连续流传输率；相图、细扫与噪声共用同一采样率。")
    system_settle_s: float = parameter(
        default=0.5, external_name="SYSTEM_SETTLE_S", label="系统稳定等待", unit="s",
        group="basic", minimum=0.1, maximum=4.0)
    temp_switch_on_lag_s: float = parameter(
        default=2.0, external_name="TEMP_SWITCH_ON_LAG_S", label="温控恢复等待", unit="s",
        minimum=0.1, maximum=30.0,
        description="每次恢复温控开关（5 V DC + ON）后的固定等待，之后才允许写盘或采下一点。")
    set_tec_target: bool = parameter(default=False, external_name="SET_TEC_TARGET", label="下发 TEC 目标温度")
    target_temperature_c: float = parameter(
        default=90.0, external_name="TARGET_TEMPERATURE_C", label="TEC 目标温度", unit="°C",
        minimum=20.0, maximum=130.0, active_when="SET_TEC_TARGET",
        description="只下发目标温度；不启停 TEC、不判稳，串口不可用时跳过。")

    @property
    def y_carrier_vpk(self) -> float:
        """MF-MOD 载波幅度（Vpk）。"""
        return self.y_rf_vpp / 2.0

    @property
    def y_pm_depth_rad(self) -> float:
        """标准 Mx 全程关闭 PM 边带，调相深度恒为 0。"""
        return 0.0

    @property
    def carrier_osc_idx(self) -> int:
        return MOD_CARRIER_OSC_IDX

    @property
    def sideband_osc_idxs(self) -> tuple[int, int]:
        return MOD_SIDEBAND_OSC_IDXS

    @property
    def readout_osc_idx(self) -> int:
        return READOUT_OSC_IDX

    def y_output_envelope(self, *, pm_enabled: bool = False) -> tuple[float, float]:
        """MFLI Signal Output 1 的理论电压包络 (min_v, max_v)。

        标准 Mx 不开边带，pm_enabled 只为共享量程选择接口保留同一签名。
        """
        depth = self.y_pm_depth_rad if pm_enabled else 0.0
        half_span = (1.0 + depth) * self.y_carrier_vpk
        return float(self.y_bias_v - half_span), float(self.y_bias_v + half_span)

    @property
    def main_field_current_ma(self) -> float:
        """由主磁场标定式反算的 GS200 电流（mA）：唯一路径，不接受手填覆盖。"""
        for name, value in (("载波频率", self.y_rf_freq_hz), ("标定斜率 K", self.cal_k_hz_per_ma),
                            ("标定截距 b", self.cal_b_hz)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{name} 必须是有限数值：{value!r}")
        if self.cal_k_hz_per_ma <= 0:
            raise ValueError(f"标定斜率 K 必须为正：{self.cal_k_hz_per_ma}")
        return (self.y_rf_freq_hz - self.cal_b_hz) / self.cal_k_hz_per_ma

    @property
    def phase_grid_deg(self) -> tuple[float, ...]:
        """整周相位网格（度）：点数 = 360 / 步长，从 0° 起，不重复 360°。"""
        return phase_grid(self.phase_grid_step_deg)

    def validate_model(self) -> list[str]:
        errors = [] if self.run_tag.strip() else ["RUN_TAG 不能为空"]
        if self.demod_idx != READOUT_DEMOD_IDX:
            errors.append("标准 Mx 载波占用 MF-MOD，原子读出须使用 Demod 4（索引 3）")
        try:
            count = 360.0 / float(self.phase_grid_step_deg)
        except ZeroDivisionError:
            errors.append("PHASE_GRID_STEP_DEG 不能为 0")
        else:
            if not math.isfinite(count) or abs(count - round(count)) > 1e-9:
                errors.append(f"PHASE_GRID_STEP_DEG={self.phase_grid_step_deg:g} 不能整除 360°")
            elif round(count) < 6:
                errors.append("相位网格点数过少（少于 6 点），无法做圆拟合与段差分")
        try:
            current_ma = self.main_field_current_ma
        except ValueError as exc:
            errors.append(str(exc))
        else:
            if not math.isfinite(current_ma):
                errors.append("反算的 GS200 电流不是有限值")
        y_min, y_max = self.y_output_envelope()
        if max(abs(y_min), abs(y_max)) > MFLI_MAX_SIGOUT_V + 1e-9:
            errors.append(f"载波与偏置包络 {y_min:g}～{y_max:g} V 超出 MFLI Signal Output 1 "
                          f"±{MFLI_MAX_SIGOUT_V:g} V 安全范围")
        if self.welch_segment_s > self.noise_duration_s + 1e-9:
            errors.append("WELCH_SEGMENT_S 不得大于 NOISE_DURATION_S")
        samples = self.demod_rate_sa_s * self.point_duration_s
        if samples < 256:
            errors.append(f"单点样本数 {samples:.0f} 过少（至少 256），请提高 DEMOD_RATE 或 POINT_DURATION_S")
        return errors

    @classmethod
    def derive_external(cls, values):
        """表单派生预览：由 K、b 反算的 GS200 电流与噪声统计量；校验不通过时不给值。"""
        params = cls.from_external(values)
        if params.validate_model():
            return {}
        return dict(
            main_field_current_ma=params.main_field_current_ma,
            y_rf_vpk=params.y_carrier_vpk,
            phase_point_count=len(params.phase_grid_deg),
            welch_resolution_hz=(1.0 / params.welch_segment_s if params.welch_segment_s > 0 else float("nan")),
            total_noise_duration_s=params.noise_segments * params.noise_duration_s,
        )


def phase_grid(step_deg: float) -> tuple[float, ...]:
    """按步长生成整周网格；步长不能整除 360° 时抛出。"""
    step = float(step_deg)
    if not math.isfinite(step) or step <= 0:
        raise ValueError(f"相位网格步长必须是有限正数：{step_deg!r}")
    count = 360.0 / step
    if abs(count - round(count)) > 1e-9:
        raise ValueError(f"相位网格步长 {step:g}° 不能整除 360°")
    return tuple(index * step for index in range(int(round(count))))
