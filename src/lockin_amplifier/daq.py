"""DAQ 模块数据采集模块.

封装 LabOne Data Acquisition 模块，支持连续和触发模式采集。
"""

import time
import logging
from typing import List, Optional
import numpy as np

from .instrument import HF2Instrument
from .config import DAQConfig, DAQResult

logger = logging.getLogger(__name__)


class DAQCollector:
    """DAQ 模块封装器.

    管理 DAQ 模块的创建、配置、订阅、执行和读取。

    参数
    ----------
    instr : HF2Instrument
        已连接的仪器实例
    """

    def __init__(self, instr: HF2Instrument):
        self._instr = instr
        self._mod = None
        self._subscribed_paths: list[str] = []

    def configure(self, config: DAQConfig) -> None:
        """配置 DAQ 模块.

        参数
        ----------
        config : DAQConfig
            DAQ 采集配置
        """
        self.close()

        self._mod = self._instr.daq.dataAcquisitionModule()

        # 基础设置
        self._mod.set("dataAcquisitionModule/device", config.device)
        self._mod.set("dataAcquisitionModule/type", config.trigger_type)
        self._mod.set("dataAcquisitionModule/duration", config.duration)

        # 触发设置（trigger_type=1 时有效）
        if config.trigger_type == 1:
            # triggernode: 使用辅助输入通道作为触发源
            trigger_node = f"/{config.device}/auxins/{config.trigger_channel}/sample.AuxIn{config.trigger_channel}"
            self._mod.set("dataAcquisitionModule/triggernode", trigger_node)
            self._mod.set("dataAcquisitionModule/level", config.trigger_level)
            self._mod.set("dataAcquisitionModule/hysteresis", 0.5)
            edge_str = "falling" if config.trigger_slope == 1 else "rising"
            self._mod.set("dataAcquisitionModule/edge", edge_str)
            if config.trigger_delay > 0:
                self._mod.set("dataAcquisitionModule/trigger/delay", config.trigger_delay)
            logger.info(
                "DAQ 触发配置: triggernode=%s, level=%.3f V, edge=%s, delay=%.3f s",
                trigger_node, config.trigger_level, edge_str, config.trigger_delay,
            )

        # 网格设置
        self._mod.set("dataAcquisitionModule/grid/mode", config.grid_mode)
        self._mod.set("dataAcquisitionModule/grid/cols", config.grid_cols)
        self._mod.set("dataAcquisitionModule/grid/rows", config.grid_rows)

        logger.info(
            "DAQ 模块已配置: type=%d, duration=%.3f s, grid=%dx%d",
            config.trigger_type, config.duration,
            config.grid_cols, config.grid_rows,
        )

    def subscribe(self, signal_paths: list[str], demod_idx: int = 0) -> None:
        """订阅解调器信号.

        参数
        ----------
        signal_paths : list[str]
            信号路径后缀列表，例如 ['sample.r', 'sample.x', 'sample.y']
        demod_idx : int
            解调器索引
        """
        if self._mod is None:
            raise RuntimeError("DAQ 模块未配置，请先调用 configure()")

        base = self._instr.demod_path(demod_idx)
        for sp in signal_paths:
            full_path = f"{base}/{sp}"
            self._mod.subscribe(full_path)
            self._subscribed_paths.append(full_path)
            logger.debug("DAQ 订阅: %s", full_path)

    def subscribe_raw(self, path: str) -> None:
        """订阅任意完整节点路径（非解调器信号）.

        参数
        ----------
        path : str
            完整节点路径，例如 /dev18246/auxins/0/sample.AuxIn0
        """
        if self._mod is None:
            raise RuntimeError("DAQ 模块未配置，请先调用 configure()")
        self._mod.subscribe(path)
        self._subscribed_paths.append(path)
        logger.debug("DAQ 订阅(原始节点): %s", path)

    def execute(self) -> None:
        """开始执行采集."""
        if self._mod is None:
            raise RuntimeError("DAQ 模块未配置，请先调用 configure()")
        self._mod.execute()
        logger.info("DAQ 模块开始采集")

    @property
    def progress(self) -> float:
        """采集进度 [0, 1]."""
        if self._mod is None:
            return 0.0
        return np.asarray(self._mod.progress()).item()

    @property
    def finished(self) -> bool:
        """采集是否完成."""
        if self._mod is None:
            return True
        return bool(self._mod.finished())

    def wait(self, target: float = 1.0, poll_interval: float = 0.1,
             timeout: float = 0.0) -> None:
        """等待采集达到指定进度.

        参数
        ----------
        target : float
            目标进度 (0~1)，默认 1.0
        poll_interval : float
            轮询间隔 (s)
        timeout : float
            超时时间 (s)，0 表示不限
        """
        if self._mod is None:
            return
        elapsed = 0.0
        while np.asarray(self._mod.progress()).item() < target:
            time.sleep(poll_interval)
            elapsed += poll_interval
            if timeout > 0 and elapsed >= timeout:
                prog = np.asarray(self._mod.progress()).item()
                logger.warning(
                    "DAQ 采集超时 (timeout=%.1f s, progress=%.1f%%)",
                    timeout, prog * 100,
                )
                raise TimeoutError(
                    f"DAQ 采集超时 ({timeout:.0f}s)，进度 {prog*100:.0f}%。"
                    "可能原因：触发信号未到达、触发电平不匹配、或 aux 输入未配置。"
                )
        prog_final = np.asarray(self._mod.progress()).item()
        logger.info("DAQ 采集进度: %.1f%%", prog_final * 100)

    def read(self, flat: bool = True) -> dict:
        """读取采集数据.

        参数
        ----------
        flat : bool
            是否展平字典

        返回
        -------
        dict
            包含各订阅信号数据的字典
        """
        if self._mod is None:
            raise RuntimeError("DAQ 模块未配置")
        data = self._mod.read(flat)
        return data

    def close(self) -> None:
        """清理 DAQ 模块."""
        if self._mod is not None:
            try:
                self._mod.clear()
            except Exception:
                pass
            self._mod = None
            self._subscribed_paths = []
            logger.debug("DAQ 模块已清理")


