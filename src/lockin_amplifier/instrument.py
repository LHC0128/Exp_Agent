import logging
from typing import Optional

logger = logging.getLogger(__name__)

# zhinst 在首次导入时会输出大量日志，暂时压制
import warnings
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    try:
        import zhinst.core as _zi
    except ImportError:
        _zi = None


class HF2Instrument:
    """Zurich Instruments HF2 锁相放大器连接封装.

    通过 LabOne ziDAQServer 与 HF2 设备通信。
    使用节点树 (node tree) 进行配置和数据读取。

    参数
    ----------
    host : str
        LabOne 数据服务器地址，默认 127.0.0.1
    port : int
        HF2 数据服务器端口，默认 8005
    api_level : int
        API 级别，HF2 仅支持 1
    device_id : str
        设备 ID，例如 "dev18246"
    """

    def __init__(
        self,
        host: str = '127.0.0.1',
        port: int = 8005,
        api_level: int = 1,
        device_id: str = "dev18246",
        interface: str = "PCIe",
    ):
        self.host = host
        self.port = port
        self.api_level = api_level
        self.device_id = device_id.lower()
        self.interface = interface

        self._daq: Optional[_zi.ziDAQServer] = None

    # ------------------------------------------------------------------
    # 连接管理
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """连接到 LabOne 数据服务器并连接设备."""
        if _zi is None:
            raise RuntimeError(
                "zhinst 包未安装。请运行: pip install zhinst"
            )
        logger.info(
            "正在连接 LabOne 数据服务器 %s:%s (API Level %s)",
            self.host, self.port, self.api_level,
        )
        self._daq = _zi.ziDAQServer(self.host, self.port, self.api_level)
        # 连接设备（PCIe / USB / 1GbE）
        try:
            self._daq.connectDevice(self.device_id, self.interface)
            logger.info("已连接设备 %s (接口: %s)", self.device_id, self.interface)
        except Exception as e:
            logger.warning(
                "设备 %s 连接失败 (%s)，数据服务器已连接但设备不可用",
                self.device_id, e,
            )

    def disconnect(self) -> None:
        """断开设备连接和数据服务器连接."""
        if self._daq is not None:
            try:
                self._daq.disconnectDevice(self.device_id)
            except Exception:
                pass
            try:
                self._daq = None
            except Exception:
                pass
            logger.info("已断开连接")

    @property
    def connected(self) -> bool:
        """是否已连接."""
        return self._daq is not None

    @property
    def daq(self):
        """底层 ziDAQServer 实例."""
        return self._daq

    def __enter__(self) -> "HF2Instrument":
        self.connect()
        return self

    def __exit__(self, *args) -> None:
        self.disconnect()

    # ------------------------------------------------------------------
    # 节点路径辅助
    # ------------------------------------------------------------------

    def node_path(self, *parts: str) -> str:
        """构建完整的节点路径."""
        return "/" + "/".join(str(p).strip("/") for p in parts)

    def demod_path(self, idx: int = 0) -> str:
        """解调器节点基路径."""
        return f"/{self.device_id}/demods/{idx}"

    def osc_path(self, idx: int = 0) -> str:
        """振荡器节点基路径."""
        return f"/{self.device_id}/oscs/{idx}"

    def sig_in_path(self, idx: int = 0) -> str:
        """输入通道节点基路径."""
        return f"/{self.device_id}/sigins/{idx}"

    def sig_out_path(self, idx: int = 0) -> str:
        """输出通道节点基路径."""
        return f"/{self.device_id}/sigouts/{idx}"

    def aux_out_path(self, idx: int = 0) -> str:
        """辅助输出节点基路径."""
        return f"/{self.device_id}/auxouts/{idx}"

    # ------------------------------------------------------------------
    # 节点读写
    # ------------------------------------------------------------------

    def _require_connected(self):
        if self._daq is None:
            raise RuntimeError("未连接到数据服务器，请先调用 connect()")

    def set_int(self, path: str, value: int) -> None:
        """设置整数节点."""
        self._require_connected()
        logger.debug("setInt %s = %s", path, value)
        self._daq.setInt(path, value)

    def set_double(self, path: str, value: float) -> None:
        """设置双精度节点."""
        self._require_connected()
        logger.debug("setDouble %s = %s", path, value)
        self._daq.setDouble(path, value)

    def set_string(self, path: str, value: str) -> None:
        """设置字符串节点."""
        self._require_connected()
        logger.debug("setString %s = %s", path, value)
        self._daq.setString(path, value)

    def get_int(self, path: str) -> int:
        """读取整数节点."""
        self._require_connected()
        value = self._daq.getInt(path)
        logger.debug("getInt %s -> %s", path, value)
        return value

    def get_double(self, path: str) -> float:
        """读取双精度节点."""
        self._require_connected()
        value = self._daq.getDouble(path)
        logger.debug("getDouble %s -> %s", path, value)
        return value

    def get_string(self, path: str) -> str:
        """读取字符串节点."""
        self._require_connected()
        value = self._daq.getString(path)
        logger.debug("getString %s -> %s", path, value)
        return value

    def sync(self) -> None:
        """同步：阻塞直到所有 set 命令生效."""
        self._require_connected()
        self._daq.sync()

    # ------------------------------------------------------------------
    # 流式数据
    # ------------------------------------------------------------------

    def subscribe(self, path: str) -> None:
        """订阅节点以获取流式数据."""
        self._require_connected()
        logger.debug("subscribe %s", path)
        self._daq.subscribe(path)

    def unsubscribe(self, path: str) -> None:
        """取消订阅."""
        self._require_connected()
        logger.debug("unsubscribe %s", path)
        self._daq.unsubscribe(path)

    def poll(self, recording_time: float, timeout: int = 500, flat: bool = True) -> dict:
        """从订阅节点轮询累积数据.

        参数
        ----------
        recording_time : float
            记录时间 (s)
        timeout : int
            超时时间 (ms)
        flat : bool
            是否展平返回字典

        返回
        -------
        dict
            包含各订阅节点数据的字典
        """
        self._require_connected()
        data = self._daq.poll(recording_time, timeout, 0, flat)
        return data

    def get_sample(self, path: str) -> dict:
        """获取单个解调样本."""
        self._require_connected()
        return self._daq.getSample(path)

    # ------------------------------------------------------------------
    # 设备信息
    # ------------------------------------------------------------------

    @property
    def clockbase(self) -> int:
        """ADC 时钟基准 (Hz)，用于时间戳转换到秒."""
        return self.get_int(f"/{self.device_id}/clockbase")

    @property
    def idn(self) -> str:
        """设备标识."""
        return self.get_string(f"/{self.device_id}/features/devtype")
