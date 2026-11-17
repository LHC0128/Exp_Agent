"""A03 相位粗扫与 A04 脉宽扫描：温控关闭窗口内读 MFLI 第 0 路解调器，恢复后落盘。

Z 通道的 ±π 双脉冲任意波在一个阶段内只写一次（A03 用预测 π 宽度写一次，A04 按每个扫描
宽度重写）并 arm 无限 burst；A03 只改射频 burst 相位，不改 Z 波形。每次读数在温控关闭窗口
内连续采 ``SCAN_POINT_DURATION_S``；A04 每 ``TEMP_GROUP_SIZE`` 个读数恢复一次温控并等待
``TEMP_SWITCH_ON_LAG_S`` 后才写盘——关闭温控的窗口内不写任何文件，故障时先恢复温控再抢救
已读数据。
"""
from __future__ import annotations

import csv
import math
import time
from datetime import datetime
from pathlib import Path

import numpy as np
from lockin_amplifier import DAQConfig, acquisition_metadata, daq, result_rate

from ...common import load_safety_limits, validate_safety_limit
from ...experiment_runtime import check_cancelled, report_runtime_progress
from ...steps import DeferredAcquisitionWrites, set_temperature_switch
from ..z_chain_stability import processing as pulse_processing
from . import phase_scan, preparation, run_format
from .preparation import ARB_POINTS, plan, sleep
from .run_format import SOURCE_KEY, TEC_KEY

SCAN_FIELDS = ("width_us", "repeat", "R_v", "theta_deg", "timestamp", "rf_vpp", "rf_freq_hz",
               "z_peak_ma", "temp_c")
PHASE_SCAN_FIELDS = ("phase_deg", "repeat", "R_v", "theta_deg", "timestamp")
POINT_FIELDS = ("time_s", "R_v", "theta_deg")
MIN_POINT_SAMPLES = 4


def _daq_config(ctx, rate):
    """单次读数的 DAQ 配置：时长固定，网格按回读速率换算。"""
    p = ctx.params
    return DAQConfig(device=ctx.devices["hf2"].device_id, duration=p.scan_point_duration_s,
                     grid_cols=max(8, int(round(rate * p.scan_point_duration_s))), grid_rows=1,
                     grid_mode=2, signal_paths=["sample.r", "sample.theta"])


def _arm_width(ctx, width_us):
    """按宽度上传一次双脉冲任意波并 arm 无限 burst；Z 波形相位固定 0。

    无限 burst 让 Z 通道与射频同时连续运行、共同进入稳态；``Time_sequence_2`` 只负责启动
    与同步。输出在配置完成后才打开。
    """
    p = ctx.params
    signals, channels = ctx.devices["signals"], ctx.devices["channels"]
    limits, levels = load_safety_limits(ctx.root), plan(p)
    for value in (0.0, levels["pulse_max_v"], levels["pulse_min_v"]):
        validate_safety_limit(SOURCE_KEY, value, limits)
    wave = pulse_processing.pulse_waveform(p.pulse_period_us, width_us, p.z_pulse_edge_us, points=ARB_POINTS)
    signals.set_output(False, channel=channels["z"])
    signals.setup_arbitrary(wave, p.y_rf_freq_hz, p.pulse_vpp, 0.0, output=False, channel=channels["z"])
    signals.set_burst_mode("INFinity", channel=channels["z"])
    signals.set_burst_ncycles(preparation.BURST_NCYCLES, channel=channels["z"])
    signals.set_burst_trigger_source("EXTernal", channel=channels["z"])
    signals.set_burst_trigger_slope("POSitive", channel=channels["z"])
    signals.set_burst_phase(0.0, channel=channels["z"])
    signals.set_burst_state(True, channel=channels["z"])
    signals.set_output(True, channel=channels["z"])
    ctx.outputs["Z 通道"] = (f"±π 双脉冲连续输出，周期 {p.pulse_period_us:g} µs、宽 {width_us:g} µs、"
                             f"峰值 {p.z_peak_current_ma:g} mA、外触发 {p.y_rf_freq_hz:g} Hz")


