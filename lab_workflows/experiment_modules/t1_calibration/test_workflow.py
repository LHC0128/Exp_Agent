"""真实 T1 配置/采集/写文件配假仪器；不连接真实硬件。"""
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
from ...common import load_mapping, WorkflowCancelled
from ...steps import routed_signal_generator
from . import workflow, preparation, acquisition, stages, storage
from .models import T1CalibrationParams

ROOT = Path(__file__).resolve().parents[3]
ACQUISITION_CASES = {"t1-calibration": {"mode": "single", "cases": {
    "order": ["test_order_slow_disk_average_and_shutdown"],
    "slow_disk": ["test_order_slow_disk_average_and_shutdown"],
    "cancel": ["test_cancel_after_read_saves_frame"],
    "acquire_failure": ["test_later_failure_preserves_complete_point"],
    "restore_failure": ["test_restore_failure_preserves_data_and_primary_error"],
    "save_failure": ["test_disk_failure_preserves_old_and_rescues"],
    "partial_save": ["test_cancel_after_read_saves_frame", "test_later_failure_preserves_complete_point"],
    "entrypoints": ["test_full_stepwise_rerun_and_parameter_impacts", "test_real_full_subprocess"]},
    "sources": ["lab_workflows/experiment_modules/t1_calibration/workflow.py",
        "lab_workflows/experiment_modules/t1_calibration/preparation.py", "lab_workflows/experiment_modules/t1_calibration/acquisition.py",
        "lab_workflows/experiment_modules/t1_calibration/session.py", "lab_workflows/experiment_modules/t1_calibration/stages.py",
        "lab_workflows/experiment_modules/t1_calibration/storage.py", "experiments/T1_Calibration.py"]}}
REGRESSION_CASES = {"t1-calibration": {"parameters": {
    "sources": ["lab_workflows/experiment_modules/t1_calibration/models.py", "params/experiments/t1-calibration.yaml"],
    "tests": ["test_defaults_migration_yaml_and_safety", "test_invalid_parameters_rejected_before_connection"]}}}


def copy_config(root):
    (root/"params").mkdir(parents=True, exist_ok=True)
    for name in ("mapping.yaml", "devices.yaml", "safety_limits.yaml"):
        shutil.copyfile(ROOT/"params"/name, root/"params"/name)


def small_params():
    return replace(T1CalibrationParams(), probe_power_list=[.03, .06], acq_repeats=2,
        scope_volt_scale=.05, pump_prep_time=.02, system_settle_s=.001, temp_switch_on_lag_s=0)


def signal():
    t = np.arange(-.01, .09, .0001)
    y = .005+.1*np.exp(-np.maximum(t, 0)/.025)+np.random.default_rng(42).normal(0, 1e-5, t.size)
    return t, y


class FakeDG:
    def __init__(self, rig, resource):
        self.rig, self.resource = rig, resource
        self.clock, self.connections, self.disconnects = "INT", 0, 0
        self.output, self.dc, self.burst, self.mod, self.sync, self.waves = {}, {}, {}, {}, {}, {}
    def connect(self): self.connections += 1
    def disconnect(self): self.disconnects += 1
    def get_ref_clock_source(self): return "INT" if self.rig.clock_error else self.clock
    def set_ref_clock_source(self, value): self.clock = value
    def set_burst_state(self, value, *, channel): self.burst[channel] = value
    def get_burst_state(self, *, channel): return self.burst.get(channel, False)
    def set_mod_state(self, value, *, channel): self.mod[channel] = value
    def set_sync_state(self, value, *, channel): self.sync[channel] = value
    def set_square_dcycle(self, value, *, channel): assert value == 50
    def set_output(self, value, *, channel): self.output[channel] = bool(value)
    def get_output(self, *, channel): return self.output.get(channel, False)
    def get_frequency(self, *, channel): return self.waves[channel][1]["freq"]
    def apply_wave(self, *args, channel, **kwargs):
        assert self.rig.enabled
        self.waves[channel] = (args, kwargs)
    def setup_dc(self, value, *, channel):
        rig, temp = self.rig, self.rig.mapping["Temp_Switch"]
        if self.resource == temp["resource"] and channel == temp["channel"]:
            rig.events.append("on" if value else "off")
            if value == 5 and rig.restore_error:
                rig.restore_attempted = True
                raise OSError("温控恢复失败")
            rig.enabled = bool(value)
        else:
            rig.events.append("configure")
        self.dc[channel], self.output[channel] = value, True


