"""零偏/直流核对、引导与主扫描；每个原子采集窗口先恢复温控再写盘。

纯电学核对不连接温控窗口；原子频点按普通气室六步时序门控，文件只在恢复温控后保存。
"""
from __future__ import annotations

import json
import time

import numpy as np

from ...common import validate_safety_limit
from ...experiment_runtime import check_cancelled
from ...steps import set_temperature_switch
from ...steps.acquisition_window import DeferredAcquisitionWrites, acquisition_window
from ...steps.coil_drive_chain import (VERTICAL_HALF_DIVISIONS, SafetyAbort, capture_config,
                                       scale_for, set_amp_output, set_dc, sleep)
from ..mx_y_rf_sensitivity.acquisition import acquire_r, summarize_r
from . import scan
from .run_format import SOURCE_KEY

MAX_SCALE_ATTEMPTS = 12


class RPointQualityError(RuntimeError):
    """所有重采尝试均未通过现有标准差门槛；保留诊断并阻断后继。"""


# ---- 帧读取与保存 ----
def read_dc_frame(ctx, config):
    """采集一帧 DC 电压；削顶时逐级放大量程，最大量程仍削顶即中止。"""
    p = ctx.params
    for _ in range(MAX_SCALE_ATTEMPTS):
        check_cancelled()
        results = {result.channel: result for result in ctx.devices["acquirer"].acquire_all(config)}
        sense = results[p.scope_current_channel]
        channel = next(item for item in config.channels if item.number == p.scope_current_channel)
        if float(np.max(np.abs(sense.voltage - channel.offset))) < 0.98 * VERTICAL_HALF_DIVISIONS * channel.scale:
            return sense
        if channel.scale >= 10.0:
            raise SafetyAbort("采样电阻电压在最大量程仍削顶")
        channel.scale = scale_for(channel.scale * VERTICAL_HALF_DIVISIONS * 2.0)
    raise SafetyAbort("量程调整后仍削顶")


def save_frame(path, sense, meta) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(path, time=sense.time, sense_v=sense.voltage,
             meta=json.dumps({**meta, "scope": sense.config_snapshot, "preamble": sense.preamble_dict},
                             default=str, ensure_ascii=False))


def _dc_scale(peak_a: float, sense_resistor_ohm: float) -> float:
    return scale_for(max(abs(peak_a), 1e-6) * sense_resistor_ohm)


def _dc_sample(ctx, config, path, meta) -> tuple[float, float]:
    """电学核对帧：不涉及温控窗口，直接读后保存。"""
    sense = read_dc_frame(ctx, config)
    save_frame(path, sense, meta)
    ctx.state["data_files"].append(_relative(ctx, path))
    return float(np.mean(sense.voltage)), float(np.std(sense.voltage))


def _relative(ctx, path) -> str:
    return str(path.relative_to(ctx.directory.root)).replace("\\", "/")


# ---- 温控门控采集 ----
def gated_acquire(ctx, acquire, writer=None):
    """配置已完成：关温控 → 等待 → 读入内存 → 恢复温控 → 写盘。"""
    p, d, ch = ctx.params, ctx.devices, ctx.channels
    check_cancelled()

    def set_enabled(enabled):
        set_temperature_switch(d["temp_switch"], enabled, channel=ch["temp_switch"])
        if enabled:
            time.sleep(max(0.0, p.temp_switch_on_lag_s))

    with DeferredAcquisitionWrites() as writes:
        with acquisition_window(set_enabled, sleep, p.system_settle_s):
            payload = acquire()
            if writer is not None:
                writes.add(writer, payload)
        return payload


def _measure_current_point(ctx, path, meta) -> dict:
    """在当前电流下门控采集采样电阻电压并换算实际电流。"""
    p = ctx.params
    dc_check = ctx.state.get("dc_check") or {}
    if "baseline_v" not in dc_check:
        raise SafetyAbort("缺少 A04 零偏/直流核对结果，无法换算实际电流")
    scale = _dc_scale(abs(meta["target_ma"]) * 1e-3, p.sense_resistor_ohm)
    config = capture_config(p, p.zero_window_s, scale)
    sense = gated_acquire(ctx, lambda: read_dc_frame(ctx, config),
                          writer=lambda payload: save_frame(path, payload, meta))
    ctx.state["data_files"].append(_relative(ctx, path))
    mean_v = float(np.mean(sense.voltage))
    current_a = ((mean_v - dc_check["baseline_v"]) / p.sense_resistor_ohm - dc_check["offset_a"])
    return dict(current_a=float(current_a), current_ma=float(current_a * 1e3),
                sense_v=mean_v, sense_std_v=float(np.std(sense.voltage)),
                drive_v=float(meta["drive_v"]), target_ma=float(meta["target_ma"]))


