"""同一轮 Burst 局部标定、原生 PM 传递函数和无调制锁相噪声采集。"""
from __future__ import annotations

import math
import time

import numpy as np
from lockin_amplifier import DAQConfig, daq, demod

from ...common import load_safety_limits, validate_safety_limit
from ...experiment_runtime import check_cancelled, report_runtime_progress
from ...steps import DeferredAcquisitionWrites, acquisition_window
from ...steps.coil_drive_chain import SafetyAbort, sleep
from ..z_chain_common import prep
from ..z_chain_common.keys import RF_KEY
from . import monitor, preparation, run_format, signal_fit
from .models import PM_DEPTH_DEG

_PM_ALIGN_HZ = 20.
_NUDGE_DEG = 30.
_CENTER_TOL_DEG = 2.
_ANCHOR_TOL_DEG = 1.
_DEPTH_TOL_DEG = 0.1
_AM_MAX_FRACTION = 0.001
_CARRIER_FRACTION_TOL = 0.05
_STATIC_GRID_DEG = tuple(range(0, 360, 15))


class Carrier:
    """只管理已映射的 Y DG4000 通道和该通道的仪器回读。"""

    def __init__(self, ctx):
        self.ctx = ctx
        routed = ctx.devices["signals"].routes[ctx.devices["channels"]["y"]]
        self.device, self.channel = routed.device, routed.actual_channel
        self.command_deg = 0.
        self.fm_hz = None

    def _call(self, name, *args):
        return getattr(self.device, name)(*args, channel=self.channel)

    def _output(self):
        return preparation.dg_output(self.device, self.channel)

    def _safe(self):
        check_cancelled()
        limits = load_safety_limits(self.ctx.root)
        for v in prep.rf_levels(self.ctx.params):
            validate_safety_limit(RF_KEY, v, limits)

    def _verify(self, *, output, mode):
        self.device.wait_for_operation_complete()
        self.device.raise_for_errors()
        p = self.ctx.params
        got = dict(shape=self._call("get_shape"), output=self._output(),
                   burst=self._call("get_burst_state"), modulation=self._call("get_mod_state"),
                   carrier_hz=self._call("get_frequency"), carrier_vpp=self._call("get_amplitude"),
                   offset_v=self._call("get_offset"))
        valid = (got["shape"].upper() in {"SIN", "SINUSOID"} and got["output"] is output
                 and got["burst"] is (mode == "burst") and got["modulation"] is (mode == "pm")
                 and math.isclose(got["carrier_hz"], p.y_rf_freq_hz, rel_tol=1e-6)
                 and math.isclose(got["carrier_vpp"], p.y_rf_vpp, abs_tol=1e-4)
                 and math.isclose(got["offset_v"], p.y_bias_v, abs_tol=1e-4))
        if mode == "burst":
            got.update(command_deg=self._call("get_burst_phase"), burst_mode=self._call("get_burst_mode"),
                       source=self._call("get_burst_trigger_source"), slope=self._call("get_burst_trigger_slope"))
            valid = (valid and got["burst_mode"].upper() in {"INF", "INFINITY"}
                     and got["source"].upper() in {"EXT", "EXTERNAL"} and got["slope"].upper() in {"POS", "POSITIVE"})
        else:
            got["command_deg"] = self._call("get_phase_adjust")
        if mode == "pm":
            got.update(frequency_hz=self._call("get_mod_pm_internal_freq"), depth_deg=self._call("get_mod_pm_deviation"),
                       type=self._call("get_mod_type"), source=self._call("get_mod_pm_source"),
                       function=self._call("get_mod_pm_internal_func"))
            valid = (valid and got["type"].upper() == "PM" and got["source"].upper() in {"INT", "INTERNAL"}
                     and got["function"].upper() in {"SIN", "SINUSOID", "SINE"}
                     and math.isclose(got["frequency_hz"], self.fm_hz, abs_tol=0.001)
                     and math.isclose(got["depth_deg"], PM_DEPTH_DEG, abs_tol=0.01))
        if not valid or not abs(signal_fit.wrap_deg(got["command_deg"] - self.command_deg)) <= 0.02:
            raise SafetyAbort(f"Y 射频 {mode} 仪器回读不符：{got}")
        return got

    def _off(self):
        self._call("set_output", False)
        if self._output():
            raise SafetyAbort("Y 射频未回读为 OFF，不切换波形模式")

    def begin_burst(self):
        self._safe()
        self._off()
        p = self.ctx.params
        self._call("set_burst_state", False)
        self._call("set_mod_state", False)
        self._call("set_shape", "SINusoid")
        self._call("set_frequency", p.y_rf_freq_hz)
        self._call("set_amplitude", p.y_rf_vpp)
        self._call("set_offset", p.y_bias_v)
        self._call("set_phase_adjust", 0.)
        self._call("set_burst_mode", "INFinity")
        self._call("set_burst_ncycles", prep.BURST_NCYCLES)
        self._call("set_burst_trigger_source", "EXTernal")
        self._call("set_burst_trigger_slope", "POSitive")
        return self.burst_phase(0.)

    def burst_phase(self, phase_deg):
        self._safe()
        self._off()
        self.command_deg = phase_deg % 360.
        self._call("set_burst_state", False)
        self._call("set_burst_phase", self.command_deg)
        self._call("set_burst_state", True)
        self._verify(output=False, mode="burst")
        self._call("set_output", True)
        if not self._output():
            raise SafetyAbort("Y Burst 启动未回读为 ON")
        self.ctx.outputs["Y 射频"] = f"Burst，指令相位 {self.command_deg:g}°"
        return self.command_deg

    def begin_pm(self):
        self._safe()
        self._off()
        self._call("set_burst_state", False)
        self._call("set_mod_state", False)
        self._call("set_mod_type", "PM")
        self._call("set_mod_pm_source", "INTernal")
        self._call("set_mod_pm_internal_func", "SINusoid")
        self.fm_hz = _PM_ALIGN_HZ
        self._call("set_mod_pm_internal_freq", self.fm_hz)
        self._call("set_mod_pm_deviation", PM_DEPTH_DEG)
        self._call("set_mod_state", True)
        self._call("set_phase_adjust", self.command_deg)
        self._verify(output=False, mode="pm")
        self._call("set_output", True)
        return self._verify(output=True, mode="pm")

    def phase_adjust(self, value, mode):
        self._safe()
        self.command_deg = value % 360.
        self._call("set_phase_adjust", self.command_deg)
        return self._verify(output=True, mode=mode)

    def frequency(self, f_hz):
        self._safe()
        if f_hz != self.fm_hz:
            self._call("set_mod_pm_internal_freq", f_hz)
            self.fm_hz = f_hz
        self.ctx.outputs["Y 射频"] = f"原生 PM {self.fm_hz:g} Hz，深度 1° 峰值"
        return self._verify(output=True, mode="pm")

    def stop_pm(self):
        self._safe()
        self._call("set_mod_state", False)
        # 不退出载波正弦；若仪器同时改变相位指令，记录后按物理相位回调。
        self.command_deg = self._call("get_phase_adjust")
        self.ctx.outputs["Y 射频"] = "连续载波，PM OFF"
        return self._verify(output=True, mode="off")

    def verify_unmodulated(self):
        return self._verify(output=True, mode="off")


