import time
import logging
from typing import Optional, Literal, overload

import pyvisa
from pyvisa.resources import MessageBasedResource

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 10000  # ms

# ---------------------------------------------------------------------------
# 波形枚举
# ---------------------------------------------------------------------------

WaveShape = Literal[
    "SINusoid", "SQUare", "RAMP", "PULSe", "NOISe",
    "USER", "HARMonic", "CUSTom", "DC",
]

ModType = Literal[
    "AM", "FM", "PM", "FSKey", "BPSKey", "QPSKey",
    "3FSKey", "4FSKey", "OSKey", "ASKey", "PSKey",
    "PWM",
]

SweepSpacing = Literal["LINear", "LOGarithmic"]
BurstMode = Literal["TRIGgered", "GATed", "INFinity"]
VoltageUnit = Literal["VPP", "VRMS", "DBM"]
OutputPolarity = Literal["NORMal", "INVerted"]
SyncPolarity = Literal["POSitive", "NEGative"]

# ---------------------------------------------------------------------------


class DG4000Instrument:
    """RIGOL DG4000 系列函数/任意波形发生器 PyVISA 封装.

    支持型号: DG4162, DG4202 等 DG4000 系列。

    可通过 channel 参数绑定实例到固定通道:

        ch1 = DG4000Instrument("USB0::...", channel=1)  # 默认 CH1
        ch2 = DG4000Instrument("USB0::...", channel=2)  # 默认 CH2
        ch1.set_frequency(1000)   # 操作 CH1
        ch2.set_frequency(2000)   # 操作 CH2
    """

    def __init__(self, resource_string: str, timeout: int = DEFAULT_TIMEOUT,
                 channel: int = 1):
        self.resource_string = resource_string
        self.timeout = timeout
        self.channel = channel      # 实例默认通道
        self._rm: Optional[pyvisa.ResourceManager] = None
        self._inst: Optional[MessageBasedResource] = None

    def _ch(self, channel: Optional[int]) -> int:
        """解析通道号: 优先使用调用者传入的值，否则回退到实例默认通道."""
        return channel if channel is not None else self.channel

    # ==================================================================
    # 连接管理
    # ==================================================================

    def connect(self) -> None:
        self._rm = pyvisa.ResourceManager()
        self._inst = self._rm.open_resource(self.resource_string)
        self._inst.timeout = self.timeout
        idn = self.idn()
        logger.info("已连接: %s", idn)

    def disconnect(self) -> None:
        if self._inst:
            try:
                self._inst.close()
            except Exception:
                pass
            self._inst = None
        if self._rm:
            try:
                self._rm.close()
            except Exception:
                pass
            self._rm = None

    def __enter__(self) -> "DG4000Instrument":
        self.connect()
        return self

    def __exit__(self, *args) -> None:
        self.disconnect()

    # ==================================================================
    # 底层 I/O
    # ==================================================================

    def _ensure_connected(self) -> None:
        """若未连接则自动连接."""
        if self._inst is None:
            self.connect()

    def write(self, command: str) -> None:
        self._ensure_connected()
        logger.debug(">> %s", command)
        self._inst.write(command)

    def query(self, command: str) -> str:
        self._ensure_connected()
        logger.debug(">> %s", command)
        resp = self._inst.query(command).strip()
        logger.debug("<< %s", resp)
        return resp

    def query_float(self, command: str) -> float:
        return float(self.query(command))

    def query_int(self, command: str) -> int:
        return int(float(self.query(command)))

    @staticmethod
    def _expand_scpi_value(value: str) -> str:
        names = {
            "INT": "INTernal", "EXT": "EXTernal", "MAN": "MANual",
            "TRIG": "TRIGgered", "GAT": "GATed", "INF": "INFinity",
            "POS": "POSitive", "NEG": "NEGative",
            "NORM": "NORMal", "INV": "INVerted",
            "SIN": "SINusoid", "SQU": "SQUare", "TRI": "TRIangle",
            "RAMP": "RAMP", "NRAM": "NRAMp", "NOIS": "NOISe",
            "ARB": "ARBitrary", "FSK": "FSKey",
        }
        normalized = value.strip().strip('"').upper()
        return names.get(normalized, value.strip().strip('"'))

    # ==================================================================
    # IEEE 488.2 公用命令
    # ==================================================================

    def idn(self) -> str:
        """查询仪器 ID 字符串."""
        return self.query("*IDN?")

    def reset(self) -> None:
        """复位至出厂默认状态."""
        self.write("*RST")

    def save_state(self, slot: int) -> None:
        """保存当前仪器状态到存储位置 (0-9)."""
        self.write(f"*SAV {slot}")

    def recall_state(self, slot: int) -> None:
        """从存储位置恢复仪器状态 (0-9)."""
        self.write(f"*RCL {slot}")

    # ==================================================================
    # 波形快速设置 — APPLy
    # ==================================================================

    @overload
    def apply_wave(self, shape: Literal["SINusoid", "SQUare", "RAMP", "PULSe"],
                   channel: Optional[int] = None,
                   freq: Optional[float] = None,
                   amp: Optional[float] = None, offset: Optional[float] = None,
                   phase_or_delay: Optional[float] = None) -> None:
        ...

    @overload
    def apply_wave(self, shape: Literal["NOISe"],
                   channel: Optional[int] = None,
                   amp: Optional[float] = None,
                   offset: Optional[float] = None) -> None:
        ...

    @overload
    def apply_wave(self, shape: Literal["CUSTom", "HARMonic", "USER"],
                   channel: Optional[int] = None,
                   freq: Optional[float] = None,
                   amp: Optional[float] = None, offset: Optional[float] = None,
                   phase: Optional[float] = None) -> None:
        ...

    def apply_wave(self, shape, channel=None, **kwargs):
        """快速配置并输出标准波形.

        参数
        ----------
        shape : str
            波形类型: SINusoid, SQUare, RAMP, PULSe, NOISe, CUSTom, HARMonic, USER
        channel : int, optional
            通道号 (1 或 2), 默认使用实例绑定的通道
        **kwargs : 可选参数
            freq : float — 频率 (Hz), 默认 1 kHz
            amp : float — 幅度 (Vpp), 默认 5 Vpp
            offset : float — DC 偏置 (V), 默认 0 V
            phase : float — 初始相位 (度, 0-360), 默认 0°
            delay : float — 脉冲延迟 (秒, 仅 PULSe)
        """
        ch = self._ch(channel)
        prefix = f":SOURce{ch}:APPLy:{shape}"

        # APPLy 命令格式: <keyword> <val1>,<val2>,<val3>,...
        # 参数间用逗号分隔，命令与第一个参数间用空格分隔
        params: list[str] = []

        if shape == "NOISe":
            if kwargs.get("amp") is not None:
                params.append(str(kwargs["amp"]))
                if kwargs.get("offset") is not None:
                    params.append(str(kwargs["offset"]))
        elif shape == "PULSe":
            if kwargs.get("freq") is not None:
                params.append(str(kwargs["freq"]))
                if kwargs.get("amp") is not None:
                    params.append(str(kwargs["amp"]))
                    if kwargs.get("offset") is not None:
                        params.append(str(kwargs["offset"]))
                        if kwargs.get("delay") is not None:
                            params.append(str(kwargs["delay"]))
        else:
            if kwargs.get("freq") is not None:
                params.append(str(kwargs["freq"]))
                if kwargs.get("amp") is not None:
                    params.append(str(kwargs["amp"]))
                    if kwargs.get("offset") is not None:
                        params.append(str(kwargs["offset"]))
                        if kwargs.get("phase") is not None:
                            params.append(str(kwargs["phase"]))

        if params:
            self.write(f"{prefix} {','.join(params)}")
        else:
            self.write(prefix)

    def query_apply(self, channel: Optional[int] = None) -> str:
        """查询当前 APPLy 设置."""
        return self.query(f":SOURce{self._ch(channel)}:APPLy?")

    # ==================================================================
    # 波形选择 — FUNCtion
    # ==================================================================

    def set_shape(self, shape: WaveShape, channel: Optional[int] = None) -> None:
        """选择输出波形类型."""
        self.write(f":SOURce{self._ch(channel)}:FUNCtion:SHAPe {shape}")

    def get_shape(self, channel: Optional[int] = None) -> str:
        """查询当前波形类型."""
        return self.query(f":SOURce{self._ch(channel)}:FUNCtion:SHAPe?")

    def set_square_dcycle(self, percent: float,
                          channel: Optional[int] = None) -> None:
        """设置方波占空比 (%)."""
        self.write(f":SOURce{self._ch(channel)}:FUNCtion:SQUare:DCYCle {percent}")

    def get_square_dcycle(self, channel: Optional[int] = None) -> float:
        """读取方波占空比。"""
        return self.query_float(
            f":SOURce{self._ch(channel)}:FUNCtion:SQUare:DCYCle?"
        )

    def set_ramp_symmetry(self, percent: float,
                          channel: Optional[int] = None) -> None:
        """设置斜波对称度 (%)."""
        self.write(f":SOURce{self._ch(channel)}:FUNCtion:RAMP:SYMMetry {percent}")

    def get_ramp_symmetry(self, channel: Optional[int] = None) -> float:
        """读取斜波对称度。"""
        return self.query_float(
            f":SOURce{self._ch(channel)}:FUNCtion:RAMP:SYMMetry?"
        )

    # ==================================================================
    # 频率 / 周期
    # ==================================================================

    def set_frequency(self, freq: float, channel: Optional[int] = None) -> None:
        """设置输出频率 (Hz)."""
        self.write(f":SOURce{self._ch(channel)}:FREQuency:FIXed {freq:e}")

    def get_frequency(self, channel: Optional[int] = None) -> float:
        """查询输出频率 (Hz)."""
        return self.query_float(f":SOURce{self._ch(channel)}:FREQuency:FIXed?")

    def set_period(self, period: float, channel: Optional[int] = None) -> None:
        """设置输出周期 (s)."""
        self.write(f":SOURce{self._ch(channel)}:PERiod:FIXed {period:e}")

    def get_period(self, channel: Optional[int] = None) -> float:
        """查询输出周期 (s)."""
        return self.query_float(f":SOURce{self._ch(channel)}:PERiod:FIXed?")

    # ==================================================================
    # 幅度 / 偏置 / 高-低电平
    # ==================================================================

    def set_amplitude(self, amplitude: float,
                      channel: Optional[int] = None) -> None:
        """设置输出幅度 (默认单位 Vpp)."""
        ch = self._ch(channel)
        self.write(f":SOURce{ch}:VOLTage:LEVel:IMMediate:AMPLitude {amplitude:e}")

    def get_amplitude(self, channel: Optional[int] = None) -> float:
        """查询输出幅度."""
        return self.query_float(
            f":SOURce{self._ch(channel)}:VOLTage:LEVel:IMMediate:AMPLitude?")

    def set_offset(self, offset: float, channel: Optional[int] = None) -> None:
        """设置 DC 偏置电压 (V)."""
        ch = self._ch(channel)
        self.write(f":SOURce{ch}:VOLTage:LEVel:IMMediate:OFFSet {offset:e}")

    def get_offset(self, channel: Optional[int] = None) -> float:
        """查询 DC 偏置电压 (V)."""
        return self.query_float(
            f":SOURce{self._ch(channel)}:VOLTage:LEVel:IMMediate:OFFSet?")

    def set_high_level(self, voltage: float,
                       channel: Optional[int] = None) -> None:
        """设置高电平电压 (V)."""
        ch = self._ch(channel)
        self.write(f":SOURce{ch}:VOLTage:LEVel:IMMediate:HIGH {voltage:e}")

    def get_high_level(self, channel: Optional[int] = None) -> float:
        """查询高电平电压 (V)."""
        return self.query_float(
            f":SOURce{self._ch(channel)}:VOLTage:LEVel:IMMediate:HIGH?")

    def set_low_level(self, voltage: float,
                      channel: Optional[int] = None) -> None:
        """设置低电平电压 (V)."""
        ch = self._ch(channel)
        self.write(f":SOURce{ch}:VOLTage:LEVel:IMMediate:LOW {voltage:e}")

    def set_dc_voltage(self, voltage: float,
                       channel: Optional[int] = None) -> None:
        """使用 DG4000 专用 OFFSET 命令设置 DC 波形电平 (V)。"""
        self.set_offset(float(voltage), channel=channel)

    def get_low_level(self, channel: Optional[int] = None) -> float:
        """查询低电平电压 (V)."""
        return self.query_float(
            f":SOURce{self._ch(channel)}:VOLTage:LEVel:IMMediate:LOW?")

    def get_dc_voltage(self, channel: Optional[int] = None) -> float:
        """查询仪器报告的 DC OFFSET 设置值 (V).

        DG4162 固件 00.01.14 的内部查询值可能不随物理输出同步，不能把
        此结果当作示波器意义上的实际输出电压。
        """
        return self.get_offset(channel=channel)

    def set_voltage_unit(self, unit: VoltageUnit,
                         channel: Optional[int] = None) -> None:
        """设置幅度单位: VPP, VRMS, DBM."""
        self.write(f":SOURce{self._ch(channel)}:VOLTage:UNIT {unit}")

    def get_voltage_unit(self, channel: Optional[int] = None) -> str:
        """查询幅度单位."""
        return self.query(f":SOURce{self._ch(channel)}:VOLTage:UNIT?")

    # ==================================================================
    # 相位
    # ==================================================================

    def set_phase_adjust(self, angle: float,
                         channel: Optional[int] = None) -> None:
        """设置相位 (度, 0-360)."""
        self.write(f":SOURce{self._ch(channel)}:PHASe:ADJust {angle}")

    def get_phase_adjust(self, channel: Optional[int] = None) -> float:
        """查询相位 (度)."""
        return self.query_float(
            f":SOURce{self._ch(channel)}:PHASe:ADJust?")

    def phase_init(self, channel: Optional[int] = None) -> None:
        """将相位初始化为 0°."""
        self.write(f":SOURce{self._ch(channel)}:PHASe:INITiate")

    # ==================================================================
    # 输出通道控制
    # ==================================================================

    def set_output(self, state: bool, channel: Optional[int] = None) -> None:
        """打开/关闭指定通道输出."""
        self.write(f":OUTPut{self._ch(channel)}:STATe "
                   f"{'ON' if state else 'OFF'}")

    def get_output(self, channel: Optional[int] = None) -> bool:
        """查询指定通道输出状态."""
        return self.query(f":OUTPut{self._ch(channel)}:STATe?") == "ON"

    def set_output_load(self, ohms: float,
                        channel: Optional[int] = None) -> None:
        """设置输出负载阻抗 (Ω), INFinity 表示高阻."""
        ch = self._ch(channel)
        if isinstance(ohms, str) and ohms.upper() == "INF":
            self.write(f":OUTPut{ch}:LOAD INFinity")
        else:
            self.write(f":OUTPut{ch}:LOAD {ohms}")

    def get_output_load(self, channel: Optional[int] = None) -> str:
        """查询输出负载阻抗."""
        return self.query(f":OUTPut{self._ch(channel)}:LOAD?")

    def set_output_polarity(self, polarity: OutputPolarity,
                            channel: Optional[int] = None) -> None:
        """设置输出极性 (NORMal | INVerted)."""
        self.write(f":OUTPut{self._ch(channel)}:POLarity {polarity}")

    def set_sync_state(self, state: bool,
                       channel: Optional[int] = None) -> None:
        """打开/关闭同步信号输出."""
        self.write(f":OUTPut{self._ch(channel)}:SYNC:STATe "
                   f"{'ON' if state else 'OFF'}")

    def set_sync_polarity(self, polarity: SyncPolarity,
                          channel: Optional[int] = None) -> None:
        """设置同步信号极性."""
        self.write(f":OUTPut{self._ch(channel)}:SYNC:POLarity {polarity}")

    # ==================================================================
    # 调制 (MOD)
    # ==================================================================

    def set_mod_type(self, mod_type: ModType,
                     channel: Optional[int] = None) -> None:
        """设置调制类型."""
        self.write(f":SOURce{self._ch(channel)}:MOD:TYPE {mod_type}")

    def get_mod_type(self, channel: Optional[int] = None) -> str:
        return self._expand_scpi_value(
            self.query(f":SOURce{self._ch(channel)}:MOD:TYPE?")
        )

    def set_mod_state(self, state: bool,
                      channel: Optional[int] = None) -> None:
        """打开/关闭调制."""
        self.write(f":SOURce{self._ch(channel)}:MOD:STATe "
                   f"{'ON' if state else 'OFF'}")

    def get_mod_state(self, channel: Optional[int] = None) -> bool:
        return self.query(
            f":SOURce{self._ch(channel)}:MOD:STATe?").upper() in {"1", "ON"}

    def set_mod_source(self, source: str,
                       channel: Optional[int] = None) -> None:
        """设置调制源 (INTernal | EXTernal)，适用于当前调制类型.

        根据当前已选的调制类型 (AM/FM/PM/FSK/PWM)，自动选择对应的
        :SOURce:MOD:<type>:SOURce 命令。
        """
        ch = self._ch(channel)
        mod_type = self.query(f":SOURce{ch}:MOD:TYPe?").strip()
        self.write(f":SOURce{ch}:MOD:{mod_type}:SOURce {source}")

    def get_mod_source(self, channel: Optional[int] = None) -> str:
        ch = self._ch(channel)
        mod_type = self.query(f":SOURce{ch}:MOD:TYPe?").strip()
        return self._expand_scpi_value(
            self.query(f":SOURce{ch}:MOD:{mod_type}:SOURce?")
        )

    # ---- AM ----

    def set_mod_am_depth(self, depth: float,
                         channel: Optional[int] = None) -> None:
        """设置 AM 调制深度 (0-120%)."""
        self.write(f":SOURce{self._ch(channel)}:MOD:AM:DEPTh {depth}")

    def get_mod_am_depth(self, channel: Optional[int] = None) -> float:
        return self.query_float(
            f":SOURce{self._ch(channel)}:MOD:AM:DEPTh?"
        )

    def set_mod_am_source(self, source: str,
                          channel: Optional[int] = None) -> None:
        """设置 AM 调制源 (INTernal | EXTernal)."""
        self.write(f":SOURce{self._ch(channel)}:MOD:AM:SOURce {source}")

    def get_mod_am_source(self, channel: Optional[int] = None) -> str:
        return self._expand_scpi_value(
            self.query(f":SOURce{self._ch(channel)}:MOD:AM:SOURce?")
        )

    def set_mod_am_internal_freq(self, freq: float,
                                 channel: Optional[int] = None) -> None:
        """设置 AM 内部调制频率 (Hz)."""
        self.write(
            f":SOURce{self._ch(channel)}:MOD:AM:INTernal:FREQuency {freq:e}")

    def get_mod_am_internal_freq(self,
                                 channel: Optional[int] = None) -> float:
        return self.query_float(
            f":SOURce{self._ch(channel)}:MOD:AM:INTernal:FREQuency?"
        )

    def set_mod_am_internal_func(self, func: WaveShape,
                                 channel: Optional[int] = None) -> None:
        """设置 AM 内部调制波形."""
        self.write(
            f":SOURce{self._ch(channel)}:MOD:AM:INTernal:FUNCtion {func}")

    def get_mod_am_internal_func(self,
                                 channel: Optional[int] = None) -> str:
        return self._expand_scpi_value(self.query(
            f":SOURce{self._ch(channel)}:MOD:AM:INTernal:FUNCtion?"
        ))

    # ---- FM ----

    def set_mod_fm_deviation(self, deviation: float,
                             channel: Optional[int] = None) -> None:
        """设置 FM 频偏 (Hz)."""
        self.write(
            f":SOURce{self._ch(channel)}:MOD:FM:DEViation {deviation:e}")

    def get_mod_fm_deviation(self, channel: Optional[int] = None) -> float:
        return self.query_float(
            f":SOURce{self._ch(channel)}:MOD:FM:DEViation?"
        )

    def set_mod_fm_source(self, source: str,
                          channel: Optional[int] = None) -> None:
        self.write(f":SOURce{self._ch(channel)}:MOD:FM:SOURce {source}")

    def get_mod_fm_source(self, channel: Optional[int] = None) -> str:
        return self._expand_scpi_value(
            self.query(f":SOURce{self._ch(channel)}:MOD:FM:SOURce?")
        )

    def set_mod_fm_internal_freq(self, freq: float,
                                 channel: Optional[int] = None) -> None:
        self.write(
            f":SOURce{self._ch(channel)}:MOD:FM:INTernal:FREQuency {freq:e}")

    def get_mod_fm_internal_freq(self,
                                 channel: Optional[int] = None) -> float:
        return self.query_float(
            f":SOURce{self._ch(channel)}:MOD:FM:INTernal:FREQuency?"
        )

    def set_mod_fm_internal_func(self, func: WaveShape,
                                 channel: Optional[int] = None) -> None:
        self.write(
            f":SOURce{self._ch(channel)}:MOD:FM:INTernal:FUNCtion {func}")

    def get_mod_fm_internal_func(self,
                                 channel: Optional[int] = None) -> str:
        return self._expand_scpi_value(self.query(
            f":SOURce{self._ch(channel)}:MOD:FM:INTernal:FUNCtion?"
        ))

    # ---- PM ----

    def set_mod_pm_deviation(self, deviation: float,
                             channel: Optional[int] = None) -> None:
        """设置 PM 相偏 (度)."""
        self.write(f":SOURce{self._ch(channel)}:MOD:PM:DEViation {deviation}")

    def get_mod_pm_deviation(self, channel: Optional[int] = None) -> float:
        return self.query_float(
            f":SOURce{self._ch(channel)}:MOD:PM:DEViation?"
        )

    def set_mod_pm_source(self, source: str,
                          channel: Optional[int] = None) -> None:
        self.write(f":SOURce{self._ch(channel)}:MOD:PM:SOURce {source}")

    def get_mod_pm_source(self, channel: Optional[int] = None) -> str:
        return self._expand_scpi_value(
            self.query(f":SOURce{self._ch(channel)}:MOD:PM:SOURce?")
        )

    def set_mod_pm_internal_freq(self, freq: float,
                                 channel: Optional[int] = None) -> None:
        self.write(
            f":SOURce{self._ch(channel)}:MOD:PM:INTernal:FREQuency {freq:e}")

    def get_mod_pm_internal_freq(self,
                                 channel: Optional[int] = None) -> float:
        return self.query_float(
            f":SOURce{self._ch(channel)}:MOD:PM:INTernal:FREQuency?"
        )

    def set_mod_pm_internal_func(self, func: WaveShape,
                                 channel: Optional[int] = None) -> None:
        self.write(
            f":SOURce{self._ch(channel)}:MOD:PM:INTernal:FUNCtion {func}")

    def get_mod_pm_internal_func(self,
                                 channel: Optional[int] = None) -> str:
        return self._expand_scpi_value(self.query(
            f":SOURce{self._ch(channel)}:MOD:PM:INTernal:FUNCtion?"
        ))

    # ---- FSK ----

    def set_mod_fsk_frequency(self, freq: float,
                              channel: Optional[int] = None) -> None:
        """设置 FSK 跳频频率 (Hz)."""
        self.write(
            f":SOURce{self._ch(channel)}:MOD:FSKey:FREQuency {freq:e}")

    def get_mod_fsk_frequency(self, channel: Optional[int] = None) -> float:
        return self.query_float(
            f":SOURce{self._ch(channel)}:MOD:FSKey:FREQuency?"
        )

    def set_mod_fsk_rate(self, rate: float,
                         channel: Optional[int] = None) -> None:
        """设置 FSK 跳频速率 (Hz)."""
        self.write(
            f":SOURce{self._ch(channel)}:MOD:FSKey:INTernal:RATE {rate:e}")

    def get_mod_fsk_rate(self, channel: Optional[int] = None) -> float:
        return self.query_float(
            f":SOURce{self._ch(channel)}:MOD:FSKey:INTernal:RATE?"
        )

    def set_mod_fsk_polarity(self, polarity: OutputPolarity,
                             channel: Optional[int] = None) -> None:
        self.write(f":SOURce{self._ch(channel)}:MOD:FSKey:POLarity {polarity}")

    def get_mod_fsk_polarity(self, channel: Optional[int] = None) -> str:
        return self._expand_scpi_value(self.query(
            f":SOURce{self._ch(channel)}:MOD:FSKey:POLarity?"
        ))

    def set_mod_fsk_source(self, source: str,
                           channel: Optional[int] = None) -> None:
        self.write(f":SOURce{self._ch(channel)}:MOD:FSKey:SOURce {source}")

    def get_mod_fsk_source(self, channel: Optional[int] = None) -> str:
        return self._expand_scpi_value(self.query(
            f":SOURce{self._ch(channel)}:MOD:FSKey:SOURce?"
        ))

    # ---- PWM ----

    def set_mod_pwm_deviation_dcycle(self, percent: float,
                                     channel: Optional[int] = None) -> None:
        """设置 PWM 偏差 (占空比 %)."""
        self.write(
            f":SOURce{self._ch(channel)}:MOD:PWM:DEViation:DCYCle {percent}")

    def get_mod_pwm_deviation_dcycle(self,
                                     channel: Optional[int] = None) -> float:
        return self.query_float(
            f":SOURce{self._ch(channel)}:MOD:PWM:DEViation:DCYCle?"
        )

    def set_mod_pwm_internal_freq(self, freq: float,
                                  channel: Optional[int] = None) -> None:
        self.write(
            f":SOURce{self._ch(channel)}:MOD:PWM:INTernal:FREQuency {freq:e}")

    def get_mod_pwm_internal_freq(self,
                                  channel: Optional[int] = None) -> float:
        return self.query_float(
            f":SOURce{self._ch(channel)}:MOD:PWM:INTernal:FREQuency?"
        )

    def set_mod_pwm_internal_func(self, func: WaveShape,
                                  channel: Optional[int] = None) -> None:
        self.write(
            f":SOURce{self._ch(channel)}:MOD:PWM:INTernal:FUNCtion {func}")

    def get_mod_pwm_internal_func(self,
                                  channel: Optional[int] = None) -> str:
        return self._expand_scpi_value(self.query(
            f":SOURce{self._ch(channel)}:MOD:PWM:INTernal:FUNCtion?"
        ))

    def set_mod_pwm_source(self, source: str,
                           channel: Optional[int] = None) -> None:
        self.write(f":SOURce{self._ch(channel)}:MOD:PWM:SOURce {source}")

    def get_mod_pwm_source(self, channel: Optional[int] = None) -> str:
        return self._expand_scpi_value(self.query(
            f":SOURce{self._ch(channel)}:MOD:PWM:SOURce?"
        ))

    # ==================================================================
    # 扫描 (SWEep)
    # ==================================================================

    def set_sweep_state(self, state: bool,
                        channel: Optional[int] = None) -> None:
        """打开/关闭扫描."""
        self.write(f":SOURce{self._ch(channel)}:SWEep:STATe "
                   f"{'ON' if state else 'OFF'}")

    def set_sweep_time(self, seconds: float,
                       channel: Optional[int] = None) -> None:
        """设置扫描时间 (s)."""
        self.write(f":SOURce{self._ch(channel)}:SWEep:TIME {seconds:e}")

    def set_sweep_spacing(self, spacing: SweepSpacing,
                          channel: Optional[int] = None) -> None:
        """设置扫描间隔模式: LINear / LOGarithmic."""
        self.write(f":SOURce{self._ch(channel)}:SWEep:SPACing {spacing}")

    def set_sweep_start_freq(self, freq: float,
                             channel: Optional[int] = None) -> None:
        """设置扫描起始频率 (Hz)."""
        self.write(f":SOURce{self._ch(channel)}:SWEep:STARt {freq:e}")

    def set_sweep_stop_freq(self, freq: float,
                            channel: Optional[int] = None) -> None:
        """设置扫描终止频率 (Hz)."""
        self.write(f":SOURce{self._ch(channel)}:SWEep:STOP {freq:e}")

    def set_sweep_center_freq(self, freq: float,
                              channel: Optional[int] = None) -> None:
        """设置扫描中心频率 (Hz)."""
        self.write(f":SOURce{self._ch(channel)}:FREQuency:CENTer {freq:e}")

    def set_sweep_span(self, span: float,
                       channel: Optional[int] = None) -> None:
        """设置扫描频率跨度 (Hz)."""
        self.write(f":SOURce{self._ch(channel)}:FREQuency:SPAN {span:e}")

    def sweep_trigger(self, channel: Optional[int] = None) -> None:
        """触发一次扫描."""
        self.write(f":SOURce{self._ch(channel)}:SWEep:TRIGger:IMMediate")

    # ==================================================================
    # 脉冲串 (BURSt)
    # ==================================================================

    def set_burst_state(self, state: bool,
                        channel: Optional[int] = None) -> None:
        """打开/关闭脉冲串模式."""
        self.write(f":SOURce{self._ch(channel)}:BURSt:STATe "
                   f"{'ON' if state else 'OFF'}")

    def get_burst_state(self, channel: Optional[int] = None) -> bool:
        return self.query(
            f":SOURce{self._ch(channel)}:BURSt:STATe?").upper() in {"1", "ON"}

    def set_burst_mode(self, mode: BurstMode,
                       channel: Optional[int] = None) -> None:
        """设置脉冲串模式: TRIGgered / GATed."""
        self.write(f":SOURce{self._ch(channel)}:BURSt:MODE {mode}")

    def get_burst_mode(self, channel: Optional[int] = None) -> str:
        return self._expand_scpi_value(
            self.query(f":SOURce{self._ch(channel)}:BURSt:MODE?")
        )

    def set_burst_ncycles(self, n,
                          channel: Optional[int] = None) -> None:
        """设置脉冲串周期数 (1-50000)."""
        self.write(f":SOURce{self._ch(channel)}:BURSt:NCYCles {n}")

    def get_burst_ncycles(self, channel: Optional[int] = None):
        value = self.query(f":SOURce{self._ch(channel)}:BURSt:NCYCles?")
        numeric = None if value.upper().startswith("INF") else float(value)
        return "INFinity" if numeric is None or numeric >= 9e37 else int(numeric)

    def set_burst_phase(self, angle: float,
                        channel: Optional[int] = None) -> None:
        """设置脉冲串起始相位 (度)."""
        self.write(f":SOURce{self._ch(channel)}:BURSt:PHASe {angle}")

    def get_burst_phase(self, channel: Optional[int] = None) -> float:
        return self.query_float(f":SOURce{self._ch(channel)}:BURSt:PHASe?")

    def set_burst_period(self, seconds: float,
                         channel: Optional[int] = None) -> None:
        """设置脉冲串周期 (s)."""
        self.write(
            f":SOURce{self._ch(channel)}:BURSt:INTernal:PERiod {seconds:e}")

    def get_burst_period(self, channel: Optional[int] = None) -> float:
        return self.query_float(
            f":SOURce{self._ch(channel)}:BURSt:INTernal:PERiod?"
        )

    def set_burst_delay(self, seconds: float,
                        channel: Optional[int] = None) -> None:
        """设置脉冲串延迟 (s)."""
        self.write(f":SOURce{self._ch(channel)}:BURSt:TDELay {seconds:e}")

    def get_burst_delay(self, channel: Optional[int] = None) -> float:
        return self.query_float(
            f":SOURce{self._ch(channel)}:BURSt:TDELay?"
        )

    def set_burst_trigger_source(self, source: str,
                                 channel: Optional[int] = None) -> None:
        """设置脉冲串触发源: INTernal | EXTernal | MANual."""
        self.write(
            f":SOURce{self._ch(channel)}:BURSt:TRIGger:SOURce {source}")

    def get_burst_trigger_source(self,
                                 channel: Optional[int] = None) -> str:
        return self._expand_scpi_value(self.query(
            f":SOURce{self._ch(channel)}:BURSt:TRIGger:SOURce?"
        ))

    def set_burst_trigger_slope(self, slope: str,
                                channel: Optional[int] = None) -> None:
        """设置脉冲串触发边沿: POSitive | NEGative."""
        self.write(
            f":SOURce{self._ch(channel)}:BURSt:TRIGger:SLOPe {slope}")

    def get_burst_trigger_slope(self,
                                channel: Optional[int] = None) -> str:
        return self._expand_scpi_value(self.query(
            f":SOURce{self._ch(channel)}:BURSt:TRIGger:SLOPe?"
        ))

    def burst_trigger(self, channel: Optional[int] = None) -> None:
        """手动触发一次脉冲串."""
        self.write(f":SOURce{self._ch(channel)}:BURSt:TRIGger:IMMediate")

    # ==================================================================
    # 脉冲参数 (PULSe)
    # ==================================================================

    def set_pulse_period(self, period: float,
                         channel: Optional[int] = None) -> None:
        """设置脉冲周期 (s)."""
        self.write(f":SOURce{self._ch(channel)}:PULSe:PERiod {period:e}")

    def set_pulse_width(self, width: float,
                        channel: Optional[int] = None) -> None:
        """设置脉冲宽度 (s)."""
        self.write(f":SOURce{self._ch(channel)}:PULSe:WIDTh {width:e}")

    def get_pulse_width(self, channel: Optional[int] = None) -> float:
        return self.query_float(
            f":SOURce{self._ch(channel)}:PULSe:WIDTh?")

    def set_pulse_dcycle(self, percent: float,
                         channel: Optional[int] = None) -> None:
        """设置脉冲占空比 (%)."""
        self.write(f":SOURce{self._ch(channel)}:PULSe:DCYCle {percent}")

    def set_pulse_delay(self, delay: float,
                        channel: Optional[int] = None) -> None:
        """设置脉冲延迟 (s)."""
        self.write(f":SOURce{self._ch(channel)}:PULSe:DELay {delay:e}")

    def get_pulse_delay(self, channel: Optional[int] = None) -> float:
        """读取脉冲延迟。"""
        return self.query_float(
            f":SOURce{self._ch(channel)}:PULSe:DELay?"
        )

    def set_pulse_leading(self, seconds: float,
                          channel: Optional[int] = None) -> None:
        """设置脉冲上升沿时间 (s)."""
        self.write(
            f":SOURce{self._ch(channel)}:PULSe:TRANsition:LEADing {seconds:e}")

    def set_pulse_trailing(self, seconds: float,
                           channel: Optional[int] = None) -> None:
        """设置脉冲下降沿时间 (s)."""
        self.write(
            f":SOURce{self._ch(channel)}:PULSe:TRANsition:TRAiling {seconds:e}")

    def set_pulse_hold(self, hold: str,
                       channel: Optional[int] = None) -> None:
        """设置脉冲保持模式: WIDTh | DCYCle."""
        self.write(f":SOURce{self._ch(channel)}:PULSe:HOLD {hold}")

    # ==================================================================
    # 谐波 (HARMonic)
    # ==================================================================

    def set_harmonic_type(self, harmonic_type: str,
                          channel: Optional[int] = None) -> None:
        """设置谐波类型: EVEN | ODD | ALL | USER."""
        self.write(f":SOURce{self._ch(channel)}:HARMonic:TYPe {harmonic_type}")

    def set_harmonic_order(self, order: int,
                           channel: Optional[int] = None) -> None:
        """设置谐波阶次."""
        self.write(f":SOURce{self._ch(channel)}:HARMonic:ORDEr {order}")

    def set_harmonic_amplitude(self, amplitude: float,
                               channel: Optional[int] = None) -> None:
        """设置谐波幅度."""
        self.write(
            f":SOURce{self._ch(channel)}:HARMonic:AMPL {amplitude:e}")

    def set_harmonic_phase(self, angle: float,
                           channel: Optional[int] = None) -> None:
        """设置谐波相位 (度)."""
        self.write(f":SOURce{self._ch(channel)}:HARMonic:PHASe {angle}")

    # ==================================================================
    # 系统 (SYSTem)
    # ==================================================================

    def set_ref_clock_source(self, source: str) -> None:
        """设置参考时钟源: INTernal (内部 10 MHz) | EXTernal (外部).

        使用 EXTernal 时，需从后面板 [10MHz In/Out] 接口注入外部 10 MHz 时钟。
        可用于多台仪器同步。

        参数
        ----------
        source : str
            "INTernal" — 内部 10 MHz 参考时钟（默认）
            "EXTernal" — 外部参考时钟（需注入 10 MHz 信号）
        """
        self.write(f":SYSTem:ROSCillator:SOURce {source}")

    def get_ref_clock_source(self) -> str:
        """查询当前参考时钟源: INTernal | EXTernal."""
        return self.query(":SYSTem:ROSCillator:SOURce?")

    def system_error(self) -> str:
        """查询并清除系统错误队列."""
        return self.query(":SYSTem:ERRor?")

    def system_version(self) -> str:
        """查询 SCPI 版本."""
        return self.query(":SYSTem:VERSion?")

    def wait_for_operation_complete(self) -> None:
        """等待波形上传和设置完成。"""
        if self.query("*OPC?").strip() not in {"1", "+1"}:
            raise RuntimeError("DG4000 未确认操作完成")

    def raise_for_errors(self, *, max_errors: int = 20) -> None:
        """读取错误队列，避免将命令被拒绝误报为配置成功。"""
        errors = []
        for _ in range(max_errors):
            response = self.system_error().strip()
            try:
                code = int(response.split(",", 1)[0])
            except ValueError:
                raise RuntimeError(f"DG4000 错误队列响应无效：{response}")
            if code == 0:
                break
            errors.append(response)
        else:
            errors.append("错误队列未清空")
        if errors:
            raise RuntimeError("DG4000：" + "；".join(errors))

    def system_preset(self) -> None:
        """恢复出厂预设."""
        self.write(":SYSTem:PRESet")

    def system_beep(self) -> None:
        """发出蜂鸣声."""
        self.write(":SYSTem:BEEPer:IMMediate")

    # ==================================================================
    # 便捷方法: 一键配置常用波形
    # ==================================================================

    def setup_sine(self, freq: float, amplitude: float,
                   offset: float = 0.0, phase: float = 0.0,
                   channel: Optional[int] = None) -> None:
        """一键配置正弦波并打开输出.

        Parameters
        ----------
        phase : float
            初始相位 (度, 0-360).
        """
        ch = self._ch(channel)
        self.apply_wave("SINusoid", channel=ch,
                        freq=freq, amp=amplitude,
                        offset=offset, phase=phase)
        self.set_output(True, channel=ch)

    def setup_square(self, freq: float, amplitude: float,
                     offset: float = 0.0, dcycle: float = 50.0,
                     phase: float = 0.0,
                     channel: Optional[int] = None) -> None:
        """一键配置方波并打开输出.

        Parameters
        ----------
        phase : float
            初始相位 (度, 0-360).
        """
        ch = self._ch(channel)
        self.apply_wave("SQUare", channel=ch,
                        freq=freq, amp=amplitude,
                        offset=offset, phase=phase)
        self.set_square_dcycle(dcycle, channel=ch)
        self.set_output(True, channel=ch)

    def setup_ramp(self, freq: float, amplitude: float,
                   offset: float = 0.0, symmetry: float = 50.0,
                   phase: float = 0.0,
                   channel: Optional[int] = None) -> None:
        """一键配置斜波并打开输出.

        Parameters
        ----------
        phase : float
            初始相位 (度, 0-360).
        """
        ch = self._ch(channel)
        self.apply_wave("RAMP", channel=ch,
                        freq=freq, amp=amplitude,
                        offset=offset, phase=phase)
        self.set_ramp_symmetry(symmetry, channel=ch)
        self.set_output(True, channel=ch)

    def setup_pulse(self, freq: float, amplitude: float,
                    offset: float = 0.0, width: Optional[float] = None,
                    channel: Optional[int] = None) -> None:
        """一键配置脉冲并打开输出."""
        ch = self._ch(channel)
        self.apply_wave("PULSe", channel=ch,
                        freq=freq, amp=amplitude, offset=offset)
        if width is not None:
            self.set_pulse_width(width, channel=ch)
        self.set_output(True, channel=ch)

    def setup_dc(self, voltage: float,
                 channel: Optional[int] = None) -> None:
        """使用 OFFSET 命令配置 DC 电平并打开输出。"""
        ch = self._ch(channel)
        self.set_shape("DC", channel=ch)
        self.set_dc_voltage(voltage, channel=ch)
        self.set_output(True, channel=ch)

    def setup_noise(self, amplitude: float, offset: float = 0.0,
                    channel: Optional[int] = None) -> None:
        """一键配置噪声输出并打开."""
        ch = self._ch(channel)
        self.apply_wave("NOISe", channel=ch, amp=amplitude, offset=offset)
        self.set_output(True, channel=ch)

    # ==================================================================
    # 任意波 (ARB / TRACe)
    # ==================================================================

    MAX_ARB_POINTS = 16384
    MAX_ARB_POINTS_TOTAL = 512 * 1024

    def send_arbitrary_waveform(self, values, channel: Optional[int] = None):
        """发送自定义波形到易失性存储器（ASCII 格式）.

        将用户定义的波形数据（归一化到 [-1, +1]）写入信号发生器的
        易失性波形存储区。使用 ASCII 逗号分隔、5 位小数格式（用户已验证）。

        参数
        ----------
        values : array-like
            一个周期内的波形数据点，归一化到 [-1, +1]。
        channel : int, optional
            通道号，默认使用实例绑定的通道。
        """
        ch = self._ch(channel)
        pts = list(values)
        n = len(pts)
        if n < 2 or n > self.MAX_ARB_POINTS:
            raise ValueError(
                f"点数需在 2 ~ {self.MAX_ARB_POINTS} 之间，实际为 {n}")

        # 裁剪到 [-1, 1] 并转为 5 位小数
        clipped = [max(-1.0, min(1.0, float(v))) for v in pts]
        rounded = [round(v, 5) for v in clipped]

        # 先设点数，再发 ASCII 数据
        self.write(f":SOURce{ch}:TRACe:DATA:POINts VOLATILE,{n}")
        val_str = ",".join(f"{v:.5f}" for v in rounded)
        self.write(f":SOURce{ch}:TRACe:DATA:DATA VOLATILE,{val_str}")

    def setup_arbitrary(self, y_values, freq: float = 1000.0,
                        amplitude: float = 5.0, offset: float = 0.0,
                        phase: float = 0.0,
                        channel: Optional[int] = None,
                        output: bool = True) -> None:
        """一键配置自定义波形输出.

        按照用户已验证的工作流程：
        1. 退出 DC（切到 SINusoid）→ APPLy:USER 设置参数
        2. 上传波形数据到 VOLATILE 存储区
        3. 按需打开输出

        关键：APPLy:USER 在 USER/非DC 状态下正常工作，
        仅在 DC 状态下会被特殊处理（忽略 freq/amp/phase）。
        因此先切到 SINusoid 退出 DC 态。

        参数
        ----------
        y_values : array-like
            一个周期内的波形数据，归一化到 [-1, +1]。
        freq : float
            波形重复频率 (Hz), 默认 1 kHz。
        amplitude : float
            输出幅度 (Vpp), 默认 5 Vpp。
        offset : float
            DC 偏置 (V), 默认 0 V。
        phase : float
            初始相位 (度, 0-360), 默认 0°。
        channel : int, optional
            通道号，默认使用实例绑定的通道。
        output : bool
            是否在配置完成后打开输出。需要先配置 Burst/触发再开输出时设为 False。
        """
        ch = self._ch(channel)

        # Step 1: 退出 DC（切到 SINusoid 确保 APPLy:USER 不被特殊处理）
        self.set_shape("SINusoid", channel=ch)
        time.sleep(0.05)

        # Step 2: APPLy:USER 设置参数并切换到 USER 模式
        self.write(
            f":SOURce{ch}:APPLy:USER {freq:e},{amplitude:e},{offset:e},{phase:e}")

        # Step 3: 上传波形数据到 VOLATILE
        self.send_arbitrary_waveform(y_values, channel=ch)

        # Step 4: 按需打开输出
        if output:
            self.set_output(True, channel=ch)

    def set_custom_point(self, point: int, value: float,
                         channel: Optional[int] = None) -> None:
        """修改指定通道易失性存储器中某点的 DAC 值 (0 ~ 16383)."""
        ch = self._ch(channel)
        self.write(f":SOURce{ch}:TRACe:DATA:VALue "
                   f"VOLATILE,{int(point)},{int(value)}")

    def get_custom_point(self, point: int,
                         channel: Optional[int] = None) -> int:
        """查询指定通道易失性存储器中某点的 DAC 值."""
        ch = self._ch(channel)
        return self.query_int(
            f":SOURce{ch}:TRACe:DATA:VALue? VOLATILE,{int(point)}")

    def set_arb_points(self, n: int, channel: Optional[int] = None) -> None:
        """设置易失性存储器的初始点数（重置所有点为 0）。

        参数
        ----------
        n : int
            点数，范围 2 ~ 16384。
        channel : int, optional
            通道号，默认使用实例绑定的通道。
        """
        ch = self._ch(channel)
        if n < 2 or n > self.MAX_ARB_POINTS:
            raise ValueError(
                f"点数需在 2 ~ {self.MAX_ARB_POINTS} 之间，实际为 {n}")
        self.write(f":SOURce{ch}:TRACe:DATA:POINts VOLATILE,{n}")

    def get_arb_points(self, channel: Optional[int] = None) -> int:
        """查询指定通道易失性存储器的当前点数."""
        ch = self._ch(channel)
        return self.query_int(f":SOURce{ch}:TRACe:DATA:POINts? VOLATILE")

    def all_off(self) -> None:
        """关闭所有通道输出."""
        self.set_output(False, channel=1)
        self.set_output(False, channel=2)
