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

import csv
import logging
import math
from typing import Mapping, Optional

import numpy as np
import pyvisa
from pyvisa.resources import MessageBasedResource

from signal_generator import _visa
from signal_generator.types import BurstMode, ModType, OutputPolarity, VoltageUnit, WaveShape, normalize_output_load

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 10000  # ms


class DG900Instrument:
    """RIGOL DG900 Pro 系列 PyVISA 封装."""

    MOD_TYPES = ("AM", "FM", "PM", "FSKey", "PWM")
    MIN_ARB_POINTS = 32
    MAX_ARB_POINTS = 16 * 1024 * 1024
    MAX_ARB_CHUNK_BYTES = 20_000
    DEFAULT_ARB_POINTS = 16_384
    MIN_SAMPLE_RATE = 1e-6
    MAX_SAMPLE_RATE = 312.5e6

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
                 channel: int = 1,
                 load_policy: Mapping[int, float | str] | None = None):
        self.resource_string = resource_string
        self.timeout = timeout
        self.channel = channel
        self._load_policy = dict(load_policy or {})
        self._rm: Optional[pyvisa.ResourceManager] = None
        self._inst: Optional[MessageBasedResource] = None
        self._selected_mod_types: dict[int, str] = {}
        self._arb_file_points: dict[str, int] = {}

    @property
    def load_policy(self) -> dict[int, float | str]:
        """连接时待应用的通道负载策略。"""
        return dict(self._load_policy)

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
        """打开 PyVISA 资源。"""
        self._rm = pyvisa.ResourceManager()
        self._inst = self._rm.open_resource(self.resource_string)
        self._inst.timeout = self.timeout
        try:
            idn = self.idn()
            for channel, load in self._load_policy.items():
                self.set_output_load(load, channel=channel)
        except Exception:
            self.disconnect()
            raise
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
            # pyvisa-py 的后端是进程级单例，rm.close() 会摧毁其他设备的会话
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

    def apply_wave(self, shape, channel=None, **kwargs):
        """使用 DG4000 的 freq/amp/offset/phase 参数配置波形。"""
        unknown = kwargs.keys() - {"freq", "amp", "offset", "phase", "delay"}
        if unknown:
            raise TypeError(f"未知 APPLy 参数 {sorted(unknown)}；旧 frequency/amplitude 请迁移为 freq/amp")
        if kwargs.get("delay") is not None:
            raise ValueError("DG912 Pro APPLy 不支持 DG4000 的脉冲延时参数 delay")
        if shape == "PULSe" and kwargs.get("phase") is not None:
            raise ValueError("共同 APPLy 脉冲接口不包含相位，请单独调用 set_phase_adjust()")
        ch = self._ch(channel)
        native_shape = {"CUSTom": "ARBitrary", "USER": "ARBitrary"}.get(shape, shape)
        if native_shape not in {"SINusoid", "SQUare", "RAMP", "PULSe", "NOISe", "DC", "ARBitrary"}:
            raise ValueError(f"DG912 Pro 不支持此 APPLy 波形: {shape}")
        params: list[str] = []
        if shape == "NOISe":
            if kwargs.get("amp") is not None:
                params = ["DEF", str(kwargs["amp"])]
                if kwargs.get("offset") is not None:
                    params.append(str(kwargs["offset"]))
        else:
            for name in ("freq", "amp", "offset", "phase"):
                if kwargs.get(name) is None:
                    break
                params.append(str(kwargs[name]))
        suffix = f" {','.join(params)}" if params else ""
        self.write(f":SOURce{ch}:APPLy:{native_shape}{suffix}")

    def set_shape(self, shape: WaveShape, channel: Optional[int] = None) -> None:
        native_shape = {"CUSTom": "ARBitrary", "USER": "ARBitrary"}.get(shape, shape)
        self.write(f":SOURce{self._ch(channel)}:FUNCtion {native_shape}")

    def get_shape(self, channel: Optional[int] = None) -> str:
        return self.get_wave_parameters(channel)["shape"]

    def set_frequency(self, freq: float,
                      channel: Optional[int] = None) -> None:
        self.write(f":SOURce{self._ch(channel)}:FREQuency {freq:e}")

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

    def set_phase_adjust(self, angle: float,
                         channel: Optional[int] = None) -> None:
        """兼容 DG4000 的相位方法名。"""
        self.set_phase(angle, channel)

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

    def set_pulse_width(self, width: float,
                        channel: Optional[int] = None) -> None:
        self.write(f":SOURce{self._ch(channel)}:FUNCtion:PULSe:WIDTh {width:e}")

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

    def set_dc_voltage(self, voltage: float,
                       channel: Optional[int] = None) -> None:
        """设置 DC 波形电平，不改变输出开关。"""
        ch = self._ch(channel)
        self.write(f":SOURce{ch}:APPLy:DC DEF,DEF,{voltage}")

    def setup_dc(self, voltage: float,
                 channel: Optional[int] = None) -> None:
        """设置 DC 电平并打开输出.

        DG900 Pro 使用 :SOURce:APPLy:DC 命令（不同于 DG4000 的
        :SOURce:FUNCtion:SHAPe DC），此命令自动配置波形为 DC、
        设置偏置电压并开启输出。
        """
        ch = self._ch(channel)
        self.set_dc_voltage(voltage, channel=ch)
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

    def set_mod_type(self, mod_type: ModType,
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

    def set_mod_fm_deviation(self, deviation: float,
                             channel: Optional[int] = None) -> None:
        self._set_mod_value("FM", "DEViation", deviation, channel)

    def get_mod_fm_deviation(self, channel: Optional[int] = None) -> float:
        return self._get_mod_float("FM", "DEViation", channel)

    def set_mod_pm_deviation(self, deviation: float,
                             channel: Optional[int] = None) -> None:
        self._set_mod_value("PM", "DEViation", deviation, channel)

    def get_mod_pm_deviation(self, channel: Optional[int] = None) -> float:
        return self._get_mod_float("PM", "DEViation", channel)

    def set_mod_fsk_frequency(self, freq: float,
                              channel: Optional[int] = None) -> None:
        self._set_mod_value("FSKey", "FREQuency", freq, channel)

    def get_mod_fsk_frequency(self, channel: Optional[int] = None) -> float:
        return self._get_mod_float("FSKey", "FREQuency", channel)

    def set_mod_fsk_rate(self, rate: float,
                         channel: Optional[int] = None) -> None:
        self._set_mod_value("FSKey", "INTernal:RATE", rate, channel)

    def get_mod_fsk_rate(self, channel: Optional[int] = None) -> float:
        return self._get_mod_float("FSKey", "INTernal:RATE", channel)

    def set_mod_fsk_polarity(self, polarity: OutputPolarity,
                             channel: Optional[int] = None) -> None:
        self._set_mod_value("FSKey", "POLarity", polarity, channel)

    def get_mod_fsk_polarity(self, channel: Optional[int] = None) -> str:
        return self._get_mod_text("FSKey", "POLarity", channel)

    def set_mod_pwm_deviation_dcycle(self, percent: float,
                                     channel: Optional[int] = None) -> None:
        self._set_mod_value("PWM", "DEViation:DCYCle", percent, channel)

    def get_mod_pwm_deviation_dcycle(self,
                                     channel: Optional[int] = None) -> float:
        return self._get_mod_float("PWM", "DEViation:DCYCle", channel)

    def set_mod_source(self, source: str,
                       channel: Optional[int] = None) -> None:
        ch = self._ch(channel)
        mod_type = self._selected_mod_types.get(ch) or self.get_mod_type(ch)
        self._set_mod_value(mod_type, "SOURce", source, channel)

    def get_mod_source(self, channel: Optional[int] = None) -> str:
        return self._get_mod_text(self.get_mod_type(channel), "SOURce", channel)

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

    def set_burst_mode(self, mode: BurstMode,
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

    def set_burst_ncycles(self, n,
                          channel: Optional[int] = None) -> None:
        self.write(f":SOURce{self._ch(channel)}:BURSt:NCYCles {n}")

    def get_burst_ncycles(self, channel: Optional[int] = None):
        value = self.query(f":SOURce{self._ch(channel)}:BURSt:NCYCles?")
        numeric = None if value.strip().upper().startswith("INF") else float(value)
        return "INFinity" if numeric is None or numeric >= 9e37 else int(numeric)

    def set_burst_phase(self, angle: float,
                        channel: Optional[int] = None) -> None:
        self.write(f":SOURce{self._ch(channel)}:BURSt:PHASe {angle}")

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

    def set_output_load(self, ohms: float | str,
                        channel: Optional[int] = None) -> None:
        """设置负载并回读验证；高阻使用 INF，避免长参数拼写差异。"""
        value = normalize_output_load(ohms)
        expected = None if value == "INF" else float(value)
        self.write(f":OUTPut{self._ch(channel)}:LOAD {value}")
        actual = float(self.get_output_load(channel))
        matches = actual >= 9e37 if expected is None else math.isclose(actual, expected, rel_tol=1e-6)
        if not matches:
            target = "High-Z" if expected is None else f"{expected:g} Ω"
            raise RuntimeError(
                f"DG900 CH{self._ch(channel)} 负载设置失败：期望 {target}，实际 {actual:g} Ω"
            )

    def get_output_load(self, channel: Optional[int] = None) -> str:
        return self.query(f":OUTPut{self._ch(channel)}:LOAD?")

    def set_voltage_unit(self, unit: VoltageUnit,
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

    def set_ref_clock_source(self, source: str) -> None:
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
        self.apply_wave("SINusoid", channel=ch, freq=freq, amp=amplitude, offset=offset, phase=phase)
        self.set_output(True, ch)

    def setup_square(self, freq: float, amplitude: float,
                     offset: float = 0.0, dcycle: float = 50.0,
                     phase: float = 0.0,
                     channel: Optional[int] = None) -> None:
        ch = self._ch(channel)
        self.apply_wave("SQUare", channel=ch, freq=freq, amp=amplitude, offset=offset, phase=phase)
        self.set_square_dcycle(dcycle, ch)
        self.set_output(True, ch)

    def setup_ramp(self, freq: float, amplitude: float,
                   offset: float = 0.0, symmetry: float = 50.0,
                   phase: float = 0.0,
                   channel: Optional[int] = None) -> None:
        ch = self._ch(channel)
        self.apply_wave("RAMP", channel=ch, freq=freq, amp=amplitude, offset=offset, phase=phase)
        self.set_ramp_symmetry(symmetry, ch)
        self.set_output(True, ch)

    def setup_pulse(self, freq: float, amplitude: float,
                    offset: float = 0.0, width: Optional[float] = None,
                    channel: Optional[int] = None) -> None:
        ch = self._ch(channel)
        self.apply_wave("PULSe", channel=ch, freq=freq, amp=amplitude, offset=offset)
        if width is not None:
            self.set_pulse_width(width, ch)
        self.set_output(True, ch)

    def setup_noise(self, amplitude: float, offset: float = 0.0,
                    channel: Optional[int] = None) -> None:
        ch = self._ch(channel)
        self.apply_wave("NOISe", channel=ch, amp=amplitude, offset=offset)
        self.set_output(True, ch)

    @staticmethod
    def _arb_codes(values) -> np.ndarray:
        if np.iscomplexobj(values):
            raise ValueError("任意波数据必须是实数")
        values = np.asarray(values, dtype=np.float64)
        if values.ndim != 1 or not np.isfinite(values).all() or np.any(np.abs(values) > 1):
            raise ValueError("任意波数据必须是一维、有限且位于 [-1, 1]，不会裁剪或重采样")
        return np.rint(values * np.where(values >= 0, 32767, 32768)).astype(np.int16)

    def _arb_chunks(self, codes: np.ndarray) -> list[str]:
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
            self.apply_wave("ARBitrary", channel=ch, freq=freq, amp=amplitude, offset=offset, phase=phase)
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

    def set_counter(self, enable=True):
        """打开或关闭频率计。"""
        self.write(f":COUNter:STATe {'ON' if enable else 'OFF'}")

    def get_counter_measure(self):
        """返回频率计的原始测量字符串。"""
        self._ensure_connected()
        return self._inst.query(":COUNter:MEASure?")

    def get_screenshot(self, format="BMP", save_path=None, timeout_ms=15000):
        """完整读取 BMP/PNG 图片，保存后仍返回图片字节。"""
        return _visa.get_screenshot(self, format, save_path, timeout_ms, supports_png=True)

    def recover_connection(self, drain_reads=3):
        """清理通信队列并返回身份，不复位仪器。"""
        return _visa.recover_connection(self, drain_reads)

    def recover_connection_with_reset(self, wait_seconds=8.0):
        """显式复位仪器并返回身份，会恢复仪器默认设置。"""
        if wait_seconds is None:
            raise ValueError("wait_seconds 必须是有限的非负数")
        self._selected_mod_types.clear()
        return _visa.recover_connection(self, 3, wait_seconds=wait_seconds)

    @classmethod
    def reset_stuck_resource(cls, resource_string, wait_seconds=8.0):
        """无需预先连接即可显式复位资源，操作后关闭临时连接。"""
        device = cls(resource_string)
        try:
            return device.recover_connection_with_reset(wait_seconds)
        finally:
            device.disconnect()

    def set_mod_am_dssc(self, state, channel=None):
        """设置 AM 载波抑制，不改变调制开关。"""
        self._set_mod_value("AM", "DSSC", "ON" if state else "OFF", channel)

    @staticmethod
    def _file_path(path: str) -> str:
        """统一内部路径，防止别名绕过覆盖检查。"""
        if not isinstance(path, str) or not path.strip():
            raise ValueError("仪器文件路径不能为空")
        path = path.strip().replace("/", "\\")
        if any(ord(char) < 32 or char in '\";' for char in path):
            raise ValueError("仪器文件路径包含无效字符")
        if ":" not in path:
            path = "INT:\\" + path
        drive, relative = path.split(":", 1)
        if drive.upper() not in {"INT", "USB"} or not relative.startswith("\\") or ":" in relative:
            raise ValueError("路径必须位于 INT:\\ 或 USB:\\")
        parts: list[str] = []
        for part in relative.split("\\"):
            if part in {"", "."}:
                continue
            if part == "..":
                if not parts:
                    raise ValueError("路径不能越过存储器根目录")
                parts.pop()
            else:
                parts.append(part)
        normalized = drive.upper() + ":\\" + "\\".join(parts)
        if len(normalized) > 200:
            raise ValueError("仪器文件路径不能超过 200 个字符")
        return normalized

    def list_files(self, path="INT:\\"):
        """返回 name、attribute 和 size_bytes；字节大小不能用于推算点数。"""
        path = self._file_path(path)
        response = self.query(f':MMEMory:CATalog:ALL? "{path}"')
        try:
            fields = next(csv.reader([response], skipinitialspace=True, strict=True))
        except csv.Error as exc:
            raise ValueError(f"目录返回格式无效: {response!r}") from exc
        if len(fields) < 2:
            raise ValueError(f"目录返回格式无效: {response!r}")
        try:
            if min(int(fields[0]), int(fields[1])) < 0:
                raise ValueError
            result = []
            for entry in fields[2:]:
                name, attribute, size = entry.rsplit(",", 2)
                size_bytes = int(size)
                if not name or size_bytes < 0:
                    raise ValueError
                result.append({"name": name, "attribute": attribute, "size_bytes": size_bytes})
        except ValueError as exc:
            raise ValueError(f"目录返回格式无效: {response!r}") from exc
        self.raise_for_errors()
        return result

    def delete_file(self, file_path):
        """删除仪器文件并使本实例对应点数记录失效。"""
        file_path = self._file_path(file_path)
        self._arb_file_points.pop(file_path.casefold(), None)
        self.write(f':MMEMory:DELete "{file_path}"')
        self.wait_for_operation_complete()
        self.raise_for_errors()

    def upload_arb_wave(self, file_path, data, advanced=False, *, overwrite=False):
        """严格上传 .arb 文件，全部确认成功后返回并记录准确点数。"""
        file_path = self._file_path(file_path)
        if not file_path.lower().endswith(".arb"):
            raise ValueError("上传文件名必须以 .arb 结尾")
        codes = self._arb_codes(data)
        count = len(codes)
        if advanced:
            if not self.MIN_ARB_POINTS <= count <= self.MAX_ARB_POINTS:
                raise ValueError(f"高级任意波点数必须在 {self.MIN_ARB_POINTS}～{self.MAX_ARB_POINTS} 之间")
        elif count != self.DEFAULT_ARB_POINTS:
            raise ValueError(f"普通任意波必须恰好 {self.DEFAULT_ARB_POINTS} 点")
        folder, name = file_path.rsplit("\\", 1)
        existing = self.list_files(folder + "\\")
        if not overwrite and any(item["name"].casefold() == name.casefold() for item in existing):
            raise FileExistsError(f"仪器文件已存在: {file_path}；覆盖需显式设置 overwrite=True")
        chunks = self._arb_chunks(codes)
        self._ensure_connected()
        instrument = self._inst
        old_timeout = instrument.timeout
        old_read, old_write = instrument.read_termination, instrument.write_termination
        key = file_path.casefold()
        self._arb_file_points.pop(key, None)
        try:
            instrument.timeout = max(self.timeout, 60_000)
            instrument.read_termination = instrument.write_termination = "\n"
            self.clear_status()
            for index, chunk in enumerate(chunks):
                flag = "END" if index == len(chunks) - 1 else "HEADer" if index == 0 else "CONTinue"
                self.write(f':MMEMory:TRACe:ARB:DATA:DAC "{file_path}",{flag},{chunk}')
                self.wait_for_operation_complete()
                self.raise_for_errors()
        finally:
            instrument.timeout = old_timeout
            instrument.read_termination, instrument.write_termination = old_read, old_write
        self._arb_file_points[key] = count
        return count

    def get_arb_wave_points(self, file_path):
        """仅返回本实例成功上传的点数；未知文件由 config_arb_wave(points=...) 指定。"""
        file_path = self._file_path(file_path)
        try:
            return self._arb_file_points[file_path.casefold()]
        except KeyError as exc:
            raise KeyError(f"没有 {file_path} 的成功上传记录，请显式提供 points") from exc

    def config_arb_wave(self, channel, file_path, freq=None, sample_rate=None,
                        vpp=1.0, offset=0.0, phase=0.0, *, points=None):
        """配置已存文件，16384 点且指定 freq 时用普通模式，其余用高级模式。"""
        if isinstance(channel, bool) or not isinstance(channel, (int, np.integer)) or channel not in (1, 2):
            raise ValueError("DG912 Pro 通道只能是 1 或 2")
        if (freq is None) == (sample_rate is None):
            raise ValueError("freq 和 sample_rate 必须且只能提供一个")
        file_path = self._file_path(file_path)
        if not file_path.lower().endswith((".arb", ".csv")):
            raise ValueError("此接口支持 .arb/.csv，.txt 加载需要未提供的分隔符和数据类型参数")
        points = self.get_arb_wave_points(file_path) if points is None else points
        if isinstance(points, bool) or not isinstance(points, (int, np.integer)) or not self.MIN_ARB_POINTS <= points <= self.MAX_ARB_POINTS:
            raise ValueError(f"points 必须是 {self.MIN_ARB_POINTS}～{self.MAX_ARB_POINTS} 之间的整数")
        if not all(math.isfinite(value) for value in (vpp, offset, phase)) or vpp <= 0:
            raise ValueError("幅度必须为有限正数，偏置和相位必须有限")
        if freq is not None and (not math.isfinite(freq) or freq <= 0):
            raise ValueError("freq 必须为有限正数")
        advanced = sample_rate is not None or points != self.DEFAULT_ARB_POINTS
        if advanced:
            sample_rate = points * freq if sample_rate is None else sample_rate
            if not math.isfinite(sample_rate) or not self.MIN_SAMPLE_RATE <= sample_rate <= self.MAX_SAMPLE_RATE:
                raise ValueError(f"高级采样率需在 {self.MIN_SAMPLE_RATE}～{self.MAX_SAMPLE_RATE} Sa/s，不会自动降频")
        prefix = f":SOURce{channel}"
        self.clear_status()
        self.write(f"{prefix}:FUNCtion:SEQuence:ARB:STATe {int(advanced)}")
        if advanced:
            self.write(f'{prefix}:FUNCtion:SEQuence:ARB:LOAD "{file_path}"')
            self.write(f"{prefix}:FUNCtion:SEQuence:ARB:SRATe {sample_rate}")
        else:
            self.set_shape("CUSTom", channel)
            self.write(f':MMEMory:LOAD:DATA {channel},"{file_path}"')
            self.set_frequency(freq, channel)
        self.set_amplitude(vpp, channel)
        self.set_offset(offset, channel)
        self.set_phase_adjust(phase, channel)
        self.wait_for_operation_complete()
        self.raise_for_errors()
