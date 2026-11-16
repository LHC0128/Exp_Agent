"""A03 DG 0 V 零点、A04 可设定基线零点、A05 ±π 双脉冲角分布。

三段都是纯电学采集：不连接 TEC、不操作温控，因此不适用普通气室的关温控采集窗口。
零点长记把窗口均值追加到 CSV，默认每帧原始波形另存 NPZ；脉冲段按周期捕获短窗，
逐脉冲积分后把每发的 Q/θ 追加到 CSV。每段都可协作式取消，已写入的数据保留。
"""
from __future__ import annotations

import csv
import json
import time
from datetime import datetime
from pathlib import Path

import numpy as np

from ...common import load_safety_limits, validate_safety_limit
from ...experiment_runtime import check_cancelled, report_runtime_progress
from ...steps import arm_sync_output
from . import processing
from .preparation import (VERTICAL_HALF_DIVISIONS, SafetyAbort, baseline_scale, capture_config, current_limit,
                          nice_up, pulse_scale, set_amp_output, set_dc, sleep, zero_scale)
from .run_format import CURRENT_LIMIT_KEY, SOURCE_KEY

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
    """采一帧并返回 (time, sense_v, sync_v, result)；削顶时逐级放大量程。"""
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


def _csv_path(ctx, name):
    path = ctx.directory.root / "raw" / name
    path.parent.mkdir(exist_ok=True)
    return path


def _append_csv(path: Path, row: dict, fieldnames: list[str]):
    new = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        if new:
            writer.writeheader()
        writer.writerow(row)


def _progress(stage, label, index, total, started):
    elapsed = time.monotonic() - started
    report_runtime_progress(stage, f"{label} {index}/{total}", 100.0 * min(index, total) / max(1, total),
                            estimated_remaining_seconds=elapsed / index * (total - index) if index else None)


def _zero_frames(ctx, kind):
    p = ctx.params
    scale = zero_scale(p)
    means = []
    for index in range(p.zero_frames):
        check_cancelled()
        t, sense, sync, result = _acquire(ctx, capture_config(p, p.zero_window_s, scale))
        scale = ctx.current_scale or scale  # 首帧定档后沿用，避免每帧重新爬档
        _save_frame(ctx, f"{kind}_{index:02d}", t, sense, sync, _meta(result, {"kind": kind}))
        means.append(float(np.mean(sense)))
    return means


def _baseline_offset(ctx):
    """功放关/开两态、DG 0 V 的零偏；返回 (读数基线 V, 零偏电流 A)。"""
    p = ctx.params
    set_amp_output(ctx, False)
    set_dc(ctx, 0.0)
    ctx.devices["sg"].set_sync_state(True)
    ctx.outputs["Z 通道"] = "DC 0 V + ON，同步 ON"
    off = _zero_frames(ctx, "zero_off")
    set_amp_output(ctx, True)
    sleep(p.settle_s)
    on = _zero_frames(ctx, "zero_on")
    baseline, current = float(np.mean(off)), (float(np.mean(on)) - float(np.mean(off))) / p.sense_resistor_ohm
    ctx.state["zero_offset"] = dict(scope_baseline_v=baseline, offset_current_a=current,
                                    offset_std_a=float(np.std(on) / p.sense_resistor_ohm))
    return baseline, current


def _log_zero(ctx, mode, volts, *, stage, duration_s, interval_s, save_frames, raw_interval_s, scale):
    """按固定间隔记录窗口均值，逐点追加 CSV；原始帧按「每帧都存」或稀疏间隔保存。"""
    p = ctx.params
    path = _csv_path(ctx, f"zero_{mode}.csv")
    fields = ["timestamp", "elapsed_s", "mean_v", "std_v", "current_a"]
    ctx.state["data_files"].append(f"raw/{path.name}")
    total = max(1, int(duration_s / interval_s))
    started, next_raw, rows, saved = time.monotonic(), 0.0, 0, 0
    ctx.outputs["Z 通道"] = f"DC {volts:.6g} V + ON，零点记录 {mode}"
    while (elapsed := time.monotonic() - started) < duration_s:
        check_cancelled()
        loop_start = time.monotonic()
        t, sense, sync, result = _acquire(ctx, capture_config(p, p.zero_window_s, scale))
        scale = ctx.current_scale or scale  # 首帧定档后沿用，避免每帧重新爬档
        mean_v, std_v = float(np.mean(sense)), float(np.std(sense))
        _append_csv(path, dict(timestamp=datetime.now().astimezone().isoformat(timespec="seconds"),
                               elapsed_s=f"{elapsed:.6f}", mean_v=f"{mean_v:.9g}", std_v=f"{std_v:.9g}",
                               current_a=f"{mean_v / p.sense_resistor_ohm:.9g}"), fields)
        rows += 1
        sparse_save = raw_interval_s > 0 and elapsed >= next_raw
        if save_frames or sparse_save:
            _save_frame(ctx, f"zero_{mode}_{rows - 1:04d}", t, sense, sync,
                        _meta(result, {"kind": f"zero_{mode}", "elapsed_s": elapsed, "volts": volts}))
            saved += 1
            if sparse_save:
                next_raw += raw_interval_s
        _progress(stage, f"零点 {mode}", rows, total, started)
        sleep(max(0.0, interval_s - (time.monotonic() - loop_start)))
    ctx.state.setdefault("zero_logs", {})[mode] = dict(volts=volts, duration_s=duration_s,
                                                       interval_s=interval_s, rows=rows,
                                                       saved_frames=saved)


