"""本ID真实仪器准备、R采集、温控恢复与writer的假仪器回归。"""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import json
import os
import time
import numpy as np
import pytest
import yaml
from lockin_amplifier import HF2Instrument, DAQResult
from ...common import find_project_root, load_mapping, WorkflowCancelled
from ...steps import routed_signal_generator
from ..mx_z_field_calibration import preparation as connections
from ..mx_y_rf_sensitivity import workflow as point_workflow, acquisition as r_acquisition, acquisition_steps as point_steps
from . import workflow, preparation, acquisition, models
from .models import MxYOptimalControlRFFrequencyResponseParams
from .stages import ACQUISITION, FINISH_ID, definition

ACQUISITION_CASES = {
    "mx-y-optimal-control-rf-frequency-response": {"mode": "single", "cases": {
        "order": ["test_full_slow_disk_order_and_real_scientific_axes"],
        "slow_disk": ["test_full_slow_disk_order_and_real_scientific_axes"],
        "cancel": ["test_failure_partial_data_and_original_error"],
        "acquire_failure": ["test_failure_partial_data_and_original_error"],
        "restore_failure": ["test_restore_failure_salvages_payload"],
        "save_failure": ["test_save_failure_after_temperature_restore"],
        "partial_save": ["test_failure_partial_data_and_original_error", "test_restore_failure_salvages_payload", "test_original_error_survives_restore_and_config_failure"],
        "entrypoints": ["test_full_stepwise_same_values_rerun_and_draft_shutdown"],
    }, "sources": ["lab_workflows/experiment_modules/mx_y_optimal_control_rf_frequency_response/workflow.py",
        "lab_workflows/experiment_modules/mx_y_optimal_control_rf_frequency_response/acquisition.py",
        "lab_workflows/experiment_modules/mx_y_optimal_control_rf_frequency_response/preparation.py"]},
}


def copy_config(root):
    source = find_project_root()
    (root / "params").mkdir(exist_ok=True)
    for name in ("mapping.yaml", "devices.yaml", "safety_limits.yaml"):
        (root / "params" / name).write_bytes((source / "params" / name).read_bytes())
    path = root / "params/mapping.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    for key in ("lockin_r", "lockin_xy"):
        config["mapping"][key]["device_id"] = "hf2_dev18246"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    frozen = root / "data/Z_AW_Closed_Loop_Waveform_Correction/frozen/results/corrected_control_waveform.npz"
    frozen.parent.mkdir(parents=True, exist_ok=True)
    normalized = .5 * np.sin(np.arange(16) * 2 * np.pi / 16)
    np.savez(frozen, format_version=2, time_s=np.arange(16) / 1600., omega_ctrl_hz=normalized,
        target_current_a=normalized * .01, normalized=normalized, voltage_v=normalized * .1,
        repeat_frequency_hz=100., amplitude_vpp=.2, offset_v=0., coupling_calibration_run="coupling", coupling_calibration_sha256="f" * 64)
    calibration = root / "data/Mx_Z_Current_Coupling_Calibration/coupling/results/analysis.yaml"
    calibration.parent.mkdir(parents=True, exist_ok=True)
    calibration.write_text(yaml.safe_dump(dict(experiment_id="mx-z-current-coupling-calibration", success=True,
        K_Z_Hz_per_A=100000., K_Z_Hz_per_Vsense=10000., f_0_at_zero_current_hz=0., sense_resistor_ohm=10.,
        linearity={"r_squared": 1.}, curves=[dict(z_bias_v=x, current_a=x * .01, fit={"success": True}) for x in (0., .5, 1.)])), encoding="utf-8")


