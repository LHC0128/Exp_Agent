"""线圈驱动特性测量的无硬件测试：假信号源/功放/示波器模拟一阶 RL 回路。"""

from __future__ import annotations

import json
import math

import numpy as np
import pytest

from oscilloscope import AcquisitionResult

from . import analysis
from .measurement import (
    CURRENT_LIMIT_KEY, SOURCE_KEY, CoilDriveCharacterization, CoilDriveParams, SafetyAbort, capture_config,
    preflight,
)

LIMITS = {
    SOURCE_KEY: {"min": -20.0, "max": 20.0},
    CURRENT_LIMIT_KEY: {"min": -0.3, "max": 0.3},
}
TAU = 2e-6  # L/R 时间常数 → 一阶带宽约 80 kHz


class FakeSource:
    def __init__(self, log):
        self.channel = 1
        self.log = log
        self.mode = ("dc", 0.0)

    def set_burst_state(self, state, channel=None):
        self.log.append(("dg_burst", state))

    def set_mod_state(self, state, channel=None):
        self.log.append(("dg_mod", state))

    def set_sync_state(self, state, channel=None):
        self.log.append(("dg_sync", state))

    def setup_dc(self, volts, channel=None):
        self.mode = ("dc", volts)
        self.log.append(("dg_dc", volts))

    def setup_sine(self, freq, vpp, offset, channel=None):
        self.mode = ("sine", freq, vpp)
        self.log.append(("dg_sine", freq))

    def setup_pulse(self, freq, amp, offset, width=None, channel=None):
        self.mode = ("pulse", amp, width)
        self.log.append(("dg_pulse", width))

    def set_output(self, state, channel=None):
        self.log.append(("dg_output", state))


class FakeAmplifier:
    def __init__(self, log, check=None):
        self.log = log
        self.on = False
        self.check = check

    def set_output(self, enabled):
        if enabled and self.check:
            self.check("output", True)
        self.on = enabled
        self.log.append(("amp_output", enabled))


class FakeScope:
    def __init__(self, source, amplifier, *, gain, loop_ohm=20.0, offset_a=2e-3, sense_ohm=10.0,
                 cancel_after=None):
        self.source, self.amplifier = source, amplifier
        self.gain, self.loop_ohm, self.offset_a, self.sense_ohm = gain, loop_ohm, offset_a, sense_ohm
        self.calls = 0
        self.cancel_after = cancel_after
        self.rng = np.random.default_rng(0)

    def _current(self, t):
        if not self.amplifier.on:
            return np.zeros_like(t)
        k = self.gain / self.loop_ohm
        mode = self.source.mode
        if mode[0] == "dc":
            base = np.full_like(t, k * mode[1])
        elif mode[0] == "sine":
            _, f, vpp = mode
            h = 1.0 / complex(1.0, 2 * math.pi * f * TAU)
            base = k * vpp / 2 * abs(h) * np.cos(2 * math.pi * f * t + np.angle(h))
        else:
            _, amp, width = mode
            on = (t >= 0) & (t < width)
            after = t >= width
            base = np.zeros_like(t)
            base[on] = 1 - np.exp(-t[on] / TAU)
            base[after] = (1 - math.exp(-width / TAU)) * np.exp(-(t[after] - width) / TAU)
            base *= k * amp
        return base + self.offset_a

    def acquire_all(self, config):
        self.calls += 1
        if self.cancel_after is not None and self.calls > self.cancel_after:
            raise KeyboardInterrupt("cancelled")
        n = round(config.sampling_rate * config.sampling_time)
        t = (np.arange(n) - n / 2) / config.sampling_rate
        sense = self._current(t) * self.sense_ohm + self.rng.normal(0, 1e-5, n)
        channels = {c.number: c for c in config.channels}
        full = 4 * channels[2].scale
        sense = np.clip(sense, -full, full)
        sync = np.where(t >= 0, 3.3, 0.0)
        snap = {"requested": config.to_dict()}
        return [AcquisitionResult(2, np.array([], np.int16), sense, t, {}, snap),
                AcquisitionResult(3, np.array([], np.int16), sync, t, {}, snap)]


def _params(**overrides):
    base = dict(amplifier_gain_db=20.0, frequency_start_hz=100.0, frequency_stop_hz=1e6, points_per_decade=4,
                settle_s=0.0, zero_frames=2, pulse_widths_s=(5e-6, 20e-6, 100e-6))
    base.update(overrides)
    return CoilDriveParams(**base)


def _build(tmp_path, *, gain=10.0, cancel_after=None, limits=LIMITS, **overrides):
    log = []
    source = FakeSource(log)
    amplifier = FakeAmplifier(log)
    scope = FakeScope(source, amplifier, gain=gain, cancel_after=cancel_after)
    run = CoilDriveCharacterization(_params(**overrides), source=source, amplifier=amplifier,
                                    scope_acquisition=scope, directory=tmp_path / "run", limits=limits,
                                    sleep=lambda s: None, log=lambda m: None)
    amplifier.check = run.amplifier_safety_check
    return run, log


