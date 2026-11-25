"""同一轮四段采集：MFLI MF-MOD 整周调相、纯锁相局部 K₀ 标定、相干边带 PM 频响与无调制噪声。"""
from __future__ import annotations

import math
import time

import numpy as np
from lockin_amplifier import DAQConfig, daq, demod

from ...common import load_safety_limits, validate_safety_limit
from ...experiment_runtime import check_cancelled, report_runtime_progress
from ...steps import DeferredAcquisitionWrites, acquisition_window
from ...steps.coil_drive_chain import SafetyAbort, sleep
from . import preparation, run_format, signal_fit
from .models import MOD_CARRIER_OSC_IDX, MOD_IDX, MOD_SIDEBAND_OSC_IDX, PM_DEPTH_DEG

_STATIC_GRID_DEG = tuple(range(0, 360, 15))
# 局部标定：中心 → 在 ±跨度内等分加密细扫 → 中心；首尾中心用于核对工作点漂移。
_LOCAL_STEPS_PER_SIDE = 16


def local_offsets(span_deg):
    """返回中心、等分细扫整个 ±跨度、中心组成的局部标定角度序列。"""
    steps = _LOCAL_STEPS_PER_SIDE
    step = span_deg / steps
    sweep = tuple(-span_deg + index * step for index in range(2 * steps + 1))
    return (0.0, *sweep, 0.0)


