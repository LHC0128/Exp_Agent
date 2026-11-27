"""MFLI MF-MOD 射频相位扫描与局部 K₀ 标定的公共采集编排。

整周调相扫描、把 Demod 4 解调相位转到切线、局部加密细扫与理论式 (2.2.2) 斜率标定
只通过回调写盘与开关温控：调用方传入 ``save_frame(root, label, kind, metadata, **arrays)``、
``write_temperature(ctx, enabled)`` 与 ``wait(seconds)``，并用关键字参数指定 stage 标签、
状态键与输出标签。本模块不导入任何具体实验包。
"""
from __future__ import annotations

import math
import time

import numpy as np
from lockin_amplifier import DAQConfig, daq, demod

from ...common import load_safety_limits, validate_safety_limit
from ...experiment_runtime import check_cancelled, report_runtime_progress
from ...steps import DeferredAcquisitionWrites, acquisition_window
from ...steps.coil_drive_chain import SafetyAbort, sleep
from . import signal_fit
from .keys import LOCKIN_KEY

# MFLI MF-MOD 固定占用：MOD 0 的载波接 Osc 2（索引 1），上下边带共用 Osc 3（索引 2）。
MOD_IDX = 0
MOD_CARRIER_OSC_IDX = 1
MOD_SIDEBAND_OSC_IDX = 2
# 2° 峰值相干边带深度与 MFLI Signal Output 1 最大量程，取本实验当前取值作为共享默认。
PM_DEPTH_DEG = 2.0
MFLI_MAX_SIGOUT_V = 10.0
_MFLI_OUTPUT_RANGES_V = (0.01, 0.1, 1.0, 10.0)
DEFAULT_STATIC_GRID_DEG = tuple(range(0, 360, 15))
# 局部标定：中心 → 在 ±跨度内等分加密细扫 → 中心；首尾中心用于核对工作点漂移。
LOCAL_STEPS_PER_SIDE = 16


def oscillator_layout(ctx) -> tuple[int, tuple[int, int], int]:
    """返回 (载波振荡器索引, 两个边带振荡器索引, 读出振荡器索引).

    默认沿用历史布局（载波 Osc 2、边带 Osc 3、读出与载波同源）；实验可在 ctx 上设置
    mod_carrier_osc_idx / mod_sideband_osc_idxs / readout_osc_idx 覆盖，
    以统一 Demod n ↔ Osc n 编号。
    """
    carrier = int(getattr(ctx, "mod_carrier_osc_idx", MOD_CARRIER_OSC_IDX))
    sidebands = tuple(int(item) for item in getattr(
        ctx, "mod_sideband_osc_idxs", (MOD_SIDEBAND_OSC_IDX, MOD_SIDEBAND_OSC_IDX)))
    if len(sidebands) != 2:
        raise ValueError("边带振荡器必须恰好两个")
    readout = int(getattr(ctx, "readout_osc_idx", carrier))
    return carrier, sidebands, readout


def local_offsets(span_deg):
    """返回中心、等分细扫整个 ±跨度、中心组成的局部标定角度序列。"""
    steps = LOCAL_STEPS_PER_SIDE
    step = span_deg / steps
    sweep = tuple(-span_deg + index * step for index in range(2 * steps + 1))
    return (0.0, *sweep, 0.0)


def mfli_sigout_safety_key(mapping: dict) -> str:
    """按锁相设备库 ID 生成 Signal Output 1 安全限值键名。"""
    lockin_cfg = mapping.get(LOCKIN_KEY) or {}
    device_id = str(lockin_cfg.get("device_library_id") or lockin_cfg.get("device_id") or "").strip()
    if not device_id:
        raise ValueError("lockin_r 映射缺少有效的 device_library_id")
    return f"{device_id}_signal_output_0"


def mfli_output_levels(params) -> tuple[float, ...]:
    """返回 MFLI Signal Output 1 需经限值校验的全部电压点（包含 2° PM 边带包络）。"""
    y_min, y_max = params.y_output_envelope(pm_enabled=True)
    return (0.0, float(params.y_bias_v), y_min, y_max)


