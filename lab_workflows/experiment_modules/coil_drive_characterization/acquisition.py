"""零偏/增益、正弦扫频、脉冲阶跃三段测量；每帧采集后立即保存到 raw/。

纯电学工具，不连接 TEC、不操作温控开关，因此不适用普通气室的关温控采集窗口。
"""
from __future__ import annotations

import json
import time

import numpy as np

from ...common import load_safety_limits, validate_safety_limit
from ...experiment_runtime import check_cancelled, report_runtime_progress
from . import processing
from .preparation import (VERTICAL_HALF_DIVISIONS, SafetyAbort, capture_config, current_limit, plan,
                          scale_for, set_dc, sleep)
from .run_format import SOURCE_KEY

# DG4000 同步为 0→3.5 V TTL，真机（DG4162 固件 00.01.14）只在「输出 OFF→ON 边沿、且边沿时
# 同步已使能」的情况下让同步发生器起振：输出已开启时写同步使能只更新状态位，CH3 无输出；
# APPLy 重绘波形既不产生也不破坏已锁存的同步。NORMal 触发帧的同帧同步峰峰值低于此值即判
# 同步缺失。阈值远低于 3.5 V、远高于噪声。
SYNC_MIN_PEAK_V = 1.0


def arm_sync(sg):
    """重新锁存 DG4000 同步：先使能同步，再用一次输出 OFF→ON 边沿起振。

    真机受控实验：APPLy 后直接写同步使能，CH3 连续 3 次无同步；补上输出边沿后 3 次全部
    出现 3.5 V TTL，零延时与 0.3 s 间隔等效，脉冲与 ±0.4 V 正弦都成立。波形在关输出期间
    保持不变，不会把线圈驱动切换成 DC。
    """
    sg.set_output(False)
    sg.set_sync_state(True)
    sg.set_output(True)


def _guard_current(ctx, current_a):
    low, high = current_limit(load_safety_limits(ctx.root))
    peak_high, peak_low = float(np.max(current_a)), float(np.min(current_a))
    if peak_high > high or peak_low < low:
        raise SafetyAbort(f"实测线圈电流 [{peak_low:.4g}, {peak_high:.4g}] A 越过限值 [{low}, {high}] A")


def capture(ctx, name, config, meta):
    """采集一帧并保存；削顶时逐级放大电流通道量程直到最大档，削顶读数看不到真实电流。返回 (t, sense_v)。"""
    p = ctx.params
    for _ in range(12):
        check_cancelled()
        results = {result.channel: result for result in ctx.devices["acquirer"].acquire_all(config)}
        sense, sync = results[p.scope_current_channel], results[p.scope_sync_channel]
        channel = next(c for c in config.channels if c.number == p.scope_current_channel)
        _guard_current(ctx, sense.voltage / p.sense_resistor_ohm)
        # NORMal 触发依赖同步信号；DG4000 同步仅在非 DC 波形下输出，状态位不可靠，
        # 用同帧实测同步幅度判定。缺失即中止，避免把无同步触发的帧当有效数据。
        if config.trigger.mode == "NORMal":
            sync_peak = float(np.max(sync.voltage) - np.min(sync.voltage))
            if sync_peak < SYNC_MIN_PEAK_V:
                raise SafetyAbort(f"{name}: 同步信号缺失（同帧峰峰值 {sync_peak:.3f} V < {SYNC_MIN_PEAK_V} V）；"
                                  "DG4000 同步仅在非 DC 波形下输出，请确认信号源波形与同步接线")
        if float(np.max(np.abs(sense.voltage - channel.offset))) < 0.98 * VERTICAL_HALF_DIVISIONS * channel.scale:
            break
        if channel.scale >= 10.0:
            raise SafetyAbort(f"{name}: 采样电阻电压在最大量程仍削顶")
        channel.scale = scale_for(channel.scale * VERTICAL_HALF_DIVISIONS * 2.0)
        ctx.current_scale = channel.scale
    else:
        raise SafetyAbort(f"{name}: 量程调整后仍削顶")
    path = ctx.directory.root / "raw" / f"{name}.npz"
    path.parent.mkdir(exist_ok=True)
    np.savez(path, time=sense.time, sense_v=sense.voltage, sync_v=sync.voltage,
             meta=json.dumps({**meta, "scope": sense.config_snapshot}, default=str, ensure_ascii=False))
    ctx.state["data_files"].append(f"raw/{path.name}")
    return sense.time, sense.voltage


def _progress(stage, label, index, total, started):
    elapsed = time.monotonic() - started
    report_runtime_progress(stage, f"{label} {index}/{total}", 100.0 * index / total,
                            estimated_remaining_seconds=elapsed / index * (total - index) if index else None)


