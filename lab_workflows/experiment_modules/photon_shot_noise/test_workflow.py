"""真实业务配假仪器及真实落盘；不连接任何仪器。"""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import json
import os
import shutil
import subprocess
import sys
import time
import numpy as np
import pytest
import yaml
from lockin_amplifier import lockin_capabilities
from lockin_amplifier.acquisition import acquisition_module, nearest_scope_rate, restore_demod_streams
from ...common import WorkflowCancelled, load_mapping
from ...steps import routed_signal_generator
from . import workflow, preparation, acquisition, storage, stages
from .models import PhotonShotNoiseParams

ACQUISITION_CASES = {
    "photon-shot-noise": {"mode": "single", "cases": {
        "order": ["test_each_repeat_order_real_files_and_slow_disk"],
        "slow_disk": ["test_each_repeat_order_real_files_and_slow_disk"],
        "cancel": ["test_cancel_after_read_saves_partial_and_stops"],
        "acquire_failure": ["test_bad_record_is_nan_but_cancel_is_not_swallowed"],
        "restore_failure": ["test_restore_failure_saves_read_data_and_primary_error"],
        "save_failure": ["test_atomic_save_failure_preserves_previous_and_rescues"],
        "partial_save": ["test_cancel_after_read_saves_partial_and_stops", "test_restore_failure_saves_read_data_and_primary_error"],
        "entrypoints": ["test_full_stepwise_rerun_and_frozen_snapshot", "test_real_full_subprocess"],
    }, "sources": [
        "lab_workflows/experiment_modules/photon_shot_noise/preparation.py",
        "lab_workflows/experiment_modules/photon_shot_noise/acquisition.py",
        "lab_workflows/experiment_modules/photon_shot_noise/storage.py",
        "lab_workflows/experiment_modules/photon_shot_noise/workflow.py",
        "lab_workflows/experiment_modules/photon_shot_noise/session.py",
    ]},
}
REGRESSION_CASES = {
    "photon-shot-noise": {
        "parameters": {
            "sources": [
                "lab_workflows/experiment_modules/photon_shot_noise/models.py",
                "params/experiments/photon-shot-noise.yaml",
            ],
            "tests": [
                "test_invalid_parameters_before_connection",
                "test_model_old_wait_new_key_priority_and_yaml_roundtrip",
                "test_saved_defaults_are_preflighted_without_connection",
            ],
        },
    },
}

ROOT = Path(__file__).resolve().parents[3]


def copy_config(root):
    (root / "params").mkdir(parents=True, exist_ok=True)
    for name in ("devices.yaml", "mapping.yaml", "safety_limits.yaml"):
        shutil.copyfile(ROOT / "params" / name, root / "params" / name)


def small_params():
    return replace(PhotonShotNoiseParams(), probe_power_points=5, acq_repeats=3,
        acq_duration=0.25, hf2_demod_rate=1024, psd_nperseg=64,
        system_settle_s=0.001, temp_recovery_time_s=0)


class FakeDG:
    def __init__(self, rig, resource):
        self.rig, self.resource = rig, resource
        self.output, self.dc, self.shape, self.output_events = {}, {}, {}, []
        self.clock = "INT"
        self.connections = self.disconnections = 0

    def connect(self):
        self.connections += 1

    def disconnect(self):
        self.disconnections += 1

    def setup_dc(self, value, *, channel):
        key = next((key for key in preparation.SIGNALS if self.rig.mapping[key]["resource"] == self.resource
                    and self.rig.mapping[key]["channel"] == channel), None)
        if key == "Temp_Switch":
            self.rig.events.append("on" if value else "off")
            if value and self.rig.restore_error and not self.rig.enabled:
                raise OSError("温控恢复失败")
            self.rig.enabled = bool(value)
        elif key == "Probe_laser_power":
            assert self.rig.enabled
            self.rig.events.append("configure")
            self.rig.power = value
        self.dc[channel] = value
        self.set_output(True, channel=channel)

    def set_shape(self, shape, *, channel):
        self.shape[channel] = shape

    def set_dc_voltage(self, value, *, channel):
        self.dc[channel] = value

    def set_output(self, value, *, channel):
        self.output[channel] = bool(value)
        self.output_events.append((channel, bool(value)))

    def set_burst_state(self, enabled, *, channel):
        assert not enabled

    def set_mod_state(self, enabled, *, channel):
        assert not enabled

    def get_ref_clock_source(self):
        return self.clock

    def set_ref_clock_source(self, source):
        self.clock = source


