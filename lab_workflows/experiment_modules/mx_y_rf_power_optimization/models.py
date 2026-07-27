"""Mx Y RF 光功率灵敏度优化参数。"""

from __future__ import annotations

from dataclasses import dataclass

from ...common import validate_safety_limit
from ...experiment_params import parameter
from ..mx_y_rf_sensitivity.models import LinewidthMode, MxYRFParams


@dataclass(slots=True)
class MxYRFPowerOptimizationParams(MxYRFParams):
    """固定 Mx Y RF 测量协议并扫描 Pump/Probe 光功率。"""

    schema_version = 1

    run_tag: str = parameter(
        default="mx_y_rf_power_opt",
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
        label="结束恢复 Pump 光功率",
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
        label="气室温度",
        unit="°C",
        group="basic",
        safety_key="temperature",
    )

    pump_power_start_v: float = parameter(
        default=0.2,
        external_name="PUMP_POWER_START_V",
        label="Pump 扫描起点",
        unit="V",
        group="basic",
        safety_key="Pump_laser_power",
    )
    pump_power_stop_v: float = parameter(
        default=0.8,
        external_name="PUMP_POWER_STOP_V",
        label="Pump 扫描终点",
        unit="V",
        group="basic",
        safety_key="Pump_laser_power",
    )
    pump_power_points: int = parameter(
        default=7,
        external_name="PUMP_POWER_POINTS",
        label="Pump 扫描点数",
        group="basic",
        minimum=2,
    )
    probe_power_start_v: float = parameter(
        default=0.1,
        external_name="PROBE_POWER_START_V",
        label="Probe 扫描起点",
        unit="V",
        group="basic",
        safety_key="Probe_laser_power",
    )
    probe_power_stop_v: float = parameter(
        default=0.5,
        external_name="PROBE_POWER_STOP_V",
        label="Probe 扫描终点",
        unit="V",
        group="basic",
        safety_key="Probe_laser_power",
    )
    probe_power_points: int = parameter(
        default=7,
        external_name="PROBE_POWER_POINTS",
        label="Probe 扫描点数",
        group="basic",
        minimum=2,
    )

    def validate_model(self) -> list[str]:
        errors = MxYRFParams.validate_model(self)
        if self.linewidth_mode != "amplitude_equivalent":
            errors.append("LINEWIDTH_MODE 必须固定为 amplitude_equivalent")
        if self.y_rf_nt_per_vpp <= 0:
            errors.append("Y_RF_NT_PER_VPP 必须大于 0 才能比较磁场灵敏度")
        if self.pump_power_start_v >= self.pump_power_stop_v:
            errors.append("Pump 功率扫描起点必须小于终点")
        if self.probe_power_start_v >= self.probe_power_stop_v:
            errors.append("Probe 功率扫描起点必须小于终点")
        for safety_key, name, value in (
            ("Pump_laser_power", "PUMP_POWER_START_V", self.pump_power_start_v),
            ("Pump_laser_power", "PUMP_POWER_STOP_V", self.pump_power_stop_v),
            ("Probe_laser_power", "PROBE_POWER_START_V", self.probe_power_start_v),
            ("Probe_laser_power", "PROBE_POWER_STOP_V", self.probe_power_stop_v),
        ):
            try:
                validate_safety_limit(safety_key, value)
            except ValueError as exc:
                errors.append(f"{name}: {exc}")
        return errors