def select_mfli_output_range(params) -> float:
    """选择能完整容纳直流偏置与调相边带包络的最小 MFLI Signal Output 1 量程。"""
    peak = max(abs(value) for value in mfli_output_levels(params))
    for option in _MFLI_OUTPUT_RANGES_V:
        if peak <= option + 1e-12:
            return float(option)
    raise ValueError(f"Y 射频与偏置总包络峰值 {peak:g} V 超出 MFLI Signal Output 1 最大 {MFLI_MAX_SIGOUT_V:g} V 量程")


class Carrier:
    """控制 MFLI Signal Output 1 与 MF-MOD 调制器，并作全量节点回读验证。"""

    def __init__(self, ctx):
        self.ctx = ctx
        self.device = ctx.devices["hf2"]
        self.dev = self.device.device_id
        self.safety_key = mfli_sigout_safety_key(ctx.mapping)
        self.carrier_osc_idx, self.sideband_osc_idxs, self.readout_osc_idx = oscillator_layout(ctx)
        self.command_deg = 0.0
        self.fm_hz = float(ctx.params.sweep_order_hz[0])
        self.sidebands_enabled = False
        self.output_range_v = select_mfli_output_range(ctx.params)

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
        carrier_osc, sideband_oscs, readout_osc = (
            self.carrier_osc_idx, self.sideband_osc_idxs, self.readout_osc_idx)
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
            carrier_hz=float(self.device.get_double(f"{self.device.osc_path(carrier_osc)}/freq")),
            carrier_vpk=float(self.device.get_double(self._mod("carrier/amplitude"))),
            command_deg=float(self.device.get_double(self._mod("carrier/phaseshift"))),
            sideband0_enable=bool(self.device.get_int(self._mod("sidebands/0/enable"))),
            sideband1_enable=bool(self.device.get_int(self._mod("sidebands/1/enable"))),
            sideband0_osc=int(self.device.get_int(self._mod("sidebands/0/oscselect"))),
            sideband1_osc=int(self.device.get_int(self._mod("sidebands/1/oscselect"))),
            mod_index_rad=float(self.device.get_double(self._mod("index"))),
            fm_hz=float(self.device.get_double(f"{self.device.osc_path(sideband_oscs[0])}/freq")),
            readout_osc_hz=float(self.device.get_double(f"{self.device.osc_path(readout_osc)}/freq")),
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
            and got["carrier_osc"] == carrier_osc
            and math.isclose(got["carrier_hz"], p.y_rf_freq_hz, rel_tol=1e-6)
            and math.isclose(got["carrier_vpk"], p.y_carrier_vpk, abs_tol=1e-5)
            and abs(signal_fit.wrap_deg(got["command_deg"] - self.command_deg)) <= 0.02
            and got["sideband0_osc"] == sideband_oscs[0]
            and got["sideband1_osc"] == sideband_oscs[1]
            and got["sideband0_enable"] is sidebands
            and got["sideband1_enable"] is sidebands
            and math.isclose(got["mod_index_rad"], p.y_pm_depth_rad, abs_tol=1e-4)
            and math.isclose(got["readout_osc_hz"], p.y_rf_freq_hz, rel_tol=1e-6)
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
        self.device.set_int(self._mod("carrier/oscselect"), self.carrier_osc_idx)
        self.device.set_double(f"{self.device.osc_path(self.carrier_osc_idx)}/freq", float(p.y_rf_freq_hz))
        self.device.set_int(self._mod("sidebands/0/oscselect"), self.sideband_osc_idxs[0])
        self.device.set_int(self._mod("sidebands/1/oscselect"), self.sideband_osc_idxs[1])
        for index in self.sideband_osc_idxs:
            self.device.set_double(f"{self.device.osc_path(index)}/freq", self.fm_hz)
        self.device.set_double(f"{self.device.osc_path(self.readout_osc_idx)}/freq", float(p.y_rf_freq_hz))
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
        for index in self.sideband_osc_idxs:
            self.device.set_double(f"{self.device.osc_path(index)}/freq", self.fm_hz)
        self.device.set_double(self._mod("index"), float(self.ctx.params.y_pm_depth_rad))
        self.device.set_int(self._mod("sidebands/0/enable"), 1)
        self.device.set_int(self._mod("sidebands/1/enable"), 1)
        return self._verify(output=True, sidebands=True)

    def frequency(self, f_hz):
        """切换 Osc 3 调制基带频率并回读确认。"""
        self._safe(pm_enabled=True)
        if f_hz != self.fm_hz:
            self.fm_hz = float(f_hz)
            for index in self.sideband_osc_idxs:
                self.device.set_double(f"{self.device.osc_path(index)}/freq", self.fm_hz)
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
    readout_osc_idx = oscillator_layout(ctx)[2]
    base = d.demod_path(p.demod0_idx)
    expected_tc = p.demod0_dynamic_tc_s if dynamic else p.demod0_tc_s
    min_rate = 10 * max(p.pm_frequencies_hz) if dynamic else 0.0
    state = dict(
        phase_deg=float(d.get_double(f"{base}/phaseshift")),
        time_constant_s=float(d.get_double(f"{base}/timeconstant")),
        stream_rate_sa_s=float(d.get_double(f"{base}/rate")),
        order=int(d.get_int(f"{base}/order")),
        oscillator_hz=float(d.get_double(f"{d.osc_path(readout_osc_idx)}/freq")),
    )
    if (not d.get_extclk() or not all(math.isfinite(v) for v in state.values())
            or state["stream_rate_sa_s"] <= 0
            or state["stream_rate_sa_s"] < min_rate
            or not math.isclose(state["oscillator_hz"], p.y_rf_freq_hz, rel_tol=1e-6)
            or not math.isclose(state["time_constant_s"], expected_tc, rel_tol=0.01)
            or state["order"] != p.demod0_order
            or any(d.get_int(f"{base}/{node}") != value for node, value in
                   (("adcselect", 0), ("oscselect", readout_osc_idx), ("harmonic", 1), ("enable", 1)))):
        raise SafetyAbort(f"MFLI Demod{p.demod0_idx + 1} 输入、参考、解调滤波或采样率回读不符：{state}")
    return state


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


