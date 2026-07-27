from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional
import numpy as np
import yaml


@dataclass
class ChannelConfig:
    """单通道配置."""
    number: int
    enabled: bool = True
    scale: float = 1.0       # V/div
    offset: float = 0.0      # V
    coupling: str = "DC"     # AC, DC, GND
    impedance: str = "ONEMeg"  # ONEMeg, FIFTy
    probe: float = 1.0       # 探头衰减比


@dataclass
class TriggerConfig:
    """触发配置."""
    mode: str = "AUTO"      # SINGle, NORMal, AUTO, FTRIG（AUTO=无触发模式）
    source: str = "C1"      # C1-C4, EXT
    type: str = "EDGE"
    slope: str = "RISing"   # RISing, FALLing, ALTernate
    level: float = 0.0      # V


@dataclass
class AcquisitionConfig:
    """完整采集配置.

    核心参数: 采样率 (Sa/s) 和 采样时间 (s)。
    时基 (s/div) 和 总点数由两者自动计算。
    总采样点数 = sampling_rate × sampling_time
    """
    # 采集
    sampling_rate: float = 5e5            # Sa/s
    sampling_time: float = 0.01           # s, 总采集时间窗口
    acquire_type: str = "NORMal"          # NORMal, PEAK, AVERage, ERES
    acquire_type_param: Optional[int] = None  # AVERage:<times> / ERES:<bits>
    memory_management: Optional[str] = None  # AUTO, FSRate, FMDepth；None 表示保持当前模式
    interpolation: str = "ON"

    # 时基 (timebase_scale = sampling_time / horizontal_divisions)
    timebase_delay: float = 0.0           # s
    horizontal_divisions: int = 10        # SDS 通常为 10 格

    # 采集控制
    acquire_delay: float = 0.5            # s, 触发启动后等待采集完成的时间

    # 通道与触发
    channels: List[ChannelConfig] = field(default_factory=lambda: [
        ChannelConfig(number=1, enabled=True),
        ChannelConfig(number=2, enabled=False),
        ChannelConfig(number=3, enabled=False),
        ChannelConfig(number=4, enabled=False),
    ])
    trigger: TriggerConfig = field(default_factory=TriggerConfig)

    @property
    def timebase_scale(self) -> float:
        """s/div = 采样总时间 / 格数"""
        return self.sampling_time / self.horizontal_divisions

    @property
    def total_points(self) -> int:
        """总采样点数 = 采样率 × 采样时间"""
        return int(self.sampling_rate * self.sampling_time)

    @classmethod
    def from_yaml(cls, path: str) -> "AcquisitionConfig":
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return cls._from_dict(data)

    @classmethod
    def _from_dict(cls, data: dict) -> "AcquisitionConfig":
        channels_data = data.pop("channels", [])
        trigger_data = data.pop("trigger", {})

        # 兼容旧配置: 弹出不再使用的字段
        data.pop("memory_depth", None)
        data.pop("timebase_scale", None)

        config = cls(**data)
        if channels_data:
            config.channels = [ChannelConfig(**ch) for ch in channels_data]
        if trigger_data:
            config.trigger = TriggerConfig(**trigger_data)
        return config

    def to_yaml(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(self.to_dict(), f, default_flow_style=False, allow_unicode=True)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["timebase_scale"] = self.timebase_scale  # 计算属性, 供旧代码兼容
        return d


@dataclass
class AcquisitionResult:
    """单通道采集结果."""
    channel: int
    raw_data: np.ndarray        # ADC 原始码
    voltage: np.ndarray         # 电压值 (V)
    time: np.ndarray            # 时间轴 (s)
    preamble_dict: Dict[str, Any]  # preamble 参数字典
    config_snapshot: Dict[str, Any]  # 采集时的配置快照
    timestamp: float = 0.0      # 采集时间戳
