"""Mx Z 最优控制 XY 平衡场 RF 相位响应参数。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ...common import find_project_root, validate_safety_limit
from ...current_feedback import load_corrected_control_waveform
from ...experiment_params import parameter
from ...control_sources import (
    build_applied_control,
    load_theory_control,
    load_z_calibration,
)
from ..mx_z_optimal_control_rf_sensitivity.phase import paired_phase_order
from ..mx_z_optimal_control_xyz_balance.models import (
    MxZOptimalControlXYZBalanceParams,
)


@dataclass(slots=True)
class MxZOptimalControlXYRFPhaseResponseParams(
    MxZOptimalControlXYZBalanceParams
):
    """固定主场并叠加 Z 最优控制，扫描 X/Y DC 后测量 RF 相位响应。"""

    schema_version = 1

    # XYZ 平衡父类中的 Z 扫描字段在本实验中只保留兼容性，不展示给 GUI。
    z_field_start_ma: float = parameter(
        default=0.0,
        external_name="Z_FIELD_START_MA",
        label="未使用 Z 扫描起点",
        unit="mA",
        visible=False,
    )
    z_field_stop_ma: float = parameter(
        default=0.0,
        external_name="Z_FIELD_STOP_MA",
        label="未使用 Z 扫描终点",
        unit="mA",
        visible=False,
    )
    z_field_points: int = parameter(
        default=1,
        external_name="Z_FIELD_POINTS",
        label="未使用 Z 扫描点数",
        visible=False,
        minimum=1,
    )
    demod_frequency_hz: float = parameter(
        default=12000.0,
        external_name="DEMOD_FREQUENCY_HZ",
        label="未使用独立 Demod 频率",
        unit="Hz",
        visible=False,
    )

    main_magnetic_field_ma: float = parameter(
        default=0.0,
        external_name="FIXED_PARAMS.main_magnetic_field",
        label="GS200 主磁场电流",
        unit="mA",
        group="basic",
        safety_key="main_magnetic_field",
    )
    y_rf_frequency_hz: float = parameter(
        default=12000.0,
        external_name="Y_RF_FREQUENCY_HZ",
        label="Y RF / HF2 固定频率",
        unit="Hz",
        group="basic",
        minimum=0.001,
    )
    phase_cal_rf_amplitude_vpp: float = parameter(
        default=0.01,
        external_name="PHASE_CAL_RF_AMPLITUDE_VPP",
        label="RF 相位扫描幅度",
        unit="Vpp",
        group="basic",
        minimum=0.001,
        maximum=2.0,
        safety_key="rf_coil",
    )
    response_settle_time_s: float = parameter(
        default=0.1,
        external_name="RESPONSE_SETTLE_TIME_S",
        label="相位点稳定时间",
        unit="s",
        group="basic",
        minimum=0.0,
    )
    response_duration_s: float = parameter(
        default=0.2,
        external_name="RESPONSE_DURATION_S",
        label="相位点采样时间",
        unit="s",
        group="basic",
        minimum=0.001,
    )
    r_bad_point_std_threshold_v: float = parameter(
        default=0.1,
        external_name="R_BAD_POINT_STD_THRESHOLD_V",
        label="R 点标准差拒绝门槛",
        unit="V",
        group="advanced",
        minimum=0.0,
    )
    r_point_max_attempts: int = parameter(
        default=3,
        external_name="R_POINT_MAX_ATTEMPTS",
        label="R 点最大采集次数",
        group="advanced",
        minimum=1,
    )
    phase_scan_start_deg: float = parameter(
        default=0.0,
        external_name="PHASE_SCAN_START_DEG",
        label="RF 相位扫描起点",
        unit="deg",
        group="basic",
        minimum=0.0,
        maximum=360.0,
    )
    phase_scan_stop_deg: float = parameter(
        default=340.0,
        external_name="PHASE_SCAN_STOP_DEG",
        label="RF 相位扫描终点",
        unit="deg",
        group="basic",
        minimum=0.0,
        maximum=360.0,
    )
    phase_scan_step_deg: float = parameter(
        default=20.0,
        external_name="PHASE_SCAN_STEP_DEG",
        label="RF 相位扫描步进",
        unit="deg",
        group="basic",
        minimum=0.001,
        maximum=360.0,
    )
    phase_fit_r_squared_min: float = parameter(
        default=0.1,
        external_name="PHASE_FIT_R_SQUARED_MIN",
        label="相位拟合最低 R²",
        group="advanced",
        minimum=0.0,
        maximum=1.0,
    )
    phase_fit_amplitude_sigma_min: float = parameter(
        default=3.0,
        external_name="PHASE_FIT_AMPLITUDE_SIGMA_MIN",
        label="相位拟合幅度显著性",
        unit="σ",
        group="advanced",
        minimum=0.0,
    )
    phase_outlier_sigma_threshold: float = parameter(
        default=6.0,
        external_name="PHASE_OUTLIER_SIGMA_THRESHOLD",
        label="跨相位异常残差门槛",
        unit="robust σ",
        group="advanced",
        minimum=3.0,
    )
    phase_outlier_max_reacquire_points: int = parameter(
        default=4,
        external_name="PHASE_OUTLIER_MAX_REACQUIRE_POINTS",
        label="单轮最多异常重采点数",
        group="advanced",
        minimum=1,
    )
    control_waveform_source: str = parameter(
        default="corrected_run",
        external_name="CONTROL_WAVEFORM_SOURCE",
        label="控制波形来源",
        group="basic",
        options=(("theory", "理论换算"), ("corrected_run", "闭环冻结波形")),
    )
    corrected_control_source_run: str = parameter(
        default="0824_094157_z_aw_closed_loop_waveform_correction",
        external_name="CORRECTED_CONTROL_SOURCE_RUN",
        label="闭环校正运行",
        group="basic",
        description="CONTROL_WAVEFORM_SOURCE=corrected_run 时使用。",
    )

    def phase_axis_deg(self) -> np.ndarray:
        intervals = (self.phase_scan_stop_deg - self.phase_scan_start_deg) / self.phase_scan_step_deg
        return self.phase_scan_start_deg + self.phase_scan_step_deg * np.arange(
            int(round(intervals)) + 1,
            dtype=float,
        )

    @staticmethod
    def _validate_axis(name: str, start: float, stop: float, points: int) -> list[str]:
        if points == 1:
            return [] if np.isclose(start, stop) else [f"{name} 点数为 1 时必须满足 START=STOP"]
        if start >= stop:
            return [f"{name} 点数大于 1 时必须满足 START<STOP"]
        return []

    def validate_model(self) -> list[str]:
        errors: list[str] = []
        if not self.run_tag.strip():
            errors.append("RUN_TAG 不能为空")
        errors.extend(self._validate_axis("X", self.x_field_start_v, self.x_field_stop_v, self.x_field_points))
        errors.extend(self._validate_axis("Y", self.y_field_start_v, self.y_field_stop_v, self.y_field_points))
        for key, values in (
            ("X_magnetic_field", (self.x_field_start_v, self.x_field_stop_v)),
            ("Y_magnetic_field", (self.y_field_start_v, self.y_field_stop_v)),
            (
                "main_magnetic_field",
                (self.main_magnetic_field_ma, self.main_magnetic_field_ma),
            ),
            ("rf_coil", (self.phase_cal_rf_amplitude_vpp,)),
        ):
            for value in values:
                try:
                    validate_safety_limit(key, float(value))
                except ValueError as exc:
                    errors.append(str(exc))
        if self.response_rate_sa_s * self.response_duration_s < 8:
            errors.append("相位点预计采样数不足 8")
        if self.phase_outlier_sigma_threshold < 3.0:
            errors.append("跨相位异常残差门槛不能低于 3σ")
        if self.phase_outlier_max_reacquire_points < 1:
            errors.append("单轮最多异常重采点数必须至少为 1")
        if self.phase_scan_step_deg <= 0:
            errors.append("相位扫描步进必须大于 0")
        elif self.phase_scan_start_deg >= self.phase_scan_stop_deg:
            errors.append("相位扫描必须满足起始相位 < 终止相位")
        else:
            intervals = (self.phase_scan_stop_deg - self.phase_scan_start_deg) / self.phase_scan_step_deg
            if not np.isclose(intervals, round(intervals), atol=1e-10):
                errors.append("相位扫描范围必须被相位步进整除")
            else:
                points = int(round(intervals)) + 1
                phase_axis = self.phase_axis_deg()
                wrapped = np.mod(phase_axis, 360.0)
                if np.unique(np.round(wrapped, decimals=9)).size != points:
                    errors.append("RF 相位扫描首尾或相位点不能重复")
                else:
                    try:
                        paired_phase_order(phase_axis)
                    except ValueError as exc:
                        errors.append(f"RF 相位扫描不满足 180° 配对规则: {exc}")
                if points < 6 or points % 2 or not np.isclose(points * self.phase_scan_step_deg, 360.0, atol=1e-10):
                    errors.append("RF 相位扫描必须覆盖恰好一个 360° 周期且包含偶数个点")
        for offset in (self.y_field_start_v, self.y_field_stop_v):
            for value in (
                offset - self.phase_cal_rf_amplitude_vpp / 2.0,
                offset + self.phase_cal_rf_amplitude_vpp / 2.0,
            ):
                try:
                    validate_safety_limit("Y_magnetic_field", float(value))
                except ValueError as exc:
                    errors.append(f"Y RF 输出包络: {exc}")
        trigger_low = self.trigger_offset_v - self.trigger_amplitude_vpp / 2.0
        trigger_high = self.trigger_offset_v + self.trigger_amplitude_vpp / 2.0
        for value in (trigger_low, trigger_high):
            try:
                validate_safety_limit("Time_sequence_2", float(value))
            except ValueError as exc:
                errors.append(str(exc))
        root = find_project_root()
        try:
            if self.control_waveform_source == "corrected_run":
                corrected = load_corrected_control_waveform(root, self.corrected_control_source_run)
                for value in (float(np.min(corrected.voltage_v)), float(np.max(corrected.voltage_v)), corrected.offset_v - corrected.amplitude_vpp / 2.0, corrected.offset_v + corrected.amplitude_vpp / 2.0):
                    validate_safety_limit("Z_magnetic_field", value)
            else:
                theory = load_theory_control(Path(self.control_results_root), self.control_version)
                calibration = load_z_calibration(root, self.z_calibration_source_run)
                build_applied_control(theory, calibration, self.control_scale, output_vpp=self.z_aw_output_vpp, output_offset_v=self.z_aw_output_offset_v)
        except (OSError, TypeError, ValueError, KeyError) as exc:
            errors.append(str(exc))
        return errors
