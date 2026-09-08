"""Mx Z 最优控制 RF 灵敏度实验参数。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ...common import find_project_root, validate_safety_limit
from ...current_feedback import load_corrected_control_waveform
from ...experiment_params import parameter
from ..mx_y_rf_sensitivity.models import MxYRFParams
from .sources import (
    build_applied_control,
    load_theory_control,
    load_z_calibration,
)


@dataclass(slots=True)
class MxZOptimalControlRFParams(MxYRFParams):
    """配置可选 GS200 主场，并叠加 Z 周期控制测量 Y RF 灵敏度。"""

    schema_version = 4

    noise_rf_enabled: bool = parameter(
        default=False,
        external_name="NOISE_RF_ENABLED",
        label="噪声测量时开启 RF",
        group="basic",
        description="仅作用于 RF 灵敏度模式的噪声采集；沿用 Y RF 频率和校准相位。",
    )
    noise_rf_amplitude_vpp: float = parameter(
        default=0.002,
        external_name="NOISE_RF_AMPLITUDE_VPP",
        label="噪声测量 RF 幅值",
        unit="Vpp",
        group="basic",
        minimum=0.0,
        maximum=2.0,
        safety_key="rf_coil",
        description="开启噪声 RF 时使用的峰峰值；设为 0 时仅保留 Y DC 补偿。",
    )

    run_tag: str = parameter(
        default="mx_z_optimal_control_rf",
        external_name="RUN_TAG",
        label="运行标签",
        group="basic",
    )
    linewidth_mode: str = parameter(
        default="amplitude_equivalent",
        external_name="LINEWIDTH_MODE",
        label="固定幅度等效线宽模式",
        visible=False,
    )
    y_rf_frequency_hz: float = parameter(
        default=12000.0,
        external_name="Y_RF_FREQUENCY_HZ",
        label="Y RF / HF2 固定频率",
        unit="Hz",
        group="basic",
        minimum=0.001,
    )
    y_rf_amp_start_vpp: float = parameter(
        default=-0.1,
        external_name="Y_RF_AMP_START_VPP",
        label="Y RF 起始带符号幅度",
        unit="Vpp",
        group="basic",
        minimum=-2.0,
        maximum=2.0,
    )
    y_rf_amp_stop_vpp: float = parameter(
        default=0.1,
        external_name="Y_RF_AMP_STOP_VPP",
        label="Y RF 终止带符号幅度",
        unit="Vpp",
        group="basic",
        minimum=-2.0,
        maximum=2.0,
    )
    y_rf_amp_points: int = parameter(
        default=21,
        external_name="Y_RF_AMP_POINTS",
        label="Y RF 幅度点数",
        group="basic",
        minimum=5,
    )
    response_settle_time_s: float = parameter(
        default=0.1,
        external_name="RESPONSE_SETTLE_TIME_S",
        label="响应/校相点稳定时间",
        unit="s",
        group="basic",
        minimum=0,
    )
    response_duration_s: float = parameter(
        default=0.2,
        external_name="RESPONSE_DURATION_S",
        label="响应/校相点采样时间",
        unit="s",
        group="basic",
        minimum=0.001,
    )
    y_rf_nt_per_vpp: float = parameter(
        default=1517.79147,
        external_name="Y_RF_NT_PER_VPP",
        label="Y RF 线圈标定系数",
        unit="nT/Vpp",
        group="basic",
        minimum=0,
    )
    noise_n_avg: int = parameter(
        default=5,
        external_name="NOISE_N_AVG",
        label="噪声平均次数",
        group="basic",
        minimum=1,
    )
    pump_laser_power_v: float = parameter(
        default=0.5,
        external_name="FIXED_PARAMS.Pump_laser_power",
        label="Pump 光功率",
        unit="V",
        group="basic",
        safety_key="Pump_laser_power",
    )
    probe_laser_power_v: float = parameter(
        default=0.3,
        external_name="FIXED_PARAMS.Probe_laser_power",
        label="Probe 光功率",
        unit="V",
        group="basic",
        safety_key="Probe_laser_power",
    )
    main_magnetic_field_ma: float = parameter(
        default=0.0,
        external_name="FIXED_PARAMS.main_magnetic_field",
        label="GS200 主磁场电流",
        unit="mA",
        group="basic",
        safety_key="main_magnetic_field",
        description="设为 0 mA 时归零并关闭输出；非零时设定电流并开启输出。",
    )
    x_dc_field_v: float = parameter(
        default=-0.01,
        external_name="FIXED_PARAMS.X_magnetic_field",
        label="X 方向 DC 补偿场",
        unit="V",
        group="basic",
        safety_key="X_magnetic_field",
        description="由 X_magnetic_field 通道输出；设为 0 V 时关闭输出。",
    )
    y_rf_offset_v: float = parameter(
        default=0.008,
        external_name="FIXED_PARAMS.Y_magnetic_field",
        label="Y RF DC 补偿偏置",
        unit="V",
        group="basic",
        safety_key="Y_magnetic_field",
        description=(
            "作为 rf_coil 正弦/Burst 的 DC offset 叠加；"
            "Y RF 幅度为 0 时仍保持该 DC 补偿场。"
        ),
    )
    temperature_c: float = parameter(
        default=120.0,
        external_name="FIXED_PARAMS.temperature",
        label="气室温度",
        unit="°C",
        group="basic",
        safety_key="temperature",
    )

    frequency_start_hz: float = parameter(
        default=6000.0,
        external_name="FREQUENCY_START_HZ",
        label="未使用频扫起点",
        visible=False,
    )
    frequency_stop_hz: float = parameter(
        default=14000.0,
        external_name="FREQUENCY_STOP_HZ",
        label="未使用频扫终点",
        visible=False,
    )
    frequency_points: int = parameter(
        default=101,
        external_name="FREQUENCY_POINTS",
        label="未使用频扫点数",
        visible=False,
    )
    frequency_rf_amplitude_vpp: float = parameter(
        default=0.05,
        external_name="FREQUENCY_RF_AMPLITUDE_VPP",
        label="未使用频扫幅度",
        visible=False,
        safety_key="rf_coil",
    )
    frequency_settle_time_s: float = parameter(
        default=0.05,
        external_name="FREQUENCY_SETTLE_TIME_S",
        label="未使用频扫稳定时间",
        visible=False,
    )
    frequency_duration_s: float = parameter(
        default=0.1,
        external_name="FREQUENCY_DURATION_S",
        label="未使用频扫采样时间",
        visible=False,
    )

    control_version: str = parameter(
        default="v1",
        external_name="CONTROL_VERSION",
        label="最优控制版本",
        group="basic",
        description="按 vN 解析同版本的波形与理论参数文件。",
    )
    control_waveform_source: str = parameter(
        default="theory",
        external_name="CONTROL_WAVEFORM_SOURCE",
        label="控制波形来源",
        group="basic",
        options=(("theory", "理论换算"), ("corrected_run", "闭环冻结波形")),
    )
    corrected_control_source_run: str = parameter(
        default="",
        external_name="CORRECTED_CONTROL_SOURCE_RUN",
        label="闭环校正运行",
        group="basic",
        description="CONTROL_WAVEFORM_SOURCE=corrected_run 时必须填写。",
    )
    control_results_root: str = parameter(
        default=r"D:\Code\theory_agent\simulate\results\oc_sens",
        external_name="CONTROL_RESULTS_ROOT",
        label="最优控制结果根目录",
        group="advanced",
    )
    z_calibration_source_run: str = parameter(
        default="0728_161259_mx_z_cal",
        external_name="Z_CALIBRATION_SOURCE_RUN",
        label="Z 标定来源运行",
        group="basic",
    )
    control_scale: float = parameter(
        default=1.0,
        external_name="CONTROL_SCALE",
        label="控制幅度比例",
        group="basic",
        minimum=0.0,
    )
    z_aw_output_vpp: float = parameter(
        default=4.0,
        external_name="Z_AW_OUTPUT_VPP",
        label="Z 控制固定 AW 输出幅度",
        unit="Vpp",
        group="basic",
        minimum=0.001,
    )
    z_aw_output_offset_v: float = parameter(
        default=0.0,
        external_name="Z_AW_OUTPUT_OFFSET",
        label="Z 控制固定 AW 输出偏置",
        unit="V",
        group="advanced",
    )
    control_burst_phase_deg: float = parameter(
        default=0.0,
        external_name="CONTROL_BURST_PHASE_DEG",
        label="Z 控制固定触发相位",
        unit="deg",
        group="advanced",
        minimum=0.0,
        maximum=360.0,
    )
    phase_cal_rf_amplitude_vpp: float = parameter(
        default=0.05,
        external_name="PHASE_CAL_RF_AMPLITUDE_VPP",
        label="校相 Y RF 幅度",
        unit="Vpp",
        group="basic",
        minimum=0.0,
        maximum=2.0,
        safety_key="rf_coil",
        description=(
            "大于 0 时扫描 Y RF Burst 相位并继续 RF 灵敏度测量；"
            "等于 0 时关闭 RFY 交流分量并保持 Y DC 补偿，"
            "改扫 Z 控制 Burst 相位，扫描后直接结束。"
        ),
    )
    phase_scan_start_deg: float = parameter(
        default=0.0,
        external_name="PHASE_SCAN_START_DEG",
        label="Y RF 校相起始相位",
        unit="deg",
        group="advanced",
        minimum=0.0,
        maximum=360.0,
    )
    phase_scan_stop_deg: float = parameter(
        default=350.0,
        external_name="PHASE_SCAN_STOP_DEG",
        label="Y RF 校相终止相位",
        unit="deg",
        group="advanced",
        minimum=0.0,
        maximum=360.0,
    )
    phase_scan_step_deg: float = parameter(
        default=10.0,
        external_name="PHASE_SCAN_STEP_DEG",
        label="Y RF 校相相位步进",
        unit="deg",
        group="advanced",
        minimum=0.001,
        maximum=360.0,
    )
    phase_fit_r_squared_min: float = parameter(
        default=0.85,
        external_name="PHASE_FIT_R_SQUARED_MIN",
        label="校相拟合最低 R²",
        group="advanced",
        minimum=0.0,
        maximum=1.0,
    )
    phase_fit_amplitude_sigma_min: float = parameter(
        default=3.0,
        external_name="PHASE_FIT_AMPLITUDE_SIGMA_MIN",
        label="校相幅度最低显著性",
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
    trigger_frequency_hz: float = parameter(
        default=100.0,
        external_name="TRIGGER_FREQUENCY_HZ",
        label="共同触发频率",
        unit="Hz",
        group="advanced",
        minimum=0.001,
    )
    trigger_amplitude_vpp: float = parameter(
        default=5.0,
        external_name="TRIGGER_AMPLITUDE_VPP",
        label="共同触发幅度",
        unit="Vpp",
        group="advanced",
        minimum=0.0,
        maximum=20.0,
    )
    trigger_offset_v: float = parameter(
        default=2.5,
        external_name="TRIGGER_OFFSET_V",
        label="共同触发偏置",
        unit="V",
        group="advanced",
        minimum=-10.0,
        maximum=10.0,
    )
    trigger_duty_percent: float = parameter(
        default=50.0,
        external_name="TRIGGER_DUTY_PERCENT",
        label="共同触发占空比",
        unit="%",
        group="advanced",
        minimum=0.1,
        maximum=99.9,
    )

    @classmethod
    def migrate_external(
        cls,
        values: dict[str, object],
        schema_version: int,
    ) -> dict[str, object]:
        """旧配置缺少补偿场时保持原来的 XY 零输出行为。"""
        migrated = super(MxZOptimalControlRFParams, cls).migrate_external(
            values,
            schema_version,
        )
        if schema_version < 3:
            migrated.setdefault("FIXED_PARAMS.X_magnetic_field", 0.0)
            migrated.setdefault("FIXED_PARAMS.Y_magnetic_field", 0.0)
        return migrated

    def phase_axis_deg(self) -> np.ndarray:
        count = int(
            round(
                (self.phase_scan_stop_deg - self.phase_scan_start_deg)
                / self.phase_scan_step_deg
            )
        )
        return self.phase_scan_start_deg + self.phase_scan_step_deg * np.arange(
            count + 1,
            dtype=float,
        )

    def validate_model(self) -> list[str]:
        errors = MxYRFParams.validate_model(self)
        if not np.isfinite(self.noise_rf_amplitude_vpp):
            errors.append("NOISE_RF_AMPLITUDE_VPP 必须是有限数值")
        if self.linewidth_mode != "amplitude_equivalent":
            errors.append("LINEWIDTH_MODE 必须固定为 amplitude_equivalent")
        if self.control_waveform_source not in {"theory", "corrected_run"}:
            errors.append("CONTROL_WAVEFORM_SOURCE 必须是 theory 或 corrected_run")
        if self.control_waveform_source == "theory" and self.control_scale <= 0:
            errors.append("CONTROL_SCALE 必须大于 0")
        if self.phase_scan_start_deg >= self.phase_scan_stop_deg:
            errors.append("相位扫描必须满足起始相位 < 终止相位")
        else:
            intervals = (
                self.phase_scan_stop_deg - self.phase_scan_start_deg
            ) / self.phase_scan_step_deg
            if not np.isclose(intervals, round(intervals), atol=1e-10):
                errors.append("相位扫描范围必须被相位步进整除")
            elif round(intervals) + 1 < 6:
                errors.append("相位扫描至少需要 6 个点")
            elif self.phase_cal_rf_amplitude_vpp > 0.0:
                point_count = int(round(intervals)) + 1
                covered_period = point_count * self.phase_scan_step_deg
                if (
                    point_count % 2
                    or not np.isclose(
                        covered_period,
                        360.0,
                        atol=1e-10,
                    )
                ):
                    errors.append(
                        "Y RF 正交校相必须覆盖恰好一个 360° 周期，"
                        "且不得重复首尾相位"
                    )
        trigger_low = self.trigger_offset_v - self.trigger_amplitude_vpp / 2.0
        trigger_high = self.trigger_offset_v + self.trigger_amplitude_vpp / 2.0
        for value in (trigger_low, trigger_high):
            try:
                validate_safety_limit("Time_sequence_2", value)
            except ValueError as exc:
                errors.append(str(exc))

        maximum_rf_amplitude_vpp = max(
            abs(self.y_rf_amp_start_vpp),
            abs(self.y_rf_amp_stop_vpp),
            self.phase_cal_rf_amplitude_vpp,
            self.noise_rf_amplitude_vpp if self.noise_rf_enabled else 0.0,
        )
        for label, value in (
            (
                "Y RF 输出下限",
                self.y_rf_offset_v - maximum_rf_amplitude_vpp / 2.0,
            ),
            (
                "Y RF 输出上限",
                self.y_rf_offset_v + maximum_rf_amplitude_vpp / 2.0,
            ),
        ):
            try:
                validate_safety_limit("Y_magnetic_field", value)
            except ValueError as exc:
                errors.append(f"{label}: {exc}")

        root = find_project_root()
        try:
            if self.control_waveform_source == "corrected_run":
                corrected = load_corrected_control_waveform(
                    root,
                    self.corrected_control_source_run,
                )
                for value in (
                    float(np.min(corrected.voltage_v)),
                    float(np.max(corrected.voltage_v)),
                    corrected.offset_v - corrected.amplitude_vpp / 2.0,
                    corrected.offset_v + corrected.amplitude_vpp / 2.0,
                ):
                    validate_safety_limit("Z_magnetic_field", value)
            else:
                theory = load_theory_control(
                    Path(self.control_results_root),
                    self.control_version,
                )
                calibration = load_z_calibration(
                    root,
                    self.z_calibration_source_run,
                )
                build_applied_control(
                    theory,
                    calibration,
                    self.control_scale,
                    output_vpp=self.z_aw_output_vpp,
                    output_offset_v=self.z_aw_output_offset_v,
                )
        except (OSError, TypeError, ValueError, KeyError) as exc:
            errors.append(str(exc))
        return errors
