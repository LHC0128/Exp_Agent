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

from ...common import WorkflowCancelled, load_mapping
from ...experiment_runtime import PROGRESS_PREFIX, PROGRESS_PROTOCOL_ENV
from ...steps import RoutedSignalGenerator, SignalGeneratorRoute
from ...steps.coil_drive_chain import SafetyAbort
from . import acquisition, analysis, preparation, run_format, signal_fit
from .definition import ADAPTER, DEFINITION
from .models import MOD_CARRIER_OSC_IDX, MOD_SIDEBAND_OSC_IDX, READOUT_DEMOD_IDX, RfPhaseSensitivityParams
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
    device_id = "dev30661"
    acquisition_mode = "daq"

    def __init__(self, events):
        self.events = events
        self.capabilities = {
            "model": "MFLI",
            "options": ["MF-F5M", "MF-MD", "MF-MOD", "MF-DIG"],
            "demodulators": 4,
            "oscillators": 4,
            "voltage_inputs": 1,
            "signal_outputs": 1,
            "aux_outputs": 4,
            "max_frequency_hz": 5e6,
        }
        dev = self.device_id
        self.values = {
            f"/{dev}/sigouts/0/on": 0,
            f"/{dev}/sigouts/0/add": 0,
            f"/{dev}/sigouts/0/range": 1.0,
            f"/{dev}/sigouts/0/offset": 0.0,
            f"/{dev}/mods/0/enable": 0,
            f"/{dev}/mods/0/mode": 1,
            f"/{dev}/mods/0/freqdevenable": 0,
            f"/{dev}/mods/0/output": 0,
            f"/{dev}/mods/0/carrier/enable": 0,
            f"/{dev}/mods/0/carrier/oscselect": 0,
            f"/{dev}/mods/0/carrier/amplitude": 0.0,
            f"/{dev}/mods/0/carrier/phaseshift": 0.0,
            f"/{dev}/mods/0/sidebands/0/enable": 0,
            f"/{dev}/mods/0/sidebands/1/enable": 0,
            f"/{dev}/mods/0/sidebands/0/oscselect": 0,
            f"/{dev}/mods/0/sidebands/1/oscselect": 0,
            f"/{dev}/mods/0/index": 0.0,
        }

    def demod_path(self, i):
        return f"/{self.device_id}/demods/{i}"

    def osc_path(self, i):
        return f"/{self.device_id}/oscs/{i}"

    def sig_in_path(self, i):
        return f"/{self.device_id}/sigins/{i}"

    def set_double(self, path, value):
        self.events.append(("mfli", path, float(value)))
        self.values[path] = float(value)

    def get_double(self, path):
        return self.values[path]

    def set_int(self, path, value):
        self.events.append(("mfli", path, int(value)))
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
    lockin = FakeLockin(events)
    channels = dict(x=1, z=2, trigger=3, temp=4)
    proxy = RoutedSignalGenerator({i: SignalGeneratorRoute(dev, c, name) for i, (name, dev, c) in enumerate(
        (("x", xygen, 1), ("z", zgen, 1), ("trigger", zgen, 2), ("temp", temp, 2)), 1)})
    devices = dict(hf2=lockin, amp=amp, gs200=gs, signals=proxy, channels=channels, tec=tec)

    def connect(ctx, **kwargs):
        preparation._require_mfli_capabilities(lockin.capabilities, require_mod_option=True)
        for name, device in (("xy", xygen), ("z", zgen), ("temp", temp), ("hf2", lockin),
                             ("amp", amp), ("gs200", gs), ("tec", tec)):
            ctx.device_session.connect(name, name, lambda device=device: device)
        ctx.devices.update(devices)

    monkeypatch.setattr(preparation, "_connect_devices", connect)
    monkeypatch.setattr(preparation, "clocks", lambda ctx: {"fake": "EXT"})
    monkeypatch.setattr(acquisition, "sleep", lambda s: events.append(("wait", s)))
    monkeypatch.setattr(acquisition, "_STATIC_GRID_DEG", tuple(range(0, 360, 45)))
    monkeypatch.setenv(PROGRESS_PROTOCOL_ENV, "jsonl")
    p = replace(RfPhaseSensitivityParams(), pm_frequencies_hz=[20., 40.], pm_tone_duration_s=.5,
                noise_duration_s=8., welch_segment_s=2., static_point_duration_s=.05,
                demod0_rate_sa_s=1000., demod0_dynamic_rate_sa_s=2000., system_settle_s=.1, temp_switch_on_lag_s=.1)
    rnd = np.random.default_rng(7429)
    dev = lockin.device_id

    def center():
        # MF-MOD 保持统一 10 MHz 相干参考，载波中心由 MFLI 相位指令直接决定。
        return float(lockin.values[f"/{dev}/mods/0/carrier/phaseshift"]) + 37.0

    def collect(instr, config, index, rate, **kwargs):
        assert index == READOUT_DEMOD_IDX
        tc_now = lockin.values[f"/{dev}/demods/{READOUT_DEMOD_IDX}/timeconstant"]
        expected_req = (p.demod0_dynamic_rate_sa_s
                        if math.isclose(tc_now, p.demod0_dynamic_tc_s, rel_tol=1e-6) else p.demod0_rate_sa_s)
        assert kwargs["strict_cleanup"] and config.grid_mode == 4 and kwargs["requested_rate"] == expected_req
        assert temp.channels[2]["offset"] == 0.
        events.append(("daq", config.duration, center()))
        t = np.arange(config.grid_cols) / rate
        theta = np.deg2rad(center() + 1.)
        sb_on = lockin.values[f"/{dev}/mods/0/sidebands/0/enable"] and lockin.values[f"/{dev}/mods/0/sidebands/1/enable"]
        if lockin.values[f"/{dev}/mods/0/enable"] and sb_on:
            f = lockin.values[f"/{dev}/oscs/{MOD_SIDEBAND_OSC_IDX}/freq"]
            theta += lockin.values[f"/{dev}/mods/0/index"] * np.sin(2 * np.pi * f * t) / np.sqrt(1 + (f / 85.)**2)
        phase_shift = np.deg2rad(lockin.get_double(lockin.demod_path(READOUT_DEMOD_IDX) + "/phaseshift"))
        z = (.1j * np.sin(theta) + .02 * np.cos(theta)) * np.exp(-1j * phase_shift)
        snaps = dict(actual_rate=rate, time_source="module_timestamp", acquisition_module="daq", unit="V")
        return [SimpleNamespace(signal_name=f"sample.{key}", values=signal + rnd.normal(0, 6e-6, t.size),
            time=t, record_slices=[(0, t.size)], config_snapshot=snaps.copy()) for key, signal in (("x", z.real), ("y", z.imag))]

    monkeypatch.setattr(acquisition.daq, "acquire_data", collect)
    original_write = run_format.write_frame

    def checked_write(path, label, kind, metadata, **arrays):
        assert temp.channels[2]["offset"] == 5., "写帧前温控必须恢复"
        events.append(("disk", label))
        return original_write(path, label, kind, metadata, **arrays)

    monkeypatch.setattr(run_format, "write_frame", checked_write)
    return SimpleNamespace(events=events, p=p, xy=xygen, z=zgen, temp=temp, gs=gs_state, lockin=lockin)


