"""Demod3 连续 R 采集与解析工具。"""

from __future__ import annotations

from typing import Any

import numpy as np



def extract_polled_values(payload: dict[str, Any], expected_path: str) -> np.ndarray:
    """从 HF2 poll 返回值中提取一维有限数值数组。"""
    candidate = payload.get(expected_path)
    if candidate is None:
        expected_lower = expected_path.lower()
        for path, value in payload.items():
            if str(path).lower() == expected_lower or str(path).lower().endswith("/sample.r"):
                candidate = value
                break
    if candidate is None:
        return np.array([], dtype=float)
    if isinstance(candidate, list) and candidate:
        candidate = candidate[0]
    if isinstance(candidate, dict):
        candidate = candidate.get("value", [])
    values = np.asarray(candidate, dtype=float).reshape(-1)
    return values[np.isfinite(values)]


def acquire_demod_r_mean(collector: Any, expected_path: str, duration_s: float) -> float:
    """连续采集 Demod R，在内存中求均值且不保留时序样本。"""
    collector.execute()
    collector.wait(
        1.0,
        poll_interval=min(0.02, max(0.005, float(duration_s) / 5.0)),
        timeout=max(2.0, float(duration_s) + 1.0),
    )
    payload = collector.read()
    values = extract_polled_values(payload, expected_path)
    return float(np.mean(values)) if values.size else float("nan")
