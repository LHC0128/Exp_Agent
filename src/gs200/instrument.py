import logging
from typing import Optional

import pyvisa
from pyvisa.resources import MessageBasedResource

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 10000  # ms


class GS200Instrument:
    """Yokogawa GS200 直流电压/电流源 PyVISA 封装.

    GS200 支持 USB-TMC、Ethernet (VXI-11)、GP-IB 以及 RS-232 接口。
    本类通过 PyVISA 与设备通信，使用标准 SCPI 命令。

    参数
    ----------
    resource_string : str
        VISA 资源字符串，例如:
        - "USB0::0x0B21::0x0039::XXXXXXXX::INSTR" (USB)
        - "TCPIP0::192.168.1.10::inst0::INSTR" (Ethernet)
        - "GPIB0::1::INSTR" (GP-IB)
        - "ASRL3::INSTR" (RS-232 串口 COM3)
    timeout : int
        超时时间 (ms)
    """

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

    @property
    def connected(self) -> bool:
        return self._inst is not None

    def __enter__(self) -> "GS200Instrument":
        self.connect()
        return self

    def __exit__(self, *args) -> None:
        self.disconnect()

    # ------------------------------------------------------------------
    # 底层 I/O
    # ------------------------------------------------------------------

    def _ensure_connected(self) -> None:
        if self._inst is None:
            raise RuntimeError("未连接到 GS200，请先调用 connect()")

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

    # ------------------------------------------------------------------
    # 识别
    # ------------------------------------------------------------------

    def idn(self) -> str:
        return self.query("*IDN?")

    def reset(self) -> None:
        """仪器复位."""
        self.write("*RST")

    def clear_status(self) -> None:
        """清除状态."""
        self.write("*CLS")

    # ------------------------------------------------------------------
    # 源功能设置
    # ------------------------------------------------------------------

    def set_source_function(self, function: str) -> None:
        """设置源功能.

        参数
        ----------
        function : str
            "CURRent" 或 "VOLTage"
        """
        self.write(f":SOURce:FUNCtion {function}")

    def get_source_function(self) -> str:
        """查询源功能."""
        return self.query(":SOURce:FUNCtion?")

    # ------------------------------------------------------------------
    # 电流设置
    # ------------------------------------------------------------------

    def set_current(self, current: float) -> None:
        """设置输出电流值.

        自动将源功能切换为电流模式。使用 SOURce:LEVel:AUTO
        自动选择最佳量程。

        参数
        ----------
        current : float
            输出电流值 (A)，例如 0.1 表示 100 mA
        """
        self.set_source_function("CURRent")
        self.write(f":SOURce:LEVel:AUTO {current}")

    def get_current(self) -> float:
        """查询当前输出电流设定值 (A)."""
        return float(self.query(":SOURce:LEVel?"))

    def set_current_range(self, current: float) -> None:
        """设置电流量程.

        参数
        ----------
        current : float
            电流值，将选择包含该值的最小量程
        """
        self.write(f":SOURce:RANGe {current}")

    def get_current_range(self) -> float:
        """查询当前量程."""
        return float(self.query(":SOURce:RANGe?"))

    # ------------------------------------------------------------------
    # 电压设置
    # ------------------------------------------------------------------

    def set_voltage(self, voltage: float) -> None:
        """设置输出电压值.

        自动将源功能切换为电压模式。

        参数
        ----------
        voltage : float
            输出电压值 (V)
        """
        self.set_source_function("VOLTage")
        self.write(f":SOURce:LEVel:AUTO {voltage}")

    def get_voltage(self) -> float:
        """查询当前输出电压设定值 (V)."""
        return float(self.query(":SOURce:LEVel?"))

    # ------------------------------------------------------------------
    # 输出控制
    # ------------------------------------------------------------------

    def set_output(self, state: bool) -> None:
        """设置输出状态 (ON/OFF).

        参数
        ----------
        state : bool
            True 打开输出, False 关闭输出
        """
        self.write(f":OUTPut:STATe {1 if state else 0}")

    def get_output(self) -> bool:
        """查询输出状态."""
        resp = self.query(":OUTPut:STATe?")
        return resp.strip() == "1"

    # ------------------------------------------------------------------
    # 保护设置
    # ------------------------------------------------------------------

    def set_voltage_limit(self, voltage: float) -> None:
        """设置限压值.

        参数
        ----------
        voltage : float
            限压值 (V)
        """
        self.write(f":SOURce:PROTection:VOLTage {voltage}")

    def get_voltage_limit(self) -> float:
        """查询限压值."""
        return float(self.query(":SOURce:PROTection:VOLTage?"))

    def set_current_limit(self, current: float) -> None:
        """设置限流值.

        参数
        ----------
        current : float
            限流值 (A)
        """
        self.write(f":SOURce:PROTection:CURRent {current}")

    def get_current_limit(self) -> float:
        """查询限流值."""
        return float(self.query(":SOURce:PROTection:CURRent?"))