def lockin_readback(ctx):
    """回读实际参考、滤波与传输，禁止采集中替换链路。"""
    d, p = ctx.devices["hf2"], ctx.params
    base = d.demod_path(p.demod0_idx)
    state = dict(phase_deg=float(d.get_double(f"{base}/phaseshift")),
                 time_constant_s=float(d.get_double(f"{base}/timeconstant")),
                 stream_rate_sa_s=float(d.get_double(f"{base}/rate")),
                 order=int(d.get_int(f"{base}/order")),
                 oscillator_hz=float(d.get_double(f"{d.osc_path(0)}/freq")))
    if (not d.get_extclk() or not all(math.isfinite(v) for v in state.values())
            or state["stream_rate_sa_s"] < 10 * max(p.pm_frequencies_hz)
            or not math.isclose(state["oscillator_hz"], p.y_rf_freq_hz, rel_tol=1e-6)
            or not math.isclose(state["time_constant_s"], p.demod0_tc_s, rel_tol=0.01)
            or state["order"] != p.demod0_order
            or any(d.get_int(f"{base}/{node}") != value for node, value in
                   (("adcselect", 0), ("oscselect", 0), ("harmonic", 1), ("enable", 1)))):
        raise SafetyAbort(f"MFLI 输入、参考、解调滤波或采样率回读不符：{state}")
    return state


