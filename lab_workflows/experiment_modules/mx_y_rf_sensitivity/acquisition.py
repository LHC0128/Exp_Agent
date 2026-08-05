"""HF2 Demod0 的 R 或 R/X/Y 时间序列采集与质量统计。"""

from __future__ import annotations

from typing import Any

import numpy as np
from lockin_amplifier import DAQConfig, daq


def normalize_r_daq_results(
    results: list[Any],
    actual_rate_sa_s: float,
) -> dict[str, np.ndarray]:
    """把 DAQResult 规范化为 R 和对应时间轴。"""
    r_values: np.ndarray | None = None
    for result in results:
        name = str(result.signal_name).lower()
        if name == "r" or name.endswith(".r"):
            r_values = np.asarray(result.values, dtype=float).reshape(-1)
            break
    if r_values is None:
        raise RuntimeError("HF2 DAQ 缺少 R 通道")
    r_values = r_values[np.isfinite(r_values)]
    if r_values.size < 8:
        raise RuntimeError(
            f"HF2 DAQ 的 R 有效样本不足 8: {r_values.size}"
        )
    return {
        "time_s": np.arange(r_values.size, dtype=float)
        / float(actual_rate_sa_s),
        "r": r_values,
    }


def acquire_r(
    hf2: Any,
    *,
    device_id: str,
    demod_idx: int,
    actual_rate_sa_s: float,
    duration_s: float,
) -> dict[str, np.ndarray]:
    """连续采集 Demod0 的 R，不订阅 X/Y。"""
    config = DAQConfig(
        device=device_id,
        trigger_type=0,
        duration=float(duration_s),
        grid_cols=max(8, int(round(actual_rate_sa_s * duration_s))),
        grid_rows=1,
        grid_mode=2,
        signal_paths=["sample.r"],
    )
    results = daq.acquire_data(
        hf2,
        config=config,
        demod_idx=demod_idx,
        actual_rate=actual_rate_sa_s,
        timeout=max(5.0, float(duration_s) + 3.0),
    )
    return normalize_r_daq_results(results, actual_rate_sa_s)


def normalize_rxy_daq_results(
    results: list[Any],
    actual_rate_sa_s: float,
) -> dict[str, np.ndarray]:
    """把 DAQResult 规范化为同步的 R/X/Y 和对应时间轴。"""
    signals: dict[str, np.ndarray] = {}
    for result in results:
        name = str(result.signal_name).lower()
        for signal in ("r", "x", "y"):
            if name == signal or name.endswith(f".{signal}"):
                signals[signal] = np.asarray(
                    result.values,
                    dtype=float,
                ).reshape(-1)
                break
    missing = [signal for signal in ("r", "x", "y") if signal not in signals]
    if missing:
        raise RuntimeError(
            "HF2 DAQ 缺少正交通道: " + ", ".join(missing)
        )
    sample_count = min(signal.size for signal in signals.values())
    if sample_count < 8:
        raise RuntimeError(
            f"HF2 DAQ 的 R/X/Y 同步样本不足 8: {sample_count}"
        )
    finite = np.ones(sample_count, dtype=bool)
    for signal in signals.values():
        finite &= np.isfinite(signal[:sample_count])
    if int(np.count_nonzero(finite)) < 8:
        raise RuntimeError(
            "HF2 DAQ 的 R/X/Y 同步有限样本不足 8: "
            f"{int(np.count_nonzero(finite))}"
        )
    normalized = {
        signal: values[:sample_count][finite]
        for signal, values in signals.items()
    }
    normalized["time_s"] = (
        np.arange(normalized["r"].size, dtype=float)
        / float(actual_rate_sa_s)
    )
    return normalized


def acquire_rxy(
    hf2: Any,
    *,
    device_id: str,
    demod_idx: int,
    actual_rate_sa_s: float,
    duration_s: float,
) -> dict[str, np.ndarray]:
    """连续同步采集 Demod0 的 R/X/Y。"""
    config = DAQConfig(
        device=device_id,
        trigger_type=0,
        duration=float(duration_s),
        grid_cols=max(8, int(round(actual_rate_sa_s * duration_s))),
        grid_rows=1,
        grid_mode=2,
        signal_paths=["sample.r", "sample.x", "sample.y"],
    )
    results = daq.acquire_data(
        hf2,
        config=config,
        demod_idx=demod_idx,
        actual_rate=actual_rate_sa_s,
        timeout=max(5.0, float(duration_s) + 3.0),
    )
    return normalize_rxy_daq_results(results, actual_rate_sa_s)


def summarize_r(payload: dict[str, np.ndarray]) -> dict[str, float]:
    """返回 R 均值、标准差和有效样本数。"""
    r = np.asarray(payload["r"], dtype=float)
    return {
        "r_mean_v": float(np.mean(r)),
        "r_scalar_mean_v": float(np.mean(r)),
        "r_std_v": float(np.std(r, ddof=1)) if r.size > 1 else 0.0,
        "n_samples": int(r.size),
    }


def summarize_rxy(payload: dict[str, np.ndarray]) -> dict[str, float]:
    """返回同步 R/X/Y 的均值、标准差和复噪声。"""
    r = np.asarray(payload["r"], dtype=float)
    x = np.asarray(payload["x"], dtype=float)
    y = np.asarray(payload["y"], dtype=float)
    r_std = float(np.std(r, ddof=1)) if r.size > 1 else 0.0
    x_std = float(np.std(x, ddof=1)) if x.size > 1 else 0.0
    y_std = float(np.std(y, ddof=1)) if y.size > 1 else 0.0
    return {
        "r_mean_v": float(np.mean(r)),
        "r_scalar_mean_v": float(np.mean(r)),
        "r_std_v": r_std,
        "x_mean_v": float(np.mean(x)),
        "x_std_v": x_std,
        "y_mean_v": float(np.mean(y)),
        "y_std_v": y_std,
        "complex_std_v": float(np.hypot(x_std, y_std)),
        "n_samples": int(r.size),
    }
