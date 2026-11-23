"""新实验的微型真实编排、当前格式和安全路径；不连接仪器。"""
from __future__ import annotations

from dataclasses import replace
import math
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from ...common import WorkflowCancelled
from ...steps import RoutedSignalGenerator, SignalGeneratorRoute
from ...steps.coil_drive_chain import SafetyAbort
from . import acquisition, analysis, monitor, preparation, run_format, signal_fit
from .definition import ADAPTER
from .models import RfPhaseSensitivityParams
from .workflow import Workflow

ROOT = Path(__file__).resolve().parents[3]
ACQUISITION_CASES = {"rf-phase-sensitivity": {"mode": "single", "cases": {
    "order": ["test_full_round_and_physical_phase_transfer"],
    "slow_disk": ["test_full_round_and_physical_phase_transfer"],
    "cancel": ["test_cancel_preserves_completed_frames"],
    "acquire_failure": ["test_uses_strict_daq_and_rejects_synthetic_time"],
    "restore_failure": ["test_restore_failure_saves_record_then_aborts"],
    "save_failure": ["test_save_failure_keeps_previous_frames"],
    "partial_save": ["test_cancel_preserves_completed_frames", "test_save_failure_keeps_previous_frames"],
    "entrypoints": ["test_full_round_and_physical_phase_transfer", "test_stepwise_rerun_keeps_prior_run"]},
    "sources": ["lab_workflows/experiment_modules/rf_phase_sensitivity/preparation.py",
                "lab_workflows/experiment_modules/rf_phase_sensitivity/acquisition.py",
                "lab_workflows/experiment_modules/rf_phase_sensitivity/monitor.py",
                "lab_workflows/experiment_modules/rf_phase_sensitivity/workflow.py",
                "lab_workflows/experiment_modules/rf_phase_sensitivity/session.py",
                "experiments/RfPhaseSensitivity.py"]}}
REGRESSION_CASES = {"rf-phase-sensitivity": {"parameters": {
    "sources": ["lab_workflows/experiment_modules/rf_phase_sensitivity/models.py",
                "params/experiments/rf-phase-sensitivity.yaml"], "tests": ["test_preflight_and_defaults"]}}}


class FakeGenerator:
    """保留真实协议的读写分离；setup_dc 和 setup_square 会隐式开启输出。"""

    def __init__(self, events, name):
        self.events, self.name = events, name
        self.channels = {i: dict(output=False, shape="DC", frequency=5000., amplitude=.05, offset=0.,
                                 output_load="INF", voltage_unit="VPP", burst_state=False, mod_state=False,
                                 burst_delay=0., phase_adjust=0., sync_state=False) for i in (1, 2)}
        self.restore_failures = 0

    def __getattr__(self, name):
        def getter(*, channel):
            return self.channels[channel][name[4:]]

        def setter(value, *, channel):
            self.events.append((self.name, channel, name, value))
            self.channels[channel][name[4:]] = value

        if name.startswith("get_"):
            return getter
        if name.startswith("set_"):
            return setter
        raise AttributeError(name)

    def get_dc_voltage(self, *, channel):
        return self.get_offset(channel=channel)

    def set_dc_voltage(self, value, *, channel):
        if self.name == "temp" and value == 5. and self.channels[channel]["offset"] == 0. and self.restore_failures:
            self.restore_failures -= 1
            raise IOError("注入温控恢复错误")
        self.set_offset(value, channel=channel)

    def setup_dc(self, value, *, channel):
        self.set_shape("DC", channel=channel)
        self.set_dc_voltage(value, channel=channel)
        self.set_output(True, channel=channel)

    def setup_square(self, *, freq, amplitude, offset, dcycle, phase, channel):
        self.channels[channel].update(shape="SQUare", frequency=freq, amplitude=amplitude, offset=offset,
                                      square_dcycle=dcycle, output=True)

    def setup_arbitrary(self, wave, freq, amplitude, offset, *, output, channel):
        assert wave.size > 1000 and np.max(abs(wave)) <= 1.0
        self.channels[channel].update(shape="USER", frequency=freq, amplitude=amplitude, offset=offset,
                                      arb_points=wave.size, output=output)

    def phase_init(self, *, channel):
        self.set_phase_adjust(0., channel=channel)

    def idn(self):
        return "RIGOL,DG4162,DG4E261300664,FAKE"

    def query(self, command):
        if "COUPling" in command:
            return "OFF,OFF,OFF"
        if command.endswith(":STATe?"):
            channel = int(command.split(":")[1][-1])
            return "1" if self.get_output(channel=channel) else "0"
        assert command.endswith(":POLarity?")
        return "NORM"

    def get_ref_clock_source(self):
        return "EXTernal"

    def raise_for_errors(self):
        pass

    def wait_for_operation_complete(self):
        pass

    def connect(self):
        self.events.append((self.name, "connect"))

    def disconnect(self):
        self.events.append((self.name, "disconnect"))


