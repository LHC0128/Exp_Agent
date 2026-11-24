"""新实验的微型真实编排、当前格式和安全路径；不连接仪器。"""
from __future__ import annotations

from dataclasses import replace
import json
import math
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from ...common import WorkflowCancelled
from ...experiment_runtime import PROGRESS_PREFIX, PROGRESS_PROTOCOL_ENV
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
                                 dc_level=0., output_load="INF", voltage_unit="VPP", burst_state=False,
                                 mod_state=False, burst_delay=0., phase_adjust=0., sync_state=False) for i in (1, 2)}
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

    def set_offset(self, value, *, channel):
        self.events.append((self.name, channel, "set_offset", value))
        # 复现 DG4162 固件 00.01.14：DC 波形下 OFFSET/APPLy 写入被忽略，必须先离开 DC。
        if self.name in {"xy", "z"} and self.channels[channel]["shape"].upper() == "DC":
            return
        self.channels[channel]["offset"] = value

    def set_dc_voltage(self, value, *, channel):
        ch = self.channels[channel]
        assert ch["shape"] == "DC", "必须先选择 DC 波形"
        if self.name == "temp" and value == 5. and ch["offset"] == 0. and self.restore_failures:
            self.restore_failures -= 1
            raise IOError("注入温控恢复错误")
        ch["dc_level"] = value
        if self.name == "xy" and channel == 1:
            # 模拟 DG4162 物理 DC 与 OFFSET? 不同步，两个寄存器分别写入。
            self.events.append((self.name, channel, "set_dc_voltage", value))
        else:
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
            field = {":STATe?": "output", ":BURSt:STATe?": "burst_state",
                     ":MOD:STATe?": "mod_state", ":SYNC:STATe?": "sync_state"}
            _, route, suffix = command.split(":", 2)
            return "1" if self.channels[int(route[-1])][field[":" + suffix]] else "0"
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
    xygen.channels[1]["offset"] = .16  # 专用 DC 命令不更新这个残留的普通偏置设定。
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
    monkeypatch.setenv(PROGRESS_PROTOCOL_ENV, "jsonl")
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
    for ch in (rig.xy.channels[1], rig.z.channels[1]):
        assert ch["shape"] == "DC" and ch["output"] is False and ch["offset"] == ch["dc_level"] == 0.
    assert rig.z.channels[2]["output"] is True
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


def test_full_round_and_physical_phase_transfer(rig, capsys):
    run_dir = Workflow.run(rig.p, project_root=ROOT)
    events = [json.loads(line.removeprefix(PROGRESS_PREFIX)) for line in capsys.readouterr().out.splitlines()
              if line.startswith(PROGRESS_PREFIX)]
    for scope, stage_id, count in (("当前静态扫描", "A03", len(acquisition._STATIC_GRID_DEG)),
                                   ("当前局部标定", "A04", 7),
                                   ("当前基带扫频", "A05", len(rig.p.sweep_order_hz))):
        updates = [event for event in events if event["data"].get("eta_scope") == scope]
        assert len(updates) == count and all(event["stage"] == stage_id for event in updates)
        assert [event["percent"] for event in updates] == sorted({event["percent"] for event in updates})
        assert all(event["data"]["estimated_remaining_seconds"] >= 0 for event in updates)
        assert updates[-1]["data"]["estimated_remaining_seconds"] == 0
    c = run_format.read_config(run_dir / "experiment_config.yaml")
    assert c["completion_status"] == "completed" and c["calibration"]["accepted"] and c["noise"]["accepted"], c
    assert abs(c["calibration"]["k0_v_per_rad"]) == pytest.approx(.10, rel=.02)
    assert abs(c["calibration"]["slope_x_v_per_rad"]) < 1e-3
    assert c["pm_alignment"]["measured_delta_deg"] == pytest.approx(30., abs=.04)
    assert [point["frequency_hz"] for point in c["transfer"] if point["accepted"]] == [20., 40., 20.]
    assert [e[3] for e in rig.events if e[:3] == ("xy", 2, "set_mod_pm_deviation")] == [1.]
    assert c["noise"]["candidate"]["carrier_center_deg"] != pytest.approx(c["noise"]["after"]["carrier_center_deg"], abs=5.)
    assert c["safety_shutdown"]["completed"] and c["shutdown_readbacks"]["Temp_Switch"]["dc_setting_v"] == 5.
    x = c["shutdown_readbacks"]["X_magnetic_field"]
    assert x == dict(channel=1, output=False, shape="DC", dc_commanded_v=0., dc_setting_v=0.,
                     burst=False, modulation=False)
    assert len(c["data_files"]) == len(list((run_dir / "raw").glob("*.npz")))
    metadata, arrays = run_format.read_frame(run_dir, "noise_xy", "xy")
    assert metadata["rate_sa_s"] == 1000. and len(arrays["time_s"]) == 8000
    assert ("scope", "restore") in rig.events
    shutdown = max(i for i, event in enumerate(rig.events) if event == ("amp", False))
    assert rig.events.index(("scope", "restore")) < rig.events.index(("temp", "disconnect"))
    assert rig.events[-3:] == [("temp", "disconnect"), ("z", "disconnect"), ("xy", "disconnect")]
    for device, channel in (("z", 1), ("xy", 2), ("xy", 1)):
        end = [event for event in rig.events[shutdown:] if event[:2] == (device, channel)]
        assert end[0] == end[-1] == (device, channel, "set_output", False)
        assert (device, channel, "set_output", True) not in end
        offset_write = end.index((device, channel, "set_offset", 0.))
        if (device, channel) == ("xy", 1):
            # X 收尾时已处于 DC：必须先切正弦写 OFFSET 0，再切回 DC。
            assert (end.index((device, channel, "set_shape", "SINusoid"))
                    < offset_write < end.index((device, channel, "set_shape", "DC")))
        else:
            assert offset_write < end.index((device, channel, "set_shape", "DC"))
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
        chain = ["A01", "A02", "A03", "A04", "A05", "A06"]
        ctx.execute(["A01", "A02", "A03"])
        ctx.execute(["A04"])
        ctx.execute(["A05"])
        ctx.execute(["A06"])
        original = ctx.directory.root
        baseline = (original / "experiment_config.yaml").read_bytes()
        assert ctx.states["A06"]["status"] == "completed" and ctx.round_sealed
        ctx.execute(chain)
        assert ctx.directory.root != original and (original / "experiment_config.yaml").read_bytes() == baseline
    finally:
        ctx.finish()
    _assert_parked(rig)


