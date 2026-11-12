"""SDS / DHO900 统一创建入口；构造、配置类型导入和保存均不连接硬件。"""

from __future__ import annotations

from typing import Any, Mapping

from sds_acquisition import (
    AcquisitionConfig, AcquisitionResult, ChannelConfig, TriggerConfig,
    SDSInstrument, SDSAcquisition, load_npz, save_to_csv,
)
from sds_acquisition.save import save_to_npz as _save_to_npz
from dho900 import DHO900Instrument, DHO900Acquisition
from dho900.instrument import MODELS


def create_oscilloscope(config: Mapping[str, Any]) -> SDSInstrument | DHO900Instrument:
    """按设备库原始条目或解析映射创建驱动，不猜型号、不自动连接/换设备。"""
    kind = str(config.get("instrument", ""))
    model = str(config.get("model", "")).strip().upper()
    if kind == "sds_acquisition" and model == "SDS":
        driver = SDSInstrument
    elif kind == "oscilloscope" and model in MODELS | {"DHO900"}:
        driver = DHO900Instrument
    else:
        raise ValueError(f"不支持的示波器类型/型号: {kind}/{model}")
    resource = str(config.get("resource") or "").strip()
    if not resource:
        raise ValueError("示波器配置缺少 resource")
    connection = dict(config.get("connection") or {})
    timeout = config.get("timeout", connection.get("timeout", 10000))
    if driver is DHO900Instrument:
        return driver(resource, timeout=timeout,
                      read_chunk_points=connection.get("read_chunk_points", config.get("read_chunk_points", 10000)))
    return driver(resource, timeout=timeout)


def create_scope_acquisition(instrument, *, check_cancelled=None, maximum_points=None):
    """采集器与具体驱动配对；不把 RIGOL preamble 交给 SDS 解码器。"""
    if isinstance(instrument, DHO900Instrument):
        acquirer = DHO900Acquisition
    elif isinstance(instrument, SDSInstrument):
        acquirer = SDSAcquisition
    else:
        raise TypeError("请传入统一工厂支持的 SDS 或 DHO900 仪器实例")
    return acquirer(instrument, check_cancelled=check_cancelled, maximum_points=maximum_points)


def save_to_npz(filepath: str, results: list[AcquisitionResult], config_dict: dict | None = None,
                save_mode: str = "voltage_time") -> str:
    """两型号通用 NPZ；默认保存实际时间/电压，不虚构 DHO900 的原始 ADC 码。"""
    payload = config_dict
    if any(result.preamble_dict.get("model") == "DHO900" for result in results):
        if save_mode not in {"voltage_time", "all"}:
            raise ValueError("DHO900 ASCII 采集没有原始 ADC 码；请保存 voltage_time，不使用 raw/voltage_only")
        payload = {**(config_dict or {}), "scope_acquisition_snapshots": {
            f"ch{result.channel}": result.config_snapshot for result in results
        }}
    return _save_to_npz(filepath, results, payload, save_mode=save_mode)


__all__ = [
    "create_oscilloscope", "create_scope_acquisition", "AcquisitionConfig", "AcquisitionResult",
    "ChannelConfig", "TriggerConfig", "save_to_npz", "load_npz", "save_to_csv",
]
