"""静磁场灵敏度的强类型参数。"""

from __future__ import annotations

from dataclasses import dataclass

from ...common import validate_safety_limit
from ...experiment_params import ExperimentParams, parameter


@dataclass(slots=True)
class StaticSensitivityParams(ExperimentParams):
    """保留静磁场实验现有配置键，同时提供 typed schema。"""

    schema_version = 1

    run_tag: str = parameter(default="sens", external_name="run_tag", label="运行标签", group="basic")
    pump_laser_power: float = parameter(default=0.2, external_name="pump_laser_power", label="Pump 光功率", unit="V", group="basic", minimum=0, maximum=1, safety_key="Pump_laser_power")
    probe_laser_power: float = parameter(default=0.2, external_name="probe_laser_power", label="Probe 光功率", unit="V", group="basic", minimum=0, maximum=1, safety_key="Probe_laser_power")
    temperature: float = parameter(default=120.0, external_name="temperature", label="气室温度", unit="°C", group="basic", minimum=20, maximum=130, safety_key="temperature")
    main_magnetic_field: float = parameter(default=9.28, external_name="main_magnetic_field", label="主磁场电流", unit="mA", group="basic", minimum=-10, maximum=10, safety_key="main_magnetic_field")
    x_magnetic_field: float = parameter(default=0.0, external_name="x_magnetic_field", label="X 磁场", unit="V", group="basic", minimum=-10, maximum=10, safety_key="X_magnetic_field")
    y_magnetic_field: float = parameter(default=0.0, external_name="y_magnetic_field", label="Y 磁场", unit="V", group="basic", minimum=-10, maximum=10, safety_key="Y_magnetic_field")
    ramp_low: float = parameter(default=-0.4, external_name="ramp_low", label="Z 扫场下限", unit="V", group="basic", minimum=-10, maximum=10, safety_key="Z_magnetic_field")
    ramp_high: float = parameter(default=0.4, external_name="ramp_high", label="Z 扫场上限", unit="V", group="basic", minimum=-10, maximum=10, safety_key="Z_magnetic_field")
    ramp_freq: float = parameter(default=1.0, external_name="ramp_freq", label="Z 扫场频率", unit="Hz", group="basic", minimum=0.001)
    ramp_symmetry: float = parameter(default=20.0, external_name="ramp_symmetry", label="斜波对称度", unit="%", group="basic", minimum=0, maximum=100)
    daq_duration: float = parameter(default=1.0, external_name="daq_duration", label="色散采集时长", unit="s", group="basic", minimum=0.01)
    pump_mod_freq: float = parameter(default=90000.0, external_name="pump_mod_freq", label="Pump 调制频率", unit="Hz", group="basic", minimum=1)
    pump_mod_amplitude: float = parameter(default=0.18, external_name="pump_mod_amplitude", label="Pump 载波幅度", unit="Vpp", group="basic", minimum=0, maximum=0.18, safety_key="Pump_modulation")
    pump_mod_duty: float = parameter(default=5.0, external_name="pump_mod_duty", label="Pump 占空比", unit="%", group="basic", minimum=1, maximum=50, safety_key="PUMP_MOD_DUTY")
    noise_n_avg: int = parameter(default=1, external_name="noise_n_avg", label="噪声平均次数", group="basic", minimum=1)
    noise_duration: float = parameter(default=1.0, external_name="noise_duration", label="单次噪声时长", unit="s", group="basic", minimum=0.01)

    temp_switch: float = parameter(default=5.0, external_name="temp_switch", label="温控开关电平", unit="V", group="advanced", minimum=0, maximum=5, safety_key="Temp_Switch")
    time_sequence: float = parameter(default=10.0, external_name="time_sequence", label="时序信号幅度", unit="Vpp", group="advanced", minimum=-10, maximum=10, safety_key="Time_sequence")
    time_sequence_2: float = parameter(default=0.0, external_name="time_sequence_2", label="时序信号 2", unit="V", group="advanced", minimum=-10, maximum=10, safety_key="Time_sequence_2")
    rf_gate_amplitude: float = parameter(default=5.0, external_name="rf_gate_amplitude", label="RF 门控幅度", unit="Vpp", group="advanced")
    rf_gate_offset: float = parameter(default=2.5, external_name="rf_gate_offset", label="RF 门控偏置", unit="V", group="advanced")
    rf_gate_delay: float = parameter(default=0.0, external_name="rf_gate_delay", label="RF 门控延迟", unit="s", group="advanced", minimum=0)
    hf2_demod_idx: int = parameter(default=0, external_name="hf2_demod_idx", label="HF2 Demod", group="advanced", minimum=0)
    hf2_demod_order: int = parameter(default=4, external_name="hf2_demod_order", label="HF2 阶数", group="advanced", minimum=1)
    hf2_signal_range: float = parameter(default=2.0, external_name="hf2_signal_range", label="HF2 输入量程", unit="V", group="advanced", minimum=0)
    hf2_demod_rate: float = parameter(default=1000.0, external_name="hf2_demod_rate", label="色散采样率", unit="Sa/s", group="advanced", minimum=1)
    hf2_demod_tc: float = parameter(default=0.001, external_name="hf2_demod_tc", label="色散时间常数", unit="s", group="advanced", minimum=0)
    hf2_noise_rate: float = parameter(default=50000.0, external_name="hf2_noise_rate", label="噪声采样率", unit="Sa/s", group="advanced", minimum=1)
    hf2_noise_tc: float = parameter(default=1e-6, external_name="hf2_noise_tc", label="噪声时间常数", unit="s", group="advanced", minimum=0)
    temp_tolerance_c: float = parameter(default=1.0, external_name="temp_tolerance_c", label="温度容差", unit="°C", group="advanced", minimum=0)
    temp_stable_reads: int = parameter(default=3, external_name="temp_stable_reads", label="稳定连续读数", group="advanced", minimum=1)
    temp_poll_interval_s: float = parameter(default=5.0, external_name="temp_poll_interval_s", label="温度轮询间隔", unit="s", group="advanced", minimum=0.1)
    max_temp_wait_s: float = parameter(default=1200.0, external_name="max_temp_wait_s", label="最长温稳等待", unit="s", group="advanced", minimum=1)
    gs200_current_ranges: list[float] = parameter(default_factory=lambda: [0.01], external_name="gs200_current_ranges", label="GS200 量程列表", unit="A", group="advanced")
    gs200_range_headroom: float = parameter(default=1.2, external_name="gs200_range_headroom", label="GS200 量程余量", group="advanced", minimum=1)
    gs200_range_settle_time: float = parameter(default=1.0, external_name="gs200_range_settle_time", label="GS200 换挡等待", unit="s", group="advanced", minimum=0)
    z_v_to_nt: float = parameter(default=3517.0, external_name="z_v_to_nt", label="Z 场换算系数", unit="nT/V", group="advanced", minimum=0)
    daq_trigger_channel: int = parameter(default=0, external_name="daq_trigger_channel", label="DAQ 触发通道", group="advanced", minimum=0)
    daq_trigger_level: float = parameter(default=1.0, external_name="daq_trigger_level", label="DAQ 触发电平", unit="V", group="advanced")
    daq_trigger_slope: int = parameter(default=0, external_name="daq_trigger_slope", label="DAQ 触发斜率", group="advanced")

    def validate_model(self) -> list[str]:
        errors: list[str] = []
        if not self.run_tag.strip():
            errors.append("run_tag 不能为空")
        if self.ramp_low >= self.ramp_high:
            errors.append("ramp_low 必须小于 ramp_high")
        if not 0 < self.ramp_symmetry < 100:
            errors.append("ramp_symmetry 必须位于 0 到 100 之间")
        if not self.gs200_current_ranges:
            errors.append("gs200_current_ranges 不能为空")
        for value in (self.ramp_low, self.ramp_high):
            try:
                validate_safety_limit("Z_magnetic_field", float(value))
            except ValueError as exc:
                errors.append(str(exc))
        return errors

    def to_legacy(self) -> dict[str, object]:
        """转换为旧主脚本仍读取的稳定 snake_case 配置。"""
        return self.to_external()
