"""标准 Mx 四段采集：整周相位扫描、工作点细扫、基带频率响应与无调制噪声。

每帧顺序固定：确认锁相配置未变 → 关温控 → SYSTEM_SETTLE_S 稳定 → DAQ 采到内存 →
恢复温控（5 V DC + ON）→ 等恢复滞后 → 写盘。帧体检在写盘前完成，含 100 Hz 以上单音
（宽带档忽略 5 kHz 载波泄漏的已知寄生频点）或标准差过大的帧直接抛错，不落盘。
A05 起把 Demod 4 切到高带宽档并开启 MF-MOD 2° 峰值相干边带 PM 按频率表逐点扫频；
A06 关闭边带后仍在同一宽带档分段采集无调制噪声。判据算法在 analysis_steps，采集只调用不复制。
"""
from __future__ import annotations

import math
import time

import numpy as np
from lockin_amplifier import DAQConfig, daq

from ...experiment_runtime import check_cancelled, report_runtime_progress
from ...steps import DeferredAcquisitionWrites, acquisition_window
from ...steps.coil_drive_chain import SafetyAbort
from . import preparation, run_format
from .analysis_steps import (choose_working_point, circle_fit, diagram_metrics, fine_offsets,
                             fine_scan_slope, frame_quality)
from .models import READOUT_OSC_IDX

# 开扫前等原子响应建立：相邻两次直流相量差降到阈值以下再扫，超时带读数继续并记录。
STABLE_TOL_V = 0.1e-3
STABLE_TIMEOUT_S = 180.0
STABLE_POLL_S = 3.0
STABLE_READ_S = 0.2
# 加载到帧元数据里的激励模式：A03/A04 连续无调制载波，A05 相干边带 PM，A06 无调制载波。
SOURCE_UNPULSED = "mx_standard_unpulsed"
SOURCE_PM = "mx_standard_pm_sideband"
SOURCE_UNMODULATED = "mx_standard_unmodulated"


def lockin_readback(ctx, *, dynamic: bool = False) -> dict:
    """回读参考、滤波与传输，确认原子读出锁定在 Demod 4 / Osc 4。

    dynamic 为真时按 A05/A06 的宽带档核对时间常数，并要求传输率不低于最高调相频率的 10 倍；
    A02–A04 仍按窄带静态读出核对。
    """
    d, p = ctx.devices["hf2"], ctx.params
    base = d.demod_path(p.demod_idx)
    expected_tc = p.demod_dynamic_tc_s if dynamic else p.demod_tc_s
    min_rate = 10 * max(p.pm_frequencies_hz) if dynamic else 0.0
    state = dict(
        phase_deg=float(d.get_double(f"{base}/phaseshift")),
        time_constant_s=float(d.get_double(f"{base}/timeconstant")),
        stream_rate_sa_s=float(d.get_double(f"{base}/rate")),
        order=int(d.get_int(f"{base}/order")),
        oscillator_hz=float(d.get_double(f"{d.osc_path(READOUT_OSC_IDX)}/freq")),
    )
    if (not d.get_extclk() or not all(math.isfinite(value) for value in state.values())
            or state["stream_rate_sa_s"] <= 0
            or state["stream_rate_sa_s"] < min_rate
            or not math.isclose(state["oscillator_hz"], p.y_rf_freq_hz, rel_tol=1e-6)
            or not math.isclose(state["time_constant_s"], expected_tc, rel_tol=0.01)
            or state["order"] != p.demod_order
            or any(d.get_int(f"{base}/{node}") != value for node, value in
                   (("adcselect", 0), ("oscselect", READOUT_OSC_IDX), ("harmonic", 1), ("enable", 1)))):
        raise SafetyAbort(f"MFLI Demod {p.demod_idx + 1} 输入、参考、解调滤波或采样率回读不符：{state}")
    return state