def _save(ctx, label, kind, metadata, **arrays):
    path = run_format.write_frame(ctx.directory.root, label, kind, metadata, **arrays)
    ctx.state["data_files"].append(path)


def _daq(ctx, duration_s, lockin):
    rate = lockin["stream_rate_sa_s"]
    d, p = ctx.devices["hf2"], ctx.params
    cols = round(rate * duration_s)
    cfg = DAQConfig(device=d.device_id, duration=duration_s, grid_cols=cols, grid_rows=1, grid_mode=4,
                    signal_paths=["sample.x", "sample.y"])
    results = daq.acquire_data(d, cfg, p.demod0_idx, rate, timeout=duration_s + 15.,
                               requested_rate=p.demod0_rate_sa_s, cancellation_check=check_cancelled, strict_cleanup=True)
    by_name = {r.signal_name: r for r in results}
    if len(results) != 2 or set(by_name) != {"sample.x", "sample.y"}:
        raise SafetyAbort("DAQ 未返回完整同步 X/Y")
    x, y = by_name["sample.x"], by_name["sample.y"]
    details = x.config_snapshot
    got = details["actual_rate"]
    if (x.config_snapshot != y.config_snapshot or not np.array_equal(x.time, y.time)
            or any(r.record_slices != [(0, cols)] or len(r.values) != cols for r in results)
            or details.get("acquisition_module") != "daq" or details.get("time_source") != "module_timestamp"
            or details.get("unit") != "V" or not math.isclose(got, rate, rel_tol=0.003)):
        raise SafetyAbort("DAQ 短帧、丢样、配置改变或无真实同步时间戳，不将插值波形当作有效记录")
    return dict(rate_sa_s=got, time_source=details["time_source"], lockin=lockin, daq=details), dict(
        time_s=x.time, x_v=x.values, y_v=y.values)


def _xy(ctx, label, duration_s, *, lockin=None, **record):
    """仪器设置在关温控前，缓冲数据仅在开温控后保存。"""
    before = lockin_readback(ctx)
    if lockin is not None and before != lockin:
        raise SafetyAbort("MFLI 配置发生变化，拒绝并入同轮标定")
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

    with DeferredAcquisitionWrites(restore=retry_restore) as pending:
        with acquisition_window(set_enabled, sleep, ctx.params.system_settle_s):
            metadata, arrays = _daq(ctx, duration_s, before)
            pending.add(lambda meta, **data: _save(ctx, label, "xy", meta, **data), dict(metadata, **record), **arrays)
        sleep(ctx.params.temp_switch_on_lag_s)
        if lockin_readback(ctx) != before:
            raise SafetyAbort("MFLI 采集前后回读不一致；原始记录仅供诊断")
    return complex(np.mean(arrays["x_v"]), np.mean(arrays["y_v"]))


def _electrical(ctx, label, settings, f_hz=None):
    """电学帧在温控开启时读取，只在同一冻结帧内分离 PM/AM。"""
    sleep(ctx.params.system_settle_s)
    metadata, arrays = monitor.acquire(ctx, f_hz)
    _save(ctx, label, "scope", dict(metadata, generator=settings, modulation_analysis_hz=f_hz), **arrays)
    return signal_fit.measure_electrical(arrays["time_s"], arrays["reference_v"], arrays["coil_v"],
                                         ctx.params.y_rf_freq_hz, f_hz)


