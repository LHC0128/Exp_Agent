"""固定峰值、扫单脉冲宽度：每次捕获算逐发 θ，结果写入 raw/scan.csv。

脉冲波形与分段积分沿用 z 链路实验的纯函数实现（``z_chain_stability.processing``），
避免同一套「升余弦 + 尾部积分」在两处各写一份而口径漂移；这里只保留本实验的扫描、
逐帧存盘与安全守卫。
"""
from __future__ import annotations

import csv
import json
import time
from datetime import datetime
from pathlib import Path

import numpy as np

from ...common import load_safety_limits
from ...experiment_runtime import check_cancelled, report_runtime_progress
from ...steps import arm_sync_output
from ..z_chain_stability import processing as pulse_processing
from .preparation import (VERTICAL_HALF_DIVISIONS, SafetyAbort, capture_config, current_limit, nice_up,
                          plan, pulse_scale, set_amp_output, sleep)
from .run_format import CURRENT_LIMIT_KEY, SOURCE_KEY
from ...common import validate_safety_limit

SYNC_MIN_PEAK_V = 1.0
SYNC_ARM_ATTEMPTS = 4


def _guard_current(ctx, current_a):
    low, high = current_limit(load_safety_limits(ctx.root))
    peak_high, peak_low = float(np.max(current_a)), float(np.min(current_a))
    if peak_high > high or peak_low < low:
        raise SafetyAbort(f"实测线圈电流 [{peak_low:.4g}, {peak_high:.4g}] A 越过限值 [{low}, {high}] A")


class MissingSync(SafetyAbort):
    """NORMal 帧取到的同步峰峰值不足；整帧丢弃，重新锁存同步后重采。"""


def _acquire(ctx, config):
    """采一帧并返回 (time, sense_v, sync_v, result)；削顶时按 1-2-5 逐档放大量程。"""
    p = ctx.params
    for attempt in range(SYNC_ARM_ATTEMPTS):
        try:
            for _ in range(12):
                check_cancelled()
                results = {result.channel: result for result in ctx.devices["acquirer"].acquire_all(config)}
                sense = results[p.scope_current_channel]
                sync = results.get(p.scope_sync_channel)
                channel = next(c for c in config.channels if c.number == p.scope_current_channel)
                _guard_current(ctx, sense.voltage / p.sense_resistor_ohm)
                if config.trigger.mode == "NORMal" and sync is not None:
                    sync_peak = float(np.max(sync.voltage) - np.min(sync.voltage))
                    if sync_peak < SYNC_MIN_PEAK_V:
                        raise MissingSync(f"同步信号缺失（峰峰值 {sync_peak:.3f} V < {SYNC_MIN_PEAK_V} V）")
                if float(np.max(np.abs(sense.voltage - channel.offset))) < 0.98 * VERTICAL_HALF_DIVISIONS * channel.scale:
                    ctx.current_scale = channel.scale
                    return sense.time, sense.voltage, (sync.voltage if sync is not None else None), sense
                if channel.scale >= 10.0:
                    raise SafetyAbort("采样电阻电压在最大量程仍削顶")
                channel.scale = min(10.0, nice_up(channel.scale * 2.0))
            raise SafetyAbort("量程调整后仍削顶")
        except MissingSync:
            if attempt == SYNC_ARM_ATTEMPTS - 1:
                raise
            arm_sync_output(ctx.devices["sg"])


def _meta(result, extra):
    return {**extra, "scope": result.config_snapshot, "preamble": result.preamble_dict}


def _save_frame(ctx, name, t, sense, sync, meta):
    path = ctx.directory.root / "raw" / f"{name}.npz"
    path.parent.mkdir(exist_ok=True)
    payload = dict(time=t, sense_v=sense, meta=json.dumps(meta, default=str, ensure_ascii=False))
    if sync is not None:
        payload["sync_v"] = sync
    np.savez(path, **payload)
    ctx.state["data_files"].append(f"raw/{path.name}")


