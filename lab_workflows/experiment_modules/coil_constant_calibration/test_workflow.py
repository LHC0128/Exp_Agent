"""真实编排、真实文件与假仪器：一阶线圈链 + 假 Mx 共振，不连接硬件。"""
from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import yaml

from oscilloscope import AcquisitionResult
from lockin_amplifier import HF2Instrument

from ...common import WorkflowCancelled, find_project_root, load_mapping
from ...experiment_params import ExperimentParams
from ...steps import routed_signal_generator
from ..mx_y_rf_sensitivity import acquisition as r_acquisition
from . import acquisition, analysis, preparation, stages, workflow
from .models import CoilConstantCalibrationParams
from .preparation import SafetyAbort

ACQUISITION_CASES = {
    "coil-constant-calibration": {"mode": "single", "cases": {
        "order": ["test_full_run_then_analysis_recovers_constant"],
        "cancel": ["test_cancel_mid_scan_shuts_down_and_keeps_points"],
        "acquire_failure": ["test_gain_mismatch_is_recorded_without_abort"],
        "partial_save": ["test_cancel_mid_scan_shuts_down_and_keeps_points"],
        "entrypoints": ["test_stepwise_rerun_inherits_dc_check"],
    }, "sources": [
        "lab_workflows/experiment_modules/coil_constant_calibration/preparation.py",
        "lab_workflows/experiment_modules/coil_constant_calibration/acquisition.py",
        "lab_workflows/experiment_modules/coil_constant_calibration/workflow.py",
        "lab_workflows/experiment_modules/coil_constant_calibration/session.py",
    ]},
}
REGRESSION_CASES = {
    "coil-constant-calibration": {"parameters": {
        "sources": ["lab_workflows/experiment_modules/coil_constant_calibration/models.py",
                    "params/experiments/coil-constant-calibration.yaml"],
        "tests": ["test_default_yaml_round_trip", "test_every_visible_parameter_has_an_edit_entry"],
    }},
}
ROOT = Path(__file__).resolve().parents[3]
ZERO_FIELD_HZ = 30000.0
TRUE_K_HZ_PER_MA = 600.0
GAMMA_HZ = 800.0


def copy_config(root):
    (root / "params").mkdir(parents=True, exist_ok=True)
    for name in ("devices.yaml", "mapping.yaml", "safety_limits.yaml", "clock_sources.yaml"):
        source = ROOT / "params" / name
        if source.exists():
            shutil.copyfile(source, root / "params" / name)
    path = root / "params/mapping.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    for key in ("lockin_r", "lockin_xy"):
        config["mapping"][key]["device_id"] = "hf2_dev18246"
    path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")


def small_params(**changes):
    base = replace(
        CoilConstantCalibrationParams(),
        main_field_enabled=True, main_field_ma=15.555, main_field_center_hz=ZERO_FIELD_HZ,
        initial_hz_per_ma=TRUE_K_HZ_PER_MA, prediction_polarity="positive_increases_frequency",
        current_start_ma=-100.0, current_stop_ma=100.0, current_step_ma=25.0,
        coarse_half_width_hz=3000.0, coarse_step_hz=500.0,
        frequency_half_width_hz=1200.0, frequency_step_hz=100.0,
        near_zero_scan_floor_hz=4000.0, near_zero_fit_floor_hz=2000.0,
        frequency_duration_s=0.005, system_settle_s=0.001, temp_switch_on_lag_s=0.0,
        zero_frames=2, settle_s=0.0, dc_check_currents_ma=[-20.0, 0.0, 20.0],
        expected_k_nt_per_ma=85.7)
    return replace(base, **changes)