def _require_stable(point):
    if not point["stable"]:
        raise SafetyAbort("DHO924 电学帧载波拟合或帧内相位稳定度不足")
    return point["carrier_center_deg"]


def _calibrate(ctx, carrier):
    p = ctx.params
    start = time.monotonic()
    coarse = []
    coarse_lockin = lockin_readback(ctx)
    for i, phase in enumerate(_STATIC_GRID_DEG):
        carrier.burst_phase(phase)
        coarse.append(_xy(ctx, f"coarse_{i:02d}", p.static_point_duration_s, lockin=coarse_lockin, burst_command_deg=phase))
        done = i + 1
        report_runtime_progress("A03", f"扫描原子相位 {done}/{len(_STATIC_GRID_DEG)}", 15 * done / len(_STATIC_GRID_DEG),
                                estimated_remaining_seconds=(time.monotonic() - start) / done * (len(_STATIC_GRID_DEG) - done),
                                eta_scope="当前静态扫描")
    best, rotation, prediction = signal_fit.best_tangent(_STATIC_GRID_DEG, coarse)
    d = ctx.devices["hf2"]
    setpoint = float(signal_fit.wrap_deg(lockin_readback(ctx)["phase_deg"] + rotation))
    demod.set_demod_phase(d, p.demod0_idx, setpoint)
    settings = lockin_readback(ctx)
    if abs(signal_fit.wrap_deg(settings["phase_deg"] - setpoint)) > 0.5:
        raise SafetyAbort("MFLI 最大切线转到 Y 的相位设定未通过回读")

    span = p.local_span_deg
    rows = []
    cal = dict(accepted=False, burst_command_deg=best, lockin_phase_deg=settings["phase_deg"],
               coarse_fit=prediction, points=rows)
    ctx.state["calibration"] = cal
    values, times, centers, carrier_levels = [], [], [], []
    for i, delta in enumerate((0., -span, span, 0., span / 2, -span / 2, 0.)):
        command = carrier.burst_phase(best + delta)
        scope_label, xy_label = f"local_{i:02d}_scope", f"local_{i:02d}_xy"
        scope = _electrical(ctx, scope_label, dict(mode="burst", command_deg=command))
        center = _require_stable(scope)
        now = time.monotonic() - start
        rows.append(dict(burst_command_deg=command, requested_offset_deg=delta,
                         scope_label=scope_label, xy_label=xy_label, elapsed_s=now, **scope))
        value = _xy(ctx, xy_label, p.static_point_duration_s, lockin=settings, burst_command_deg=command,
                    carrier_center_deg=center, scope_label=scope_label)
        values.append(value)
        times.append(now)
        centers.append(center)
        carrier_levels.append(scope["carrier_v_pk"])
        rows[-1].update(x_mean_v=value.real, y_mean_v=value.imag)
    target_center, target_v_pk = centers[-1], float(np.median(carrier_levels))
    if np.max(np.abs(np.asarray(carrier_levels) / target_v_pk - 1.)) > _CARRIER_FRACTION_TOL:
        raise SafetyAbort("Burst 局部扫描的线圈监视幅度变化超过 5%")
    fitted = signal_fit.local_slopes(target_center, centers, times, values, span)
    cal.update(fitted, carrier_center_deg=target_center, carrier_v_pk=target_v_pk)
    k = fitted["k0_v_per_rad"]
    if (abs(k) < max(1e-6, 5 * fitted["k0_se_v_per_rad"]) or abs(fitted["slope_x_v_per_rad"]) > 0.15 * abs(k)
            or max(abs(fitted[name] - k) for name in ("outer_v_per_rad", "inner_v_per_rad")) > 0.2 * abs(k)):
        raise SafetyAbort("局部 K0 信噪比、Y 投影或内外两档线性一致性不足")
    cal["accepted"] = True
    ctx.save()
    report_runtime_progress("A03", f"静态局部斜率 {k:.5g} V/rad，本轮工作点 {target_center:.3f}°", 35,
                            estimated_remaining_seconds=None)
    return cal, settings


