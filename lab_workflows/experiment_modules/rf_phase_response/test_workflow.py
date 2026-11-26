"""射频相位响应的离线回归：MFLI MF-MOD 载波链路、A01–A04 状态、raw 帧与 B01 分析。

只用临时目录、假仪器与合成数据，不连接硬件、不写运行目录。
"""
from __future__ import annotations

import csv
import json
import math
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest
import yaml

from ...common import WorkflowCancelled, load_mapping
from ...experiment_runtime import PROGRESS_PREFIX, PROGRESS_PROTOCOL_ENV
from ...steps import RoutedSignalGenerator, SignalGeneratorRoute
from ...steps.coil_drive_chain import SafetyAbort
from ..z_chain_common import rf_phase, signal_fit
from ..z_chain_common.rf_phase import MOD_CARRIER_OSC_IDX
from . import acquisition, analysis, analysis_steps, preparation, run_format, stages
from .definition import ADAPTER, DEFINITION
from .models import READOUT_DEMOD_IDX, RfPhaseResponseParams
from .workflow import Workflow

ROOT = Path(__file__).resolve().parents[3]
REGRESSION_CASES = {
    "rf-phase-response": {
        "analysis": {
            "sources": ["lab_workflows/experiment_modules/rf_phase_response/analysis_steps.py",
                        "lab_workflows/experiment_modules/rf_phase_response/run_format.py"],
            "tests": ["TestSyntheticEq222Analysis::test_synthetic_eq222_frames_recover_k0_and_work_point",
                      "TestSyntheticEq222Analysis::test_rotation_deg_aligns_raw_frames_before_fit",
                      "TestSyntheticEq222Analysis::test_staged_analysis_runs_on_synthetic_frames",
                      "TestLegacyCsvAnalysis::test_legacy_v2_csv_uses_eq222_without_rotation",
                      "TestLegacyCsvAnalysis::test_legacy_v1_csv_stays_three_component",
                      "TestLegacyCsvAnalysis::test_missing_frames_write_unavailable_summary"],
        },
        "parameters": {
            "sources": ["lab_workflows/experiment_modules/rf_phase_response/models.py",
                        "params/experiments/rf-phase-response.yaml"],
            "tests": ["test_defaults_and_preflight",
                      "test_legacy_v1_v2_configs_migrate_to_demod4",
                      "test_preflight_allows_mapping_without_scope_or_amplifier"],
        },
    },
}
ACQUISITION_CASES = {
    "rf-phase-response": {
        "mode": "single",
        "cases": {
            "order": ["test_full_round_mfli_carrier_scan_and_shutdown"],
            "slow_disk": ["test_full_round_mfli_carrier_scan_and_shutdown"],
            "cancel": ["test_cancel_preserves_completed_frames"],
            "acquire_failure": ["test_uses_strict_daq_and_rejects_synthetic_time"],
            "restore_failure": ["test_restore_failure_saves_record_then_aborts"],
            "save_failure": ["test_save_failure_keeps_previous_frames"],
            "partial_save": ["test_cancel_preserves_completed_frames",
                             "test_save_failure_keeps_previous_frames"],
            "entrypoints": ["test_full_round_mfli_carrier_scan_and_shutdown",
                            "test_stepwise_rerun_keeps_prior_run"],
        },
        "sources": ["lab_workflows/experiment_modules/rf_phase_response/preparation.py",
                    "lab_workflows/experiment_modules/rf_phase_response/acquisition.py",
                    "lab_workflows/experiment_modules/rf_phase_response/workflow.py",
                    "lab_workflows/experiment_modules/rf_phase_response/session.py",
                    "experiments/RfPhaseResponse.py"],
    },
}


class FakeGenerator:
    """保留真实协议的读写分离；setup_dc 和 setup_square 会隐式开启输出。"""

    def __init__(self, events, name):
        self.events, self.name = events, name
        self.channels = {i: dict(output=False, shape="DC", frequency=5000., amplitude=.05, offset=0.,
                                 dc_level=0., output_load="INF", voltage_unit="VPP", burst_state=False,
                                 mod_state=False, burst_delay=0., phase_adjust=0., sync_state=False)
                         for i in (1, 2)}
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


# A03 合成的物理响应：Demod 4 原始相量由固定三分量模型给出（raw 帧即测到的相量）。
_RAW_C0 = 0.012 + 0.008j
_RAW_PLUS1 = 0.004 - 0.003j
_RAW_MINUS1 = 0.0035 + 0.002j


def _raw_signal(command_deg, demod_phase_deg):
    phi = math.radians(command_deg)
    z = (_RAW_C0 + _RAW_PLUS1 * np.exp(1j * phi) + _RAW_MINUS1 * np.exp(-1j * phi))
    return z * np.exp(-1j * math.radians(demod_phase_deg))