class FakeScope:
    def __init__(self, rig): self.rig, self.settings, self.connections, self.disconnects = rig, {}, 0, 0
    def connect(self): self.connections += 1
    def disconnect(self): self.disconnects += 1
    def get_actual_points(self): return len(signal()[0])
    def get_sampling_rate(self): return self.rig.params.scope_sampling_rate
    def get_trigger_mode(self): return self.settings["set_trigger_mode"][0]
    def get_trigger_source(self): return self.settings["set_trigger_source"][0]
    def trigger_status(self): return "Trig'd"
    def trigger_stop(self): self.rig.events.append("stop")
    def trigger_run(self): self.rig.events.append("arm")
    def __getattr__(self, name):
        if name.startswith("set_"):
            def setter(*args):
                assert self.rig.enabled, name
                self.rig.events.append("prepare")
                self.settings[name] = args
            return setter
        raise AttributeError(name)


class FakeAcquirer(preparation.SDSAcquisition):
    def acquire_channel(self, channel, *args, **kwargs):
        rig = self._inst.rig
        assert channel == 1 and not rig.enabled
        rig.reads += 1
        rig.events.append("acquire")
        if rig.reads == rig.fail_at:
            raise rig.acquire_error
        t, y = signal()
        if rig.axis_drift and rig.reads > 1: t = t+.001
        if rig.cancel_after_read: rig.cancel = True
        return SimpleNamespace(time=t, voltage=y)


def install_rig(root, monkeypatch, params=None, *, progress=False):
    rig = SimpleNamespace(root=root, params=params or small_params(), mapping=load_mapping(root), events=[], enabled=True,
        cancel=False, cancel_after_read=False, restore_error=False, restore_attempted=False, clock_error=False,
        axis_drift=False, reads=0, fail_at=-1, acquire_error=RuntimeError("模拟采集失败"), generators={})
    for key in preparation.SIGNALS:
        resource = rig.mapping[key]["resource"]
        rig.generators.setdefault(resource, FakeDG(rig, resource))
    rig.tec, rig.scope = Mock(), FakeScope(rig)
    monkeypatch.setattr(preparation, "TECInstrument", lambda **kw: rig.tec)
    monkeypatch.setattr(preparation, "SDSInstrument", lambda *a: rig.scope)
    monkeypatch.setattr(preparation, "SDSAcquisition", FakeAcquirer)
    monkeypatch.setattr(routed_signal_generator, "create_signal_generator", lambda cfg: rig.generators[cfg["resource"]])
    original_check, sync = workflow.check_cancelled, preparation.synchronize_connected_clocks
    def check():
        original_check()
        if rig.cancel: raise WorkflowCancelled("模拟取消")
    def wait(seconds):
        rig.events.append("settle" if not rig.enabled else "recovery")
        check()
    monkeypatch.setattr(acquisition, "wait", wait)
    monkeypatch.setattr(acquisition, "check_cancelled", check)
    monkeypatch.setattr(workflow, "check_cancelled", check)
    monkeypatch.setattr(workflow, "find_project_root", lambda: root)
    monkeypatch.setattr(preparation, "synchronize_connected_clocks", lambda *a, **kw: sync(*a, **kw, settle_s=0))
    if not progress:
        monkeypatch.setattr(workflow, "report_runtime_progress", lambda *a, **kw: None)
        monkeypatch.setattr(acquisition, "report_runtime_progress", lambda *a, **kw: None)
    return rig


@pytest.fixture
def rig(tmp_path, monkeypatch):
    copy_config(tmp_path)
    return install_rig(tmp_path, monkeypatch)


def prepared(rig):
    ctx = workflow.Workflow(rig.params, project_root=rig.root)
    ctx.execute([f"A{i:02d}" for i in range(1, 7)])
    rig.events.clear()
    return ctx


def assert_safe(rig):
    for key in ("X_magnetic_field", "Y_magnetic_field", "Z_magnetic_field"):
        cfg = rig.mapping[key]; dg = rig.generators[cfg["resource"]]
        assert not dg.output[cfg["channel"]] and dg.dc[cfg["channel"]] == 0
    for key in ("Probe_laser_power", "Pump_modulation", "Time_sequence"):
        cfg = rig.mapping[key]
        assert rig.generators[cfg["resource"]].output[cfg["channel"]]
    cfg = rig.mapping["Time_sequence"]
    assert rig.generators[cfg["resource"]].sync[cfg["channel"]]


