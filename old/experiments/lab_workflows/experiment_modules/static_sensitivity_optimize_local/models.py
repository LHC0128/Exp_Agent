"""局部优化参数；旧外部键保留，单点采用静磁场 DC 采集契约。"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
from typing import Any, ClassVar

from ...experiment_params import ExperimentParams, parameter
from ..static_sensitivity.models import StaticSensitivityParams

BASELINE = {"Pump_laser_power": 0.3, "Probe_laser_power": 0.1, "PUMP_MOD_DUTY": 5.0,
    "temperature": 100.0, "main_magnetic_field": 1.023, "X_magnetic_field": 0.0, "Y_magnetic_field": 0.0}
CHILD_KEYS = dict(Pump_laser_power="pump_laser_power", Probe_laser_power="probe_laser_power",
    PUMP_MOD_DUTY="pump_mod_duty", temperature="temperature", main_magnetic_field="main_magnetic_field",
    X_magnetic_field="x_magnetic_field", Y_magnetic_field="y_magnetic_field")
SEQUENCE = [{"parameter": "Pump_laser_power", "values": [.20, .25, .30, .35, .40]},
    {"parameter": "Probe_laser_power", "values": [.05, .08, .10, .12, .15]},
    {"parameter": "PUMP_MOD_DUTY", "values": [3, 4, 5, 6, 8]}]


@dataclass(slots=True)
class StaticSensitivityOptimizeLocalParams(ExperimentParams):
    schema_version = 3
    system_settle_legacy_defaults: ClassVar[dict[str, float]] = {"LEGACY_SCAN_WAIT": 2.0}
    removed_temperature_keys: ClassVar[tuple[str, ...]] = ("MAX_TEMP_WAIT_S", "TEMP_TOLERANCE_C")
    run_tag: str = parameter(default="opt_local", external_name="RUN_TAG_PREFIX", label="批次标签", group="basic")
    baseline_params: dict[str, float] = parameter(default_factory=lambda: dict(BASELINE), external_name="BASELINE_PARAMS", label="基准参数", group="basic", description="每个候选点均从此基准出发，只覆盖一个变量；不累计前一维最优值。光功率/补偿场单位 V，主场 mA，温度 °C，占空比 %。")
    optimize_sequence: str = parameter(default=json.dumps(SEQUENCE), external_name="OPTIMIZE_SEQUENCE", label="局部扫描序列（JSON）", group="basic", description='按顺序填写 [{"parameter":"Pump_laser_power","values":[0.2,0.3]}]；可扫描基准中的任意变量，数组顺序保留，基准重复点不去重。兼容旧 YAML 数组。')
    system_settle_s: float = parameter(default=2.0, external_name="SYSTEM_SETTLE_S", label="系统稳定时间", unit="s", group="basic", minimum=0)
    dispersion_start_v: float = parameter(default=-.5, external_name="RAMP_LOW", label="Z 直流扫描起点", unit="V", group="basic", safety_key="Z_magnetic_field")
    dispersion_stop_v: float = parameter(default=.5, external_name="RAMP_HIGH", label="Z 直流扫描终点", unit="V", group="basic", safety_key="Z_magnetic_field")
    dispersion_steps: int = parameter(default=41, external_name="DISPERSION_STEPS", label="色散扫描点数", group="basic", minimum=7)
    dispersion_duration_s: float = parameter(default=.1, external_name="DISPERSION_DURATION_S", label="色散每点采集时间", unit="s", group="basic", minimum=0)
    dispersion_temp_switch_interval_points: int = parameter(default=5, external_name="DISPERSION_TEMP_SWITCH_INTERVAL_POINTS", label="色散温控恢复间隔点数", minimum=0, description="0 为整段一组；否则按指定点数恢复，末组同样保留。")
    dispersion_temp_switch_on_settle_s: float = parameter(default=3.0, external_name="DISPERSION_TEMP_SWITCH_ON_SETTLE_S", label="温控恢复等待", unit="s", minimum=0)
    pump_mod_freq: float = parameter(default=10000., external_name="PUMP_MOD_FREQ", label="Pump 调制频率", unit="Hz", group="basic", minimum=1)
    pump_mod_amplitude: float = parameter(default=.18, external_name="PUMP_MOD_AMPLITUDE", label="Pump 载波幅度", unit="Vpp", safety_key="Pump_modulation", minimum=0, maximum=.18)
    rf_gate_amplitude: float = parameter(default=5., external_name="RF_GATE_AMPLITUDE", label="Pump 门控幅度", unit="Vpp", minimum=0)
    rf_gate_offset: float = parameter(default=2.5, external_name="RF_GATE_OFFSET", label="Pump 门控偏置", unit="V")
    hf2_demod_idx: int = parameter(default=0, external_name="HF2_DEMOD_IDX", label="锁相解调器索引", minimum=0)
    hf2_demod_order: int = parameter(default=4, external_name="HF2_DEMOD_ORDER", label="解调滤波阶数", minimum=1)
    hf2_signal_range: float = parameter(default=2., external_name="HF2_SIGNAL_RANGE", label="锁相输入量程", unit="V", minimum=0)
    hf2_demod_rate: float = parameter(default=1000., external_name="HF2_DEMOD_RATE", label="色散采样率", unit="Sa/s", minimum=1)
    hf2_demod_tc: float = parameter(default=.001, external_name="HF2_DEMOD_TC", label="色散时间常数", unit="s", minimum=0)
    hf2_noise_rate: float = parameter(default=50000., external_name="HF2_NOISE_RATE", label="噪声采样率", unit="Sa/s", minimum=1)
    hf2_noise_tc: float = parameter(default=1e-6, external_name="HF2_NOISE_TC", label="噪声时间常数", unit="s", minimum=0)
    noise_n_avg: int = parameter(default=20, external_name="NOISE_N_AVG", label="噪声重复次数", group="basic", minimum=1)
    noise_duration: float = parameter(default=1., external_name="NOISE_DURATION", label="每段噪声时长", unit="s", group="basic", minimum=.01)
    gs200_current_range_a: float = parameter(default=.001, external_name="GS200_CURRENT_RANGE_A", label="GS200 电流量程", unit="A", minimum=0)
    gs200_range_settle_time: float = parameter(default=1., external_name="GS200_RANGE_SETTLE_TIME", label="GS200 换挡等待", unit="s", minimum=0)
    z_v_to_nt: float = parameter(default=3517., external_name="Z_V_TO_NT", label="Z 场换算系数", unit="nT/V", minimum=0, description="用于每个 DC 子运行的即时分析；批次离线分析只重建排名，不重算子运行。")
    # 仅接受旧配置；DC 模式不使用 RAMP 频率、对称度、触发或整帧时长。
    scan_variable: str = parameter(default="Z_magnetic_field", external_name="SCAN_VARIABLE", label="旧扫描轴", visible=False)
    ramp_freq: float = parameter(default=1., external_name="RAMP_FREQ", label="旧 RAMP 频率", visible=False)
    ramp_symmetry: float = parameter(default=20., external_name="RAMP_SYMMETRY", label="旧 RAMP 对称度", visible=False)
    daq_duration: float = parameter(default=1., external_name="DAQ_DURATION", label="旧 RAMP 整帧时长", visible=False)
    daq_trigger_channel: int = parameter(default=0, external_name="DAQ_TRIGGER_CHANNEL", label="旧触发通道", visible=False)
    daq_trigger_level: float = parameter(default=1., external_name="DAQ_TRIGGER_LEVEL", label="旧触发电平", visible=False)
    daq_trigger_slope: int = parameter(default=0, external_name="DAQ_TRIGGER_SLOPE", label="旧触发方向", visible=False)

    @classmethod
    def migrate_external(cls, values, schema_version):
        values = ExperimentParams.migrate_external.__func__(cls, values, schema_version)
        baseline = dict(values.get("BASELINE_PARAMS", BASELINE))
        if baseline.pop("Temp_Switch", 5.) != 5.:
            raise ValueError("温控恢复电平必须为 5 V")
        for key in ("Time_sequence", "Time_sequence_2"):
            baseline.pop(key, None)
        if "BASELINE_PARAMS" in values:
            values["BASELINE_PARAMS"] = baseline
        if "OPTIMIZE_SEQUENCE" in values:
            sequence = values["OPTIMIZE_SEQUENCE"]
            sequence = json.loads(sequence) if isinstance(sequence, str) else sequence
            if not isinstance(sequence, list) or not all(isinstance(item, dict) for item in sequence):
                raise ValueError("OPTIMIZE_SEQUENCE 必须为对象数组")
            sequence = [dict(item) for item in sequence]
            waits = [item.pop("stabilize_time") for item in sequence if "stabilize_time" in item]
            if waits and "SYSTEM_SETTLE_S" not in values:
                values["SYSTEM_SETTLE_S"] = max(waits)
            values["OPTIMIZE_SEQUENCE"] = json.dumps(sequence, ensure_ascii=False)
        return values

    def sequence(self) -> list[dict[str, Any]]:
        sequence = json.loads(self.optimize_sequence)
        if not isinstance(sequence, list) or not sequence:
            raise ValueError("局部扫描序列不能为空")
        for item in sequence:
            if not isinstance(item, dict) or set(item) != {"parameter", "values"}:
                raise ValueError("每个扫描维度必须且只能包含 parameter 和 values")
            if item["parameter"] not in CHILD_KEYS:
                raise ValueError("未知或不参与测量的扫描参数")
            if not isinstance(item["values"], list) or not item["values"]:
                raise ValueError("每个扫描维度至少需要一个候选值")
            for value in item["values"]:
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ValueError("候选值必须为有限数值")
        return sequence

    def point_parameters(self, parameter_name=None, value=None, *, run_tag="opt_local_point"):
        baseline = dict(self.baseline_params)
        if parameter_name is not None:
            baseline[parameter_name] = value
        return StaticSensitivityParams.from_external({**{CHILD_KEYS[key]: item for key, item in baseline.items()},
            "run_tag": run_tag, "SYSTEM_SETTLE_S": self.system_settle_s,
            "ramp_low": self.dispersion_start_v, "ramp_high": self.dispersion_stop_v,
            "dispersion_steps": self.dispersion_steps, "dispersion_duration_s": self.dispersion_duration_s,
            "dispersion_temp_switch_interval_points": self.dispersion_temp_switch_interval_points,
            "dispersion_temp_switch_on_settle_s": self.dispersion_temp_switch_on_settle_s,
            "pump_mod_freq": self.pump_mod_freq, "pump_mod_amplitude": self.pump_mod_amplitude,
            "rf_gate_amplitude": self.rf_gate_amplitude, "rf_gate_offset": self.rf_gate_offset,
            "hf2_demod_idx": self.hf2_demod_idx, "hf2_demod_order": self.hf2_demod_order,
            "hf2_signal_range": self.hf2_signal_range, "hf2_demod_rate": self.hf2_demod_rate,
            "hf2_demod_tc": self.hf2_demod_tc, "hf2_noise_rate": self.hf2_noise_rate,
            "hf2_noise_tc": self.hf2_noise_tc, "noise_n_avg": self.noise_n_avg,
            "noise_duration": self.noise_duration, "gs200_current_ranges": [self.gs200_current_range_a],
            "gs200_range_settle_time": self.gs200_range_settle_time, "z_v_to_nt": self.z_v_to_nt})

    def validate_model(self):
        try:
            if set(self.baseline_params) != set(BASELINE):
                raise ValueError("BASELINE_PARAMS 必须完整包含七个有效基准参数")
            if self.scan_variable != "Z_magnetic_field":
                raise ValueError("色散扫描轴只能为 Z_magnetic_field")
            sequence = self.sequence()
            errors = self.point_parameters(run_tag=self.run_tag).validate()
            for item in sequence:
                for value in item["values"]:
                    errors.extend(self.point_parameters(item["parameter"], value).validate())
            return list(dict.fromkeys(errors))
        except (KeyError, TypeError, ValueError) as exc:
            return [str(exc)]

    @classmethod
    def derive_external(cls, values):
        params = cls.from_external(values)
        count = sum(len(item["values"]) for item in params.sequence())
        return {"候选点数量": count, "每候选 DC 点数": params.dispersion_steps,
            "DC 点总数": count * params.dispersion_steps, "测量方式": "DC step / X,Y,R median; Z OFF Y noise"}
