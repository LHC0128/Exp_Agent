"""强类型参数；外部键、单位和原脚本默认值保持兼容。"""
from dataclasses import dataclass
from typing import ClassVar, Literal
import math
from ...experiment_params import ExperimentParams, parameter


@dataclass(slots=True)
class BBPowerScanBurstParams(ExperimentParams):
    schema_version: ClassVar[int] = 2
    removed_temperature_keys: ClassVar[tuple[str, ...]] = ()
    system_settle_legacy_defaults: ClassVar[dict[str, float]] = {"TEMP_SWITCH_OFF_DELAY_S": 0.1}
    run_tag: str = parameter(default="rpump_eff", external_name="RUN_TAG", label="运行标签", group="basic")
    pump_power: list[float] = parameter(default_factory=lambda: [0.02 + i * 0.02 for i in range(10)], external_name="PUMP_POWER", label="Pump 功率控制电压列表", unit="V", group="basic")
    system_settle_s: float = parameter(default=0.1, external_name="SYSTEM_SETTLE_S", label="系统稳定时间", unit="s", group="basic", minimum=0)
    modulation_frequency_hz: float = parameter(default=90000, external_name="MODULATION_FREQUENCY_HZ", label="Pump 调制频率", unit="Hz", group="basic", minimum=1)
    fixed_duty_cycle: float = parameter(default=5, external_name="FIXED_DUTY_CYCLE", label="固定占空比", unit="%", group="basic", minimum=1, maximum=50)
    burst_cycles: int = parameter(default=1000, external_name="BURST_CYCLES", label="Burst 周期数", minimum=1)
    pre_baseline_time_s: float = parameter(default=0.005, external_name="PRE_BASELINE_TIME_S", label="触发前基线时间", unit="s", minimum=0.000001)
    post_dark_time_s: float = parameter(default=0.020, external_name="POST_DARK_TIME_S", label="暗弛豫窗口", unit="s", minimum=0.000001)
    burst_burst_period_s: float = parameter(default=0.5, external_name="BURST_BURST_PERIOD_S", label="Burst 触发间隔", unit="s", minimum=0.000001)
    acq_repeats: int = parameter(default=10, external_name="ACQ_REPEATS", label="每功率点重复次数", minimum=1)
    z_field_setpoint: float = parameter(default=9.31, external_name="Z_FIELD_SETPOINT", label="主磁场电流", unit="mA", group="basic")
    pump_rf_gate_high_v: float = parameter(default=5, external_name="PUMP_RF_GATE_HIGH_V", label="门控高电平", unit="V")
    pump_rf_gate_low_v: float = parameter(default=0, external_name="PUMP_RF_GATE_LOW_V", label="门控低电平", unit="V")
    probe_power: float = parameter(default=0.1, external_name="PROBE_POWER", label="Probe 功率控制电压", unit="V", group="basic")
    aom_carrier_freq_hz: float = parameter(default=100e6, external_name="AOM_CARRIER_FREQ_HZ", label="AOM 载波频率", unit="Hz", group="basic", minimum=1)
    aom_carrier_amplitude_v: float = parameter(default=0.18, external_name="AOM_CARRIER_AMPLITUDE_V", label="AOM 载波幅度", unit="Vpp", group="basic", minimum=0)
    aom_carrier_offset_v: float = parameter(default=0, external_name="AOM_CARRIER_OFFSET_V", label="AOM 载波偏置", unit="V")
    use_hf2_monitor: bool = parameter(default=False, external_name="USE_HF2_MONITOR", label="初始化可选锁相监控", description="沿用旧入口：仅连接并初始化主输入，不新增解调采集。")
    sds_sample_rate: float = parameter(default=1e6, external_name="SDS_SAMPLE_RATE", label="示波器请求采样率", unit="Sa/s", minimum=1)
    sds_pd_channel: int = parameter(default=1, external_name="SDS_PD_CHANNEL", label="PD 通道", minimum=1, maximum=4)
    sds_trig_channel: int = parameter(default=4, external_name="SDS_TRIG_CHANNEL", label="触发通道", minimum=1, maximum=4)
    sds_volt_scale_pd: float = parameter(default=0.1, external_name="SDS_VOLT_SCALE_PD", label="PD 初始垂直档位", unit="V/div", minimum=0.001)
    sds_volt_scale_trig: float = parameter(default=2, external_name="SDS_VOLT_SCALE_TRIG", label="触发通道垂直档位", unit="V/div", minimum=0.001)
    sds_trig_level: float = parameter(default=2.5, external_name="SDS_TRIG_LEVEL", label="触发阈值", unit="V")
    sds_trig_slope: Literal["RISing", "FALLing", "ALTernate"] = parameter(default="RISing", external_name="SDS_TRIG_SLOPE", label="触发边沿")
    vert_divs: int = parameter(default=4, external_name="VERT_DIVS", label="自适应档位半屏格数", minimum=1)
    scale_min: float = parameter(default=0.005, external_name="SCALE_MIN", label="最小自适应档位", unit="V/div", minimum=0.001)
    scale_max: float = parameter(default=10, external_name="SCALE_MAX", label="最大自适应档位", unit="V/div", minimum=0.001)
    temp_switch_off_v: float = parameter(default=0, external_name="TEMP_SWITCH_OFF_V", label="温控关闭电平（固定）", unit="V")
    temp_switch_on_v: float = parameter(default=5, external_name="TEMP_SWITCH_ON_V", label="温控恢复电平（固定）", unit="V")
    temp_switch_on_delay_s: float = parameter(default=2, external_name="TEMP_SWITCH_ON_DELAY_S", label="恢复温控等待时间", unit="s", minimum=0)
    tec_temperature: float = parameter(default=100, external_name="TEC_TEMPERATURE", label="TEC 目标温度", unit="°C", group="basic")
    sds_timebase_delay_s: float = parameter(default=0, external_name="SDS_TIMEBASE_DELAY_S", label="示波器时基延迟", unit="s")

    @property
    def burst_duration_s(self):
        return self.burst_cycles / self.modulation_frequency_hz

    @property
    def total_acq_s(self):
        return 2 * max(self.pre_baseline_time_s, self.burst_duration_s + self.post_dark_time_s)

    @classmethod
    def derive_external(cls, values):
        p = cls.from_external(values)
        return {"burst_duration_s": p.burst_duration_s, "total_acquisition_s": p.total_acq_s,
                "power_points": len(p.pump_power), "repeat_records": len(p.pump_power) * p.acq_repeats}

    def validate_model(self):
        errors = []
        for key, value in self.to_external().items():
            if isinstance(value, float) and not math.isfinite(value):
                errors.append(f"{key} 必须是有限数值")
        if not self.pump_power or any(not math.isfinite(v) for v in self.pump_power):
            errors.append("PUMP_POWER 必须是非空有限电压列表")
        if self.temp_switch_off_v != 0 or self.temp_switch_on_v != 5:
            errors.append("温控开关固定使用 0/5 V DC + Output ON")
        if self.sds_pd_channel == self.sds_trig_channel:
            errors.append("PD 与触发不能占用同一示波器通道")
        if not self.scale_min <= self.sds_volt_scale_pd <= self.scale_max:
            errors.append("PD 初始档位必须落在 SCALE_MIN/MAX 范围内")
        if self.modulation_frequency_hz > 0:
            if self.sds_sample_rate <= 2 * self.modulation_frequency_hz:
                errors.append("示波器采样率必须高于两倍 Pump 调制频率")
            if self.burst_burst_period_s <= self.burst_duration_s + self.post_dark_time_s:
                errors.append("Burst 间隔必须覆盖 burst 与暗弛豫窗口")
            half = self.total_acq_s / 2
            if self.sds_timebase_delay_s - half > -self.pre_baseline_time_s or self.sds_timebase_delay_s + half < self.burst_duration_s + self.post_dark_time_s:
                errors.append("示波器时间窗必须覆盖触发前基线、burst 和暗弛豫")
        return errors
