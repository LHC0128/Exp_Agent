"""A03：在 X/Y 直流偏置网格上同时读 1f 与 2f 解调的复振幅；温控关闭窗口内只读，恢复后落盘。

每个网格点由 ``X_BIAS_V + 偏移/1000`` 与 ``Y_BIAS_V + 偏移/1000`` 给出；每个点重复
``N_REPEAT`` 次，每次都在温控关闭窗口内连续采 ``SCAN_POINT_DURATION_S``。两路解调器在同一个
DAQ 采集里同时订阅，保证 1f 与 2f 的读数来自同一段时间。
"""
from __future__ import annotations

import csv
import math
import time
from datetime import datetime
from pathlib import Path

import numpy as np
from lockin_amplifier import DAQConfig
from lockin_amplifier.daq import DAQCollector

from ...common import load_safety_limits, validate_safety_limit
from ...experiment_runtime import check_cancelled, report_runtime_progress
from ...steps import DeferredAcquisitionWrites, configure_fixed_dc_field, set_temperature_switch
from . import preparation, run_format
from .run_format import SOURCE_KEY, TEC_KEY, X_KEY, Y_KEY

MIN_POINT_SAMPLES = 8


def _daq_config(ctx, rate):
    p = ctx.params
    return DAQConfig(device=ctx.devices["hf2"].device_id, duration=p.scan_point_duration_s,
                     grid_cols=max(8, int(round(rate * p.scan_point_duration_s))), grid_rows=1,
                     grid_mode=2, signal_paths=["sample.x", "sample.y"])


def _burst_values(value):
    bursts = value if isinstance(value, list) else [value]
    arrays = []
    for burst in bursts:
        flags = burst.get("flags", 0) if isinstance(burst, dict) else 0
        if np.any(np.asarray(flags, dtype=int) & 4):
            raise RuntimeError("DAQ 丢样或传输异常")
        raw = burst["value"] if isinstance(burst, dict) else burst
        array = np.asarray(raw, dtype=float).ravel()
        if array.size:
            arrays.append(array)
    if not arrays:
        return np.empty(0)
    return np.concatenate(arrays)


def _acquire_point(ctx, config, rate):
    """一次 DAQ 采集同时读两路解调器的 X/Y；返回各分量的均值与标准误。"""
    p, mfli = ctx.params, ctx.devices["hf2"]
    collector = DAQCollector(mfli)
    try:
        collector.configure(config)
        collector.subscribe(["sample.x", "sample.y"], p.demod0_idx)
        collector.subscribe(["sample.x", "sample.y"], p.demod1_idx)
        collector.execute()
        collector.wait(1.0, timeout=p.scan_point_duration_s + 10, cancellation_check=check_cancelled)
        raw = collector.read()
    finally:
        collector.close(strict=True)
    series = {}
    for path, value in raw.items():
        if "sample." not in path:
            continue
        parts = path.split("/")
        if "demods" not in parts:
            continue
        demod_index = int(parts[parts.index("demods") + 1])
        component = parts[-1].split(".")[-1]
        series[(demod_index, component)] = _burst_values(value)
    needed = [(p.demod0_idx, "x"), (p.demod0_idx, "y"), (p.demod1_idx, "x"), (p.demod1_idx, "y")]
    missing = [key for key in needed if key not in series]
    if missing:
        raise RuntimeError(f"MFLI 未返回所需解调信号：{missing}")
    size = min(series[key].size for key in needed)
    if size < MIN_POINT_SAMPLES:
        raise RuntimeError(f"该点有效样本不足 {MIN_POINT_SAMPLES}：{size}")
    result = {"samples": size}
    for key in needed:
        values = series[key][:size]
        if not np.isfinite(values).all():
            raise RuntimeError(f"MFLI 解调信号 {key} 含非有限值")
        result[f"x{key[0]}" if key[1] == "x" else f"y{key[0]}"] = float(np.mean(values))
        result[f"x{key[0]}_sem" if key[1] == "x" else f"y{key[0]}_sem"] = (
            float(np.std(values, ddof=1) / math.sqrt(values.size)) if values.size > 1 else 0.0)
    return result


def _start_table(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        csv.DictWriter(handle, fieldnames=list(run_format.GRID_FIELDS)).writeheader()


def _append_row(path, row):
    path = Path(path)
    new = not path.exists() or path.stat().st_size == 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(run_format.GRID_FIELDS))
        if new:
            writer.writeheader()
        writer.writerow(row)