class FakeSignal:
    def __init__(self, resource, rig):
        self.resource, self.rig = resource, rig
        self.output, self.amplitude, self.phase, self.frequency, self.dc = {}, {}, {}, {}, {}
        self.clock = "INT"

    def connect(self):
        self.rig.events.append("connect")

    def disconnect(self):
        self.rig.events.append("disconnect")

    def set_ref_clock_source(self, value):
        self.clock = value

    def get_ref_clock_source(self):
        return self.clock

    def __getattr__(self, name):
        if name.startswith("iter_"):
            raise AttributeError(name)

        def command(*args, channel=1, **kwargs):
            value = args[0] if args else None
            if name == "get_output":
                return self.output.get(channel, False)
            if name == "get_shape":
                return "DC" if channel in self.dc else "SIN"
            if name in ("set_output", "set_amplitude", "set_phase_adjust", "set_frequency"):
                attribute = {"set_output": "output", "set_amplitude": "amplitude",
                             "set_phase_adjust": "phase", "set_frequency": "frequency"}[name]
                getattr(self, attribute)[channel] = value
                temp = self.rig.mapping["Temp_Switch"]
                if self.resource != temp["resource"] or channel != temp["channel"]:
                    self.rig.events.append("configure")
                if name == "set_frequency" and channel == self.rig.rf_channel:
                    self.rig.last_rf_hz = float(value)
                if name == "set_output" and self.resource == self.rig.z_resource and channel == self.rig.z_channel:
                    self.rig.events.append(("sg_output", bool(value)))
            if name == "setup_dc":
                self.dc[channel] = float(value)
                temp = self.rig.mapping["Temp_Switch"]
                if self.resource == temp["resource"] and channel == temp["channel"]:
                    self.rig.temp_events.append("on" if float(value) else "off")
                if self.resource == self.rig.z_resource and channel == self.rig.z_channel:
                    self.rig.events.append(("sg_dc", float(value)))
            if name == "setup_sine":
                self.frequency[channel], self.amplitude[channel] = float(args[0]), float(args[1])
                self.rig.events.append("prepare_sine")
            if name == "set_output_load":
                self.rig.events.append(("set_output_load", args[0] if args else None))
        return command


class FakeAmp:
    def __init__(self, resource, *, serial_number=None, safety_check=None):
        self.resource, self.check = resource, safety_check

    def connect(self):
        pass

    def disconnect(self):
        pass

    def set_output(self, enabled):
        if enabled:
            self.check("output", True)
        self.rig.amp_on = enabled
        self.rig.amp_events.append(("output", enabled))
        self.rig.events.append(("amp_output", bool(enabled)))

    def set_gain_db(self, value):
        self.check("gain_db", value)

    def set_input_impedance(self, ohms):
        self.check("input_impedance_ohms", ohms)

    def set_output_impedance(self, ohms):
        self.check("output_impedance_ohms", ohms)


class FakeScope:
    def connect(self):
        pass

    def disconnect(self):
        pass

    def trigger_stop(self):
        pass


class FakeAcquirer:
    def __init__(self, rig):
        self.rig = rig
        self.depth_cap = None

    def use_auto_depth(self, max_points):
        self.depth_cap = max_points

    def acquire_all(self, config):
        self.rig.reads += 1
        if self.rig.cancel_at is not None and self.rig.reads >= self.rig.cancel_at:
            raise WorkflowCancelled("模拟取消")
        assert self.depth_cap == 1_000_000
        n = 5000
        rate = n / config.sampling_time
        time = (np.arange(n) - n / 2) / rate
        voltage = self.rig.current_a() * self.rig.sense_resistor_ohm + self.rig.rng.normal(0, 1e-4, n)
        return [AcquisitionResult(2, np.array([], np.int16), voltage, time, {},
                                  {"requested": config.to_dict()})]


class FakeGS200:
    def __init__(self):
        self.source, self.current, self.output, self.range, self.limit = "CURRent", 0.009333, True, 0.01, 0.01
        self.calls = []

    def connect(self):
        pass

    def disconnect(self):
        pass

    def get_source_function(self):
        return self.source

    def get_current(self):
        return self.current

    def get_output(self):
        return self.output

    def get_current_range(self):
        return self.range

    def get_current_limit(self):
        return self.limit

    def set_source_function(self, value):
        self.source = value
        self.calls.append(("source", value))

    def set_current(self, value):
        self.current = value
        self.calls.append(("current", value))

    def set_output(self, value):
        self.output = value
        self.calls.append(("output", value))

    def set_current_range(self, value):
        self.range = value

    def set_current_limit(self, value):
        self.limit = value
        self.calls.append(("limit", value))


