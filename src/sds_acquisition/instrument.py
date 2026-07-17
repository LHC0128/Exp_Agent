import time
import logging
from typing import Optional

import pyvisa
from pyvisa.resources import MessageBasedResource

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 10000      # ms
DEFAULT_CHUNK_SIZE = 20 * 1024 * 1024  # 20 MB


class SDSInstrument:
    """SDS 系列数字示波器 PyVISA 封装."""

    def __init__(self, resource_string: str, timeout: int = DEFAULT_TIMEOUT):
        self.resource_string = resource_string
        self.timeout = timeout
        self._rm: Optional[pyvisa.ResourceManager] = None
        self._inst: Optional[MessageBasedResource] = None

    # ------------------------------------------------------------------
    # 连接管理
    # ------------------------------------------------------------------

    def connect(self) -> None:
        self._rm = pyvisa.ResourceManager()
        self._inst = self._rm.open_resource(self.resource_string)
        self._inst.timeout = self.timeout
        self._inst.chunk_size = DEFAULT_CHUNK_SIZE
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

    def __enter__(self) -> "SDSInstrument":
        self.connect()
        return self

    def __exit__(self, *args) -> None:
        self.disconnect()

    # ------------------------------------------------------------------
    # 底层 I/O
    # ------------------------------------------------------------------

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

    def read_raw(self) -> bytes:
        self._ensure_connected()
        return self._inst.read_raw()

    def query_float(self, command: str) -> float:
        return float(self.query(command))

    def query_int(self, command: str) -> int:
        return int(float(self.query(command)))

    def query_binary(self, command: str) -> bytes:
        """发送命令并读取 SCPI 二进制块（#N<len><data>），返回数据部分."""
        self.write(command)
        raw = self.read_raw()
        return self._strip_binary_header(raw)

    @staticmethod
    def _strip_binary_header(data: bytes) -> bytes:
        """移除 SCPI 二进制块头，返回纯数据。"""
        if data and data[0:1] == b"#":
            digits_start = 1
            digits_end = digits_start + int(chr(data[1]))
            length = int(data[digits_start:digits_end])
            header_len = 2 + len(str(length))
            return data[header_len:header_len + length]
        return data

    # ------------------------------------------------------------------
    # 识别与复位
    # ------------------------------------------------------------------

    def idn(self) -> str:
        return self.query("*IDN?")

    def reset(self) -> None:
        self.write("*RST")

    # ------------------------------------------------------------------
    # 采集设置
    # ------------------------------------------------------------------

    def set_sampling_rate(self, rate: float) -> None:
        self.write(f":ACQuire:SRATe {rate:.6E}")

    def get_sampling_rate(self) -> float:
        return self.query_float(":ACQuire:SRATe?")

    def set_memory_depth(self, depth: str) -> None:
        self.write(f":ACQuire:MDEPth {depth}")

    def get_memory_depth(self) -> str:
        return self.query(":ACQuire:MDEPth?")

    def set_acquire_type(self, acq_type: str, param: Optional[int] = None) -> None:
        if param is not None:
            self.write(f":ACQuire:TYPE {acq_type},{param}")
        else:
            self.write(f":ACQuire:TYPE {acq_type}")

    def get_acquire_type(self) -> str:
        return self.query(":ACQuire:TYPE?")

    def get_actual_points(self) -> float:
        return self.query_float(":ACQuire:POINts?")

    # ------------------------------------------------------------------
    # 时基设置
    # ------------------------------------------------------------------

    def set_timebase_scale(self, scale: float) -> None:
        self.write(f":TIMebase:SCALe {scale:.6E}")

    def get_timebase_scale(self) -> float:
        return self.query_float(":TIMebase:SCALe?")

    def set_timebase_delay(self, delay: float) -> None:
        self.write(f":TIMebase:DELay {delay:.6E}")

    def get_timebase_delay(self) -> float:
        return self.query_float(":TIMebase:DELay?")

    # ------------------------------------------------------------------
    # 通道设置
    # ------------------------------------------------------------------

    def set_channel_state(self, channel: int, state: bool) -> None:
        self.write(f":CHANnel{channel}:SWITch {'ON' if state else 'OFF'}")

    def get_channel_state(self, channel: int) -> bool:
        return self.query(f":CHANnel{channel}:SWITch?").upper() in {"1", "ON"}

    def set_channel_scale(self, channel: int, scale: float) -> None:
        self.write(f":CHANnel{channel}:SCALe {scale:.6E}")

    def get_channel_scale(self, channel: int) -> float:
        return self.query_float(f":CHANnel{channel}:SCALe?")

    def set_channel_offset(self, channel: int, offset: float) -> None:
        self.write(f":CHANnel{channel}:OFFSet {offset:.6E}")

    def get_channel_offset(self, channel: int) -> float:
        return self.query_float(f":CHANnel{channel}:OFFSet?")

    def set_channel_coupling(self, channel: int, coupling: str) -> None:
        self.write(f":CHANnel{channel}:COUPling {coupling}")

    def get_channel_coupling(self, channel: int) -> str:
        return self.query(f":CHANnel{channel}:COUPling?")

    def set_channel_impedance(self, channel: int, impedance: str) -> None:
        self.write(f":CHANnel{channel}:IMPedance {impedance}")

    def get_channel_impedance(self, channel: int) -> str:
        return self.query(f":CHANnel{channel}:IMPedance?")

    def set_channel_probe(self, channel: int, attenuation: float) -> None:
        self.write(f":CHANnel{channel}:PROBe VALue,{attenuation:.6E}")

    def get_channel_probe(self, channel: int) -> float:
        response = self.query(f":CHANnel{channel}:PROBe?")
        return float(response.split(",")[-1])

    # ------------------------------------------------------------------
    # 触发设置
    # ------------------------------------------------------------------

    def set_trigger_mode(self, mode: str) -> None:
        self.write(f":TRIGger:MODE {mode}")

    def get_trigger_mode(self) -> str:
        return self.query(":TRIGger:MODE?")

    def set_trigger_type(self, trig_type: str) -> None:
        self.write(f":TRIGger:TYPE {trig_type}")

    def get_trigger_type(self) -> str:
        return self.query(":TRIGger:TYPE?")

    def set_trigger_source(self, source: str) -> None:
        self.write(f":TRIGger:EDGE:SOURce {source}")

    def get_trigger_source(self) -> str:
        return self.query(":TRIGger:EDGE:SOURce?")

    def set_trigger_slope(self, slope: str) -> None:
        self.write(f":TRIGger:EDGE:SLOPe {slope}")

    def get_trigger_slope(self) -> str:
        return self.query(":TRIGger:EDGE:SLOPe?")

    def set_trigger_level(self, level: float) -> None:
        self.write(f":TRIGger:EDGE:LEVel {level:.6E}")

    def get_trigger_level(self) -> float:
        return self.query_float(":TRIGger:EDGE:LEVel?")

    def trigger_run(self) -> None:
        self.write(":TRIGger:RUN")

    def trigger_stop(self) -> None:
        self.write(":TRIGger:STOP")

    def trigger_status(self) -> str:
        return self.query(":TRIGger:STATus?")

    def wait_for_trigger(self, poll_interval: float = 0.05,
                         timeout: float = 5.0) -> bool:
        """等待触发完成，返回是否成功触发。"""
        start = time.time()
        while time.time() - start < timeout:
            status = self.trigger_status()
            if status == "Trig'd":
                return True
            if status in ("Stop",):
                return False
            time.sleep(poll_interval)
        logger.warning("触发等待超时 (%s s)", timeout)
        return False

    # ------------------------------------------------------------------
    # 波形读取
    # ------------------------------------------------------------------

    def set_waveform_source(self, source: str) -> None:
        self.write(f":WAVeform:SOURce {source}")

    def set_waveform_width(self, width: str) -> None:
        self.write(f":WAVeform:WIDTh {width}")

    def set_waveform_start(self, start: int) -> None:
        self.write(f":WAVeform:STARt {start}")

    def set_waveform_points(self, points: int) -> None:
        self.write(f":WAVeform:POINt {points}")

    def get_waveform_max_points(self) -> int:
        return self.query_int(":WAVeform:MAXPoint?")

    def get_waveform_preamble(self) -> bytes:
        return self.query_binary(":WAVeform:PREamble?")

    def get_waveform_data(self) -> bytes:
        return self.query_binary(":WAVeform:DATA?")