def _ctx(rig):
    return Workflow(rig.p, project_root=ROOT)


def _assert_parked(rig):
    dev = rig.lockin.device_id
    vals = rig.lockin.values
    assert vals[f"/{dev}/sigouts/0/on"] == 0
    assert vals[f"/{dev}/sigouts/0/offset"] == 0.0
    assert vals[f"/{dev}/mods/0/enable"] == 0
    assert vals[f"/{dev}/mods/0/output"] == 0
    assert vals[f"/{dev}/mods/0/sidebands/0/enable"] == 0 and vals[f"/{dev}/mods/0/sidebands/1/enable"] == 0
    assert vals[f"/{dev}/mods/0/carrier/amplitude"] == 0.0
    assert vals[f"/{dev}/demods/{READOUT_DEMOD_IDX}/enable"] == 0
    for ch in (rig.xy.channels[1], rig.z.channels[1]):
        assert ch["shape"] == "DC" and ch["output"] is False and ch["offset"] == ch["dc_level"] == 0.
    assert rig.z.channels[2]["output"] is True
    assert rig.temp.channels[2]["offset"] == 5. and rig.temp.channels[2]["output"] is True
    assert rig.gs["output"] and rig.gs["current"] == -.001


def test_preflight_and_defaults():
    p = RfPhaseSensitivityParams.from_yaml(ROOT / "params/experiments/rf-phase-sensitivity.yaml")
    assert p == RfPhaseSensitivityParams() and p.demod0_idx == 3 and ADAPTER.preflight({}) == []
    assert p.demod0_tc_s == pytest.approx(0.001) and p.demod0_rate_sa_s == pytest.approx(5000.)
    assert p.demod0_dynamic_tc_s == pytest.approx(1e-6) and p.demod0_dynamic_rate_sa_s == pytest.approx(50000.)
    assert p.pm_frequencies_hz == [float(f) for f in range(10, 201, 10)]
    assert ADAPTER.preflight({"Y_RF_VPP": 0.2}) == []
    assert ADAPTER.preflight({"Y_RF_VPP": 2.0, "Y_BIAS_V": 1.0}) == []
    assert ADAPTER.preflight({"Y_RF_VPP": 21.0})
    assert ADAPTER.preflight({"Y_RF_VPP": 2.0, "Y_BIAS_V": 9.5})
    assert ADAPTER.preflight({"Y_RF_FREQ_HZ": 6000.0})
    assert ADAPTER.preflight({"DEMOD0_IDX": 0})
    assert ADAPTER.preflight({"Z_PEAK_CURRENT_MA": 1000})
    assert ADAPTER.preflight({"Z_PEAK_CURRENT_MA": 301})
    assert ADAPTER.preflight({"Z_PULSE_WIDTH_US": 300})
    assert ADAPTER.preflight({"PM_FREQUENCIES_HZ": [5., 5., 20.]})
    assert ADAPTER.preflight({"PM_FREQUENCIES_HZ": [1., 5., 20.]})
    assert ADAPTER.preflight({"SYSTEM_SETTLE_S": 0.101, "DEMOD0_TC": .01})
    assert ADAPTER.preflight({"DEMOD0_DYNAMIC_RATE": 1500.})