class Carrier:
    """控制 MFLI Signal Output 1 与 MF-MOD 调制器，并作全量节点回读验证。"""

    def __init__(self, ctx):
        self.ctx = ctx
        self.device = ctx.devices["hf2"]
        self.dev = self.device.device_id
        self.safety_key = preparation.mfli_sigout_safety_key(ctx.mapping)
        self.command_deg = 0.0
        self.fm_hz = float(ctx.params.sweep_order_hz[0])
        self.sidebands_enabled = False
        self.output_range_v = preparation.select_mfli_output_range(ctx.params)

    def _sigout(self, node: str) -> str:
        return f"/{self.dev}/sigouts/0/{node}"

    def _mod(self, node: str) -> str:
        return f"/{self.dev}/mods/{MOD_IDX}/{node}"

    def _safe(self, *, pm_enabled: bool):
        check_cancelled()
        limits = load_safety_limits(self.ctx.root)
        y_min, y_max = self.ctx.params.y_output_envelope(pm_enabled=pm_enabled)
        for v in (0.0, float(self.ctx.params.y_bias_v), y_min, y_max):
            validate_safety_limit(self.safety_key, v, limits)

    def _verify(self, *, output: bool, sidebands: bool):
        self.device.sync()
        p = self.ctx.params
        got = dict(
            extclk=bool(self.device.get_extclk()),
            output=bool(self.device.get_int(self._sigout("on"))),
            range_v=float(self.device.get_double(self._sigout("range"))),
            offset_v=float(self.device.get_double(self._sigout("offset"))),
            add_enabled=bool(self.device.get_int(self._sigout("add"))),
            mod_enable=bool(self.device.get_int(self._mod("enable"))),
            mode=int(self.device.get_int(self._mod("mode"))),
            freqdevenable=int(self.device.get_int(self._mod("freqdevenable"))),
            mod_output=int(self.device.get_int(self._mod("output"))),
            carrier_enable=bool(self.device.get_int(self._mod("carrier/enable"))),
            carrier_osc=int(self.device.get_int(self._mod("carrier/oscselect"))),
            carrier_hz=float(self.device.get_double(f"{self.device.osc_path(MOD_CARRIER_OSC_IDX)}/freq")),
            carrier_vpk=float(self.device.get_double(self._mod("carrier/amplitude"))),
            command_deg=float(self.device.get_double(self._mod("carrier/phaseshift"))),
            sideband0_enable=bool(self.device.get_int(self._mod("sidebands/0/enable"))),
            sideband1_enable=bool(self.device.get_int(self._mod("sidebands/1/enable"))),
            sideband0_osc=int(self.device.get_int(self._mod("sidebands/0/oscselect"))),
            sideband1_osc=int(self.device.get_int(self._mod("sidebands/1/oscselect"))),
            mod_index_rad=float(self.device.get_double(self._mod("index"))),
            fm_hz=float(self.device.get_double(f"{self.device.osc_path(MOD_SIDEBAND_OSC_IDX)}/freq")),
        )
        valid = (
            got["extclk"]
            and got["output"] is output
            and not got["add_enabled"]
            and got["range_v"] >= max(abs(v) for v in p.y_output_envelope(pm_enabled=sidebands)) - 1e-9
            and math.isclose(got["offset_v"], p.y_bias_v, abs_tol=1e-4)
            and got["mod_enable"]
            and got["mode"] == 1
            and got["freqdevenable"] == 0
            and got["mod_output"] == 1
            and got["carrier_enable"]
            and got["carrier_osc"] == MOD_CARRIER_OSC_IDX
            and math.isclose(got["carrier_hz"], p.y_rf_freq_hz, rel_tol=1e-6)
            and math.isclose(got["carrier_vpk"], p.y_carrier_vpk, abs_tol=1e-5)
            and abs(signal_fit.wrap_deg(got["command_deg"] - self.command_deg)) <= 0.02
            and got["sideband0_osc"] == MOD_SIDEBAND_OSC_IDX
            and got["sideband1_osc"] == MOD_SIDEBAND_OSC_IDX
            and got["sideband0_enable"] is sidebands
            and got["sideband1_enable"] is sidebands
            and math.isclose(got["mod_index_rad"], p.y_pm_depth_rad, abs_tol=1e-4)
        )
        if sidebands:
            valid = valid and math.isclose(got["fm_hz"], self.fm_hz, abs_tol=1e-3)
        if not valid:
            mode_name = "pm" if sidebands else "carrier"
            raise SafetyAbort(f"MFLI MF-MOD {mode_name} 仪器回读不符：{got}")
        return got

    def begin_carrier(self):
        """先停输出并配置 MF-MOD（FM index 模式，闭环载波，初始关闭边带），再开启 Out1。"""
        self._safe(pm_enabled=False)
        if int(self.device.get_int(self._sigout("add"))) != 0:
            raise SafetyAbort("MFLI Signal Output 1 开启了外部 Add 叠加，无法确保总电压安全")
        p = self.ctx.params
        self.command_deg = 0.0
        self.fm_hz = float(p.sweep_order_hz[0])
        self.sidebands_enabled = False
        self.device.set_int(self._sigout("on"), 0)
        self.device.set_int(self._mod("enable"), 0)
        self.device.set_double(self._sigout("range"), self.output_range_v)
        self.device.set_double(self._sigout("offset"), float(p.y_bias_v))
        self.device.set_int(self._mod("mode"), 1)
        self.device.set_int(self._mod("freqdevenable"), 0)
        self.device.set_int(self._mod("carrier/oscselect"), MOD_CARRIER_OSC_IDX)
        self.device.set_double(f"{self.device.osc_path(MOD_CARRIER_OSC_IDX)}/freq", float(p.y_rf_freq_hz))
        self.device.set_int(self._mod("sidebands/0/oscselect"), MOD_SIDEBAND_OSC_IDX)
        self.device.set_int(self._mod("sidebands/1/oscselect"), MOD_SIDEBAND_OSC_IDX)
        self.device.set_double(f"{self.device.osc_path(MOD_SIDEBAND_OSC_IDX)}/freq", self.fm_hz)
        self.device.set_double(self._mod("index"), float(p.y_pm_depth_rad))
        self.device.set_double(self._mod("carrier/amplitude"), float(p.y_carrier_vpk))
        self.device.set_double(self._mod("carrier/phaseshift"), 0.0)
        self.device.set_int(self._mod("carrier/enable"), 1)
        self.device.set_int(self._mod("sidebands/0/enable"), 0)
        self.device.set_int(self._mod("sidebands/1/enable"), 0)
        self.device.set_int(self._mod("output"), 1)
        self.device.set_int(self._mod("enable"), 1)
        self.device.set_int(self._sigout("on"), 1)
        self._verify(output=True, sidebands=False)
        self.ctx.outputs["Y 射频"] = (
            f"MFLI Out1 连续载波（MOD 无调制），{p.y_carrier_vpk:g} Vpk，"
            f"偏置 {p.y_bias_v:+g} V，指令相位 0°"
        )
        return self.command_deg

    def set_phase(self, phase_deg):
        """在输出开启状态下连续改写 MOD 载波相移并回读确认。"""
        self._safe(pm_enabled=self.sidebands_enabled)
        self.command_deg = float(phase_deg) % 360.0
        self.device.set_double(self._mod("carrier/phaseshift"), self.command_deg)
        self._verify(output=True, sidebands=self.sidebands_enabled)
        tag = f"PM {self.fm_hz:g} Hz" if self.sidebands_enabled else "MOD 无调制"
        self.ctx.outputs["Y 射频"] = f"MFLI Out1（{tag}），指令相位 {self.command_deg:g}°"
        return self.command_deg

    def phase_adjust(self, phase_deg):
        """微调载波相移并返回完整回读字典。"""
        self._safe(pm_enabled=self.sidebands_enabled)
        self.command_deg = float(phase_deg) % 360.0
        self.device.set_double(self._mod("carrier/phaseshift"), self.command_deg)
        return self._verify(output=True, sidebands=self.sidebands_enabled)

    def begin_pm(self):
        """保持载波与相移不变，使能 2° 峰值相干边带 PM。"""
        self._safe(pm_enabled=True)
        self.fm_hz = float(self.ctx.params.sweep_order_hz[0])
        self.sidebands_enabled = True
        self.device.set_double(f"{self.device.osc_path(MOD_SIDEBAND_OSC_IDX)}/freq", self.fm_hz)
        self.device.set_double(self._mod("index"), float(self.ctx.params.y_pm_depth_rad))
        self.device.set_int(self._mod("sidebands/0/enable"), 1)
        self.device.set_int(self._mod("sidebands/1/enable"), 1)
        return self._verify(output=True, sidebands=True)

    def frequency(self, f_hz):
        """切换 Osc 3 调制基带频率并回读确认。"""
        self._safe(pm_enabled=True)
        if f_hz != self.fm_hz:
            self.fm_hz = float(f_hz)
            self.device.set_double(f"{self.device.osc_path(MOD_SIDEBAND_OSC_IDX)}/freq", self.fm_hz)
        self.ctx.outputs["Y 射频"] = f"MFLI MF-MOD 相干边带 PM {self.fm_hz:g} Hz，深度 {PM_DEPTH_DEG:g}° 峰值"
        return self._verify(output=True, sidebands=True)

    def stop_pm(self):
        """关闭上下边带回到无调制连续载波，保持相移与幅值不变。"""
        self._safe(pm_enabled=False)
        self.sidebands_enabled = False
        self.device.set_int(self._mod("sidebands/0/enable"), 0)
        self.device.set_int(self._mod("sidebands/1/enable"), 0)
        self.ctx.outputs["Y 射频"] = (
            f"MFLI Out1 连续载波（MOD 无调制），{self.ctx.params.y_carrier_vpk:g} Vpk，指令相位 {self.command_deg:g}°"
        )
        return self._verify(output=True, sidebands=False)

    def verify_unmodulated(self):
        return self._verify(output=True, sidebands=False)