class FakeSignal:
    def __init__(self, resource, rig):
        self.resource, self.rig = resource, rig
        self.output, self.amplitude, self.phase, self.frequency, self.dc, self.shape = {}, {}, {}, {}, {}, {}
        self.clock = "INT"

    def connect(self):
        self.rig.events.append("connect")

    def disconnect(self):
        self.rig.events.append("disconnect")

    def set_ref_clock_source(self, value):
        self.clock = value

    def get_ref_clock_source(self):
        if self.rig.clock_error:
            raise OSError("时钟回读失败")
        return self.clock

    def __getattr__(self, name):
        if name.startswith("iter_"):
            raise AttributeError(name)
        def command(*args, channel=1, **kwargs):
            value = args[0] if args else None
            temp = self.rig.mapping["Temp_Switch"]
            is_temp = self.resource == temp["resource"] and channel == temp["channel"]
            if name == "get_shape":
                return self.shape.get(channel, "SIN")
            if name == "get_output":
                return self.output.get(channel, False)
            if name in ("set_output", "set_amplitude", "set_burst_phase", "set_frequency"):
                attribute = {"set_output": "output", "set_amplitude": "amplitude", "set_burst_phase": "phase", "set_frequency": "frequency"}[name]
                getattr(self, attribute)[channel] = value
                if not is_temp:
                    self.rig.events.append("configure")
            if name == "setup_dc":
                self.shape[channel], self.dc[channel] = "DC", float(value)
                if is_temp:
                    self.rig.events.append("on" if value else "off")
                    if value and self.rig.restore_error and len(self.rig.captures) >= getattr(self.rig, "restore_after", 1):
                        raise OSError("温控恢复失败")
                    self.rig.enabled = bool(value)
            if name == "setup_sine":
                assert self.rig.enabled
                self.shape[channel] = "SIN"
                self.frequency[channel], self.amplitude[channel] = float(args[0]), float(args[1])
            if name == "setup_arbitrary":
                assert self.rig.enabled
                self.shape[channel] = "USER"
                self.frequency[channel], self.amplitude[channel] = kwargs["freq"], kwargs["amplitude"]
                self.rig.aw_settings.append((kwargs["amplitude"], kwargs["offset"]))
        return command


class FakeHF2(HF2Instrument):
    def __init__(self, rig, **kwargs):
        super().__init__(**kwargs)
        self.rig, self.nodes = rig, {}
    def connect(self):
        self.rig.events.append("connect")
    def disconnect(self):
        self.rig.events.append("disconnect")
    def sync(self):
        pass
    def set_int(self, path, value):
        self.nodes[path] = value
    def set_double(self, path, value):
        self.nodes[path] = value
    def get_int(self, path):
        return 210000000 if path.endswith("/clockbase") else int(self.nodes.get(path, 0))
    def get_double(self, path):
        return float(self.nodes.get(path, 0))


def install_rig(root, monkeypatch, params=None):
    rig = SimpleNamespace(root=root, mapping=load_mapping(root), generators={}, events=[], captures=[], aw_settings=[], enabled=True,
        fault_index=None, acquire_error=None, restore_error=False, clock_error=False, quality_bad=False)
    rig.params = params or replace(MxYOptimalControlRFFrequencyResponseParams(), corrected_control_source_run="frozen",
        frequency_points=3, frequency_start_hz=100., frequency_stop_hz=300., phase_scan_stop_deg=180., phase_scan_step_deg=90.,
        frequency_duration_s=.016, system_settle_s=.001, temp_switch_on_lag_s=0, comparison_enabled=True,
        constant_control_calibration_source_run="coupling", constant_control_larmor_frequency_hz=1000.)
    for config in rig.mapping.values():
        if config["instrument"] == "signal_generator":
            rig.generators.setdefault(config["resource"], FakeSignal(config["resource"], rig))
    monkeypatch.setattr(routed_signal_generator, "create_signal_generator", lambda config: rig.generators[config["resource"]])
    monkeypatch.setattr(connections, "create_signal_generator", lambda config: rig.generators[config["resource"]])
    monkeypatch.setattr(connections, "GS200Instrument", lambda resource: Mock())
    monkeypatch.setattr(connections, "TECInstrument", lambda **kwargs: Mock())
    monkeypatch.setattr(connections, "create_lockin", lambda config: FakeHF2(rig, device_id=config["device_id"]))
    monkeypatch.setattr(workflow, "find_project_root", lambda: root)
    monkeypatch.setattr(models, "find_project_root", lambda: root)
    from ... import experiment_params
    monkeypatch.setattr(experiment_params, "find_project_root", lambda: root)
    monkeypatch.setattr(point_workflow, "_sleep", lambda seconds: rig.events.append("settle"))
    monkeypatch.setattr(point_steps, "_uncancellable_sleep", lambda seconds: None)
    monkeypatch.setattr(connections, "_sleep", lambda seconds: None)
    def acquire(hf2, config, **kwargs):
        assert not rig.enabled
        assert config.signal_paths == ["sample.r"]
        rig.events.append("acquire")
        index = len(rig.captures)
        rig.captures.append(index)
        if index == rig.fault_index:
            raise rig.acquire_error or WorkflowCancelled("模拟取消")
        kwargs["cancellation_check"]()
        source = rig.mapping["rf_coil"]
        rf = rig.generators[source["resource"]]
        channel = source["channel"]
        mean = rf.frequency[channel] / 1000. + float(rf.phase.get(channel, 0)) / 360.
        values = np.full(max(8, config.grid_cols), mean)
        if rig.quality_bad:
            values += np.linspace(-1, 1, values.size)
        return [DAQResult(signal_name="sample.r", demod_index=kwargs["demod_idx"], values=values,
            time=np.arange(values.size) / kwargs["actual_rate"])]
    monkeypatch.setattr(r_acquisition.daq, "acquire_data", acquire)
    return rig