def test_legacy_demod0_history_config_remains_readable():
    legacy = RfPhaseSensitivityParams(demod0_idx=0, y_rf_vpp=0.5)
    v1_params = legacy.to_external()
    v1_params.pop("DEMOD0_DYNAMIC_TC")
    v1_params.pop("DEMOD0_DYNAMIC_RATE")
    v1_params["DEMOD0_RATE"] = 2000.0
    cfg = run_format.initial_metadata(legacy) | {
        "completion_status": "completed",
        "schema_version": 1,
        "parameters": v1_params,
    }
    assert run_format.format_block_reason(cfg) == ""
    assert run_format.analysis_block_reason(cfg) == ""
    migrated = run_format.parameters(cfg)
    assert migrated.demod0_dynamic_tc_s == pytest.approx(migrated.demod0_tc_s)
    assert migrated.demod0_dynamic_rate_sa_s == pytest.approx(2000.0)


def test_connect_rejects_missing_mod_option(rig):
    rig.lockin.capabilities["options"] = ["MF-F5M", "MF-MD", "MF-DIG"]
    with pytest.raises(ValueError, match="MF-MOD"):
        Workflow.run(rig.p, project_root=ROOT)


def test_full_round_and_physical_phase_transfer(rig, capsys):
    run_dir = Workflow.run(rig.p, project_root=ROOT)
    events = [json.loads(line.removeprefix(PROGRESS_PREFIX)) for line in capsys.readouterr().out.splitlines()
              if line.startswith(PROGRESS_PREFIX)]
    for scope, stage_id, count in (("当前静态扫描", "A03", len(acquisition._STATIC_GRID_DEG)),
                                   ("当前局部标定", "A04", len(acquisition.local_offsets(rig.p.local_span_deg))),
                                   ("当前基带扫频", "A05", len(rig.p.sweep_order_hz))):
        updates = [event for event in events if event["data"].get("eta_scope") == scope]
        assert len(updates) == count and all(event["stage"] == stage_id for event in updates)
        assert [event["percent"] for event in updates] == sorted({event["percent"] for event in updates})
        assert all(event["data"]["estimated_remaining_seconds"] >= 0 for event in updates)
        assert updates[-1]["data"]["estimated_remaining_seconds"] == 0
    c = run_format.read_config(run_dir / "experiment_config.yaml")
    assert c["completion_status"] == "completed" and c["calibration"]["accepted"] and c["noise"]["accepted"], c
    assert c["lockin"]["demod_idx"] == READOUT_DEMOD_IDX and c["lockin"]["osc_select"] == MOD_CARRIER_OSC_IDX
    assert abs(c["calibration"]["k0_v_per_rad"]) == pytest.approx(.10, rel=.02)
    assert abs(c["calibration"]["slope_x_v_per_rad"]) < 1e-3
    assert len(c["calibration"]["points"]) == len(acquisition.local_offsets(rig.p.local_span_deg)) == 35
    assert [point["frequency_hz"] for point in c["transfer"] if point["accepted"]] == [20., 40.]
    assert all(point["repeat"] is False and point["scope_label"] is None for point in c["transfer"])
    dev = rig.lockin.device_id
    mod_index_writes = [e[2] for e in rig.events if e[:2] == ("mfli", f"/{dev}/mods/0/index")]
    assert mod_index_writes and all(math.isclose(v, math.radians(2.0), rel_tol=1e-6) for v in mod_index_writes)
    assert c["safety_shutdown"]["completed"] and c["shutdown_readbacks"]["Temp_Switch"]["dc_setting_v"] == 5.
    x = c["shutdown_readbacks"]["X_magnetic_field"]
    assert x == dict(channel=1, output=False, shape="DC", dc_commanded_v=0., dc_setting_v=0.,
                     burst=False, modulation=False)
    sigout_key = preparation.mfli_sigout_safety_key(load_mapping(ROOT))
    mfli_shut = c["shutdown_readbacks"][sigout_key]
    assert mfli_shut["output"] is False and mfli_shut["mod_enable"] == 0 and mfli_shut["offset_v"] == 0.0
    assert mfli_shut["carrier_amplitude_vpk"] == 0.0
    assert len(c["data_files"]) == len(list((run_dir / "raw").glob("*.npz")))
    coarse_meta, _ = run_format.read_frame(run_dir, "coarse_00", "xy")
    assert coarse_meta["rate_sa_s"] == 1000. and coarse_meta["lockin"]["time_constant_s"] == pytest.approx(0.001)
    metadata, arrays = run_format.read_frame(run_dir, "noise_xy", "xy")
    assert metadata["rate_sa_s"] == 2000. and metadata["lockin"]["time_constant_s"] == pytest.approx(1e-6) and len(arrays["time_s"]) == 16000
    assert not any(e[0] == "scope" for e in rig.events)
    assert "DHO924" not in DEFINITION.required_devices
    assert "coil_current_scope" not in run_format.REQUIRED_MAPPING_KEYS
    shutdown = max(i for i, event in enumerate(rig.events) if event == ("amp", False))
    assert rig.events[-3:] == [("temp", "disconnect"), ("z", "disconnect"), ("xy", "disconnect")]
    for device, channel in (("z", 1), ("xy", 1)):
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
    assert result["anchor_repeat"]["configured"] is False
    assert result["transfer_bands_hz"] == [[20., 40.]]
    assert result["k0_closure"]["normalized_gain"] == pytest.approx(1 / np.sqrt(1 + (20. / 85.)**2), rel=.01)
    assert result["transfer_lorentzian_fit"]["available"] and result["transfer_lorentzian_fit"]["fc_hz"] == pytest.approx(85., rel=.03)
    assert result["phase_referral"]["lorentzian_full_band"]["available"]
    assert "median_urad_per_sqrt_hz" in result["phase_referral"]["bands"][0]
    for artifact in (run_format.RESPONSE_PLOT, run_format.LOCAL_PLOT, run_format.TRANSFER_PLOT, run_format.NOISE_PLOT):
        assert (run_dir / "results" / artifact).is_file()


