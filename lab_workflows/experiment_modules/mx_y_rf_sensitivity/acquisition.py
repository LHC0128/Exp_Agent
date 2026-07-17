"""HF2 Demod0 的 R 时间序列采集与质量统计。"""

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


def summarize_r(payload: dict[str, np.ndarray]) -> dict[str, float]:
    """返回 R 均值、标准差和有效样本数。"""
    r = np.asarray(payload["r"], dtype=float)
    return {
        "r_mean_v": float(np.mean(r)),
        "r_scalar_mean_v": float(np.mean(r)),
        "r_std_v": float(np.std(r, ddof=1)) if r.size > 1 else 0.0,
        "n_samples": int(r.size),
    }