@pytest.fixture
def rig(monkeypatch, tmp_path):
    monkeypatch.setenv("LAB_DATA_ROOT", str(tmp_path / "data"))
    events = []
    xygen, zgen, temp = (FakeGenerator(events, name) for name in ("xy", "z", "temp"))
    xygen.channels[1]["offset"] = .16  # 专用 DC 命令不更新这个残留的普通偏置设定。
    tec = Mock()
    gs = Mock()
    gs_state = dict(source_function="CURR", current=-0.001, output=True, current_range=0.01,
                    current_limit=0.02)
    for key in gs_state:
        getattr(gs, f"get_{key}").side_effect = lambda key=key: gs_state[key]
        getattr(gs, f"set_{key}").side_effect = lambda value, key=key: gs_state.update({key: value})
    lockin = FakeLockin(events)
    channels = dict(x=1, z=2, trigger=3, temp=4)
    proxy = RoutedSignalGenerator({i: SignalGeneratorRoute(dev, c, name) for i, (name, dev, c) in enumerate(
        (("x", xygen, 1), ("z", zgen, 1), ("trigger", zgen, 2), ("temp", temp, 2)), 1)})
    devices = dict(hf2=lockin, gs200=gs, signals=proxy, channels=channels, tec=tec)

    def connect(ctx, **kwargs):
        preparation._require_mfli_capabilities(lockin.capabilities, require_mod_option=True)
        for name, device in (("xy", xygen), ("z", zgen), ("temp", temp), ("hf2", lockin),
                             ("gs200", gs), ("tec", tec)):
            ctx.device_session.connect(name, name, lambda device=device: device)
        ctx.devices.update(devices)

    clock_calls = []

    def clocks(ctx):
        clock_calls.append("sync")
        return {"fake": "EXT"}

    monkeypatch.setattr(preparation, "_connect_devices", connect)
    monkeypatch.setattr(preparation, "clocks", clocks)
    monkeypatch.setattr(preparation, "sleep", lambda s: events.append(("wait", s)))
    monkeypatch.setattr(acquisition, "sleep", lambda s: events.append(("wait", s)))
    monkeypatch.setenv(PROGRESS_PROTOCOL_ENV, "jsonl")
    p = replace(RfPhaseResponseParams(), y_rf_phase_step_deg=45.0, static_point_duration_s=0.05,
                system_settle_s=0.0, temp_switch_on_lag_s=0.1, demod0_rate_sa_s=1000.0)
    rnd = np.random.default_rng(7429)
    dev = lockin.device_id

    def collect(instr, config, index, rate, **kwargs):
        assert index == READOUT_DEMOD_IDX
        assert kwargs["strict_cleanup"] and config.grid_mode == 4 and kwargs["requested_rate"] == p.demod0_rate_sa_s
        assert temp.channels[2]["offset"] == 0.
        command = float(lockin.values[f"/{dev}/mods/0/carrier/phaseshift"])
        demod_phase = float(lockin.values[f"{lockin.demod_path(READOUT_DEMOD_IDX)}/phaseshift"])
        z = _raw_signal(command, demod_phase)
        events.append(("daq", config.duration, command))
        t = np.arange(config.grid_cols) / rate
        snaps = dict(actual_rate=rate, time_source="module_timestamp", acquisition_module="daq", unit="V")
        return [SimpleNamespace(signal_name=f"sample.{key}", values=signal + rnd.normal(0, 6e-6, t.size),
            time=t, record_slices=[(0, t.size)], config_snapshot=snaps.copy())
            for key, signal in (("x", np.full(t.size, z.real)), ("y", np.full(t.size, z.imag)))]

    monkeypatch.setattr(rf_phase.daq, "acquire_data", collect)
    original_write = run_format.write_frame

    def checked_write(path, label, kind, metadata, **arrays):
        assert temp.channels[2]["offset"] == 5., "写帧前温控必须恢复"
        events.append(("disk", label))
        return original_write(path, label, kind, metadata, **arrays)

    monkeypatch.setattr(run_format, "write_frame", checked_write)
    return SimpleNamespace(events=events, p=p, xy=xygen, z=zgen, temp=temp, gs=gs_state,
                           lockin=lockin, clock_calls=clock_calls)


def _ctx(rig):
    return Workflow(rig.p, project_root=ROOT)


def _assert_parked(rig):
    dev = rig.lockin.device_id
    vals = rig.lockin.values
    assert not any(event[0] == "amp" for event in rig.events)
    assert not any(event[0] == "scope" for event in rig.events)
    assert vals[f"/{dev}/sigouts/0/on"] == 0
    assert vals[f"/{dev}/sigouts/0/offset"] == 0.0
    assert vals[f"/{dev}/mods/0/enable"] == 0
    assert vals[f"/{dev}/mods/0/output"] == 0
    assert vals[f"/{dev}/mods/0/sidebands/0/enable"] == 0
    assert vals[f"/{dev}/mods/0/sidebands/1/enable"] == 0
    assert vals[f"/{dev}/mods/0/carrier/amplitude"] == 0.0
    assert vals[f"/{dev}/demods/{READOUT_DEMOD_IDX}/enable"] == 0
    for ch in (rig.xy.channels[1], rig.z.channels[1]):
        assert ch["shape"] == "DC" and ch["output"] is False and ch["offset"] == 0.
    assert rig.z.channels[2]["output"] is True
    assert rig.temp.channels[2]["offset"] == 5. and rig.temp.channels[2]["output"] is True
    assert rig.gs["output"] and rig.gs["current"] == -.001


# ---------------------------------------------------------------- 参数与预检

