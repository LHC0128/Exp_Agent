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
    MIN_ARB_POINTS = 32
    MAX_ARB_POINTS = 16 * 1024 * 1024
    MAX_ARB_CHUNK_BYTES = 20_000

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
        "INF": "INFinity",
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
        self._selected_mod_types: dict[int, str] = {}

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

    def __enter__(self) -> "DG900Instrument":
        self.connect()
        return self

    def __exit__(self, *args) -> None:
        self.disconnect()

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

    def set_phase_adjust(self, phase: float,
                         channel: Optional[int] = None) -> None:
        """兼容 DG4000 的相位方法名。"""
        self.set_phase(phase, channel)

    def get_phase_adjust(self, channel: Optional[int] = None) -> float:
        return self.get_phase(channel)

    def phase_init(self, channel: Optional[int] = None) -> None:
        """执行 DG900 Pro 原生同相位操作。"""
        self.write(f":SOURce{self._ch(channel)}:PHASe:SYNChronize")

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

    def set_pulse_dcycle(self, percent: float,
                         channel: Optional[int] = None) -> None:
        self.write(
            f":SOURce{self._ch(channel)}:FUNCtion:PULSe:DCYCle {percent}"
        )

    def get_pulse_dcycle(self, channel: Optional[int] = None) -> float:
        return self.query_float(
            f":SOURce{self._ch(channel)}:FUNCtion:PULSe:DCYCle?"
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
        self.set_output(True, ch)

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

    def set_mod_type(self, mod_type: str,
                     channel: Optional[int] = None) -> None:
        """选择兼容接口后续 ``set_mod_state`` 使用的调制类型。"""
        if mod_type not in self.MOD_TYPES:
            raise ValueError(f"DG900 不支持调制类型: {mod_type}")
        self._selected_mod_types[self._ch(channel)] = mod_type

    def get_mod_type(self, channel: Optional[int] = None) -> str:
        ch = self._ch(channel)
        return (
            self.get_active_mod_type(ch)
            or self._selected_mod_types.get(ch, "AM")
        )

    def set_mod_state(self, state: bool,
                      channel: Optional[int] = None) -> None:
        ch = self._ch(channel)
        if not state:
            self.disable_all_mod(ch)
            return
        mod_type = (
            self._selected_mod_types.get(ch)
            or self.get_active_mod_type(ch)
            or "AM"
        )
        self.disable_all_mod(ch)
        self.set_mod_type_state(mod_type, True, ch)

    def get_mod_state(self, channel: Optional[int] = None) -> bool:
        return self.get_active_mod_type(channel) is not None

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
        ch = self._ch(channel)
        if str(mode).upper().startswith("INF"):
            self.write(f":SOURce{ch}:BURSt:MODE TRIGgered")
            self.set_burst_ncycles("INFinity", ch)
            return
        self.write(f":SOURce{ch}:BURSt:MODE {mode}")

    def get_burst_mode(self, channel: Optional[int] = None) -> str:
        ch = self._ch(channel)
        mode = self._expand_value(
            self.query(f":SOURce{ch}:BURSt:MODE?")
        )
        if mode == "TRIGgered":
            try:
                if self.get_burst_ncycles(ch) == "INFinity":
                    return "INFinity"
            except Exception:
                pass
        return mode

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

    def set_output_load(self, ohms,
                        channel: Optional[int] = None) -> None:
        value = "INFinity" if str(ohms).upper() in {"INF", "INFINITY"} else ohms
        self.write(f":OUTPut{self._ch(channel)}:LOAD {value}")

    def get_output_load(self, channel: Optional[int] = None) -> str:
        return self._expand_value(
            self.query(f":OUTPut{self._ch(channel)}:LOAD?")
        )

    def set_voltage_unit(self, unit: str,
                         channel: Optional[int] = None) -> None:
        normalized = str(unit).upper()
        if normalized not in {"VPP", "VRMS", "DBM"}:
            raise ValueError("幅度单位必须为 VPP、VRMS 或 DBM")
        self.write(f":SOURce{self._ch(channel)}:VOLTage:UNIT {normalized}")

    def get_voltage_unit(self, channel: Optional[int] = None) -> str:
        return self.query(
            f":SOURce{self._ch(channel)}:VOLTage:UNIT?"
        ).strip().upper()

    def set_sync_state(self, state: bool,
                       channel: Optional[int] = None) -> None:
        self.write(
            f":OUTPut{self._ch(channel)}:SYNC {'ON' if state else 'OFF'}"
        )

    def get_sync_state(self, channel: Optional[int] = None) -> bool:
        return self.query_bool(f":OUTPut{self._ch(channel)}:SYNC?")

    def set_ref_clock_source(self, source: str = "EXTernal") -> None:
        """设置参考时钟源 (INTernal / EXTernal)."""
        self.write(f":SYSTem:ROSCillator:SOURce {source}")

    def get_ref_clock_source(self) -> str:
        """读取参考时钟源。"""
        return self.query(":SYSTem:ROSCillator:SOURce?")

    # ------------------------------------------------------------------
    # 便捷波形与任意波兼容接口
    # ------------------------------------------------------------------

    def setup_sine(self, freq: float, amplitude: float,
                   offset: float = 0.0, phase: float = 0.0,
                   channel: Optional[int] = None) -> None:
        ch = self._ch(channel)
        self.apply_wave("SINusoid", freq, amplitude, offset, phase, ch)
        self.set_output(True, ch)

    def setup_square(self, freq: float, amplitude: float,
                     offset: float = 0.0, dcycle: float = 50.0,
                     phase: float = 0.0,
                     channel: Optional[int] = None) -> None:
        ch = self._ch(channel)
        self.apply_wave("SQUare", freq, amplitude, offset, phase, ch)
        self.set_square_dcycle(dcycle, ch)
        self.set_output(True, ch)

    def setup_ramp(self, freq: float, amplitude: float,
                   offset: float = 0.0, symmetry: float = 50.0,
                   phase: float = 0.0,
                   channel: Optional[int] = None) -> None:
        ch = self._ch(channel)
        self.apply_wave("RAMP", freq, amplitude, offset, phase, ch)
        self.set_ramp_symmetry(symmetry, ch)
        self.set_output(True, ch)

    def setup_pulse(self, freq: float, amplitude: float,
                    offset: float = 0.0, width: Optional[float] = None,
                    channel: Optional[int] = None) -> None:
        ch = self._ch(channel)
        self.apply_wave("PULSe", freq, amplitude, offset, None, ch)
        if width is not None:
            self.set_pulse_width(width, ch)
        self.set_output(True, ch)

    def setup_noise(self, amplitude: float, offset: float = 0.0,
                    channel: Optional[int] = None) -> None:
        ch = self._ch(channel)
        self.apply_wave("NOISe", None, amplitude, offset, None, ch)
        self.set_output(True, ch)

    @staticmethod
    def _arb_codes(values) -> list[int]:
        codes = []
        for raw in values:
            value = max(-1.0, min(1.0, float(raw)))
            scale = 32767 if value >= 0 else 32768
            codes.append(int(round(value * scale)))
        return codes

    def _arb_chunks(self, codes: list[int]) -> list[str]:
        chunks: list[str] = []
        current: list[str] = []
        size = 0
        for code in codes:
            token = str(code)
            added = len(token) + (1 if current else 0)
            if current and size + added > self.MAX_ARB_CHUNK_BYTES:
                chunks.append(",".join(current))
                current = []
                size = 0
                added = len(token)
            current.append(token)
            size += added
        if current:
            chunks.append(",".join(current))
        return chunks

    def send_arbitrary_waveform(self, values,
                                channel: Optional[int] = None) -> None:
        """按 DG900 Pro 手册将归一化波表分块下载到易失存储器。"""
        ch = self._ch(channel)
        codes = self._arb_codes(values)
        if not self.MIN_ARB_POINTS <= len(codes) <= self.MAX_ARB_POINTS:
            raise ValueError(
                f"点数需在 {self.MIN_ARB_POINTS} ~ {self.MAX_ARB_POINTS} 之间，"
                f"实际为 {len(codes)}"
            )
        chunks = self._arb_chunks(codes)
        self.clear_status()
        for index, data in enumerate(chunks):
            if len(chunks) == 1 or index == len(chunks) - 1:
                flag = "END"
            elif index == 0:
                flag = "HEADer"
            else:
                flag = "CONTinue"
            self.write(
                f":SOURce{ch}:TRACe:DATA:DAC16 CODE,{flag},{data}"
            )
        self.wait_for_operation_complete()
        self.raise_for_errors()

    def setup_arbitrary(self, y_values, freq: float = 1000.0,
                        amplitude: float = 5.0, offset: float = 0.0,
                        phase: float = 0.0,
                        channel: Optional[int] = None,
                        output: bool = True) -> None:
        ch = self._ch(channel)
        output_before: bool | None = None
        try:
            output_before = self.get_output(ch)
        except Exception:
            pass
        try:
            if not output:
                self.set_output(False, ch)
            self.send_arbitrary_waveform(y_values, ch)
            self.apply_wave("ARBitrary", freq, amplitude, offset, phase, ch)
            if output:
                self.set_output(True, ch)
        except Exception:
            if output_before is not None:
                try:
                    self.set_output(output_before, ch)
                except Exception:
                    logger.exception("DG900 任意波失败后恢复输出状态失败")
            raise

    def all_off(self) -> None:
        for channel in (1, 2):
            self.set_output(False, channel)