@pytest.fixture
def rig(tmp_path, monkeypatch):
    copy_config(tmp_path)
    return install_rig(tmp_path, monkeypatch)


def through(last):
    ids = [stage.id for stage in ACQUISITION if stage.id != FINISH_ID]
    return ids[:ids.index(last) + 1]


def assert_safe(rig):
    assert rig.enabled
    for key in ("rf_coil", "X_magnetic_field"):
        source = rig.mapping[key]
        assert rig.generators[source["resource"]].output[source["channel"]] is False
    z = rig.mapping["Z_magnetic_field"]
    assert rig.generators[z["resource"]].shape[z["channel"]] == "USER"
    assert rig.generators[z["resource"]].output[z["channel"]] is True
    trigger = rig.mapping["Time_sequence_2"]
    assert rig.generators[trigger["resource"]].output[trigger["channel"]] is True


def test_full_slow_disk_order_and_real_scientific_axes(rig, monkeypatch):
    save = np.savez
    def slow(path, **data):
        assert rig.enabled
        if "attempt" in str(path):
            rig.events.append("save")
            time.sleep(.001)
        save(path, **data)
    monkeypatch.setattr(np, "savez", slow)
    directory = workflow.run(rig.params)
    assert_safe(rig)
    for index, event in enumerate(rig.events):
        if event == "acquire":
            assert rig.events[index - 2:index] == ["off", "settle"]
            assert rig.events[index + 1:index + 3] == ["on", "save"]
    assert len(rig.captures) == 12
    with np.load(directory / workflow.OPTIMAL_CONTROL_DATA_FILE, allow_pickle=True) as data:
        np.testing.assert_allclose(data["r_mean_v"], np.array([.1, .2, .3])[:, None] + np.array([0, .25, .5]))
    with np.load(directory / workflow.CONSTANT_CONTROL_DATA_FILE, allow_pickle=True) as data:
        np.testing.assert_allclose(data["r_mean_v"], [.1, .2, .3])
        assert list(data["phase_deg"]) == [0, 0, 0]
    config = yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf-8"))
    assert config["raw_format_version"] == 1 and config["completion_status"] == "completed"
    assert config["temperature_control"]["actual_temperature_c"] is None
    assert rig.aw_settings[-1] == (.2, 0.)


@pytest.mark.parametrize("failure", [WorkflowCancelled("模拟取消"), OSError("模拟采集失败")])
def test_failure_partial_data_and_original_error(rig, failure):
    rig.fault_index, rig.acquire_error = 1, failure
    with pytest.raises(type(failure)) as caught:
        workflow.run(rig.params)
    assert caught.value is failure
    assert_safe(rig)
    directory = next((rig.root / "data" / workflow.DATA_TYPE).iterdir())
    assert (directory / "raw/optimal_frequency_0000_phase_000_attempt_00.npz").is_file()
    assert not (directory / workflow.CONSTANT_CONTROL_DATA_FILE).exists()


