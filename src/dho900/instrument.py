"""RIGOL DHO900 的基础 SCPI 控制；连接前执行仓库硬件门控。"""

from __future__ import annotations

import math
import re
import time
from typing import Any

import pyvisa
from hardware_gate import require_hardware_access

MODELS = frozenset({"DHO914", "DHO914S", "DHO924", "DHO924S"})
MEMORY_DEPTHS = (1_000, 10_000, 100_000, 1_000_000, 5_000_000, 10_000_000, 25_000_000, 50_000_000)
RATE_REL_TOLERANCE = 1e-6


def _positive(value: float, name: str) -> float:
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{name} 必须为有限正数")
    return result


def _finite(value: float, name: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} 必须为有限数")
    return result


def _integer(value: Any, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} 必须为 {minimum}--{maximum} 的整数")
    number = float(value)
    if not math.isfinite(number) or not number.is_integer() or not minimum <= number <= maximum:
        raise ValueError(f"{name} 必须为 {minimum}--{maximum} 的整数")
    return int(number)


def _channel(channel: int) -> int:
    return _integer(channel, "模拟通道", 1, 4)


def _source(source: str) -> str:
    match = re.fullmatch(r"(?:C|CHAN|CHANNEL)([1-4])", str(source).strip().upper())
    if match is None:
        raise ValueError("DHO900 基础接口仅支持模拟通道 C1--C4，不支持 EXT")
    return f"CHANnel{match[1]}"


def _choice(value: str, choices: dict[str, str], name: str) -> str:
    try:
        return choices[str(value).strip().upper()]
    except KeyError as exc:
        raise ValueError(f"不支持的{name}: {value}") from exc