class FakeTEC:
    def __init__(self):
        self.targets = []

    def connect(self):
        pass

    def disconnect(self):
        pass

    def set_target_temperature(self, value, **kwargs):
        self.targets.append(value)


class FakeHF2(HF2Instrument):
    """只需模拟锁相配置与 R 采集所需的节点读写。"""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.nodes = {}

    def connect(self):
        pass

    def disconnect(self):
        pass

    def sync(self):
        pass

    def set_int(self, path, value):
        self.nodes[path] = value

    def set_double(self, path, value):
        self.nodes[path] = value

    def get_int(self, path):
        return 210000000 if path.endswith("/clockbase") else int(self.nodes.get(path, 0))

    def get_double(self, path):
        return float(self.nodes.get(path, 0.0))

    def get_ref_clock_source(self):
        return "INT"

    def set_ref_clock_source(self, value):
        pass


class Rig:
    def __init__(self, *, true_k_a_per_v=0.04518, offset_a=0.0, r_std=1e-4, polarity=1.0):
        self.events, self.amp_events, self.temp_events = [], [], []
        self.true_k_a_per_v, self.offset_a, self.r_std, self.polarity = true_k_a_per_v, offset_a, r_std, polarity
        self.amp_on, self.reads, self.cancel_at, self.last_rf_hz = False, 0, None, ZERO_FIELD_HZ
        self.rng = np.random.default_rng(20261116)
        self.generators, self.gs200, self.tec, self.hf2 = {}, FakeGS200(), FakeTEC(), FakeHF2(device_id="dev18246")
        self.mapping = load_mapping(ROOT)
        self.sense_resistor_ohm = 10.0
        self.z_resource = str(self.mapping["Z_magnetic_field"]["resource"])
        self.z_channel = int(self.mapping["Z_magnetic_field"]["channel"])
        self.rf_channel = int(self.mapping["rf_coil"]["channel"])
        for config in self.mapping.values():
            if config.get("instrument") == "signal_generator":
                self.generators.setdefault(str(config["resource"]), FakeSignal(str(config["resource"]), self))

    def current_a(self):
        if not self.amp_on:
            return 0.0
        z = self.generators[self.z_resource]
        return self.true_k_a_per_v * float(z.dc.get(self.z_channel, 0.0)) + self.offset_a

    def center_hz(self):
        current_ma = self.current_a() * 1e3
        return abs(ZERO_FIELD_HZ + self.polarity * TRUE_K_HZ_PER_MA * current_ma)


def install(monkeypatch, rig):
    monkeypatch.setattr(routed_signal_generator, "create_signal_generator",
                        lambda config: rig.generators[str(config["resource"])])
    monkeypatch.setattr(preparation, "create_signal_generator",
                        lambda config: rig.generators[str(config["resource"])])
    monkeypatch.setattr(preparation, "GS200Instrument", lambda resource: rig.gs200)
    monkeypatch.setattr(preparation, "TECInstrument", lambda **kwargs: rig.tec)
    monkeypatch.setattr(preparation, "create_lockin", lambda config: rig.hf2)
    monkeypatch.setattr(preparation, "Aigtek1200BInstrument", lambda resource, **kwargs: _amp(rig, resource, **kwargs))
    monkeypatch.setattr(preparation, "create_oscilloscope", lambda config: FakeScope())
    monkeypatch.setattr(preparation, "create_scope_acquisition", lambda scope, **kwargs: FakeAcquirer(rig))
    monkeypatch.setattr(preparation, "sleep", lambda *args, **kwargs: None)
    monkeypatch.setattr(acquisition, "sleep", lambda *args, **kwargs: None)
    monkeypatch.setattr(acquisition, "acquire_r", lambda hf2, **kwargs: _fake_r(rig, **kwargs))

    def check_cancelled():
        if rig.cancel_at is not None and rig.reads >= rig.cancel_at:
            raise WorkflowCancelled("模拟取消")

    monkeypatch.setattr(acquisition, "check_cancelled", check_cancelled)