# ---- A04 零偏与直流 I–V 核对 ----
def measure_zero_and_dc_check(ctx) -> dict:
    p, root = ctx.params, ctx.directory.root
    set_amp_output(ctx, False)
    set_dc(ctx, 0.0, SOURCE_KEY)
    config = capture_config(p, p.zero_window_s, 0.01)
    off_means = [_dc_sample(ctx, config, root / "raw" / f"zero_off_{index:02d}.npz",
                            {"kind": "zero_off"})[0] for index in range(p.zero_frames)]
    set_amp_output(ctx, True)
    sleep(p.settle_s)
    on_means, noises = [], []
    for index in range(p.zero_frames):
        mean_v, std_v = _dc_sample(ctx, config, root / "raw" / f"zero_on_{index:02d}.npz",
                                   {"kind": "zero_on"})
        on_means.append(mean_v)
        noises.append(std_v)
    baseline = float(np.mean(off_means))
    offset_a = (float(np.mean(on_means)) - baseline) / p.sense_resistor_ohm

    points = []
    for index, target_ma in enumerate(p.dc_check_currents_ma):
        volts = float(target_ma) * 1e-3 / p.amps_per_drive_volt
        validate_safety_limit(SOURCE_KEY, volts)
        set_dc(ctx, volts, SOURCE_KEY)
        sleep(p.settle_s)
        scale = _dc_scale(abs(float(target_ma)) * 1e-3, p.sense_resistor_ohm)
        frame_config = capture_config(p, p.zero_window_s, scale)
        mean_v, std_v = _dc_sample(ctx, frame_config, root / "raw" / f"dc_check_{index:02d}.npz",
                                   {"kind": "dc_check", "target_ma": float(target_ma), "drive_v": volts})
        current_a = (mean_v - baseline) / p.sense_resistor_ohm - offset_a
        points.append(dict(target_ma=float(target_ma), drive_v=volts, sense_v=mean_v,
                           current_a=float(current_a), current_std_a=float(std_v / p.sense_resistor_ohm)))
    set_dc(ctx, 0.0, SOURCE_KEY)
    ctx.outputs["Z 通道"] = "DC 0 V + ON（直流核对完成）"

    drive = np.asarray([point["drive_v"] for point in points], dtype=float)
    current = np.asarray([point["current_a"] for point in points], dtype=float)
    slope, intercept = np.polyfit(drive, current, 1)
    predicted = slope * drive + intercept
    residual = current - predicted
    ss_res = float(np.sum(residual**2))
    ss_tot = float(np.sum((current - np.mean(current)) ** 2))
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 1e-30 else 0.0
    ratio = float(slope / p.amps_per_drive_volt)
    peak_current = float(np.max(np.abs(current))) if current.size else 0.0
    residual_max = float(np.max(np.abs(residual))) if residual.size else 0.0
    # 只记录电学链路实测值供换算与追溯，不设判据、不中止：链路已在“线圈驱动链路电学特性”表征过。
    ctx.state["dc_check"] = dict(
        baseline_v=baseline, offset_a=float(offset_a), noise_rms_a=float(np.mean(noises) / p.sense_resistor_ohm),
        k_a_per_v=float(slope), offset_fit_a=float(intercept), r_squared=float(r_squared),
        predicted_amps_per_volt=p.amps_per_drive_volt, ratio=ratio,
        residual_max_a=residual_max,
        residual_fraction=residual_max / peak_current if peak_current > 0 else 0.0,
        points=points)
    return ctx.state["dc_check"]


# ---- 频点采集 ----
def _lockin_device_id(ctx) -> str:
    return str(ctx.mapping["lockin_r"]["device_id"])