def _assert_shutdown_order(log):
    amp_off = max(i for i, item in enumerate(log) if item == ("amp_output", False))
    dg_off = max(i for i, item in enumerate(log) if item == ("dg_output", False))
    assert amp_off < dg_off
    assert ("dg_sync", False) in log[amp_off:]
    assert ("dg_dc", 0.0) in log[amp_off:]


def test_full_run_recovers_offset_bandwidth_and_rise_time(tmp_path):
    run, log = _build(tmp_path)
    record = run.run()
    summary = json.loads((record.directory / "results" / "summary.json").read_text(encoding="utf-8"))
    assert summary["error"] is None and summary["shutdown"]["completed"]
    assert summary["zero_offset"]["offset_current_a"] == pytest.approx(2e-3, abs=1e-5)
    expected_fc = 1 / (2 * math.pi * TAU)
    assert summary["bandwidth_3db_hz"] == pytest.approx(expected_fc, rel=0.05)
    low = summary["transfer"][0]
    assert low["admittance_a_per_v"] == pytest.approx(10.0 / 20.0, rel=0.01)
    longest = summary["pulse"][-1]
    assert longest["rise_10_90_s"] == pytest.approx(TAU * math.log(9), rel=0.05)
    assert longest["area_deviation_fraction"] == pytest.approx(0.0, abs=0.01)
    # 线性 RL：含拖尾面积守恒；5 us（2.5τ）脉冲峰值未到平台，20 us 起可用
    assert summary["pulse"][0]["area_deviation_fraction"] == pytest.approx(0.0, abs=0.01)
    assert summary["pulse"][0]["peak_fraction"] == pytest.approx(1 - math.exp(-2.5), abs=0.01)
    assert summary["shortest_usable_pulse_width_s"] == pytest.approx(20e-6)
    assert (record.directory / "results" / "coil_drive.png").exists()
    assert log.index(("amp_output", True)) > log.index(("dg_dc", 0.0))
    _assert_shutdown_order(log)


def test_gain_higher_than_declared_aborts_and_still_shuts_down(tmp_path):
    run, log = _build(tmp_path, gain=20.0)  # 实际增益是声明 20 dB(10×) 的两倍
    with pytest.raises(SafetyAbort, match="核对功放面板增益"):
        run.run()
    summary = json.loads((run.record.directory / "results" / "summary.json").read_text(encoding="utf-8"))
    assert "SafetyAbort" in summary["error"]
    assert any(name.startswith("sine_00") for name in summary["frames"])  # 已采帧保留
    assert not any(item[0] == "dg_sine" for item in log[log.index(("dg_sine", 100.0)) + 1:])
    _assert_shutdown_order(log)


def test_missing_current_rule_refuses_before_any_output(tmp_path):
    limits = {SOURCE_KEY: LIMITS[SOURCE_KEY]}
    run, log = _build(tmp_path, limits=limits)
    with pytest.raises(ValueError, match=CURRENT_LIMIT_KEY):
        run.run()
    assert log == []


def test_planned_current_above_limit_is_rejected():
    with pytest.raises(ValueError, match="安全拦截"):
        preflight(_params(sine_peak_current_a=0.35), LIMITS)
    with pytest.raises(ValueError, match="功耗"):
        preflight(_params(sine_peak_current_a=0.3, resistor_rated_w=0.5), LIMITS)


def test_measured_overcurrent_aborts(tmp_path):
    run, log = _build(tmp_path, gain=10.0, sine_peak_current_a=0.28)
    run.scope.offset_a = 0.05  # 零偏叠加后超过 0.3 A
    with pytest.raises(SafetyAbort, match="越过限值"):
        run.run()
    _assert_shutdown_order(log)


def test_cancel_mid_sweep_runs_shutdown(tmp_path):
    run, log = _build(tmp_path, cancel_after=6)
    with pytest.raises(KeyboardInterrupt):
        run.run()
    assert run.record.shutdown["completed"]
    _assert_shutdown_order(log)


def test_amplifier_callback_rejects_gain_or_impedance_changes(tmp_path):
    run, _ = _build(tmp_path)
    with pytest.raises(ValueError, match="不修改功放"):
        run.amplifier_safety_check("gain_db", 10.0)


def test_capture_config_uses_scope_depth_table_and_rate_cap():
    params = _params()
    config = capture_config(10 / 1e3, 0.1, params)
    assert config.sampling_time == pytest.approx(0.01)
    assert round(config.sampling_rate * config.sampling_time) == 10_000
    fast = capture_config(5 * 5e-6, 0.1, params, min_points=100_000)
    assert fast.sampling_time == pytest.approx(5e-5)
    assert fast.sampling_rate <= params.max_sample_rate_sa_s
    assert round(fast.sampling_rate * fast.sampling_time) == 10_000


def test_fit_sine_recovers_amplitude_and_phase():
    t = np.linspace(0, 1e-2, 5000)
    fit = analysis.fit_sine(t, 0.3 * np.cos(2 * math.pi * 1e3 * t - 0.4) + 0.01, 1e3)
    assert fit.amplitude == pytest.approx(0.3)
    assert fit.phase_rad == pytest.approx(-0.4)
    assert fit.quality == pytest.approx(1.0)