def _read_temperature_c(ctx):
    """TEC 可选：缺设备或读数异常时该点温度留空，不影响 R 判据。"""
    tec = ctx.devices.get("tec")
    if tec is None:
        return None
    try:
        value = float(tec.get_temperature(channel=int(ctx.mapping.get(TEC_KEY, {}).get("channel", 1))))
    except Exception:
        return None
    return value if math.isfinite(value) else None


def _acquire_point(ctx, config, actual_rate):
    """在温控关闭窗口内读一段 R/θ；返回 (series, meta)。"""
    p, mfli = ctx.params, ctx.devices["hf2"]
    results = daq.acquire_data(mfli, config=config, demod_idx=p.demod0_idx, actual_rate=actual_rate,
                               requested_rate=p.demod0_rate_sa_s, timeout=p.scan_point_duration_s + 10,
                               cancellation_check=check_cancelled, strict_cleanup=True)
    capture_rate = result_rate(results, actual_rate)
    by_name = {result.signal_name: np.asarray(result.values, dtype=float) for result in results}
    if set(by_name) != {"sample.r", "sample.theta"}:
        raise RuntimeError(f"MFLI 第 {p.demod0_idx} 路解调器未返回 R/θ：{sorted(by_name)}")
    size = min(values.size for values in by_name.values())
    r_values = by_name["sample.r"][:size]
    theta_values = by_name["sample.theta"][:size]
    axis = np.asarray(results[0].time, dtype=float)[:size]
    if axis.size != size:
        axis = np.arange(size) / capture_rate
    finite = np.isfinite(r_values) & np.isfinite(theta_values)
    if int(np.count_nonzero(finite)) < MIN_POINT_SAMPLES:
        raise RuntimeError(f"该点 R/θ 有效样本不足 {MIN_POINT_SAMPLES}：{int(np.count_nonzero(finite))}")
    r_values, theta_values, axis = r_values[finite], theta_values[finite], axis[finite]
    series = dict(time_s=axis.tolist(), R_v=r_values.tolist(), theta_deg=np.degrees(theta_values).tolist())
    # 相位按圆周平均，避免 ±180° 附近的跳变把均值拉回 0。
    theta_mean = math.atan2(float(np.mean(np.sin(theta_values))), float(np.mean(np.cos(theta_values))))
    meta = {"acquisition": acquisition_metadata(results), "actual_rate_Sa_s": capture_rate,
            "samples": int(r_values.size), "R_mean_v": float(np.mean(r_values)),
            "R_sem_within_v": (float(np.std(r_values, ddof=1) / math.sqrt(r_values.size))
                               if r_values.size > 1 else 0.0),
            "theta_mean_deg": math.degrees(theta_mean)}
    return series, meta


def _write_point_series(path, series):
    """一次性写出该点完整读数序列；失败保留其他点。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(POINT_FIELDS))
        writer.writeheader()
        for time_s, r_v, theta_deg in zip(series["time_s"], series["R_v"], series["theta_deg"]):
            writer.writerow({"time_s": f"{time_s:.9g}", "R_v": f"{r_v:.9g}", "theta_deg": f"{theta_deg:.9g}"})


def _append_scan_row(path, row):
    """扫描表逐点追加；首次写入时写表头。"""
    path = Path(path)
    new = not path.exists() or path.stat().st_size == 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(SCAN_FIELDS))
        if new:
            writer.writeheader()
        writer.writerow(row)


def _start_phase_table(path):
    """A03 开始时清空相位表并写表头；与逐行追加同批执行，重跑不追加旧数据。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        csv.DictWriter(handle, fieldnames=list(PHASE_SCAN_FIELDS)).writeheader()


def _append_phase_row(path, row):
    """相位表逐行追加；首次写入时补表头。"""
    path = Path(path)
    new = not path.exists() or path.stat().st_size == 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(PHASE_SCAN_FIELDS))
        if new:
            writer.writeheader()
        writer.writerow(row)


def _progress(stage, label, done, total, started):
    elapsed = time.monotonic() - started
    report_runtime_progress(stage, f"{label} {done}/{total}", 100.0 * min(done, total) / max(1, total),
                            estimated_remaining_seconds=(elapsed / done * (total - done)) if done else None)


