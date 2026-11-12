"""线圈驱动链路电学特性测量：功放零偏、正弦扫频传递函数、矩形脉冲阶跃响应。

接线：信号源 Z_magnetic_field 通道 → Aigtek 1200B → 阻尼电阻 → 线圈 → 采样电阻 → 功放返回地；
示波器 current_channel 测采样电阻电压，sync_channel 接信号源该通道同步输出并作触发。
纯电学工具，不连接 TEC、锁相，不操作温控开关；每帧采集后立即保存。
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from oscilloscope import AcquisitionConfig, ChannelConfig, TriggerConfig

from ...common import validate_safety_limit
from ...steps.safety_shutdown import (
    DGChannelShutdown, DisconnectTarget, SafetyShutdownReport, ShutdownAction, run_safety_shutdown,
)
from . import analysis

SOURCE_KEY = "Z_magnetic_field"
CURRENT_LIMIT_KEY = "z_pulse_coil_current"
MEMORY_DEPTHS = (1_000, 10_000, 100_000, 1_000_000)
VERTICAL_HALF_DIVISIONS = 4.0


class SafetyAbort(RuntimeError):
    """实测量越过安全判据，立即中止并执行安全收尾。"""


@dataclass(frozen=True, slots=True)
class CoilDriveParams:
    amplifier_gain_db: float
    sense_resistor_ohm: float = 10.0
    damping_resistor_ohm: float = 10.0
    coil_resistance_ohm: float = 0.0
    resistor_rated_w: float = 2.0
    sine_peak_current_a: float = 0.1
    pulse_current_a: float = 0.1
    frequency_start_hz: float = 10.0
    frequency_stop_hz: float = 500e3
    points_per_decade: int = 4
    sine_cycles: float = 10.0
    settle_s: float = 0.3
    pulse_widths_s: tuple[float, ...] = (10e-6, 20e-6, 50e-6, 100e-6, 200e-6, 500e-6, 1e-3)
    pulse_repetition_hz: float = 50.0
    zero_frames: int = 5
    current_channel: int = 2
    sync_channel: int = 3
    sync_trigger_level_v: float = 1.0
    sync_scale_v_div: float = 2.0
    max_sample_rate_sa_s: float = 250e6
    gain_tolerance: float = 1.25
    area_tolerance: float = 0.02

    @property
    def gain(self) -> float:
        return 10.0 ** (self.amplifier_gain_db / 20.0)

    @property
    def loop_resistance_ohm(self) -> float:
        return self.sense_resistor_ohm + self.damping_resistor_ohm + self.coil_resistance_ohm

    @property
    def amps_per_drive_volt(self) -> float:
        """按声明增益和回路电阻预测的直流 I/V_drive；coil_resistance_ohm=0 时偏保守（偏大）。"""
        return self.gain / self.loop_resistance_ohm

    def frequencies(self) -> list[float]:
        decades = math.log10(self.frequency_stop_hz / self.frequency_start_hz)
        count = max(2, int(math.ceil(decades * self.points_per_decade)) + 1)
        return [float(f) for f in np.geomspace(self.frequency_start_hz, self.frequency_stop_hz, count)]


def current_limit(limits: Mapping[str, Mapping[str, Any]]) -> tuple[float, float]:
    """线圈电流规则必须存在且上下界都为有限数，否则拒绝开启功放。"""
    rule = limits.get(CURRENT_LIMIT_KEY) or {}
    low, high = rule.get("min"), rule.get("max")
    if low is None or high is None or not (math.isfinite(low) and math.isfinite(high)):
        raise ValueError(f"params/safety_limits.yaml 缺少 {CURRENT_LIMIT_KEY} 的有限上下界（单位 A），拒绝开启功放")
    return float(low), float(high)


def preflight(params: CoilDriveParams, limits: Mapping[str, Mapping[str, Any]]) -> dict[str, float]:
    """按声明增益预测全部计划输出的峰值电流、信号源电平与电阻功耗，任何一项越界即拒绝。"""
    current_limit(limits)
    if params.amplifier_gain_db < 0 or params.loop_resistance_ohm <= 0:
        raise ValueError("增益须 ≥0 dB，回路电阻须为正")
    sine_drive_peak = params.sine_peak_current_a / params.amps_per_drive_volt
    pulse_drive = params.pulse_current_a / params.amps_per_drive_volt
    for level in (sine_drive_peak, -sine_drive_peak, pulse_drive, 0.0):
        validate_safety_limit(SOURCE_KEY, level, dict(limits))
    for current in (params.sine_peak_current_a, -params.sine_peak_current_a, params.pulse_current_a):
        validate_safety_limit(CURRENT_LIMIT_KEY, current, dict(limits))
    sine_power = (params.sine_peak_current_a / math.sqrt(2.0)) ** 2 * max(
        params.sense_resistor_ohm, params.damping_resistor_ohm)
    duty = max(params.pulse_widths_s) * params.pulse_repetition_hz
    if duty >= 0.5:
        raise ValueError("脉冲占空比须 <50%，避免把阶跃测量变成连续加热")
    pulse_power = params.pulse_current_a ** 2 * duty * max(params.sense_resistor_ohm, params.damping_resistor_ohm)
    if max(sine_power, pulse_power) > params.resistor_rated_w / 2:
        raise ValueError(f"电阻平均功耗 {max(sine_power, pulse_power):.3g} W 超过额定功率一半")
    return {"sine_drive_peak_v": sine_drive_peak, "pulse_drive_v": pulse_drive,
            "sine_resistor_power_w": sine_power, "pulse_resistor_power_w": pulse_power}


def _nice_up(value: float) -> float:
    """不小于 value 的 1-2-5 序列值。"""
    exponent = math.floor(math.log10(value))
    for mantissa in (1.0, 2.0, 5.0, 10.0):
        candidate = mantissa * 10.0 ** exponent
        if candidate >= value * (1 - 1e-12):
            return candidate
    raise AssertionError("unreachable")


def capture_config(window_s: float, current_scale: float, params: CoilDriveParams, *,
                   trigger_mode: str = "NORMal", min_points: int = 10_000) -> AcquisitionConfig:
    """十格主时基取 1-2-5 序列；点数取深度表内不超采样率上限的最大值（至少 1000）。"""
    timebase = _nice_up(window_s / 10.0)
    duration = timebase * 10.0
    allowed = [p for p in MEMORY_DEPTHS if p <= min_points and p / duration <= params.max_sample_rate_sa_s]
    if not allowed:
        raise ValueError(f"{duration:g} s 窗口在采样率上限 {params.max_sample_rate_sa_s:g} Sa/s 下不足 1000 点")
    points = max(allowed)
    return AcquisitionConfig(
        sampling_rate=points / duration, sampling_time=duration, memory_management=None,
        acquire_delay=0.0,
        channels=[
            ChannelConfig(params.current_channel, enabled=True, scale=current_scale, coupling="DC",
                          impedance="ONEMeg", probe=1),
            ChannelConfig(params.sync_channel, enabled=True, scale=params.sync_scale_v_div, coupling="DC",
                          impedance="ONEMeg", probe=1),
        ],
        trigger=TriggerConfig(mode=trigger_mode, source=f"C{params.sync_channel}", slope="RISing",
                              level=params.sync_trigger_level_v),
    )


def _scale_for(peak_v: float) -> float:
    return min(10.0, max(0.0005, _nice_up(max(peak_v, 1e-4) * 1.25 / VERTICAL_HALF_DIVISIONS)))


@dataclass
class RunRecord:
    directory: Path
    frames: list[str] = field(default_factory=list)
    preflight: dict[str, float] = field(default_factory=dict)
    zero: dict[str, Any] = field(default_factory=dict)
    transfer: list[analysis.TransferPoint] = field(default_factory=list)
    steps: list[analysis.StepMetrics] = field(default_factory=list)
    step_traces: list[tuple[float, np.ndarray, np.ndarray]] = field(default_factory=list)
    shutdown: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class CoilDriveCharacterization:
    """依赖注入的测量编排；source/amplifier/scope_acquisition 由调用方创建并连接。"""

    def __init__(self, params: CoilDriveParams, *, source: Any, amplifier: Any, scope_acquisition: Any,
                 directory: Path, limits: Mapping[str, Mapping[str, Any]],
                 disconnect_targets: tuple[DisconnectTarget, ...] = (),
                 sleep: Callable[[float], None] = time.sleep,
                 check_cancelled: Callable[[], None] = lambda: None,
                 log: Callable[[str], None] = print):
        self.params = params
        self.source = source
        self.amplifier = amplifier
        self.scope = scope_acquisition
        self.limits = dict(limits)
        self.disconnect_targets = disconnect_targets
        self.sleep = sleep
        self.check_cancelled = check_cancelled
        self.log = log
        self.channel = int(source.channel)
        self.record = RunRecord(Path(directory))
        self._current_scale = _scale_for(params.sine_peak_current_a * params.sense_resistor_ohm)

    # ---- 安全 ----
    def amplifier_safety_check(self, operation: str, value: Any) -> None:
        """1200B 的 safety_check 回调：只放行 output=True，且须通过整次计划的预检。"""
        if operation != "output" or value is not True:
            raise ValueError(f"本测量不修改功放 {operation}；请在面板上设置后声明增益")
        preflight(self.params, self.limits)

    def _guard_current(self, current_a: np.ndarray) -> None:
        low, high = current_limit(self.limits)
        peak_high, peak_low = float(np.max(current_a)), float(np.min(current_a))
        if peak_high > high or peak_low < low:
            raise SafetyAbort(f"实测线圈电流 [{peak_low:.4g}, {peak_high:.4g}] A 越过限值 [{low}, {high}] A")

    def _set_dc(self, volts: float) -> None:
        validate_safety_limit(SOURCE_KEY, volts, self.limits)
        self.source.set_burst_state(False)
        self.source.set_mod_state(False)
        self.source.setup_dc(volts)

    # ---- 采集与保存 ----
    def _capture(self, name: str, config: AcquisitionConfig, meta: dict[str, Any]
                 ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """采集一帧；CH 电流削顶时放大量程重采（最多 3 次）。返回 (t, sense_v, sync_v)。"""
        p = self.params
        for _ in range(4):
            self.check_cancelled()
            results = {result.channel: result for result in self.scope.acquire_all(config)}
            sense, sync = results[p.current_channel], results[p.sync_channel]
            channel = next(c for c in config.channels if c.number == p.current_channel)
            clipped = float(np.max(np.abs(sense.voltage - channel.offset))) >= 0.98 * VERTICAL_HALF_DIVISIONS * channel.scale
            self._guard_current(sense.voltage / p.sense_resistor_ohm)
            if not clipped:
                break
            if channel.scale >= 10.0:
                raise SafetyAbort("采样电阻电压在最大量程仍削顶")
            channel.scale = _scale_for(channel.scale * VERTICAL_HALF_DIVISIONS * 2.0)
            self._current_scale = channel.scale
        else:
            raise SafetyAbort(f"{name}: 量程调整后仍削顶")
        path = self.record.directory / "raw" / f"{name}.npz"
        np.savez(path, time=sense.time, sense_v=sense.voltage, sync_v=sync.voltage,
                 meta=json.dumps({**meta, "scope": sense.config_snapshot}, default=str))
        self.record.frames.append(path.name)
        return sense.time, sense.voltage, sync.voltage

    # ---- 三段测量 ----
    def measure_zero(self) -> None:
        """(a) 功放关/开两态、信号源 DC 0 V 下的采样电阻平均电压；差值为功放零偏电流。"""
        p = self.params
        config = capture_config(0.01, 0.01, p, trigger_mode="AUTO")
        means: dict[str, list[float]] = {"amplifier_off": [], "amplifier_on": []}
        stds: dict[str, list[float]] = {"amplifier_off": [], "amplifier_on": []}
        for state in ("amplifier_off", "amplifier_on"):
            if state == "amplifier_on":
                self.amplifier.set_output(True)
                self.sleep(p.settle_s)
            for index in range(p.zero_frames):
                _, sense, _ = self._capture(f"zero_{state}_{index:02d}", config, {"state": state})
                means[state].append(float(np.mean(sense)))
                stds[state].append(float(np.std(sense)))
        off, on = np.mean(means["amplifier_off"]), np.mean(means["amplifier_on"])
        self.record.zero = {
            "scope_baseline_v": float(off),
            "offset_current_a": float((on - off) / p.sense_resistor_ohm),
            "offset_current_frame_std_a": float(np.std(means["amplifier_on"]) / p.sense_resistor_ohm),
            "noise_rms_a": float(np.mean(stds["amplifier_on"]) / p.sense_resistor_ohm),
        }
        self.log(f"零偏电流 {self.record.zero['offset_current_a'] * 1e3:.3f} mA")

    def measure_transfer(self) -> None:
        """(b) 正弦扫频；首频实测 I/V 超过预测 gain_tolerance 倍即中止（增益声明不符）。"""
        p = self.params
        drive_peak = p.sine_peak_current_a / p.amps_per_drive_volt
        validate_safety_limit(SOURCE_KEY, drive_peak, self.limits)
        validate_safety_limit(SOURCE_KEY, -drive_peak, self.limits)
        for index, frequency in enumerate(p.frequencies()):
            self.check_cancelled()
            self.source.setup_sine(frequency, 2.0 * drive_peak, 0.0)
            self.sleep(p.settle_s)
            config = capture_config(p.sine_cycles / frequency, self._current_scale, p)
            t, sense, _ = self._capture(f"sine_{index:02d}_{frequency:.6g}Hz", config,
                                         {"frequency_hz": frequency, "drive_peak_v": drive_peak})
            point = analysis.transfer_point(frequency, drive_peak, t, sense, p.sense_resistor_ohm)
            self.record.transfer.append(point)
            self.log(f"{frequency:10.4g} Hz  |I/V|={point.admittance_a_per_v * 1e3:.4g} mA/V  "
                     f"phase={point.phase_deg:7.2f} deg  q={point.fit_quality:.4f}")
            if index == 0 and point.admittance_a_per_v > p.gain_tolerance * p.amps_per_drive_volt:
                raise SafetyAbort(
                    f"实测 I/V={point.admittance_a_per_v:.4g} A/V 超过按 {p.amplifier_gain_db} dB 预测的 "
                    f"{p.amps_per_drive_volt:.4g} A/V 的 {p.gain_tolerance} 倍；核对功放面板增益")
        self._set_dc(0.0)

    def measure_steps(self) -> None:
        """(c) 0→V 单极性矩形脉冲，触发居中、触发后窗口 ≥2.5 倍脉宽。"""
        p = self.params
        drive = p.pulse_current_a / p.amps_per_drive_volt
        validate_safety_limit(SOURCE_KEY, drive, self.limits)
        self._current_scale = _scale_for(p.pulse_current_a * p.sense_resistor_ohm)
        for width in sorted(p.pulse_widths_s):
            self.check_cancelled()
            self.source.setup_pulse(p.pulse_repetition_hz, drive, drive / 2.0, width=width)
            self.sleep(p.settle_s)
            config = capture_config(5.0 * width, self._current_scale, p, min_points=100_000)
            t, sense, _ = self._capture(f"pulse_{width * 1e6:g}us", config,
                                         {"width_s": width, "drive_v": drive})
            current = sense / p.sense_resistor_ohm
            metrics = analysis.step_metrics(t, current, width)
            self.record.steps.append(metrics)
            self.record.step_traces.append((width, t, current))
            self.log(f"脉宽 {width * 1e6:g} us  上升 {metrics.rise_10_90_s * 1e6:.3g} us  "
                     f"下降 {metrics.fall_90_10_s * 1e6:.3g} us  过冲 {metrics.overshoot_fraction * 100:.2f}%")
        self._set_dc(0.0)

    # ---- 生命周期 ----
    def shutdown(self) -> SafetyShutdownReport:
        """先关功放（去掉线圈电流），再把信号源通道归零并关闭输出与同步，最后断开。"""
        first = run_safety_shutdown(
            extra_actions=[ShutdownAction("发送 1200B 关闭命令", lambda: self.amplifier.set_output(False))],
            preserved_outputs=())
        second = run_safety_shutdown(
            dg_channels=[DGChannelShutdown(self.source, self.channel, SOURCE_KEY, "Z 线圈驱动", disable_sync=True)],
            disconnect_targets=self.disconnect_targets, preserved_outputs=())
        return SafetyShutdownReport(first.action_errors + second.action_errors, second.disconnect_errors,
                                    second.preserved_outputs)

    def run(self) -> RunRecord:
        p = self.params
        record = self.record
        (record.directory / "raw").mkdir(parents=True, exist_ok=True)
        (record.directory / "results").mkdir(exist_ok=True)
        record.preflight = preflight(p, self.limits)
        (record.directory / "experiment_config.json").write_text(json.dumps(
            {"params": asdict(p), "preflight": record.preflight, "limits": {
                key: self.limits.get(key) for key in (SOURCE_KEY, CURRENT_LIMIT_KEY)}},
            ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        try:
            self.amplifier.set_output(False)
            self._set_dc(0.0)
            self.source.set_sync_state(True)
            self.measure_zero()
            self.measure_transfer()
            self.measure_steps()
        except BaseException as exc:
            record.error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            report = self.shutdown()
            record.shutdown = report.to_dict()
            self._write_results()
        return record

    def _write_results(self) -> None:
        """抢救已采到的部分：分析失败也保留原始帧与收尾记录。"""
        record = self.record
        payload: dict[str, Any] = {"error": record.error, "shutdown": record.shutdown, "frames": record.frames}
        try:
            payload.update(analysis.summarize(record.zero, record.transfer, record.steps, self.params.area_tolerance))
            analysis.plot_summary(payload, record.step_traces, record.directory / "results" / "coil_drive.png")
        except Exception as exc:  # 分析失败不覆盖测量异常
            payload["analysis_error"] = f"{type(exc).__name__}: {exc}"
        (record.directory / "results" / "summary.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