def _daq(ctx, duration_s: float, lockin: dict, *, requested_rate: float):
    """连续流采一帧同步 X/Y；短帧、丢样、配置改变或无真实时间戳都拒绝。"""
    rate = lockin["stream_rate_sa_s"]
    d, p = ctx.devices["hf2"], ctx.params
    cols = round(rate * duration_s)
    cfg = DAQConfig(device=d.device_id, duration=duration_s, grid_cols=cols, grid_rows=1, grid_mode=4,
                    signal_paths=["sample.x", "sample.y"])
    results = daq.acquire_data(d, cfg, p.demod_idx, rate, timeout=duration_s + 15.0,
                               requested_rate=requested_rate, cancellation_check=check_cancelled,
                               strict_cleanup=True)
    by_name = {result.signal_name: result for result in results}
    if len(results) != 2 or set(by_name) != {"sample.x", "sample.y"}:
        raise SafetyAbort("DAQ 未返回完整同步 X/Y")
    x, y = by_name["sample.x"], by_name["sample.y"]
    details = x.config_snapshot
    got = details["actual_rate"]
    if (x.config_snapshot != y.config_snapshot or not np.array_equal(x.time, y.time)
            or any(result.record_slices != [(0, cols)] or len(result.values) != cols for result in results)
            or details.get("acquisition_module") != "daq" or details.get("time_source") != "module_timestamp"
            or details.get("unit") != "V" or not math.isclose(got, rate, rel_tol=0.003)):
        raise SafetyAbort("DAQ 短帧、丢样、配置改变或无真实同步时间戳，不把插值波形当作有效记录")
    return dict(rate_sa_s=got, time_source=details["time_source"], lockin=lockin, daq=details), dict(
        time_s=x.time, x_v=x.values, y_v=y.values)


def _xy(ctx, label: str, duration_s: float, *, lockin=None, dynamic: bool = False, **record) -> complex:
    """仪器设置在关温控前，缓冲数据仅在开温控后保存；返回该帧直流相量。"""
    before = lockin_readback(ctx, dynamic=dynamic)
    if lockin is not None and before != lockin:
        raise SafetyAbort("MFLI 配置发生变化，拒绝并入同轮记录")
    p = ctx.params
    requested_rate = p.demod_dynamic_rate_sa_s if dynamic else p.demod_rate_sa_s
    recovery_wait_s = p.dynamic_temp_switch_on_lag_s if dynamic else p.temp_switch_on_lag_s
    temperature_on = True

    def set_enabled(enabled):
        nonlocal temperature_on
        if not enabled:
            temperature_on = False
        preparation.write_temperature(ctx, enabled)
        temperature_on = enabled

    def retry_restore():
        if not temperature_on:
            set_enabled(True)

    def write_record(metadata, **arrays):
        path = run_format.write_frame(ctx.directory.root, label, "xy", metadata, **arrays)
        ctx.state["data_files"].append(path)

    with DeferredAcquisitionWrites(restore=retry_restore) as pending:
        with acquisition_window(set_enabled, preparation.wait, p.system_settle_s):
            metadata, arrays = _daq(ctx, duration_s, before, requested_rate=requested_rate)
            quality = frame_quality(label, arrays["x_v"], arrays["y_v"], metadata["rate_sa_s"], dynamic=dynamic)
            pending.add(write_record, dict(metadata, quality=quality, dynamic=dynamic, **record), **arrays)
        if recovery_wait_s > 0:
            preparation.wait(recovery_wait_s)
        if lockin_readback(ctx, dynamic=dynamic) != before:
            raise SafetyAbort("MFLI 采集前后回读不一致；原始记录仅供诊断")
    return complex(np.mean(arrays["x_v"]), np.mean(arrays["y_v"]))


def _read_point(ctx, label: str, duration_s: float, lockin: dict, *, dynamic: bool = False,
                source_mode: str = SOURCE_UNPULSED, **record) -> complex:
    """采一段 XY 并返回其直流相量；温控窗口、写盘与前后回读一致性由 _xy 保证。"""
    return _xy(ctx, label, duration_s, lockin=lockin, dynamic=dynamic,
               command_deg=float(ctx.carrier["command_deg"]) if ctx.carrier else None,
               source_mode=source_mode, **record)