class FakeLockin:
    model = "MFLI"
    device_id = "devfake"
    capabilities = {}
    acquisition_mode = "daq"

    def __init__(self):
        self.values = {}

    def demod_path(self, i):
        return f"/{self.device_id}/demods/{i}"

    def osc_path(self, i):
        return f"/{self.device_id}/oscs/{i}"

    def sig_in_path(self, i):
        return f"/{self.device_id}/sigins/{i}"

    def set_double(self, path, value):
        self.values[path] = float(value)

    def get_double(self, path):
        return self.values[path]

    def set_int(self, path, value):
        self.values[path] = int(value)

    def get_int(self, path):
        return self.values[path]

    def sync(self):
        pass

    def get_extclk(self):
        return True

    def connect(self):
        pass


@pytest.fixture
def rig(monkeypatch, tmp_path):
    monkeypatch.setenv("LAB_DATA_ROOT", str(tmp_path / "data"))
    events = []
    xygen, zgen, temp = (FakeGenerator(events, name) for name in ("xy", "z", "temp"))
    amp, tec = Mock(), Mock()
    amp.set_output.side_effect = lambda enable: events.append(("amp", enable))
    gs = Mock()
    gs_state = dict(source_function="CURR", current=-0.001, output=True, current_range=0.01, current_limit=0.02)
    for key in gs_state:
        getattr(gs, f"get_{key}").side_effect = lambda key=key: gs_state[key]
        getattr(gs, f"set_{key}").side_effect = lambda value, key=key: gs_state.update({key: value})
    lockin = FakeLockin()
    channels = dict(x=1, y=2, z=3, trigger=4, temp=5)
    proxy = RoutedSignalGenerator({i: SignalGeneratorRoute(dev, c, name) for i, (name, dev, c) in enumerate(
        (("x", xygen, 1), ("y", xygen, 2), ("z", zgen, 1), ("trigger", zgen, 2), ("temp", temp, 2)), 1)})
    scope = Mock()
    devices = dict(hf2=lockin, amp=amp, gs200=gs, signals=proxy, channels=channels, scope=scope, tec=tec)

    def connect(ctx, **kwargs):
        for name, device in (("xy", xygen), ("z", zgen), ("temp", temp), ("hf2", lockin),
                             ("amp", amp), ("gs200", gs), ("scope", scope), ("tec", tec)):
            ctx.device_session.connect(name, name, lambda device=device: device)
        ctx.devices.update(devices)

    monkeypatch.setattr(preparation.prep, "connect", connect)
    monkeypatch.setattr(preparation.prep, "clocks", lambda ctx: {"fake": "EXT"})
    monkeypatch.setattr(monitor, "snapshot_scope", lambda s: {"status": "AUTO"})
    monkeypatch.setattr(monitor, "restore_scope", lambda ctx: events.append(("scope", "restore")))
    monkeypatch.setattr(acquisition, "sleep", lambda s: events.append(("wait", s)))
    monkeypatch.setattr(acquisition, "_STATIC_GRID_DEG", tuple(range(0, 360, 45)))
    monkeypatch.setattr(acquisition, "report_runtime_progress", lambda *a, **k: None)
    p = replace(RfPhaseSensitivityParams(), pm_frequencies_hz=[20., 40.], pm_tone_duration_s=.5,
                noise_duration_s=8., welch_segment_s=2., static_point_duration_s=.05,
                demod0_rate_sa_s=1000., system_settle_s=.1, temp_switch_on_lag_s=.1)
    rnd = np.random.default_rng(7429)
    settings = xygen.channels[2]

    def center():
        if settings["burst_state"]:
            return settings["burst_phase"] - 91.
        return settings["phase_adjust"] - (51. if settings["mod_state"] else 22.)

    def capture(ctx, f):
        assert temp.channels[2]["offset"] == 5.
        ctx.scope_touched = True
        events.append(("scope", f, center()))
        fs = 80_000.  # 该夹具替换严格驱动边界；实机采样率门槛单测覆盖。
        t = np.arange(int(fs * (0.5 if f is None else max(.5, 10 / f)))) / fs
        theta = 2 * np.pi * ctx.params.y_rf_freq_hz * t
        ref = 5. * (np.sin(theta + .1) >= 0)
        phase = np.deg2rad(center())
        if settings["mod_state"]:
            phase += np.deg2rad(settings["mod_pm_deviation"]) * np.sin(2 * np.pi * settings["mod_pm_internal_freq"] * t)
        return dict(rate_sa_s=fs), dict(time_s=t, reference_v=ref, coil_v=.016 * np.cos(theta + phase))

    def collect(instr, config, index, rate, **kwargs):
        assert kwargs["strict_cleanup"] and config.grid_mode == 4 and kwargs["requested_rate"] == p.demod0_rate_sa_s
        assert temp.channels[2]["offset"] == 0.
        events.append(("daq", config.duration, center()))
        t = np.arange(config.grid_cols) / rate
        theta = np.deg2rad(center() + 1.)
        if settings["mod_state"]:
            f = settings["mod_pm_internal_freq"]
            theta += np.deg2rad(1.) * np.sin(2 * np.pi * f * t) / np.sqrt(1 + (f / 85.)**2)
        phase_shift = np.deg2rad(lockin.get_double(lockin.demod_path(0) + "/phaseshift"))
        z = (.1j * np.sin(theta) + .02 * np.cos(theta)) * np.exp(-1j * phase_shift)
        snaps = dict(actual_rate=rate, time_source="module_timestamp", acquisition_module="daq", unit="V")
        return [SimpleNamespace(signal_name=f"sample.{key}", values=signal + rnd.normal(0, 6e-6, t.size),
            time=t, record_slices=[(0, t.size)], config_snapshot=snaps.copy()) for key, signal in (("x", z.real), ("y", z.imag))]

    monkeypatch.setattr(monitor, "acquire", capture)
    monkeypatch.setattr(acquisition.daq, "acquire_data", collect)
    original_write = run_format.write_frame

    def checked_write(path, label, kind, metadata, **arrays):
        assert temp.channels[2]["offset"] == 5., "写帧前温控必须恢复"
        events.append(("disk", label))
        return original_write(path, label, kind, metadata, **arrays)

    monkeypatch.setattr(run_format, "write_frame", checked_write)
    return SimpleNamespace(events=events, p=p, xy=xygen, z=zgen, temp=temp, gs=gs_state, settings=settings, lockin=lockin)