def _read_temperature_c(ctx):
    tec = ctx.devices.get("tec")
    if tec is None:
        return None
    try:
        value = float(tec.get_temperature(channel=int(ctx.mapping.get(TEC_KEY, {}).get("channel", 1))))
    except Exception:
        return None
    return value if math.isfinite(value) else None


def _progress(done, total, started):
    elapsed = time.monotonic() - started
    report_runtime_progress("A03", f"X/Y 网格 {done}/{total}", 100.0 * min(done, total) / max(1, total),
                            estimated_remaining_seconds=(elapsed / done * (total - done)) if done else None)


def measure_grid(ctx):
    """A03：逐点写 X/Y 偏置、按温控分组采集两路解调，组末恢复温控后落盘。"""
    p, directory = ctx.params, ctx.directory
    signals, channels = ctx.devices["signals"], ctx.devices["channels"]
    limits = load_safety_limits(ctx.root)
    rate = float(ctx.state["actual_rates"][f"demod{p.demod0_idx}_Sa_s"])
    config = _daq_config(ctx, rate)
    points = p.scan_voltages()
    path = directory.raw / Path(run_format.GRID_FILE).name
    ctx.state["scan_acquisition"] = {
        "mode": "daq_duration", "duration_s": p.scan_point_duration_s, "actual_rate_Sa_s": rate,
        "repeats": p.n_repeat, "temperature_group_size": p.temp_group_size,
        "width_us": p.z_pulse_width_us, "x_bias_v": p.x_bias_v, "y_bias_v": p.y_bias_v,
        "x_offsets_mv": p.x_offsets_mv(), "y_offsets_mv": p.y_offsets_mv(),
        "demod0_idx": p.demod0_idx, "demod1_idx": p.demod1_idx,
        "signals": ["demod0.sample.x", "demod0.sample.y", "demod1.sample.x", "demod1.sample.y"]}
    directory.update_config(**ctx.state)
    total = len(points) * p.n_repeat
    done, started = 0, time.monotonic()
    with DeferredAcquisitionWrites(
            restore=lambda: set_temperature_switch(signals, True, channel=channels["temp"])) as pending:
        pending.add(_start_table, path)
        ctx.state["data_files"].append(run_format.GRID_FILE)
        pending.add(directory.update_config, **ctx.state)
        preparation.arm_pulse(ctx)
        for x_mv, y_mv, x_v, y_v in points:
            check_cancelled()
            validate_safety_limit(X_KEY, x_v, limits)
            validate_safety_limit(Y_KEY, y_v, limits)
            configure_fixed_dc_field(signals, channels["x"], X_KEY, x_v)
            configure_fixed_dc_field(signals, channels["y"], Y_KEY, y_v)
            for repeat in range(p.n_repeat):
                check_cancelled()
                if done % p.temp_group_size == 0:
                    set_temperature_switch(signals, False, channel=channels["temp"])
                preparation.sleep(p.system_settle_s)
                meta = _acquire_point(ctx, config, rate)
                temperature_c = _read_temperature_c(ctx)
                pending.add(_append_row, path, {
                    "width_us": f"{p.z_pulse_width_us:g}", "x_offset_mv": f"{x_mv:g}",
                    "y_offset_mv": f"{y_mv:g}", "x_bias_v": f"{x_v:.9g}", "y_bias_v": f"{y_v:.9g}",
                    "repeat": repeat,
                    "x2_v": f"{meta[f'x{p.demod1_idx}']:.9g}", "y2_v": f"{meta[f'y{p.demod1_idx}']:.9g}",
                    "x1_v": f"{meta[f'x{p.demod0_idx}']:.9g}", "y1_v": f"{meta[f'y{p.demod0_idx}']:.9g}",
                    "temp_c": "" if temperature_c is None else f"{temperature_c:.6g}",
                    "timestamp": datetime.now().astimezone().isoformat(timespec="seconds")})
                done += 1
                ctx.state["scan_reads_completed"] = done
                pending.add(directory.update_config, **ctx.state)
                if done % p.temp_group_size == 0 or done == total:
                    set_temperature_switch(signals, True, channel=channels["temp"])
                    preparation.sleep(p.temp_switch_on_lag_s)
                    pending.flush()
                _progress(done, total, started)
    ctx.state["scan_points_completed"] = len(points)
    ctx.state["scan_reads_total"] = total
    ctx.outputs["X 偏置"] = f"扫描完成：{len(p.x_offsets_mv())} × {len(p.y_offsets_mv())} 点"
    ctx.outputs["Y 偏置"] = f"中心 X {p.x_bias_v:+.6f} V、Y {p.y_bias_v:+.6f} V"