def wait_for_stable(ctx, phase_deg: float, lockin: dict) -> dict:
    """等原子响应建立：相邻两次直流相量差小于阈值才开扫；超时也继续，但记进状态。"""
    preparation.carrier_set_phase(ctx, phase_deg)
    history: list[complex] = []
    started = time.monotonic()
    index = 0
    while True:
        mean = _read_point(ctx, f"mx_std_settle_{index:02d}", STABLE_READ_S, lockin)
        history.append(mean)
        elapsed = time.monotonic() - started
        delta = abs(history[-1] - history[-2]) if len(history) > 1 else float("nan")
        if len(history) > 1 and delta <= STABLE_TOL_V:
            record = dict(stable=True, waited_s=elapsed, reads=len(history), last_delta_v=float(delta),
                          phasors=[[float(z.real), float(z.imag)] for z in history])
            ctx.state["settle"] = record
            ctx.save()
            return record
        if elapsed >= STABLE_TIMEOUT_S:
            record = dict(stable=False, waited_s=elapsed, reads=len(history), last_delta_v=float(delta),
                          phasors=[[float(z.real), float(z.imag)] for z in history])
            ctx.state["settle"] = record
            ctx.save()
            print(f"  [警告] 稳定等待超时（{elapsed:.1f} s），按当前读数继续", flush=True)
            return record
        index += 1
        preparation.wait(STABLE_POLL_S)


def acquire_phase_diagram(ctx) -> dict:
    """A03：整周相位扫描（预热丢弃 + N 点 + 末尾回采首点）与圆拟合。"""
    p = ctx.params
    grid = list(p.phase_grid_deg)
    if len(grid) < 6:
        raise SafetyAbort(f"相位网格点数过少：{len(grid)}")
    lockin = lockin_readback(ctx)
    settle = wait_for_stable(ctx, grid[0], lockin)
    warm = _read_point(ctx, "mx_std_warmup_00", p.point_duration_s, lockin)
    start = time.monotonic()
    labels: list[str] = []
    phasors: list[complex] = []
    for index, phase in enumerate(grid):
        check_cancelled()
        preparation.carrier_set_phase(ctx, phase)
        label = f"mx_std_coarse_{index:02d}"
        mean = _read_point(ctx, label, p.point_duration_s, lockin)
        labels.append(label)
        phasors.append(mean)
        done = index + 1
        report_runtime_progress("A03", f"整周相位扫描 {done}/{len(grid)}，{phase:g}°", 10 + 80 * done / len(grid),
                                estimated_remaining_seconds=(time.monotonic() - start) / done * (len(grid) - done),
                                data={"eta_scope": "当前相位扫描"})
    preparation.carrier_set_phase(ctx, grid[0])
    repeat = _read_point(ctx, "mx_std_repeat_00", p.point_duration_s, lockin)
    drift = float(abs(repeat - phasors[0]))
    fit = circle_fit(np.asarray([z.real for z in phasors]), np.asarray([z.imag for z in phasors]))
    metrics = diagram_metrics(phasors, fit)
    chosen = choose_working_point(np.asarray(grid, dtype=float), phasors, fit)
    ctx.state["phase_diagram"] = dict(
        labels=labels, grid_deg=grid, warmup_label="mx_std_warmup_00", repeat_label="mx_std_repeat_00",
        first_point_drift_v=drift, warmup_delta_v=float(abs(warm - phasors[0])), settle=settle,
        warmup_phasor=[float(warm.real), float(warm.imag)],
        repeat_phasor=[float(repeat.real), float(repeat.imag)],
        circle_fit=fit, coarse_candidate=chosen,
        tangent_median_v_per_rad=metrics["tangent_median_v_per_rad"],
        tangent_relative_spread=metrics["tangent_relative_spread"],
        tangent_over_radius=metrics["tangent_median_v_per_rad"] / fit["radius_v"],
        accepted_count=metrics["accepted_count"], accepted_total=metrics["accepted_total"])
    ctx.save()
    ctx.outputs["相位扫描"] = (f"整周 {len(grid)} 点，R={fit['radius_v'] * 1e3:.3f} mV，"
                               f"回采首点漂移 {drift * 1e6:.1f} uV")
    print(f"  圆拟合 R={fit['radius_v'] * 1e3:.3f} mV，残差 rms {fit['rms_residual_v'] * 1e3:.3f} mV；"
          f"工作点候选 指令 {chosen['command_deg']:.1f}°、相位角 {chosen['phasor_angle_deg']:.1f}°", flush=True)
    report_runtime_progress("A03", f"整周扫描完成，R={fit['radius_v'] * 1e3:.3f} mV", 95,
                            estimated_remaining_seconds=None)
    return ctx.state["phase_diagram"]


