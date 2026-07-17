"""静磁场灵敏度参数模型与动态表单元数据。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

import yaml

from ..common import find_project_root, load_yaml


def ui(
    label: str,
    unit: str = "",
    group: str = "basic",
    minimum: float | None = None,
    maximum: float | None = None,
    description: str = "",
) -> dict[str, Any]:
    return {
        "label": label,
        "unit": unit,
        "group": group,
        "minimum": minimum,
        "maximum": maximum,
        "description": description,
    }


@dataclass(slots=True)
class StaticSensitivityParams:
    run_tag: str = field(default="sens", metadata=ui("运行标签"))
    pump_laser_power: float = field(default=0.5, metadata=ui("Pump 光功率", "V", minimum=0, maximum=1))
    probe_laser_power: float = field(default=0.3, metadata=ui("Probe 光功率", "V", minimum=0, maximum=1))
    temperature: float = field(default=100.0, metadata=ui("气室温度", "°C", minimum=20, maximum=110))
    main_magnetic_field: float = field(default=9.3, metadata=ui("主磁场电流", "mA", minimum=0, maximum=10))
    x_magnetic_field: float = field(default=0.0, metadata=ui("X 磁场", "V", minimum=-10, maximum=10))
    y_magnetic_field: float = field(default=0.0, metadata=ui("Y 磁场", "V", minimum=-10, maximum=10))
    ramp_low: float = field(default=-0.3, metadata=ui("Z 扫场下限", "V", minimum=-10, maximum=10))
    ramp_high: float = field(default=0.3, metadata=ui("Z 扫场上限", "V", minimum=-10, maximum=10))
    ramp_freq: float = field(default=1.0, metadata=ui("Z 扫场频率", "Hz", minimum=0.001))
    ramp_symmetry: float = field(default=20, metadata=ui("斜波对称度", "%", minimum=0, maximum=100))
    daq_duration: float = field(default=1.0, metadata=ui("色散采集时长", "s", minimum=0.01))
    pump_mod_freq: float = field(default=90000.0, metadata=ui("Pump 调制频率", "Hz", minimum=1))
    pump_mod_amplitude: float = field(default=0.18, metadata=ui("Pump 载波幅度", "Vpp", minimum=0, maximum=0.18))
    pump_mod_duty: float = field(default=5.0, metadata=ui("Pump 占空比", "%", minimum=1, maximum=50))
    noise_n_avg: int = field(default=20, metadata=ui("噪声平均次数", minimum=1))
    noise_duration: float = field(default=1.0, metadata=ui("单次噪声时长", "s", minimum=0.01))
    temp_switch: float = field(default=5.0, metadata=ui("温控开关电平", "V", "advanced", 0, 5))
    time_sequence: float = field(default=10.0, metadata=ui("时序信号幅度", "Vpp", "advanced", -10, 10))
    time_sequence_2: float = field(default=0.0, metadata=ui("时序信号 2", "V", "advanced", -10, 10))
    rf_gate_amplitude: float = field(default=5.0, metadata=ui("RF 门控幅度", "Vpp", "advanced"))
    rf_gate_offset: float = field(default=2.5, metadata=ui("RF 门控偏置", "V", "advanced"))
    rf_gate_delay: float = field(default=0.0, metadata=ui("RF 门控延迟", "s", "advanced"))
    hf2_demod_idx: int = field(default=0, metadata=ui("HF2 Demod", group="advanced", minimum=0))
    hf2_demod_order: int = field(default=4, metadata=ui("HF2 阶数", group="advanced", minimum=1))
    hf2_signal_range: float = field(default=2.0, metadata=ui("HF2 输入量程", "V", "advanced", minimum=0))
    hf2_demod_rate: float = field(default=1000.0, metadata=ui("色散采样率", "Sa/s", "advanced", minimum=1))
    hf2_demod_tc: float = field(default=0.001, metadata=ui("色散时间常数", "s", "advanced", minimum=0))
    hf2_noise_rate: float = field(default=50000.0, metadata=ui("噪声采样率", "Sa/s", "advanced", minimum=1))
    hf2_noise_tc: float = field(default=1e-6, metadata=ui("噪声时间常数", "s", "advanced", minimum=0))
    temp_tolerance_c: float = field(default=1.0, metadata=ui("温度容差", "°C", "advanced", minimum=0))
    temp_stable_reads: int = field(default=3, metadata=ui("稳定连续读数", group="advanced", minimum=1))
    temp_poll_interval_s: float = field(default=5.0, metadata=ui("温度轮询间隔", "s", "advanced", minimum=0.1))
    max_temp_wait_s: float = field(default=1200.0, metadata=ui("最长温稳等待", "s", "advanced", minimum=1))
    gs200_current_ranges: list[float] = field(default_factory=lambda: [0.01], metadata=ui("GS200 量程列表", "A", "advanced"))
    gs200_range_headroom: float = field(default=1.2, metadata=ui("GS200 量程余量", group="advanced", minimum=1))
    gs200_range_settle_time: float = field(default=1.0, metadata=ui("GS200 换挡等待", "s", "advanced", minimum=0))
    z_v_to_nt: float = field(default=3517.0, metadata=ui("Z 场换算系数", "nT/V", "advanced", minimum=0))
    daq_trigger_channel: int = field(default=0, metadata=ui("DAQ 触发通道", group="advanced", minimum=0))
    daq_trigger_level: float = field(default=1.0, metadata=ui("DAQ 触发电平", "V", "advanced"))
    daq_trigger_slope: int = field(default=0, metadata=ui("DAQ 触发斜率", group="advanced"))

    @classmethod
    def from_dict(cls, values: dict[str, Any]) -> "StaticSensitivityParams":
        names = {item.name for item in fields(cls)}
        return cls(**{key: value for key, value in values.items() if key in names})

    @classmethod
    def from_yaml(cls, path: Path | None = None) -> "StaticSensitivityParams":
        root = find_project_root()
        return cls.from_dict(load_yaml(path or root / "params" / "static_sensitivity.yaml"))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_yaml(self, path: Path | None = None) -> Path:
        """将当前参数写入静磁场灵敏度默认配置。"""
        root = find_project_root()
        target = path or root / "params" / "static_sensitivity.yaml"
        temporary = target.with_suffix(f"{target.suffix}.tmp")
        content = yaml.safe_dump(self.to_dict(), allow_unicode=True, sort_keys=False)
        try:
            temporary.write_text(content, encoding="utf-8")
            temporary.replace(target)
        except PermissionError:
            # 部分受限运行环境允许更新已有配置，但禁止在 params 中新建临时文件。
            temporary.unlink(missing_ok=True)
            target.write_text(content, encoding="utf-8")
        return target

    @classmethod
    def schema(cls, values: "StaticSensitivityParams" | None = None) -> dict[str, Any]:
        current = (values or cls()).to_dict()
        result = []
        for item in fields(cls):
            result.append(
                {
                    "name": item.name,
                    "type": "array" if item.name == "gs200_current_ranges" else item.type.__class__.__name__,
                    "default": current[item.name],
                    **dict(item.metadata),
                }
            )
        return {"experiment": "static-sensitivity", "fields": result}
