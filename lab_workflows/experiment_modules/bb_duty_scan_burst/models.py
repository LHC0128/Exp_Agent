"""占空比扫描的显式参数；保留已发布 GUI/YAML 键和比例单位。"""
from __future__ import annotations
from dataclasses import dataclass
from typing import ClassVar
import math
from ...experiment_params import ExperimentParams, parameter


@dataclass
class BBDutyScanBurstParams(ExperimentParams):
    schema_version: ClassVar[int] = 2
    system_settle_legacy_defaults: ClassVar[dict] = {"TEMP_SWITCH_OFF_DELAY_S": 0.1}
    removed_temperature_keys: ClassVar[tuple[str, ...]] = ()

    system_settle_s: float = parameter(default=0.1, external_name="SYSTEM_SETTLE_S", label="系统稳定时间", unit="s", minimum=0, group="basic")
    run_tag: str = parameter(default="duty_scan", external_name="RUN_TAG", label="运行标签", group="basic")
    duty_cycle_list: list[float] = parameter(default_factory=lambda: [0.02 * i for i in range(1, 11)], external_name="DUTY_CYCLE_LIST", label="占空比列表", description="0～1 比例；写入仪器时换算为百分比", group="basic")
    fixed_pump_power_voltage: float = parameter(default=0.2, external_name="FIXED_PUMP_POWER_VOLTAGE", label="固定 Pump 控制电压", unit="V", group="basic")
    modulation_frequency_hz: float = parameter(default=90000.0, external_name="MODULATION_FREQUENCY_HZ", label="Pump 脉冲频率", unit="Hz", minimum=1, group="basic")
    burst_cycles: int = parameter(default=1000, external_name="BURST_CYCLES", label="Burst 周期数", minimum=1)
    pre_baseline_time_s: float = parameter(default=0.005, external_name="PRE_BASELINE_TIME_S", label="触发前基线时长", unit="s", minimum=1e-6)
    post_dark_time_s: float = parameter(default=0.020, external_name="POST_DARK_TIME_S", label="暗弛豫记录/拟合时长", unit="s", minimum=1e-6)
    burst_burst_period_s: float = parameter(default=0.5, external_name="BURST_BURST_PERIOD_S", label="Burst 触发周期", unit="s", minimum=1e-6)
    acq_repeats: int = parameter(default=10, external_name="ACQ_REPEATS", label="每点重复次数", minimum=1, group="basic")
    z_field_setpoint: float = parameter(default=9.31, external_name="Z_FIELD_SETPOINT", label="主场电流", unit="mA", group="basic")
    pump_rf_gate_high_v: float = parameter(default=5.0, external_name="PUMP_RF_GATE_HIGH_V", label="Pump 门控高电平", unit="V")
    pump_rf_gate_low_v: float = parameter(default=0.0, external_name="PUMP_RF_GATE_LOW_V", label="Pump 门控低电平", unit="V")
    probe_power: float = parameter(default=0.1, external_name="PROBE_POWER", label="Probe 控制电压", unit="V", group="basic")
    aom_carrier_freq_hz: float = parameter(default=100e6, external_name="AOM_CARRIER_FREQ_HZ", label="AOM 载波频率", unit="Hz", minimum=1)
    aom_carrier_amplitude_v: float = parameter(default=0.18, external_name="AOM_CARRIER_AMPLITUDE_V", label="AOM 载波幅度", unit="Vpp", minimum=0)
    aom_carrier_offset_v: float = parameter(default=0.0, external_name="AOM_CARRIER_OFFSET_V", label="AOM 载波偏置", unit="V")
    use_hf2_monitor: bool = parameter(default=False, external_name="USE_HF2_MONITOR", label="连接可选锁相监控", description="仅连接并初始化主输入，不采集锁相数据；主检测始终是 SDS")
    sds_sample_rate: float = parameter(default=1e6, external_name="SDS_SAMPLE_RATE", label="SDS 请求采样率", unit="Sa/s", minimum=1)
    sds_pd_channel: int = parameter(default=1, external_name="SDS_PD_CHANNEL", label="SDS PD 通道", minimum=1, maximum=4)
    sds_trig_channel: int = parameter(default=4, external_name="SDS_TRIG_CHANNEL", label="SDS SYNC 触发通道", minimum=1, maximum=4)
    sds_volt_scale_pd: float = parameter(default=0.1, external_name="SDS_VOLT_SCALE_PD", label="PD 初始垂直档位", unit="V/div", minimum=1e-6)
    sds_volt_scale_trig: float = parameter(default=2.0, external_name="SDS_VOLT_SCALE_TRIG", label="触发通道垂直档位", unit="V/div", minimum=1e-6)
    sds_trig_level: float = parameter(default=2.5, external_name="SDS_TRIG_LEVEL", label="SDS 触发电平", unit="V")
    sds_trig_slope: str = parameter(default="RISing", external_name="SDS_TRIG_SLOPE", label="SYNC 触发斜率", options=(("RISing", "上升沿"),), description="上升沿定义 Burst 起点；不自动更改接线")
    vert_divs: int = parameter(default=4, external_name="VERT_DIVS", label="PD 半屏格数", minimum=1)
    scale_min: float = parameter(default=0.005, external_name="SCALE_MIN", label="最小垂直档位", unit="V/div", minimum=1e-6)
    scale_max: float = parameter(default=10.0, external_name="SCALE_MAX", label="最大垂直档位", unit="V/div", minimum=1e-6)
    temp_switch_off_v: float = parameter(default=0.0, external_name="TEMP_SWITCH_OFF_V", label="温控关闭电压", unit="V", minimum=0, maximum=0, read_only=True)
    temp_switch_on_v: float = parameter(default=5.0, external_name="TEMP_SWITCH_ON_V", label="温控恢复电压", unit="V", minimum=5, maximum=5, read_only=True)
    temp_switch_on_delay_s: float = parameter(default=2.0, external_name="TEMP_SWITCH_ON_DELAY_S", label="恢复温控等待时间", unit="s", minimum=0)
    tec_temperature: float = parameter(default=100.0, external_name="TEC_TEMPERATURE", label="TEC 目标温度", unit="°C", group="basic")
    sds_timebase_delay_s: float = parameter(default=0.0, external_name="SDS_TIMEBASE_DELAY_S", label="SDS 水平延迟", unit="s", description="实际波形必须覆盖基线、Burst 和暗弛豫")

    @property
    def burst_duration_s(self):
        return self.burst_cycles / self.modulation_frequency_hz

    @property
    def total_acq_s(self):
        return 2 * max(self.pre_baseline_time_s, self.burst_duration_s + self.post_dark_time_s)

    def validate_model(self):
        errors = []
        for key, value in self.to_external().items():
            if isinstance(value, (int, float)) and not isinstance(value, bool) and not math.isfinite(value):
                errors.append(f"{key} 必须是有限数值")
        for key in ("burst_cycles", "acq_repeats", "sds_pd_channel", "sds_trig_channel", "vert_divs"):
            if type(getattr(self, key)) is not int:
                errors.append(f"{key} 必须是整数")
        if not self.duty_cycle_list or any(type(v) not in (int, float) or not math.isfinite(v) or not 0 < v < 1 for v in self.duty_cycle_list):
            errors.append("DUTY_CYCLE_LIST 必须为非空有限占空比列表（0～1 比例）")
        if self.modulation_frequency_hz > 0 and self.burst_cycles > 0:
            if self.burst_burst_period_s <= self.total_acq_s:
                errors.append("BURST_BURST_PERIOD_S 必须大于完整采集窗口，避免相邻 Burst 重叠")
            if self.sds_sample_rate <= 2 * self.modulation_frequency_hz:
                errors.append("SDS 请求采样率必须大于两倍 Pump 脉冲频率")
        if self.sds_pd_channel == self.sds_trig_channel:
            errors.append("SDS PD 与 SYNC 触发不能占用同一通道")
        if not 0 < self.scale_min <= self.sds_volt_scale_pd <= self.scale_max:
            errors.append("PD 初始档位必须位于 SCALE_MIN～SCALE_MAX")
        if self.pump_rf_gate_low_v != 0 or self.pump_rf_gate_high_v != 5:
            errors.append("RF 开关使用固定 TTL 0/5 V，不以门控电平扫描 Pump 强度")
        if self.temp_switch_off_v != 0 or self.temp_switch_on_v != 5:
            errors.append("温控开关必须为 0/5 V DC，输出始终 ON")
        if self.sds_trig_slope != "RISing":
            errors.append("SYNC 必须使用上升沿定义 Burst 起点")
        if not self.run_tag.strip() or any(c in self.run_tag for c in ("/", chr(92), ":", chr(0))):
            errors.append("运行标签不能为空或包含路径分隔符")
        return errors

    @classmethod
    def derive_external(cls, values):
        p = cls.from_external(values)
        return dict(burst_duration_s=p.burst_duration_s, total_acq_s=p.total_acq_s,
                    total_records=len(p.duty_cycle_list) * p.acq_repeats)