def test_interrupted_chain_must_rerun_whole_chain(rig, monkeypatch):
    ctx = _ctx(rig)
    chain = ["A01", "A02", "A03", "A04", "A05", "A06"]
    try:
        ctx.execute(["A01", "A02", "A03"])
        with pytest.raises(ValueError, match="同一轮不能改参数"):
            ctx.update_parameters({"NOISE_DURATION_S": 10.})
        ctx.execute(["A04"])
        original = ctx.directory.root
        frames = sorted((original / "raw").glob("*.npz"))
        real_frequency_response = acquisition.frequency_response
        monkeypatch.setattr(acquisition, "frequency_response", lambda ctx: (_ for _ in ()).throw(SafetyAbort("注入频响失败")))
        with pytest.raises(SafetyAbort, match="注入频响失败"):
            ctx.execute(["A05"])
        assert ctx.states["A05"]["status"] == "quality_failed"
        with pytest.raises(ValueError, match="请整链选择 A03"):
            ctx.execute(["A05"])
        monkeypatch.setattr(acquisition, "frequency_response", real_frequency_response)
        ctx.execute(chain)
        assert ctx.directory.root != original
        assert sorted((original / "raw").glob("*.npz")) == frames
        assert ctx.states["A06"]["status"] == "completed" and ctx.round_sealed
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


def test_failed_acquisition_preserves_failed_shutdown_readbacks(rig, monkeypatch):
    original_read = acquisition.daq.acquire_data
    original_offset = rig.xy.set_offset
    original_query = rig.xy.query

    def read(*args, **kwargs):
        if ("disk", "coarse_00") in rig.events:
            # 模拟普通 OFFSET 清零没有生效、Burst 回读含无效字串；不是对真机根因的假定。
            rig.xy.channels[1]["offset"] = .16  # 残留旧偏置，供 DC 回读不符断言使用
            monkeypatch.setattr(rig.xy, "set_offset", lambda v, *, channel:
                                None if channel == 1 else original_offset(v, channel=channel), raising=False)
            monkeypatch.setattr(rig.xy, "query", lambda cmd:
                                "?" if cmd == ":SOURce1:BURSt:STATe?" else original_query(cmd))
            raise WorkflowCancelled("注入取消")
        return original_read(*args, **kwargs)

    monkeypatch.setattr(acquisition.daq, "acquire_data", read)
    ctx = _ctx(rig)
    with pytest.raises(WorkflowCancelled, match="注入取消") as caught:
        ctx.execute(["A01", "A02", "A03"])
    ctx.finish(caught.value)
    c = run_format.read_config(ctx.directory.config_path)
    x = c["shutdown_readbacks"]["X_magnetic_field"]
    assert c["completion_status"] == "cancelled" and c["data_files"] == ["raw/coarse_00.npz"]
    assert not c["safety_shutdown"]["completed"] and ctx.states["A07"]["status"] == "failed"
    assert x["output"] is False and "dc_commanded_v" not in x and x["dc_setting_v"] == .16
    assert x["modulation"] is False and "burst" not in x
    assert len(x["errors"]) == 3 and "DC 电平" in x["errors"][0]
    assert "dc_setting_v" in x["errors"][1] and "burst" in x["errors"][2]
    assert ctx.outputs["X 偏置"] == "OFF（已回读），其余收尾未确认"
    assert c["shutdown_readbacks"]["Temp_Switch"] == dict(output=True, dc_setting_v=5.)
    assert c["main_field"]["restored_on_shutdown"]
    assert not rig.xy.channels[2]["output"] and not rig.z.channels[1]["output"]
    assert rig.events[-3:] == [("temp", "disconnect"), ("z", "disconnect"), ("xy", "disconnect")]


