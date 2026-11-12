"""Aigtek 1200B 的单向 ASCII 串口控制，不提供未经协议支持的状态回读。"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
import math
from threading import RLock
import time
from typing import Callable

import serial
from serial.tools import list_ports

from hardware_gate import require_hardware_access


logger = logging.getLogger(__name__)

# 回调在每次有风险的命令写入前执行；拒绝时直接抛出异常。
SafetyCheck = Callable[[str, float | bool], None]


@dataclass(frozen=True, slots=True)
class CommandReceipt:
    """完整写入的命令及原始返回；没有成功应答，执行结果始终未确认。"""

    command: str
    response: bytes
    execution_confirmed: bool = field(default=False, init=False)


class AmplifierCommandError(RuntimeError):
    """设备明确报错或返回超过收集上限，保留本次命令和原始字节。"""

    def __init__(self, message: str, receipt: CommandReceipt) -> None:
        super().__init__(message)
        self.receipt = receipt


class Aigtek1200BInstrument:
    """1200B 基础控制库。

    构造和导入不接触硬件。connect 先经过硬件门控，再核对指定端口的
    CP210x VID/PID/适配器序列号；不探测型号，不发送初始化命令。

    safety_check 必须由调用方执行实际物理量的 validate_safety_limit 校验，
    并确认上游停输出、负载或测试隔离条件。它不是增益量程校验的替代品。
    没有回调时仅允许发送关闭输出命令；驱动不反向依赖 lab_workflows。

    所有设置只返回发送凭据，不缓存或推断真实输出状态。disconnect 和
    上下文管理器退出只释放串口，不负责关闭输出，也不自动保存或重试。
    短写或写异常会锁定本实例的后续发送，包含 OFF；重连不清除此锁定，
    因为关闭串口不能确认设备端已丢弃半帧，需人工核验并安全恢复。
    """

    USB_VID = 0x10C4
    USB_PID = 0xEA60
    BAUDRATE = 9600
    MAX_RESPONSE_BYTES = 4096
    READ_SLICE_S = 0.1

    def __init__(
        self,
        port: str,
        *,
        serial_number: str = "0001",
        response_timeout: float = 3.0,
        write_timeout: float = 1.0,
        safety_check: SafetyCheck | None = None,
    ) -> None:
        if not isinstance(port, str) or not port.strip():
            raise ValueError("1200B 串口不能为空")
        if not isinstance(serial_number, str) or not serial_number.strip():
            raise ValueError("CP210x 适配器序列号不能为空")
        self.port = port.strip()
        self.serial_number = serial_number.strip()
        self.response_timeout = self._positive_timeout("返回收集时限", response_timeout)
        self.write_timeout = self._positive_timeout("写入超时", write_timeout)
        if safety_check is not None and not callable(safety_check):
            raise ValueError("safety_check 必须是可调用的安全校验")
        self._safety_check = safety_check
        self._serial: serial.Serial | None = None
        self._write_faulted = False
        self._lock = RLock()

    @staticmethod
    def _finite(name: str, value: float) -> float:
        if isinstance(value, bool):
            raise ValueError(f"{name}必须是有限数值，不接受布尔值")
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name}必须是有限数值") from exc
        if not math.isfinite(number):
            raise ValueError(f"{name}必须是有限数值")
        return number

    @classmethod
    def _positive_timeout(cls, name: str, value: float) -> float:
        seconds = cls._finite(name, value)
        if seconds <= 0:
            raise ValueError(f"{name}必须大于零")
        return seconds

    @property
    def connected(self) -> bool:
        """仅表示本地串口已打开，不代表已识别仪器或已知输出状态。"""
        return self._serial is not None and self._serial.is_open

    def connect(self) -> None:
        """核对目标适配器后打开 9600/8N1 串口，不改变任何输出或设置。"""
        with self._lock:
            if self.connected:
                return
            require_hardware_access(f"Aigtek 1200B 串口 {self.port}")
            matches = [
                entry for entry in list_ports.comports()
                if entry.vid == self.USB_VID and entry.pid == self.USB_PID
                and entry.serial_number == self.serial_number
            ]
            if len(matches) != 1 or matches[0].device.casefold() != self.port.casefold():
                raise RuntimeError(
                    f"目标 CP210x 不唯一、不存在或端口已变化：要求 {self.port}，"
                    f"适配器序列号 {self.serial_number}；停止且不自动换端口"
                )
            connection = serial.Serial(
                port=None,
                baudrate=self.BAUDRATE,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=min(self.READ_SLICE_S, self.response_timeout),
                write_timeout=self.write_timeout,
                xonxoff=False,
                rtscts=False,
                dsrdtr=False,
            )
            try:
                # 在 open 前取消握手线置位，不主动驱动 DTR/RTS。
                connection.dtr = False
                connection.rts = False
                connection.port = self.port
                connection.open()
            except BaseException:
                try:
                    connection.close()
                except Exception:
                    logger.warning("1200B 打开失败后的串口释放失败", exc_info=True)
                raise
            self._serial = connection

    def disconnect(self) -> None:
        """仅关闭通信；不发送 AOF，不改变输出，不保存设置。"""
        with self._lock:
            connection = self._serial
            if connection is not None:
                connection.close()
                self._serial = None

    def __enter__(self) -> Aigtek1200BInstrument:
        self.connect()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if exc_type is None:
            self.disconnect()
        else:
            try:
                self.disconnect()
            except Exception:
                logger.error("1200B 异常退出时串口释放失败；保留原始异常", exc_info=True)

    def _send(
        self, opcode: str, parameter: str, *, operation: str, value: float | bool,
    ) -> CommandReceipt:
        with self._lock:
            connection = self._serial
            if connection is None or not connection.is_open:
                raise RuntimeError("1200B 尚未连接，请先调用 connect()")
            if self._write_faulted:
                raise RuntimeError(
                    "1200B 之前写入失败，设备端帧同步未知，拒绝后续命令（包括 OFF）。"
                    "断开/重连不能确认恢复；请人工核验输出并按厂家方法安全恢复，勿补发终止符"
                )
            if opcode != "AOF":
                if self._safety_check is None:
                    raise RuntimeError(
                        "未提供输出安全校验 safety_check；仅允许关闭输出。"
                        "先确认线圈限值或隔离测试条件，不使用协议量程作为负载安全范围"
                    )
                self._safety_check(operation, value)
            command = f"ATA,SET,{opcode},{parameter};"
            payload = command.encode("ascii")
            try:
                sent = connection.write(payload)
            except BaseException:
                # 设备可能已收到任意前缀，继续发 OFF 也会拼接成坏帧。
                self._write_faulted = True
                logger.error("1200B 写入失败，帧同步未知；停止发送，请人工核验输出")
                raise
            if sent != len(payload):
                self._write_faulted = True
                raise OSError(
                    f"1200B 命令未完整写入：{sent}/{len(payload)} 字节；"
                    "设备端帧同步与输出状态未知，停止发送且不自动重试，请人工核验"
                )
            response = bytearray()
            deadline = time.monotonic() + self.response_timeout
            while len(response) < self.MAX_RESPONSE_BYTES:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                connection.timeout = min(self.READ_SLICE_S, remaining)
                size = min(connection.in_waiting or 1, self.MAX_RESPONSE_BYTES - len(response))
                response.extend(connection.read(size))
                receipt = CommandReceipt(command, bytes(response))
                if b"OVER RANGE ERR!" in response:
                    raise AmplifierCommandError("1200B 返回 OVER RANGE ERR!；停止且不自动重试", receipt)
            receipt = CommandReceipt(command, bytes(response))
            if len(response) >= self.MAX_RESPONSE_BYTES:
                raise AmplifierCommandError("1200B 返回达到收集上限；停止且不自动重试", receipt)
            return receipt

    def set_output(self, enabled: bool) -> CommandReceipt:
        """发送开关命令；False 无需风险放行，写入流故障时仍拒绝，返回值不证明状态。"""
        if not isinstance(enabled, bool):
            raise ValueError("输出开关必须为 bool")
        return self._send("AON" if enabled else "AOF", "0000", operation="output", value=enabled)

    def set_gain_db(self, gain_db: float) -> CommandReceipt:
        """设置 0～20 dB、0.5 dB 步进的增益；0 dB 不是零输出。"""
        gain = self._finite("增益", gain_db)
        if not 0 <= gain <= 20 or not (gain * 2).is_integer():
            raise ValueError("1200B 增益必须为 0～20 dB，步进 0.5 dB；不自动取整")
        return self._send("AMP", f"{int(gain * 10):04d}", operation="gain_db", value=gain)

    def set_input_impedance(self, ohms: float) -> CommandReceipt:
        """设置 50 Ω 或 1 MΩ；放行前必须关闭上游输出或断开输入线。"""
        value = self._finite("输入阻抗", ohms)
        if value not in (50, 1_000_000):
            raise ValueError("1200B 输入阻抗只支持 50 或 1000000 Ω")
        parameter = "0050" if value == 50 else "1M00"
        return self._send("RIN", parameter, operation="input_impedance_ohms", value=value)

    def set_output_impedance(self, ohms: float) -> CommandReceipt:
        """设置 1 Ω 或 50 Ω 档位；该数值不是外接线圈电阻。"""
        value = self._finite("输出阻抗", ohms)
        if value not in (1, 50):
            raise ValueError("1200B 输出阻抗只支持 1 或 50 Ω")
        return self._send("ROT", f"{int(value):04d}", operation="output_impedance_ohms", value=value)

    def save_settings(self) -> CommandReceipt:
        """显式发送保存命令；保存范围与开机输出状态仍需现场核实。"""
        return self._send("SAV", "0000", operation="save_settings", value=True)