def _ctx(rig):
    return Workflow(rig.p, project_root=ROOT)


def _assert_parked(rig):
    assert rig.settings["shape"] == "DC" and not rig.settings["output"] and rig.settings["offset"] == 0.
    assert not rig.settings["mod_state"] and not rig.settings["burst_state"]
    assert rig.z.channels[1]["output"] is False and rig.z.channels[2]["output"] is True
    assert rig.temp.channels[2]["offset"] == 5. and rig.temp.channels[2]["output"] is True
    assert rig.gs["output"] and rig.gs["current"] == -.001


def test_preflight_and_defaults():
    p = RfPhaseSensitivityParams.from_yaml(ROOT / "params/experiments/rf-phase-sensitivity.yaml")
    assert p == RfPhaseSensitivityParams() and ADAPTER.preflight({}) == []
    assert ADAPTER.preflight({"Y_RF_VPP": 1., "Z_PEAK_CURRENT_MA": 1000})
    assert ADAPTER.preflight({"Z_PEAK_CURRENT_MA": 301})
    assert ADAPTER.preflight({"Z_PULSE_WIDTH_US": 300})
    assert ADAPTER.preflight({"PM_FREQUENCIES_HZ": [5., 5., 20.]})
    assert ADAPTER.preflight({"PM_FREQUENCIES_HZ": [1., 5., 20.]})
    assert ADAPTER.preflight({"SYSTEM_SETTLE_S": 0.101, "DEMOD0_TC": .01})


