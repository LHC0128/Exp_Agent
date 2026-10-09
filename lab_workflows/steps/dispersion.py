"""色散单点连续采集；接收已连接 HF2，保留原始波形及故障诊断。"""

from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Callable

import numpy as np
from lockin_amplifier import DAQConfig
from lockin_amplifier.daq import DAQCollector


def dispersion_sample_count(rate: float, duration_s: float) -> int:
    """Exact 模式向上取整，使记录时间不少于请求时间。"""
    if not math.isfinite(rate) or rate <= 0 or not math.isfinite(duration_s) or duration_s <= 0:
        raise ValueError("色散采样率和采集时间必须为有限正数")
    product = rate * duration_s
    if not math.isfinite(product) or product > np.iinfo(np.int64).max:
        raise ValueError("色散样本数超出 DAQ 支持范围")
    count = math.ceil(product)
    if count < 2:
        raise ValueError("色散每点采集至少需要 2 个样本，请增加采样率或采集时间")
    return count


def save_scan(path: Path, data: dict) -> None:
    """原子保存原始点或小型扫描快照。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".npz.tmp")
    try:
        with temporary.open("wb") as stream:
            np.savez(stream, **data)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_waveforms(raw: dict, base: str, data: dict) -> None:
    errors = []
    for key in ("x", "y"):
        blocks = raw.get(f"{base}/sample.{key}", [])
        if isinstance(blocks, dict):
            blocks = [blocks]
        if not blocks:
            errors.append(f"DAQ 未返回 sample.{key}")
            continue
        values, timestamps = [], []
        for block in blocks:
            value = np.asarray(block.get("value", []), dtype=float)
            ticks = np.asarray(block.get("timestamp", []))
            values.append(value.reshape(-1))
            timestamps.append(ticks.reshape(-1))
            if value.ndim != 2 or value.shape[0] != 1:
                errors.append(f"sample.{key} 必须返回单行波形")
        data[f"{key}_samples"] = np.concatenate(values)
        data[f"{key}_timestamp_ticks"] = np.concatenate(timestamps)
        if len(blocks) != 1:
            errors.append(f"sample.{key} 返回了多个帧")
    if data["x_samples"].size == data["y_samples"].size:
        data["r_samples"] = np.hypot(data["x_samples"], data["y_samples"])
    data["sample_count"] = np.array(data["y_samples"].size)
    ticks = data["x_timestamp_ticks"]
    if ticks.size and ticks.dtype.kind in "ui" and np.array_equal(ticks, data["y_timestamp_ticks"]):
        data["timestamp_ticks"] = ticks
        data["time_s"] = (ticks - ticks[0]).astype(float) / data["clockbase_hz"]
    if errors:
        raise ValueError("；".join(errors))


def acquire_dispersion_point(hf2, *, demod_idx: int, duration_s: float,
                             requested_rate: float, offset_v: float, path: Path,
                             cancellation_check: Callable[[], None]) -> dict:
    """采集一个稳定后的 DC 点；异常时保存能够读取的波形并保留原始异常。"""
    dispersion_sample_count(requested_rate, duration_s)
    base = hf2.demod_path(demod_idx)
    rate = float(hf2.get_double(f"{base}/rate"))
    count = dispersion_sample_count(rate, duration_s)
    clockbase = float(hf2.clockbase)
    if not math.isfinite(clockbase) or clockbase <= 0:
        raise ValueError("HF2 时钟基准必须为有限正数")
    config = DAQConfig(device=hf2.device_id, trigger_type=0, duration=count / rate,
                       grid_cols=count, grid_rows=1, grid_mode=4,
                       signal_paths=["sample.x", "sample.y"], record_flags=4)
    data = {
        "schema_version": np.array(3), "offset_v": np.array(offset_v),
        "demod_idx": np.array(demod_idx), "requested_rate_sa_s": np.array(requested_rate),
        "actual_rate_sa_s": np.array(rate), "requested_duration_s": np.array(duration_s),
        "actual_duration_s": np.array(count / rate), "expected_sample_count": np.array(count),
        "sample_count": np.array(0), "clockbase_hz": np.array(clockbase),
        "grid_mode": np.array(4), "record_flags": np.array(4),
        "trigger_type": np.array(0), "grid_rows": np.array(1), "frame_count": np.array(1),
        "started_unix_s": np.array(time.time()),
        "time_s": np.empty(0), "timestamp_ticks": np.empty(0, dtype=np.uint64),
        **{f"{key}_samples": np.empty(0) for key in ("x", "y", "r")},
        **{f"{key}_timestamp_ticks": np.empty(0, dtype=np.uint64) for key in ("x", "y")},
    }
    collector = DAQCollector(hf2)
    error = None
    raw = None
    try:
        cancellation_check()
        hf2.sync()
        collector.configure(config)
        collector.subscribe(config.signal_paths, demod_idx)
        collector.execute()
        collector.wait(timeout=count / rate + 5, cancellation_check=cancellation_check)
        cancellation_check()
        raw = collector.read()
        _read_waveforms(raw, base, data)
        for key in ("x", "y", "r"):
            values = data[f"{key}_samples"]
            if values.size != count or not np.isfinite(values).all():
                raise ValueError(f"色散 {key} 样本数错误或包含非有限值")
        ticks = data["x_timestamp_ticks"]
        if (ticks.size != count or ticks.dtype.kind not in "ui"
                or not np.array_equal(ticks, data["y_timestamp_ticks"])
                or np.any(ticks[1:] <= ticks[:-1])):
            raise ValueError("色散 X/Y 时间戳缺失、未对齐或非递增")
        if not np.allclose(np.diff(data["time_s"]), 1 / rate, rtol=0, atol=1.5 / clockbase):
            raise ValueError("色散样本时间戳不连续或与实际采样率不一致")
        for key in ("x", "y", "r"):
            data[f"{key}_median"] = np.array(np.median(data[f"{key}_samples"]))
    except BaseException as exc:
        error = exc
        if raw is None:
            try:
                _read_waveforms(collector.read(), base, data)
            except Exception as read_error:
                print(f"色散故障点读取未完成：{read_error}", flush=True)
    finally:
        try:
            collector.close(strict=True)
        except Exception as close_error:
            if error is None:
                error = close_error
            else:
                print(f"色散 DAQ 清理失败：{close_error}", flush=True)
        data["completed"] = np.array(error is None)
        data["error"] = np.array(str(error) if error is not None else "")
        data["finished_unix_s"] = np.array(time.time())
        try:
            save_scan(path, data)
        except Exception as save_error:
            if error is None:
                error = save_error
            else:
                print(f"色散故障点保存失败：{save_error}", flush=True)
    if error is not None:
        raise error
    return data