def _amp(rig, resource, **kwargs):
    amp = FakeAmp(resource, **kwargs)
    amp.rig = rig
    return amp


def _fake_r(rig, *, device_id, demod_idx, actual_rate_sa_s, duration_s, requested_rate_sa_s=None):
    rig.reads += 1
    if rig.cancel_at is not None and rig.reads >= rig.cancel_at:
        raise WorkflowCancelled("模拟取消")
    count = max(8, int(round(actual_rate_sa_s * duration_s)))
    frequency = rig.last_rf_hz
    center = rig.center_hz()
    noise = rig.rng.normal(0, rig.r_std, count)
    response = 1.0 + 2.0 * GAMMA_HZ / np.hypot(frequency - center, GAMMA_HZ) + noise
    return {"time_s": np.arange(count) / actual_rate_sa_s, "r": response,
            "actual_rate_Sa_s": float(actual_rate_sa_s)}


@pytest.fixture
def rig(tmp_path, monkeypatch):
    copy_config(tmp_path)
    value = Rig()
    install(monkeypatch, value)
    return value


def read_config(directory):
    return yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf-8"))


def assert_shutdown_order(events):
    amp_off = max(index for index, event in enumerate(events) if event == ("amp_output", False))
    assert ("sg_dc", 0.0) in events[amp_off:]
    assert ("sg_output", False) in events[amp_off:]


def run(tmp_path, params):
    return workflow.run(params, project_root=tmp_path)


def test_full_run_then_analysis_recovers_constant(tmp_path, rig):
    directory = run(tmp_path, small_params())
    config = read_config(directory)
    assert config["completion_status"] == "completed" and config["safety_shutdown"]["completed"]
    assert config["main_field"] == {"enabled": True, "current_ma": 15.555, "center_hz": ZERO_FIELD_HZ}
    assert config["dc_check"]["ratio"] == pytest.approx(1.0, rel=0.02)
    assert config["current_scan"]["anchor_center_hz"] == pytest.approx(ZERO_FIELD_HZ)
    assert config["current_scan"]["scan_failed"] == 0
    assert config["current_scan"]["near_zero_skipped"] >= 1
    assert (directory / "raw" / "current_scan_index.npz").is_file()
    assert any((directory / "raw" / "current_000").glob("coarse_*_attempt_00.npz"))
    assert any((directory / "raw" / "current_000").glob("frequency_*_attempt_00.npz"))

    summary = analysis.analyze(directory)
    assert summary["status"] == "completed"
    payload = json.loads((directory / "results" / "calibration_evaluation.json").read_text(encoding="utf-8"))
    assert payload["success"], payload["rejection_reasons"]
    assert payload["K_Hz_per_mA"] == pytest.approx(TRUE_K_HZ_PER_MA, rel=0.02)
    assert payload["K_B_nT_per_mA"] == pytest.approx(TRUE_K_HZ_PER_MA / 7.0, rel=0.02)
    assert payload["K_signed_Hz_per_mA"] == pytest.approx(TRUE_K_HZ_PER_MA, rel=0.02)
    assert payload["I0_ma"] == pytest.approx(-ZERO_FIELD_HZ / TRUE_K_HZ_PER_MA, abs=2.0)
    assert payload["anchor_center_hz"] == pytest.approx(ZERO_FIELD_HZ)
    assert payload["geometry"]["crossing_inside"] is True
    assert (directory / "results" / "coil_constant_calibration.png").is_file()
    assert (directory / "results" / "frequency_response_fits.png").is_file()
    assert_shutdown_order(rig.events)