def _electrical_reason(point, calibration, anchor, *, pm):
    if not point["stable"]:
        return "帧内载波中心或拟合不稳定"
    center = point["carrier_center_deg"]
    if abs(signal_fit.wrap_deg(center - calibration["carrier_center_deg"])) > _CENTER_TOL_DEG:
        return "实测载波中心偏离 Burst 工作点超过 2°"
    if anchor is not None and abs(signal_fit.wrap_deg(center - anchor)) > _ANCHOR_TOL_DEG:
        return "实测载波中心与对准锚点相差超过 1°"
    if not math.isclose(point["carrier_v_pk"], calibration["carrier_v_pk"], rel_tol=_CARRIER_FRACTION_TOL):
        return "载波监视幅度与 Burst 工作点相差超过 5%"
    pm_deg = math.degrees(point["phase_pk_rad"])
    if pm and (abs(pm_deg - PM_DEPTH_DEG) > _DEPTH_TOL_DEG or math.degrees(point["phase_scatter_rad"]) > 0.2):
        return "实测 PM 峰值或帧内相量重复性超限"
    if not pm and pm_deg > _DEPTH_TOL_DEG:
        return "无调制对照在锚点频率仍检出超过 0.1° 的 PM"
    if point["am_fraction_pk"] > _AM_MAX_FRACTION:
        return "残余 AM 超过 0.1% 峰值"
    return ""


def _align_pm(ctx, carrier, cal):
    a = _electrical(ctx, "pm_align_start", carrier.begin_pm(), _PM_ALIGN_HZ)
    phase_a = _require_stable(a)
    b = _electrical(ctx, "pm_align_nudge", carrier.phase_adjust(carrier.command_deg + _NUDGE_DEG, "pm"), _PM_ALIGN_HZ)
    phase_b = _require_stable(b)
    measured = signal_fit.wrap_deg(phase_b - phase_a)
    ctx.state["pm_alignment"] = dict(start=a, nudge=b, command_delta_deg=_NUDGE_DEG, measured_delta_deg=measured)
    if abs(abs(measured) - _NUDGE_DEG) > 3.:
        raise SafetyAbort("原生 PM 开启时载波调整未按实测跟随，不能回调工作点")
    gain = measured / _NUDGE_DEG
    command = carrier.command_deg + signal_fit.wrap_deg(cal["carrier_center_deg"] - phase_b) / gain
    point = _electrical(ctx, "pm_align_verified", carrier.phase_adjust(command, "pm"), _PM_ALIGN_HZ)
    ctx.state["pm_alignment"].update(aligned=point, command_deg=carrier.command_deg)
    if reason := _electrical_reason(point, cal, None, pm=True):
        raise SafetyAbort(f"原生 PM 回调后拒绝：{reason}")
    return point["carrier_center_deg"]


def _sweep(ctx, carrier, cal, lockin, anchor):
    frequencies = ctx.params.sweep_order_hz
    start = time.monotonic()
    for i, f_hz in enumerate(frequencies):
        settings = carrier.frequency(f_hz)
        label = f"pm_{i:02d}"
        row = dict(frequency_hz=f_hz, repeat=i == len(frequencies) - 1,
                   scope_label=label + "_scope", xy_label=None, accepted=False, reason="尚未完成")
        ctx.state["transfer"].append(row)
        try:
            scope = _electrical(ctx, row["scope_label"], settings, f_hz)
        except ValueError as exc:
            row["reason"] = str(exc)
        else:
            row.update(scope, center_error_deg=float(signal_fit.wrap_deg(scope["carrier_center_deg"] - cal["carrier_center_deg"])))
            row["reason"] = _electrical_reason(scope, cal, anchor, pm=True)
            if not row["reason"]:
                row["xy_label"] = label + "_xy"
                _xy(ctx, row["xy_label"], ctx.params.pm_tone_duration_s, lockin=lockin, frequency_hz=f_hz,
                    scope_label=row["scope_label"])
                row["accepted"] = True
        ctx.save()
        done = i + 1
        report_runtime_progress("A03", f"基带频响 {done}/{len(frequencies)}，{f_hz:g} Hz：{row['reason'] or '电学与时序通过'}",
                                40 + 40 * done / len(frequencies),
                                estimated_remaining_seconds=(time.monotonic() - start) / done * (len(frequencies) - done),
                                eta_scope="当前基带扫频")