def lockin_readback(ctx, *, dynamic: bool = False):
    """回读实际参考、滤波与传输，确认原子读出锁定在 Demod 4 + Osc 2，禁止采集中替换链路。"""
    d, p = ctx.devices["hf2"], ctx.params
    base = d.demod_path(p.demod0_idx)
    expected_tc = p.demod0_dynamic_tc_s if dynamic else p.demod0_tc_s
    min_rate = 10 * max(p.pm_frequencies_hz) if dynamic else 0.0
    state = dict(
        phase_deg=float(d.get_double(f"{base}/phaseshift")),
        time_constant_s=float(d.get_double(f"{base}/timeconstant")),
        stream_rate_sa_s=float(d.get_double(f"{base}/rate")),
        order=int(d.get_int(f"{base}/order")),
        oscillator_hz=float(d.get_double(f"{d.osc_path(MOD_CARRIER_OSC_IDX)}/freq")),
    )
    if (not d.get_extclk() or not all(math.isfinite(v) for v in state.values())
            or state["stream_rate_sa_s"] <= 0
            or state["stream_rate_sa_s"] < min_rate
            or not math.isclose(state["oscillator_hz"], p.y_rf_freq_hz, rel_tol=1e-6)
            or not math.isclose(state["time_constant_s"], expected_tc, rel_tol=0.01)
            or state["order"] != p.demod0_order
            or any(d.get_int(f"{base}/{node}") != value for node, value in
                   (("adcselect", 0), ("oscselect", MOD_CARRIER_OSC_IDX), ("harmonic", 1), ("enable", 1)))):
        raise SafetyAbort(f"MFLI Demod{p.demod0_idx + 1} 输入、参考、解调滤波或采样率回读不符：{state}")
    return state


