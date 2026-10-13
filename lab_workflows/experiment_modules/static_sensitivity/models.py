"""静磁场灵敏度的强类型参数。"""

from __future__ import annotations

from lockin_amplifier import SignalInputConfig
import math
from dataclasses import asdict, dataclass

from ...common import validate_safety_limit
from ...experiment_params import ExperimentParams, parameter
from ...steps.dispersion import dispersion_sample_count


@dataclass(slots=True)
class StaticSensitivityParams(ExperimentParams):
    """保留静磁场实验现有配置键，同时提供 typed schema。"""

    schema_version = 3

    run_tag: str = parameter(default="sens", external_name="run_tag", label="运行标签", group="basic")
    pump_laser_power: float = parameter(default=0.2, external_name="pump_laser_power", label="Pump 光功率", unit="V", group="basic", minimum=0, maximum=1, safety_key="Pump_laser_power")
    probe_laser_power: float = parameter(default=0.2, external_name="probe_laser_power", label="Probe 光功率", unit="V", group="basic", minimum=0, maximum=1, safety_key="Probe_laser_power")
    temperature: float = parameter(default=120.0, external_name="temperature", label="气室温度", unit="°C", group="basic", minimum=20, maximum=130, safety_key="temperature")
    main_magnetic_field: float = parameter(default=9.28, external_name="main_magnetic_field", label="主磁场电流", unit="mA", group="basic", minimum=-10, maximum=10, safety_key="main_magnetic_field")
    x_magnetic_field: float = parameter(default=0.0, external_name="x_magnetic_field", label="X 磁场", unit="V", group="basic", minimum=-10, maximum=10, safety_key="X_magnetic_field")
    y_magnetic_field: float = parameter(default=0.0, external_name="y_magnetic_field", label="Y 磁场", unit="V", group="basic", minimum=-10, maximum=10, safety_key="Y_magnetic_field")
    dispersion_start_v: float = parameter(default=-0.4, external_name="ramp_low", label="色散扫描起点", unit="V", group="basic", minimum=-10, maximum=10, safety_key="Z_magnetic_field", description="直流逐点扫描起点；仅保留旧外部键名，不使用 RAMP。")
    dispersion_stop_v: float = parameter(default=0.4, external_name="ramp_high", label="色散扫描终点", unit="V", group="basic", minimum=-10, maximum=10, safety_key="Z_magnetic_field")
    dispersion_steps: int = parameter(default=41, external_name="dispersion_steps", label="色散扫描点数", group="basic", minimum=7)
    dispersion_step_settle_s: float = parameter(default=0.1, external_name="dispersion_step_settle_s", label="色散每步稳定等待", unit="s", minimum=0)
    dispersion_duration_s: float = parameter(default=0.1, external_name="dispersion_duration_s", label="色散每点采集时间", unit="s", minimum=0, description="每步稳定等待后连续采集的时间；实际记录时间按 HF2 回读速率向上取整到整数样本。")
    dispersion_temp_switch_interval_points: int = parameter(default=5, external_name="dispersion_temp_switch_interval_points", label="色散温控恢复间隔点数", minimum=0, description="每组完成后恢复温控并固定等待；0 表示整段色散扫描保持温控关闭，仅在收尾恢复。")
    dispersion_temp_switch_on_settle_s: float = parameter(default=3.0, external_name="dispersion_temp_switch_on_settle_s", label="每组后温控恢复等待", unit="s", minimum=0, description="每组后及校相后恢复温控的固定等待；色散扫描结束后温控由安全收尾恢复，不使用本等待。")
    dispersion_temp_switch_off_settle_s: float = parameter(default=1.0, external_name="dispersion_temp_switch_off_settle_s", label="色散关闭温控后等待", unit="s", minimum=0)
    pump_mod_freq: float = parameter(default=90000.0, external_name="pump_mod_freq", label="Pump 调制频率", unit="Hz", group="basic", minimum=1)
    pump_mod_amplitude: float = parameter(default=0.18, external_name="pump_mod_amplitude", label="Pump 载波幅度", unit="Vpp", group="basic", minimum=0, maximum=0.18, safety_key="Pump_modulation")
    pump_mod_duty: float = parameter(default=5.0, external_name="pump_mod_duty", label="Pump 占空比", unit="%", group="basic", minimum=1, maximum=50, safety_key="PUMP_MOD_DUTY")
    noise_n_avg: int = parameter(default=1, external_name="noise_n_avg", label="噪声平均次数", group="basic", minimum=1)
    noise_duration: float = parameter(default=1.0, external_name="noise_duration", label="单次噪声时长", unit="s", group="basic", minimum=0.01)

    temp_switch: float = parameter(default=5.0, external_name="temp_switch", label="温控开关电平", unit="V", group="advanced", minimum=0, maximum=5, safety_key="Temp_Switch")
    rf_gate_amplitude: float = parameter(default=5.0, external_name="rf_gate_amplitude", label="RF 门控幅度", unit="Vpp", group="advanced")
    rf_gate_offset: float = parameter(default=2.5, external_name="rf_gate_offset", label="RF 门控偏置", unit="V", group="advanced")
    rf_gate_delay: float = parameter(default=0.0, external_name="rf_gate_delay", label="RF 门控延迟", unit="s", group="advanced", minimum=0)
    hf2_demod_idx: int = parameter(default=0, external_name="hf2_demod_idx", label="锁相放大器 Demod", group="advanced", minimum=0)
    hf2_demod_order: int = parameter(default=4, external_name="hf2_demod_order", label="锁相放大器 阶数", group="advanced", minimum=1)
    hf2_signal_range: float = parameter(default=2.0, external_name="hf2_signal_range", label="锁相放大器 输入量程", unit="V", group="advanced", minimum=0)
    hf2_ac_coupling: bool = parameter(default=False, external_name="hf2_ac_coupling", label="锁相放大器 输入 AC 耦合", group="advanced", description="兼容旧配置；所有正式实验统一关闭 AC 耦合与 50 Ω 终端。", visible=False)
    hf2_50_ohm: bool = parameter(default=False, external_name="hf2_50_ohm", label="锁相放大器 输入 50 Ω", group="advanced", description="兼容旧配置；所有正式实验统一关闭 AC 耦合与 50 Ω 终端。", visible=False)
    hf2_demod_rate: float = parameter(default=1000.0, external_name="hf2_demod_rate", label="色散采样率", unit="Sa/s", group="advanced", minimum=1, description="用于 HF2 校相与色散连续采集；样本数和记录时长以实际回读速率为准。")
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

    def __post_init__(self):
        # 历史参数键仍可读取，输入模式统一使用公共关闭设置。
        defaults = SignalInputConfig()
        self.hf2_ac_coupling = defaults.ac_coupling
        self.hf2_50_ohm = defaults.impedance == 50

    def validate_model(self) -> list[str]:
        errors: list[str] = []
        if not self.run_tag.strip():
            errors.append("run_tag 不能为空")
        if any(ord(char) < 32 or char in '/\\<>:"|?*' for char in self.run_tag):
            errors.append("run_tag 不能包含路径分隔符或非法文件名字符")
        for name, value in asdict(self).items():
            if isinstance(value, float) and not math.isfinite(value):
                errors.append(f"{name} 必须为有限数值")
        if self.dispersion_start_v >= self.dispersion_stop_v:
            errors.append("色散扫描起点必须小于终点")
        for name in ("dispersion_steps", "dispersion_temp_switch_interval_points"):
            if type(getattr(self, name)) is not int:
                errors.append(f"{name} 必须为整数")
        try:
            dispersion_sample_count(self.hf2_demod_rate, self.dispersion_duration_s)
        except ValueError as exc:
            errors.append(str(exc))
        if self.hf2_demod_idx not in range(6):
            errors.append("HF2 解调器索引必须在 0 到 5 之间")
        if self.temp_switch != 5.0:
            errors.append("温控恢复电平必须为 5 V")
        if self.z_v_to_nt <= 0:
            errors.append("z_v_to_nt 必须为正数")
        if not self.gs200_current_ranges:
            errors.append("gs200_current_ranges 不能为空")
        if len({f"{value * 1000:g}" for value in self.gs200_current_ranges}) != len(self.gs200_current_ranges):
            errors.append("GS200 量程标签不能重复，以免覆盖同名原始数据")
        main_current_a = abs(self.main_magnetic_field) / 1000.0
        for current_range in self.gs200_current_ranges:
            if not math.isfinite(current_range) or current_range <= 0:
                errors.append("GS200 量程必须为有限正数")
            elif main_current_a > current_range * self.gs200_range_headroom:
                errors.append("GS200 量程及余量不足以容纳主场电流")
        for value in (self.dispersion_start_v, self.dispersion_stop_v):
            try:
                validate_safety_limit("Z_magnetic_field", float(value))
            except ValueError as exc:
                errors.append(str(exc))
        for key, half_amplitude, offset in (("Time_sequence", self.rf_gate_amplitude / 2, self.rf_gate_offset), ("Pump_modulation", self.pump_mod_amplitude / 2, 0.0)):
            for voltage in (offset - half_amplitude, offset + half_amplitude):
                try:
                    validate_safety_limit(key, voltage)
                except ValueError as exc:
                    errors.append(str(exc))
        return errors