def _baseline(ctx, carrier, cal, lockin, anchor):
    f_hz = ctx.params.pm_anchor_hz
    settings = carrier.stop_pm()
    state = dict(accepted=False, reason="未完成基线检查", scope_labels=[], xy_label=None)
    ctx.state["noise"] = state
    label = "noise_candidate_scope"
    candidate = _electrical(ctx, label, settings, f_hz)
    state["candidate"] = candidate
    previous = _require_stable(candidate)
    if abs(signal_fit.wrap_deg(previous - cal["carrier_center_deg"])) > _ANCHOR_TOL_DEG or abs(signal_fit.wrap_deg(previous - anchor)) > _ANCHOR_TOL_DEG:
        probe = _electrical(ctx, "noise_nudge_scope", carrier.phase_adjust(carrier.command_deg + _NUDGE_DEG, "off"), f_hz)
        moved = signal_fit.wrap_deg(_require_stable(probe) - previous)
        state["nudge"] = dict(probe, measured_delta_deg=moved)
        if abs(abs(moved) - _NUDGE_DEG) > 3.:
            state["reason"] = "PM OFF 时相位调整未通过实测跟随"
            return
        goal = carrier.command_deg + signal_fit.wrap_deg(cal["carrier_center_deg"] - probe["carrier_center_deg"]) / (moved / _NUDGE_DEG)
        label = "noise_aligned_scope"
        candidate = _electrical(ctx, label, carrier.phase_adjust(goal, "off"), f_hz)
    state.update(before=candidate, scope_labels=[label])
    if reason := _electrical_reason(candidate, cal, anchor, pm=False):
        state["reason"] = reason
        return
    state["xy_label"] = "noise_xy"
    mean = _xy(ctx, "noise_xy", ctx.params.noise_duration_s, lockin=lockin, source_mode="unmodulated_carrier")
    state.update(mean_x_v=mean.real, mean_y_v=mean.imag)
    after = _electrical(ctx, "noise_after_scope", carrier.verify_unmodulated(), f_hz)
    state.update(after=after, scope_labels=[label, "noise_after_scope"])
    reason = _electrical_reason(after, cal, anchor, pm=False)
    if not reason and abs(signal_fit.wrap_deg(after["carrier_center_deg"] - candidate["carrier_center_deg"])) > _ANCHOR_TOL_DEG:
        reason = "噪声前后载波中心改变超过 1°"
    if not reason and not math.isclose(after["carrier_v_pk"], candidate["carrier_v_pk"], rel_tol=_CARRIER_FRACTION_TOL):
        reason = "噪声前后载波幅度不一致"
    state.update(accepted=not reason, reason=reason)


def measure(ctx, carrier):
    try:
        cal, lockin = _calibrate(ctx, carrier)
        anchor = _align_pm(ctx, carrier, cal)
        _sweep(ctx, carrier, cal, lockin, anchor)
        _baseline(ctx, carrier, cal, lockin, anchor)
    except ValueError as exc:
        raise SafetyAbort(str(exc)) from exc
    ctx.outputs["相位灵敏度"] = "数据已读入；频响、低频一致性和噪声折算资格见离线分析"
    report_runtime_progress("A03", "完成原始采集，开始安全恢复", 90, estimated_remaining_seconds=None)
