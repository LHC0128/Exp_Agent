"""T1 参数；保留外部键，旧 T1_BOUNDS 拆为两个可独立编辑的标量。"""
from dataclasses import dataclass
from typing import ClassVar, Literal
from ...experiment_params import ExperimentParams, parameter


@dataclass(slots=True)
class T1CalibrationParams(ExperimentParams):
    schema_version: ClassVar[int] = 3
    system_settle_legacy_defaults: ClassVar[dict[str, float]] = {}
    removed_temperature_keys: ClassVar[tuple[str, ...]] = ()

    system_settle_s: float = parameter(default=0.5, external_name="SYSTEM_SETTLE_S", label="系统稳定时间", unit="s", group="basic", minimum=0)
    run_tag: str = parameter(default="test", external_name="RUN_TAG", label="运行标签", group="basic")
    do_power_scan: bool = parameter(default=True, external_name="DO_POWER_SCAN", label="扫描 Probe 控制电压", group="basic")
    pump_switch_freq: float = parameter(default=1.0, external_name="PUMP_SWITCH_FREQ", label="Pump 开关频率", unit="Hz", group="basic", minimum=0.000001)
    pump_mod_amplitude: float = parameter(default=0.18, external_name="PUMP_MOD_AMPLITUDE", label="Pump 载波幅度", unit="Vpp", group="basic", minimum=0.000001, safety_key="Pump_modulation")
    pump_prep_time: float = parameter(default=0.5, external_name="PUMP_PREP_TIME", label="CSS 制备时间", unit="s", group="basic", minimum=0)
    probe_power: float = parameter(default=0.05, external_name="PROBE_POWER", label="单点 Probe 控制电压", unit="V", group="basic", safety_key="Probe_laser_power")
    probe_power_list: list[float] = parameter(default_factory=lambda: [0.01+i*0.01 for i in range(10)], external_name="PROBE_POWER_LIST", label="Probe 控制电压列表", unit="V", group="basic")
    x_field_amplitude: float = parameter(default=5.0, external_name="X_FIELD_AMPLITUDE", label="X 场直流电压", unit="V", group="basic", safety_key="X_magnetic_field")
    scope_sampling_rate: float = parameter(default=10000.0, external_name="SCOPE_SAMPLING_RATE", label="示波器采样率", unit="Sa/s", minimum=1)
    acq_duration: float = parameter(default=0.1, external_name="ACQ_DURATION", label="采集时长", unit="s", group="basic", minimum=0.000001)
    acq_repeats: int = parameter(default=5, external_name="ACQ_REPEATS", label="每点重复帧数", group="basic", minimum=1)
    trigger_source: Literal["C4"] = parameter(default="C4", external_name="TRIGGER_SOURCE", label="同步触发源", description="CH1 为 PDB 差分，CH4 接 Time_sequence 同步下降沿；本实验不读取 CH4 波形。")
    trigger_level: float = parameter(default=2.5, external_name="TRIGGER_LEVEL", label="触发阈值", unit="V")
    scope_volt_scale: float = parameter(default=0.2, external_name="SCOPE_VOLT_SCALE", label="CH1 初始垂直档位", unit="V/div", minimum=0.000001)
    vert_divs: int = parameter(default=4, external_name="VERT_DIVS", label="单侧垂直格数", minimum=1)
    scale_min: float = parameter(default=0.002, external_name="SCALE_MIN", label="自动档位下限", unit="V/div", minimum=0.000001)
    scale_max: float = parameter(default=10.0, external_name="SCALE_MAX", label="自动档位上限", unit="V/div", minimum=0.000001)
    t1_min_s: float = parameter(default=0.0005, external_name="T1_MIN_S", label="T1 拟合下限", unit="s", minimum=0.000000001)
    t1_max_s: float = parameter(default=0.5, external_name="T1_MAX_S", label="T1 拟合上限", unit="s", minimum=0.000000001)
    tec_temperature: float = parameter(default=100.0, external_name="TEC_TEMPERATURE", label="气室目标温度", unit="°C", group="basic", safety_key="temperature")
    temp_switch_on_lag_s: float = parameter(default=2.0, external_name="TEMP_SWITCH_ON_LAG_S", label="恢复温控等待", unit="s", minimum=0, description="保留旧入口每帧恢复后的等待，与 SYSTEM_SETTLE_S 分开。")

    @classmethod
    def migrate_external(cls, values, schema_version):
        values = super(T1CalibrationParams, cls).migrate_external(values, schema_version)
        if "T1_BOUNDS" in values:
            bounds = values.pop("T1_BOUNDS")
            if not isinstance(bounds, (tuple, list)) or len(bounds) != 2:
                raise ValueError("T1_BOUNDS 必须包含两个秒数")
            values.setdefault("T1_MIN_S", bounds[0])
            values.setdefault("T1_MAX_S", bounds[1])
        if "EXPERIMENT_TYPE" in values:
            if values.pop("EXPERIMENT_TYPE") != "T1_Calibration":
                raise ValueError("EXPERIMENT_TYPE 必须为 T1_Calibration")
        return values

    @property
    def probe_values(self):
        return list(self.probe_power_list) if self.do_power_scan else [self.probe_power]

    @property
    def t1_bounds(self):
        return (self.t1_min_s, self.t1_max_s)

    def validate_model(self):
        errors = []
        if not self.run_tag.strip() or self.run_tag in (".", "..") or any(c in self.run_tag for c in '/\\:*?"<>|'):
            errors.append("运行标签不能为空或包含路径字符")
        if self.pump_switch_freq > 0:
            if self.pump_prep_time > 0.5/self.pump_switch_freq:
                errors.append("PUMP_PREP_TIME 不能超过 Pump 半周期")
            if self.acq_duration > 0.5/self.pump_switch_freq:
                errors.append("ACQ_DURATION 不能超过 Pump 半周期")
        if int(self.acq_duration*self.scope_sampling_rate) < 20:
            errors.append("采集窗口至少需要 20 个采样点")
        if not self.probe_values:
            errors.append("Probe 扫描列表不能为空")
        if not self.scale_min <= self.scope_volt_scale <= self.scale_max or self.scale_min >= self.scale_max:
            errors.append("垂直档位必须满足 SCALE_MIN ≤ SCOPE_VOLT_SCALE ≤ SCALE_MAX 且上下限不同")
        if not 0 < self.t1_min_s < self.t1_max_s:
            errors.append("T1 拟合上下限必须为递增的正秒数")
        if not 0 < self.trigger_level < 5:
            errors.append("触发阈值必须位于 0–5 V 同步电平之间")
        return errors

    @classmethod
    def derive_external(cls, values):
        p = cls.from_external(values)
        return {"Pump 半周期 (s)": 0.5/p.pump_switch_freq,
                "Probe 点数": len(p.probe_values), "计划接受帧数": len(p.probe_values)*p.acq_repeats,
                "请求采样点数": int(p.scope_sampling_rate*p.acq_duration)}
