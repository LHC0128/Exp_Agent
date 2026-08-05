"""Mx Y RF Probe 光功率与 PZT 失谐优化参数。"""

from __future__ import annotations

from dataclasses import dataclass

from ...common import validate_safety_limit
from ...experiment_params import parameter
from ..mx_y_rf_sensitivity.models import LinewidthMode, MxYRFParams


@dataclass(slots=True)
class MxYRFProbeDetuningOptimizationParams(MxYRFParams):
    """固定 Pump，二维扫描 Probe 功率和 DLC pro PZT Scan Offset。"""

    schema_version = 1

    run_tag: str = parameter(
        default="mx_y_rf_probe_detuning_opt",
        external_name="RUN_TAG",
        label="运行标签",
        group="basic",
    )
    linewidth_mode: LinewidthMode = parameter(
        default="amplitude_equivalent",
        external_name="LINEWIDTH_MODE",
        label="线宽模式",
        visible=False,
    )
    y_rf_frequency_hz: float = parameter(
        default=90000.0,
        external_name="Y_RF_FREQUENCY_HZ",
        label="Y RF 固定频率",
        unit="Hz",
        group="basic",
        minimum=0.001,
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
        label="幅度点稳定时间",
        unit="s",
        group="basic",
        minimum=0,
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
    frequency_start_hz: float = parameter(
        default=6000.0,
        external_name="FREQUENCY_START_HZ",
        label="未使用的模式1扫频起点",
        unit="Hz",
        visible=False,
        minimum=0.001,
    )
    frequency_stop_hz: float = parameter(
        default=14000.0,
        external_name="FREQUENCY_STOP_HZ",
        label="未使用的模式1扫频终点",
        unit="Hz",
        visible=False,
        minimum=0.001,
    )
    frequency_points: int = parameter(
        default=101,
        external_name="FREQUENCY_POINTS",
        label="未使用的模式1扫频点数",
        visible=False,
        minimum=5,
    )
    frequency_rf_amplitude_vpp: float = parameter(
        default=0.05,
        external_name="FREQUENCY_RF_AMPLITUDE_VPP",
        label="未使用的模式1 RF 幅度",
        unit="Vpp",
        visible=False,
        minimum=0,
        maximum=2.0,
        safety_key="rf_coil",
    )
    frequency_settle_time_s: float = parameter(
        default=0.05,
        external_name="FREQUENCY_SETTLE_TIME_S",
        label="未使用的模式1稳定时间",
        unit="s",
        visible=False,
        minimum=0,
    )
    frequency_duration_s: float = parameter(
        default=0.1,
        external_name="FREQUENCY_DURATION_S",
        label="未使用的模式1采样时间",
        unit="s",
        visible=False,
        minimum=0.001,
    )
    pump_laser_power_v: float = parameter(
        default=0.5,
        external_name="FIXED_PARAMS.Pump_laser_power",
        label="固定 Pump 光功率",
        unit="V",
        group="basic",
        safety_key="Pump_laser_power",
    )
    probe_laser_power_v: float = parameter(
        default=0.3,
        external_name="FIXED_PARAMS.Probe_laser_power",
        label="结束恢复 Probe 光功率",
        unit="V",
        group="basic",
        safety_key="Probe_laser_power",
    )
    main_magnetic_field_ma: float = parameter(
        default=9.28,
        external_name="FIXED_PARAMS.main_magnetic_field",
        label="Z 主磁场电流",
        unit="mA",
        group="basic",
        safety_key="main_magnetic_field",
    )
    temperature_c: float = parameter(
        default=120.0,
        external_name="FIXED_PARAMS.temperature",
        label="未使用的 TEC 目标温度",
        unit="°C",
        visible=False,
        safety_key="temperature",
    )
    temperature_tolerance_c: float = parameter(
        default=1.0,
        external_name="TEMPERATURE_TOLERANCE_C",
        label="未使用的温度稳定容差",
        unit="°C",
        visible=False,
        minimum=0,
    )
    temperature_stable_reads: int = parameter(
        default=1,
        external_name="TEMPERATURE_STABLE_READS",
        label="未使用的温度连续稳定读数",
        visible=False,
        minimum=1,
    )
    temperature_poll_interval_s: float = parameter(
        default=5.0,
        external_name="TEMPERATURE_POLL_INTERVAL_S",
        label="未使用的温度轮询间隔",
        unit="s",
        visible=False,
        minimum=0.1,
    )
    temperature_timeout_s: float = parameter(
        default=1200.0,
        external_name="TEMPERATURE_TIMEOUT_S",
        label="未使用的温度稳定超时",
        unit="s",
        visible=False,
        minimum=1,
    )

    probe_power_start_v: float = parameter(
        default=0.1,
        external_name="PROBE_POWER_START_V",
        label="Probe 扫描起点",
        unit="V",
        group="basic",
        description="固定 Probe 时与扫描终点设为相同值，并将扫描点数设为 1。",
        safety_key="Probe_laser_power",
    )
    probe_power_stop_v: float = parameter(
        default=0.5,
        external_name="PROBE_POWER_STOP_V",
        label="Probe 扫描终点",
        unit="V",
        group="basic",
        description="固定 Probe 时与扫描起点设为相同值，并将扫描点数设为 1。",
        safety_key="Probe_laser_power",
    )
    probe_power_points: int = parameter(
        default=7,
        external_name="PROBE_POWER_POINTS",
        label="Probe 扫描点数",
        group="basic",
        minimum=1,
        description="设为 1 时固定 Probe；此时扫描起点和终点必须相等。",
    )
    pzt_voltage_start_v: float = parameter(
        default=60.0,
        external_name="PZT_VOLTAGE_START_V",
        label="PZT 扫描起点",
        unit="V",
        group="basic",
        safety_key="probe_laser_pzt_voltage",
    )
    pzt_voltage_stop_v: float = parameter(
        default=100.0,
        external_name="PZT_VOLTAGE_STOP_V",
        label="PZT 扫描终点",
        unit="V",
        group="basic",
        safety_key="probe_laser_pzt_voltage",
    )
    pzt_voltage_points: int = parameter(
        default=9,
        external_name="PZT_VOLTAGE_POINTS",
        label="PZT 扫描点数",
        group="basic",
        minimum=2,
    )
    pzt_settle_time_s: float = parameter(
        default=1.0,
        external_name="PZT_SETTLE_TIME_S",
        label="PZT 切换后稳定时间",
        unit="s",
        group="basic",
        minimum=0,
    )

    def validate_model(self) -> list[str]:
        errors = MxYRFParams.validate_model(self)
        if self.linewidth_mode != "amplitude_equivalent":
            errors.append("LINEWIDTH_MODE 必须固定为 amplitude_equivalent")
        if self.y_rf_nt_per_vpp <= 0:
            errors.append("Y_RF_NT_PER_VPP 必须大于 0 才能比较磁场灵敏度")
        if self.probe_power_points == 1:
            if self.probe_power_start_v != self.probe_power_stop_v:
                errors.append(
                    "Probe 扫描点数为 1 时，功率扫描起点必须等于终点"
                )
        elif self.probe_power_start_v >= self.probe_power_stop_v:
            errors.append(
                "Probe 扫描点数至少为 2 时，功率扫描起点必须小于终点"
            )
        if self.pzt_voltage_start_v >= self.pzt_voltage_stop_v:
            errors.append("PZT 电压扫描起点必须小于终点")
        for safety_key, name, value in (
            (
                "Probe_laser_power",
                "PROBE_POWER_START_V",
                self.probe_power_start_v,
            ),
            (
                "Probe_laser_power",
                "PROBE_POWER_STOP_V",
                self.probe_power_stop_v,
            ),
            (
                "probe_laser_pzt_voltage",
                "PZT_VOLTAGE_START_V",
                self.pzt_voltage_start_v,
            ),
            (
                "probe_laser_pzt_voltage",
                "PZT_VOLTAGE_STOP_V",
                self.pzt_voltage_stop_v,
            ),
        ):
            try:
                validate_safety_limit(safety_key, value)
            except ValueError as exc:
                errors.append(f"{name}: {exc}")
        return errors
