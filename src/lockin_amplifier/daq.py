"""DAQ 模块数据采集模块.

封装 LabOne Data Acquisition 模块，支持连续和触发模式采集。
"""

import time
import logging
import math
from dataclasses import replace
import sys
from typing import Callable, List, Optional
import numpy as np

from .base import LockinInstrument
from .config import DAQConfig, DAQResult

logger = logging.getLogger(__name__)


class DAQCollector:
    """DAQ 模块封装器.

    管理 DAQ 模块的创建、配置、订阅、执行和读取。

    参数
    ----------
    instr : LockinInstrument
        已连接的仪器实例
    """

    def __init__(self, instr: LockinInstrument):
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
        if config.device and config.device.lower() != self._instr.device_id:
            raise ValueError("DAQ 设备 ID 与当前锁相放大器不一致")
        config = replace(config, device=self._instr.device_id)
        if not math.isfinite(config.duration) or config.duration <= 0 or config.grid_cols < 1 or config.grid_rows < 1:
            raise ValueError("DAQ 时长和网格尺寸必须大于零")
        if not math.isfinite(config.trigger_delay) or not math.isfinite(config.trigger_level):
            raise ValueError("DAQ 触发延迟和电平必须为有限值")
        self._mod = self._instr.daq.dataAcquisitionModule()

        # 基础设置
        self._mod.set("dataAcquisitionModule/device", config.device)
        self._mod.set("dataAcquisitionModule/type", config.trigger_type)
        self._mod.set("dataAcquisitionModule/duration", config.duration)
        self._mod.set("dataAcquisitionModule/count", 1)
        self._mod.set("dataAcquisitionModule/endless", 0)
        self._mod.set("dataAcquisitionModule/flags", config.record_flags)

        # 触发设置（trigger_type=1 时有效）
        if config.trigger_type == 1:
            # triggernode: 使用辅助输入通道作为触发源
            if config.trigger_channel not in (0, 1):
                raise ValueError("Aux Input 触发通道必须为 0 或 1")
            trigger_node = f"/{config.device}/auxins/0/sample.AuxIn{config.trigger_channel}"
            self._mod.set("dataAcquisitionModule/triggernode", trigger_node)
            self._mod.set("dataAcquisitionModule/level", config.trigger_level)
            self._mod.set("dataAcquisitionModule/hysteresis", 0.5)
            edge_str = "falling" if config.trigger_slope == 1 else "rising"
            self._mod.set("dataAcquisitionModule/edge", edge_str)
            self._mod.set("dataAcquisitionModule/delay", config.trigger_delay)
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
             timeout: float = 0.0, *, cancellation_check: Callable[[], None] | None = None) -> None:
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
        started = time.monotonic()
        while np.asarray(self._mod.progress()).item() < target:
            if cancellation_check:
                cancellation_check()
            time.sleep(poll_interval)
            if timeout > 0 and time.monotonic() - started >= timeout:
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

    def close(self, *, strict: bool = False) -> None:
        """清理 DAQ 模块；严格模式不把清理失败当作成功。"""
        if self._mod is not None:
            try:
                self._mod.clear()
                logger.debug("DAQ 模块已清理")
            except Exception as exc:
                logger.warning("DAQ 模块清理失败: %s", exc)
                if strict:
                    raise
            finally:
                self._mod = None
                self._subscribed_paths = []