def measure_zero_and_gain(ctx):
    """功放关/开两态、信号源 DC 0 V 下的平均采样电压之差为零偏；再用小直流核对增益。"""
    p, amp = ctx.params, ctx.devices["amp"]
    amp.set_output(False)
    ctx.outputs["功放"] = "OFF（已发送 AOF，未回读）"
    set_dc(ctx, 0.0)
    ctx.devices["sg"].set_sync_state(True)
    ctx.outputs["Z 通道"] = "DC 0 V + ON，同步 ON"
    config = capture_config(p, p.zero_window_s, 0.01, trigger_mode="AUTO")
    means = {"zero_off": [], "zero_on": []}
    noise = []
    total, started = 2 * p.zero_frames + 1, time.monotonic()
    for kind in ("zero_off", "zero_on"):
        if kind == "zero_on":
            amp.set_output(True)
            ctx.outputs["功放"] = "ON（已发送 AON，未回读）"
            sleep(p.settle_s)
        for index in range(p.zero_frames):
            _, sense = capture(ctx, f"{kind}_{index:02d}", config, {"kind": kind})
            means[kind].append(float(np.mean(sense)))
            if kind == "zero_on":
                noise.append(float(np.std(sense)))
            _progress("A03", "零偏帧", len(means["zero_off"]) + len(means["zero_on"]), total, started)
    baseline, on = float(np.mean(means["zero_off"])), float(np.mean(means["zero_on"]))
    offset_a = (on - baseline) / p.sense_resistor_ohm
    drive = plan(p)["gain_check_drive_v"]
    set_dc(ctx, drive)
    sleep(p.settle_s)
    gain_config = capture_config(p, p.zero_window_s, scale_for(p.gain_check_current_ma * 1e-3 * p.sense_resistor_ohm),
                                 trigger_mode="AUTO")
    try:
        _, sense = capture(ctx, "gain_check", gain_config, {"kind": "gain_check", "drive_v": drive})
    finally:
        set_dc(ctx, 0.0)
    measured = ((float(np.mean(sense)) - baseline) / p.sense_resistor_ohm - offset_a) / drive
    ctx.state["zero_offset"] = dict(scope_baseline_v=baseline, offset_current_a=offset_a,
        offset_current_frame_std_a=float(np.std(means["zero_on"]) / p.sense_resistor_ohm),
        noise_rms_a=float(np.mean(noise) / p.sense_resistor_ohm))
    ctx.state["gain_check"] = dict(drive_v=drive, measured_amps_per_volt=measured,
                                   predicted_amps_per_volt=p.amps_per_drive_volt,
                                   ratio=measured / p.amps_per_drive_volt)
    _progress("A03", "增益核对", total, total, started)
    if measured > p.gain_tolerance * p.amps_per_drive_volt:
        raise SafetyAbort(f"实测 I/V={measured:.4g} A/V 超过按 {p.amplifier_gain_db} dB 预测的 "
                          f"{p.amps_per_drive_volt:.4g} A/V 的 {p.gain_tolerance} 倍；核对功放面板增益")


def measure_transfer(ctx):
    p, sg = ctx.params, ctx.devices["sg"]
    drive = plan(p)["sine_drive_peak_v"]
    limits = load_safety_limits(ctx.root)
    validate_safety_limit(SOURCE_KEY, drive, limits)
    validate_safety_limit(SOURCE_KEY, -drive, limits)
    ctx.current_scale = scale_for(p.sine_peak_current_ma * 1e-3 * p.sense_resistor_ohm)
    frequencies, started = p.frequencies(), time.monotonic()
    points = []
    try:
        for index, frequency in enumerate(frequencies):
            check_cancelled()
            sg.setup_sine(frequency, 2.0 * drive, 0.0)
            arm_sync(sg)  # APPLy 后必须重新锁存同步，否则 NORMal 触发永远等不到同步沿
            ctx.outputs["Z 通道"] = f"正弦 {frequency:.6g} Hz，{2 * drive:.4g} Vpp"
            sleep(p.settle_s)
            config = capture_config(p, p.sine_cycles / frequency, ctx.current_scale)
            t, sense = capture(ctx, f"sine_{index:02d}", config,
                               {"kind": "sine", "frequency_hz": frequency, "drive_peak_v": drive})
            point = processing.transfer_point(frequency, drive, t, sense, p.sense_resistor_ohm)
            points.append(point)
            _progress("A04", f"{frequency:.4g} Hz |I/V|={point.admittance_a_per_v * 1e3:.4g} mA/V",
                      index + 1, len(frequencies), started)
    finally:
        set_dc(ctx, 0.0)
        ctx.outputs["Z 通道"] = "DC 0 V + ON，同步 ON"
    ctx.state["transfer_preview"] = dict(points=len(points), bandwidth_3db_hz=processing.bandwidth_3db(points)
                                         if len(points) > 1 else None)


def measure_steps(ctx):
    p, sg = ctx.params, ctx.devices["sg"]
    drive = plan(p)["pulse_drive_v"]
    validate_safety_limit(SOURCE_KEY, drive, load_safety_limits(ctx.root))
    ctx.current_scale = scale_for(p.pulse_current_ma * 1e-3 * p.sense_resistor_ohm)
    widths, started = p.pulse_widths_s, time.monotonic()
    try:
        for index, width in enumerate(widths):
            check_cancelled()
            sg.setup_pulse(p.pulse_repetition_hz, drive, drive / 2.0, width=width)
            arm_sync(sg)  # APPLy 后必须重新锁存同步，否则 NORMal 触发永远等不到同步沿
            ctx.outputs["Z 通道"] = f"脉冲 0→{drive:.4g} V，宽 {width * 1e6:g} µs"
            sleep(p.settle_s)
            config = capture_config(p, p.pulse_window_multiple * width, ctx.current_scale)
            capture(ctx, f"pulse_{index:02d}", config, {"kind": "pulse", "width_s": width, "drive_v": drive})
            _progress("A05", f"脉宽 {width * 1e6:g} µs", index + 1, len(widths), started)
    finally:
        set_dc(ctx, 0.0)
        ctx.outputs["Z 通道"] = "DC 0 V + ON，同步 ON"