def test_order_slow_disk_average_and_shutdown(rig, monkeypatch):
    ctx = prepared(rig)
    opened, savez = Path.open, np.savez
    def open_file(path, mode="r", *args, **kwargs):
        if any(c in mode for c in "wax+"): assert rig.enabled, path
        return opened(path, mode, *args, **kwargs)
    def save(*args, **kwargs):
        assert rig.enabled
        rig.events.append("save"); time.sleep(.002)
        return savez(*args, **kwargs)
    monkeypatch.setattr(Path, "open", open_file); monkeypatch.setattr(np, "savez", save)
    ctx.execute(["A07"]); ctx.finish()
    for i, event in enumerate(rig.events):
        if event == "off":
            assert rig.events[i-1] == "recovery" and "prepare" in rig.events[:i]
            assert rig.events[i+1] == "settle"
        if event == "acquire": assert rig.events[i+1] == "on"
    assert rig.reads == 4 and rig.events.count("off") == rig.reads
    with np.load(ctx.directory.raw/"point_p00.npz") as data:
        np.testing.assert_array_equal(data["vdiff_avg"], signal()[1])
        assert int(data["n_acquired"]) == 2
    assert rig.enabled and all(g.connections == 1 for g in rig.generators.values())
    assert_safe(rig)
    rig.tec.set_enable.assert_not_called(); rig.tec.get_temperature.assert_not_called()
    assert ctx.state["temperature_control"]["actual_temperature_c"] is None


def test_cancel_after_read_saves_frame(rig):
    ctx = prepared(rig); rig.cancel_after_read = True
    with pytest.raises(WorkflowCancelled) as failure: ctx.execute(["A07"])
    ctx.finish(failure.value)
    assert rig.reads == 1 and rig.enabled and len(list(ctx.directory.raw.glob("frame_*.npz"))) == 1
    assert ctx.state["completion_status"] == "cancelled"
    assert_safe(rig)


def test_later_failure_preserves_complete_point(rig):
    ctx = prepared(rig); rig.fail_at = 4
    with pytest.raises(RuntimeError, match="模拟采集失败") as failure: ctx.execute(["A07"])
    ctx.finish(failure.value)
    assert rig.enabled and (ctx.directory.raw/"point_p00.npz").is_file()
    assert not (ctx.directory.raw/"point_p01.npz").exists()
    with np.load(ctx.directory.raw/"power_scan_results.npz") as data: assert len(data["T1"]) == 1
    assert len(list(ctx.directory.raw.glob("frame_*.npz"))) == 3
    assert_safe(rig)


@pytest.mark.parametrize("primary", [False, True])
def test_restore_failure_preserves_data_and_primary_error(rig, primary):
    ctx = prepared(rig); rig.restore_error = True
    if primary: rig.fail_at, rig.acquire_error = 1, WorkflowCancelled("最初取消")
    with pytest.raises(WorkflowCancelled if primary else OSError) as failure: ctx.execute(["A07"])
    ctx.finish(failure.value)
    assert rig.restore_attempted and rig.reads == 1 and ctx.state["safety_shutdown"]["completed"] is False
    if primary: assert failure.value is rig.acquire_error
    else:
        with np.load(next(ctx.directory.raw.glob("frame_*.npz"))) as saved:
            np.testing.assert_array_equal(saved["signal_v"], signal()[1])
    assert_safe(rig)


def test_disk_failure_preserves_old_and_rescues(rig, monkeypatch):
    ctx = prepared(rig)
    path = ctx.directory.raw/"existing.npz"; storage.save_npz(path, signal=[1]); before = path.read_bytes()
    original = Path.replace
    def replace_file(source, target):
        if Path(target) == path or Path(target).name == "frame_p00_r00_a0.npz": raise OSError("模拟磁盘失败")
        return original(source, target)
    monkeypatch.setattr(Path, "replace", replace_file)
    with pytest.raises(OSError): storage.save_npz(path, signal=[2])
    assert path.read_bytes() == before
    with pytest.raises(OSError) as failure: ctx.execute(["A07"])
    ctx.finish(failure.value)
    assert rig.enabled
    with np.load(ctx.directory.raw/"recovery_frame_p00_r00_a0.npz") as data:
        np.testing.assert_array_equal(data["signal_v"], signal()[1])
    assert_safe(rig)