def test_defaults_and_preflight():
    p = RfPhaseResponseParams.from_yaml(ROOT / "params/experiments/rf-phase-response.yaml")
    assert p == RfPhaseResponseParams()
    assert ADAPTER.preflight({}) == []
    assert p.demod0_idx == READOUT_DEMOD_IDX == 3
    assert p.y_rf_vpp == pytest.approx(0.5) and p.y_carrier_vpk == pytest.approx(0.25)
    assert p.y_pm_depth_rad == pytest.approx(math.radians(2.0))
    assert p.y_output_envelope(pm_enabled=True) == pytest.approx(
        (p.y_bias_v - (1 + math.radians(2.0)) * 0.25, p.y_bias_v + (1 + math.radians(2.0)) * 0.25))
    # 功放物理旁路：DG 直驱电压设定与回路电阻估算，拖尾补偿默认关闭。
    assert p.z_drive_amplifier_enabled is False and p.z_tail_comp_enabled is False
    assert p.z_pulse_vpp == pytest.approx(20.0) and p.z_pulse_width_us == pytest.approx(9.0)
    assert p.pulse_vpp == pytest.approx(20.0)
    assert p.loop_resistance_ohm == pytest.approx(10.0 + 10.0 + 2.0 + 50.0)
    assert p.estimated_peak_current_ma == pytest.approx(10.0 / 72.0 * 1e3)
    # 36 点整周网格：终点不写入。
    assert len(p.phase_angles_deg()) == 36 and p.phase_angles_deg()[-1] == pytest.approx(350.0)
    # 历史键仍在快照里但不再影响新链路。
    external = p.to_external()
    for key in ("Z_PEAK_CURRENT_MA", "AMPLIFIER_GAIN_DB", "RF_DIRECTION", "Y_RF_PHASE_DEG",
                "N_REPEAT", "TEMP_GROUP_SIZE"):
        assert key in external
    params, mapping = preparation.preflight(RfPhaseResponseParams(), ROOT)
    assert params == RfPhaseResponseParams()
    assert "Y_magnetic_field" not in run_format.REQUIRED_MAPPING_KEYS
    with pytest.raises(ValueError, match="Demod 4"):
        preparation.preflight(replace(RfPhaseResponseParams(), demod0_idx=0), ROOT)
    assert ADAPTER.preflight({"Z_PULSE_WIDTH_US": 150.0})
    assert ADAPTER.preflight({"Z_PULSE_VPP": 21.0})
    assert ADAPTER.preflight({"Z_DRIVE_SOURCE_IMPEDANCE_OHM": 0.5})
    assert ADAPTER.preflight({"Y_BIAS_V": 9.99})
    assert ADAPTER.preflight({"Y_RF_FREQ_HZ": 6000.0})
    assert ADAPTER.preflight({"Z_PULSE_VPP": 12.0, "Z_DRIVE_SOURCE_IMPEDANCE_OHM": 0.0}) == []


def test_legacy_v1_v2_configs_migrate_to_demod4():
    legacy = RfPhaseResponseParams()
    v2_parameters = legacy.to_external()
    v2_parameters["DEMOD0_IDX"] = 0
    v2_parameters["DEMOD0_PHASE_DEG"] = 90.0
    v2_config = run_format.initial_metadata(legacy) | {
        "raw_format_version": 2, "schema_version": 2, "parameters": v2_parameters,
        "completion_status": "completed",
    }
    assert run_format.format_block_reason(v2_config) == ""
    assert run_format.analysis_block_reason(v2_config) == ""
    migrated = run_format.parameters(v2_config)
    assert migrated.demod0_idx == READOUT_DEMOD_IDX
    assert "DEMOD0_PHASE_DEG" not in migrated.to_external()

    # 版本 1 的初始快照缺少后来加入的直驱与方向键，按默认值补齐后仍可读。
    v1_parameters = {key: value for key, value in v2_parameters.items()
                     if key not in {"Z_PULSE_VPP", "Z_DRIVE_SOURCE_IMPEDANCE_OHM",
                                    "MAIN_FIELD_CURRENT_MA", "RF_DIRECTION"}}
    v1_config = run_format.initial_metadata(legacy) | {
        "raw_format_version": 1, "schema_version": 1, "parameters": v1_parameters,
        "completion_status": "completed",
    }
    assert run_format.format_block_reason(v1_config) == ""
    migrated_v1 = run_format.parameters(v1_config)
    assert migrated_v1.z_pulse_vpp == pytest.approx(20.0)
    assert migrated_v1.z_drive_source_impedance_ohm == pytest.approx(50.0)
    assert migrated_v1.demod0_idx == READOUT_DEMOD_IDX
    assert run_format.format_block_reason({"experiment_id": "other"}) != ""


def test_preflight_allows_mapping_without_scope_or_amplifier(tmp_path):
    config = tmp_path / "params"
    config.mkdir()
    for name in ("devices.yaml", "safety_limits.yaml"):
        (config / name).write_text((ROOT / "params" / name).read_text(encoding="utf-8"),
                                   encoding="utf-8")
    mapping = yaml.safe_load((ROOT / "params/mapping.yaml").read_text(encoding="utf-8"))
    for key in ("coil_drive_amplifier", "coil_current_scope"):
        mapping.get("mapping", {}).pop(key, None)
    (config / "mapping.yaml").write_text(yaml.safe_dump(mapping, allow_unicode=True), encoding="utf-8")
    params, loaded = preparation.preflight(RfPhaseResponseParams(), tmp_path)
    assert params.z_drive_amplifier_enabled is False
    assert "coil_current_scope" not in run_format.REQUIRED_MAPPING_KEYS
    assert "Y_magnetic_field" not in run_format.REQUIRED_MAPPING_KEYS



# ---------------------------------------------------------------- A01–A04 全链路

