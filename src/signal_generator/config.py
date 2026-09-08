from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional
import yaml


@dataclass
class SignalGeneratorConfig:
    """单台信号发生器配置.

    包含 VISA 资源地址和通道参数，支持 YAML 序列化。
    适用于 DG4000 系列函数/任意波形发生器。

    参数
    ----------
    resource : str
        VISA 资源字符串，例如 "USB0::0x1AB1::0x0641::DG4X00000000::INSTR"
    channel : int
        绑定的通道号 (1 或 2)
    """

    # ---- 仪器标识 ----
    resource: str = ""
    channel: int = 1
    label: str = ""                     # 可选标签，例如 "参考信号", "调制源"

    # ---- 波形 ----
    shape: str = "SINusoid"             # SINusoid / SQUare / RAMP / PULSe / NOISe / CUSTom / USER / DC
    frequency: float = 1000.0           # Hz
    amplitude: float = 5.0              # Vpp (取决于 voltage_unit)
    offset: float = 0.0                 # V
    phase: float = 0.0                  # 度
    voltage_unit: str = "VPP"           # VPP / VRMS / DBM

    # ---- 方波 ----
    square_duty_cycle: float = 50.0     # % (仅 SQUare)

    # ---- 斜波 ----
    ramp_symmetry: float = 50.0         # % (仅 RAMP)

    # ---- 脉冲 ----
    pulse_width: Optional[float] = None   # s
    pulse_duty_cycle: Optional[float] = None  # %
    pulse_delay: float = 0.0            # s
    pulse_leading: Optional[float] = None  # s, 上升沿
    pulse_trailing: Optional[float] = None  # s, 下降沿

    # ---- 任意波 ----
    arb_points: int = 1000
    arb_data: Optional[List[float]] = None  # 归一化 [-1, +1]

    # ---- 输出控制 ----
    output_enabled: bool = False
    load: float = 50.0                  # Ω (或用 "INF" 表示高阻)
    polarity: str = "NORMal"            # NORMal / INVerted
    sync_enabled: bool = False

    # ---- 调制 ----
    mod_enabled: bool = False
    mod_type: str = "AM"                # AM / FM / PM / FSKey / PWM / ...
    mod_am_depth: float = 80.0          # % (AM)
    mod_fm_deviation: float = 1000.0    # Hz (FM)
    mod_pm_deviation: float = 90.0      # 度 (PM)
    mod_fsk_frequency: float = 5000.0   # Hz (FSK)
    mod_fsk_rate: float = 100.0         # Hz (FSK)
    mod_internal_freq: float = 100.0    # Hz, 内部调制频率
    mod_internal_func: str = "SINusoid" # 内部调制波形

    # ---- 扫描 ----
    sweep_enabled: bool = False
    sweep_time: float = 1.0             # s
    sweep_spacing: str = "LINear"       # LINear / LOGarithmic
    sweep_start: float = 100.0          # Hz
    sweep_stop: float = 10000.0         # Hz

    # ---- 脉冲串 ----
    burst_enabled: bool = False
    burst_mode: str = "TRIGgered"       # TRIGgered / GATed
    burst_ncycles: int = 5
    burst_period: float = 0.1           # s

    # ---- 谐波 ----
    harmonic_type: str = "EVEN"         # EVEN / ODD / ALL / USER
    harmonic_order: int = 2
    harmonic_amplitude: float = 0.5     # V

    # ---- 参考时钟 ----
    ref_clock_source: str = "INTernal"  # INTernal / EXTernal

    @classmethod
    def from_yaml(cls, path: str) -> "SignalGeneratorConfig":
        """从 YAML 文件加载配置."""
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return cls(**data)

    def to_yaml(self, path: str) -> None:
        """保存配置到 YAML 文件."""
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(self.to_dict(), f,
                      default_flow_style=False, allow_unicode=True)

    def to_dict(self) -> dict:
        """转为字典."""
        d = asdict(self)
        # 移除空列表/None 以简化 YAML
        if d.get("arb_data") is None:
            del d["arb_data"]
        return d

    # ---- 便捷方法 ----

    def apply_to(self, instrument) -> None:
        """将配置应用到 DG4000Instrument 实例.

        Parameters
        ----------
        instrument : DG4000Instrument
            已连接的仪器实例。其绑定的 channel 会被覆盖为 config.channel。
        """
        ch = self.channel

        # 若配置中有资源且与仪器不同，切换连接 (忽略)
        # 直接应用参数

        # 波形形状与参数
        if self.shape == "DC":
            # 统一通过型号驱动的 DC 专用设置路径下发电平。
            instrument.set_shape("DC", channel=ch)
            instrument.set_dc_voltage(self.offset, channel=ch)
        elif self.shape == "NOISe":
            instrument.apply_wave("NOISe", channel=ch,
                                  amp=self.amplitude, offset=self.offset)
        elif self.shape == "PULSe":
            kw = dict(freq=self.frequency, amp=self.amplitude,
                      offset=self.offset)
            if self.pulse_delay:
                kw["delay"] = self.pulse_delay
            instrument.apply_wave("PULSe", channel=ch, **kw)
            if self.pulse_width is not None:
                instrument.set_pulse_width(self.pulse_width, channel=ch)
            if self.pulse_duty_cycle is not None:
                instrument.set_pulse_dcycle(self.pulse_duty_cycle, channel=ch)
            if self.pulse_leading is not None:
                instrument.set_pulse_leading(self.pulse_leading, channel=ch)
            if self.pulse_trailing is not None:
                instrument.set_pulse_trailing(self.pulse_trailing, channel=ch)
        elif self.shape == "CUSTom" and self.arb_data:
            instrument.setup_arbitrary(
                self.arb_data, freq=self.frequency,
                amplitude=self.amplitude, offset=self.offset,
                channel=ch)
            return  # setup_arbitrary 已打开输出
        else:
            instrument.apply_wave(self.shape, channel=ch,
                                  freq=self.frequency, amp=self.amplitude,
                                  offset=self.offset, phase=self.phase)

        # 波形特殊参数
        if self.shape == "SQUare":
            instrument.set_square_dcycle(self.square_duty_cycle, channel=ch)
        elif self.shape == "RAMP":
            instrument.set_ramp_symmetry(self.ramp_symmetry, channel=ch)

        # 输出控制
        instrument.set_output(self.output_enabled, channel=ch)
        instrument.set_output_load(self.load, channel=ch)
        instrument.set_output_polarity(self.polarity, channel=ch)
        if instrument.get_output(ch) != self.output_enabled:
            instrument.set_output(self.output_enabled, channel=ch)

        # 调制
        if self.mod_enabled:
            instrument.set_mod_type(self.mod_type, channel=ch)
            if self.mod_type == "AM":
                instrument.set_mod_am_depth(self.mod_am_depth, channel=ch)
                instrument.set_mod_am_internal_freq(
                    self.mod_internal_freq, channel=ch)
            elif self.mod_type == "FM":
                instrument.set_mod_fm_deviation(
                    self.mod_fm_deviation, channel=ch)
                instrument.set_mod_fm_internal_freq(
                    self.mod_internal_freq, channel=ch)
            elif self.mod_type == "PM":
                instrument.set_mod_pm_deviation(
                    self.mod_pm_deviation, channel=ch)
                instrument.set_mod_pm_internal_freq(
                    self.mod_internal_freq, channel=ch)
            elif self.mod_type == "FSKey":
                instrument.set_mod_fsk_frequency(
                    self.mod_fsk_frequency, channel=ch)
                instrument.set_mod_fsk_rate(self.mod_fsk_rate, channel=ch)
            instrument.set_mod_state(True, channel=ch)

        # 扫描 (互斥)
        if self.sweep_enabled:
            instrument.set_sweep_start_freq(self.sweep_start, channel=ch)
            instrument.set_sweep_stop_freq(self.sweep_stop, channel=ch)
            instrument.set_sweep_time(self.sweep_time, channel=ch)
            instrument.set_sweep_spacing(self.sweep_spacing, channel=ch)
            instrument.set_sweep_state(True, channel=ch)

        # 脉冲串 (互斥)
        if self.burst_enabled:
            instrument.set_burst_mode(self.burst_mode, channel=ch)
            instrument.set_burst_ncycles(self.burst_ncycles, channel=ch)
            instrument.set_burst_period(self.burst_period, channel=ch)
            instrument.set_burst_state(True, channel=ch)

        # 谐波
        if self.harmonic_type:
            instrument.set_harmonic_type(self.harmonic_type, channel=ch)
            instrument.set_harmonic_order(self.harmonic_order, channel=ch)
            if self.harmonic_amplitude:
                instrument.set_harmonic_amplitude(
                    self.harmonic_amplitude, channel=ch)

        # 电压单位
        instrument.set_voltage_unit(self.voltage_unit, channel=ch)

        # 同步
        instrument.set_sync_state(self.sync_enabled, channel=ch)