def test_scale_retry_reconfigures_warm_and_axis_drift_is_not_averaged(rig, monkeypatch):
    ctx = prepared(rig)
    monkeypatch.setattr(acquisition, "auto_scale_signal", Mock(side_effect=[(.025, True), (.025, False), (.025, False)]))
    result = acquisition.measure_single(ctx, .03, 0)
    assert result["n_acquired"] == 2 and rig.reads == 3 and rig.enabled
    rig.axis_drift = True; rig.reads = 0
    monkeypatch.setattr(acquisition, "auto_scale_signal", lambda *args: (.05, False))
    with pytest.raises(ValueError, match="时间轴不一致"): acquisition.measure_single(ctx, .03, 1)
    assert len(list(ctx.directory.raw.glob("frame_p01*.npz"))) == 2
    ctx.finish()


def test_full_stepwise_rerun_and_parameter_impacts(rig):
    ctx = workflow.Workflow(rig.params, project_root=rig.root)
    with pytest.raises(ValueError, match="缺少前置"): ctx.execute(["A07"])
    assert not ctx.devices
    ctx.execute([f"A{i:02d}" for i in range(1, 8)])
    first = ctx.directory.root; config = (first/"experiment_config.yaml").read_bytes()
    assert ctx.states["A04"]["status"] == "stale"
    with pytest.raises(ValueError): ctx.execute(["A06", "A07"])
    ctx.execute(["A04", "A05", "A06", "A07"])
    assert ctx.directory.root != first and (first/"experiment_config.yaml").read_bytes() == config
    ctx.execute(["A01"])
    assert ctx.states["A03"]["status"] == "completed"
    ctx.update_parameters({"T1_MAX_S": .4, "RUN_TAG": "next"})
    assert ctx.states["A03"]["status"] == "completed" and ctx.states["A07"]["status"] == "stale"
    ctx.finish(); assert_safe(rig)


@pytest.mark.parametrize("with_outputs", [False, True])
def test_clock_failure_and_connection_failure_cleanup(rig, with_outputs):
    ctx = prepared(rig) if with_outputs else workflow.Workflow(rig.params, project_root=rig.root)
    if not with_outputs:
        ctx.execute(["A01", "A02"])
    rig.clock_error = True
    with pytest.raises(RuntimeError) as failure: ctx.execute(["A03"])
    ctx.finish(failure.value)
    assert all(g.disconnects == 1 for g in rig.generators.values())
    if with_outputs:
        assert_safe(rig)
    else:
        assert not any(g.waves for g in rig.generators.values())


def test_mode_drift_and_tec_unavailable(rig):
    rig.tec.connect.side_effect = OSError("串口占用")
    ctx = prepared(rig)
    assert ctx.devices["tec"] is None and ctx.state["temperature_control"]["control_source"] == "external_software"
    gate = rig.mapping["Time_sequence"]
    rig.generators[gate["resource"]].burst[gate["channel"]] = True
    with pytest.raises(ValueError, match="Pump 模式已改变"): ctx.execute(["A07"])
    assert rig.reads == 0
    ctx.finish()