class FakeLockin:
    _acquisition_stream_settings = None
    clockbase = 210000000

    def __init__(self, rig):
        self.rig, self.nodes, self.extclk = rig, {}, False
        cfg = rig.mapping["lockin_r"]
        self.model, self.device_id = cfg["model"], cfg["device_id"]
        self.acquisition_mode = cfg.get("acquisition_mode", "auto")
        self.capabilities = lockin_capabilities(self.model, cfg.get("capabilities", {}).get("options", []))

    def connect(self):
        pass

    def disconnect(self):
        self.rig.events.append("lockin-disconnect")

    def get_extclk(self):
        return self.extclk and not self.rig.clock_error

    def set_extclk(self, value):
        self.extclk = bool(value)

    def sig_in_path(self, index):
        return f"/{self.device_id}/sigins/{index}"

    def osc_path(self, index):
        return f"/{self.device_id}/oscs/{index}"

    def demod_path(self, index):
        return f"/{self.device_id}/demods/{index}"

    def set_int(self, path, value):
        self.nodes[path] = value

    def set_double(self, path, value):
        self.nodes[path] = value

    def get_int(self, path):
        return int(self.nodes.get(path, 0))

    def get_double(self, path):
        return float(self.nodes.get(path, 1024 if path.endswith("/rate") else 0))

    def sync(self):
        pass


def install_rig(root, monkeypatch, params=None, *, progress=False):
    rig = SimpleNamespace(root=root, params=params or small_params(), mapping=load_mapping(root), events=[],
        acquisitions=[], enabled=True, power=0, cancel=False, cancel_after_read=False,
        restore_error=False, acquire_error=None, acquire_error_at=0, clock_error=False, generators={})
    for key in preparation.SIGNALS:
        resource = rig.mapping[key]["resource"]
        rig.generators.setdefault(resource, FakeDG(rig, resource))
    rig.gs, rig.tec, rig.hfi = Mock(), Mock(), FakeLockin(rig)
    monkeypatch.setattr(preparation, "GS200Instrument", lambda *a: rig.gs)
    monkeypatch.setattr(preparation, "TECInstrument", lambda **kw: rig.tec)
    monkeypatch.setattr(preparation, "create_lockin", lambda cfg, **kw: rig.hfi)
    monkeypatch.setattr(routed_signal_generator, "create_signal_generator", lambda cfg: rig.generators[cfg["resource"]])
    original = workflow.check_cancelled
    def check():
        original()
        if rig.cancel:
            raise WorkflowCancelled("模拟取消")
    def wait(seconds):
        rig.events.append("settle" if not rig.enabled else "recovery")
        if rig.enabled and rig.cancel_after_read and rig.acquisitions:
            rig.cancel = True
        check()
    monkeypatch.setattr(acquisition, "wait", wait)
    monkeypatch.setattr(workflow, "check_cancelled", check)
    monkeypatch.setattr(acquisition, "check_cancelled", check)
    def acquire(instr, config, **kwargs):
        assert not rig.enabled
        kwargs["cancellation_check"]()
        index = len(rig.acquisitions)
        rig.events.append("acquire")
        rig.acquisitions.append(config)
        if rig.acquire_error is not None and index == rig.acquire_error_at:
            raise rig.acquire_error
        module = acquisition_module(instr.model, kwargs["requested_rate"], instr.acquisition_mode)
        rate = nearest_scope_rate(kwargs["requested_rate"]) if module == "scope" else kwargs["requested_rate"]
        count = config.grid_cols
        rng = np.random.default_rng(index + 17)
        scale = np.sqrt(1e-6 + rig.power * 1e-5)
        snapshot = dict(acquisition_module=module, requested_rate=kwargs["requested_rate"], actual_rate=rate,
            demod_stream_rate=kwargs["actual_rate"], actual_duration=count/rate, time_source="device")
        if module == "scope":
            restore_demod_streams(instr)
        return [SimpleNamespace(signal_name=signal, values=rng.normal(size=count) * scale,
            time=np.arange(count)/rate, config_snapshot=snapshot) for signal in ("sample.x", "sample.y")]
    monkeypatch.setattr(acquisition.daq, "acquire_data", acquire)
    monkeypatch.setattr(workflow, "find_project_root", lambda: root)
    if not progress:
        monkeypatch.setattr(workflow, "report_runtime_progress", lambda *a, **kw: None)
        monkeypatch.setattr(acquisition, "report_runtime_progress", lambda *a, **kw: None)
    return rig