def test_restore_failure_salvages_payload(rig):
    rig.restore_error = True
    with pytest.raises(OSError, match="温控恢复失败"):
        workflow.run(rig.params)
    directory = next((rig.root / "data" / workflow.DATA_TYPE).iterdir())
    with np.load(directory / "raw/optimal_frequency_0000_phase_000_attempt_00.npz") as data:
        assert data["r_v"].size >= 8
    assert rig.events.index("on", rig.events.index("acquire")) > rig.events.index("acquire")


def test_save_failure_after_temperature_restore(rig, monkeypatch):
    original = np.savez
    def save(path, **data):
        assert rig.enabled
        if "attempt" in str(path):
            raise OSError("磁盘故障")
        original(path, **data)
    monkeypatch.setattr(np, "savez", save)
    with pytest.raises(OSError, match="磁盘故障"):
        workflow.run(rig.params)
    assert_safe(rig)
    assert len(rig.captures) == 1


def test_original_error_survives_restore_and_config_failure(rig, monkeypatch):
    from ...steps.run_directory import RunDirectory
    original = RunDirectory.update_config
    failure = OSError("原始采集异常")
    rig.fault_index, rig.acquire_error, rig.restore_error = 1, failure, True
    rig.restore_after = 2
    def save(self, **updates):
        if updates.get("failure_reason"):
            raise OSError("次要配置写失败")
        original(self, **updates)
    monkeypatch.setattr(RunDirectory, "update_config", save)
    with pytest.raises(OSError) as caught:
        workflow.run(rig.params)
    assert caught.value is failure
    directory = next((rig.root / "data" / workflow.DATA_TYPE).iterdir())
    assert (directory / "raw/optimal_frequency_0000_phase_000_attempt_00.npz").is_file()


def test_quality_dependency_invalidation_and_no_prerequisite_finish(rig):
    ctx = workflow.Workflow(rig.params)
    with pytest.raises(ValueError, match="缺少前置"):
        ctx.execute(["A04"])
    assert not rig.events
    ctx.execute(through("A03"))
    rig.quality_bad = True
    ctx.execute(["A04", "A05"])
    assert ctx.states["A04"]["status"] == "quality_failed"
    assert ctx.outputs["Y RF"] == "未知（本模块未完成）"
    assert ctx.states["A05"]["status"] == "pending"
    assert len(list(ctx.directory.raw.glob("*attempt*.npz"))) == rig.params.r_point_max_attempts
    ctx.finish()
    assert ctx.closed
    empty = workflow.Workflow(rig.params)
    empty.execute([FINISH_ID])
    assert empty.directory is None and empty.closed
    assert set(empty.outputs.values()) == {"未知"}


def test_full_stepwise_same_values_rerun_and_draft_shutdown(rig):
    full = workflow.run(rig.params)
    ctx = workflow.Workflow(rig.params)
    for stage in ACQUISITION:
        if stage.id != FINISH_ID:
            ctx.execute([stage.id])
    with np.load(full / workflow.OPTIMAL_CONTROL_DATA_FILE, allow_pickle=True) as left, np.load(ctx.directory.root / workflow.OPTIMAL_CONTROL_DATA_FILE, allow_pickle=True) as right:
        np.testing.assert_array_equal(left["r_mean_v"], right["r_mean_v"])
    original = ctx.directory.root
    ctx.devices["tec"].disconnect.assert_not_called()
    original_bytes = (original / "experiment_config.yaml").read_bytes()
    ctx.update_parameters({"PUMP_CARRIER_FREQUENCY_HZ": 99000000., "FREQUENCY_POINTS": 4})
    assert ctx.states["A03"]["status"] == "stale"
    ctx.execute(["A03", "A04", "A05", "A06"])
    assert ctx.directory.root != original
    assert (original / "experiment_config.yaml").read_bytes() == original_bytes
    last_params = ctx.shutdown_parameters
    ctx.update_parameters({"PUMP_CARRIER_FREQUENCY_HZ": 98000000.})
    assert ctx.shutdown_parameters is last_params
    ctx.finish()
    assert_safe(rig)
    ctx.devices["tec"].disconnect.assert_called_once()