def _save(ctx, label, kind, metadata, **arrays):
    path = run_format.write_frame(ctx.directory.root, label, kind, metadata, **arrays)
    ctx.state["data_files"].append(path)


def _daq(ctx, duration_s, lockin, *, requested_rate):
    rate = lockin["stream_rate_sa_s"]
    d, p = ctx.devices["hf2"], ctx.params
    cols = round(rate * duration_s)
    cfg = DAQConfig(device=d.device_id, duration=duration_s, grid_cols=cols, grid_rows=1, grid_mode=4,
                    signal_paths=["sample.x", "sample.y"])
    results = daq.acquire_data(d, cfg, p.demod0_idx, rate, timeout=duration_s + 15.,
                               requested_rate=requested_rate, cancellation_check=check_cancelled, strict_cleanup=True)
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


def _xy(ctx, label, duration_s, *, lockin=None, dynamic=False, **record):
    """仪器设置在关温控前，缓冲数据仅在开温控后保存。"""
    before = lockin_readback(ctx, dynamic=dynamic)
    if lockin is not None and before != lockin:
        raise SafetyAbort("MFLI 配置发生变化，拒绝并入同轮标定")
    temperature_on = True
    requested_rate = ctx.params.demod0_dynamic_rate_sa_s if dynamic else ctx.params.demod0_rate_sa_s

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
            metadata, arrays = _daq(ctx, duration_s, before, requested_rate=requested_rate)
            pending.add(lambda meta, **data: _save(ctx, label, "xy", meta, **data), dict(metadata, **record), **arrays)
        sleep(ctx.params.temp_switch_on_lag_s)
        if lockin_readback(ctx, dynamic=dynamic) != before:
            raise SafetyAbort("MFLI 采集前后回读不一致；原始记录仅供诊断")
    return complex(np.mean(arrays["x_v"]), np.mean(arrays["y_v"]))


def _unique_warnings(items):
    return list(dict.fromkeys(item for item in items if item))


def phase_response(ctx, carrier):
    """整周调相扫描找最大切线，并把 MFLI Demod 4 解调相位转到该切线。"""
    p = ctx.params
    start = time.monotonic()
    coarse = []
    coarse_lockin = lockin_readback(ctx)
    for i, phase in enumerate(_STATIC_GRID_DEG):
        carrier.set_phase(phase)
        coarse.append(_xy(ctx, f"coarse_{i:02d}", p.static_point_duration_s, lockin=coarse_lockin, burst_command_deg=phase))
        done = i + 1
        report_runtime_progress("A03", f"扫描原子相位 {done}/{len(_STATIC_GRID_DEG)}", 10 + 80 * done / len(_STATIC_GRID_DEG),
                                estimated_remaining_seconds=(time.monotonic() - start) / done * (len(_STATIC_GRID_DEG) - done),
                                data={"eta_scope": "当前静态扫描"})
    best, rotation, prediction = signal_fit.best_tangent(_STATIC_GRID_DEG, coarse)
    d = ctx.devices["hf2"]
    setpoint = float(signal_fit.wrap_deg(lockin_readback(ctx)["phase_deg"] + rotation))
    demod.set_demod_phase(d, p.demod0_idx, setpoint)
    settings = lockin_readback(ctx)
    if abs(signal_fit.wrap_deg(settings["phase_deg"] - setpoint)) > 0.5:
        raise SafetyAbort("MFLI 最大切线转到 Y 的相位设定未通过回读")
    ctx.state["phase_response"] = dict(
        burst_command_deg=best,
        carrier_command_deg=best,
        rotation_deg=rotation,
        coarse_fit=prediction,
        lockin_phase_deg=settings["phase_deg"],
        lockin=settings,
        grid_deg=list(_STATIC_GRID_DEG),
        points=len(coarse),
    )
    ctx.save()
    ctx.outputs["相位灵敏度"] = f"整周扫描完成，工作点 {best:.3f}°；待局部 K₀ 标定"
    report_runtime_progress("A03", f"整周扫描完成，工作点 {best:.3f}°", 95, estimated_remaining_seconds=None)