def test_editable_y_rf_vpp_and_xy_biases(rig):
    rig.p = replace(rig.p, y_rf_vpp=0.2, y_bias_v=0.45, x_bias_v=-0.2)
    run_dir = Workflow.run(rig.p, project_root=ROOT)
    dev = rig.lockin.device_id
    amp_writes = [e[2] for e in rig.events if e[:2] == ("mfli", f"/{dev}/mods/0/carrier/amplitude")]
    offset_writes = [e[2] for e in rig.events if e[:2] == ("mfli", f"/{dev}/sigouts/0/offset")]
    range_writes = [e[2] for e in rig.events if e[:2] == ("mfli", f"/{dev}/sigouts/0/range")]
    assert amp_writes[0] == pytest.approx(0.1) and amp_writes[-1] == 0.0
    assert offset_writes[0] == pytest.approx(0.45) and offset_writes[-1] == 0.0
    assert range_writes[0] == pytest.approx(1.0)
    assert ("xy", 1, "set_offset", -0.2) in rig.events
    c = run_format.read_config(run_dir / "experiment_config.yaml")
    assert c["parameters"]["Y_RF_VPP"] == pytest.approx(0.2)
    assert c["parameters"]["Y_BIAS_V"] == pytest.approx(0.45)
    assert c["parameters"]["X_BIAS_V"] == pytest.approx(-0.2)
    _assert_parked(rig)


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
    assert rig.lockin.values[f"/{rig.lockin.device_id}/sigouts/0/on"] == 0 and not rig.z.channels[1]["output"]
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