def measure_zero_dg0(ctx):
    """A03：DG 0 V 下功放零偏与长时间零点；不写 I–V 电压。"""
    p = ctx.params
    _baseline_offset(ctx)
    _log_zero(ctx, "dg0", 0.0, stage="A03", duration_s=p.zero_log_duration_s,
              interval_s=p.zero_log_interval_s, save_frames=p.zero_save_all_frames,
              raw_interval_s=p.zero_raw_save_interval_s, scale=ctx.current_scale or zero_scale(p))


def measure_baseline(ctx):
    """A04：目标电流经手动 I–V 换算写电压，再长时间记录残流。"""
    p = ctx.params
    set_amp_output(ctx, True)
    set_dc(ctx, 0.0)
    sleep(p.settle_s)
    volts = p.baseline_setpoint_voltage
    target_current = p.baseline_current_ma * 1e-3
    limits = load_safety_limits(ctx.root)
    validate_safety_limit(SOURCE_KEY, volts, limits)
    validate_safety_limit(CURRENT_LIMIT_KEY, target_current, limits)
    set_dc(ctx, volts)
    sleep(p.settle_s)
    t, sense, sync, result = _acquire(ctx, capture_config(p, p.zero_window_s, baseline_scale(p)))
    measured = float(np.mean(sense)) / p.sense_resistor_ohm
    _save_frame(ctx, "baseline_setup", t, sense, sync,
                _meta(result, {"kind": "baseline_setup", "target_current_a": target_current, "set_volts": volts,
                               "conversion_amps_per_volt": p.conversion_amps_per_volt,
                               "conversion_offset_ma": p.conversion_offset_ma,
                               "measured_current_a": measured}))
    ctx.state["baseline_setpoint_v"] = volts
    ctx.state["baseline_target_current_a"] = target_current
    ctx.state["baseline_measured_current_a"] = measured
    _log_zero(ctx, "baseline", volts, stage="A04", duration_s=p.zero_log_duration_s,
              interval_s=p.zero_log_interval_s, save_frames=p.zero_save_all_frames,
              raw_interval_s=p.zero_raw_save_interval_s, scale=ctx.current_scale or baseline_scale(p))


def _pulse_offset(ctx):
    """±π 脉冲的 DG 直流偏置：用脉冲自己的参数，不继承 A04 的基线目标。"""
    return float(ctx.params.pulse_baseline_voltage)


def measure_pulse_angles(ctx):
    """A05：连续 ±π 双脉冲，按间隔捕获短窗，逐脉冲积分得到 θ 分布。"""
    p, sg = ctx.params, ctx.devices["sg"]
    set_amp_output(ctx, True)
    sleep(p.settle_s)
    offset, vpp = _pulse_offset(ctx), p.pulse_vpp
    limits = load_safety_limits(ctx.root)
    for value in (offset + vpp / 2.0, offset - vpp / 2.0):
        validate_safety_limit(SOURCE_KEY, value, limits)
    wave = processing.pulse_waveform(p.pulse_period_us, p.pulse_width_us, p.pulse_edge_us, points=16384)
    sg.setup_arbitrary(wave, 1.0 / (p.pulse_period_us * 1e-6), vpp, offset, output=False)
    arm_sync_output(sg)
    ctx.outputs["Z 通道"] = (f"±π 双脉冲，周期 {p.pulse_period_us:g} µs、宽 {p.pulse_width_us:g} µs、"
                             f"峰值 {p.pulse_peak_current_ma:g} mA、偏置 {offset:.4g} V")
    path = _csv_path(ctx, "pulse_angles.csv")
    ctx.state["data_files"].append(f"raw/{path.name}")
    fields = ["timestamp", "elapsed_s", "sign", "q_a_s", "theta_pi", "i_base_a", "peak_a"]
    config = capture_config(p, p.pulse_capture_window_s, pulse_scale(p), sync=True)
    started, pulses_total, captures, period_s = time.monotonic(), 0, 0, p.pulse_period_us * 1e-6
    while (elapsed := time.monotonic() - started) < p.pulse_duration_s:
        check_cancelled()
        loop_start = time.monotonic()
        t, sense, sync, result = _acquire(ctx, config)
        stamp = datetime.now().astimezone().isoformat(timespec="seconds")
        # 每次捕获都留原始波形；角度分布由离线分析从这些帧重算。
        _save_frame(ctx, f"pulse_{captures:06d}", t, sense, sync,
                    _meta(result, {"kind": "pulse", "elapsed_s": elapsed, "timestamp": stamp,
                                   "vpp": vpp, "offset_v": offset}))
        captures += 1
        pulses = processing.segment_pulses(t, sense / p.sense_resistor_ohm, period_s=period_s,
                                           width_s=p.pulse_width_us * 1e-6,
                                           tail_s=p.pulse_tail_window_us * 1e-6)
        for pulse in pulses:
            theta = processing.pulse_angle_pi(pulse["q_a_s"], p.coil_constant_hz_per_ma)
            _append_csv(path, dict(timestamp=stamp, elapsed_s=f"{elapsed:.6f}",
                                   sign="+" if theta >= 0 else "-",
                                   q_a_s=f"{pulse['q_a_s']:.9g}", theta_pi=f"{theta:.9g}",
                                   i_base_a=f"{pulse['i_base_a']:.9g}", peak_a=f"{pulse['peak_a']:.9g}"), fields)
            pulses_total += 1
        _progress("A05", "脉冲捕获", int(elapsed), max(1, int(p.pulse_duration_s)), started)
        sleep(max(0.0, p.pulse_capture_interval_s - (time.monotonic() - loop_start)))
    ctx.state["pulse_capture_count"] = pulses_total
    ctx.state["pulse_captures"] = captures