def acquire_working_point(ctx) -> dict:
    """A04：在工作点附近细扫，用实测相位角回归 |dY/dphi| 并与圆模型互校，定最终工作点。"""
    p = ctx.params
    diagram = ctx.state.get("phase_diagram") or {}
    chosen = diagram.get("coarse_candidate")
    fit = diagram.get("circle_fit")
    if not chosen or not fit:
        raise SafetyAbort("缺少本轮整周扫描结果；A04 必须在 A03 之后按同一轮执行")
    lockin = lockin_readback(ctx)
    commands = [float((chosen["command_deg"] + float(offset)) % 360.0) for offset in fine_offsets()]
    start = time.monotonic()
    labels: list[str] = []
    phasors: list[complex] = []
    for index, phase in enumerate(commands):
        check_cancelled()
        preparation.carrier_set_phase(ctx, phase)
        label = f"mx_std_fine_{index:02d}"
        phasors.append(_read_point(ctx, label, p.point_duration_s, lockin))
        labels.append(label)
        done = index + 1
        report_runtime_progress("A04", f"工作点细扫 {done}/{len(commands)}，{phase:.2f}°", 10 + 80 * done / len(commands),
                                estimated_remaining_seconds=(time.monotonic() - start) / done * (len(commands) - done),
                                data={"eta_scope": "当前工作点细扫"})
    slope = fine_scan_slope(commands, phasors, fit)
    work_phase = float(slope["best_command_deg"])
    preparation.carrier_set_phase(ctx, work_phase)
    ctx.state["fine_scan"] = dict(labels=labels, commands_deg=commands,
                                  phasors=[[float(z.real), float(z.imag)] for z in phasors],
                                  slope_v_per_rad=slope["slope_v_per_rad"],
                                  residual_v=slope["residual_v"],
                                  analytic_v_per_rad=slope["analytic_v_per_rad"],
                                  best_index=slope["best_index"])
    ctx.state["working_point"] = dict(command_deg=work_phase,
                                      phasor_angle_deg=float(slope["best_phasor_angle_deg"]),
                                      cos_phi=float(slope["best_cos_phi"]),
                                      local_slope_v_per_rad=float(slope["slope_v_per_rad"]),
                                      analytic_slope_v_per_rad=float(slope["analytic_v_per_rad"]),
                                      coarse_command_deg=float(chosen["command_deg"]))
    ctx.save()
    ctx.outputs["工作点"] = (f"指令 {work_phase:.2f}°、相位角 {slope['best_phasor_angle_deg']:.2f}°、"
                             f"局部斜率 {slope['slope_v_per_rad']:.5g} V/rad（圆模型 {slope['analytic_v_per_rad']:.5g}）")
    report_runtime_progress("A04", f"工作点 {work_phase:.2f}°", 95, estimated_remaining_seconds=None)
    return ctx.state["working_point"]


def acquire_frequency_response(ctx) -> list[dict]:
    """A05：切宽带档、开启 2° 峰值相干边带 PM，按频率表逐点采集 XY 并登记频响行。"""
    p = ctx.params
    working = ctx.state.get("working_point") or {}
    if "command_deg" not in working:
        raise SafetyAbort("缺少本轮工作点；A05 必须在 A04 之后按同一轮执行")
    preparation.carrier_set_phase(ctx, float(working["command_deg"]))
    phase_deg = float(lockin_readback(ctx)["phase_deg"])
    preparation.configure_dynamic_lockin(ctx, phase_deg=phase_deg)
    lockin = lockin_readback(ctx, dynamic=True)
    pm_settings = preparation.carrier_begin_pm(ctx)
    pm_rad = float(pm_settings["mod_index_rad"])
    if not math.isfinite(pm_rad) or pm_rad <= 0:
        raise SafetyAbort(f"MFLI MF-MOD 调相深度回读无效：{pm_rad}")
    ctx.state["pm_alignment"] = dict(skipped=True, method="mf_mod_coherent_direct",
                                     command_deg=pm_settings["command_deg"], index_rad=pm_rad,
                                     quality_passed=True, quality_warnings=[], reason="")
    ctx.state["transfer"] = []
    ctx.save()
    frequencies = list(p.sweep_order_hz)
    start = time.monotonic()
    for index, frequency_hz in enumerate(frequencies):
        check_cancelled()
        settings = preparation.carrier_set_pm_frequency(ctx, frequency_hz)
        row_pm = float(settings["mod_index_rad"])
        if not math.isfinite(row_pm) or row_pm <= 0:
            raise SafetyAbort(f"MFLI MF-MOD 调相深度回读无效：{row_pm}")
        label = f"mx_std_pm_{index:02d}_xy"
        row = dict(frequency_hz=float(frequency_hz), repeat=False, scope_label=None, xy_label=None,
                   phase_pk_rad=row_pm, modulation_hz=float(frequency_hz),
                   electrical_accepted=True, accepted=False, reason="尚未完成")
        ctx.state["transfer"].append(row)
        _read_point(ctx, label, p.pm_tone_duration_s, lockin, dynamic=True, source_mode=SOURCE_PM,
                    frequency_hz=float(frequency_hz), phase_pk_rad=row_pm, modulation_hz=float(frequency_hz))
        row.update(xy_label=label, accepted=True, reason="")
        ctx.save()
        done = index + 1
        report_runtime_progress("A05", f"基带频响 {done}/{len(frequencies)}，{frequency_hz:g} Hz：已采 XY",
                                10 + 80 * done / len(frequencies),
                                estimated_remaining_seconds=(time.monotonic() - start) / done * (len(frequencies) - done),
                                data={"eta_scope": "当前基带扫频"})
    ctx.state["transfer_lockin"] = dict(lockin, pm_depth_deg=math.degrees(pm_rad),
                                        frequencies_hz=frequencies)
    ctx.save()
    ctx.outputs["基带频响"] = (f"{len(frequencies)} 档调相扫频完成（2° 峰值，{frequencies[0]:g}–"
                              f"{frequencies[-1]:g} Hz，宽带档 TC {p.demod_dynamic_tc_s:g} s）")
    report_runtime_progress("A05", "基带频响完成", 95, estimated_remaining_seconds=None)
    return ctx.state["transfer"]