def test_mfli_rf_shutdown_always_attempts_final_off(rig, monkeypatch):
    ctx = _ctx(rig)
    dev = rig.lockin.device_id
    sigout_key = preparation.mfli_sigout_safety_key(load_mapping(ROOT))
    try:
        ctx.execute(["A01", "A02"])
        rig.lockin.values.update({
            f"/{dev}/sigouts/0/on": 1,
            f"/{dev}/sigouts/0/offset": 0.4,
            f"/{dev}/mods/0/enable": 1,
            f"/{dev}/mods/0/carrier/amplitude": 0.25,
        })
        orig_set_int = rig.lockin.set_int

        def skip_first_off(path, val):
            if path == f"/{dev}/sigouts/0/on" and val == 0 and ("mfli", "first_off_lost") not in rig.events:
                rig.events.append(("mfli", "first_off_lost"))
            else:
                orig_set_int(path, val)

        monkeypatch.setattr(rig.lockin, "set_int", skip_first_off)
        with pytest.raises(SafetyAbort, match="未回读为 OFF"):
            preparation._stop_mfli_rf(ctx)
        shut = ctx.state["shutdown_readbacks"][sigout_key]
        assert shut["output"] is False and rig.lockin.values[f"/{dev}/sigouts/0/on"] == 0
        assert rig.lockin.values[f"/{dev}/mods/0/enable"] == 0
        assert shut["offset_v"] == 0.4 and "offset_commanded_v" not in shut
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


def test_best_tangent_accepts_non_sinusoidal_high_rf_response():
    """去掉 15% 三分量残差门控后，高射频幅度（如 0.5 Vpp）下的陡峭非正弦过零仍应定位并对齐到 Y。"""
    grid = np.arange(0., 360., 15.)
    phi = np.deg2rad(signal_fit.wrap_deg(grid - 141.37))
    # 陡峭色散曲线含显著 3θ 等高次谐波（三分量模型 RMS 残差 > 0.15 * max_gain）
    dispersive = 0.35 * np.tanh(3.5 * np.sin(phi)) + 0.12 * np.sin(3 * phi) + 0.015 * np.cos(phi)
    values = dispersive * np.exp(1j * np.deg2rad(131.07))
    best, rot, fit = signal_fit.best_tangent(grid, values)
    assert fit["residual_rms_v"] > 0.15 * fit["tangent_v_per_command_rad"]
    assert abs(signal_fit.wrap_deg(best - 141.37)) < 3.0
    rotated = values * np.exp(-1j * np.deg2rad(rot))
    # 旋转后在 135°–150° 之间存在显著单向 Y 跳变
    assert rotated[10].imag - rotated[9].imag > 0.2