@pytest.fixture
def rig(tmp_path, monkeypatch):
    copy_config(tmp_path)
    return install_rig(tmp_path, monkeypatch)


def through(last):
    ids = [s.id for s in stages.ACQUISITION if s.id != stages.FINISH_ID]
    return ids[:ids.index(last) + 1]


def prepared(rig):
    ctx = workflow.Workflow(rig.params, project_root=rig.root)
    ctx.execute(through("A06"))
    rig.events.clear()
    return ctx


def test_each_repeat_order_real_files_and_slow_disk(rig, monkeypatch):
    ctx = prepared(rig)
    original_open, savez = Path.open, np.savez
    def open_file(path, mode="r", *args, **kwargs):
        if any(c in mode for c in "wax+"):
            assert rig.enabled, f"关闭窗口禁止写盘：{path}"
        return original_open(path, mode, *args, **kwargs)
    def save(stream, *args, **kwargs):
        assert rig.enabled
        rig.events.append("save")
        time.sleep(0.002)
        return savez(stream, *args, **kwargs)
    monkeypatch.setattr(Path, "open", open_file)
    monkeypatch.setattr(np, "savez", save)
    ctx.execute(["A07"])
    events = rig.events
    assert events[:7] == ["configure", "off", "settle", "acquire", "on", "recovery", "save"]
    assert events.count("off") == events.count("acquire") == 15
    for i, event in enumerate(events):
        if event == "acquire":
            assert events[i-2:i] == ["off", "settle"]
            assert events[i+1:i+3] == ["on", "recovery"]
    assert len(list(ctx.directory.raw.glob("waveforms_P*.npz"))) == 5
    with np.load(ctx.directory.raw / "scan_meta.npz") as meta:
        np.testing.assert_array_equal(meta["completed_records"], [3]*5)
    assert all(g.connections == 1 for g in rig.generators.values())
    pump = rig.mapping["Pump_laser_power"]
    assert (pump["channel"], True) not in rig.generators[pump["resource"]].output_events
    rig.tec.set_enable.assert_not_called()
    rig.tec.get_temperature.assert_not_called()
    ctx.finish()
    assert rig.enabled and ctx.state["safety_shutdown"]["completed"]
    assert ctx.state["temperature_control"]["actual_temperature_c"] is None
    for key, output in (("Pump_laser_power", False), ("Probe_laser_power", True), ("Temp_Switch", True)):
        cfg = rig.mapping[key]
        assert rig.generators[cfg["resource"]].output[cfg["channel"]] is output


def test_cancel_after_read_saves_partial_and_stops(rig):
    ctx = prepared(rig)
    rig.cancel_after_read = True
    with pytest.raises(WorkflowCancelled, match="模拟取消") as failure:
        ctx.execute(["A07"])
    ctx.finish(failure.value)
    assert len(rig.acquisitions) == 1 and rig.enabled
    with np.load(ctx.directory.raw / "waveforms_P000.npz") as wave:
        assert wave["x_waveforms"].shape == (1, 256)
    assert ctx.state["completion_status"] == "cancelled"


def test_bad_record_is_nan_but_cancel_is_not_swallowed(rig):
    ctx = prepared(rig)
    rig.acquire_error = RuntimeError("模拟采集失败")
    ctx.execute(["A07"])
    with np.load(ctx.directory.raw / "waveforms_P000.npz") as wave:
        assert np.all(np.isnan(wave["x_waveforms"][0]))
        assert np.all(np.isfinite(wave["x_waveforms"][1:]))
        assert "模拟采集失败" in str(wave["acquisition_metadata_json"][0])
    assert len(ctx.state["failed_records"]) == 1
    ctx.finish()


@pytest.mark.parametrize("primary", [False, True])
def test_restore_failure_saves_read_data_and_primary_error(rig, primary):
    ctx = prepared(rig)
    rig.restore_error = True
    if primary:
        rig.acquire_error = WorkflowCancelled("原始取消")
        with pytest.raises(WorkflowCancelled, match="原始取消") as failure:
            ctx.execute(["A07"])
    else:
        with pytest.raises(OSError, match="温控恢复失败") as failure:
            ctx.execute(["A07"])
        with np.load(ctx.directory.raw / "waveforms_P000.npz") as wave:
            assert wave["x_waveforms"].shape[0] == 1
    ctx.finish(failure.value)
    assert rig.events.index("on") > rig.events.index("acquire")
    assert len(rig.acquisitions) == 1
    assert not ctx.snapshot()["safety_shutdown"]["completed"]