def test_hidden_compatibility_keys_have_no_effect_and_visible_fields_owned(rig):
    fields = MxYOptimalControlRFFrequencyResponseParams().schema()["fields"]
    visible = {field["name"] for field in fields}
    declared = {key for stage in definition()["acquisition"] for key in stage["display_parameters"]}
    assert visible <= declared
    assert "Z_AW_OUTPUT_VPP" not in visible and "Z_AW_OUTPUT_OFFSET" not in visible
    params = MxYOptimalControlRFFrequencyResponseParams.from_external(rig.params.to_external() | {"Z_AW_OUTPUT_VPP": 7., "Z_AW_OUTPUT_OFFSET": 1.})
    workflow.run(params)
    assert all(settings == (.2, 0.) for settings in rig.aw_settings)


def test_frozen_envelope_and_common_trigger_reject_before_connection(rig):
    path = rig.root / "data/Z_AW_Closed_Loop_Waveform_Correction/frozen/results/corrected_control_waveform.npz"
    with np.load(path) as data:
        values = {key: data[key] for key in data.files}
    values["amplitude_vpp"] = np.asarray(100.)
    values["voltage_v"] = values["normalized"] * 50.
    np.savez(path, **values)
    with pytest.raises(ValueError, match="Z_magnetic_field"):
        workflow.Workflow(rig.params)
    assert not rig.events


def test_frozen_v4_requires_independent_reload_and_unchanged_science(rig):
    path = rig.root / "data/Z_AW_Closed_Loop_Waveform_Correction/frozen/results/corrected_control_waveform.npz"
    with np.load(path) as data:
        values = {key: data[key] for key in data.files}
    values.update(format_version=np.int64(4), correction_method="harmonic_jacobian", final_reload_validated=np.bool_(False),
        phase_reference="CH4_falling_edge", identification_protocol="central_difference_harmonic_jacobian",
        measured_current_a=values["target_current_a"], relative_rms_error=0., error_cutoff_hz=500., inverse_regularization=.01,
        frequency_response_run="", frequency_response_sha256="")
    np.savez(path, **values)
    with pytest.raises(ValueError, match="独立重载验证"):
        workflow.Workflow(rig.params)
    assert not rig.events
    values["final_reload_validated"] = np.bool_(True)
    np.savez(path, **values)
    before = path.read_bytes()
    preparation.preflight(rig.params, rig.root)
    assert path.read_bytes() == before and not rig.events


if __name__ == "__main__":
    from pytest import MonkeyPatch
    from .session import main
    root = Path(os.environ["MX_Y_OPTIMAL_FREQUENCY_TEST_ROOT"])
    patch = MonkeyPatch()
    rig = install_rig(root, patch)
    if name := os.environ.get("MX_Y_OPTIMAL_FREQUENCY_SHUTDOWN_GATE"):
        gate = Path(name)
        shutdown = preparation.safe_shutdown
        def gated(devices, channels, params, **kwargs):
            gate.with_suffix(".started").write_text("收尾开始", encoding="utf-8")
            deadline = time.monotonic() + 30
            while not gate.exists():
                if time.monotonic() > deadline:
                    raise TimeoutError("测试没有释放收尾")
                time.sleep(.01)
            report = shutdown(devices, channels, params, **kwargs)
            source = rig.mapping["Pump_modulation"]
            gate.with_suffix(".finished").write_text(json.dumps({"pump_frequency_hz": rig.generators[source["resource"]].frequency[source["channel"]],
                "completed": report.completed}), encoding="utf-8")
            return report
        patch.setattr(preparation, "safe_shutdown", gated)
    main()