class DHO900Instrument:
    """与 SDS 同名的基础控制接口；构造不连接、不复位、不自动识别地址。"""

    def __init__(self, resource_string: str, timeout: int = 10000, *, read_chunk_points: int = 10000):
        self.resource_string = str(resource_string).strip()
        if not self.resource_string or self.resource_string.lower().startswith(("http:", "https:")):
            raise ValueError("请填写 VISA 资源，不使用 HTTP 网页地址建立 SCPI 连接")
        self.timeout = _integer(timeout, "通信超时毫秒数", 1, 2_147_483_647)
        # 这是客户端分块预算，不是手册未给出的单次传输硬件上限。
        self.read_chunk_points = _integer(read_chunk_points, "读取分块点数", 1, 50_000_000)
        self._rm = None
        self._inst = None
        self._waveform_start = 0
        self._single_armed = False

    def connect(self) -> None:
        require_hardware_access(f"DHO900 {self.resource_string}")
        if self._inst is not None:
            return
        self._rm = pyvisa.ResourceManager()
        try:
            self._inst = self._rm.open_resource(self.resource_string, open_timeout=self.timeout)
            self._inst.timeout = self.timeout
            self._inst.read_termination = "\n"
            self._inst.write_termination = "\n"
            self._inst.chunk_size = 1024 * 1024
            identity = self.idn().split(",")
            if len(identity) < 2 or identity[0].strip().upper() not in {"RIGOL", "RIGOL TECHNOLOGIES"} or identity[1].strip().upper() not in MODELS:
                raise ValueError("资源未返回受支持的 RIGOL DHO900 身份，已拒绝继续控制")
        except BaseException:
            try:
                self.disconnect()
            except Exception:
                pass  # 连接原始异常优先于通信句柄清理异常。
            raise

    def disconnect(self) -> None:
        instrument, self._inst = self._inst, None
        # 与 SDS 一致，不关闭进程级共享 VISA 后端，以免销毁其他设备会话。
        self._rm = None
        self._single_armed = False
        if instrument is not None:
            instrument.close()

    def __enter__(self) -> DHO900Instrument:
        self.connect()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if exc_type is None:
            self.disconnect()
        else:
            try:
                self.disconnect()
            except Exception:
                pass

    def _ensure_connected(self) -> None:
        if self._inst is None:
            self.connect()

    def write(self, command: str) -> None:
        self._ensure_connected()
        self._inst.write(command)

    def query(self, command: str) -> str:
        self._ensure_connected()
        return str(self._inst.query(command)).strip()

    def query_float(self, command: str) -> float:
        return _finite(float(self.query(command)), f"{command} 回读")

    def query_int(self, command: str) -> int:
        return _integer(self.query(command), f"{command} 回读", 0, 50_000_000)

    def idn(self) -> str:
        return self.query("*IDN?")

    def check_errors(self) -> None:
        """查询一条错误队列消息；非零就停止，不清空后继续采集。"""
        response = self.query(":SYSTem:ERRor?")
        code, separator, _ = response.partition(",")
        if not separator or int(code) != 0:
            raise RuntimeError(f"DHO900 SCPI 错误：{response}")

    def reset(self) -> None:
        """仅在调用方显式请求时复位；连接和采集均不自动调用。"""
        self.write("*RST")

    def set_sampling_rate(self, rate: float) -> None:
        """校验协调后的实际率；DHO900 没有采样率写命令，不发送伪指令。"""
        requested = _positive(rate, "目标采样率")
        actual = self.get_sampling_rate()
        if not math.isclose(actual, requested, rel_tol=RATE_REL_TOLERANCE, abs_tol=0):
            raise ValueError(f"DHO900 不能满足目标采样率 {requested:g} Sa/s，实际 {actual:g} Sa/s")

    def get_sampling_rate(self) -> float:
        return _positive(self.query_float(":ACQuire:SRATe?"), "实际采样率")

    def set_memory_management(self, mode: str) -> None:
        raise NotImplementedError("DHO900 没有 SDS 的独立 AUTO/FSRate/FMDepth 存储管理接口；请使用完整采集配置")

    def get_memory_management(self) -> str:
        raise NotImplementedError("DHO900 没有 SDS 的存储管理模式查询，不伪造 FSRate 回读")

    def set_memory_depth(self, depth: str | int) -> None:
        text = str(depth).strip().upper()
        if text == "AUTO":
            self.write(":ACQuire:MDEPth AUTO")
            return
        multiplier = 1_000_000 if text.endswith("M") else 1_000 if text.endswith("K") else 1
        number = float(text[:-1] if multiplier != 1 else text) * multiplier
        points = _integer(number, "存储深度", 1, 50_000_000)
        if points not in MEMORY_DEPTHS:
            raise ValueError(f"DHO900 不支持存储深度 {points}；可选 {MEMORY_DEPTHS}")
        self.write(f":ACQuire:MDEPth {points}")

    def get_memory_depth(self) -> str:
        return self.query(":ACQuire:MDEPth?")

    def get_actual_points(self) -> int:
        """读取整帧深度；AUTO 无数字回读时拒绝，不推测帧点数。"""
        return _integer(self.get_memory_depth(), "实际存储深度", 1, 50_000_000)

    @staticmethod
    def validate_acquire_type(acq_type: str, param: int | None = None) -> str:
        mode = _choice(acq_type, {"NORM": "NORMal", "NORMAL": "NORMal", "PEAK": "PEAK",
                                 "AVER": "AVERages", "AVERAGE": "AVERages", "AVERAGES": "AVERages"}, "采集类型")
        if param is not None:
            count = _integer(param, "平均次数", 2, 65536)
            if mode != "AVERages" or count & (count - 1):
                raise ValueError("只有平均采集支持参数，次数须为 2--65536 的二次幂；不支持 SDS ERES")
        return mode

    def set_acquire_type(self, acq_type: str, param: int | None = None) -> None:
        mode = self.validate_acquire_type(acq_type, param)
        self.write(f":ACQuire:TYPE {mode}")
        if param is not None:
            self.write(f":ACQuire:AVERages {param}")

    def get_acquire_type(self) -> str:
        return self.query(":ACQuire:TYPE?")

    def set_timebase_scale(self, scale: float) -> None:
        self.write(f":TIMebase:MAIN:SCALe {_positive(scale, '时基'):.12g}")

    def get_timebase_scale(self) -> float:
        return _positive(self.query_float(":TIMebase:MAIN:SCALe?"), "实际时基")

    def set_timebase_delay(self, delay: float) -> None:
        self.write(f":TIMebase:MAIN:OFFSet {_finite(delay, '时基偏移'):.12g}")

    def get_timebase_delay(self) -> float:
        return self.query_float(":TIMebase:MAIN:OFFSet?")

    def set_channel_state(self, channel: int, state: bool) -> None:
        if not isinstance(state, bool):
            raise ValueError("通道开关须为 bool")
        self.write(f":CHANnel{_channel(channel)}:DISPlay {'ON' if state else 'OFF'}")

    def get_channel_state(self, channel: int) -> bool:
        return _choice(self.query(f":CHANnel{_channel(channel)}:DISPlay?"),
                       {"1": "ON", "ON": "ON", "0": "OFF", "OFF": "OFF"}, "通道开关回读") == "ON"

    def set_channel_scale(self, channel: int, scale: float) -> None:
        self.write(f":CHANnel{_channel(channel)}:SCALe {_positive(scale, '垂直档位'):.12g}")

    def get_channel_scale(self, channel: int) -> float:
        return _positive(self.query_float(f":CHANnel{_channel(channel)}:SCALe?"), "实际垂直档位")

    def set_channel_offset(self, channel: int, offset: float) -> None:
        self.write(f":CHANnel{_channel(channel)}:OFFSet {_finite(offset, '垂直偏移'):.12g}")

    def get_channel_offset(self, channel: int) -> float:
        return self.query_float(f":CHANnel{_channel(channel)}:OFFSet?")

    def set_channel_coupling(self, channel: int, coupling: str) -> None:
        mode = _choice(coupling, {"AC": "AC", "DC": "DC", "GND": "GND"}, "通道耦合")
        self.write(f":CHANnel{_channel(channel)}:COUPling {mode}")

    def get_channel_coupling(self, channel: int) -> str:
        return self.query(f":CHANnel{_channel(channel)}:COUPling?")

    def set_channel_impedance(self, channel: int, impedance: str) -> None:
        """DHO900 固定 1 MΩ；接受同值配置，不发送手册不存在的阻抗命令。"""
        _channel(channel)
        if str(impedance).strip().upper() not in {"ONEMEG", "1M", "1MOHM", "1000000"}:
            raise NotImplementedError("DHO900 基础接口仅支持固定 1 MΩ 输入，不支持 SDS 的 50 Ω 切换")

    def get_channel_impedance(self, channel: int) -> str:
        """返回型号的固定输入阻抗，不代表执行了远程查询。"""
        _channel(channel)
        return "ONEMeg"

    def set_channel_probe(self, channel: int, attenuation: float) -> None:
        value = _positive(attenuation, "探头比")
        allowed = {factor * 10 ** power for factor in (1, 2, 5) for power in range(-3, 5)}
        if value not in allowed:
            raise ValueError("探头比须为 0.001--50000 的 1/2/5 系列值")
        self.write(f":CHANnel{_channel(channel)}:PROBe {value:.12g}")

    def get_channel_probe(self, channel: int) -> float:
        return _positive(self.query_float(f":CHANnel{_channel(channel)}:PROBe?"), "实际探头比")

    def get_channel_units(self, channel: int) -> str:
        return self.query(f":CHANnel{_channel(channel)}:UNITs?")

    @staticmethod
    def validate_trigger_mode(mode: str) -> str:
        if str(mode).strip().upper() == "FTRIG":
            raise NotImplementedError("DHO900 的 TFORce 不是 SDS FTRIG 完整帧确认协议，基础接口不做等价替换")
        return _choice(mode, {"AUTO": "AUTO", "NORM": "NORMal", "NORMAL": "NORMal",
                              "SING": "SINGle", "SINGLE": "SINGle"}, "触发方式")

    def set_trigger_mode(self, mode: str) -> None:
        canonical = self.validate_trigger_mode(mode)
        # 手册规定 SINGle 与根命令 :SINGle 等价；只发送一次，避免重复布防。
        self.write(f":TRIGger:SWEep {canonical}")
        self._single_armed = canonical == "SINGle"

    def get_trigger_mode(self) -> str:
        return _choice(self.query(":TRIGger:SWEep?"), {"AUTO": "AUTO", "NORM": "NORMal", "SING": "SINGle"}, "触发方式回读")

    def set_trigger_type(self, trig_type: str) -> None:
        if str(trig_type).strip().upper() != "EDGE":
            raise NotImplementedError("DHO900 基础接口仅支持 EDGE 触发")
        self.write(":TRIGger:MODE EDGE")

    def get_trigger_type(self) -> str:
        return self.query(":TRIGger:MODE?")

    def set_trigger_source(self, source: str) -> None:
        self.write(f":TRIGger:EDGE:SOURce {_source(source)}")

    def get_trigger_source(self) -> str:
        source = _source(self.query(":TRIGger:EDGE:SOURce?"))
        return f"C{source[-1]}"

    @staticmethod
    def validate_trigger_slope(slope: str) -> str:
        return _choice(slope, {"RISING": "POSitive", "POS": "POSitive", "POSITIVE": "POSitive",
                               "FALLING": "NEGative", "NEG": "NEGative", "NEGATIVE": "NEGative",
                               "ALTERNATE": "RFALl", "RFAL": "RFALl", "RFALL": "RFALl"}, "触发边沿")

    def set_trigger_slope(self, slope: str) -> None:
        self.write(f":TRIGger:EDGE:SLOPe {self.validate_trigger_slope(slope)}")

    def get_trigger_slope(self) -> str:
        return _choice(self.query(":TRIGger:EDGE:SLOPe?"),
                       {"POS": "RISing", "NEG": "FALLing", "RFAL": "ALTernate"}, "触发边沿回读")

    def set_trigger_level(self, level: float) -> None:
        self.write(f":TRIGger:EDGE:LEVel {_finite(level, '触发电平'):.12g}")

    def get_trigger_level(self) -> float:
        return self.query_float(":TRIGger:EDGE:LEVel?")

    def trigger_run(self) -> None:
        self._single_armed = False
        self.write(":RUN")

    def trigger_stop(self) -> None:
        self._single_armed = False
        self.write(":STOP")

    def trigger_status(self) -> str:
        """将原生 TD/STOP 转为 SDS 已用的状态名称，未知回读拒绝。"""
        return _choice(self.query(":TRIGger:STATus?"), {"TD": "Trig'd", "STOP": "Stop",
                       "WAIT": "Ready", "RUN": "Arm", "AUTO": "Auto"}, "触发状态回读")

    def wait_for_trigger(self, poll_interval: float = 0.05, timeout: float = 5.0) -> bool:
        """等待已布防的触发；SINGLE 的 STOP 表示完成，调用方须先显式布防。"""
        interval, timeout = _positive(poll_interval, "轮询间隔"), _positive(timeout, "等待超时")
        if self._single_armed:
            self.check_errors()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = self.trigger_status()
            if status == "Trig'd":
                self._single_armed = False
                return True
            if status == "Stop":
                completed = self._single_armed and self.get_trigger_mode() == "SINGle"
                self._single_armed = False
                return completed
            time.sleep(interval)
        return False

    def set_waveform_source(self, source: str) -> None:
        self.write(f":WAVeform:SOURce {_source(source)}")

    def set_waveform_mode(self, mode: str) -> None:
        canonical = _choice(mode, {"NORM": "NORMal", "NORMAL": "NORMal", "RAW": "RAW"}, "波形读取模式")
        self.write(f":WAVeform:MODE {canonical}")

    def set_waveform_width(self, width: str) -> None:
        if str(width).strip().upper() not in {"ASC", "ASCII"}:
            raise NotImplementedError("基础采集使用 ASCII 电压值，不假设 DHO900 WORD 的符号和端序")
        self.write(":WAVeform:FORMat ASCii")

    def set_waveform_start(self, start: int) -> None:
        """公共接口为零基偏移；线上命令换算成 RIGOL 的一基闭区间。"""
        value = _integer(start, "波形起点偏移", 0, 49_999_999)
        self.write(f":WAVeform:STARt {value + 1}")
        self._waveform_start = value

    def set_waveform_points(self, points: int) -> None:
        count = _integer(points, "波形点数", 1, 50_000_000)
        end = self._waveform_start + count
        if end > 50_000_000:
            raise ValueError("波形结束点超出 DHO900 最大深度")
        self.write(f":WAVeform:STOP {end}")

    def get_waveform_max_points(self) -> int:
        """返回本驱动的客户端分块预算，不冒充未规定的硬件查询。"""
        return self.read_chunk_points

    def get_waveform_preamble(self) -> str:
        """RIGOL 原生十字段文本；由对应采集器解析，不能交给 SDS 二进制解析器。"""
        return self.query(":WAVeform:PREamble?")

    def get_waveform_data(self) -> bytes:
        if self.query(":WAVeform:FORMat?").upper() not in {"ASC", "ASCII"}:
            raise ValueError("波形格式不是 ASCII，拒绝按电压文本解码")
        return self.query(":WAVeform:DATA?").encode("ascii")