def test_atomic_save_failure_preserves_previous_and_rescues(rig, monkeypatch):
    ctx = prepared(rig)
    replace_file = Path.replace
    previous = {}
    def replace_atomic(source, target):
        if Path(target).name == "waveforms_P000.npz" and len(rig.acquisitions) == 2:
            previous["bytes"] = Path(target).read_bytes()
            raise OSError("磁盘替换失败")
        return replace_file(source, target)
    monkeypatch.setattr(Path, "replace", replace_atomic)
    with pytest.raises(OSError, match="磁盘替换失败") as failure:
        ctx.execute(["A07"])
    ctx.finish(failure.value)
    assert (ctx.directory.raw / "waveforms_P000.npz").read_bytes() == previous["bytes"]
    rescue = list(ctx.directory.raw.glob("waveforms_P000_rescue_*.npz"))
    assert len(rescue) == 1 and len(rig.acquisitions) == 2
    with np.load(rescue[0]) as wave:
        assert wave["x_waveforms"].shape[0] == 2


def test_full_stepwise_rerun_and_frozen_snapshot(rig):
    ctx = prepared(rig)
    ctx.execute(["A07"])
    first = ctx.directory.root
    frozen = {p:p.read_bytes() for p in first.rglob("*") if p.is_file()}
    ctx.update_parameters({"PROBE_POWER_STOP": 0.2, "FIXED_PARAMS.Pump_laser_power": 0.3})
    assert ctx.states["A04"]["status"] == "stale"
    ctx.execute(through("A07"))
    assert ctx.directory.root != first
    assert {p:p.read_bytes() for p in frozen} == frozen
    ctx.finish()
    cfg = yaml.safe_load((ctx.directory.root / "experiment_config.yaml").read_text(encoding="utf-8"))
    assert cfg["completion_status"] == "completed" and cfg["raw_format_version"] == 1


def test_missing_dependency_and_parameter_impacts_without_hardware(rig):
    ctx = workflow.Workflow(rig.params, project_root=rig.root)
    with pytest.raises(ValueError, match="缺少前置"):
        ctx.execute(["A07"])
    assert not ctx.devices
    flow = stages.definition()
    assert {key for s in flow["acquisition"]+flow["analysis"] for key in s["display_parameters"]} == set(rig.params.to_external())
    for values in ({"ACQ_DURATION": 0.5}, {"PROBE_POWER_START": 0.01, "ACQ_REPEATS": 4}, {"PSD_NPERSEG": 32}):
        ctx.states = {s.id:dict(status="completed", error="") for s in stages.ACQUISITION}
        ctx.update_parameters(values)
        affected = {sid for key in values for sid in flow["parameter_impacts"]["acquisition"].get(key, [])}
        assert {key for key, value in ctx.states.items() if value["status"] == "stale"} == affected
        before = ctx.snapshot()["steps"]
        ctx.recheck()
        assert ctx.snapshot()["steps"] == before
    assert not ctx.devices


def test_mode_drift_and_configuration_change_rejected(rig):
    ctx = prepared(rig)
    rig.hfi.nodes[rig.hfi.demod_path(rig.params.hf2_demod_idx)+"/adcselect"] = 1
    with pytest.raises(ValueError, match="模式已改变"):
        ctx.execute(["A07"])
    assert not rig.acquisitions
    path = rig.root / "params/safety_limits.yaml"
    path.write_text(path.read_text(encoding="utf-8")+"\n", encoding="utf-8")
    with pytest.raises(ValueError, match="已改变"):
        ctx.recheck()
    ctx.finish()


def test_clock_failure_and_partial_connection_cleanup(rig, monkeypatch):
    ctx = workflow.Workflow(rig.params, project_root=rig.root)
    rig.clock_error = True
    with pytest.raises(RuntimeError, match="时钟源设置失败") as failure:
        ctx.execute(through("A03"))
    ctx.finish(failure.value)
    rig.gs.set_current.assert_not_called()
    rig.gs.disconnect.assert_called_once()
    assert "lockin-disconnect" in rig.events


