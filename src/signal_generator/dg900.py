"""RIGOL DG900 Pro 系列信号发生器 PyVISA 封装（仅 DC 模式）.

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
    """RIGOL DG900 Pro 系列 PyVISA 封装（仅 DC 和开关）."""

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

    def query(self, command: str) -> str:
        """发送 SCPI 查询并返回响应."""
        self._ensure_connected()
        logger.debug(">> %s", command)
        resp = self._inst.query(command).strip()
        logger.debug("<< %s", resp)
        return resp

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

    # ------------------------------------------------------------------
    # 输出开关 / 时钟
    # ------------------------------------------------------------------

    def set_output(self, state: bool,
                   channel: Optional[int] = None) -> None:
        """打开/关闭指定通道输出."""
        ch = self._ch(channel)
        self.write(f":OUTPut{ch}:STATe {'ON' if state else 'OFF'}")

    def set_ref_clock_source(self, source: str = "EXTernal") -> None:
        """设置参考时钟源 (INTernal / EXTernal)."""
        self.write(f":SYSTem:ROSCillator:SOURce {source}")
