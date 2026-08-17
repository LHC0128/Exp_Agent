"""Mx Z 最优控制 RF 灵敏度实验参数。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ...common import find_project_root, validate_safety_limit
from ...experiment_params import parameter
from ...steps.keithley_6221 import (
    KEITHLEY_6221_CURRENT_RANGE_OPTIONS_MA,
    require_keithley_6221_current_range,
)
from ..mx_y_rf_sensitivity.models import MxYRFParams
from .sources import (
    build_applied_current,
    load_keithley_calibration,
    load_theory_control,
)


@dataclass(slots=True)
class MxKeithley6221OptimalControlRFParams(MxYRFParams):
    """6221 主场任意波控制下的 RF 灵敏度参数。"""

    schema_version = 3

    run_tag: str = parameter(
        default="mx_6221_optimal_control_rf",
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
        default=30000.0,
        external_name="Y_RF_FREQUENCY_HZ",
        label="Y RF / HF2 固定频率",
        unit="Hz",
        group="basic",
        minimum=0.001,
        description=(
            "Y RF 驱动与 HF2 解调的共用频率；切换 CONTROL_VERSION 时"
            "自动填充为理论 rf 频率，可手动修改。"
        ),
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
        minimum=-10.0,
        maximum=10.0,
        description="GS200 控制 Z 主磁场线圈；可设置非零电流，范围由 safety_limits.yaml 约束。",
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
        default="v2",
        external_name="CONTROL_VERSION",
        label="最优控制版本",
        group="basic",
        description="按 vN 解析同版本的波形与理论参数文件。",
    )
    control_results_root: str = parameter(
        default=r"D:\Code\theory_agent\simulate\results\oc_sens",
        external_name="CONTROL_RESULTS_ROOT",
        label="最优控制结果根目录",
        group="advanced",
        visible=False,
    )
    keithley_calibration_source_run: str = parameter(
        default="0813_114104_mx_6221_main_field_cal",
        external_name="KEITHLEY_CALIBRATION_SOURCE_RUN",
        label="6221 主场标定来源运行",
        group="basic",
    )
    control_scale: float = parameter(
        default=1.0,
        external_name="CONTROL_SCALE",
        label="控制幅度比例",
        group="basic",
        minimum=0.0,
    )
    keithley_current_range_ma: float = parameter(
        default=20.0,
        external_name="KEITHLEY_CURRENT_RANGE_MA",
        label="6221 电流源档位",
        unit="mA",
        group="basic",
        options=KEITHLEY_6221_CURRENT_RANGE_OPTIONS_MA,
        description=(
            "根据理论控制波形、6221 标定结果和 CONTROL_SCALE 计算完整电流包络；"
            "所选固定档位必须覆盖最大绝对控制电流。"
        ),
    )
    phase_cal_rf_amplitude_vpp: float = parameter(
        default=0.01,
        external_name="PHASE_CAL_RF_AMPLITUDE_VPP",
        label="校相 Y RF 幅度",
        unit="Vpp",
        group="basic",
        minimum=0.0,
        maximum=2.0,
        safety_key="rf_coil",
        description=(
            "校相时 Y RF 幅度（复合拟合模型中固定为 A）；6221 变体固定使用 "
            "Y RF 触发相位扫描，并用 Demod R 信号拟合 |色散| 折叠模型 "
            "R = |scale*(B-b0)/((B-b0)^2+w^2)|、B(phi) = |A e^{i phi} + C e^{i phi_c}|"
            "选择建设性相位；不支持控制波形相位扫描。"
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
        label="校相响应峰谷差最低显著性",
        unit="σ",
        group="advanced",
        minimum=0.0,
        description=(
            "|色散| 折叠拟合的响应峰谷差与中位点噪声之比的最低门槛；"
            "低于该值认为相位响应不显著。"
        ),
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
        default=30000.0,
        external_name="TRIGGER_FREQUENCY_HZ",
        label="共同触发频率",
        unit="Hz",
        group="advanced",
        minimum=0.001,
        read_only=True,
        description=(
            "由 CONTROL_VERSION 任意波时间轴自动计算，不能手动修改；"
            "GUI 在版本变化时自动刷新该只读显示。"
        ),
    )
    trigger_amplitude_vpp: float = parameter(
        default=5.0,
        external_name="TRIGGER_AMPLITUDE_VPP",
        label="共同触发幅度",
        unit="Vpp",
        group="advanced",
        minimum=0.0,
        maximum=20.0,
        read_only=True,
    )
    trigger_offset_v: float = parameter(
        default=2.5,
        external_name="TRIGGER_OFFSET_V",
        label="共同触发偏置",
        unit="V",
        group="advanced",
        minimum=-10.0,
        maximum=10.0,
        read_only=True,
    )
    trigger_duty_percent: float = parameter(
        default=50.0,
        external_name="TRIGGER_DUTY_PERCENT",
        label="共同触发占空比",
        unit="%",
        group="advanced",
        minimum=0.1,
        maximum=99.9,
        read_only=True,
    )

    confirm_gs200_connected: bool = parameter(
        default=False,
        external_name="CONFIRM_GS200_PHYSICALLY_CONNECTED",
        label="确认 GS200 已接入 Z 主磁场线圈",
        group="basic",
        description="6221 接 Z 小磁场线圈，GS200 接 Z 主磁场线圈；必须明确确认接线正确。",
    )
    keithley_output_response: str = parameter(
        default="FAST",
        external_name="KEITHLEY_OUTPUT_RESPONSE",
        label="6221 输出响应",
        group="advanced",
        options=(("FAST", "FAST（适合 30 kHz 任意波）"),),
        read_only=True,
    )
    keithley_compliance_v: float = parameter(
        default=15.0,
        external_name="KEITHLEY_COMPLIANCE_V",
        label="6221 Compliance",
        unit="V",
        group="advanced",
        minimum=0.1,
        maximum=105.0,
        read_only=True,
        description=(
            "当前主线圈 30 kHz 重复外触发真机测试中 12 V 进入 Compliance、"
            "13 V 通过；固定 15 V 以保留动态余量。"
        ),
    )
    keithley_trigger_line: int = parameter(
        default=1,
        external_name="KEITHLEY_TRIGGER_LINE",
        label="6221 Trigger Link 输入线",
        group="advanced",
        minimum=1,
        maximum=6,
        read_only=True,
    )

    @classmethod
    def migrate_external(
        cls,
        values: dict[str, object],
        schema_version: int,
    ) -> dict[str, object]:
        """迁移旧配置并避免把旧的断开确认误当成新的接入确认。"""
        migrated = super(MxKeithley6221OptimalControlRFParams, cls).migrate_external(
            values,
            schema_version,
        )
        if schema_version < 3 and "CONFIRM_GS200_PHYSICALLY_DISCONNECTED" in migrated:
            # 旧确认的语义与新确认相反，不能静默转换为已接入。
            migrated.pop("CONFIRM_GS200_PHYSICALLY_DISCONNECTED", None)
            migrated.setdefault("CONFIRM_GS200_PHYSICALLY_CONNECTED", False)
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
        if not self.confirm_gs200_connected:
            errors.append("必须确认 GS200 已接入 Z 主磁场线圈（6221 接 Z 小磁场线圈）")
        if self.keithley_output_response != "FAST":
            errors.append("6221 输出响应必须固定为 FAST")
        if self.keithley_compliance_v != 15.0:
            errors.append("6221 Compliance 必须固定为 15 V")
        if self.keithley_trigger_line != 1:
            errors.append("6221 外部触发必须固定使用 Trigger Link Line 1")
        if self.trigger_amplitude_vpp != 5.0 or self.trigger_offset_v != 2.5:
            errors.append("Time_sequence_2 触发必须固定为 5 Vpp、2.5 V offset")
        if self.trigger_duty_percent != 50.0:
            errors.append("Time_sequence_2 触发占空比必须固定为 50%")
        if self.phase_cal_rf_amplitude_vpp <= 0.0:
            errors.append("6221 变体不支持关闭 Y RF 后扫描控制波形相位")
        if self.linewidth_mode != "amplitude_equivalent":
            errors.append("LINEWIDTH_MODE 必须固定为 amplitude_equivalent")
        if self.control_scale <= 0:
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
                        "Y RF 校相必须覆盖恰好一个 360° 周期，"
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
            theory = load_theory_control(
                Path(self.control_results_root),
                self.control_version,
            )
            calibration = load_keithley_calibration(
                root,
                self.keithley_calibration_source_run,
            )
            applied = build_applied_current(
                theory,
                calibration,
                self.control_scale,
            )
            try:
                require_keithley_6221_current_range(
                    self.keithley_current_range_ma,
                    applied.minimum_ma,
                    applied.maximum_ma,
                )
            except ValueError as exc:
                errors.append(str(exc))
            if not np.isclose(
                self.trigger_frequency_hz,
                theory.repeat_frequency_hz,
                rtol=1e-9,
                atol=1e-6,
            ):
                errors.append(
                    "TRIGGER_FREQUENCY_HZ 必须等于理论任意波重复频率 "
                    f"{theory.repeat_frequency_hz:.9g} Hz"
                )
        except (OSError, TypeError, ValueError, KeyError) as exc:
            errors.append(str(exc))
        return errors

    @classmethod
    def derive_external(cls, values: dict[str, object]) -> dict[str, object]:
        """按当前 CONTROL_VERSION 计算只读派生显示值（GUI 派生字段机制）。"""
        defaults = cls()
        root = Path(
            str(values.get("CONTROL_RESULTS_ROOT") or defaults.control_results_root)
        )
        version = str(values.get("CONTROL_VERSION") or defaults.control_version)
        try:
            theory = load_theory_control(root, version)
        except Exception:
            return {}
        return {
            "TRIGGER_FREQUENCY_HZ": theory.repeat_frequency_hz,
            "Y_RF_FREQUENCY_HZ": theory.theory_rf_frequency_hz,
        }