def test_main_field_disabled_only_turns_output_off(tmp_path, rig):
    directory = run(tmp_path, small_params(main_field_enabled=False))
    assert not [value for name, value in rig.gs200.calls if name == "current"]
    assert ("output", False) in rig.gs200.calls
    assert read_config(directory)["main_field"]["enabled"] is False


def test_main_field_enabled_sets_current(tmp_path, rig):
    directory = run(tmp_path, small_params())
    currents = [value for name, value in rig.gs200.calls if name == "current"]
    assert currents and currents[-1] == pytest.approx(0.015555)
    assert ("output", True) in rig.gs200.calls
    assert read_config(directory)["main_field"] == {
        "enabled": True, "current_ma": 15.555, "center_hz": ZERO_FIELD_HZ}


def test_gain_mismatch_is_recorded_without_abort(tmp_path, monkeypatch):
    copy_config(tmp_path)
    value = Rig(true_k_a_per_v=0.014)  # 实测 I/V 只有预测的 31%
    install(monkeypatch, value)
    directory = run(tmp_path, small_params())
    config = read_config(directory)
    assert config["completion_status"] == "completed"
    assert config["dc_check"]["ratio"] == pytest.approx(0.014 / 0.04518, rel=0.02)
    assert config["dc_check"]["r_squared"] > 0.999
    assert (directory / "raw" / "dc_check_00.npz").is_file()
    assert config["current_scan"]["scan_failed"] == 0


def test_measured_current_over_limit_is_recorded_without_abort(tmp_path, monkeypatch):
    copy_config(tmp_path)
    value = Rig(offset_a=0.35)  # 功放零偏电流 350 mA，超过 z_pulse_coil_current 上限
    install(monkeypatch, value)
    directory = run(tmp_path, small_params())
    config = read_config(directory)
    assert config["completion_status"] == "completed"
    assert config["dc_check"]["offset_a"] == pytest.approx(0.35, rel=0.05)


def test_wrong_polarity_is_self_healed_by_symmetric_coarse(tmp_path, monkeypatch):
    copy_config(tmp_path)
    value = Rig(polarity=-1.0)  # 实际正电流降频，但参数仍是 positive_increases
    install(monkeypatch, value)
    directory = run(tmp_path, small_params())
    config = read_config(directory)
    assert config["completion_status"] == "completed"
    assert config["current_scan"]["scan_failed"] <= 2  # 近过零两点预测偏差，其余靠对称粗扫找回
    summary = analysis.analyze(directory)
    assert summary["status"] == "completed"
    payload = json.loads((directory / "results" / "calibration_evaluation.json").read_text(encoding="utf-8"))
    assert payload["K_Hz_per_mA"] == pytest.approx(TRUE_K_HZ_PER_MA, rel=0.05)


def test_cancel_mid_scan_shuts_down_and_keeps_points(tmp_path, rig):
    rig.cancel_at = 20  # 第一个主扫描点的粗扫内取消
    ctx = workflow.Workflow(small_params(), project_root=tmp_path)
    with pytest.raises(WorkflowCancelled):
        ctx.execute([stage.id for stage in stages.ACQUISITION if stage.id != stages.FINISH_ID])
    ctx.finish(WorkflowCancelled("模拟取消"))
    assert ctx.states[stages.RUN_ID]["status"] == "cancelled"
    assert read_config(ctx.directory.root)["completion_status"] == "cancelled"
    assert list((ctx.directory.root / "raw" / "current_000").glob("*.npz"))
    assert_shutdown_order(rig.events)


