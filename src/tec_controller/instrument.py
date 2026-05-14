import time
import logging
from typing import Optional, Tuple

import serial

logger = logging.getLogger(__name__)

# TEC103 默认串口参数
DEFAULT_BAUDRATE = 38400
DEFAULT_BYTESIZE = serial.EIGHTBITS
DEFAULT_PARITY = serial.PARITY_NONE
DEFAULT_STOPBITS = serial.STOPBITS_ONE
DEFAULT_TIMEOUT = 0.5  # 读取超时 (s)

# 温度值缩放系数：寄存器值 = 实际温度 × 100000
TEMP_SCALE = 100000


class TECInstrument:
    """光测未来 TEC103 温控器串口控制封装.

    通过 RS485 或 TTL 串口与 TEC103 通信，使用 ASCII 指令格式。
    支持单通道/双通道机型，该类以通道一的控制为主，通道二可通过指定 channel=2 访问。

    参数
    ----------
    port : str
        串口号，例如 "COM3"
    baudrate : int
        波特率，默认 38400
    timeout : float
        读取超时时间 (秒)
    """

    def __init__(
        self,
        port: str,
        baudrate: int = DEFAULT_BAUDRATE,
        timeout: float = DEFAULT_TIMEOUT,
    ):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self._serial: Optional[serial.Serial] = None

    # ------------------------------------------------------------------
    # 连接管理
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """打开串口连接."""
        self._serial = serial.Serial(
            port=self.port,
            baudrate=self.baudrate,
            bytesize=DEFAULT_BYTESIZE,
            parity=DEFAULT_PARITY,
            stopbits=DEFAULT_STOPBITS,
            timeout=self.timeout,
        )
        logger.info("已连接 TEC103 (%s, %d baud)", self.port, self.baudrate)

    def disconnect(self) -> None:
        """关闭串口连接."""
        if self._serial and self._serial.is_open:
            try:
                self._serial.close()
            except Exception:
                pass
            self._serial = None
            logger.info("已断开 TEC103 连接")

    @property
    def connected(self) -> bool:
        return self._serial is not None and self._serial.is_open

    def __enter__(self) -> "TECInstrument":
        self.connect()
        return self

    def __exit__(self, *args) -> None:
        self.disconnect()

    # ------------------------------------------------------------------
    # 底层 I/O
    # ------------------------------------------------------------------

    def _ensure_connected(self) -> None:
        if not self.connected:
            raise RuntimeError("未连接到 TEC103，请先调用 connect()")

    def _channel_prefix(self, channel: int) -> str:
        """返回通道前缀，例如 "TC1" 或 "TC2"."""
        return f"TC{channel}"

    def write(self, command: str) -> None:
        """发送 ASCII 指令（自动附加换行符）. """
        self._ensure_connected()
        payload = (command + "\n").encode("ascii")
        logger.debug(">> %s", command)
        self._serial.write(payload)

    def read(self, size: int = 64) -> str:
        """读取串口返回数据并解码为 ASCII 字符串."""
        self._ensure_connected()
        raw = self._serial.read(size)
        if not raw:
            raise TimeoutError("读取 TEC103 响应超时")
        return raw.decode("ascii", errors="replace")

    def query(self, command: str, size: int = 64) -> str:
        """发送指令并读取返回.

        两次读取之间自动等待 5ms，避免数据连发。
        """
        self.write(command)
        time.sleep(0.005)
        return self.read(size)

    def _parse_ok_response(self, response: str, expected_prefix: str) -> str:
        """验证 OK 响应并返回等号后的数值字符串.

        在响应中搜索 "OK<prefix>="，提取其后到 "@" 之间的内容。
        不严格要求结尾格式，兼容截断或缺少 \r\n 的情况。
        """
        resp = response.strip()
        marker = f"OK{expected_prefix}="
        if marker not in resp:
            raise ValueError(
                f"响应中未找到 '{marker}', 实际 '{resp[:64]}'"
            )
        value_start = resp.index(marker) + len(marker)
        value_end = resp.index("@", value_start) if "@" in resp[value_start:] else len(resp)
        return resp[value_start:value_end]

    def _parse_general_response(self, response: str, keyword: str) -> int:
        """解析通用参数指令的响应，支持多种实际响应格式.

        实际设备不同通用指令的响应格式不一致：
          TEC=?@       -> OKMODELNUMBER=103@
          FPV=?@       -> OK:FPV=429@
        """
        resp = response.strip()
        for fmt in (f"OKMODELNUMBER=", f"OK:{keyword}=", f"OK{keyword}="):
            if fmt in resp:
                value_start = resp.index(fmt) + len(fmt)
                value_end = resp.index("@", value_start)
                return int(resp[value_start:value_end])
        raise ValueError(f"无法解析通用参数响应: '{resp[:64]}'")

    # ------------------------------------------------------------------
    # 目标温度控制
    # ------------------------------------------------------------------

    def set_target_temperature(self, temp_celsius: float, channel: int = 1) -> None:
        """设置目标温度.

        参数
        ----------
        temp_celsius : float
            目标温度值 (℃)，范围 -400.00000 ~ 1000.00000
        channel : int
            通道号 (1 或 2)
        """
        reg_value = int(round(temp_celsius * TEMP_SCALE))
        ch = self._channel_prefix(channel)
        cmd = f"{ch}:TG={reg_value}@"
        resp = self.query(cmd)
        self._parse_ok_response(resp, f"{ch}:TG")
        logger.info("通道 %s 目标温度已设为 %.5f ℃", channel, temp_celsius)

    def get_target_temperature(self, channel: int = 1) -> float:
        """查询目标温度."""
        ch = self._channel_prefix(channel)
        cmd = f"{ch}:TG=?@"
        resp = self.query(cmd)
        value_str = self._parse_ok_response(resp, f"{ch}:TG")
        return int(value_str) / TEMP_SCALE

    # ------------------------------------------------------------------
    # 实际温度查询
    # ------------------------------------------------------------------

    def get_temperature(self, channel: int = 1) -> float:
        """查询当前实际温度."""
        ch = self._channel_prefix(channel)
        cmd = f"{ch}:TCADJTEMP=?@"
        resp = self.query(cmd)
        value_str = self._parse_ok_response(resp, f"{ch}:TCADJTEMP")
        raw = int(value_str)
        if raw == 999999999:
            logger.warning("通道 %s 未接温度传感器", channel)
            return float("nan")
        return raw / TEMP_SCALE

    # ------------------------------------------------------------------
    # 输出使能控制
    # ------------------------------------------------------------------

    def set_enable(self, enable: bool, channel: int = 1) -> None:
        """设置输出使能状态.

        参数
        ----------
        enable : bool
            True 为使能输出, False 为关闭输出
        channel : int
            通道号 (1 或 2)
        """
        ch = self._channel_prefix(channel)
        cmd = f"{ch}:ENABLE={1 if enable else 0}@"
        resp = self.query(cmd)
        self._parse_ok_response(resp, f"{ch}:ENABLE")
        state_str = "使能" if enable else "关闭"
        logger.info("通道 %s 输出已%s", channel, state_str)

    def get_enable(self, channel: int = 1) -> bool:
        """查询输出使能状态."""
        ch = self._channel_prefix(channel)
        cmd = f"{ch}:ENABLE=?@"
        resp = self.query(cmd)
        value_str = self._parse_ok_response(resp, f"{ch}:ENABLE")
        return int(value_str) == 1

    # ------------------------------------------------------------------
    # 传感器电阻值（只读）
    # ------------------------------------------------------------------

    def get_resistance(self, channel: int = 1) -> float:
        """查询传感器当前电阻值.

        返回电阻值，单位 kΩ。例如 10.000000 kΩ。
        """
        ch = self._channel_prefix(channel)
        cmd = f"{ch}:RESISTOR=?@"
        resp = self.query(cmd, size=128)
        value_str = self._parse_ok_response(resp, f"{ch}:RESISTOR")
        return int(value_str) / 1e9  # uint64, 单位 1e-9 kΩ

    # ------------------------------------------------------------------
    # 输出模式
    # ------------------------------------------------------------------

    def set_output_mode(self, mode: int, channel: int = 1) -> None:
        """设置输出模式.

        参数
        ----------
        mode : int
            0: 双向模式, 1: 制冷模式, 2: 加热模式, 3: 通信设置电压百分比
        channel : int
            通道号 (1 或 2)
        """
        ch = self._channel_prefix(channel)
        cmd = f"{ch}:MODE={mode}@"
        resp = self.query(cmd)
        self._parse_ok_response(resp, f"{ch}:MODE")
        logger.info("通道 %s 输出模式已设为 %d", channel, mode)

    def get_output_mode(self, channel: int = 1) -> int:
        """查询输出模式."""
        ch = self._channel_prefix(channel)
        cmd = f"{ch}:MODE=?@"
        resp = self.query(cmd)
        value_str = self._parse_ok_response(resp, f"{ch}:MODE")
        return int(value_str)

    # ------------------------------------------------------------------
    # 一次性查询关键数据
    # ------------------------------------------------------------------

    def query_key_data(self, mode: int = 1) -> str:
        """一次性查询关键数据.

        参数
        ----------
        mode : int
            1: 返回温度、电阻、PWM输出百分比
            2: 返回温度、电阻、实际输出电压

        返回
        -------
        str
            原始响应字符串
        """
        cmd = f"DATADEMAND={mode}@"
        resp = self.query(cmd, size=256)
        logger.debug("DATADEMAND -> %s", resp.strip())
        return resp.strip()

    def get_all_temperatures(self) -> dict:
        """解析 DATADEMAND=1 返回，提取各通道温度.

        返回格式: {"tc1": temp, "tc2": temp, "internal": temp}
        """
        raw = self.query_key_data(1)
        result = {}
        # 简易解析：提取 TC1:TCADJTEMP=xxx 和 TC2:TCADJTEMP=xxx
        for ch in ("TC1", "TC2"):
            marker = f"{ch}:TCADJTEMP="
            if marker in raw:
                start = raw.index(marker) + len(marker)
                end = raw.index("@", start)
                val = int(raw[start:end])
                result[ch.lower()] = val / TEMP_SCALE if val != 999999999 else float("nan")
        marker = "SINTERIORTEMP="
        if marker in raw:
            start = raw.index(marker) + len(marker)
            end = raw.index("@", start)
            result["internal"] = int(raw[start:end])
        return result

    # ------------------------------------------------------------------
    # PID 控制
    # ------------------------------------------------------------------

    def set_pid(self, kp: int, ki: int, kd: int, channel: int = 1) -> None:
        """设置 PID 参数.

        参数
        ----------
        kp : int
            比例系数 (0~9000000)
        ki : int
            积分系数 (0~9000000)
        kd : int
            微分系数 (0~9000000)
        channel : int
            通道号 (1 或 2)
        """
        self.set_kp(kp, channel)
        self.set_ki(ki, channel)
        self.set_kd(kd, channel)

    def set_kp(self, value: int, channel: int = 1) -> None:
        ch = self._channel_prefix(channel)
        cmd = f"{ch}:KP={value}@"
        self._parse_ok_response(self.query(cmd), f"{ch}:KP")

    def set_ki(self, value: int, channel: int = 1) -> None:
        ch = self._channel_prefix(channel)
        cmd = f"{ch}:KI={value}@"
        self._parse_ok_response(self.query(cmd), f"{ch}:KI")

    def set_kd(self, value: int, channel: int = 1) -> None:
        ch = self._channel_prefix(channel)
        cmd = f"{ch}:KD={value}@"
        self._parse_ok_response(self.query(cmd), f"{ch}:KD")

    def get_kp(self, channel: int = 1) -> int:
        ch = self._channel_prefix(channel)
        cmd = f"{ch}:KP=?@"
        return int(self._parse_ok_response(self.query(cmd), f"{ch}:KP"))

    def get_ki(self, channel: int = 1) -> int:
        ch = self._channel_prefix(channel)
        cmd = f"{ch}:KI=?@"
        return int(self._parse_ok_response(self.query(cmd), f"{ch}:KI"))

    def get_kd(self, channel: int = 1) -> int:
        ch = self._channel_prefix(channel)
        cmd = f"{ch}:KD=?@"
        return int(self._parse_ok_response(self.query(cmd), f"{ch}:KD"))

    # ------------------------------------------------------------------
    # 温度变化斜率
    # ------------------------------------------------------------------

    def set_slope(self, slope_celsius_per_sec: float, channel: int = 1) -> None:
        """设置温度变化斜率.

        参数
        ----------
        slope_celsius_per_sec : float
            斜率 (℃/s)，范围 0~10，0 代表不限
        channel : int
            通道号 (1 或 2)
        """
        reg = int(round(slope_celsius_per_sec * 1000))
        ch = self._channel_prefix(channel)
        cmd = f"{ch}:SPEED={reg}@"
        self._parse_ok_response(self.query(cmd), f"{ch}:SPEED")

    def get_slope(self, channel: int = 1) -> float:
        ch = self._channel_prefix(channel)
        cmd = f"{ch}:SPEED=?@"
        return int(self._parse_ok_response(self.query(cmd), f"{ch}:SPEED")) / 1000

    # ------------------------------------------------------------------
    # 最大输出占空比
    # ------------------------------------------------------------------

    def set_max_duty(self, percent: int, channel: int = 1) -> None:
        """设置最大输出占空比."""
        ch = self._channel_prefix(channel)
        cmd = f"{ch}:LIMITED={percent}@"
        self._parse_ok_response(self.query(cmd), f"{ch}:LIMITED")

    def get_max_duty(self, channel: int = 1) -> int:
        ch = self._channel_prefix(channel)
        cmd = f"{ch}:LIMITED=?@"
        return int(self._parse_ok_response(self.query(cmd), f"{ch}:LIMITED"))

    # ------------------------------------------------------------------
    # 设备信息
    # ------------------------------------------------------------------

    def get_model(self) -> int:
        """查询温控器型号代码.

        实际设备响应前缀为 MODELNUMBER 而非 TEC。
        """
        cmd = "TEC=?@"
        return self._parse_general_response(self.query(cmd), "TEC")

    def get_firmware_version(self) -> str:
        """查询固件版本号。例如 429 -> 4.2.9"""
        cmd = "FPV=?@"
        resp = self.query(cmd).strip()
        # 实际响应: "OK:FPV=429@" (带冒号)
        val = self._parse_general_response(resp, "FPV")
        major = val // 100
        minor = (val % 100) // 10
        patch = val % 10
        return f"{major}.{minor}.{patch}"

    def get_error_code(self) -> int:
        """查询错误代码."""
        cmd = "ERRORCODE=?@"
        resp = self.query(cmd).strip()
        return self._parse_general_response(resp, "ERRORCODE")
