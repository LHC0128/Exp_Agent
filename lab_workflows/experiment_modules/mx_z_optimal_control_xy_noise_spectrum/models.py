"""Mx Z 最优控制 XY 偏置噪声谱参数。"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ...common import validate_safety_limit
from ...experiment_params import parameter
from ..mx_z_optimal_control_rf_sensitivity.models import (
    MxZOptimalControlRFParams,
)


@dataclass(slots=True)
class MxZOptimalControlXYNoiseSpectrumParams(MxZOptimalControlRFParams):
    """在 Z 周期最优控制下扫描 XY DC 偏置并测量 R 噪声谱。"""

    schema_version = 1
    noise_rf_enabled: bool = parameter(
        default=False, external_name="NOISE_RF_ENABLED",
        label="未使用噪声 RF 开关", visible=False,
    )
    noise_rf_amplitude_vpp: float = parameter(
        default=0.002, external_name="NOISE_RF_AMPLITUDE_VPP",
        label="未使用噪声 RF 幅值", visible=False,
    )

    # 噪声实验不需要 RF 响应扫描；保留父类字段以兼容统一参数契约。
    linewidth_mode: str = parameter(default="amplitude_equivalent", external_name="LINEWIDTH_MODE", label="隐藏兼容线宽模式", visible=False)
    y_rf_frequency_hz: float = parameter(default=12000.0, external_name="Y_RF_FREQUENCY_HZ", label="隐藏兼容频率", visible=False)
    y_rf_amp_start_vpp: float = parameter(default=-0.1, external_name="Y_RF_AMP_START_VPP", label="隐藏兼容幅度起点", visible=False)
    y_rf_amp_stop_vpp: float = parameter(default=0.1, external_name="Y_RF_AMP_STOP_VPP", label="隐藏兼容幅度终点", visible=False)
    y_rf_amp_points: int = parameter(default=41, external_name="Y_RF_AMP_POINTS", label="隐藏兼容幅度点数", visible=False)
    response_settle_time_s: float = parameter(default=0.1, external_name="RESPONSE_SETTLE_TIME_S", label="隐藏兼容响应稳定时间", visible=False)
    response_duration_s: float = parameter(default=0.2, external_name="RESPONSE_DURATION_S", label="隐藏兼容响应采样时间", visible=False)
    y_rf_nt_per_vpp: float = parameter(default=0.0, external_name="Y_RF_NT_PER_VPP", label="隐藏兼容 RF 标定系数", visible=False)
    r_bad_point_std_threshold_v: float = parameter(default=0.1, external_name="R_BAD_POINT_STD_THRESHOLD_V", label="隐藏兼容 R 质量阈值", visible=False)
    r_point_max_attempts: int = parameter(default=1, external_name="R_POINT_MAX_ATTEMPTS", label="隐藏兼容 R 重试次数", visible=False)
    frequency_start_hz: float = parameter(default=6000.0, external_name="FREQUENCY_START_HZ", label="隐藏兼容频扫起点", visible=False)
    frequency_stop_hz: float = parameter(default=14000.0, external_name="FREQUENCY_STOP_HZ", label="隐藏兼容频扫终点", visible=False)
    frequency_points: int = parameter(default=101, external_name="FREQUENCY_POINTS", label="隐藏兼容频扫点数", visible=False)
    frequency_rf_amplitude_vpp: float = parameter(default=0.05, external_name="FREQUENCY_RF_AMPLITUDE_VPP", label="隐藏兼容频扫幅度", visible=False)
    frequency_settle_time_s: float = parameter(default=0.05, external_name="FREQUENCY_SETTLE_TIME_S", label="隐藏兼容频扫稳定时间", visible=False)
    frequency_duration_s: float = parameter(default=0.1, external_name="FREQUENCY_DURATION_S", label="隐藏兼容频扫采样时间", visible=False)
    phase_cal_rf_amplitude_vpp: float = parameter(default=0.0, external_name="PHASE_CAL_RF_AMPLITUDE_VPP", label="隐藏兼容校相幅度", visible=False, safety_key="rf_coil")
    phase_scan_start_deg: float = parameter(default=0.0, external_name="PHASE_SCAN_START_DEG", label="隐藏兼容校相起点", visible=False)
    phase_scan_stop_deg: float = parameter(default=350.0, external_name="PHASE_SCAN_STOP_DEG", label="隐藏兼容校相终点", visible=False)
    phase_scan_step_deg: float = parameter(default=10.0, external_name="PHASE_SCAN_STEP_DEG", label="隐藏兼容校相步进", visible=False)
    phase_fit_r_squared_min: float = parameter(default=0.85, external_name="PHASE_FIT_R_SQUARED_MIN", label="隐藏兼容校相 R²", visible=False)
    phase_fit_amplitude_sigma_min: float = parameter(default=3.0, external_name="PHASE_FIT_AMPLITUDE_SIGMA_MIN", label="隐藏兼容校相显著性", visible=False)
    phase_outlier_sigma_threshold: float = parameter(default=6.0, external_name="PHASE_OUTLIER_SIGMA_THRESHOLD", label="隐藏兼容校相异常门槛", visible=False)
    phase_outlier_max_reacquire_points: int = parameter(default=4, external_name="PHASE_OUTLIER_MAX_REACQUIRE_POINTS", label="隐藏兼容校相重采点数", visible=False)

    noise_n_avg: int = parameter(default=5, external_name="NOISE_N_AVG", label="噪声重复次数", group="basic", minimum=1)
    noise_duration_s: float = parameter(default=1.0, external_name="NOISE_DURATION_S", label="单次噪声时长", unit="s", group="basic", minimum=0.01)
    noise_rate_sa_s: float = parameter(default=50000.0, external_name="NOISE_RATE_SA_S", label="噪声请求采样率", unit="Sa/s", group="advanced", minimum=1.0)
    noise_time_constant_s: float = parameter(default=1e-6, external_name="NOISE_TIME_CONSTANT_S", label="噪声时间常数", unit="s", group="advanced", minimum=0.0)
    noise_demod_order: int = parameter(default=4, external_name="NOISE_DEMOD_ORDER", label="噪声解调阶数", group="advanced", minimum=1)
    low_freq_skip_hz: float = parameter(default=3.0, external_name="LOW_FREQ_SKIP_HZ", label="噪声统计频率下限", unit="Hz", group="basic", minimum=0.0)
    noise_band_max_hz: float = parameter(default=200.0, external_name="NOISE_BAND_MAX_HZ", label="噪声统计频率上限", unit="Hz", group="basic", minimum=0.001)

    x_dc_field_v: float = parameter(default=0.0, external_name="FIXED_PARAMS.X_magnetic_field", label="隐藏兼容 X 偏置", visible=False, safety_key="X_magnetic_field")
    y_rf_offset_v: float = parameter(default=0.0, external_name="FIXED_PARAMS.Y_magnetic_field", label="隐藏兼容 Y 偏置", visible=False, safety_key="Y_magnetic_field")
    x_field_start_v: float = parameter(default=0.0, external_name="X_FIELD_START_V", label="X 偏置起点", unit="V", group="basic", safety_key="X_magnetic_field")
    x_field_stop_v: float = parameter(default=0.02, external_name="X_FIELD_STOP_V", label="X 偏置终点", unit="V", group="basic", safety_key="X_magnetic_field")
    x_field_points: int = parameter(default=11, external_name="X_FIELD_POINTS", label="X 偏置点数", group="basic", minimum=1)
    y_field_start_v: float = parameter(default=-0.02, external_name="Y_FIELD_START_V", label="Y 偏置起点", unit="V", group="basic", safety_key="Y_magnetic_field")
    y_field_stop_v: float = parameter(default=0.0, external_name="Y_FIELD_STOP_V", label="Y 偏置终点", unit="V", group="basic", safety_key="Y_magnetic_field")
    y_field_points: int = parameter(default=11, external_name="Y_FIELD_POINTS", label="Y 偏置点数", group="basic", minimum=1)

    control_waveform_source: str = parameter(default="corrected_run", external_name="CONTROL_WAVEFORM_SOURCE", label="控制波形来源", group="basic", options=(("corrected_run", "闭环冻结波形"), ("theory", "理论换算")))
    corrected_control_source_run: str = parameter(default="0824_094157_z_aw_closed_loop_waveform_correction", external_name="CORRECTED_CONTROL_SOURCE_RUN", label="闭环校正运行", group="basic")

    def x_axis(self) -> np.ndarray:
        return np.linspace(self.x_field_start_v, self.x_field_stop_v, self.x_field_points, dtype=float)

    def y_axis(self) -> np.ndarray:
        return np.linspace(self.y_field_start_v, self.y_field_stop_v, self.y_field_points, dtype=float)

    @staticmethod
    def _validate_axis(label: str, start: float, stop: float, points: int) -> list[str]:
        if points < 1:
            return [f"{label} 点数必须至少为 1"]
        if points == 1:
            return [] if np.isclose(start, stop) else [f"{label} 点数为 1 时必须满足 START=STOP"]
        if not start < stop:
            return [f"{label} 点数大于 1 时必须满足 START<STOP"]
        return []

    def validate_model(self) -> list[str]:
        errors = MxZOptimalControlRFParams.validate_model(self)
        errors.extend(self._validate_axis("X 偏置", self.x_field_start_v, self.x_field_stop_v, self.x_field_points))
        errors.extend(self._validate_axis("Y 偏置", self.y_field_start_v, self.y_field_stop_v, self.y_field_points))
        if not self.low_freq_skip_hz < self.noise_band_max_hz:
            errors.append("LOW_FREQ_SKIP_HZ 必须小于 NOISE_BAND_MAX_HZ")
        if self.noise_band_max_hz >= self.noise_rate_sa_s / 2.0:
            errors.append("NOISE_BAND_MAX_HZ 必须低于请求采样率的 Nyquist 频率")
        if self.noise_rate_sa_s * self.noise_duration_s < 8:
            errors.append("噪声记录预计采样数不足 8")
        for key, values in (
            ("X_magnetic_field", self.x_axis()),
            ("Y_magnetic_field", self.y_axis()),
        ):
            for value in values:
                try:
                    validate_safety_limit(key, float(value))
                except ValueError as exc:
                    errors.append(str(exc))
        return errors