def acquire_noise(ctx) -> dict:
    """A06：关闭调相边带、保持宽带档，按独立温控窗口分段采集无调制噪声。"""
    p = ctx.params
    working = ctx.state.get("working_point") or {}
    if "command_deg" not in working:
        raise SafetyAbort("缺少本轮工作点；A06 必须在 A04 之后按同一轮执行")
    if not any(row.get("accepted") for row in ctx.state.get("transfer") or ()):
        raise SafetyAbort("缺少本轮有效频响采集记录；A06 必须在 A05 之后按同一轮执行")
    preparation.carrier_set_phase(ctx, float(working["command_deg"]))
    preparation.carrier_stop_pm(ctx)
    lockin = lockin_readback(ctx, dynamic=True)
    segments = int(p.noise_segments)
    state = dict(accepted=False, electrical_accepted=True, quality_passed=False, quality_warnings=[],
                 reason="未完成噪声采集", labels=[], xy_label=None, segments=segments, duration_s=p.noise_duration_s,
                 segments_requested=segments, segments_completed=0,
                 demod_dynamic=True, time_constant_s=lockin["time_constant_s"],
                 stream_rate_sa_s=lockin["stream_rate_sa_s"],
                 carrier_command_deg=float(working["command_deg"]))
    ctx.state["noise"] = state
    ctx.save()
    start = time.monotonic()
    for index in range(segments):
        check_cancelled()
        label = f"mx_std_noise_{index:02d}_xy"
        mean = _read_point(ctx, label, p.noise_duration_s, lockin, dynamic=True,
                           source_mode=SOURCE_UNMODULATED)
        state["labels"].append(label)
        state.update(xy_label=state["labels"][0] if segments == 1 else None,
                     segments_completed=index + 1,
                     mean_x_v=float(mean.real), mean_y_v=float(mean.imag))
        ctx.save()
        done = index + 1
        report_runtime_progress("A06", f"无调制噪声分段 {done}/{segments}", 10 + 80 * done / segments,
                                estimated_remaining_seconds=(time.monotonic() - start) / done * (segments - done),
                                data={"eta_scope": "当前噪声分段"})
    verification = preparation.carrier_verify(ctx, output=True)
    state.update(accepted=True, quality_passed=True, reason="",
                 carrier_command_deg=float(verification["command_deg"]))
    ctx.save()
    ctx.outputs["噪声"] = (f"{segments} 段 × {p.noise_duration_s:g} s（宽带档 TC {p.demod_dynamic_tc_s:g} s，"
                           f"无调制载波，独立温控窗口）")
    report_runtime_progress("A06", "噪声采集完成", 95, estimated_remaining_seconds=None)
    return ctx.state["noise"]