def _acquire_valid_r_point(ctx, point_dir, file_stem, metadata) -> tuple[dict, int, str]:
    p = ctx.params
    actual_rate = float(ctx.state.get("actual_rate_sa_s") or p.response_rate_sa_s)
    device_id = _lockin_device_id(ctx)
    for attempt in range(p.r_point_max_attempts):
        filename = f"{file_stem}_attempt_{attempt:02d}.npz"
        saved: list[bool] = []

        def save(payload):
            summary = summarize_r(payload)
            np.savez(point_dir / filename,
                     time_s=payload["time_s"], r_v=payload["r"],
                     actual_rate_sa_s=np.float64(payload.get("actual_rate_Sa_s", actual_rate)),
                     **{key: (np.float64(value) if isinstance(value, float) else value)
                        for key, value in metadata.items()},
                     **{key: np.float64(value) for key, value in summary.items()},
                     quality_accepted=np.uint8(summary["r_std_v"] <= p.r_bad_point_std_threshold_v),
                     r_std_threshold_v=np.float64(p.r_bad_point_std_threshold_v))
            saved.append(True)
            ctx.state["data_files"].append(_relative(ctx, point_dir / filename))
            return summary

        payload = gated_acquire(
            ctx,
            lambda: acquire_r(ctx.devices["hf2"], device_id=device_id, demod_idx=p.demod_idx,
                              actual_rate_sa_s=actual_rate, requested_rate_sa_s=p.response_rate_sa_s,
                              duration_s=p.frequency_duration_s),
            writer=save)
        if not saved:
            save(payload)
        summary = summarize_r(payload)
        if summary["r_std_v"] <= p.r_bad_point_std_threshold_v:
            return {**summary, "actual_rate_sa_s": float(payload.get("actual_rate_Sa_s", actual_rate))}, attempt, filename
    raise RPointQualityError(
        f"{file_stem} 连续 {p.r_point_max_attempts} 次 std(R) > {p.r_bad_point_std_threshold_v:.6g} V")


def _runtime_frequency_limit(ctx) -> float:
    capabilities = getattr(ctx.devices["hf2"], "capabilities", {}) or {}
    return float(capabilities.get("max_frequency_hz", np.inf))


def _checked_axis(frequencies: list[float], label: str) -> list[float]:
    """频率轴必须全为正且不超过锁相上限；越界直接中止。"""
    if not frequencies:
        raise SafetyAbort(f"{label} 频率轴为空")
    if frequencies[0] <= 0:
        raise SafetyAbort(f"{label} 扫频下限 {frequencies[0]:.6g} Hz 不大于 0")
    return [float(value) for value in frequencies]


def _axis_from(center_hz: float, half_width_hz: float, step_hz: float) -> list[float]:
    count = int(round(2.0 * half_width_hz / step_hz))
    frequencies = [float(center_hz - half_width_hz + index * step_hz) for index in range(count + 1)]
    return [value for value in frequencies if value > 0]


def _set_rf(ctx, frequency_hz: float) -> None:
    from .excitation import set_y_rf_frequency
    set_y_rf_frequency(ctx, frequency_hz)


def _stop_rf(ctx) -> None:
    from .excitation import stop_y_rf
    stop_y_rf(ctx)


def _axis_scan(ctx, point_dir, stem: str, frequencies, meta: dict) -> list[dict]:
    """逐频点设 Y RF 并采 R；attempt 文件由 _acquire_valid_r_point 保存。"""
    summaries = []
    try:
        for index, frequency in enumerate(frequencies):
            check_cancelled()
            _set_rf(ctx, frequency)
            summary, attempt, filename = _acquire_valid_r_point(
                ctx, point_dir, f"{stem}_{index:04d}",
                {**meta, "frequency_index": np.int64(index), "frequency_hz": np.float64(frequency)})
            summaries.append({**summary, "attempt": attempt, "file": filename})
    finally:
        _stop_rf(ctx)
    return summaries


