"""A03 射频相位响应扫描：温控关闭窗口内读 MFLI 第 0 路解调器，恢复后落盘。

Z 通道的 ±π 双脉冲任意波只写一次并 arm 无限 burst；之后逐个射频相位按 DirectAW 顺序
改 burst 相位（关 burst → 写相位 → 开 burst → 开 output），每点读 ``N_REPEAT`` 次。
读数按 ``TEMP_GROUP_SIZE`` 分组：组首关闭温控、等 ``SYSTEM_SETTLE_S`` 再读，组末恢复温控、
等 ``TEMP_SWITCH_ON_LAG_S`` 后落盘——关温控窗口内不写任何文件，故障时先恢复温控再抢救数据。
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
from . import preparation, run_format
from .preparation import ARB_POINTS, plan, sleep
from .run_format import SOURCE_KEY, TEC_KEY

PHASE_RESPONSE_FIELDS = ("phase_deg", "repeat", "R_v", "theta_deg", "timestamp")
POINT_FIELDS = ("time_s", "R_v", "theta_deg")
MIN_POINT_SAMPLES = 4


def _daq_config(ctx, rate):
    """单次读数的 DAQ 配置：时长固定，网格按回读速率换算。"""
    p = ctx.params
    return DAQConfig(device=ctx.devices["hf2"].device_id, duration=p.scan_point_duration_s,
                     grid_cols=max(8, int(round(rate * p.scan_point_duration_s))), grid_rows=1,
                     grid_mode=2, signal_paths=["sample.r", "sample.theta"])


def _arm_width(ctx):
    """按固定脉宽上传一次双脉冲任意波并 arm 无限 burst；Z 波形相位固定 0。"""
    p = ctx.params
    signals, channels = ctx.devices["signals"], ctx.devices["channels"]
    limits, levels = load_safety_limits(ctx.root), plan(p)
    for value in (0.0, levels["pulse_max_v"], levels["pulse_min_v"]):
        validate_safety_limit(SOURCE_KEY, value, limits)
    width_us = float(p.z_pulse_width_us)
    wave, record = preparation.pulse_wave(p, width_us)
    ctx.state.setdefault("z_waveform", dict(
        tail_comp=bool(p.z_tail_comp_enabled), points=ARB_POINTS,
        **(dict(s_f=p.z_tail_s_f, tau_e_us=p.z_tail_tau_e_us, tau_c_us=p.z_tail_tau_c_us)
           if p.z_tail_comp_enabled else {}),
        widths={}))["widths"][f"{width_us:g}"] = record
    signals.set_output(False, channel=channels["z"])
    signals.setup_arbitrary(wave, p.y_rf_freq_hz, p.pulse_vpp, 0.0, output=False, channel=channels["z"])
    signals.set_burst_mode("INFinity", channel=channels["z"])
    signals.set_burst_ncycles(preparation.BURST_NCYCLES, channel=channels["z"])
    signals.set_burst_trigger_source("EXTernal", channel=channels["z"])
    signals.set_burst_trigger_slope("POSitive", channel=channels["z"])
    signals.set_burst_phase(0.0, channel=channels["z"])
    signals.set_burst_state(True, channel=channels["z"])
    signals.set_output(True, channel=channels["z"])
    # A02 收尾把 1200B 输出关掉了；不发 AON 时脉冲只到功放输入、到不了线圈。
    preparation.set_amp_output(ctx, True)
    tail = (f"、拖尾补偿尾 {record['tail_start_current_ma']:+.1f} mA / τc {record['tau_c_us']:.1f} µs"
            if record["tail_comp"] else "")
    ctx.outputs["Z 通道"] = (f"±π 双脉冲连续输出，周期 {p.pulse_period_us:g} µs、宽 {width_us:g} µs、"
                             f"峰值 {p.z_peak_current_ma:g} mA{tail}、外触发 {p.y_rf_freq_hz:g} Hz")


def _read_temperature_c(ctx):
    """TEC 可选：缺设备或读数异常时该点温度留空，不影响相位响应。"""
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


def _start_phase_table(path):
    """扫描开始时清空相位表并写表头；重跑不追加旧数据。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        csv.DictWriter(handle, fieldnames=list(PHASE_RESPONSE_FIELDS)).writeheader()