@pytest.mark.parametrize("trigger", ["Stop", "Wait"])
def test_unconfirmed_trigger_restores_and_never_reads_old_frame(rig, monkeypatch, trigger):
    ctx = prepared(rig)
    rig.scope.trigger_status = lambda: trigger
    ticks = iter(range(0, 1000, 20))
    monkeypatch.setattr(acquisition, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    with pytest.raises((RuntimeError, TimeoutError)) as failure:
        ctx.execute(["A07"])
    ctx.finish(failure.value)
    assert rig.reads == 0 and rig.enabled
    assert not list(ctx.directory.raw.glob("frame_*.npz"))
    assert_safe(rig)


def test_partial_connection_failure_releases_all_devices(rig):
    rig.scope.connect = Mock(side_effect=OSError("示波器连接失败"))
    ctx = workflow.Workflow(rig.params, project_root=rig.root)
    with pytest.raises(OSError) as failure:
        ctx.execute(["A01", "A02"])
    ctx.finish(failure.value)
    assert not ctx.devices and all(g.disconnects == 1 for g in rig.generators.values())


def test_single_power_full_thin_entry_matches_stepwise(rig, monkeypatch):
    import runpy
    rig.params = replace(rig.params, do_power_scan=False, acq_repeats=1)
    ctx = prepared(rig)
    ctx.execute(["A07"]); ctx.finish()
    with np.load(ctx.directory.raw/"t1_measurement.npz") as data:
        first = {k: data[k] for k in data.files}
    monkeypatch.setenv("LAB_TYPED_PARAMETERS", json.dumps(rig.params.to_external()))
    with pytest.raises(SystemExit) as exit_status:
        runpy.run_path(str(ROOT/"experiments/T1_Calibration.py"), run_name="__main__")
    assert exit_status.value.code == 0
    runs = sorted((rig.root/"data/T1_Calibration").iterdir())
    assert len(runs) == 2
    other = next(p for p in runs if p != ctx.directory.root)
    with np.load(other/"raw/t1_measurement.npz") as data:
        for key in first:
            np.testing.assert_array_equal(first[key], data[key])
        assert np.all(data["t_axis"] >= 0)
    assert_safe(rig)


def test_new_round_does_not_copy_previous_requested_scope_metadata(rig):
    ctx = prepared(rig)
    ctx.update_parameters({"SCOPE_SAMPLING_RATE": 20000})
    ctx.execute(["A06"])
    assert ctx.state["scope"]["sampling_rate_Hz"] == 20000
    assert ctx.run_parameters["SCOPE_SAMPLING_RATE"] == 20000
    ctx.finish()


def test_defaults_migration_yaml_and_safety(tmp_path):
    from .definition import ADAPTER
    p = T1CalibrationParams.from_yaml(ROOT/"params/experiments/t1-calibration.yaml")
    preparation.preflight(p, ROOT); p.save_yaml(tmp_path/"saved.yaml")
    assert T1CalibrationParams.from_yaml(tmp_path/"saved.yaml").to_external() == p.to_external()
    assert T1CalibrationParams.from_external({"T1_BOUNDS": [.001, .3]}).t1_bounds == (.001, .3)
    assert T1CalibrationParams.from_external({"T1_BOUNDS": [.001, .3], "T1_MAX_S": .4}).t1_bounds == (.001, .4)
    assert ADAPTER._resolved({"T1_BOUNDS": [.001, .3]}).t1_bounds == (.001, .3)
    original = ADAPTER.defaults_path
    try:
        ADAPTER.defaults_path = tmp_path/"unsafe.yaml"
        replace(p, probe_power_list=[99]).save_yaml(ADAPTER.defaults_path)
        assert ADAPTER.preflight({})
    finally: ADAPTER.defaults_path = original
    owned = {key for s in (*stages.ACQUISITION, *stages.ANALYSIS) for key in (s.display_parameters if s.display_parameters is not None else s.parameters)}
    assert owned == {f["name"] for f in p.schema()["fields"]}


@pytest.mark.parametrize("values", [{"PROBE_POWER_LIST": []}, {"PROBE_POWER_LIST": [2]}, {"PROBE_POWER_LIST": [float("nan")]},
    {"PUMP_SWITCH_FREQ": 10}, {"TRIGGER_SOURCE": "C1"}, {"ACQ_REPEATS": True}, {"T1_BOUNDS": [1, .1]},
    {"SCOPE_VOLT_SCALE": 0}, {"SYSTEM_SETTLE_S": -1}, {"X_FIELD_AMPLITUDE": 99}])
def test_invalid_parameters_rejected_before_connection(tmp_path, monkeypatch, values):
    copy_config(tmp_path)
    connect = Mock(side_effect=AssertionError("预检不得连接"))
    monkeypatch.setattr(preparation, "SDSInstrument", connect)
    with pytest.raises((ValueError, TypeError)):
        workflow.Workflow(T1CalibrationParams.from_external(values), project_root=tmp_path)
    connect.assert_not_called()


def test_real_full_subprocess(tmp_path):
    copy_config(tmp_path)
    env = os.environ | {"T1_TEST_ROOT": str(tmp_path), "T1_TEST_FULL": "1", "PYTHONUTF8": "1", "MPLBACKEND": "Agg"}
    result = subprocess.run([sys.executable, "-m", __name__], cwd=ROOT, env=env, text=True, encoding="utf-8", capture_output=True, timeout=60)
    assert result.returncode == 0, result.stdout+result.stderr
    runs = list((tmp_path/"data/T1_Calibration").iterdir())
    assert len(runs) == 1 and (runs[0]/"raw/point_p01.npz").is_file()


if __name__ == "__main__":
    from .session import serve
    from pytest import MonkeyPatch
    with MonkeyPatch.context() as mp:
        root = Path(os.environ["T1_TEST_ROOT"])
        payload = json.loads(os.environ.get("LAB_TYPED_PARAMETERS", "{}"))
        sid = payload.pop("session_id", None)
        params = T1CalibrationParams.from_external(payload) if payload else small_params()
        install_rig(root, mp, params, progress=True)
        if os.environ.get("T1_TEST_FULL") == "1":
            os.environ["LAB_TYPED_PARAMETERS"] = json.dumps(params.to_external())
            workflow.main()
        else: serve(workflow.Workflow(params, project_root=root, session_id=sid), sys.stdin)