def test_final_disconnect_failure_and_unapplied_pump_draft(rig):
    ctx = prepared(rig)
    ctx.execute(["A07"])
    cfg = rig.mapping["Pump_laser_power"]
    sg = rig.generators[cfg["resource"]]
    ctx.update_parameters({"FIXED_PARAMS.Pump_laser_power": 0.6})
    rig.tec.disconnect.side_effect = OSError("TEC 断开失败")
    with pytest.raises(RuntimeError, match="TEC"):
        ctx.finish()
    assert sg.dc[cfg["channel"]] == 0 and not sg.output[cfg["channel"]]
    assert ctx.states["A08"]["status"] == "failed" and not ctx.snapshot()["safety_shutdown"]["completed"]
    config = yaml.safe_load(ctx.directory.config_path.read_text(encoding="utf-8"))
    assert config["completion_status"] == "failed" and not config["safety_shutdown"]["completed"]
    assert config["parameters"]["FIXED_PARAMS.Pump_laser_power"] == 0


@pytest.mark.parametrize("key,value", [("hf2_demod_rate", float("nan")), ("probe_power_stop", 3),
    ("shot_noise_field_ma", 100), ("system_settle_s", -1), ("hf2_demod_idx", 6), ("temp_switch", 0)])
def test_invalid_parameters_before_connection(rig, key, value):
    with pytest.raises(ValueError):
        workflow.Workflow(replace(rig.params, **{key:value}), project_root=rig.root)
    assert not rig.events


def test_saved_defaults_are_preflighted_without_connection(rig, monkeypatch):
    from .definition import ADAPTER
    assert ADAPTER.defaults_path.resolve() == (ROOT / "params/experiments/photon-shot-noise.yaml").resolve()
    params = ADAPTER.defaults()
    preparation.preflight(params, rig.root)
    unsafe = rig.root / "params/experiments/unsafe_defaults.yaml"
    replace(params, probe_power_stop=3.0).save_yaml(unsafe)
    monkeypatch.setattr(ADAPTER, "defaults_path", unsafe)
    with pytest.raises(ValueError):
        preparation.preflight(ADAPTER.defaults(), rig.root)
    assert not rig.events


def test_model_old_wait_new_key_priority_and_yaml_roundtrip(rig, monkeypatch):
    from .definition import ADAPTER
    assert PhotonShotNoiseParams.from_external({"PROBE_SETTLE_TIME": 0.7}).system_settle_s == 0.7
    assert PhotonShotNoiseParams.from_external({"PROBE_SETTLE_TIME": 0.7, "SYSTEM_SETTLE_S": 0.2}).system_settle_s == 0.2
    monkeypatch.setattr(ADAPTER, "defaults", lambda: replace(rig.params, acq_repeats=7))
    assert ADAPTER._resolved({"PROBE_SETTLE_TIME": 0.7}).acq_repeats == 7
    target = rig.root / "params/experiments/photon-shot-noise.yaml"
    rig.params.save_yaml(target)
    assert PhotonShotNoiseParams.from_yaml(target).to_external() == rig.params.to_external()


@pytest.mark.parametrize("model,rate,module", [("HF2",1024,"daq"),("MFLI",1024,"daq"),("MFLI",200000,"scope")])
def test_hf2_mfli_auto_modules_save_final_rate_and_time(tmp_path, monkeypatch, model, rate, module):
    copy_config(tmp_path)
    path = tmp_path / "params/mapping.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    for key in ("lockin_r","lockin_xy"):
        config["mapping"][key]["device_id"] = "hf2_dev18246" if model == "HF2" else "mfli_dev30661"
    path.write_text(yaml.safe_dump(config),encoding="utf-8")
    params = replace(small_params(), hf2_demod_rate=rate, acq_duration=0.01, acq_repeats=1)
    rig = install_rig(tmp_path,monkeypatch,params)
    ctx = prepared(rig)
    assert ctx.plan.module == module
    ctx.execute(["A07"])
    expected = nearest_scope_rate(rate) if module == "scope" else rate
    with np.load(ctx.directory.raw / "waveforms_P000.npz") as wave:
        assert float(wave["actual_rates_Sa_s"][0]) == expected
        np.testing.assert_allclose(np.diff(wave["time_s"][0]), 1/expected)
        assert json.loads(str(wave["acquisition_metadata_json"][0]))["acquisition_module"] == module
    ctx.finish()