def measure_phase_scan(ctx):
    """A03：逐角度改射频 burst 相位并读 R/θ，取 R 峰（含抛物线精化）供 A04 使用。

    每个角度按 DirectAW 校相的顺序改相位（关 burst → 写 burst 相位 → 开 burst → 开输出），
    再等 ``SYSTEM_SETTLE_S`` 后读 ``Y_RF_PHASE_REPEAT`` 次。Z 脉冲串在本阶段开始时用预测
    π 宽度写一次，之后不再改动。
    """
    p, directory = ctx.params, ctx.directory
    if not p.phase_check_enabled:
        ctx.state["phase_check"] = dict(enabled=False)
        ctx.state["phase_source"] = "parameter"
        directory.update_config(**ctx.state)
        ctx.outputs["相位粗扫"] = "PHASE_CHECK_ENABLED=false，未测量；A04 使用 Y_RF_PHASE_DEG"
        return

    signals, channels = ctx.devices["signals"], ctx.devices["channels"]
    angles = p.rf_phase_angles_deg()
    rate = float(ctx.state["actual_rates"]["demod0_Sa_s"])
    config = _daq_config(ctx, rate)
    width_us = p.phase_check_width_us()
    path = directory.raw / Path(run_format.PHASE_SCAN_FILE).name
    total, done, started = len(angles) * p.y_rf_phase_repeat, 0, time.monotonic()
    ctx.state["phase_scan_acquisition"] = {
        "mode": "daq_duration", "duration_s": p.scan_point_duration_s, "actual_rate_Sa_s": rate,
        "angles_deg": angles, "repeats": p.y_rf_phase_repeat, "width_us": width_us,
        "demod_idx": p.demod0_idx, "signals": ["sample.r", "sample.theta"], "phase_check_enabled": True}
    directory.update_config(**ctx.state)
    points = []
    with DeferredAcquisitionWrites(
            restore=lambda: set_temperature_switch(signals, True, channel=channels["temp"])) as pending:
        # 表格先清空再逐行补入：全部写盘都排在恢复温控之后，取消时已完成读数仍会落盘。
        pending.add(_start_phase_table, path)
        ctx.state["data_files"].append(run_format.PHASE_SCAN_FILE)
        pending.add(directory.update_config, **ctx.state)
        _arm_width(ctx, width_us)
        set_temperature_switch(signals, False, channel=channels["temp"])
        try:
            for angle in angles:
                preparation.set_rf_phase(ctx, angle)
                sleep(p.system_settle_s)
                values = []
                for repeat in range(p.y_rf_phase_repeat):
                    check_cancelled()
                    _, meta = _acquire_point(ctx, config, rate)
                    values.append(meta["R_mean_v"])
                    pending.add(_append_phase_row, path, {
                        "phase_deg": f"{angle:g}", "repeat": repeat,
                        "R_v": f"{meta['R_mean_v']:.9g}",
                        "theta_deg": f"{meta['theta_mean_deg']:.9g}",
                        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds")})
                    done += 1
                    _progress("A03", "相位粗扫", done, total, started)
                points.append(dict(phase_deg=float(angle), r_mean_v=float(np.mean(values)),
                                   r_sem_v=(float(np.std(values, ddof=1) / math.sqrt(len(values)))
                                            if len(values) > 1 else 0.0)))
        finally:
            set_temperature_switch(signals, True, channel=channels["temp"])
            sleep(p.temp_switch_on_lag_s)
        ctx.state["phase_check"] = dict(enabled=True, **phase_scan.choose_peak(points))
        ctx.state["phase_source"] = "stage"
        pending.add(directory.update_config, **ctx.state)
    phase_scan.plot_phase_scan(directory.results / Path(run_format.PHASE_PLOT_FILE).name,
                               points, ctx.state["phase_check"])
    ctx.state["data_files"].append(run_format.PHASE_PLOT_FILE)
    directory.update_config(**ctx.state)
    record = ctx.state["phase_check"]
    picked = record["refined_deg"] if record["refined_deg"] is not None else record["chosen_deg"]
    ctx.outputs["相位粗扫"] = (f"{len(angles)} 个角度，选 {record['chosen_deg']:g}°"
                              f"（精化 {picked:g}°）、R = {record['R_chosen']:.6g} V")


def measure_scan(ctx):
    """A04：逐点扫脉宽并在温控关闭窗口内读 MFLI R/θ，组末恢复温控后落盘。

    射频相位取 A03 的结果（有精化值优先），A03 未运行或关闭时回落到 ``Y_RF_PHASE_DEG``，
    并把来源记进摘要的 ``phase_source``。
    """
    p, directory = ctx.params, ctx.directory
    signals, channels = ctx.devices["signals"], ctx.devices["channels"]
    widths = p.widths_us()
    record = ctx.state.get("phase_check") or {}
    if record.get("enabled") and record.get("chosen_deg") is not None:
        refined = record.get("refined_deg")
        phase_deg = float(refined if refined is not None else record["chosen_deg"])
        phase_source = "stage"
    else:
        phase_deg, phase_source = float(p.y_rf_phase_deg), "parameter"
        ctx.state.setdefault("phase_check", dict(enabled=False))
    ctx.state["phase_source"] = phase_source
    rate = float(ctx.state["actual_rates"]["demod0_Sa_s"])
    config = _daq_config(ctx, rate)
    ctx.state["scan_acquisition"] = {
        "mode": "daq_duration", "duration_s": p.scan_point_duration_s, "actual_rate_Sa_s": rate,
        "repeats": p.n_repeat, "temperature_group_size": p.temp_group_size, "widths_us": widths,
        "demod_idx": p.demod0_idx, "signals": ["sample.r", "sample.theta"],
        "phase_source": phase_source}
    directory.update_config(**ctx.state)
    preparation.set_rf_phase(ctx, phase_deg)

    scan_path = directory.raw / "scan.csv"
    ctx.state["data_files"].append(run_format.SCAN_FILE)
    total, index, started = len(widths) * p.n_repeat, 0, time.monotonic()
    with DeferredAcquisitionWrites(
            restore=lambda: set_temperature_switch(signals, True, channel=channels["temp"])) as pending:
        for width in widths:
            _arm_width(ctx, width)
            for repeat in range(p.n_repeat):
                check_cancelled()
                if index % p.temp_group_size == 0:
                    set_temperature_switch(signals, False, channel=channels["temp"])
                sleep(p.system_settle_s)
                series, meta = _acquire_point(ctx, config, rate)
                temperature_c = _read_temperature_c(ctx)
                point_name = run_format.point_file(width, repeat)
                ctx.state["data_files"].append(point_name)
                ctx.state["scan_acquisition"].update(meta)
                ctx.state["scan_points_completed"] = index + 1
                pending.add(_write_point_series, directory.raw / Path(point_name).name, series)
                pending.add(_append_scan_row, scan_path, {
                    "width_us": f"{width:g}", "repeat": repeat, "R_v": f"{meta['R_mean_v']:.9g}",
                    "theta_deg": f"{meta['theta_mean_deg']:.9g}",
                    "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "rf_vpp": f"{p.y_rf_vpp:g}", "rf_freq_hz": f"{p.y_rf_freq_hz:g}",
                    "z_peak_ma": f"{p.z_peak_current_ma:g}",
                    "temp_c": "" if temperature_c is None else f"{temperature_c:.6g}"})
                index += 1
                pending.add(directory.update_config, **ctx.state)
                if index % p.temp_group_size == 0 or index == total:
                    set_temperature_switch(signals, True, channel=channels["temp"])
                    sleep(p.temp_switch_on_lag_s)
                    pending.flush()
                _progress("A04", "脉宽扫描", index, total, started)
    ctx.state["scan_widths"] = widths
    ctx.state["scan_repeats"] = p.n_repeat
    ctx.state["scan_points_completed"] = total
    ctx.outputs["Z 通道"] = f"扫描完成：{len(widths)} 个宽度 × {p.n_repeat} 次"
