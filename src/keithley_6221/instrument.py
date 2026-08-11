"""Keithley 6221 精密电流源的 PyVISA 驱动。"""

from __future__ import annotations

import logging
import math
from collections.abc import Iterable
from typing import Optional

import pyvisa
from pyvisa.resources import MessageBasedResource

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 10000


class Keithley6221Instrument:
    """通过 VISA 控制 Keithley 6221 的直流源和内部波形发生器。"""

    MIN_CURRENT_A = -0.105
    MAX_CURRENT_A = 0.105
    MIN_COMPLIANCE_V = 0.1
    MAX_COMPLIANCE_V = 105.0
    MIN_CURRENT_RANGE_A = 2e-12
    MIN_WAVEFORM_AMPLITUDE_A = 2e-12
    MAX_WAVEFORM_AMPLITUDE_A = 0.105
    MIN_WAVEFORM_FREQUENCY_HZ = 1e-3
    MAX_WAVEFORM_FREQUENCY_HZ = 1e5
    MIN_WAVEFORM_TIME_S = 100e-9
    MAX_WAVEFORM_TIME_S = 999999.999
    MIN_WAVEFORM_CYCLES = 0.001
    MAX_WAVEFORM_CYCLES = 99999999900.0
    MIN_ARB_POINTS = 2
    MAX_ARB_POINTS = 65535
    ARB_BATCH_SIZE = 100

    _WAVEFORM_FUNCTIONS = {"SIN", "SQU", "RAMP", "ARB"}
    _WAVEFORM_RANGING = {"BEST", "FIXED"}
    _OUTPUT_RESPONSES = {"FAST", "SLOW"}

    def __init__(self, resource_string: str, timeout: int = DEFAULT_TIMEOUT):
        self.resource_string = resource_string
        self.timeout = timeout
        self._rm: Optional[pyvisa.ResourceManager] = None
        self._inst: Optional[MessageBasedResource] = None

    def connect(self) -> None:
        self._rm = pyvisa.ResourceManager()
        self._inst = self._rm.open_resource(self.resource_string)
        self._inst.timeout = self.timeout
        logger.info("已连接: %s", self.idn())

    def disconnect(self) -> None:
        if self._inst is not None:
            try:
                self._inst.close()
            except Exception:
                pass
            self._inst = None
        if self._rm is not None:
            try:
                self._rm.close()
            except Exception:
                pass
            self._rm = None

    @property
    def connected(self) -> bool:
        return self._inst is not None

    def __enter__(self) -> "Keithley6221Instrument":
        self.connect()
        return self

    def __exit__(self, *args) -> None:
        self.disconnect()

    def _ensure_connected(self) -> None:
        if self._inst is None:
            raise RuntimeError("未连接到 Keithley 6221，请先调用 connect()")

    def write(self, command: str) -> None:
        self._ensure_connected()
        logger.debug(">> %s", command)
        self._inst.write(command)

    def query(self, command: str) -> str:
        self._ensure_connected()
        logger.debug(">> %s", command)
        response = str(self._inst.query(command)).strip()
        logger.debug("<< %s", response)
        return response

    @staticmethod
    def _finite(name: str, value: float) -> float:
        converted = float(value)
        if not math.isfinite(converted):
            raise ValueError(f"{name} 必须是有限数值")
        return converted

    @classmethod
    def _bounded(cls, name: str, value: float, low: float, high: float) -> float:
        converted = cls._finite(name, value)
        if converted < low or converted > high:
            raise ValueError(f"{name} 必须在 {low:g} 到 {high:g} 之间")
        return converted

    @staticmethod
    def _format(value: float) -> str:
        return format(float(value), ".12g")

    @staticmethod
    def _parse_bool(value: str) -> bool:
        normalized = value.strip().strip('"').upper()
        if normalized in {"1", "ON"}:
            return True
        if normalized in {"0", "OFF"}:
            return False
        raise ValueError(f"无法解析布尔回读值: {value!r}")

    @staticmethod
    def _parse_float_or_inf(value: str) -> float | str:
        normalized = value.strip().strip('"').upper()
        if normalized in {"INF", "+INF", "INFINITY", "+INFINITY"}:
            return "INF"
        return float(normalized)

    def idn(self) -> str:
        return self.query("*IDN?")

    def reset(self) -> None:
        self.write("*RST")

    def clear_status(self) -> None:
        self.write("*CLS")

    def wait_for_operation_complete(self) -> None:
        response = self.query("*OPC?")
        if response != "1":
            raise RuntimeError(f"Keithley 6221 操作未完成: {response!r}")

    def get_error(self) -> tuple[int, str]:
        response = self.query("SYST:ERR?")
        code_text, separator, message = response.partition(",")
        if not separator:
            raise RuntimeError(f"无法解析 Keithley 6221 错误回读: {response!r}")
        return int(code_text), message.strip().strip('"')

    def raise_for_errors(self, *, maximum: int = 20) -> None:
        errors: list[str] = []
        for _ in range(maximum):
            code, message = self.get_error()
            if code == 0:
                break
            errors.append(f"{code}: {message}")
        else:
            errors.append("错误队列超过读取上限")
        if errors:
            raise RuntimeError("Keithley 6221 错误: " + "; ".join(errors))

    def set_current(self, current_a: float) -> None:
        current = self._bounded(
            "直流电流", current_a, self.MIN_CURRENT_A, self.MAX_CURRENT_A
        )
        self.write(f"CURR {self._format(current)}")

    def get_current(self) -> float:
        return float(self.query("CURR?"))

    def set_current_range(self, range_a: float) -> None:
        current_range = self._bounded(
            "电流量程", range_a, self.MIN_CURRENT_RANGE_A, self.MAX_CURRENT_A
        )
        self.write(f"CURR:RANG {self._format(current_range)}")

    def get_current_range(self) -> float:
        return float(self.query("CURR:RANG?"))

    def set_autorange(self, enabled: bool) -> None:
        self.write(f"CURR:RANG:AUTO {'ON' if enabled else 'OFF'}")

    def get_autorange(self) -> bool:
        return self._parse_bool(self.query("CURR:RANG:AUTO?"))

    def set_compliance(self, voltage_v: float) -> None:
        compliance = self._bounded(
            "Compliance", voltage_v, self.MIN_COMPLIANCE_V, self.MAX_COMPLIANCE_V
        )
        self.write(f"CURR:COMP {self._format(compliance)}")

    def get_compliance(self) -> float:
        return float(self.query("CURR:COMP?"))

    def set_analog_filter(self, enabled: bool) -> None:
        self.write(f"CURR:FILT {'ON' if enabled else 'OFF'}")

    def get_analog_filter(self) -> bool:
        return self._parse_bool(self.query("CURR:FILT?"))

    def set_output_response(self, response: str) -> None:
        normalized = str(response).strip().upper()
        if normalized not in self._OUTPUT_RESPONSES:
            raise ValueError("输出响应只能是 FAST 或 SLOW")
        self.write(f"OUTP:RESP {normalized}")

    def get_output_response(self) -> str:
        return self.query("OUTP:RESP?").strip().strip('"').upper()

    def set_output(self, enabled: bool) -> None:
        self.write(f"OUTP {'ON' if enabled else 'OFF'}")

    def get_output(self) -> bool:
        return self._parse_bool(self.query("OUTP?"))

    def clear_source(self) -> None:
        self.write("SOUR:CLE")

    def set_waveform_function(self, shape: str) -> None:
        normalized = str(shape).strip().upper()
        if normalized == "ARB0":
            normalized = "ARB"
        if normalized not in self._WAVEFORM_FUNCTIONS:
            raise ValueError("波形类型只能是 SIN、SQU、RAMP 或 ARB")
        device_shape = "ARB0" if normalized == "ARB" else normalized
        self.write(f"SOUR:WAVE:FUNC {device_shape}")

    def get_waveform_function(self) -> str:
        value = self.query("SOUR:WAVE:FUNC?").strip().strip('"').upper()
        return "ARB" if value == "ARB0" else value

    def set_waveform_frequency(self, frequency_hz: float) -> None:
        frequency = self._bounded(
            "波形频率",
            frequency_hz,
            self.MIN_WAVEFORM_FREQUENCY_HZ,
            self.MAX_WAVEFORM_FREQUENCY_HZ,
        )
        self.write(f"SOUR:WAVE:FREQ {self._format(frequency)}")

    def get_waveform_frequency(self) -> float:
        return float(self.query("SOUR:WAVE:FREQ?"))

    def set_waveform_amplitude(self, amplitude_a: float) -> None:
        amplitude = self._bounded(
            "波形峰值幅度",
            amplitude_a,
            self.MIN_WAVEFORM_AMPLITUDE_A,
            self.MAX_WAVEFORM_AMPLITUDE_A,
        )
        self.write(f"SOUR:WAVE:AMPL {self._format(amplitude)}")

    def get_waveform_amplitude(self) -> float:
        return float(self.query("SOUR:WAVE:AMPL?"))

    def set_waveform_offset(self, offset_a: float) -> None:
        offset = self._bounded(
            "波形偏置", offset_a, self.MIN_CURRENT_A, self.MAX_CURRENT_A
        )
        self.write(f"SOUR:WAVE:OFFS {self._format(offset)}")

    def get_waveform_offset(self) -> float:
        return float(self.query("SOUR:WAVE:OFFS?"))

    def set_waveform_duty_cycle(self, duty_cycle_percent: float) -> None:
        duty = self._bounded("占空比", duty_cycle_percent, 0.0, 100.0)
        self.write(f"SOUR:WAVE:DCYC {self._format(duty)}")

    def get_waveform_duty_cycle(self) -> float:
        return float(self.query("SOUR:WAVE:DCYC?"))

    def set_waveform_ranging(self, ranging: str) -> None:
        normalized = str(ranging).strip().upper()
        if normalized not in self._WAVEFORM_RANGING:
            raise ValueError("波形量程方式只能是 BEST 或 FIXED")
        self.write(f"SOUR:WAVE:RANG {normalized}")

    def get_waveform_ranging(self) -> str:
        return self.query("SOUR:WAVE:RANG?").strip().strip('"').upper()

    def set_waveform_duration(
        self, mode: str, value: float | None = None
    ) -> None:
        normalized = str(mode).strip().upper()
        if normalized == "TIME":
            if value is None:
                raise ValueError("时间时长模式必须提供 value")
            duration = self._bounded(
                "波形时长",
                value,
                self.MIN_WAVEFORM_TIME_S,
                self.MAX_WAVEFORM_TIME_S,
            )
            self.write("SOUR:WAVE:DUR:CYCL INF")
            self.write(f"SOUR:WAVE:DUR:TIME {self._format(duration)}")
            return
        if normalized == "CYCLES":
            if value is None:
                raise ValueError("周期数时长模式必须提供 value")
            cycles = self._bounded(
                "波形周期数",
                value,
                self.MIN_WAVEFORM_CYCLES,
                self.MAX_WAVEFORM_CYCLES,
            )
            self.write("SOUR:WAVE:DUR:TIME INF")
            self.write(f"SOUR:WAVE:DUR:CYCL {self._format(cycles)}")
            return
        if normalized == "INFINITE":
            if value is not None:
                raise ValueError("无限时长模式不能提供 value")
            self.write("SOUR:WAVE:DUR:TIME INF")
            self.write("SOUR:WAVE:DUR:CYCL INF")
            return
        raise ValueError("波形时长模式只能是 TIME、CYCLES 或 INFINITE")

    def get_waveform_duration_time(self) -> float | str:
        return self._parse_float_or_inf(self.query("SOUR:WAVE:DUR:TIME?"))

    def get_waveform_duration_cycles(self) -> float | str:
        return self._parse_float_or_inf(self.query("SOUR:WAVE:DUR:CYCL?"))

    def upload_arbitrary(self, points: Iterable[float]) -> int:
        values = [self._bounded("任意波点", value, -1.0, 1.0) for value in points]
        if not self.MIN_ARB_POINTS <= len(values) <= self.MAX_ARB_POINTS:
            raise ValueError(
                f"任意波点数必须在 {self.MIN_ARB_POINTS} 到 "
                f"{self.MAX_ARB_POINTS} 之间"
            )
        for start in range(0, len(values), self.ARB_BATCH_SIZE):
            batch = values[start:start + self.ARB_BATCH_SIZE]
            payload = ",".join(self._format(value) for value in batch)
            command = (
                "SOUR:WAVE:ARB:DATA"
                if start == 0
                else "SOUR:WAVE:ARB:APPEND"
            )
            self.write(f"{command} {payload}")
        return len(values)

    def get_arbitrary_point_count(self) -> int:
        return int(float(self.query("SOUR:WAVE:ARB:POIN?")))

    def arm_waveform(self) -> None:
        self.write("SOUR:WAVE:ARM")

    def start_waveform(self) -> None:
        self.write("SOUR:WAVE:INIT")

    def abort_waveform(self) -> None:
        self.write("SOUR:WAVE:ABOR")
