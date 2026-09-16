"""Mx Y 最优控制 RF 频率响应参数。"""
from __future__ import annotations
import math
from dataclasses import dataclass
import numpy as np
from ...common import find_project_root
from ...experiment_params import parameter
from ..mx_z_control_params import MxZControlSourceSelectionParams

@dataclass(slots=True)
class MxYOptimalControlRFFrequencyResponseParams(
    MxZControlSourceSelectionParams
):
    """固定 Y RF 幅度，对比最优控制与常数 Z 控制的 RF 频率响应。"""
    schema_version = 2
    noise_rf_enabled: bool = parameter(
        default=False, external_name="NOISE_RF_ENABLED",
        label="未使用噪声 RF 开关", visible=False,
    )
    noise_rf_amplitude_vpp: float = parameter(
        default=0.002, external_name="NOISE_RF_AMPLITUDE_VPP",
        label="未使用噪声 RF 幅值", visible=False,
    )
    run_tag: str = parameter(default="mx_y_optimal_control_rf_freq_resp", external_name="RUN_TAG", label="运行标签", group="basic")
    linewidth_mode: str = parameter(default="frequency_phase_observation", external_name="LINEWIDTH_MODE", label="观察模式", visible=False)
    y_rf_frequency_hz: float = parameter(default=100.0, external_name="Y_RF_FREQUENCY_HZ", label="Y RF 参考频率", unit="Hz", visible=False, minimum=0.001)
    frequency_start_hz: float = parameter(default=100.0, external_name="FREQUENCY_START_HZ", label="频率扫描起点", unit="Hz", group="basic", minimum=0.001)
    frequency_stop_hz: float = parameter(default=10000.0, external_name="FREQUENCY_STOP_HZ", label="频率扫描终点", unit="Hz", group="basic", minimum=0.001)
    frequency_points: int = parameter(default=100, external_name="FREQUENCY_POINTS", label="频率点数", group="basic", minimum=2)
    frequency_rf_amplitude_vpp: float = parameter(default=0.01, external_name="FREQUENCY_RF_AMPLITUDE_VPP", label="Y RF 固定幅度", unit="Vpp", group="basic", minimum=0.0, maximum=2.0, safety_key="rf_coil")
    frequency_settle_time_s: float = parameter(default=0.05, external_name="FREQUENCY_SETTLE_TIME_S", label="频点稳定时间", unit="s", group="advanced", minimum=0)
    frequency_duration_s: float = parameter(default=0.1, external_name="FREQUENCY_DURATION_S", label="频点采样时间", unit="s", group="advanced", minimum=0.001)
    phase_cal_rf_amplitude_vpp: float = parameter(default=0.01, external_name="PHASE_CAL_RF_AMPLITUDE_VPP", label="相位扫描 RF 幅度", unit="Vpp", visible=False, minimum=0.0, maximum=2.0, safety_key="rf_coil")
    phase_scan_start_deg: float = parameter(default=0.0, external_name="PHASE_SCAN_START_DEG", label="相位起点", unit="deg", group="basic", minimum=0.0, maximum=360.0)
    phase_scan_stop_deg: float = parameter(default=350.0, external_name="PHASE_SCAN_STOP_DEG", label="相位终点", unit="deg", group="basic", minimum=0.0, maximum=360.0)
    phase_scan_step_deg: float = parameter(default=10.0, external_name="PHASE_SCAN_STEP_DEG", label="相位步进", unit="deg", group="basic", minimum=0.001, maximum=360.0)
    corrected_control_source_run: str = parameter(default="obbv6", external_name="CORRECTED_CONTROL_SOURCE_RUN", label="闭环校正运行", group="basic")
    comparison_enabled: bool = parameter(default=False, external_name="COMPARISON_ENABLED", label="启用常数 Z 控制对照", group="basic", description="启用后，最优控制扫描结束后把 Z 通道切为恒定 DC 并再扫描一次 Y RF 频率。")
    constant_control_larmor_frequency_hz: float = parameter(default=0.0, external_name="CONSTANT_CONTROL_LARMOR_FREQUENCY_HZ", label="常数控制目标 Larmor 频率", unit="Hz", group="basic", minimum=0.0, active_when="COMPARISON_ENABLED", description="用来反解恒定 Z 电压的目标共振中心频率；关闭对照时不校验。")
    constant_control_calibration_source_run: str = parameter(default="", external_name="CONSTANT_CONTROL_CALIBRATION_SOURCE_RUN", label="电流耦合标定运行", group="basic", options_from_directory="data/Mx_Z_Current_Coupling_Calibration", options_pattern="*", options_include_directories=True, options_require_analysis="results/analysis.yaml", options_require_experiment_id="mx-z-current-coupling-calibration", active_when="COMPARISON_ENABLED", description="只列出 experiment_id 正确且 success: true 的电流耦合标定运行。")
    control_waveform_source: str = parameter(default="corrected_run", external_name="CONTROL_WAVEFORM_SOURCE", label="控制波形来源", visible=False)
    y_rf_amp_start_vpp: float = parameter(default=-0.1, external_name="Y_RF_AMP_START_VPP", label="未使用幅度扫描起点", visible=False)
    y_rf_amp_stop_vpp: float = parameter(default=0.1, external_name="Y_RF_AMP_STOP_VPP", label="未使用幅度扫描终点", visible=False)
    y_rf_amp_points: int = parameter(default=21, external_name="Y_RF_AMP_POINTS", label="未使用幅度扫描点数", visible=False)
    response_settle_time_s: float = parameter(default=0.1, external_name="RESPONSE_SETTLE_TIME_S", label="未使用响应稳定时间", visible=False)
    response_duration_s: float = parameter(default=0.1, external_name="RESPONSE_DURATION_S", label="未使用响应采样时间", visible=False)
    y_rf_nt_per_vpp: float = parameter(default=0.0, external_name="Y_RF_NT_PER_VPP", label="未使用 RF 标定系数", visible=False)
    noise_n_avg: int = parameter(default=1, external_name="NOISE_N_AVG", label="未使用噪声次数", visible=False)
    noise_duration_s: float = parameter(default=1.0, external_name="NOISE_DURATION_S", label="未使用噪声时长", visible=False)
    noise_rate_sa_s: float = parameter(default=50000.0, external_name="NOISE_RATE_SA_S", label="未使用噪声采样率", visible=False)
    noise_time_constant_s: float = parameter(default=1e-6, external_name="NOISE_TIME_CONSTANT_S", label="未使用噪声时间常数", visible=False)
    noise_demod_order: int = parameter(default=4, external_name="NOISE_DEMOD_ORDER", label="未使用噪声解调阶数", visible=False)
    low_freq_skip_hz: float = parameter(default=3.0, external_name="LOW_FREQ_SKIP_HZ", label="未使用最低频率", visible=False)
    fit_r_squared_min: float = parameter(default=0.0, external_name="FIT_R_SQUARED_MIN", label="未使用拟合门槛", visible=False)
    fit_relative_gamma_uncertainty_max: float = parameter(default=1.0, external_name="FIT_RELATIVE_GAMMA_UNCERTAINTY_MAX", label="未使用线宽不确定度", visible=False)
    linear_check_gamma_fraction: float = parameter(default=0.25, external_name="LINEAR_CHECK_GAMMA_FRACTION", label="未使用线性检查", visible=False)
    slope_agreement_tolerance: float = parameter(default=0.2, external_name="SLOPE_AGREEMENT_TOLERANCE", label="未使用斜率门槛", visible=False)
    control_version: str = parameter(default="v1", external_name="CONTROL_VERSION", label="未使用理论控制版本", visible=False)
    control_results_root: str = parameter(default="", external_name="CONTROL_RESULTS_ROOT", label="未使用理论结果目录", visible=False)
    z_calibration_source_run: str = parameter(default="", external_name="Z_CALIBRATION_SOURCE_RUN", label="未使用 Z 标定运行", visible=False)
    control_scale: float = parameter(default=1.0, external_name="CONTROL_SCALE", label="未使用控制比例", visible=False)
    phase_fit_r_squared_min: float = parameter(default=0.0, external_name="PHASE_FIT_R_SQUARED_MIN", label="观察版不使用相位拟合", visible=False)
    phase_fit_amplitude_sigma_min: float = parameter(default=0.0, external_name="PHASE_FIT_AMPLITUDE_SIGMA_MIN", label="观察版不使用相位拟合", visible=False)
    phase_outlier_sigma_threshold: float = parameter(default=6.0, external_name="PHASE_OUTLIER_SIGMA_THRESHOLD", label="观察版不使用异常拟合", visible=False)
    phase_outlier_max_reacquire_points: int = parameter(default=1, external_name="PHASE_OUTLIER_MAX_REACQUIRE_POINTS", label="观察版不使用异常拟合", visible=False)

    def phase_axis_deg(self) -> np.ndarray:
        count = int(round((self.phase_scan_stop_deg - self.phase_scan_start_deg) / self.phase_scan_step_deg))
        return self.phase_scan_start_deg + self.phase_scan_step_deg * np.arange(count + 1, dtype=float)

    def validate_model(self) -> list[str]:
        errors: list[str] = []
        if not self.run_tag.strip(): errors.append("RUN_TAG 不能为空")
        if self.frequency_start_hz >= self.frequency_stop_hz: errors.append("频率扫描起点必须小于终点")
        if self.phase_scan_start_deg >= self.phase_scan_stop_deg: errors.append("相位扫描起点必须小于终点")
        elif self.phase_scan_step_deg > 0:
            intervals = (self.phase_scan_stop_deg - self.phase_scan_start_deg) / self.phase_scan_step_deg
            if not math.isclose(intervals, round(intervals), abs_tol=1e-10): errors.append("相位扫描范围必须被步进整除")
        if not self.corrected_control_source_run.strip(): errors.append("CORRECTED_CONTROL_SOURCE_RUN 不能为空")
        if self.frequency_duration_s * self.response_rate_sa_s < 8: errors.append("频点采样数不足 8")
        if self.comparison_enabled:
            errors.extend(self._validate_comparison())
        return errors

    def _validate_comparison(self) -> list[str]:
        """启用对照时，反解恒定 Z 电压必须能通过标定校验与安全限值。"""
        from .comparison import build_constant_control_plan

        try:
            build_constant_control_plan(
                find_project_root(),
                calibration_run=self.constant_control_calibration_source_run,
                target_larmor_frequency_hz=self.constant_control_larmor_frequency_hz,
            )
        except (OSError, KeyError, TypeError, ValueError) as exc:
            return [f"常数 Z 控制对照预检失败: {exc}"]
        return []
