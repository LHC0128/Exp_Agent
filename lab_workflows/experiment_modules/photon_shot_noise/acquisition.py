"""Probe 逐点扫描；每次重复独立门控，已读数据先恢复温控再写盘。"""
from pathlib import Path
from uuid import uuid4
import json
import time
import numpy as np
from lockin_amplifier import DAQConfig, daq, result_rate, acquisition_metadata
from ...common import WorkflowCancelled, load_safety_limits, validate_safety_limit
from ...experiment_runtime import check_cancelled, report_runtime_progress
from ...steps import acquisition_window, DeferredAcquisitionWrites
from . import preparation
from .storage import save_npz


def wait(seconds):
    check_cancelled()
    deadline = time.monotonic() + seconds
    while (remaining := deadline - time.monotonic()) > 0:
        check_cancelled()
        time.sleep(min(0.1, remaining))


def persist_power(path, power, index, records):
    values = dict(probe_power=float(power), probe_power_index=index,
        x_waveforms=np.asarray([w["x"] for w in records]), y_waveforms=np.asarray([w["y"] for w in records]),
        time_s=np.asarray([w["time_s"] for w in records]),
        acquisition_metadata_json=np.asarray([json.dumps(w["metadata"], ensure_ascii=False) for w in records]),
        actual_rate=records[-1]["rate"], actual_rates_Sa_s=np.asarray([w["rate"] for w in records]))
    try:
        save_npz(path, **values)
    except BaseException:
        # 主文件替换失败时另存本轮已读内容，旧主文件保持可读；仍向上传递原错误。
        try:
            save_npz(Path(path).with_name(Path(path).stem + "_rescue_" + uuid4().hex + ".npz"), **values)
        except BaseException:
            pass
        raise


def read_record(ctx):
    p = ctx.params
    results = daq.acquire_data(ctx.devices["lockin"], DAQConfig(
        device=ctx.mapping["lockin_r"]["device_id"], trigger_type=0, duration=p.acq_duration,
        grid_cols=ctx.grid_cols, grid_rows=1, grid_mode=2, signal_paths=["sample.x", "sample.y"]),
        demod_idx=p.hf2_demod_idx, actual_rate=ctx.stream_rate, requested_rate=p.hf2_demod_rate,
        cancellation_check=check_cancelled, timeout=p.acq_duration + 10)
    rate = float(result_rate(results, ctx.stream_rate))
    x, y, axis = (np.asarray(v, dtype=float).copy() for v in
                  (results[0].values, results[1].values, results[0].time))
    if (x.shape != (ctx.grid_cols,) or y.shape != x.shape or axis.shape != x.shape
            or not np.isfinite(rate) or rate <= 0 or not np.all(np.isfinite(axis))):
        raise ValueError("锁相返回无效的波形形状、实际采样率或时间轴")
    return dict(x=x, y=y, time_s=axis, metadata=acquisition_metadata(results), rate=rate)


def acquire_scan(ctx):
    preparation.verify_ready(ctx)
    p, raw = ctx.params, ctx.directory.raw
    powers = np.linspace(p.probe_power_start, p.probe_power_stop, p.probe_power_points)
    counts = np.zeros(p.probe_power_points, dtype=int)
    started = time.monotonic()
    rate = ctx.plan.estimated_rate if ctx.plan.module == "scope" else ctx.stream_rate
    completed, failures = 0, []
    for index, power in enumerate(powers):
        check_cancelled()
        validate_safety_limit("Probe_laser_power", float(power), load_safety_limits(ctx.root))
        ctx.devices["sg"].setup_dc(float(power), channel=ctx.channels["Probe_laser_power"])
        ctx.outputs["Probe"] = f"{power:g} V DC + ON"
        records = []
        for repeat in range(p.acq_repeats):
            check_cancelled()
            with DeferredAcquisitionWrites() as pending:
                with acquisition_window(lambda enabled: preparation.set_temp(ctx, enabled), wait, p.system_settle_s):
                    try:
                        record = read_record(ctx)
                        rate = record["rate"]
                    except (WorkflowCancelled, KeyboardInterrupt):
                        raise
                    except Exception as exc:
                        # 保留旧实验坏记录占位与继续策略；取消不作为坏点吞掉。
                        record = dict(x=np.full(ctx.grid_cols, np.nan), y=np.full(ctx.grid_cols, np.nan),
                            time_s=np.arange(ctx.grid_cols) / rate, metadata={"error": str(exc)}, rate=rate)
                        failures.append(dict(power_index=index, repeat=repeat, error=str(exc)))
                    records.append(record)
                    counts[index] = len(records)
                    # ponytail: 累积原子保存的写盘量为 O(repeats²)；大记录时改逐条分片并兼容整点导出。
                    pending.add(persist_power, raw / f"waveforms_P{index:03d}.npz", power, index, records)
                    pending.add(save_npz, raw / "scan_meta.npz", probe_powers=powers,
                        acq_duration=p.acq_duration, acq_repeats=p.acq_repeats, actual_rate=rate,
                        completed_records=counts, complete=counts == p.acq_repeats)
                wait(p.temp_recovery_time_s)
            completed += 1
            elapsed = time.monotonic() - started
            report_runtime_progress("A07", f"Probe {index+1}/{len(powers)}，重复 {repeat+1}/{p.acq_repeats}",
                completed / (len(powers) * p.acq_repeats) * 100,
                estimated_remaining_seconds=elapsed / completed * (len(powers) * p.acq_repeats - completed))
    return dict(data_files=["raw/scan_meta.npz", *[f"raw/waveforms_P{i:03d}.npz" for i in range(len(powers))]],
        actual_rates={"noise_Sa_s": float(rate)}, failed_records=failures,
        acquired_records=int(counts.sum()))