def _xy(ctx, label, duration_s, *, save_frame, write_temperature, wait=sleep, lockin=None,
        dynamic=False, recovery_wait_s=None, **record):
    """仪器设置在关温控前，缓冲数据仅在开温控后保存。"""
    before = lockin_readback(ctx, dynamic=dynamic)
    if lockin is not None and before != lockin:
        raise SafetyAbort("MFLI 配置发生变化，拒绝并入同轮标定")
    temperature_on = True
    requested_rate = ctx.params.demod0_dynamic_rate_sa_s if dynamic else ctx.params.demod0_rate_sa_s
    if recovery_wait_s is None:
        wait_lag_s = ctx.params.dynamic_temp_switch_on_lag_s if dynamic else ctx.params.temp_switch_on_lag_s
    else:
        wait_lag_s = float(recovery_wait_s)

    def set_enabled(enabled):
        nonlocal temperature_on
        if not enabled:
            temperature_on = False
        write_temperature(ctx, enabled)
        temperature_on = enabled

    def retry_restore():
        if not temperature_on:
            set_enabled(True)

    def write_record(metadata, **arrays):
        path = save_frame(ctx.directory.root, label, "xy", metadata, **arrays)
        ctx.state["data_files"].append(path)

    with DeferredAcquisitionWrites(restore=retry_restore) as pending:
        with acquisition_window(set_enabled, wait, ctx.params.system_settle_s):
            metadata, arrays = _daq(ctx, duration_s, before, requested_rate=requested_rate)
            pending.add(write_record, dict(metadata, **record), **arrays)
        if wait_lag_s > 0:
            wait(wait_lag_s)
        if lockin_readback(ctx, dynamic=dynamic) != before:
            raise SafetyAbort("MFLI 采集前后回读不一致；原始记录仅供诊断")
    return complex(np.mean(arrays["x_v"]), np.mean(arrays["y_v"]))


def _unique_warnings(items):
    return list(dict.fromkeys(item for item in items if item))