@dataclass
class MultiGeneratorSetup:
    """多台信号发生器的完整实验配置.

    支持在同一个 YAML 文件中管理多台仪器的配置。
    """

    configs: List[SignalGeneratorConfig] = field(default_factory=list)

    @classmethod
    def from_yaml(cls, path: str) -> "MultiGeneratorSetup":
        """从 YAML 文件加载多仪器配置."""
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        configs_data = data.get("configs", [data])  # 兼容单配置
        configs = [SignalGeneratorConfig(**cfg) for cfg in configs_data]
        return cls(configs=configs)

    def to_yaml(self, path: str) -> None:
        """保存多仪器配置到 YAML 文件."""
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump({"configs": self.to_dict_list()}, f,
                      default_flow_style=False, allow_unicode=True)

    def to_dict_list(self) -> List[dict]:
        """转为字典列表."""
        return [cfg.to_dict() for cfg in self.configs]

    def apply_all(self, instrument_map: Dict[str, Any]) -> None:
        """将所有配置应用到对应的仪器实例.

        Parameters
        ----------
        instrument_map : dict
            映射 {resource: DG4000Instrument}，用于匹配 config.resource。
        """
        for cfg in self.configs:
            inst = instrument_map.get(cfg.resource)
            if inst is None:
                continue
            cfg.apply_to(inst)

    def add(self, config: SignalGeneratorConfig) -> None:
        """添加一台仪器配置."""
        self.configs.append(config)