def test_stepwise_rerun_inherits_dc_check(tmp_path, rig):
    ctx = workflow.Workflow(small_params(), project_root=tmp_path)
    ctx.execute(["A01", "A02", "A03", "A04", "A05", "A06", "A07"])
    first = ctx.directory.root
    assert ctx.states["A04"]["status"] == "completed"
    ctx.update_parameters({"CURRENT_STEP_MA": 50.0})
    assert ctx.states["A08"]["status"] in ("pending", "stale") and ctx.states["A07"]["status"] == "completed"
    ctx.execute(["A08"])
    second = ctx.directory.root
    assert second != first
    config = read_config(second)
    assert config["inherited_from"] == first.name
    assert config["dc_check"]["k_a_per_v"] == pytest.approx(0.04518, rel=0.02)
    assert (second / "raw" / "dc_check_00.npz").is_file()
    assert (second / "raw" / "current_scan_index.npz").is_file()
    ctx.finish()


@pytest.mark.parametrize("changes, message", [
    ({"current_start_ma": 200.0, "current_stop_ma": 100.0}, "必须小于"),
    ({"current_step_ma": 30.0}, "整数个步进"),
    ({"frequency_step_hz": 70.0, "frequency_half_width_hz": 2000.0}, "整数个 FREQUENCY_STEP_HZ"),
    ({"current_start_ma": -400.0, "current_stop_ma": 400.0}, "z_pulse_coil_current"),
    ({"near_zero_scan_floor_hz": 1000.0, "frequency_half_width_hz": 2000.0}, "NEAR_ZERO_SCAN_FLOOR_HZ"),
    ({"main_field_center_hz": 1000.0}, "MAIN_FIELD_CENTER_HZ"),
    ({"pulse_width_s": 1e-3, "pulse_repetition_hz": 20000.0}, "占空比"),
])
def test_preflight_rejects_inconsistent_plans(tmp_path, changes, message):
    copy_config(tmp_path)
    with pytest.raises(ValueError, match=message):
        preparation.preflight(small_params(**changes), tmp_path)


def test_every_visible_parameter_has_an_edit_entry():
    visible = set(CoilConstantCalibrationParams().to_external())
    flow = stages.definition()
    shown = {key for stage in flow["acquisition"] + flow["analysis"] for key in stage["display_parameters"]}
    assert visible == shown
    assert flow["parameter_impacts"]["acquisition"]["AMPLIFIER_GAIN_DB"][0] == "A02"
    assert flow["parameter_impacts"]["acquisition"]["CURRENT_STEP_MA"] == ["A08", "A09"]
    assert flow["parameter_impacts"]["analysis"]["LINEAR_R_SQUARED_MIN"] == ["B03", "B04"]


def test_default_yaml_round_trip():
    path = ROOT / "params" / "experiments" / "coil-constant-calibration.yaml"
    params = CoilConstantCalibrationParams.from_yaml(path)
    assert isinstance(params, ExperimentParams)
    assert params.to_external()["PREDICTION_POLARITY"] in {
        "positive_increases_frequency", "positive_decreases_frequency"}
    assert CoilConstantCalibrationParams.from_external(params.to_external()) == params


def test_fingerprint_covers_nested_raw(tmp_path, rig):
    directory = run(tmp_path, small_params())
    fingerprint = analysis.steps.fingerprint(directory)
    assert any(name.startswith("raw/current_000/") for name in fingerprint)


def fake_main():
    """GUI 后端测试的子进程入口：真实会话协议 + 假仪器。"""
    import os
    import sys

    root = Path(os.environ["COIL_CONSTANT_TEST_ROOT"])
    payload = json.loads(os.environ["LAB_TYPED_PARAMETERS"])
    session_id = payload.pop("session_id", None)
    params = CoilConstantCalibrationParams.from_external(payload)
    with pytest.MonkeyPatch.context() as mp:
        copy_config(root)
        rig = Rig()
        install(mp, rig)
        if os.environ.get("COIL_CONSTANT_TEST_FULL") == "1":
            workflow.run(params, project_root=root)
        else:
            from lab_workflows.staged_workflow import serve_workflow
            serve_workflow(workflow.Workflow(params, project_root=root, session_id=session_id), sys.stdin)


if __name__ == "__main__":
    fake_main()