def test_preflight_allows_mapping_without_scope(tmp_path):
    import yaml
    config = tmp_path / "params"
    config.mkdir()
    for name in ("devices.yaml", "safety_limits.yaml"):
        (config / name).write_text((ROOT / "params" / name).read_text(encoding="utf-8"), encoding="utf-8")
    mapping = yaml.safe_load((ROOT / "params/mapping.yaml").read_text(encoding="utf-8"))
    mapping.get("mapping", {}).pop("coil_current_scope", None)
    (config / "mapping.yaml").write_text(yaml.safe_dump(mapping, allow_unicode=True), encoding="utf-8")
    preparation.preflight(RfPhaseSensitivityParams(), tmp_path)


def test_local_slope_dense_sweep_recovers_slope_with_linear_drift():
    """细角度扫描 + 时间项回归：强色散式 (2.2.2) 与时间线性漂移下仍应准确恢复中心斜率 K0。"""
    span = 15.0
    offsets = np.asarray(acquisition.local_offsets(span))
    centers = offsets + 0.08
    times = np.arange(offsets.size) * 2.0
    slope_true, r_true, drift = 1.80, 3.0, 5e-5
    y_ideal = signal_fit.eq222_y(np.deg2rad(centers), -0.03, slope_true, r_true ** 2, 0.0)
    values = 1j * (y_ideal + drift * times) + 0.002
    center_indices = (0, offsets.size - 1)
    center = float(np.mean(centers[list(center_indices)]))
    fitted = signal_fit.local_slopes(center, centers, times, values, span, center_indices=center_indices)
    assert fitted["local_model_mode"] == "eq_2_2_2"
    assert fitted["eq222_r"] == pytest.approx(r_true, rel=1e-4)
    assert fitted["k0_v_per_rad"] == pytest.approx(slope_true, abs=1e-5)


def test_local_slope_drift_warning_does_not_abort():
    """工作点中心漂移超 0.75° 或角度展宽略窄时记为告警，仍返回式 (2.2.2) 斜率供用户判断。"""
    span = 10.0
    offsets = np.asarray(acquisition.local_offsets(span))
    centers = offsets * 0.94
    centers[0] += 1.1
    centers[-1] += 1.1
    times = np.arange(offsets.size) * 2.0
    slope_true, r_true = 1.4, 2.2
    y_ideal = signal_fit.eq222_y(np.deg2rad(centers), 0.02, slope_true, r_true ** 2, 0.0)
    values = 1j * (y_ideal + 2e-5 * times)
    fitted = signal_fit.local_slopes(0.0, centers, times, values, span, center_indices=(0, offsets.size - 1))
    assert fitted["k0_v_per_rad"] == pytest.approx(slope_true, rel=5e-3)
    assert any("漂移" in msg for msg in fitted["local_warnings"])
    assert any("98%" in msg for msg in fitted["local_warnings"])


def test_advisory_warnings_do_not_withhold_phase_noise_deg(rig, monkeypatch):
    """低频闭合等参考告警只记录提示，完整采数并输出 4 幅图与 °/√Hz 灵敏度谱。"""
    rig.p = replace(rig.p, pm_frequencies_hz=[40., 80.])
    run_dir = Workflow.run(rig.p, project_root=ROOT)
    c = run_format.read_config(run_dir / "experiment_config.yaml")
    assert c["completion_status"] == "completed" and c["safety_shutdown"]["completed"]
    assert all(row["xy_label"] is not None and row["repeat"] is False for row in c["transfer"])
    assert c["noise"]["xy_label"] == "noise_xy" and c["noise"]["scope_labels"] == []
    result = analysis.analyze(run_dir)
    assert result["phase_referral"]["available"] is True
    assert result["accepted_primary_points"] == 2
    assert result["transfer_bands_hz"] == [[40., 80.]]
    assert "median_deg_per_sqrt_hz" in result["phase_referral"]["bands"][0]
    for artifact in (run_format.RESPONSE_PLOT, run_format.LOCAL_PLOT, run_format.TRANSFER_PLOT, run_format.NOISE_PLOT):
        assert (run_dir / "results" / artifact).stat().st_size > 0
    _assert_parked(rig)
