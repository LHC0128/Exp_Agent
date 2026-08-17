"""Mx Y 向 RF 频率响应实验参数。

工作点与 Mx Y RF 灵敏度实验完全一致（主场沿 Z、Pump 沿 Z、Probe 沿 X、
待测 RF 沿 Y），只保留模式 1 的固定幅度扫频 R(f) 采集，并把 RF 幅度
提升为扫描轴：点数等于 1 且起止相等时测单幅度，否则嵌套扫描多个幅度。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ...experiment_params import parameter
from ..mx_y_rf_sensitivity.models import LinewidthMode, MxYRFParams


@dataclass(slots=True)
class MxYRFFrequencyResponseParams(MxYRFParams):
    """复用 Mx Y RF 灵敏度工作点，测量频率响应曲线。"""

    schema_version = 1

    run_tag: str = parameter(
        default="mx_y_rf_freq_resp",
        external_name="RUN_TAG",
        label="运行标签",
        group="basic",
    )
    linewidth_mode: LinewidthMode = parameter(
        default="frequency_sweep",
        external_name="LINEWIDTH_MODE",
        label="线宽模式",
        visible=False,
    )

    rf_amplitude_start_vpp: float = parameter(
        default=0.01,
        external_name="RF_AMPLITUDE_START_VPP",
        label="RF 幅度扫描起点",
        unit="Vpp",
        group="basic",
        minimum=0.0,
        maximum=2.0,
        safety_key="rf_coil",
    )
    rf_amplitude_stop_vpp: float = parameter(
        default=0.01,
        external_name="RF_AMPLITUDE_STOP_VPP",
        label="RF 幅度扫描终点",
        unit="Vpp",
        group="basic",
        minimum=0.0,
        maximum=2.0,
        safety_key="rf_coil",
    )
    rf_amplitude_points: int = parameter(
        default=1,
        external_name="RF_AMPLITUDE_POINTS",
        label="RF 幅度点数",
        group="basic",
        minimum=1,
        description=(
            "等于 1 且起止幅度相等时只测单幅度；否则在起止幅度间线性嵌套扫描。"
        ),
    )

    # 以下父类字段在本实验中不使用，隐藏并保留合法默认值。
    frequency_rf_amplitude_vpp: float = parameter(
        default=0.01,
        external_name="FREQUENCY_RF_AMPLITUDE_VPP",
        label="模式1扫频 RF 幅度",
        unit="Vpp",
        visible=False,
        minimum=0,
        maximum=2.0,
        safety_key="rf_coil",
    )
    y_rf_amp_start_vpp: float = parameter(
        default=-0.2,
        external_name="Y_RF_AMP_START_VPP",
        label="Y RF 起始带符号幅度",
        unit="Vpp",
        visible=False,
        minimum=-2.0,
        maximum=2.0,
    )
    y_rf_amp_stop_vpp: float = parameter(
        default=0.2,
        external_name="Y_RF_AMP_STOP_VPP",
        label="Y RF 终止带符号幅度",
        unit="Vpp",
        visible=False,
        minimum=-2.0,
        maximum=2.0,
    )
    y_rf_amp_points: int = parameter(
        default=81,
        external_name="Y_RF_AMP_POINTS",
        label="Y RF 幅度点数",
        visible=False,
        minimum=5,
    )
    response_settle_time_s: float = parameter(
        default=0.3,
        external_name="RESPONSE_SETTLE_TIME_S",
        label="幅度点稳定时间",
        unit="s",
        visible=False,
        minimum=0,
    )
    response_duration_s: float = parameter(
        default=0.2,
        external_name="RESPONSE_DURATION_S",
        label="幅度点采样时间",
        unit="s",
        visible=False,
        minimum=0.001,
    )
    y_rf_nt_per_vpp: float = parameter(
        default=0.0,
        external_name="Y_RF_NT_PER_VPP",
        label="Y RF 线圈标定系数",
        unit="nT/Vpp",
        visible=False,
        minimum=0,
    )
    noise_n_avg: int = parameter(
        default=10,
        external_name="NOISE_N_AVG",
        label="噪声平均次数",
        visible=False,
        minimum=1,
    )
    noise_duration_s: float = parameter(
        default=1.0,
        external_name="NOISE_DURATION_S",
        label="单次噪声时长",
        unit="s",
        visible=False,
        minimum=0.01,
    )
    noise_rate_sa_s: float = parameter(
        default=50000.0,
        external_name="NOISE_RATE_SA_S",
        label="噪声请求采样率",
        unit="Sa/s",
        visible=False,
        minimum=1,
    )
    noise_time_constant_s: float = parameter(
        default=1e-6,
        external_name="NOISE_TIME_CONSTANT_S",
        label="噪声时间常数",
        unit="s",
        visible=False,
        minimum=0,
    )
    noise_demod_order: int = parameter(
        default=4,
        external_name="NOISE_DEMOD_ORDER",
        label="噪声解调阶数",
        visible=False,
        minimum=1,
    )
    low_freq_skip_hz: float = parameter(
        default=3.0,
        external_name="LOW_FREQ_SKIP_HZ",
        label="平坦段搜索最低频率",
        unit="Hz",
        visible=False,
        minimum=0,
    )
    linear_check_gamma_fraction: float = parameter(
        default=0.25,
        external_name="LINEAR_CHECK_GAMMA_FRACTION",
        label="中心线性检查范围",
        unit="γ",
        visible=False,
        minimum=0.01,
        maximum=1,
    )
    slope_agreement_tolerance: float = parameter(
        default=0.2,
        external_name="SLOPE_AGREEMENT_TOLERANCE",
        label="两种斜率差异告警阈值",
        visible=False,
        minimum=0,
    )

    def validate_model(self) -> list[str]:
        errors = MxYRFParams.validate_model(self)
        if self.linewidth_mode != "frequency_sweep":
            errors.append("LINEWIDTH_MODE 必须固定为 frequency_sweep")
        if self.rf_amplitude_points <= 0:
            errors.append("RF_AMPLITUDE_POINTS 必须为正整数")
        elif self.rf_amplitude_points == 1:
            if not math.isclose(
                self.rf_amplitude_start_vpp,
                self.rf_amplitude_stop_vpp,
                rel_tol=0.0,
                abs_tol=1e-12,
            ):
                errors.append("RF_AMPLITUDE_POINTS 等于 1 时起点幅度必须等于终点幅度")
        elif not self.rf_amplitude_start_vpp < self.rf_amplitude_stop_vpp:
            errors.append("RF_AMPLITUDE_POINTS 大于 1 时起点幅度必须小于终点幅度")
        return errors