def acquire_data(
    instr: LockinInstrument,
    config: Optional[DAQConfig] = None,
    demod_idx: int = 0,
    actual_rate: Optional[float] = None,
    timeout: float = 30.0,
    *,
    cancellation_check: Callable[[], None] | None = None,
    strict_cleanup: bool = False,
    requested_rate: Optional[float] = None,
) -> List[DAQResult]:
    """一键采集解调时序：按设备全局模式和请求速率选择 DAQ / Scope.

    返回结构化的 DAQResult 列表，格式与 sds_acquisition.AcquisitionResult 对齐。

    参数
    ----------
    instr : LockinInstrument
        已连接的仪器实例
    config : DAQConfig, optional
        DAQ 采集配置
    demod_idx : int
        解调器索引
    actual_rate : float, optional
        解调通信回读率 (Sa/s)。最终波形速率由返回时间轴决定。
    requested_rate : float, optional
        当次请求速率，用于模块选择；旧调用未传时使用解调通信回读率。
    timeout : float
        采集超时时间 (s)
    cancellation_check : callable, optional
        等待期间调用的协作式取消检查；抛出异常后仍清理 DAQ 模块。
        默认不启用，保持其他实验的采集行为。
    strict_cleanup : bool
        为 True 时清理失败使采集失败；已有采集异常仍优先保留。

    返回
    -------
    List[DAQResult]
        每路信号一个 DAQResult，含 values 和 time 数组
    """
    if config is None:
        config = DAQConfig()
    config = replace(config, device=config.device or instr.device_id)
    from .acquisition import select_acquisition, acquire_demod_scope, restore_demod_streams
    from .base import lockin_capabilities
    model = getattr(instr, "model", "HF2")
    capabilities = getattr(instr, "capabilities", None) or lockin_capabilities(model)
    try:
        stream_rate = actual_rate or instr.get_double(f"{instr.demod_path(demod_idx)}/rate")
        # 旧调用方没有请求值时继续使用已有速率；正式实验均显式传入。
        requested_rate = stream_rate if requested_rate is None else requested_rate
        if cancellation_check:
            cancellation_check()
        if config.device != instr.device_id:
            raise ValueError("采集设备 ID 必须与当前锁相连接一致")
        plan = select_acquisition(model, capabilities, config, requested_rate, demod_idx,
                                  getattr(instr, "acquisition_mode", "auto"))
    except BaseException:
        try:
            restore_demod_streams(instr)
        except Exception:
            logger.exception("采集准备失败后的数据流恢复失败")
        raise
    logger.info("%s 解调采集: %s，请求 %.6g Sa/s，预计 %.6g Sa/s", model,
                plan.module.upper(), requested_rate, plan.estimated_rate)
    if plan.module == "scope":
        return acquire_demod_scope(instr, config, plan, demod_idx, timeout, cancellation_check)

    restore_demod_streams(instr)

    collector = DAQCollector(instr)
    try:
        collector.configure(config)
        collector.subscribe(config.signal_paths, demod_idx)
        for extra_path in config.extra_paths:
            collector.subscribe_raw(extra_path)
        collector.execute()
        collector.wait(1.0, timeout=timeout, cancellation_check=cancellation_check)
        raw = collector.read()
        if cancellation_check:
            cancellation_check()
        logger.info("数据采集完成，共 %d 个信号路径", len(raw))

        if actual_rate is None or actual_rate <= 0:
            actual_rate = instr.get_double(
                f"{instr.demod_path(demod_idx)}/rate"
            )

        results = []
        origin = None
        for raw_value in raw.values():
            bursts = raw_value if isinstance(raw_value, list) else [raw_value]
            for burst in bursts:
                if isinstance(burst, dict) and "timestamp" in burst:
                    stamps = np.asarray(burst["timestamp"]).ravel()
                    if stamps.size:
                        first = int(stamps[0])
                        origin = first if origin is None else min(origin, first)
        clockbase = instr.clockbase if origin is not None else None
        for path, raw_value in raw.items():
            # 过滤掉 DAQ 模块参数节点，保留信号数据
            if "sample." not in path and "sample" not in path:
                continue
            signal_name = path.split("/")[-1]

            bursts = raw_value if isinstance(raw_value, list) else [raw_value]
            arrays, times, boundaries, cursor = [], [], [], 0
            time_source = "module_timestamp"
            for burst in bursts:
                # DAQ chunk 的 0x1 是完成标记，只有 0x4 表示丢样；与 Scope flags 不同。
                flags = burst.get("header", {}).get("flags", burst.get("flags", 0)) if isinstance(burst, dict) else 0
                if np.any(np.asarray(flags, dtype=int) & 4):
                    raise RuntimeError(f"DAQ {signal_name} 丢样或传输异常")
                values = np.atleast_2d(np.asarray(burst["value"] if isinstance(burst, dict) else burst))
                if not values.size or not np.isfinite(values).all():
                    raise RuntimeError(f"DAQ {signal_name} 返回空波形或非有限值")
                stamps = np.asarray(burst.get("timestamp", [])) if isinstance(burst, dict) else np.array([])
                if stamps.size:
                    stamps = np.broadcast_to(stamps, values.shape)
                    # 先做整数差，避免大时间戳转换 float 后损失精度或无符号回绕。
                    axis = np.array([int(tick) - origin for tick in stamps.ravel()], dtype=float).reshape(values.shape) / clockbase
                else:
                    step = 1 / actual_rate if config.grid_mode == 4 else config.duration / config.grid_cols
                    axis = np.broadcast_to(np.arange(values.shape[-1]) * step, values.shape)
                    time_source = "synthetic_rate" if config.grid_mode == 4 else "synthetic_grid"
                for row, axis_row in zip(values, axis):
                    arrays.append(row.ravel())
                    times.append(axis_row.ravel())
                    boundaries.append((cursor, cursor + row.size))
                    cursor += row.size
            data_arr = np.concatenate(arrays) if arrays else np.array([])
            time_arr = np.concatenate(times) if times else np.array([])
            steps = np.concatenate([np.diff(axis) for axis in times if axis.size > 1]) if any(axis.size > 1 for axis in times) else np.array([])
            if steps.size and (not np.isfinite(steps).all() or (steps <= 0).any()):
                raise RuntimeError("DAQ 返回无效时间轴或丢样")
            capture_rate = 1 / float(np.median(steps)) if steps.size else (
                actual_rate if config.grid_mode == 4 else config.grid_cols / config.duration)
            tolerance = max(1.5 / clockbase if clockbase else 0, 1e-5 / capture_rate)
            if steps.size and not np.allclose(steps, 1 / capture_rate, rtol=0, atol=tolerance):
                raise RuntimeError("DAQ 时间轴不连续，存在丢样")
            unit = "rad" if signal_name == "sample.theta" else (
                "Hz" if signal_name == "sample.frequency" else
                "A" if model == "MFLI" and instr.get_int(f"{instr.demod_path(demod_idx)}/adcselect") == 1 else "V")

            results.append(DAQResult(
                signal_name=signal_name,
                demod_index=demod_idx,
                values=data_arr,
                time=time_arr,
                config_snapshot={**config.to_dict(), "actual_rate": capture_rate,
                                 "acquisition_module": "daq", "requested_rate": requested_rate,
                                 "demod_stream_rate": actual_rate,
                                 "actual_duration": data_arr.size / capture_rate,
                                 "unit": unit,
                                 "time_source": time_source, "timestamp_origin": origin,
                                 "clockbase": clockbase, "model": getattr(instr, "model", "HF2"),
                                 "capabilities": instr.capabilities if isinstance(instr, LockinInstrument) else {}},
                record_slices=boundaries,
            ))

        if results:
            print(f"DAQ 解调采集：请求 {requested_rate:g} Sa/s，实际 {results[0].config_snapshot['actual_rate']:g} Sa/s", flush=True)
        return results
    finally:
        original_error = sys.exc_info()[1]
        try:
            collector.close(strict=strict_cleanup)
        except Exception:
            if original_error is None:
                raise
