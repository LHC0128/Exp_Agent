"""逐点改脉宽、在温控关闭窗口内读 MFLI 第 0 路解调器，组末恢复温控后落盘。

每点先按宽度上传 ±π 双脉冲任意波、按 ``Z_PULSE_PHASE_DEG``（或相位自检选定的相位）循环
移位，再 arm 无限 burst（外触发），使 Z 与射频共同进入稳态；随后关闭温控、等待
``SYSTEM_SETTLE_S``、连续采集 ``SCAN_POINT_DURATION_S`` 的 R/θ；每 ``TEMP_GROUP_SIZE``
个读数恢复一次温控并等待 ``TEMP_SWITCH_ON_LAG_S``，之后才写盘——关闭温控的窗口内不写
任何文件，故障时先恢复温控再抢救已读数据。
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
from . import run_format
from .models import phase_effective_deg
from .preparation import ARB_POINTS, plan, sleep
from .run_format import SOURCE_KEY, TEC_KEY

SCAN_FIELDS = ("width_us", "repeat", "R_v", "theta_deg", "timestamp", "rf_vpp", "rf_freq_hz",
               "z_peak_ma", "temp_c")
PHASE_CHECK_FIELDS = ("phase_deg", "repeat", "R_v", "theta_deg", "timestamp")
POINT_FIELDS = ("time_s", "R_v", "theta_deg")
MIN_POINT_SAMPLES = 4
# 相位自检每个候选相位的读数次数；只为比较两档幅度，不参与判据拟合。
PHASE_CHECK_REPEATS = 3
# 无限 burst 下 NCYCles 被仪器忽略；写上限值，照 DirectAW 校相档的 Z RF 做法。
Z_BURST_NCYCLES = 50000


def _arm_width(ctx, width_us, phase_deg=None):
    """按宽度上传双脉冲任意波、循环移位到指定相位并 arm 无限 burst。

    无限 burst 让 Z 通道与射频同时连续运行、共同进入稳态；``Time_sequence_2`` 只负责启动
    与同步。相位偏移在任意波数组上循环移位实现，``set_burst_phase`` 保持 0°，两种机制不叠加。
    输出在配置完成后才打开。
    """
    p = ctx.params
    signals, channels = ctx.devices["signals"], ctx.devices["channels"]
    limits, levels = load_safety_limits(ctx.root), plan(p)
    for value in (0.0, levels["pulse_max_v"], levels["pulse_min_v"]):
        validate_safety_limit(SOURCE_KEY, value, limits)
    phase = p.pulse_phase_effective_deg if phase_deg is None else phase_effective_deg(phase_deg)
    wave = pulse_processing.pulse_waveform(p.pulse_period_us, width_us, p.z_pulse_edge_us, points=ARB_POINTS)
    if shift := p.pulse_phase_shift_points(phase):
        wave = np.roll(wave, shift)
    signals.set_output(False, channel=channels["z"])
    signals.setup_arbitrary(wave, p.y_rf_freq_hz, p.pulse_vpp, 0.0, output=False, channel=channels["z"])
    signals.set_burst_mode("INFinity", channel=channels["z"])
    signals.set_burst_ncycles(Z_BURST_NCYCLES, channel=channels["z"])
    signals.set_burst_trigger_source("EXTernal", channel=channels["z"])
    signals.set_burst_trigger_slope("POSitive", channel=channels["z"])
    signals.set_burst_phase(0.0, channel=channels["z"])
    signals.set_burst_state(True, channel=channels["z"])
    signals.set_output(True, channel=channels["z"])
    offset_us = p.pulse_phase_offset_us(phase)
    ctx.state["pulse_phase_deg"] = phase
    ctx.state["pulse_phase_offset_us"] = offset_us
    ctx.outputs["Z 通道"] = (f"±π 双脉冲连续输出，周期 {p.pulse_period_us:g} µs、宽 {width_us:g} µs、"
                             f"峰值 {p.z_peak_current_ma:g} mA、相位 {phase:g}°（{offset_us:g} µs）、"
                             f"外触发 {p.y_rf_freq_hz:g} Hz")


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


def _append_phase_row(path, row):
    """相位自检逐行追加；首次写入时写表头。"""
    path = Path(path)
    new = not path.exists() or path.stat().st_size == 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(PHASE_CHECK_FIELDS))
        if new:
            writer.writeheader()
        writer.writerow(row)


def _phase_check(ctx, config, rate, pending):
    """扫描前测当前相位与其 +90° 两点的 R，返回本轮扫描用的相位。

    两次测量沿用单点采集与温控门控：先关温控、按候选相位逐次 arm 并读
    ``PHASE_CHECK_REPEATS`` 次，取平均 R 较大的一档；恢复温控后才把读数交给写盘队列。
    """
    p, directory = ctx.params, ctx.directory
    signals, channels = ctx.devices["signals"], ctx.devices["channels"]
    width_us, candidates = p.phase_check_width_us(), p.phase_check_candidates_deg()
    path = directory.raw / Path(run_format.PHASE_CHECK_FILE).name
    rows, means = [], []
    set_temperature_switch(signals, False, channel=channels["temp"])
    try:
        for phase in candidates:
            _arm_width(ctx, width_us, phase)
            sleep(p.system_settle_s)
            values = []
            for repeat in range(PHASE_CHECK_REPEATS):
                check_cancelled()
                _, meta = _acquire_point(ctx, config, rate)
                values.append(meta["R_mean_v"])
                rows.append({"phase_deg": f"{phase:g}", "repeat": repeat,
                             "R_v": f"{meta['R_mean_v']:.9g}", "theta_deg": f"{meta['theta_mean_deg']:.9g}",
                             "timestamp": datetime.now().astimezone().isoformat(timespec="seconds")})
            means.append(float(np.mean(values)))
    finally:
        set_temperature_switch(signals, True, channel=channels["temp"])
        sleep(p.temp_switch_on_lag_s)
    chosen = candidates[int(np.argmax(means))]
    ctx.state["data_files"].append(run_format.PHASE_CHECK_FILE)
    for row in rows:
        pending.add(_append_phase_row, path, row)
    ctx.state["phase_check"] = dict(enabled=True, measured_deg=candidates[0], chosen_deg=chosen,
                                    R_measured=means[0], R_chosen=float(max(means)))
    pending.add(directory.update_config, **ctx.state)
    ctx.outputs["相位自检"] = (f"φ={candidates[0]:g}° R={means[0]:.6g} V，φ={candidates[1]:g}° "
                               f"R={means[1]:.6g} V → 选 {chosen:g}°")
    return chosen


def _progress(done, total, started):
    elapsed = time.monotonic() - started
    report_runtime_progress("A03", f"脉宽扫描 {done}/{total}", 100.0 * min(done, total) / max(1, total),
                            estimated_remaining_seconds=(elapsed / done * (total - done)) if done else None)


def measure_scan(ctx):
    """逐点扫脉宽并在温控关闭窗口内读 MFLI R/θ，组末恢复温控后落盘。"""
    p, directory = ctx.params, ctx.directory
    signals, channels = ctx.devices["signals"], ctx.devices["channels"]
    widths = p.widths_us()
    rate = float(ctx.state["actual_rates"]["demod0_Sa_s"])
    config = DAQConfig(device=ctx.devices["hf2"].device_id, duration=p.scan_point_duration_s,
                       grid_cols=max(8, int(round(rate * p.scan_point_duration_s))), grid_rows=1,
                       grid_mode=2, signal_paths=["sample.r", "sample.theta"])
    ctx.state["scan_acquisition"] = {
        "mode": "daq_duration", "duration_s": p.scan_point_duration_s, "actual_rate_Sa_s": rate,
        "repeats": p.n_repeat, "temperature_group_size": p.temp_group_size, "widths_us": widths,
        "demod_idx": p.demod0_idx, "signals": ["sample.r", "sample.theta"],
        "phase_check_enabled": p.phase_check_enabled}
    directory.update_config(**ctx.state)

    scan_path = directory.raw / "scan.csv"
    ctx.state["data_files"].append(run_format.SCAN_FILE)
    total, index, started = len(widths) * p.n_repeat, 0, time.monotonic()
    with DeferredAcquisitionWrites(
            restore=lambda: set_temperature_switch(signals, True, channel=channels["temp"])) as pending:
        if p.phase_check_enabled:
            phase_deg = _phase_check(ctx, config, rate, pending)
        else:
            # 不做自检时直接用参数相位；摘要按关闭记录，扫描仍按脉冲相位配置。
            phase_deg = p.pulse_phase_effective_deg
            ctx.state["phase_check"] = dict(enabled=False)
            ctx.state["pulse_phase_deg"] = phase_deg
            ctx.state["pulse_phase_offset_us"] = p.pulse_phase_offset_us(phase_deg)
        for width in widths:
            _arm_width(ctx, width, phase_deg)
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
                _progress(index, total, started)
    ctx.state["scan_widths"] = widths
    ctx.state["scan_repeats"] = p.n_repeat
    ctx.state["scan_points_completed"] = total
    ctx.outputs["Z 通道"] = f"扫描完成：{len(widths)} 个宽度 × {p.n_repeat} 次"