def test_full_round_mfli_carrier_scan_and_shutdown(rig, capsys):
    run_dir = Workflow.run(rig.p, project_root=ROOT)
    events = [json.loads(line.removeprefix(PROGRESS_PREFIX)) for line in capsys.readouterr().out.splitlines()
              if line.startswith(PROGRESS_PREFIX)]
    assert any(event["stage"] == "A03" for event in events)
    phases = rig.p.phase_angles_deg()
    c = run_format.read_config(run_dir / "experiment_config.yaml")
    assert c["completion_status"] == "completed" and c["raw_format_version"] == 3
    assert c["safety_shutdown"]["completed"], c["safety_shutdown"]
    assert c["parameters"]["DEMOD0_IDX"] == READOUT_DEMOD_IDX
    assert c["lockin"]["demod_idx"] == READOUT_DEMOD_IDX
    assert c["lockin"]["osc_select"] == MOD_CARRIER_OSC_IDX
    assert c["lockin"]["rf_frequency_Hz"] == pytest.approx(5000.0)
    # 8 个相位点各写一帧，metadata 带指令相位与锁相/DAQ 快照。
    frames = sorted((run_dir / "raw").glob("coarse_*.npz"))
    assert len(frames) == len(phases) == 8
    assert c["data_files"] == [f"raw/{path.name}" for path in frames]
    meta, arrays = run_format.read_frame(run_dir, "coarse_00", "xy")
    assert meta["burst_command_deg"] == pytest.approx(0.0)
    assert meta["carrier_command_deg"] == pytest.approx(0.0)
    assert meta["lockin"]["oscillator_hz"] == pytest.approx(5000.0)
    assert meta["lockin"]["time_constant_s"] == pytest.approx(0.001)
    assert meta["daq"]["acquisition_module"] == "daq"
    assert arrays["time_s"].size == 50 and np.all(np.isfinite(arrays["y_v"]))
    phase = c["phase_response"]
    assert phase["grid_deg"] == phases and phase["points"] == len(phases)
    assert phase["burst_command_deg"] is not None and "rotation_deg" in phase
    assert 0.0 <= phase["burst_command_deg"] < 360.0 and abs(phase["rotation_deg"]) <= 180.0
    # 温控逐点门控：每次 DAQ 前关温控、DAQ 到写盘之间恢复 5 V 并等待。
    for index, event in enumerate(rig.events):
        if event[0] != "daq":
            continue
        assert ("temp", 2, "set_offset", 0.) in rig.events[index - 4:index]
        first_disk = next(j for j in range(index + 1, len(rig.events)) if rig.events[j][0] == "disk")
        assert ("temp", 2, "set_offset", 5.) in rig.events[index:first_disk]
        assert ("wait", rig.p.temp_switch_on_lag_s) in rig.events[index:first_disk]
    # MFLI MF-MOD 配置与 Demod 4 参考 Osc 2。
    dev = rig.lockin.device_id
    carrier_writes = [event[2] for event in rig.events if event[:2] == ("mfli", f"/{dev}/mods/0/carrier/amplitude")]
    assert carrier_writes[0] == pytest.approx(rig.p.y_carrier_vpk) and carrier_writes[-1] == 0.0
    offset_writes = [event[2] for event in rig.events if event[:2] == ("mfli", f"/{dev}/sigouts/0/offset")]
    assert offset_writes[0] == pytest.approx(rig.p.y_bias_v) and offset_writes[-1] == 0.0
    mod_index_writes = [event[2] for event in rig.events if event[:2] == ("mfli", f"/{dev}/mods/0/index")]
    assert mod_index_writes and all(math.isclose(v, math.radians(2.0), rel_tol=1e-6)
                                    for v in mod_index_writes)
    assert [event for event in rig.events if event[:2] == ("mfli", f"/{dev}/mods/0/carrier/oscselect")][0][2] == MOD_CARRIER_OSC_IDX
    assert rig.lockin.values[f"/{dev}/demods/{READOUT_DEMOD_IDX}/oscselect"] == MOD_CARRIER_OSC_IDX
    # 射频固定由 MFLI Out1 输出：不再有 DG Y 通道、功放与示波器动作。
    assert rig.clock_calls == ["sync"]
    assert not any(event[0] in {"amp", "scope"} for event in rig.events)
    assert ("xy", 1, "setup_sine") not in rig.events
    assert "Aigtek 1200B" not in DEFINITION.required_devices
    assert "DHO900" not in DEFINITION.required_devices
    # 收尾回读：MFLI 先关 Out1 再清 MF-MOD 与偏置，Z/X 归零，温控与主场恢复。
    sigout_key = preparation.mfli_sigout_safety_key(load_mapping(ROOT))
    mfli_shut = c["shutdown_readbacks"][sigout_key]
    assert mfli_shut["output"] is False and mfli_shut["mod_enable"] == 0
    assert mfli_shut["offset_v"] == 0.0 and mfli_shut["carrier_amplitude_vpk"] == 0.0
    mfli_events = [event for event in rig.events if event[0] == "mfli"]
    on_off = [index for index, event in enumerate(mfli_events)
              if event[1] == f"/{dev}/sigouts/0/on" and event[2] == 0]
    mod_clear = [index for index, event in enumerate(mfli_events)
                 if event[1] == f"/{dev}/mods/0/carrier/amplitude" and event[2] == 0.0]
    offset_zero = [index for index, event in enumerate(mfli_events)
                   if event[1] == f"/{dev}/sigouts/0/offset" and event[2] == 0.0]
    assert on_off and mod_clear and offset_zero and min(on_off) < min(mod_clear) < min(offset_zero)
    x = c["shutdown_readbacks"]["X_magnetic_field"]
    assert x["output"] is False and x["shape"] == "DC" and x["dc_setting_v"] == 0.0
    z = c["shutdown_readbacks"]["Z_magnetic_field"]
    assert z["output"] is False and z["dc_setting_v"] == 0.0 and z["sync"] is False
    assert c["shutdown_readbacks"]["Temp_Switch"]["dc_setting_v"] == 5.0
    assert c["main_field"]["restored_on_shutdown"]
    assert rig.gs["output"] and rig.gs["current"] == -.001
    _assert_parked(rig)
    # B01 对本次扫描给出 (2.2.2) 摘要与两幅图。
    result = analysis.analyze(run_dir)
    assert result["fit_mode"] == "eq222_full_cycle"
    assert math.isfinite(result["k0_v_per_rad"]) and result["phases_deg"] == phases
    assert result["rotation_deg"] == pytest.approx(phase["rotation_deg"])
    assert result["work_point_deg"] == pytest.approx(phase["burst_command_deg"])
    assert result["fit_quality"] in {"good", "poor"}
    for artifact in (run_format.SUMMARY_FILE, run_format.PHASE_RESPONSE_PLOT, run_format.PHASOR_PLOT):
        assert (run_dir / artifact).stat().st_size > 0


