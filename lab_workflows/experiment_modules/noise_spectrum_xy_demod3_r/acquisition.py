"""Demod3 连续 R 采集与解析工具。"""

from __future__ import annotations

from typing import Any
import os
import time

import numpy as np

from ...common import WorkflowCancelled
from ...experiment_runtime import check_cancelled, report_runtime_progress
from ...steps import DeferredAcquisitionWrites, acquisition_window
from . import excitation, run_format
from .scan import build_scan_axes, iter_frequency_batches



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


def acquire_demod_r_mean(collector: Any, expected_path: str, duration_s: float, *, instr=None,
                         config=None, demod_idx=0, requested_rate=None, actual_rate=None,
                         cancellation_check=None, return_metadata=False) -> float:
    """连续采集 Demod R，在内存中求均值且不保留时序样本。"""
    if instr is not None:
        from lockin_amplifier import daq, acquisition_metadata
        results = daq.acquire_data(instr, config, demod_idx, actual_rate,
                                   max(2.0, duration_s + 1.0), requested_rate=requested_rate,
                                   cancellation_check=cancellation_check, strict_cleanup=True)
        values = next(item.values for item in results if item.signal_name == "sample.r")
        mean = float(np.mean(values)) if values.size else float("nan")
        return (mean, acquisition_metadata(results)) if return_metadata else mean
    collector.execute()
    collector.wait(
        1.0,
        poll_interval=min(0.02, max(0.005, float(duration_s) / 5.0)),
        timeout=max(2.0, float(duration_s) + 1.0),
    )
    payload = collector.read()
    values = extract_polled_values(payload, expected_path)
    return float(np.mean(values)) if values.size else float("nan")


def save_matrix(path, r_mean_v, control_frequency_hz, envelope_v, demod3_frequency_hz):
    """临时文件原子替换，慢盘或失败不截断上一份完整矩阵。"""
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        with temporary.open("wb") as stream:
            np.savez(stream, r_mean_V=np.asarray(r_mean_v), control_frequency_Hz=np.asarray(control_frequency_hz),
                xy_envelope_voltage_V=np.asarray(envelope_v), demod3_frequency_Hz=np.asarray(demod3_frequency_hz))
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _persist_batch(directory, matrix, control, envelope, frequencies, records):
    path = directory.raw / run_format.RAW_FILE
    try:
        save_matrix(path, matrix, control, envelope, frequencies)
    except BaseException as exc:
        # 主文件保留上次成功快照；另存当前矩阵，离线资格明确识别该抢救文件。
        try:
            save_matrix(directory.raw / "demod3_r_mean_matrix_recovered.npz", matrix, control, envelope, frequencies)
        except BaseException as rescue:
            if hasattr(exc, "add_note"):
                exc.add_note(f"当前批次抢救保存亦失败: {rescue}")
        raise
    directory.update_config(demod3_acquisition=records,
        completed_points=int(np.count_nonzero(np.isfinite(matrix))))
    for record in records:
        if "error" in record:
            print(f"[警告] 控制索引 {record['control_index']} / Demod3 {record['frequency_Hz']:g} Hz: {record['error']}")


def acquire_scan(ctx):
    """保留控制外层和 Demod3 同向内层；仅当前批次元数据延迟落盘。"""
    p, directory = ctx.params, ctx.directory
    control, envelope, frequencies = build_scan_axes(p)
    matrix = np.full((len(control), len(frequencies)), np.nan)
    save_matrix(directory.raw / run_format.RAW_FILE, matrix, control, envelope, frequencies)
    started, attempted = time.monotonic(), 0
    total = matrix.size
    report_runtime_progress("scan", "开始 Demod3 R 二维扫描", estimated_remaining_seconds=None)
    for row, voltage in enumerate(envelope):
        check_cancelled()
        excitation.upload(ctx, float(voltage), ctx.xy_phase_deg)
        for batch in iter_frequency_batches(len(frequencies), p.temp_recovery_batch_points):
            records = []
            with DeferredAcquisitionWrites(restore=lambda: excitation.set_temp(ctx, True)) as pending:
                try:
                    # 组首实际配置在温控开启时完成；组内换频后重新稳定。
                    rate, config = excitation.configure_demod3(ctx, float(frequencies[batch.start]))
                    with acquisition_window(lambda enabled: excitation.set_temp(ctx, enabled),
                                            excitation.sleep, p.system_settle_s):
                        for column in batch:
                            check_cancelled()
                            if column != batch.start:
                                rate, config = excitation.configure_demod3(ctx, float(frequencies[column]))
                                excitation.sleep(p.system_settle_s)
                            record = dict(control_index=row, demod3_index=column, frequency_Hz=float(frequencies[column]))
                            try:
                                value, info = acquire_demod_r_mean(None,
                                    ctx.devices["lockin"].demod_path(p.demod3_idx) + "/sample.r",
                                    p.demod3_acquisition_duration_s, instr=ctx.devices["lockin"], config=config,
                                    demod_idx=p.demod3_idx, requested_rate=p.demod3_rate_sa_s, actual_rate=rate,
                                    cancellation_check=check_cancelled, return_metadata=True)
                                matrix[row, column] = value if np.isfinite(value) else np.nan
                                record["acquisition"] = info
                                if not np.isfinite(value):
                                    record["error"] = "未得到有限 R，保留 NaN"
                            except WorkflowCancelled:
                                raise
                            except Exception as exc:
                                # 与原实验一致：单点采集失败留 NaN，不伪造或插值。
                                record["error"] = str(exc)
                            records.append(record)
                            attempted += 1
                finally:
                    pending.add(_persist_batch, directory, matrix, control, envelope, frequencies, records)
            excitation.sleep(p.temp_recovery_time_s)
            elapsed = time.monotonic() - started
            report_runtime_progress("scan", f"Demod3 R {attempted}/{total}", 100 * attempted / total,
                estimated_remaining_seconds=elapsed / attempted * (total - attempted) if attempted else None)
    return dict(elapsed_scan_s=time.monotonic() - started,
        completed_points=int(np.count_nonzero(np.isfinite(matrix))), attempted_points=attempted,
        actual_rates=ctx.state.get("actual_rates", {}) | {"demod3_Sa_s": float(rate)})