@pytest.mark.parametrize("fault", ["first_off", "dc_write"])
def test_shutdown_always_attempts_final_off(rig, monkeypatch, fault):
    ctx = _ctx(rig)
    try:
        ctx.execute(["A01", "A02"])
        ch = rig.xy.channels[1]
        ch.update(output=True, dc_level=.16)
        if fault == "first_off":
            original_output = rig.xy.set_output

            def skip_first_off(value, *, channel):
                if ch["output"] and not ("xy", channel, "first_off_lost") in rig.events:
                    rig.events.append(("xy", channel, "first_off_lost"))
                else:
                    original_output(value, channel=channel)

            monkeypatch.setattr(rig.xy, "set_output", skip_first_off, raising=False)
        else:
            def bad_dc_write(value, *, channel):
                assert not ch["output"]
                raise IOError("注入 DC 写入错误")

            monkeypatch.setattr(rig.xy, "set_offset", bad_dc_write)
        with pytest.raises(SafetyAbort, match="禁止改变波形" if fault == "first_off" else "注入 DC 写入错误"):
            preparation._off_and_zero(ctx, "x", "X_magnetic_field")
        x = ctx.state["shutdown_readbacks"]["X_magnetic_field"]
        assert x["output"] is False and not ch["output"]
        assert ch["dc_level"] == .16 and "dc_commanded_v" not in x
        assert ch["offset"] == .16
        assert ("xy", 1, "set_output", False) in rig.events
    finally:
        ctx.finish()


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
    assert ctx.devices["scope"].read_chunk_points == monitor._READ_CHUNK_POINTS
    assert metadata["rate_sa_s"] == pytest.approx(actual_rate) and len(arrays["time_s"]) == 50_000
    cfg = fake.acquire_all.call_args.args[0]
    assert cfg.sampling_time == pytest.approx(.5) and cfg.acquire_delay == 1.5
    assert sorted(ch.number for ch in cfg.channels) == [3, 4]
    frames[-1] = shot(4, arrays["coil_v"], t + 1e-5)
    with pytest.raises(SafetyAbort, match="时间轴"):
        monitor.acquire(ctx, 20.)


def test_scope_restore_memory_depth_normalizes_auto_points():
    scope = Mock()
    monitor._restore_memory_depth(scope, "6.2500E+05")
    scope.set_memory_depth.assert_called_once_with("AUTO")
    scope.set_memory_depth.reset_mock()
    monitor._restore_memory_depth(scope, "1.0000E+06")
    scope.set_memory_depth.assert_called_once_with("1000000")
    scope.set_memory_depth.reset_mock()
    monitor._restore_memory_depth(scope, "AUTO")
    scope.set_memory_depth.assert_called_once_with("AUTO")


def test_pm_alignment_iterates_nonlinear_command_gain(monkeypatch):
    """真实跟随增益约 1.7% 偏差时，一次 160° 外推会留下约 3° 残差，必须按实测迭代。"""

    def wrap(value):
        return (value + 180.) % 360. - 180.

    class Carrier:
        command_deg = 0.

        def begin_pm(self):
            return self.command_deg

        def phase_adjust(self, value, mode):
            assert mode == "pm"
            self.command_deg = value % 360.
            return self.command_deg

    def center():
        command = carrier.command_deg
        return wrap(160. + 0.983 * command + 2e-4 * command * command)

    def point():
        return dict(stable=True, carrier_center_deg=center(), carrier_v_pk=0.016,
                    phase_pk_rad=math.radians(1.), phase_scatter_rad=1e-4, am_fraction_pk=1e-5)

    carrier = Carrier()
    ctx = SimpleNamespace(state={}, devices={}, outputs={}, params=RfPhaseSensitivityParams())
    monkeypatch.setattr(acquisition, "_electrical", lambda ctx, label, settings, f=None: point())
    anchor = acquisition._align_pm(ctx, carrier, dict(carrier_center_deg=0., carrier_v_pk=0.016))
    assert abs(anchor) <= acquisition._PM_ALIGN_TOL_DEG
    assert len(ctx.state["pm_alignment"]["attempts"]) == 2


def test_scope_error_drain_separates_legacy_errors():
    scope = Mock()
    scope.query.side_effect = ['-232,"Format err"', '0,"No error"']
    assert monitor._drain_errors(scope) == ['-232,"Format err"']
    scope.query.side_effect = ['0,"No error"']
    assert monitor._drain_errors(scope) == []


def test_pm_and_am_are_separated_using_same_frame():
    t = np.arange(50_000) / 100_000.
    theta = 2 * np.pi * 5000 * t
    m = 2 * np.pi * 20 * t
    ref = 5. * (np.sin(theta + .1) >= 0.)
    coil = .018 * (1 + .0007 * np.cos(m)) * np.cos(theta + .08 + np.deg2rad(1.) * np.sin(m))
    got = signal_fit.measure_electrical(t, ref, coil, 5000., 20.)
    assert got["stable"] and math.degrees(got["phase_pk_rad"]) == pytest.approx(1., abs=.002)
    assert got["am_fraction_pk"] == pytest.approx(.0007, abs=1e-5)