def _append_phase_row(path, row):
    """相位表逐行追加；首次写入时补表头。"""
    path = Path(path)
    new = not path.exists() or path.stat().st_size == 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(PHASE_RESPONSE_FIELDS))
        if new:
            writer.writeheader()
        writer.writerow(row)


def _progress(label, done, total, started):
    elapsed = time.monotonic() - started
    report_runtime_progress("A03", f"{label} {done}/{total}", 100.0 * min(done, total) / max(1, total),
                            estimated_remaining_seconds=(elapsed / done * (total - done)) if done else None)


def measure_phase_response(ctx):
    """逐个射频相位读 R/θ，写 raw/phase_response.csv；相位与分量分析在 B01。"""
    p, directory = ctx.params, ctx.directory
    signals, channels = ctx.devices["signals"], ctx.devices["channels"]
    phases = p.phase_angles_deg()
    rate = float(ctx.state["actual_rates"]["demod0_Sa_s"])
    config = _daq_config(ctx, rate)
    path = directory.raw / Path(run_format.PHASE_RESPONSE_FILE).name
    total, done, started = len(phases) * p.n_repeat, 0, time.monotonic()
    ctx.state["phase_response_acquisition"] = {
        "mode": "daq_duration", "duration_s": p.scan_point_duration_s, "actual_rate_Sa_s": rate,
        "phases_deg": phases, "repeats": p.n_repeat, "width_us": p.z_pulse_width_us,
        "temperature_group_size": p.temp_group_size,
        "demod_idx": p.demod0_idx, "signals": ["sample.r", "sample.theta"]}
    directory.update_config(**ctx.state)
    with DeferredAcquisitionWrites(
            restore=lambda: set_temperature_switch(signals, True, channel=channels["temp"])) as pending:
        # 表格先清空再逐行补入：全部写盘都排在恢复温控之后，取消时已完成读数仍会落盘。
        pending.add(_start_phase_table, path)
        ctx.state["data_files"].append(run_format.PHASE_RESPONSE_FILE)
        pending.add(directory.update_config, **ctx.state)
        _arm_width(ctx)
        index = 0
        for phase in phases:
            preparation.set_rf_phase(ctx, phase)
            for repeat in range(p.n_repeat):
                check_cancelled()
                if index % p.temp_group_size == 0:
                    set_temperature_switch(signals, False, channel=channels["temp"])
                sleep(p.system_settle_s)
                series, meta = _acquire_point(ctx, config, rate)
                pending.add(_write_point_series, directory.raw / run_format.point_file(phase, repeat), series)
                pending.add(_append_phase_row, path, {
                    "phase_deg": f"{phase:g}", "repeat": repeat,
                    "R_v": f"{meta['R_mean_v']:.9g}",
                    "theta_deg": f"{meta['theta_mean_deg']:.9g}",
                    "timestamp": datetime.now().astimezone().isoformat(timespec="seconds")})
                index += 1
                done += 1
                if index % p.temp_group_size == 0 or index == total:
                    set_temperature_switch(signals, True, channel=channels["temp"])
                    sleep(p.temp_switch_on_lag_s)
                    pending.flush()
                _progress("相位响应", done, total, started)
    ctx.state["phase_response"] = dict(enabled=True, phases_deg=phases, repeats=p.n_repeat,
                                       readings=done, width_us=p.z_pulse_width_us)
    directory.update_config(**ctx.state)
    ctx.outputs["相位响应"] = (f"{len(phases)} 个相位 × {p.n_repeat} 次读数，"
                              f"宽 {p.z_pulse_width_us:g} µs、峰值 {p.z_peak_current_ma:g} mA")