def phase_response(ctx, carrier, *, save_frame, write_temperature, wait=sleep,
                   grid_deg=DEFAULT_STATIC_GRID_DEG, stage_tag: str = "A03",
                   state_key: str = "phase_response", output_key: str = "相位灵敏度"):
    """整周调相扫描找最大切线，并把 MFLI Demod 4 解调相位转到该切线。"""
    p = ctx.params
    start = time.monotonic()
    coarse = []
    coarse_lockin = lockin_readback(ctx)
    for i, phase in enumerate(grid_deg):
        carrier.set_phase(phase)
        coarse.append(_xy(ctx, f"coarse_{i:02d}", p.static_point_duration_s, lockin=coarse_lockin,
                          burst_command_deg=phase, save_frame=save_frame,
                          write_temperature=write_temperature, wait=wait))
        done = i + 1
        report_runtime_progress(stage_tag, f"扫描原子相位 {done}/{len(grid_deg)}", 10 + 80 * done / len(grid_deg),
                                estimated_remaining_seconds=(time.monotonic() - start) / done * (len(grid_deg) - done),
                                data={"eta_scope": "当前静态扫描"})
    best, rotation, prediction = signal_fit.best_tangent(grid_deg, coarse)
    d = ctx.devices["hf2"]
    setpoint = float(signal_fit.wrap_deg(lockin_readback(ctx)["phase_deg"] + rotation))
    demod.set_demod_phase(d, p.demod0_idx, setpoint)
    settings = lockin_readback(ctx)
    if abs(signal_fit.wrap_deg(settings["phase_deg"] - setpoint)) > 0.5:
        raise SafetyAbort("MFLI 最大切线转到 Y 的相位设定未通过回读")
    ctx.state[state_key] = dict(
        burst_command_deg=best,
        carrier_command_deg=best,
        rotation_deg=rotation,
        coarse_fit=prediction,
        lockin_phase_deg=settings["phase_deg"],
        lockin=settings,
        grid_deg=list(grid_deg),
        points=len(coarse),
    )
    ctx.save()
    ctx.outputs[output_key] = f"整周扫描完成，工作点 {best:.3f}°；待局部 K₀ 标定"
    report_runtime_progress(stage_tag, f"整周扫描完成，工作点 {best:.3f}°", 95, estimated_remaining_seconds=None)


def local_calibration(ctx, carrier, *, save_frame, write_temperature, wait=sleep,
                      stage_tag: str = "A04", state_key: str = "calibration",
                      phase_state_key: str = "phase_response", output_key: str = "相位灵敏度"):
    """在整周工作点两侧以 MFLI 指令角加密细扫，线性回归标定本轮 K₀。"""
    p = ctx.params
    phase = ctx.state.get(phase_state_key) or {}
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
    ctx.state[state_key] = cal
    values, times, centers = [], [], []
    warnings = []
    offsets = local_offsets(span)
    for i, delta in enumerate(offsets):
        command = carrier.set_phase(best + delta)
        now = time.monotonic() - start
        xy_label = f"local_{i:02d}_xy"
        value = _xy(ctx, xy_label, p.static_point_duration_s, lockin=settings,
                    burst_command_deg=command, carrier_command_deg=command, save_frame=save_frame,
                    write_temperature=write_temperature, wait=wait)
        rows.append(dict(burst_command_deg=command, carrier_command_deg=command, requested_offset_deg=delta,
                         xy_label=xy_label, elapsed_s=now, x_mean_v=value.real, y_mean_v=value.imag))
        values.append(value)
        times.append(now)
        centers.append(command)
        done = i + 1
        report_runtime_progress(stage_tag, f"局部标定 {done}/{len(offsets)}", 5 + 85 * done / len(offsets),
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
    ctx.outputs[output_key] = f"局部 K₀ = {k:.5g} V/rad，工作点更新为 {refined_center:.3f}°{suffix}"
    report_runtime_progress(stage_tag, f"局部 K₀ {k:.5g} V/rad，工作点 {refined_center:.3f}°{suffix}", 95,
                            estimated_remaining_seconds=None)
    return cal
