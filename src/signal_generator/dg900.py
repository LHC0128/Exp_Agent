"""RIGOL DG900 Pro 系列信号发生器 PyVISA 封装.

支持型号: DG912 Pro 等 DG900 Pro 系列。
VISA resource 中含有 "DG9" 标识符（DG4000 系列为 "DG4E"）。

与 DG4000 的核心差异:
    DG4000: :SOURce<n>:FUNCtion:SHAPe DC
    DG900:  :SOURce<n>:APPLy:DC DEF,DEF,<offset>

用法:
    dg = DG900Instrument("USB0::0x1AB1::0x0646::DG9Q280100002::INSTR", channel=2)
    dg.connect()
    dg.setup_dc(0.1)         # CH2 输出 0.1 V DC
    dg.set_output(False)     # 关闭 CH2 输出
    dg.disconnect()
"""

import logging
from typing import Optional

import pyvisa
from pyvisa.resources import MessageBasedResource

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 10000  # ms


class DG900Instrument:
    """RIGOL DG900 Pro 系列 PyVISA 封装."""

    MOD_TYPES = ("AM", "FM", "PM", "FSKey", "PWM")

    _SHAPE_NAMES = {
        "SIN": "SINusoid",
        "SQU": "SQUare",
        "RAMP": "RAMP",
        "PULS": "PULSe",
        "NOIS": "NOISe",
        "DC": "DC",
        "HARM": "HARMonic",
        "ARB": "ARBitrary",
        "SEQ": "SEQuence",
    }

    _VALUE_NAMES = {
        "INT": "INTernal",
        "EXT": "EXTernal",
        "NORM": "NORMal",
        "INV": "INVerted",
        "TRIG": "TRIGgered",
        "GAT": "GATed",
        "IMM": "IMMediate",
        "BUS": "BUS",
        "TIM": "TIMer",
        "POS": "POSitive",
        "NEG": "NEGative",
        "SIN": "SINusoid",
        "SQU": "SQUare",
        "TRI": "TRIangle",
        "RAMP": "RAMP",
        "NRAM": "NRAMp",
        "NOIS": "NOISe",
        "ARB": "ARBitrary",
    }

    def __init__(self, resource_string: str, timeout: int = DEFAULT_TIMEOUT,
                 channel: int = 1):
        self.resource_string = resource_string
        self.timeout = timeout
        self.channel = channel
        self._rm: Optional[pyvisa.ResourceManager] = None
        self._inst: Optional[MessageBasedResource] = None

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    def _ch(self, channel: Optional[int]) -> int:
        """解析通道号: 优先用传入值，否则用实例默认通道."""
        return channel if channel is not None else self.channel

    def _ensure_connected(self) -> None:
        """若未连接则自动连接."""
        if self._inst is None:
            self.connect()

    # ------------------------------------------------------------------
    # 连接管理
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """打开 PyVISA 资源."""
        self._rm = pyvisa.ResourceManager()
        self._inst = self._rm.open_resource(self.resource_string)
        self._inst.timeout = self.timeout
        idn = self.idn()
        logger.info("已连接 DG900: %s", idn)

    def disconnect(self) -> None:
        """关闭 PyVISA 资源."""
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

    def idn(self) -> str:
        """查询设备标识."""
        return self.query("*IDN?")

    # ------------------------------------------------------------------
    # SCPI 底层
    # ------------------------------------------------------------------

    def write(self, command: str) -> None:
        """发送 SCPI 命令."""
        self._ensure_connected()
        logger.debug(">> %s", command)
        self._inst.write(command)

    def clear_status(self) -> None:
        """清除上一事务遗留的状态和错误队列."""
        self.write("*CLS")

    def wait_for_operation_complete(self) -> None:
        """等待此前发送的命令全部执行完成."""
        response = self.query("*OPC?").strip()
        if response not in {"1", "+1"}:
            raise RuntimeError(f"DG900 *OPC? 返回异常: {response!r}")

    def raise_for_errors(self, *, max_errors: int = 20) -> None:
        """读取 SCPI 错误队列，发现当前事务错误时立即失败."""
        errors: list[str] = []
        for _ in range(max_errors):
            response = self.query(":SYSTem:ERRor?").strip()
            if response.startswith("0,") or response.startswith("+0,"):
                break
            errors.append(response)
        else:
            errors.append("错误队列在限定次数内未清空")
        if errors:
            raise RuntimeError("DG900 设置失败: " + "; ".join(errors))

    def query(self, command: str) -> str:
        """发送 SCPI 查询并返回响应."""
        self._ensure_connected()
        logger.debug(">> %s", command)
        resp = self._inst.query(command).strip()
        logger.debug("<< %s", resp)
        return resp

    def query_float(self, command: str) -> float:
        """查询浮点值。"""
        return float(self.query(command))

    def query_bool(self, command: str) -> bool:
        """查询布尔值，兼容数值和 ON/OFF 响应。"""
        return self.query(command).strip().upper() in {"1", "ON"}

    @classmethod
    def _expand_value(cls, value: str) -> str:
        normalized = value.strip().strip('"').upper()
        return cls._VALUE_NAMES.get(normalized, value.strip().strip('"'))

    # ------------------------------------------------------------------
    # 基础波形
    # ------------------------------------------------------------------

    def query_apply(self, channel: Optional[int] = None) -> str:
        """查询当前基础波形参数的原始响应。"""
        return self.query(f":SOURce{self._ch(channel)}:APPLy?")

    def get_wave_parameters(self, channel: Optional[int] = None) -> dict:
        """解析 APPLy? 响应并返回基础波形参数。"""
        response = self.query_apply(channel).strip().strip('"')
        fields = [item.strip().strip('"') for item in response.split(",")]
        if len(fields) != 5:
            raise ValueError(f"无法解析 DG900 APPLy? 响应: {response!r}")

        def number(value: str) -> Optional[float]:
            return None if value.upper() == "DEF" else float(value)

        shape = self._SHAPE_NAMES.get(fields[0].upper(), fields[0])
        return {
            "shape": shape,
            "frequency": number(fields[1]),
            "amplitude": number(fields[2]),
            "offset": number(fields[3]),
            "phase": number(fields[4]),
        }

    def apply_wave(self, shape: str, frequency: Optional[float] = None,
                   amplitude: Optional[float] = None,
                   offset: Optional[float] = None,
                   phase: Optional[float] = None,
                   channel: Optional[int] = None) -> None:
        """使用 APPLy 命令一次设置基础波形参数。"""
        ch = self._ch(channel)
        values = (frequency, amplitude, offset, phase)
        params = ["DEF" if value is None else str(value) for value in values]
        self.write(f":SOURce{ch}:APPLy:{shape} {','.join(params)}")

    def set_shape(self, shape: str, channel: Optional[int] = None) -> None:
        self.write(f":SOURce{self._ch(channel)}:FUNCtion {shape}")

    def get_shape(self, channel: Optional[int] = None) -> str:
        return self.get_wave_parameters(channel)["shape"]

    def set_frequency(self, frequency: float,
                      channel: Optional[int] = None) -> None:
        self.write(f":SOURce{self._ch(channel)}:FREQuency {frequency:e}")

    def get_frequency(self, channel: Optional[int] = None) -> float:
        return self.query_float(f":SOURce{self._ch(channel)}:FREQuency?")

    def set_amplitude(self, amplitude: float,
                      channel: Optional[int] = None) -> None:
        self.write(f":SOURce{self._ch(channel)}:VOLTage {amplitude:e}")

    def get_amplitude(self, channel: Optional[int] = None) -> float:
        return self.query_float(f":SOURce{self._ch(channel)}:VOLTage?")

    def set_offset(self, offset: float,
                   channel: Optional[int] = None) -> None:
        self.write(f":SOURce{self._ch(channel)}:VOLTage:OFFSet {offset:e}")

    def get_offset(self, channel: Optional[int] = None) -> float:
        return self.query_float(f":SOURce{self._ch(channel)}:VOLTage:OFFSet?")

    def set_phase(self, phase: float,
                  channel: Optional[int] = None) -> None:
        self.write(f":SOURce{self._ch(channel)}:PHASe {phase}")

    def get_phase(self, channel: Optional[int] = None) -> float:
        return self.query_float(f":SOURce{self._ch(channel)}:PHASe?")

    def set_square_dcycle(self, percent: float,
                          channel: Optional[int] = None) -> None:
        self.write(
            f":SOURce{self._ch(channel)}:FUNCtion:SQUare:DCYCle {percent}"
        )

    def get_square_dcycle(self, channel: Optional[int] = None) -> float:
        return self.query_float(
            f":SOURce{self._ch(channel)}:FUNCtion:SQUare:DCYCle?"
        )

    def set_ramp_symmetry(self, percent: float,
                          channel: Optional[int] = None) -> None:
        self.write(
            f":SOURce{self._ch(channel)}:FUNCtion:RAMP:SYMMetry {percent}"
        )

    def get_ramp_symmetry(self, channel: Optional[int] = None) -> float:
        return self.query_float(
            f":SOURce{self._ch(channel)}:FUNCtion:RAMP:SYMMetry?"
        )

    def set_pulse_width(self, seconds: float,
                        channel: Optional[int] = None) -> None:
        self.write(f":SOURce{self._ch(channel)}:FUNCtion:PULSe:WIDTh {seconds:e}")

    def get_pulse_width(self, channel: Optional[int] = None) -> float:
        return self.query_float(
            f":SOURce{self._ch(channel)}:FUNCtion:PULSe:WIDTh?"
        )

    # ------------------------------------------------------------------
    # DC 输出（核心）
    # ------------------------------------------------------------------

    def setup_dc(self, voltage: float,
                 channel: Optional[int] = None) -> None:
        """设置 DC 电平并打开输出.

        DG900 Pro 使用 :SOURce:APPLy:DC 命令（不同于 DG4000 的
        :SOURce:FUNCtion:SHAPe DC），此命令自动配置波形为 DC、
        设置偏置电压并开启输出。
        """
        ch = self._ch(channel)
        self.write(f":SOURce{ch}:APPLy:DC DEF,DEF,{voltage}")

    def get_dc_voltage(self, channel: Optional[int] = None) -> float:
        """读取 DC 输出电平。"""
        value = self.get_wave_parameters(channel)["offset"]
        if value is None:
            raise ValueError("DG900 DC 偏置回读为 DEF")
        return value

    # ------------------------------------------------------------------
    # 调制
    # ------------------------------------------------------------------

    def set_mod_type_state(self, mod_type: str, state: bool,
                           channel: Optional[int] = None) -> None:
        self.write(
            f":SOURce{self._ch(channel)}:{mod_type}:STATe "
            f"{'ON' if state else 'OFF'}"
        )

    def get_mod_type_state(self, mod_type: str,
                           channel: Optional[int] = None) -> bool:
        return self.query_bool(
            f":SOURce{self._ch(channel)}:{mod_type}:STATe?"
        )

    def get_active_mod_type(self, channel: Optional[int] = None) -> Optional[str]:
        """返回当前启用的调制类型；全部关闭时返回 None。"""
        for mod_type in self.MOD_TYPES:
            if self.get_mod_type_state(mod_type, channel):
                return mod_type
        return None

    def disable_all_mod(self, channel: Optional[int] = None) -> None:
        """关闭当前启用的调制，避免向不兼容波形写入无效状态命令."""
        for mod_type in self.MOD_TYPES:
            if self.get_mod_type_state(mod_type, channel):
                self.set_mod_type_state(mod_type, False, channel)

    def _set_mod_value(self, mod_type: str, suffix: str, value,
                       channel: Optional[int]) -> None:
        self.write(
            f":SOURce{self._ch(channel)}:{mod_type}:{suffix} {value}"
        )

    def _get_mod_float(self, mod_type: str, suffix: str,
                       channel: Optional[int]) -> float:
        return self.query_float(
            f":SOURce{self._ch(channel)}:{mod_type}:{suffix}?"
        )

    def _get_mod_text(self, mod_type: str, suffix: str,
                      channel: Optional[int]) -> str:
        return self._expand_value(self.query(
            f":SOURce{self._ch(channel)}:{mod_type}:{suffix}?"
        ))

    def set_mod_am_depth(self, depth: float, channel: Optional[int] = None) -> None:
        self._set_mod_value("AM", "DEPTh", depth, channel)

    def get_mod_am_depth(self, channel: Optional[int] = None) -> float:
        return self._get_mod_float("AM", "DEPTh", channel)

    def set_mod_fm_deviation(self, value: float,
                             channel: Optional[int] = None) -> None:
        self._set_mod_value("FM", "DEViation", value, channel)

    def get_mod_fm_deviation(self, channel: Optional[int] = None) -> float:
        return self._get_mod_float("FM", "DEViation", channel)

    def set_mod_pm_deviation(self, value: float,
                             channel: Optional[int] = None) -> None:
        self._set_mod_value("PM", "DEViation", value, channel)

    def get_mod_pm_deviation(self, channel: Optional[int] = None) -> float:
        return self._get_mod_float("PM", "DEViation", channel)

    def set_mod_fsk_frequency(self, value: float,
                              channel: Optional[int] = None) -> None:
        self._set_mod_value("FSKey", "FREQuency", value, channel)

    def get_mod_fsk_frequency(self, channel: Optional[int] = None) -> float:
        return self._get_mod_float("FSKey", "FREQuency", channel)

    def set_mod_fsk_rate(self, value: float,
                         channel: Optional[int] = None) -> None:
        self._set_mod_value("FSKey", "INTernal:RATE", value, channel)

    def get_mod_fsk_rate(self, channel: Optional[int] = None) -> float:
        return self._get_mod_float("FSKey", "INTernal:RATE", channel)

    def set_mod_fsk_polarity(self, value: str,
                             channel: Optional[int] = None) -> None:
        self._set_mod_value("FSKey", "POLarity", value, channel)

    def get_mod_fsk_polarity(self, channel: Optional[int] = None) -> str:
        return self._get_mod_text("FSKey", "POLarity", channel)

    def set_mod_pwm_deviation_dcycle(self, value: float,
                                     channel: Optional[int] = None) -> None:
        self._set_mod_value("PWM", "DEViation:DCYCle", value, channel)

    def get_mod_pwm_deviation_dcycle(self,
                                     channel: Optional[int] = None) -> float:
        return self._get_mod_float("PWM", "DEViation:DCYCle", channel)

    def set_mod_source(self, mod_type: str, source: str,
                       channel: Optional[int] = None) -> None:
        self._set_mod_value(mod_type, "SOURce", source, channel)

    def get_mod_source(self, mod_type: str,
                       channel: Optional[int] = None) -> str:
        return self._get_mod_text(mod_type, "SOURce", channel)

    def set_mod_internal_frequency(self, mod_type: str, value: float,
                                   channel: Optional[int] = None) -> None:
        suffix = "INTernal:RATE" if mod_type == "FSKey" else "INTernal:FREQuency"
        self._set_mod_value(mod_type, suffix, value, channel)

    def get_mod_internal_frequency(self, mod_type: str,
                                   channel: Optional[int] = None) -> float:
        suffix = "INTernal:RATE" if mod_type == "FSKey" else "INTernal:FREQuency"
        return self._get_mod_float(mod_type, suffix, channel)

    def set_mod_internal_function(self, mod_type: str, value: str,
                                  channel: Optional[int] = None) -> None:
        self._set_mod_value(mod_type, "INTernal:FUNCtion", value, channel)

    def get_mod_internal_function(self, mod_type: str,
                                  channel: Optional[int] = None) -> str:
        return self._get_mod_text(mod_type, "INTernal:FUNCtion", channel)

    # ------------------------------------------------------------------
    # Burst
    # ------------------------------------------------------------------

    def set_burst_state(self, state: bool,
                        channel: Optional[int] = None) -> None:
        self.write(
            f":SOURce{self._ch(channel)}:BURSt:STATe "
            f"{'ON' if state else 'OFF'}"
        )

    def get_burst_state(self, channel: Optional[int] = None) -> bool:
        return self.query_bool(f":SOURce{self._ch(channel)}:BURSt:STATe?")

    def set_burst_mode(self, mode: str,
                       channel: Optional[int] = None) -> None:
        self.write(f":SOURce{self._ch(channel)}:BURSt:MODE {mode}")

    def get_burst_mode(self, channel: Optional[int] = None) -> str:
        return self._expand_value(
            self.query(f":SOURce{self._ch(channel)}:BURSt:MODE?")
        )

    def set_burst_ncycles(self, cycles,
                          channel: Optional[int] = None) -> None:
        self.write(f":SOURce{self._ch(channel)}:BURSt:NCYCles {cycles}")

    def get_burst_ncycles(self, channel: Optional[int] = None):
        value = self.query(f":SOURce{self._ch(channel)}:BURSt:NCYCles?")
        numeric = None if value.strip().upper().startswith("INF") else float(value)
        return "INFinity" if numeric is None or numeric >= 9e37 else int(numeric)

    def set_burst_phase(self, phase: float,
                        channel: Optional[int] = None) -> None:
        self.write(f":SOURce{self._ch(channel)}:BURSt:PHASe {phase}")

    def get_burst_phase(self, channel: Optional[int] = None) -> float:
        return self.query_float(f":SOURce{self._ch(channel)}:BURSt:PHASe?")

    def set_burst_period(self, seconds: float,
                         channel: Optional[int] = None) -> None:
        self.write(
            f":SOURce{self._ch(channel)}:BURSt:INTernal:PERiod {seconds:e}"
        )

    def get_burst_period(self, channel: Optional[int] = None) -> float:
        return self.query_float(
            f":SOURce{self._ch(channel)}:BURSt:INTernal:PERiod?"
        )

    def set_burst_delay(self, seconds: float,
                        channel: Optional[int] = None) -> None:
        self.write(f":TRIGger{self._ch(channel)}:DELay {seconds:e}")

    def get_burst_delay(self, channel: Optional[int] = None) -> float:
        return self.query_float(f":TRIGger{self._ch(channel)}:DELay?")

    def set_burst_trigger_source(self, source: str,
                                 channel: Optional[int] = None) -> None:
        self.write(f":TRIGger{self._ch(channel)}:SOURce {source}")

    def get_burst_trigger_source(self, channel: Optional[int] = None) -> str:
        return self._expand_value(
            self.query(f":TRIGger{self._ch(channel)}:SOURce?")
        )

    def set_burst_trigger_slope(self, slope: str,
                                channel: Optional[int] = None) -> None:
        self.write(f":TRIGger{self._ch(channel)}:SLOPe {slope}")

    def get_burst_trigger_slope(self, channel: Optional[int] = None) -> str:
        return self._expand_value(
            self.query(f":TRIGger{self._ch(channel)}:SLOPe?")
        )

    def burst_trigger(self, channel: Optional[int] = None) -> None:
        self.write(f":TRIGger{self._ch(channel)}:IMMediate")

    # ------------------------------------------------------------------
    # 输出开关 / 时钟
    # ------------------------------------------------------------------

    def set_output(self, state: bool,
                   channel: Optional[int] = None) -> None:
        """打开/关闭指定通道输出."""
        ch = self._ch(channel)
        self.write(f":OUTPut{ch}:STATe {'ON' if state else 'OFF'}")

    def get_output(self, channel: Optional[int] = None) -> bool:
        """读取指定通道输出状态。"""
        ch = self._ch(channel)
        value = self.query(f":OUTPut{ch}:STATe?").strip().upper()
        return value in {"1", "ON"}

    def set_ref_clock_source(self, source: str = "EXTernal") -> None:
        """设置参考时钟源 (INTernal / EXTernal)."""
        self.write(f":SYSTem:ROSCillator:SOURce {source}")

    def get_ref_clock_source(self) -> str:
        """读取参考时钟源。"""
        return self.query(":SYSTem:ROSCillator:SOURce?")