def test_z_readback_mismatch_keeps_output_off(rig, monkeypatch):
    monkeypatch.setattr(rig.z, "get_arb_points", lambda **kw: 2, raising=False)
    with pytest.raises(SafetyAbort, match="Z 双脉冲写入回读不符"):
        Workflow.run(rig.p, project_root=ROOT)
    assert ("z", 1, "set_output", True) not in rig.events
    assert not any(event[0] in {"amp", "scope"} for event in rig.events)
    assert not any(event[0] == "daq" for event in rig.events)
    _assert_parked(rig)


def test_cancel_preserves_completed_frames(rig, monkeypatch):
    orig_read = rf_phase.daq.acquire_data

    def read(*args, **kwargs):
        if any(event == ("disk", "coarse_00") for event in rig.events):
            raise WorkflowCancelled("注入取消")
        return orig_read(*args, **kwargs)

    monkeypatch.setattr(rf_phase.daq, "acquire_data", read)
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


def test_stepwise_rerun_keeps_prior_run(rig):
    ctx = _ctx(rig)
    try:
        with pytest.raises(ValueError, match="缺少前置"):
            ctx.execute(["A03"])
        ctx.execute(["A01", "A02", "A03"])
        assert ctx.states["A03"]["status"] == "completed"
        original = ctx.directory.root
        baseline = (original / "experiment_config.yaml").read_bytes()
        # A02 完成的会话里重跑测量：必须新建运行目录，旧目录保持原样。
        ctx.execute(["A03"])
        assert ctx.directory.root != original
        assert ctx.states["A03"]["status"] == "completed"
        assert (original / "experiment_config.yaml").read_bytes() == baseline
    finally:
        ctx.finish()
    _assert_parked(rig)


def test_uses_strict_daq_and_rejects_synthetic_time(rig, monkeypatch):
    original = rf_phase.daq.acquire_data

    def read(*args, **kwargs):
        out = original(*args, **kwargs)
        for frame in out:
            frame.config_snapshot["time_source"] = "synthetic_grid"
        return out

    monkeypatch.setattr(rf_phase.daq, "acquire_data", read)
    with pytest.raises(SafetyAbort, match="真实同步时间戳"):
        Workflow.run(rig.p, project_root=ROOT)
    _assert_parked(rig)
    assert not any(event[0] == "disk" for event in rig.events)


def test_restore_failure_saves_record_then_aborts(rig, monkeypatch):
    original = rf_phase.daq.acquire_data

    def read(*args, **kwargs):
        # 本轮第一点的温控恢复失败：已采集的帧仍要落盘抢救。
        rig.temp.restore_failures = 1
        return original(*args, **kwargs)

    monkeypatch.setattr(rf_phase.daq, "acquire_data", read)
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


def test_stage_list_and_artifacts():
    assert [stage.id for stage in stages.ACQUISITION] == ["A01", "A02", "A03", "A04"]
    run_stage = next(stage for stage in stages.ACQUISITION if stage.id == "A03")
    assert "raw/coarse_*.npz" in run_stage.artifacts
    analysis_stage = stages.ANALYSIS[0]
    assert set(analysis_stage.artifacts) == {run_format.SUMMARY_FILE.removeprefix("results/"),
                                             run_format.PHASE_RESPONSE_PLOT.removeprefix("results/"),
                                             run_format.PHASOR_PLOT.removeprefix("results/")}


# ---------------------------------------------------------------- B01 合成数据

_EQ222_MODEL = dict(y0=0.004, k0=0.10, r=2.2, v0=-0.15, phi_c_deg=70.0)


def _eq222_aligned(phases_deg, model=None):
    model = model or _EQ222_MODEL
    phi_c = math.radians(model["phi_c_deg"])
    delta = math.asin(max(min(-model["v0"] / model["r"], 0.999999), -0.999999))
    amp = model["k0"] / (model["r"] * math.cos(delta))
    phi = np.deg2rad(np.asarray(phases_deg, dtype=float))
    y = signal_fit.eq222_y(phi, model["y0"], amp * model["r"], model["r"] ** 2, phi_c, model["v0"])
    x = 0.002 * np.cos(phi - phi_c)
    return x, np.asarray(y, dtype=float)


def _write_frame(root, label, phase_deg, value, *, rate_sa_s=1000.0, samples=200):
    (Path(root) / "raw").mkdir(parents=True, exist_ok=True)
    time_s = np.arange(samples) / rate_sa_s
    metadata = dict(rate_sa_s=rate_sa_s, time_source="module_timestamp",
                    acquisition_module="daq", unit="V",
                    burst_command_deg=float(phase_deg), carrier_command_deg=float(phase_deg),
                    lockin=dict(phase_deg=0.0, time_constant_s=1e-3, stream_rate_sa_s=rate_sa_s,
                                order=4, oscillator_hz=5000.0),
                    daq=dict(actual_rate=rate_sa_s, time_source="module_timestamp",
                             acquisition_module="daq", unit="V"))
    return run_format.write_frame(root, label, "xy", metadata,
                                  time_s=time_s,
                                  x_v=np.full(samples, value.real),
                                  y_v=np.full(samples, value.imag))


