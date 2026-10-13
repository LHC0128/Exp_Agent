from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List
import numpy as np
import yaml


@dataclass
class SignalInputConfig:
    """信号输入模块配置.

    HF2 有 2 个信号输入模块 (sigins/0, sigins/1)。
    可设置节点: range, ac, imp50, diff。

    参数
    ----------
    input_index : int
        输入模块索引 (0 或 1)
    range : float
        输入量程 (V)
    ac_coupling : bool
        True=AC 耦合, False=DC 耦合
    diff : bool
        True=差分输入, False=单端输入
    impedance : int
        输入阻抗（50 Ω 或高阻；默认关闭 50 Ω 终端）
    """

    input_index: int = 0
    range: float = 1.0
    ac_coupling: bool = False
    diff: bool = False
    impedance: int = 1_000_000

    @classmethod
    def from_yaml(cls, path: str) -> "SignalInputConfig":
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return cls(**data)

    def to_yaml(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(self.to_dict(), f, default_flow_style=False, allow_unicode=True)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class OscillatorConfig:
    """振荡器模块配置.

    HF2 多频接口有 6 个振荡器；MFLI 由 MF-MD 选件决定可用数量。

    参数
    ----------
    osc_index : int
        振荡器索引（按所选设备能力校验）
    frequency : float
        参考频率 (Hz)，仅在 manual 模式下有效
    source : str
        本接口支持 "manual"；外部解调参考需另行配置 PLL/ExtRef
    """

    osc_index: int = 0
    frequency: float = 100e3
    source: str = "manual"

    @classmethod
    def from_yaml(cls, path: str) -> "OscillatorConfig":
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return cls(**data)

    def to_yaml(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(self.to_dict(), f, default_flow_style=False, allow_unicode=True)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DemodulatorConfig:
    """解调器模块配置（不含输入/输出配置）.

    HF2 有 6 个解调器模块 (demods/0 ~ demods/5)。
    每个解调器从指定信号输入模块获取信号，从指定振荡器获取参考。

    参数
    ----------
    demod_index : int
        解调器模块索引 (0-7)
    enable : bool
        数据传输使能（将解调结果发送到计算机）
    rate : float
        输出数据速率 (Sa/s)
    input_channel : int
        主电压输入索引；其他输入来源用 input_source 明确指定
    osc_select : int
        参考信号来源，选择振荡器索引
    harmonic : int
        解调谐波 (1=基波)
    time_constant : float
        低通时间常数 (s)，决定带宽
    order : int
        低通滤波器阶数
    phase : float
        相位偏移 (度)
    """

    demod_index: int = 0
    enable: bool = True
    rate: float = 10e3
    input_channel: int = 0
    osc_select: int = 0
    harmonic: int = 1
    time_constant: float = 0.001
    order: int = 4
    phase: float = 0.0
    input_source: str | None = None
    """明确输入物理含义：voltage0/voltage1/current0/aux_input0/aux_input1。
    未指定时 input_channel 只表示主电压输入索引，不能在 MFLI 上把 1 当电流输入。
    """

    @classmethod
    def from_yaml(cls, path: str) -> "DemodulatorConfig":
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return cls(**data)

    def to_yaml(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(self.to_dict(), f, default_flow_style=False, allow_unicode=True)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SignalOutputConfig:
    """信号输出模块配置（与辅助输出 AUXOUTS 不同）.

    HF2 有 2 个信号输出通道 (sigouts/0, sigouts/1)。

    参数
    ----------
    output_index : int
        输出通道索引 (0 或 1)
    range : float
        输出量程 (V)
    offset : float
        输出直流偏置 (V)
    enable : bool
        是否使能输出
    add : bool
        加操作模式，True=叠加到当前输出, False=独立输出
    """

    output_index: int = 0
    range: float = 1.0
    offset: float = 0.0
    enable: bool = True
    add: bool = False

    @classmethod
    def from_yaml(cls, path: str) -> "SignalOutputConfig":
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return cls(**data)

    def to_yaml(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(self.to_dict(), f, default_flow_style=False, allow_unicode=True)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DAQConfig:
    """DAQ 模块采集配置.

    参数
    ----------
    device : str
        设备 ID
    trigger_type : int
        触发模式: 0=连续, 1=边沿触发
    trigger_channel : int
        Aux Input 通道（0/1），trigger_type=1 时有效
    trigger_level : float
        触发电平 (V), trigger_type=1 时有效
    trigger_slope : int
        触发边沿: 0=上升沿, 1=下降沿, trigger_type=1 时有效
    trigger_delay : float
        触发后延迟采集时间 (s), trigger_type=1 时有效
    duration : float
        采集时长 (s)
    grid_cols : int
        每行样本数
    grid_rows : int
        行数 (每次触发的记录数)
    grid_mode : int
        网格模式: 1=最近邻, 2=线性插值, 4=原采样网格
    signal_paths : list
        订阅的信号路径后缀列表
        (例如 ['sample.r', 'sample.x', 'sample.y', 'sample.theta'])
    """

    device: str = ""
    trigger_type: int = 0
    trigger_channel: int = 0
    trigger_level: float = 1.5
    trigger_slope: int = 0  # 0=rising, 1=falling
    trigger_delay: float = 0.0
    duration: float = 0.1
    grid_cols: int = 500
    grid_rows: int = 1
    grid_mode: int = 2
    signal_paths: List[str] = field(default_factory=lambda: [
        "sample.r", "sample.x", "sample.y", "sample.theta",
    ])
    extra_paths: List[str] = field(default_factory=list)
    """额外订阅的完整节点路径列表（非解调器信号），如 ['/dev18246/auxins/0/sample.AuxIn0']"""
    record_flags: int = 0
    """记录标志；0 保持默认检测行为，4 在检测到丢样时抛出异常。"""

    @classmethod
    def from_yaml(cls, path: str) -> "DAQConfig":
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return cls(**data)

    def to_yaml(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(self.to_dict(), f, default_flow_style=False, allow_unicode=True)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AuxOutConfig:
    """辅助输出配置（与信号输出 SIGOUTS 不同）.

    参数
    ----------
    output_select : int
        信号源选择:
        0=解调器 X, 1=解调器 Y, 2=解调器 R, 3=解调器 Theta
    scale : float
        输出电压缩放
    offset : float
        输出电压偏置 (V)
    demod_index : int
        关联的解调器索引
    aux_index : int
        辅助输出通道索引
    """

    output_select: int = 2  # 默认输出 R
    scale: float = 1.0
    offset: float = 0.0
    demod_index: int = 0
    aux_index: int = 0
    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DAQResult:
    """单路 DAQ 采集结果，格式与 sds_acquisition.AcquisitionResult 对齐.

    参数
    ----------
    signal_name : str
        信号名称，如 "sample.r", "sample.x"
    demod_index : int
        解调器索引
    values : np.ndarray
        信号值 (一维数组)
    time : np.ndarray
        时间轴 (s)
    config_snapshot : dict
        采集时的配置快照
    """

    signal_name: str = ""
    demod_index: int = 0
    values: np.ndarray = field(default_factory=lambda: np.array([]))
    time: np.ndarray = field(default_factory=lambda: np.array([]))
    config_snapshot: Dict[str, Any] = field(default_factory=dict)
    record_slices: list[tuple[int, int]] = field(default_factory=list)
    """每行/每次触发在展平数组中的边界，防止将独立记录误当连续时序。"""