def local_calibration(ctx, carrier):
    """在整周工作点两侧以 MFLI 指令角加密细扫，线性回归标定本轮 K₀。"""
    p = ctx.params
    phase = ctx.state.get("phase_response") or {}
    best = phase.get("burst_command_deg")
    if best is None:
        raise SafetyAbort("缺少本轮整周扫描结果；A04 不能跨轮接在 A03 之前")
    settings = lockin_readback(ctx)
    if abs(signal_fit.wrap_deg(settings["phase_deg"] - phase["lockin_phase_deg"])) > 0.5:
        raise SafetyAbort("MFLI 解调相位与整周扫描结束时不一致；请整链重跑")
    start = time.monotonic()
    span = p.local_span_deg
    rows = []
    cal = dict(accepted=False, burst_command_deg=best, carrier_command_deg=best,
               lockin_phase_deg=settings["phase_deg"], coarse_fit=phase.get("coarse_fit"), points=rows)
    ctx.state["calibration"] = cal
    values, times, centers = [], [], []
    warnings = []
    offsets = local_offsets(span)
    for i, delta in enumerate(offsets):
        command = carrier.set_phase(best + delta)
        now = time.monotonic() - start
        xy_label = f"local_{i:02d}_xy"
        value = _xy(ctx, xy_label, p.static_point_duration_s, lockin=settings,
                    burst_command_deg=command, carrier_command_deg=command)
        rows.append(dict(burst_command_deg=command, carrier_command_deg=command, requested_offset_deg=delta,
                         xy_label=xy_label, elapsed_s=now, x_mean_v=value.real, y_mean_v=value.imag))
        values.append(value)
        times.append(now)
        centers.append(command)
        done = i + 1
        report_runtime_progress("A04", f"局部标定 {done}/{len(offsets)}", 5 + 85 * done / len(offsets),
                                estimated_remaining_seconds=(time.monotonic() - start) / done * (len(offsets) - done),
                                data={"eta_scope": "当前局部标定"})
    center_indices = (0, len(offsets) - 1)
    sweep_center = float(best % 360.0)
    try:
        fitted = signal_fit.local_slopes(sweep_center, centers, times, values, span, center_indices=center_indices)
    except ValueError as exc:
        raise SafetyAbort(str(exc)) from exc
    warnings.extend(fitted.pop("local_warnings", ()))
    offset_deg = float(np.clip(fitted.get("eq222_phi_offset_deg") or 0.0, -span, span))
    refined_center = float(carrier.set_phase((sweep_center + offset_deg) % 360.0))
    cal.update(fitted, carrier_center_deg=refined_center, carrier_v_pk=float(p.y_carrier_vpk))
    k = fitted["k0_v_per_rad"]
    if not math.isfinite(k) or abs(k) < 1e-6:
        raise SafetyAbort("局部 K0 无效或接近零，无法作为标定斜率")
    if abs(k) < 5 * fitted["k0_se_v_per_rad"]:
        warnings.append("局部 K0 拟合信噪比低于 5")
    if abs(fitted["slope_x_v_per_rad"]) > 0.15 * abs(k):
        warnings.append("局部 X 残留正交分量超过 15%")
    warnings = _unique_warnings(warnings)
    cal["accepted"] = True
    cal["quality_passed"] = not warnings
    cal["quality_warnings"] = warnings
    cal["reason"] = "；".join(warnings)
    ctx.save()
    suffix = f"（含 {len(warnings)} 项质检告警）" if warnings else ""
    ctx.outputs["相位灵敏度"] = f"局部 K₀ = {k:.5g} V/rad，工作点更新为 {refined_center:.3f}°{suffix}"
    report_runtime_progress("A04", f"局部 K₀ {k:.5g} V/rad，工作点 {refined_center:.3f}°{suffix}", 95,
                            estimated_remaining_seconds=None)
    return cal