def _write_eq222_run(root, *, rotation_deg=0.0, work_point_deg=70.0):
    params = RfPhaseResponseParams()
    phases = params.phase_angles_deg()
    x_aligned, y_aligned = _eq222_aligned(phases)
    # 原始帧是对齐帧旋转 +rotation 后的测量结果；B01 用 exp(-i*rotation) 还原。
    factor = np.exp(1j * math.radians(rotation_deg))
    data_files = []
    for index, (phase, x_v, y_v) in enumerate(zip(phases, x_aligned, y_aligned)):
        value = (complex(x_v, y_v)) * factor
        data_files.append(_write_frame(root, f"coarse_{index:02d}", phase, value))
    config = run_format.initial_metadata(params) | {
        "schema_version": params.schema_version, "parameters": params.to_external(),
        "completion_status": "completed", "data_files": data_files,
        "phase_response": {"burst_command_deg": work_point_deg, "carrier_command_deg": work_point_deg,
                           "rotation_deg": rotation_deg, "lockin_phase_deg": -rotation_deg,
                           "lockin": dict(phase_deg=-rotation_deg, time_constant_s=1e-3,
                                          stream_rate_sa_s=1000.0, order=4, oscillator_hz=5000.0),
                           "grid_deg": phases, "points": len(phases)},
    }
    (root / "experiment_config.yaml").write_text(yaml.safe_dump(config, allow_unicode=True),
                                                 encoding="utf-8")
    return params, phases


class TestSyntheticEq222Analysis:
    def test_synthetic_eq222_frames_recover_k0_and_work_point(self, tmp_path):
        params, phases = _write_eq222_run(tmp_path, rotation_deg=0.0, work_point_deg=70.0)
        summary = analysis_steps.scan_report(tmp_path, params)
        assert summary["fit_mode"] == "eq222_full_cycle"
        assert summary["k0_v_per_rad"] == pytest.approx(_EQ222_MODEL["k0"], rel=0.02)
        assert summary["k0_defined"] is True and summary["k0_degenerate_reason"] == ""
        assert summary["k0_fit_v_per_rad"] == pytest.approx(summary["k0_v_per_rad"])
        c1, cm1 = summary["components"]["c_plus1"], summary["components"]["c_minus1"]
        expected_response = (abs(complex(c1["re_v"], c1["im_v"]))
                             + abs(complex(cm1["re_v"], cm1["im_v"])))
        assert summary["response_v_per_rad"] == pytest.approx(expected_response, rel=1e-9)
        assert summary["response_v_per_rad"] > 0.0
        assert summary["phi_c_deg"] == pytest.approx(_EQ222_MODEL["phi_c_deg"], abs=0.5)
        delta = math.degrees(math.asin(-_EQ222_MODEL["v0"] / _EQ222_MODEL["r"]))
        assert summary["phi_z1_deg"] == pytest.approx(
            (_EQ222_MODEL["phi_c_deg"] + delta) % 360.0, abs=0.5)
        assert summary["rms_v"] < 1e-6 and summary["fit_quality"] == "good"
        assert summary["work_point_deg"] == pytest.approx(70.0)
        assert summary["rotation_deg"] == 0.0 and summary["rotated"] is False
        assert summary["phases_deg"] == pytest.approx(phases)
        assert len(summary["points"]) == len(phases)
        point = summary["points"][0]
        assert set(point) >= {"phase_deg", "x_mean_v", "y_mean_v", "R_mean_v", "theta_mean_deg"}
        assert summary["locus_degenerate"] is False
        assert (tmp_path / run_format.SUMMARY_FILE).is_file()
        assert (tmp_path / run_format.PHASE_RESPONSE_PLOT).stat().st_size > 0
        assert (tmp_path / run_format.PHASOR_PLOT).stat().st_size > 0

    def test_v3_phase_response_png_uses_a03_shared_phase_plot(self, tmp_path, monkeypatch):
        """v3 的 phase_response.png 由 A03 公共相图绘制，读帧函数与帧异常类型由本实验注入。"""
        params, _ = _write_eq222_run(tmp_path, rotation_deg=40.0, work_point_deg=70.0)
        seen = {}
        real_plot_data = analysis_steps.phase_plot.coarse_plot_data
        real_figure = analysis_steps.phase_plot.coarse_figure

        def spy_plot_data(run_dir, config, *, read_frame, frame_errors=Exception):
            seen.update(read_frame=read_frame, frame_errors=frame_errors,
                        data=real_plot_data(run_dir, config, read_frame=read_frame,
                                            frame_errors=frame_errors))
            return seen["data"]

        def spy_figure(coarse_data):
            figure = real_figure(coarse_data)
            seen["figure"] = figure
            return figure

        monkeypatch.setattr(analysis_steps.phase_plot, "coarse_plot_data", spy_plot_data)
        monkeypatch.setattr(analysis_steps.phase_plot, "coarse_figure", spy_figure)
        summary = analysis_steps.scan_report(tmp_path, params)
        assert seen["read_frame"] is run_format.read_frame
        assert seen["frame_errors"] == analysis_steps.FRAME_ERRORS
        data = seen["data"]
        assert data is not None and data["eq222"] is not None and data["rotated"] is True
        assert data["phi_deg"] == pytest.approx(summary["phases_deg"])
        # A03 两联图：左为 1:1 对齐相图、右为整周 X/Y 波形；摘要字段保持 (2.2.2) 拟合输出。
        titles = [axis.get_title() for axis in seen["figure"].axes]
        assert any(title == "Phase portrait (1:1 aligned)" for title in titles)
        assert any(title == "Full-cycle X & Y waveforms" for title in titles)
        assert summary["fit_mode"] == "eq222_full_cycle"
        assert summary["k0_v_per_rad"] == pytest.approx(_EQ222_MODEL["k0"], rel=0.02)
        assert summary["k0_defined"] is True and summary["response_v_per_rad"] > 0.0
        assert summary["rotation_deg"] == pytest.approx(40.0) and summary["rotated"] is True
        assert (tmp_path / run_format.PHASE_RESPONSE_PLOT).stat().st_size > 0
        assert (tmp_path / run_format.PHASOR_PLOT).stat().st_size > 0

    def test_degenerate_crossing_nulls_k0_and_reports_reason(self, tmp_path, monkeypatch):
        params, _ = _write_eq222_run(tmp_path)
        real_fit = analysis_steps.signal_fit.fit_eq222_full_cycle

        def degenerate_fit(phi_deg, y_v, **kwargs):
            fitted = real_fit(phi_deg, y_v, **kwargs)
            fitted.update(v0=0.70, r=0.05, k0_v_per_rad=5e-5)
            return fitted

        monkeypatch.setattr(analysis_steps.signal_fit, "fit_eq222_full_cycle", degenerate_fit)
        summary = analysis_steps.scan_report(tmp_path, params)
        assert summary["k0_defined"] is False
        assert summary["k0_v_per_rad"] is None
        assert "不定义" in summary["k0_degenerate_reason"]
        assert summary["k0_fit_v_per_rad"] == pytest.approx(5e-5)
        assert summary["response_v_per_rad"] > 0.0

    def test_k0_degeneracy_rule_matches_crossing_condition(self):
        assert analysis_steps._k0_is_undefined(0.69, 0.05)
        assert analysis_steps._k0_is_undefined(0.0, 0.0)
        assert not analysis_steps._k0_is_undefined(-0.048, 0.198)

    def test_rotation_deg_aligns_raw_frames_before_fit(self, tmp_path):
        params, _ = _write_eq222_run(tmp_path, rotation_deg=40.0, work_point_deg=70.0)
        summary = analysis_steps.scan_report(tmp_path, params)
        assert summary["rotation_deg"] == pytest.approx(40.0) and summary["rotated"] is True
        assert summary["k0_v_per_rad"] == pytest.approx(_EQ222_MODEL["k0"], rel=0.02)
        assert summary["phi_c_deg"] == pytest.approx(_EQ222_MODEL["phi_c_deg"], abs=0.5)
        assert summary["rms_v"] < 1e-6 and summary["fit_quality"] == "good"

    def test_staged_analysis_runs_on_synthetic_frames(self, tmp_path):
        _write_eq222_run(tmp_path)
        analysis.AnalysisWorkflow.analyze(tmp_path)
        summary = json.loads((tmp_path / run_format.SUMMARY_FILE).read_text(encoding="utf-8"))
        assert summary["fit_mode"] == "eq222_full_cycle"
        assert (tmp_path / run_format.PHASE_RESPONSE_PLOT).is_file()


