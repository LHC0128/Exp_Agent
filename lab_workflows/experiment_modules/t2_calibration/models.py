"""T2 光学 FID 标定实验的强类型参数。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, ClassVar, Literal

from ...common import validate_safety_limit
from ...experiment_params import ExperimentParams, parameter


@dataclass(slots=True)
class T2CalibrationParams(ExperimentParams):
    """保持旧脚本外部键稳定的 T2 标定参数。"""

    schema_version: ClassVar[int] = 2

    run_tag: str = parameter(default="optical_FID", external_name="RUN_TAG", label="运行标签", group="basic")
    pump_power: float = parameter(default=0.1, external_name="PUMP_POWER", label="Pump 光 DC 功率", unit="V", group="basic", safety_key="Pump_laser_power")
    aom_carrier_freq: float = parameter(default=100.0e6, external_name="AOM_CARRIER_FREQ", label="AOM 载波频率", unit="Hz", visible=False, minimum=1)
    aom_carrier_amplitude: float = parameter(default=0.1, external_name="AOM_CARRIER_AMPLITUDE", label="AOM 载波幅度", unit="V", group="basic", safety_key="Pump_modulation")
    rf_gate_freq: float = parameter(default=90000.0, external_name="RF_GATE_FREQ", label="RF 门控频率", unit="Hz", group="basic", minimum=1)
    rf_gate_amplitude: float = parameter(default=5.0, external_name="RF_GATE_AMPLITUDE", label="RF 门控幅度", unit="Vpp", visible=False, minimum=0, safety_key="Time_sequence")
    rf_gate_offset: float = parameter(default=2.5, external_name="RF_GATE_OFFSET", label="RF 门控偏置", unit="V", visible=False, safety_key="Time_sequence")
    rf_gate_duty: float = parameter(default=5.0, external_name="RF_GATE_DUTY", label="RF 门控占空比", unit="%", group="basic", safety_key="PUMP_MOD_DUTY")
    burst_ncycles: int = parameter(default=5000, external_name="BURST_NCYCLES", label="Burst 周期数", group="basic", minimum=1)
    burst_period: float = parameter(default=0.1, external_name="BURST_PERIOD", label="Burst 周期", unit="s", group="advanced", minimum=0.000001)
    do_power_scan: bool = parameter(default=True, external_name="DO_POWER_SCAN", label="启用 Probe 功率扫描", group="basic")
    probe_power: float = parameter(default=0.1, external_name="PROBE_POWER", label="单点 Probe 光功率", unit="V", group="basic", safety_key="Probe_laser_power")
    probe_power_start: float = parameter(
        default=0.01,
        external_name="PROBE_POWER_START",
        label="Probe 功率起始值",
        unit="V",
        group="basic",
        safety_key="Probe_laser_power",
    )
    probe_power_stop: float = parameter(default=0.1, external_name="PROBE_POWER_STOP", label="Probe 功率终止值", unit="V", group="basic", safety_key="Probe_laser_power")
    probe_power_points: int = parameter(default=10, external_name="PROBE_POWER_POINTS", label="Probe 功率扫描点数", group="basic", minimum=2)
    probe_power_list_compat: list[float] = parameter(default_factory=list, external_name="PROBE_POWER_LIST", label="旧版 Probe 功率列表", unit="V", visible=False)
    main_field_ma: float = parameter(default=9.30, external_name="MAIN_FIELD_mA", label="主磁场电流", unit="mA", group="basic", safety_key="main_magnetic_field")
    scope_pd_channel: int = parameter(default=1, external_name="SCOPE_PD_CHANNEL", label="PD 示波器通道", visible=False, minimum=1, maximum=4)
    scope_trig_channel: int = parameter(default=4, external_name="SCOPE_TRIG_CHANNEL", label="触发示波器通道", visible=False, minimum=1, maximum=4)
    scope_trig_slope: Literal["FALLing", "RISing"] = parameter(default="FALLing", external_name="SCOPE_TRIG_SLOPE", label="示波器触发边沿", visible=False)
    scope_sample_rate: float = parameter(default=1.0e6, external_name="SCOPE_SAMPLE_RATE", label="示波器采样率", unit="Sa/s", group="advanced", minimum=1)
    scope_duration: float = parameter(default=0.05, external_name="SCOPE_DURATION", label="FID 采集时长", unit="s", group="basic", minimum=0.000001)
    acq_repeats: int = parameter(default=5, external_name="ACQ_REPEATS", label="每个功率点重复次数", group="basic", minimum=1)
    hf2_demod_idx: int = parameter(default=1, external_name="HF2_DEMOD_IDX", label="HF2 Demod 索引", visible=False, minimum=0)
    hf2_osc_freq_compat: float = parameter(default=90000.0, external_name="HF2_OSC_FREQ", label="HF2 振荡器频率（兼容键）", unit="Hz", visible=False, minimum=1)
    hf2_signal_range: float = parameter(default=2.0, external_name="HF2_SIGNAL_RANGE", label="HF2 输入量程", unit="V", group="advanced", minimum=0.001)
    hf2_demod_tc: float = parameter(default=0.001, external_name="HF2_DEMOD_TC", label="HF2 解调时间常数", unit="s", group="advanced", minimum=0.000001)
    hf2_demod_order: int = parameter(default=4, external_name="HF2_DEMOD_ORDER", label="HF2 解调滤波阶数", group="advanced", minimum=1)
    hf2_demod_rate: float = parameter(default=10000.0, external_name="HF2_DEMOD_RATE", label="HF2 解调采样率", unit="Sa/s", group="advanced", minimum=1)
    tec_temperature: float = parameter(default=100.0, external_name="TEC_TEMPERATURE", label="气室温度", unit="°C", group="basic", safety_key="temperature")
    vert_divs: int = parameter(default=4, external_name="VERT_DIVS", label="示波器垂直格数", visible=False, minimum=1)
    scale_min: float = parameter(default=0.01, external_name="SCALE_MIN", label="自动量程下限", unit="V/div", visible=False, minimum=0.000001)
    scale_max: float = parameter(default=10.0, external_name="SCALE_MAX", label="自动量程上限", unit="V/div", visible=False, minimum=0.000001)

    _fixed_aliases: ClassVar[dict[str, str]] = {
        "FIXED_PARAMS.Pump_laser_power": "PUMP_POWER",
        "FIXED_PARAMS.Probe_laser_power": "PROBE_POWER",
        "FIXED_PARAMS.main_magnetic_field": "MAIN_FIELD_mA",
        "FIXED_PARAMS.temperature": "TEC_TEMPERATURE",
    }

    @classmethod
    def migrate_external(cls, values: dict[str, Any], schema_version: int) -> dict[str, Any]:
        if schema_version > cls.schema_version:
            raise ValueError(
                f"配置 schema_version={schema_version} 高于程序支持版本 {cls.schema_version}"
            )
        migrated = dict(values)
        for alias, canonical in cls._fixed_aliases.items():
            if alias not in migrated:
                continue
            # 兼容别名作为显式输入时覆盖默认 YAML 中已经合并的旧键值。
            migrated[canonical] = migrated.pop(alias)
        legacy_values = migrated.get("PROBE_POWER_LIST")
        if isinstance(legacy_values, (list, tuple)) and len(legacy_values) >= 2:
            try:
                numbers = [float(value) for value in legacy_values]
            except (TypeError, ValueError):
                numbers = []
            if numbers:
                migrated["PROBE_POWER_START"] = numbers[0]
                migrated["PROBE_POWER_STOP"] = numbers[-1]
                migrated["PROBE_POWER_POINTS"] = len(numbers)
                step = (numbers[-1] - numbers[0]) / (len(numbers) - 1)
                uniform = step > 0 and all(
                    math.isclose(value, numbers[0] + index * step, rel_tol=1e-9, abs_tol=1e-12)
                    for index, value in enumerate(numbers)
                )
                if uniform:
                    migrated.pop("PROBE_POWER_LIST", None)
        # HF2 参考频率不再独立配置，始终跟随 RF 门控频率。
        if "RF_GATE_FREQ" in migrated:
            migrated["HF2_OSC_FREQ"] = migrated["RF_GATE_FREQ"]
        else:
            migrated.pop("HF2_OSC_FREQ", None)
        return migrated

    @property
    def burst_duration(self) -> float:
        return self.burst_ncycles / self.rf_gate_freq

    @property
    def scope_total_points(self) -> int:
        return int(self.scope_sample_rate * self.scope_duration)

    @property
    def hf2_osc_freq(self) -> float:
        return self.rf_gate_freq

    @property
    def probe_power_values(self) -> list[float]:
        if self.probe_power_list_compat:
            return list(self.probe_power_list_compat)
        return [
            self.probe_power_start
            + index * (self.probe_power_stop - self.probe_power_start) / (self.probe_power_points - 1)
            for index in range(self.probe_power_points)
        ]

    def validate_model(self) -> list[str]:
        errors: list[str] = []
        if not self.run_tag.strip():
            errors.append("RUN_TAG 不能为空")
        if self.scope_pd_channel == self.scope_trig_channel:
            errors.append("PD 通道与触发通道不能相同")
        if self.burst_period < self.burst_duration:
            errors.append("BURST_PERIOD 不能短于 BURST_NCYCLES / RF_GATE_FREQ")
        if self.scope_sample_rate <= 2 * self.rf_gate_freq:
            errors.append("SCOPE_SAMPLE_RATE 必须高于 RF_GATE_FREQ 的两倍")
        if self.scope_total_points < 2:
            errors.append("示波器采集点数必须至少为 2")
        if self.scope_total_points > 100000:
            errors.append("当前 100K 存储深度无法容纳所需示波器采集点数")
        if self.scale_min >= self.scale_max:
            errors.append("SCALE_MIN 必须小于 SCALE_MAX")
        if self.do_power_scan and not self.probe_power_list_compat and self.probe_power_start >= self.probe_power_stop:
            errors.append("PROBE_POWER_START 必须小于 PROBE_POWER_STOP")
        for index, value in enumerate(self.probe_power_values):
            if not math.isfinite(value):
                errors.append(f"PROBE_POWER_LIST[{index}] 必须是有限数值")
                continue
            try:
                validate_safety_limit("Probe_laser_power", float(value))
            except ValueError as exc:
                errors.append(str(exc))
        gate_low = self.rf_gate_offset - self.rf_gate_amplitude / 2
        gate_high = self.rf_gate_offset + self.rf_gate_amplitude / 2
        for value in (gate_low, gate_high):
            try:
                validate_safety_limit("Time_sequence", float(value))
            except ValueError as exc:
                errors.append(str(exc))
        return errors