@pytest.mark.parametrize("model", ["DG4000","DG900"])
def test_real_signal_driver_commands_never_enable_pump(rig, monkeypatch, model):
    from signal_generator import DG4000Instrument, DG900Instrument
    cls = DG4000Instrument if model == "DG4000" else DG900Instrument
    pump = rig.mapping["Pump_laser_power"]
    native = cls(pump["resource"])
    commands = []
    monkeypatch.setattr(native,"write",commands.append)
    monkeypatch.setattr(native,"connect",lambda:None)
    monkeypatch.setattr(native,"get_ref_clock_source",lambda:"EXTernal")
    monkeypatch.setattr(native,"set_burst_state",lambda *a,**kw:None)
    monkeypatch.setattr(native,"set_mod_state",lambda *a,**kw:None)
    rig.generators[pump["resource"]] = native
    ctx = prepared(rig)
    assert any("OFF" in command and ":OUTPut1" in command for command in commands)
    assert not any("ON" in command and ":OUTPut1" in command for command in commands)
    assert any(":SOURce1:" in command and "APPLy:" in command for command in commands)
    ctx.finish()


def test_hardware_rounding_recorded_and_later_drift_rejected(rig, monkeypatch):
    setter = rig.hfi.set_double
    def rounded(path, value):
        if path.endswith("/timeconstant"):
            value *= 1.25
        elif path.endswith("/range"):
            value *= 1.5
        setter(path,value)
    monkeypatch.setattr(rig.hfi,"set_double",rounded)
    ctx = prepared(rig)
    actual = ctx.state["daq_settings"]
    assert actual["actual_time_constant_s"] == rig.params.hf2_demod_tc*1.25
    assert actual["requested_time_constant_s"] == rig.params.hf2_demod_tc
    assert actual["actual_input_range_v"] == rig.params.hf2_signal_range*1.5
    preparation.verify_ready(ctx)
    rig.hfi.nodes[rig.hfi.demod_path(rig.params.hf2_demod_idx)+"/timeconstant"] *= 2
    with pytest.raises(ValueError,match="配置回读不一致"):
        ctx.execute(["A07"])
    assert not rig.acquisitions
    ctx.finish()


def test_optional_tec_and_partial_connection_failure(rig):
    rig.tec.connect.side_effect = OSError("串口不可用")
    ctx = prepared(rig)
    assert ctx.devices["tec"] is None
    assert not ctx.state["temperature_control"]["controlled_by_experiment"]
    ctx.finish()
    rig.gs.disconnect.reset_mock()
    rig.hfi.connect = Mock(side_effect=OSError("锁相连接失败"))
    ctx = workflow.Workflow(rig.params,project_root=rig.root)
    with pytest.raises(OSError,match="锁相连接失败") as failure:
        ctx.execute(through("A02"))
    ctx.finish(failure.value)
    rig.gs.disconnect.assert_called_once()
    assert not ctx.devices


def test_real_full_subprocess(tmp_path):
    copy_config(tmp_path)
    env = dict(os.environ, PHOTON_TEST_ROOT=str(tmp_path), PHOTON_TEST_FULL="1",
        LAB_TYPED_PARAMETERS=json.dumps(small_params().to_external()))
    env.pop("LAB_CANCEL_FILE", None)
    result = subprocess.run([sys.executable, "-X", "utf8", "-m", __name__], cwd=ROOT,
        env=env, capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stdout+result.stderr
    files = list((tmp_path / "data/Photon_shot_noise").glob("*/raw/scan_meta.npz"))
    assert len(files) == 1
    with np.load(files[0]) as meta:
        np.testing.assert_array_equal(meta["completed_records"], [3]*5)
    config = yaml.safe_load((files[0].parents[1] / "experiment_config.yaml").read_text(encoding="utf-8"))
    assert config["completion_status"] == "completed" and config["safety_shutdown"]["completed"]


def fake_session_main():
    """测试显式选择的假仪器进程入口，调用生产完整/分步协议。"""
    from pytest import MonkeyPatch
    from .session import serve
    payload = json.loads(os.environ["LAB_TYPED_PARAMETERS"])
    session_id = payload.pop("session_id", None)
    root = Path(os.environ["PHOTON_TEST_ROOT"])
    params = PhotonShotNoiseParams.from_external(payload)
    with MonkeyPatch.context() as patch:
        install_rig(root, patch, params, progress=True)
        if os.environ.get("PHOTON_TEST_FULL") == "1":
            workflow.main()
        else:
            serve(workflow.Workflow(params, project_root=root, session_id=session_id), sys.stdin)


if __name__ == "__main__":
    fake_session_main()