def acquire_data(
    instr: HF2Instrument,
    config: Optional[DAQConfig] = None,
    demod_idx: int = 0,
    actual_rate: Optional[float] = None,
    timeout: float = 30.0,
) -> List[DAQResult]:
    """一键采集：配置、执行并读取 DAQ 数据.

    返回结构化的 DAQResult 列表，格式与 sds_acquisition.AcquisitionResult 对齐。

    参数
    ----------
    instr : HF2Instrument
        已连接的仪器实例
    config : DAQConfig, optional
        DAQ 采集配置
    demod_idx : int
        解调器索引
    actual_rate : float, optional
        实际采样率 (Sa/s)，用于计算时间轴。不传则从 daq_cfg 读取。
    timeout : float
        采集超时时间 (s)

    返回
    -------
    List[DAQResult]
        每路信号一个 DAQResult，含 values 和 time 数组
    """
    if config is None:
        config = DAQConfig()

    collector = DAQCollector(instr)
    try:
        collector.configure(config)
        collector.subscribe(config.signal_paths, demod_idx)
        for extra_path in config.extra_paths:
            collector.subscribe_raw(extra_path)
        collector.execute()
        collector.wait(1.0, timeout=timeout)
        raw = collector.read()
        logger.info("数据采集完成，共 %d 个信号路径", len(raw))

        if actual_rate is None or actual_rate <= 0:
            actual_rate = instr.get_double(
                f"{instr.demod_path(demod_idx)}/rate"
            )

        results = []
        for path, raw_value in raw.items():
            # 过滤掉 DAQ 模块参数节点，保留信号数据
            if "sample." not in path and "sample" not in path:
                continue
            signal_name = path.split("/")[-1]

            # 提取数据数组: 兼容 [{value:...}, ...] 或 {value:...} 或 直接数组
            if isinstance(raw_value, list) and len(raw_value) > 0:
                if isinstance(raw_value[0], dict) and "value" in raw_value[0]:
                    data_arr = np.array(raw_value[0]["value"])
                else:
                    data_arr = np.array(raw_value[0])
            elif isinstance(raw_value, dict) and "value" in raw_value:
                data_arr = np.array(raw_value["value"])
            else:
                data_arr = np.array(raw_value)

            data_arr = data_arr.flatten()
            time_arr = np.arange(len(data_arr)) / actual_rate  # s

            results.append(DAQResult(
                signal_name=signal_name,
                demod_index=demod_idx,
                values=data_arr,
                time=time_arr,
                config_snapshot=config.to_dict(),
            ))

        return results
    finally:
        collector.close()
