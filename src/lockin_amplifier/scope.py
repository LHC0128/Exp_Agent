"""两种锁相放大器的内置 Scope：由 Core Scope Module 完成组包和电压缩放。"""

from dataclasses import asdict, dataclass, field
import math
import logging
import sys
import time
from typing import Callable

import numpy as np

from .base import _driver_class

logger = logging.getLogger(__name__)


@dataclass
class ScopeConfig:
    sources: list[str] = field(default_factory=lambda: ["voltage0"])
    sample_rate_hz: float | None = None
    length: int = 2048
    records: int = 1


@dataclass
class ScopeResult:
    signal_name: str
    values: np.ndarray
    time: np.ndarray
    record_index: int
    config_snapshot: dict


def acquire_scope(instr, config: ScopeConfig | None = None, timeout: float = 30.0,
                  *, cancellation_check: Callable[[], None] | None = None) -> list[ScopeResult]:
    """采集原始电压波形；各记录保持独立，不拼成连续波形，不改输入量程。"""
    config = config or ScopeConfig()
    caps = instr.capabilities
    driver = _driver_class(instr.model)
    sources = driver.scope_input_sources
    if not config.sources or len(config.sources) > caps["scope_channels"]:
        raise ValueError(f"{instr.model} Scope 最多同时记录 {caps['scope_channels']} 路")
    if any(source not in sources for source in config.sources):
        raise ValueError(f"{instr.model} Scope 不支持所选电压来源 {config.sources}")
    if not isinstance(config.records, int) or not 1 <= config.records <= 100:
        raise ValueError("Scope 记录数必须在 1–100 内")
    if driver.scope_fixed_length is not None and config.length != driver.scope_fixed_length:
        raise ValueError(f"{instr.model} Scope 固定每条记录 {driver.scope_fixed_length} 点")
    if not isinstance(config.length, int) or not 16 <= config.length <= caps["scope_max_samples"] // len(config.sources):
        raise ValueError("Scope 记录长度超出设备内存")
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Scope 超时必须为有限正数")
    rate = caps["scope_max_rate"] if config.sample_rate_hz is None else config.sample_rate_hz
    if not math.isfinite(rate) or rate <= 0:
        raise ValueError("Scope 采样率必须大于零且为有限值")
    divider = round(math.log2(caps["scope_max_rate"] / rate))
    if not 0 <= divider <= 15 or not math.isclose(rate, caps["scope_max_rate"] / 2**divider, rel_tol=1e-6):
        raise ValueError(f"Scope 采样率必须为 {caps['scope_max_rate']:g} / 2^n Hz（n=0–15）")
    base = f"/{instr.device_id}/scopes/0"
    settings = driver._scope_settings(instr, config, divider)
    original = {key: instr.get_int(f"{base}/{key}") for key in [*settings, "enable"]}
    module = None
    try:
        instr.set_int(f"{base}/enable", 0)
        for key, value in settings.items():
            instr.set_int(f"{base}/{key}", value)
        instr.sync()
        module = instr.daq.scopeModule()
        module.set("mode", 1)
        module.set("historylength", config.records)
        module.set("averager/enable", 0)
        for key, value in driver._scope_module_settings(instr, config).items():
            module.set(key, value)
        module.subscribe(f"{base}/wave")
        module.execute()
        instr.set_int(f"{base}/enable", 1)
        instr.sync()
        started = time.monotonic()
        while module.getInt("records") < config.records or float(np.asarray(module.progress()).ravel()[0]) < 1:
            if cancellation_check:
                cancellation_check()
            if time.monotonic() - started >= timeout:
                raise TimeoutError("Scope 采集超时")
            time.sleep(0.05)
        instr.set_int(f"{base}/enable", 0)
        module.finish()
        raw = module.read(True).get(f"{base}/wave", [])
        if len(raw) != config.records:
            raise RuntimeError("Scope 返回记录数不完整")
        results = []
        for record_idx, item in enumerate(raw):
            record = item[0] if isinstance(item, list) else item
            if int(record.get("flags", 0)) & 7:
                raise RuntimeError(f"Scope 记录 {record_idx} 丢样或传输异常: flags={record['flags']}")
            waves = np.atleast_2d(np.asarray(record["wave"], dtype=float))
            if waves.shape != (len(config.sources), config.length):
                raise RuntimeError(f"Scope 返回波形尺寸异常: {waves.shape}")
            dt = float(record["dt"])
            if not math.isfinite(dt) or dt <= 0 or not np.isfinite(waves).all():
                raise RuntimeError("Scope 返回无效采样间隔或电压")
            has_trigger_time = "timestamp" in record and "triggertimestamp" in record
            if has_trigger_time:
                shift = (int(record["timestamp"]) - int(record["triggertimestamp"])) / instr.clockbase
                # 按官方 Scope 示例，以记录末端时间戳重建触发相对时间。
                axis = np.arange(-config.length, 0) * dt + shift
            else:
                axis = np.arange(config.length) * dt
            snapshot = {**asdict(config), "model": instr.model, "device_id": instr.device_id, "capabilities": caps,
                        "actual_rate": 1 / dt, "dt": dt, "unit": "V",
                        "timestamp": int(record.get("timestamp", 0)),
                        "triggertimestamp": int(record.get("triggertimestamp", 0)),
                        "clockbase": instr.clockbase, "flags": int(record.get("flags", 0)),
                        "time_source": "scope_trigger_timestamp" if has_trigger_time else "scope_dt"}
            for source, wave in zip(config.sources, waves):
                results.append(ScopeResult(source, wave, axis.copy(), record_idx, snapshot))
        return results
    finally:
        original_error = sys.exc_info()[1]
        errors = []
        for action in ([lambda: instr.set_int(f"{base}/enable", 0)]
                       + ([lambda: module.clear()] if module is not None else [])
                       + [lambda key=key, value=value: instr.set_int(f"{base}/{key}", value)
                          for key, value in original.items() if key != "enable"]
                       + [lambda: instr.set_int(f"{base}/enable", original["enable"]), instr.sync]):
            try:
                action()
            except Exception as exc:
                errors.append(str(exc))
        if errors:
            message = "Scope 清理/恢复失败: " + "；".join(errors)
            if original_error is None:
                raise RuntimeError(message)
            logger.error(message)
