"""Mx Z 最优控制 XY 泄露响应参数。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ...common import find_project_root, validate_safety_limit
from ...experiment_params import ExperimentParams, parameter
from ..mx_z_optimal_control_rf_sensitivity.sources import (
    build_applied_control,
    load_theory_control,
    load_z_calibration,
)


@dataclass(slots=True)
class MxZOptimalControlXYLeakageParams(ExperimentParams):
    """固定 Z 最优控制并二维扫描同形 X/Y 任意波。"""

    schema_version = 1

    run_tag: str = parameter(
        default="mx_z_optimal_control_xy_leakage",
        external_name="RUN_TAG",
        label="运行标签",
        group="basic",
    )
    x_control_amp_start_vpp: float = parameter(
        default=-0.1,
        external_name="X_CONTROL_AMP_START_VPP",
        label="X 控制幅度起点",
        unit="Vpp",
        group="basic",
    )
    x_control_amp_stop_vpp: float = parameter(
        default=0.1,
        external_name="X_CONTROL_AMP_STOP_VPP",
        label="X 控制幅度终点",
        unit="Vpp",
        group="basic",
    )
    x_control_amp_points: int = parameter(
        default=21,
        external_name="X_CONTROL_AMP_POINTS",
        label="X 控制幅度点数",
        group="basic",
        minimum=3,
    )
    y_control_amp_start_vpp: float = parameter(
        default=-0.1,
        external_name="Y_CONTROL_AMP_START_VPP",
        label="Y 控制幅度起点",
        unit="Vpp",
        group="basic",
    )
    y_control_amp_stop_vpp: float = parameter(
        default=0.1,
        external_name="Y_CONTROL_AMP_STOP_VPP",
        label="Y 控制幅度终点",
        unit="Vpp",
        group="basic",
    )
    y_control_amp_points: int = parameter(
        default=21,
        external_name="Y_CONTROL_AMP_POINTS",
        label="Y 控制幅度点数",
        group="basic",
        minimum=3,
    )
    demod_frequency_hz: float = parameter(
        default=30000.0,
        external_name="DEMOD_FREQUENCY_HZ",
        label="HF2 Demod0 频率",
        unit="Hz",
        group="basic",
        minimum=0.001,
    )
    response_settle_time_s: float = parameter(
        default=0.1,
        external_name="RESPONSE_SETTLE_TIME_S",
        label="网格点稳定时间",
        unit="s",
        group="basic",
        minimum=0.0,
    )
    response_duration_s: float = parameter(
        default=0.2,
        external_name="RESPONSE_DURATION_S",
        label="网格点采样时间",
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
    demod_idx: int = parameter(
        default=0,
        external_name="DEMOD_IDX",
        label="HF2 解调器索引",
        visible=False,
        minimum=0,
    )
    demod_osc_idx: int = parameter(
        default=0,
        external_name="DEMOD_OSC_IDX",
        label="HF2 振荡器索引",
        visible=False,
        minimum=0,
    )
    hf2_signal_range_v: float = parameter(
        default=2.0,
        external_name="HF2_SIGNAL_RANGE_V",
        label="HF2 输入量程",
        unit="V",
        group="advanced",
        minimum=0.001,
    )
    response_rate_sa_s: float = parameter(
        default=1000.0,
        external_name="RESPONSE_RATE_SA_S",
        label="R 请求采样率",
        unit="Sa/s",
        group="advanced",
        minimum=1.0,
    )
    response_time_constant_s: float = parameter(
        default=0.001,
        external_name="RESPONSE_TIME_CONSTANT_S",
        label="R 解调时间常数",
        unit="s",
        group="advanced",
        minimum=0.0,
    )
    response_demod_order: int = parameter(
        default=4,
        external_name="RESPONSE_DEMOD_ORDER",
        label="R 解调滤波阶数",
        group="advanced",
        minimum=1,
    )
    temp_switch_off_lead_s: float = parameter(
        default=0.1,
        external_name="TEMP_SWITCH_OFF_LEAD_S",
        label="温控关闭后等待",
        unit="s",
        group="basic",
        minimum=0.0,
    )
    temp_switch_on_lag_s: float = parameter(
        default=2.0,
        external_name="TEMP_SWITCH_ON_LAG_S",
        label="温控恢复后等待",
        unit="s",
        group="basic",
        minimum=0.0,
    )
    temperature_tolerance_c: float = parameter(
        default=1.0,
        external_name="TEMPERATURE_TOLERANCE_C",
        label="初始温度稳定容差",
        unit="°C",
        group="advanced",
        minimum=0.0,
    )
    temperature_stable_reads: int = parameter(
        default=1,
        external_name="TEMPERATURE_STABLE_READS",
        label="初始温度连续稳定读数",
        group="advanced",
        minimum=1,
    )
    temperature_poll_interval_s: float = parameter(
        default=5.0,
        external_name="TEMPERATURE_POLL_INTERVAL_S",
        label="温度轮询间隔",
        unit="s",
        group="advanced",
        minimum=0.1,
    )
    temperature_timeout_s: float = parameter(
        default=1200.0,
        external_name="TEMPERATURE_TIMEOUT_S",
        label="温度稳定超时",
        unit="s",
        group="advanced",
        minimum=1.0,
    )
    pump_carrier_frequency_hz: float = parameter(
        default=100000000.0,
        external_name="PUMP_CARRIER_FREQUENCY_HZ",
        label="Pump AOM 载波频率",
        unit="Hz",
        group="advanced",
        minimum=1.0,
    )
    pump_carrier_amplitude_vpp: float = parameter(
        default=0.18,
        external_name="PUMP_CARRIER_AMPLITUDE_VPP",
        label="Pump AOM 载波幅度",
        unit="Vpp",
        group="advanced",
        minimum=0.0,
        maximum=0.18,
        safety_key="Pump_modulation",
    )
    pump_gate_voltage_v: float = parameter(
        default=5.0,
        external_name="PUMP_GATE_VOLTAGE_V",
        label="Pump RF 开关常开电平",
        unit="V",
        group="advanced",
        minimum=5.0,
        maximum=5.0,
        safety_key="Time_sequence",
    )
    pump_laser_power_v: float = parameter(
        default=0.1,
        external_name="FIXED_PARAMS.Pump_laser_power",
        label="Pump 光功率",
        unit="V",
        group="basic",
        safety_key="Pump_laser_power",
    )
    probe_laser_power_v: float = parameter(
        default=0.1,
        external_name="FIXED_PARAMS.Probe_laser_power",
        label="Probe 光功率",
        unit="V",
        group="basic",
        safety_key="Probe_laser_power",
    )
    main_magnetic_field_ma: float = parameter(
        default=-0.1414,
        external_name="FIXED_PARAMS.main_magnetic_field",
        label="GS200 主磁场电流",
        unit="mA",
        group="basic",
        safety_key="main_magnetic_field",
    )
    temperature_c: float = parameter(
        default=120.0,
        external_name="FIXED_PARAMS.temperature",
        label="气室温度",
        unit="°C",
        group="basic",
        safety_key="temperature",
    )
    control_version: str = parameter(
        default="v2",
        external_name="CONTROL_VERSION",
        label="最优控制版本",
        group="basic",
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
        label="Z 控制幅度比例",
        group="basic",
        minimum=0.0,
    )
    z_aw_output_vpp: float = parameter(
        default=6.0,
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
        label="Z/XY 正幅度触发相位",
        unit="deg",
        group="advanced",
        minimum=0.0,
        maximum=360.0,
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

    def validate_model(self) -> list[str]:
        errors: list[str] = []
        if not self.run_tag.strip():
            errors.append("RUN_TAG 不能为空")
        if self.x_control_amp_start_vpp >= self.x_control_amp_stop_vpp:
            errors.append("X 控制幅度必须满足起点 < 终点")
        if self.y_control_amp_start_vpp >= self.y_control_amp_stop_vpp:
            errors.append("Y 控制幅度必须满足起点 < 终点")
        if self.control_scale <= 0:
            errors.append("CONTROL_SCALE 必须大于 0")

        for key, values in (
            (
                "X_magnetic_field",
                (self.x_control_amp_start_vpp, self.x_control_amp_stop_vpp),
            ),
            (
                "Y_magnetic_field",
                (self.y_control_amp_start_vpp, self.y_control_amp_stop_vpp),
            ),
        ):
            peak_v = max(abs(float(value)) for value in values) / 2.0
            for voltage in (-peak_v, peak_v):
                try:
                    validate_safety_limit(key, voltage)
                except ValueError as exc:
                    errors.append(str(exc))

        trigger_low = (
            self.trigger_offset_v - self.trigger_amplitude_vpp / 2.0
        )
        trigger_high = (
            self.trigger_offset_v + self.trigger_amplitude_vpp / 2.0
        )
        for value in (trigger_low, trigger_high):
            try:
                validate_safety_limit("Time_sequence_2", value)
            except ValueError as exc:
                errors.append(str(exc))

        root = find_project_root()
        try:
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