def test_full_round_and_physical_phase_transfer(rig):
    run_dir = Workflow.run(rig.p, project_root=ROOT)
    c = run_format.read_config(run_dir / "experiment_config.yaml")
    assert c["completion_status"] == "completed" and c["calibration"]["accepted"] and c["noise"]["accepted"], c
    assert abs(c["calibration"]["k0_v_per_rad"]) == pytest.approx(.10, rel=.02)
    assert abs(c["calibration"]["slope_x_v_per_rad"]) < 1e-3
    assert c["pm_alignment"]["measured_delta_deg"] == pytest.approx(30., abs=.04)
    assert [point["frequency_hz"] for point in c["transfer"] if point["accepted"]] == [20., 40., 20.]
    assert [e[3] for e in rig.events if e[:3] == ("xy", 2, "set_mod_pm_deviation")] == [1.]
    assert c["noise"]["candidate"]["carrier_center_deg"] != pytest.approx(c["noise"]["after"]["carrier_center_deg"], abs=5.)
    assert c["safety_shutdown"]["completed"] and c["shutdown_readbacks"]["Temp_Switch"]["dc_setting_v"] == 5.
    assert len(c["data_files"]) == len(list((run_dir / "raw").glob("*.npz")))
    metadata, arrays = run_format.read_frame(run_dir, "noise_xy", "xy")
    assert metadata["rate_sa_s"] == 1000. and len(arrays["time_s"]) == 8000
    assert ("scope", "restore") in rig.events
    shutdown = max(i for i, event in enumerate(rig.events) if event == ("amp", False))
    assert rig.events.index(("scope", "restore")) < rig.events.index(("temp", "disconnect"))
    assert rig.events[-3:] == [("temp", "disconnect"), ("z", "disconnect"), ("xy", "disconnect")]
    for device, channel in (("z", 1), ("xy", 2), ("xy", 1)):
        end = rig.events[shutdown:]
        assert end.index((device, channel, "set_output", False)) < end.index((device, channel, "set_shape", "DC"))
        assert (device, channel, "set_output", True) not in end
    for i, event in enumerate(rig.events):
        if event[0] != "daq":
            continue
        assert ("temp", 2, "set_offset", 0.) in rig.events[i-4:i]
        assert rig.events[i-1][0] == "wait"
        first_disk = next(j for j in range(i + 1, len(rig.events)) if rig.events[j][0] == "disk")
        assert ("temp", 2, "set_offset", 5.) in rig.events[i:first_disk]
    _assert_parked(rig)
    result = analysis.analyze(run_dir)
    assert result["quality_passed"] and result["phase_referral"]["available"], result
    assert result["transfer_bands_hz"] == [[20., 40.]]
    assert result["k0_closure"]["normalized_gain"] == pytest.approx(1 / np.sqrt(1 + (20. / 85.)**2), rel=.01)
    assert (run_dir / "results" / run_format.NOISE_PLOT).is_file()


def test_z_readback_mismatch_keeps_amplifier_off(rig, monkeypatch):
    monkeypatch.setattr(rig.z, "get_arb_points", lambda **kw: 2, raising=False)
    with pytest.raises(SafetyAbort, match="Z 双脉冲写入回读不符"):
        Workflow.run(rig.p, project_root=ROOT)
    assert ("amp", True) not in rig.events
    assert not any(event[0] == "daq" for event in rig.events)
    _assert_parked(rig)


def test_stepwise_rerun_keeps_prior_run(rig):
    ctx = _ctx(rig)
    try:
        with pytest.raises(ValueError, match="缺少前置"):
            ctx.execute(["A03"])
        ctx.execute(["A01", "A02", "A03"])
        original = ctx.directory.root
        baseline = (original / "experiment_config.yaml").read_bytes()
        ctx.update_parameters({"RUN_TAG": "rerun"})
        ctx.execute(["A01", "A02", "A03"])
        assert ctx.directory.root != original and (original / "experiment_config.yaml").read_bytes() == baseline
    finally:
        ctx.finish()
    _assert_parked(rig)


def test_cancel_preserves_completed_frames(rig, monkeypatch):
    original = acquisition.daq.acquire_data
    def read(*args, **kwargs):
        if any(e == ("disk", "coarse_00") for e in rig.events):
            raise WorkflowCancelled("注入取消")
        return original(*args, **kwargs)
    monkeypatch.setattr(acquisition.daq, "acquire_data", read)
    ctx = _ctx(rig)
    with pytest.raises(WorkflowCancelled, match="注入取消") as caught:
        try:
            ctx.execute(["A01", "A02", "A03"])
        finally:
            import sys
            ctx.finish(sys.exc_info()[1])
    assert str(caught.value) == "注入取消"
    c = run_format.read_config(ctx.directory.config_path)
    assert c["completion_status"] == "cancelled"
    assert c["data_files"] == ["raw/coarse_00.npz"] and c["safety_shutdown"]["completed"]
    _assert_parked(rig)