def _sweep(ctx, carrier, cal, lockin):
    frequencies = ctx.params.sweep_order_hz
    start = time.monotonic()
    for i, f_hz in enumerate(frequencies):
        settings = carrier.frequency(f_hz)
        pm_rad = float(settings["mod_index_rad"])
        if not math.isfinite(pm_rad) or pm_rad <= 0:
            raise SafetyAbort(f"MFLI MF-MOD 调相深度回读无效：{pm_rad}")
        xy_label = f"pm_{i:02d}_xy"
        row = dict(frequency_hz=f_hz, repeat=False, scope_label=None, xy_label=None,
                   phase_pk_rad=pm_rad, modulation_hz=f_hz, electrical_accepted=True,
                   accepted=False, reason="尚未完成")
        ctx.state["transfer"].append(row)
        _xy(ctx, xy_label, ctx.params.pm_tone_duration_s, lockin=lockin, dynamic=True,
            frequency_hz=f_hz, phase_pk_rad=pm_rad)
        row.update(xy_label=xy_label, accepted=True, reason="")
        ctx.save()
        done = i + 1
        report_runtime_progress("A05", f"基带频响 {done}/{len(frequencies)}，{f_hz:g} Hz：已采 XY",
                                15 + 75 * done / len(frequencies),
                                estimated_remaining_seconds=(time.monotonic() - start) / done * (len(frequencies) - done),
                                data={"eta_scope": "当前基带扫频"})


def _baseline(ctx, carrier, lockin):
    report_runtime_progress("A06", "关闭调制边带保留 5 kHz 连续载波", 10, estimated_remaining_seconds=None)
    carrier.stop_pm()
    state = dict(accepted=False, electrical_accepted=True, quality_passed=False, quality_warnings=[],
                 reason="未完成基线采集", scope_labels=[], xy_label=None)
    ctx.state["noise"] = state
    report_runtime_progress("A06", "采集无调制基带记录", 40, estimated_remaining_seconds=None)
    mean = _xy(ctx, "noise_xy", ctx.params.noise_duration_s, lockin=lockin, dynamic=True,
               source_mode="unmodulated_carrier")
    after = carrier.verify_unmodulated()
    state.update(xy_label="noise_xy", mean_x_v=mean.real, mean_y_v=mean.imag,
                 carrier_command_deg=after["command_deg"], accepted=True,
                 quality_passed=True, quality_warnings=[], reason="")


def _calibration_state(ctx, *, dynamic: bool = False):
    cal = ctx.state.get("calibration") or {}
    if cal.get("accepted") is not True:
        raise SafetyAbort("缺少本轮合格的局部 K₀ 标定；请先运行 A04")
    lockin = lockin_readback(ctx, dynamic=dynamic)
    if abs(signal_fit.wrap_deg(lockin["phase_deg"] - cal["lockin_phase_deg"])) > 0.5:
        raise SafetyAbort("MFLI 解调相位与本轮标定不一致；请整链重跑")
    return cal, lockin


def frequency_response(ctx):
    try:
        cal, _ = _calibration_state(ctx, dynamic=False)
        preparation.configure_dynamic_lockin(ctx, phase_deg=float(cal["lockin_phase_deg"]))
        _, lockin = _calibration_state(ctx, dynamic=True)
        pm_settings = ctx.carrier.begin_pm()
        pm_rad = float(pm_settings["mod_index_rad"])
        ctx.state["pm_alignment"] = dict(skipped=True, method="mf_mod_coherent_direct",
                                         command_deg=pm_settings["command_deg"], index_rad=pm_rad,
                                         quality_passed=True, quality_warnings=[], reason="")
        ctx.save()
        _sweep(ctx, ctx.carrier, cal, lockin)
    except ValueError as exc:
        raise SafetyAbort(str(exc)) from exc
    ctx.outputs["相位灵敏度"] = "基带频率响应采集完成；待无调制噪声基线"
    report_runtime_progress("A05", "基带频响完成", 95, estimated_remaining_seconds=None)


def noise_measurement(ctx):
    try:
        _, lockin = _calibration_state(ctx, dynamic=True)
        if not any(row.get("accepted") for row in ctx.state.get("transfer") or ()):
            raise SafetyAbort("缺少本轮有效频响采集记录；请先运行 A05")
        _baseline(ctx, ctx.carrier, lockin)
    except ValueError as exc:
        raise SafetyAbort(str(exc)) from exc
    ctx.outputs["相位灵敏度"] = "数据已读入；频响与相位噪声详情见离线分析"
    report_runtime_progress("A06", "完成原始采集，开始安全恢复", 95, estimated_remaining_seconds=None)