# ---- A08 主扫描：逐电流点按预测中心扫频 ----
def measure_scan(ctx) -> dict:
    p = ctx.params
    dc_check = ctx.state.get("dc_check") or {}
    if "k_a_per_v" not in dc_check:
        raise SafetyAbort("缺少 A04 直流核对结果，无法设置主扫描电流")
    anchor_hz = float(p.main_field_center_hz)
    raw = ctx.directory.root / "raw"
    targets = p.current_targets_ma()
    directions = p.current_directions()
    records = []
    try:
        for index, (target_ma, direction) in enumerate(zip(targets, directions)):
            check_cancelled()
            volts = scan.drive_voltage_for_current(p, target_ma, amps_per_drive_volt=dc_check["k_a_per_v"],
                                                   offset_a=dc_check["offset_fit_a"])
            validate_safety_limit(SOURCE_KEY, volts)
            set_dc(ctx, volts, SOURCE_KEY)
            sleep(p.current_settle_time_s)
            point_dir = raw / f"current_{index:03d}"
            point_dir.mkdir(parents=True, exist_ok=True)
            current_record = _measure_current_point(
                ctx, point_dir / "sense_current.npz",
                {"kind": "current", "target_ma": float(target_ma), "direction": direction,
                 "index": index, "drive_v": float(volts)})
            meta = {"kind": "current", "index": np.int64(index), "target_current_ma": np.float64(target_ma),
                    "current_ma": np.float64(current_record["current_ma"]), "drive_v": np.float64(volts)}
            signed = p.signed_center_hz(current_record["current_ma"], center_hz=anchor_hz)
            window_center = abs(signed)
            near_zero = window_center < p.near_zero_scan_floor_hz
            frequencies: list[float] = []
            summaries: list[dict] = []
            note = ""
            if not near_zero:
                frequencies = _axis_from(window_center, p.frequency_half_width_hz, p.frequency_step_hz)
                if len(frequencies) < 5:
                    note = f"扫频窗口贴近 0 Hz（中心 {window_center:.6g} Hz），本点不拟合"
                else:
                    frequencies = _checked_axis(frequencies, f"电流 {target_ma:g} mA 扫频")
                    summaries = _axis_scan(
                        ctx, point_dir, "frequency", frequencies,
                        {**meta, "predicted_center_hz": np.float64(window_center),
                         "window_half_width_hz": np.float64(p.frequency_half_width_hz)})
            summary_path = point_dir / "frequency_scan.npz"
            np.savez(
                summary_path,
                frequency_hz=np.asarray(frequencies, dtype=float),
                r_mean_v=np.asarray([item["r_mean_v"] for item in summaries], dtype=float),
                r_std_v=np.asarray([item["r_std_v"] for item in summaries], dtype=float),
                current_a=np.float64(current_record["current_a"]),
                current_ma=np.float64(current_record["current_ma"]),
                sense_voltage_mean_v=np.float64(current_record["sense_v"]),
                sense_voltage_std_v=np.float64(current_record["sense_std_v"]),
                drive_v=np.float64(volts), target_current_ma=np.float64(target_ma),
                scan_direction=np.asarray(direction),
                predicted_center_hz=np.float64(window_center),
                window_half_width_hz=np.float64(p.frequency_half_width_hz),
                anchor_center_hz=np.float64(anchor_hz),
                near_zero_skipped=np.uint8(bool(near_zero)),
                acquisition_note=np.asarray(note),
                accepted_attempt_index=np.asarray([item["attempt"] for item in summaries], dtype=np.int64),
                accepted_file=np.asarray([item["file"] for item in summaries], dtype=str),
                actual_rate_sa_s=np.float64(
                    summaries[0].get("actual_rate_sa_s", p.response_rate_sa_s) if summaries else p.response_rate_sa_s),
            )
            ctx.state["data_files"].append(_relative(ctx, summary_path))
            records.append(dict(index=index, target_current_ma=float(target_ma),
                                current_ma=float(current_record["current_ma"]),
                                current_a=float(current_record["current_a"]),
                                drive_v=float(volts), scan_direction=direction,
                                predicted_center_hz=float(window_center), near_zero_skipped=bool(near_zero),
                                scan_failed=bool(note), note=note,
                                summary_file=_relative(ctx, summary_path),
                                accepted_attempt_index=int(summaries[-1]["attempt"]) if summaries else -1))
    finally:
        set_dc(ctx, 0.0, SOURCE_KEY)
        _stop_rf(ctx)
        ctx.outputs["Z 通道"] = "DC 0 V + ON（扫描结束）"
    index_path = raw / "current_scan_index.npz"
    np.savez(
        index_path,
        target_current_ma=np.asarray([item["target_current_ma"] for item in records], dtype=float),
        current_ma=np.asarray([item["current_ma"] for item in records], dtype=float),
        current_a=np.asarray([item["current_a"] for item in records], dtype=float),
        drive_v=np.asarray([item["drive_v"] for item in records], dtype=float),
        scan_direction=np.asarray([item["scan_direction"] for item in records], dtype=str),
        predicted_center_hz=np.asarray([item["predicted_center_hz"] for item in records], dtype=float),
        near_zero_skipped=np.asarray([item["near_zero_skipped"] for item in records], dtype=bool),
        scan_failed=np.asarray([item["scan_failed"] for item in records], dtype=bool),
        summary_file=np.asarray([item["summary_file"] for item in records], dtype=str),
        anchor_center_hz=np.float64(anchor_hz),
        polarity_sign=np.float64(p.polarity_sign),
        initial_hz_per_ma=np.float64(p.initial_hz_per_ma),
    )
    ctx.state["data_files"].append(_relative(ctx, index_path))
    ctx.state["current_scan"] = dict(point_count=len(records),
                                     near_zero_skipped=sum(item["near_zero_skipped"] for item in records),
                                     scan_failed=sum(item["scan_failed"] for item in records),
                                     anchor_center_hz=anchor_hz,
                                     polarity_sign=p.polarity_sign)
    return ctx.state["current_scan"]