def test_uses_strict_daq_and_rejects_synthetic_time(rig, monkeypatch):
    original = acquisition.daq.acquire_data
    def read(*args, **kwargs):
        out = original(*args, **kwargs)
        for frame in out:
            frame.config_snapshot["time_source"] = "synthetic_grid"
        return out
    monkeypatch.setattr(acquisition.daq, "acquire_data", read)
    with pytest.raises(SafetyAbort, match="真实同步时间戳"):
        Workflow.run(rig.p, project_root=ROOT)
    _assert_parked(rig)
    assert not any(e[0] == "disk" for e in rig.events)


def test_restore_failure_saves_record_then_aborts(rig, monkeypatch):
    original = acquisition.daq.acquire_data
    def read(*args, **kwargs):
        rig.temp.restore_failures = 1
        return original(*args, **kwargs)
    monkeypatch.setattr(acquisition.daq, "acquire_data", read)
    with pytest.raises(IOError, match="注入温控恢复错误"):
        Workflow.run(rig.p, project_root=ROOT)
    assert ("disk", "coarse_00") in rig.events
    _assert_parked(rig)


def test_save_failure_keeps_previous_frames(rig, monkeypatch):
    original = run_format.write_frame
    def save(path, label, *args, **kwargs):
        if label == "coarse_01":
            raise OSError("模拟磁盘写入失败")
        return original(path, label, *args, **kwargs)
    monkeypatch.setattr(run_format, "write_frame", save)
    with pytest.raises(OSError, match="模拟磁盘写入失败"):
        Workflow.run(rig.p, project_root=ROOT)
    assert ("disk", "coarse_00") in rig.events
    assert ("disk", "coarse_01") not in rig.events
    _assert_parked(rig)


def test_scope_monitor_one_frame_and_mismatched_axis(monkeypatch):
    actual_rate = 100_000.
    t = np.arange(50_000) / actual_rate
    # 此测试只验证采集边界；调制解算用独立标量哨兵。
    def shot(ch, v, tx=t):
        return SimpleNamespace(channel=ch, time=tx, voltage=v, preamble_dict={"xincrement": 1 / actual_rate},
                               config_snapshot={"points": len(t)}, timestamp=1.)
    frames = [shot(3, np.full(t.size, 2.5)), shot(4, np.full(t.size, 0.026))]
    fake = Mock()
    fake.acquire_all.return_value = frames
    factory = Mock(return_value=fake)
    monkeypatch.setattr(monitor, "DHO900Acquisition", factory)
    ctx = SimpleNamespace(params=RfPhaseSensitivityParams(), devices={"scope": Mock()}, scope_touched=False)
    metadata, arrays = monitor.acquire(ctx, 20.)
    assert metadata["rate_sa_s"] == pytest.approx(actual_rate) and len(arrays["time_s"]) == 50_000
    cfg = fake.acquire_all.call_args.args[0]
    assert cfg.sampling_time == pytest.approx(.5) and cfg.acquire_delay == 1.5
    assert sorted(ch.number for ch in cfg.channels) == [3, 4]
    frames[-1] = shot(4, arrays["coil_v"], t + 1e-5)
    with pytest.raises(SafetyAbort, match="时间轴"):
        monitor.acquire(ctx, 20.)


def test_pm_and_am_are_separated_using_same_frame():
    t = np.arange(50_000) / 100_000.
    theta = 2 * np.pi * 5000 * t
    m = 2 * np.pi * 20 * t
    ref = 5. * (np.sin(theta + .1) >= 0.)
    coil = .018 * (1 + .0007 * np.cos(m)) * np.cos(theta + .08 + np.deg2rad(1.) * np.sin(m))
    got = signal_fit.measure_electrical(t, ref, coil, 5000., 20.)
    assert got["stable"] and math.degrees(got["phase_pk_rad"]) == pytest.approx(1., abs=.002)
    assert got["am_fraction_pk"] == pytest.approx(.0007, abs=1e-5)