def _append_csv(path: Path, row: dict, fieldnames: list[str]):
    new = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        if new:
            writer.writeheader()
        writer.writerow(row)


def _progress(label, done, total, started):
    elapsed = time.monotonic() - started
    report_runtime_progress("A03", f"{label} {done}/{total}", 100.0 * min(done, total) / max(1, total),
                            estimated_remaining_seconds=elapsed / done * (total - done) if done else None)


def measure_scan(ctx):
    """逐个宽度打脉冲并捕获，逐发记录积分与角度。"""
    p, sg = ctx.params, ctx.devices["sg"]
    levels = plan(p)
    limits = load_safety_limits(ctx.root)
    for value in (levels["pulse_max_v"], levels["pulse_min_v"]):
        validate_safety_limit(SOURCE_KEY, value, limits)
    set_amp_output(ctx, True)
    sleep(p.settle_s)
    path = _csv_path(ctx, "scan.csv")
    path.parent.mkdir(exist_ok=True)
    ctx.state["data_files"].append(f"raw/{path.name}")
    fields = ["width_us", "shot", "sign", "q_a_s", "theta_pi", "i_base_a", "peak_a", "elapsed_s"]
    period_s = p.pulse_period_us * 1e-6
    total = len(p.pulse_widths_us) * p.shots_per_width
    started, done, shots = time.monotonic(), 0, 0
    synced = False
    for width in p.pulse_widths_us:
        wave = pulse_processing.pulse_waveform(p.pulse_period_us, width, p.pulse_edge_us, points=16384)
        sg.setup_arbitrary(wave, 1.0 / period_s, p.pulse_vpp, p.pulse_baseline_v, output=False)
        if not synced:
            arm_sync_output(sg)
            synced = True
        sg.set_output(True)
        ctx.outputs["Z 通道"] = (f"±π 双脉冲，周期 {p.pulse_period_us:g} µs、宽 {width:g} µs、"
                                 f"峰值 {p.pulse_peak_current_ma:g} mA、偏置 {p.pulse_baseline_v:g} V")
        config = capture_config(p, p.capture_window_s, pulse_scale(p), sync=True)
        for shot in range(p.shots_per_width):
            check_cancelled()
            loop_start = time.monotonic()
            t, sense, sync, result = _acquire(ctx, config)
            stamp = datetime.now().astimezone().isoformat(timespec="seconds")
            elapsed = time.monotonic() - started
            _save_frame(ctx, f"scan_{int(round(width * 100)):05d}_{shot:02d}", t, sense, sync,
                        _meta(result, {"kind": "scan", "width_us": width, "shot": shot,
                                       "elapsed_s": elapsed, "timestamp": stamp}))
            pulses = pulse_processing.segment_pulses(
                t, sense / p.sense_resistor_ohm, period_s=period_s, width_s=width * 1e-6,
                tail_s=p.pulse_tail_window_us * 1e-6)
            for pulse in pulses:
                theta = pulse_processing.pulse_angle_pi(pulse["q_a_s"], p.coil_constant_hz_per_ma)
                _append_csv(path, dict(width_us=f"{width:g}", shot=shot,
                                       sign="+" if theta >= 0 else "-",
                                       q_a_s=f"{pulse['q_a_s']:.9g}", theta_pi=f"{theta:.9g}",
                                       i_base_a=f"{pulse['i_base_a']:.9g}", peak_a=f"{pulse['peak_a']:.9g}",
                                       elapsed_s=f"{elapsed:.6f}"), fields)
                shots += 1
            done += 1
            _progress("扫描", done, total, started)
            sleep(max(0.0, p.capture_interval_s - (time.monotonic() - loop_start)))
    ctx.state["scan_shots"] = shots
    ctx.state["scan_widths"] = list(p.pulse_widths_us)


def _csv_path(ctx, name):
    path = ctx.directory.root / "raw" / name
    path.parent.mkdir(exist_ok=True)
    return path