def _write_csv(root, rows, *, with_xy):
    path = root / run_format.PHASE_RESPONSE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["phase_deg", "repeat", "x_v", "y_v", "R_v", "theta_deg", "timestamp"] if with_xy else \
        ["phase_deg", "repeat", "R_v", "theta_deg", "timestamp"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


class TestLegacyCsvAnalysis:
    def test_legacy_v2_csv_uses_eq222_without_rotation(self, tmp_path):
        params = RfPhaseResponseParams()
        phases = params.phase_angles_deg()
        x_aligned, y_aligned = _eq222_aligned(phases)
        rows = []
        for phase, x_v, y_v in zip(phases, x_aligned, y_aligned):
            value = complex(x_v, y_v)
            rows.append(dict(phase_deg=f"{phase:g}", repeat=0, x_v=f"{value.real:.12g}",
                             y_v=f"{value.imag:.12g}", R_v=f"{abs(value):.12g}",
                             theta_deg=f"{math.degrees(np.angle(value)):.12g}",
                             timestamp="2026-11-19T00:00:00+08:00"))
        _write_csv(tmp_path, rows, with_xy=True)
        v2_parameters = params.to_external() | {"DEMOD0_IDX": 0, "DEMOD0_PHASE_DEG": 0.0}
        config = run_format.initial_metadata(params) | {
            "schema_version": 2, "raw_format_version": 2, "parameters": v2_parameters,
            "completion_status": "completed",
        }
        (tmp_path / "experiment_config.yaml").write_text(yaml.safe_dump(config, allow_unicode=True),
                                                         encoding="utf-8")
        summary = analysis_steps.scan_report(tmp_path, params)
        assert summary["fit_mode"] == "eq222_full_cycle"
        assert summary["rotation_deg"] == 0.0 and summary["rotated"] is False
        assert summary["work_point_deg"] is None
        assert summary["k0_v_per_rad"] == pytest.approx(_EQ222_MODEL["k0"], rel=0.02)
        assert summary["rms_v"] < 1e-6 and summary["fit_quality"] == "good"
        assert (tmp_path / run_format.PHASE_RESPONSE_PLOT).is_file()

    def test_legacy_v1_csv_stays_three_component(self, tmp_path):
        params = RfPhaseResponseParams()
        phases = params.phase_angles_deg()
        c0, c_plus1, c_minus1 = 0.010 + 0.005j, 0.003 - 0.002j, 0.004 + 0.001j
        rows = []
        for repeat in range(2):
            for phase in phases:
                phi = math.radians(phase)
                value = c0 + c_plus1 * np.exp(1j * phi) + c_minus1 * np.exp(-1j * phi)
                rows.append(dict(phase_deg=f"{phase:g}", repeat=repeat,
                                 R_v=f"{abs(value):.12g}",
                                 theta_deg=f"{math.degrees(np.angle(value)):.12g}",
                                 timestamp="2026-11-19T00:00:00+08:00"))
        _write_csv(tmp_path, rows, with_xy=False)
        v1_parameters = {key: value for key, value in params.to_external().items()
                         if key not in {"Z_PULSE_VPP", "Z_DRIVE_SOURCE_IMPEDANCE_OHM",
                                        "MAIN_FIELD_CURRENT_MA", "RF_DIRECTION"}}
        v1_parameters["DEMOD0_IDX"] = 0
        config = run_format.initial_metadata(params) | {
            "schema_version": 1, "raw_format_version": 1, "parameters": v1_parameters,
            "completion_status": "completed",
        }
        (tmp_path / "experiment_config.yaml").write_text(yaml.safe_dump(config, allow_unicode=True),
                                                         encoding="utf-8")
        summary = analysis_steps.scan_report(tmp_path, params)
        assert summary["fit_mode"] == "three_component"
        assert summary["mode"] == "phase_response"
        assert summary["residual_rms_v"] == pytest.approx(0.0, abs=1e-9)
        assert summary["components"]["c0"]["re_v"] == pytest.approx(c0.real, abs=1e-9)
        assert summary["best_phase_alt_deg"] == pytest.approx(
            (summary["best_phase_deg"] + 180.0) % 360.0)
        assert summary["points"][0]["x_mean_v"] is None
        assert (tmp_path / run_format.PHASE_RESPONSE_PLOT).is_file()
        assert (tmp_path / run_format.PHASOR_PLOT).is_file()

    def test_missing_frames_write_unavailable_summary(self, tmp_path):
        params = RfPhaseResponseParams()
        config = run_format.initial_metadata(params) | {
            "schema_version": params.schema_version, "parameters": params.to_external(),
            "completion_status": "completed",
        }
        (tmp_path / "experiment_config.yaml").write_text(yaml.safe_dump(config, allow_unicode=True),
                                                         encoding="utf-8")
        summary = analysis_steps.scan_report(tmp_path, params)
        assert summary["fit_mode"] == "unavailable"
        assert "缺少" in summary["error"]
        assert (tmp_path / run_format.PHASE_RESPONSE_PLOT).is_file()
        assert (tmp_path / run_format.PHASOR_PLOT).is_file()


# ---------------------------------------------------------------- 主磁场与相位工具

class _FakeGS200:
    def __init__(self):
        self.source_function = "CURR"
        self.current_a = 0.0
        self.output = False
        self.current_range_a = 0.01
        self.current_limit_a = 0.2

    def get_source_function(self):
        return self.source_function

    def get_current(self):
        return self.current_a

    def get_output(self):
        return self.output

    def get_current_range(self):
        return self.current_range_a

    def get_current_limit(self):
        return self.current_limit_a

    def set_output(self, value):
        self.output = bool(value)

    def set_source_function(self, value):
        self.source_function = value

    def set_current_limit(self, value):
        self.current_limit_a = float(value)

    def set_current_range(self, value):
        self.current_range_a = float(value)

    def set_current(self, value):
        self.current_a = float(value)


class TestMainField:
    def _context(self):
        gs = _FakeGS200()
        ctx = SimpleNamespace(root=ROOT, devices={"gs200": gs}, state={}, outputs={},
                              main_field_state=None,
                              mapping={"main_magnetic_field": {"resource": "ASRL1::INSTR",
                                                               "source_function": "CURR"}})
        return ctx, gs

    def test_zero_current_keeps_output_off(self):
        ctx, gs = self._context()
        preparation.configure_main_field(ctx, 0.0)
        assert gs.output is False
        assert ctx.state["main_field"]["output_after"] is False

    def test_nonzero_current_sets_and_reads_back(self):
        ctx, gs = self._context()
        preparation.configure_main_field(ctx, -0.0655)
        assert gs.output is True
        assert gs.current_a == pytest.approx(-6.55e-5, rel=1e-9)
        assert ctx.state["main_field"]["output_after"] is True
        assert ctx.state["main_field"]["current_after_ma"] == pytest.approx(-0.0655)

    def test_current_over_safety_limit_is_rejected(self):
        ctx, gs = self._context()
        with pytest.raises(ValueError, match="安全"):
            preparation.configure_main_field(ctx, 21.0)


def test_phase_drift_is_circular_difference():
    assert preparation.phase_drift_deg(95.0, 90.0) == pytest.approx(5.0)
    assert preparation.phase_drift_deg(355.0, 5.0) == pytest.approx(10.0)
    assert preparation.phase_drift_deg(0.0, 350.0) == pytest.approx(10.0)


def test_models_validate_model_rejects_demod_and_envelope():
    assert RfPhaseResponseParams().validate_model() == []
    assert any("Demod 4" in error for error in
               replace(RfPhaseResponseParams(), demod0_idx=0).validate_model())
    assert any("包络" in error for error in
               replace(RfPhaseResponseParams(), y_bias_v=9.99).validate_model())
    assert any("高阻档上限" in error for error in
               replace(RfPhaseResponseParams(), z_pulse_vpp=21.0).validate_model())
