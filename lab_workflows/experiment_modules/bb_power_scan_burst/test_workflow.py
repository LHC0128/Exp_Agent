"""真实实验步骤配假仪器与真实写盘，覆盖温控窗口、输出保留和完整子进程。"""
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
from ...common import load_mapping, WorkflowCancelled
from ...steps import routed_signal_generator
from . import workflow, preparation, acquisition, stages
from .models import BBPowerScanBurstParams

ROOT = Path(__file__).resolve().parents[3]
ACQUISITION_CASES = {"bb-power-scan-burst": {"mode": "single", "cases": {
    "order": ["test_six_step_slow_disk_average_and_preserved_outputs"],
    "slow_disk": ["test_six_step_slow_disk_average_and_preserved_outputs"],
    "cancel": ["test_cancel_after_read_rescues_and_keeps_gate"],
    "acquire_failure": ["test_later_acquire_failure_keeps_completed_points"],
    "restore_failure": ["test_restore_failure_and_primary_error"],
    "save_failure": ["test_save_failure_keeps_previous_and_rescues"],
    "partial_save": ["test_cancel_after_read_rescues_and_keeps_gate", "test_later_acquire_failure_keeps_completed_points"],
    "entrypoints": ["test_full_stepwise_rerun_parameters_and_snapshot", "test_real_full_subprocess"]},
    "sources": ["lab_workflows/experiment_modules/bb_power_scan_burst/workflow.py",
        "lab_workflows/experiment_modules/bb_power_scan_burst/preparation.py",
        "lab_workflows/experiment_modules/bb_power_scan_burst/excitation.py",
        "lab_workflows/experiment_modules/bb_power_scan_burst/acquisition.py",
        "lab_workflows/experiment_modules/bb_power_scan_burst/session.py",
        "lab_workflows/experiment_modules/bb_power_scan_burst/stages.py", "experiments/BB_Rpump_PowerScan_Burst.py"]}}
REGRESSION_CASES = {"bb-power-scan-burst": {"parameters": {
    "sources": ["lab_workflows/experiment_modules/bb_power_scan_burst/models.py", "params/experiments/bb-power-scan-burst.yaml"],
    "tests": ["test_parameters_defaults_migration_and_yaml", "test_invalid_parameters_before_connect"]}}}


def copy_config(root):
    (root/"params").mkdir(parents=True, exist_ok=True)
    for name in ("mapping.yaml", "devices.yaml", "safety_limits.yaml"):
        shutil.copyfile(ROOT/"params"/name, root/"params"/name)


def small_params():
    return replace(BBPowerScanBurstParams(), pump_power=[0.08, 0.12], acq_repeats=2, sds_sample_rate=500000,
                   system_settle_s=0.001, temp_switch_on_delay_s=0, burst_burst_period_s=0.05)


def signal():
    t = np.arange(-.005, .035, 2e-6)
    end = 1000/90000
    amp = np.where(t < 0, .0002, np.where(t <= end, .03*(1-np.exp(-np.maximum(0,t)/.0007)),
        .03*(1-np.exp(-end/.0007))*np.exp(-np.maximum(0,t-end)/.002)))
    y = .002+amp*np.sin(2*np.pi*90000*t+.4)+np.random.default_rng(42).normal(0,1e-5,t.size)
    return t, y


class FakeDG:
    def __init__(self, rig, resource):
        self.rig, self.resource = rig, resource
        self.clock, self.connections, self.disconnects = "INT", 0, 0
        self.output, self.dc, self.burst, self.sync, self.mod = {}, {}, {}, {}, {}
        self.settings = {}

    def connect(self): self.connections += 1
    def disconnect(self): self.disconnects += 1
    def get_ref_clock_source(self): return "INT" if self.rig.clock_error else self.clock
    def set_ref_clock_source(self, value): self.clock = value
    def set_burst_state(self, value, *, channel): self.burst[channel] = value
    def set_sync_state(self, value, *, channel): self.sync[channel] = value
    def set_mod_state(self, value, *, channel): self.mod[channel] = value
    def get_output(self, *, channel): return self.output.get(channel, False)
    def get_burst_state(self, *, channel): return self.burst.get(channel, False)
    def get_burst_mode(self, *, channel): return self.settings[("set_burst_mode",channel)]
    def get_burst_trigger_source(self, *, channel): return self.settings[("set_burst_trigger_source",channel)]
    def get_burst_ncycles(self, *, channel): return self.settings[("set_burst_ncycles",channel)]
    def get_burst_period(self, *, channel): return self.settings[("set_burst_period",channel)]
    def set_output(self, value, *, channel): self.output[channel] = bool(value)

    def setup_dc(self, value, *, channel):
        rig, temp, pump = self.rig, self.rig.mapping["Temp_Switch"], self.rig.mapping["Pump_laser_power"]
        if self.resource == temp["resource"] and channel == temp["channel"]:
            rig.events.append("on" if value else "off")
            if value == 5 and rig.restore_error:
                rig.restore_attempted = True
                raise OSError("温控恢复失败")
            rig.enabled = bool(value)
        elif self.resource == pump["resource"] and channel == pump["channel"]:
            assert rig.enabled
            rig.events.append("configure")
        self.dc[channel], self.output[channel] = value, True

    def apply_wave(self, *args, channel, **kwargs):
        assert self.rig.enabled
        self.settings[channel] = (args, kwargs)

    def __getattr__(self, name):
        if name in {"set_pulse_dcycle", "set_high_level", "set_low_level", "set_burst_mode", "set_burst_ncycles",
                    "set_burst_period", "set_burst_trigger_source", "set_sync_polarity"}:
            return lambda value, *, channel: self.settings.__setitem__((name,channel), value)
        raise AttributeError(name)


class FakeScope:
    def __init__(self, rig):
        self.rig, self.scales, self.settings = rig, {}, {}
        self.connections = self.disconnects = 0

    def connect(self): self.connections += 1
    def disconnect(self): self.disconnects += 1
    def get_actual_points(self): return len(signal()[0])
    def get_sampling_rate(self): return 500000
    def query(self, command): return .004
    def get_trigger_mode(self): return self.settings["set_trigger_mode"][0]
    def get_trigger_source(self): return self.settings["set_trigger_source"][0]
    def trigger_status(self): return "Trig'd"
    def trigger_stop(self): self.rig.events.append("freeze")
    def trigger_run(self):
        assert self.rig.enabled
        self.rig.events.append("prepare")
    def set_channel_scale(self, channel, value):
        assert self.rig.enabled
        self.scales[channel] = value
    def __getattr__(self, name):
        if name.startswith("set_"):
            def setter(*args):
                assert self.rig.enabled
                self.settings[name] = args
            return setter
        raise AttributeError(name)


class FakeAcquirer(preparation.SDSAcquisition):
    def acquire_channel(self, channel, *args, **kwargs):
        rig = self._inst.rig
        assert not rig.enabled
        rig.events.append("acquire"); rig.reads += 1
        if rig.reads == rig.fail_at:
            raise rig.acquire_error
        t, y = signal()
        if rig.cancel_after_read:
            rig.cancel = True
        return SimpleNamespace(time=t, voltage=y)


def install_rig(root, monkeypatch, params=None, *, progress=False):
    rig = SimpleNamespace(root=root, params=params or small_params(), mapping=load_mapping(root), events=[], enabled=True,
        cancel=False, cancel_after_read=False, restore_error=False, restore_attempted=False, clock_error=False,
        reads=0, fail_at=-1, acquire_error=RuntimeError("模拟采集失败"), generators={})
    for key in (*preparation.SIGNALS, *preparation.safe_signals(rig.mapping)):
        resource = rig.mapping[key]["resource"]
        rig.generators.setdefault(resource, FakeDG(rig, resource))
    rig.gs, rig.tec, rig.scope = Mock(), Mock(), FakeScope(rig)
    monkeypatch.setattr(preparation, "GS200Instrument", lambda *a: rig.gs)
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
    ctx.execute([f"A{i:02d}" for i in range(1,7)])
    rig.events.clear()
    return ctx


def assert_preserved(rig):
    gate, pump = rig.mapping["Time_sequence"], rig.mapping["Pump_laser_power"]
    dg = rig.generators[gate["resource"]]
    assert dg.output[gate["channel"]] and dg.burst[gate["channel"]] and dg.sync[gate["channel"]]
    assert rig.generators[pump["resource"]].dc[pump["channel"]] >= rig.params.pump_power[0]
    for key in preparation.safe_signals(rig.mapping):
        cfg = rig.mapping[key]
        assert not rig.generators[cfg["resource"]].output[cfg["channel"]]


def test_six_step_slow_disk_average_and_preserved_outputs(rig, monkeypatch):
    ctx = prepared(rig)
    opened, savez = Path.open, np.savez
    def open_file(path, mode="r", *args, **kwargs):
        if any(c in mode for c in "wax+"):
            assert rig.enabled, f"关闭窗口禁止写盘 {path}"
        return opened(path, mode, *args, **kwargs)
    def save(*args, **kwargs):
        assert rig.enabled
        rig.events.append("save"); time.sleep(.002)
        return savez(*args, **kwargs)
    monkeypatch.setattr(Path,"open",open_file); monkeypatch.setattr(np,"savez",save)
    ctx.execute(["A07"]); ctx.finish()
    for i, event in enumerate(rig.events):
        if event == "off":
            assert rig.events[i-1] == "recovery" and "configure" in rig.events[:i]
            assert rig.events[i+1] == "settle"
        if event == "acquire":
            assert rig.events[i+1] == "on"
    assert rig.events.count("off") == rig.reads
    assert len(list(ctx.directory.raw.glob("shot_p*_avg.npz"))) == 2
    with np.load(ctx.directory.raw/"shot_p00_avg.npz") as avg:
        np.testing.assert_array_equal(avg["signal"],signal()[1]); assert int(avg["acq_repeats"]) == 2
    with np.load(ctx.directory.raw/"power_scan_summary.npz") as summary:
        assert int(summary["n_shots"]) == 2
    assert_preserved(rig)
    assert rig.enabled and all(g.connections == 1 for g in rig.generators.values())
    rig.tec.set_enable.assert_not_called(); rig.tec.get_temperature.assert_not_called()
    assert ctx.state["temperature_control"]["actual_temperature_c"] is None


def test_cancel_after_read_rescues_and_keeps_gate(rig):
    ctx = prepared(rig); rig.cancel_after_read = True
    with pytest.raises(WorkflowCancelled) as error: ctx.execute(["A07"])
    ctx.finish(error.value)
    assert rig.reads == 1 and rig.enabled and list(ctx.directory.raw.glob("probe_*.npz"))
    assert ctx.state["completion_status"] == "cancelled"
    assert_preserved(rig)


def test_later_acquire_failure_keeps_completed_points(rig):
    ctx = prepared(rig); rig.fail_at = 5
    with pytest.raises(RuntimeError,match="模拟采集失败") as error: ctx.execute(["A07"])
    ctx.finish(error.value)
    assert rig.enabled
    assert list(ctx.directory.raw.glob("shot_p00_avg.npz")) and not list(ctx.directory.raw.glob("shot_p01_avg.npz"))
    with np.load(ctx.directory.raw/"power_scan_summary.npz") as summary: assert summary["n_shots"] == 1
    assert_preserved(rig)


@pytest.mark.parametrize("primary",[False,True])
def test_restore_failure_and_primary_error(rig, primary):
    ctx = prepared(rig); rig.restore_error = True
    if primary:
        rig.fail_at, rig.acquire_error = 1, WorkflowCancelled("最初取消")
    with pytest.raises(WorkflowCancelled if primary else OSError) as error: ctx.execute(["A07"])
    ctx.finish(error.value)
    assert rig.restore_attempted and rig.reads == 1
    assert ctx.state["safety_shutdown"]["completed"] is False
    if primary: assert error.value is rig.acquire_error
    else:
        with np.load(next(ctx.directory.raw.glob("probe_*.npz"))) as saved:
            np.testing.assert_array_equal(saved["signal"], signal()[1])
    assert_preserved(rig)


def test_save_failure_keeps_previous_and_rescues(rig, monkeypatch):
    ctx = prepared(rig)
    path = ctx.directory.raw/"existing.npz"; acquisition.save_npz(path,signal=[1]); original = path.read_bytes()
    replace_file = Path.replace
    def replace_failing(source, target):
        if Path(target) == path or Path(target).name == "probe_p00_0.npz": raise OSError("模拟磁盘失败")
        return replace_file(source,target)
    monkeypatch.setattr(Path,"replace",replace_failing)
    with pytest.raises(OSError): acquisition.save_npz(path,signal=[2])
    assert path.read_bytes() == original
    with pytest.raises(OSError) as error: ctx.execute(["A07"])
    ctx.finish(error.value)
    assert rig.enabled
    with np.load(ctx.directory.raw/"recovery_probe_p00_0.npz") as rescued:
        np.testing.assert_array_equal(rescued["signal"],signal()[1])
    assert_preserved(rig)


def test_full_stepwise_rerun_parameters_and_snapshot(rig):
    ctx = workflow.Workflow(rig.params,project_root=rig.root)
    with pytest.raises(ValueError,match="缺少前置"): ctx.execute(["A07"])
    assert not ctx.devices
    ctx.execute([f"A{i:02d}" for i in range(1,8)])
    first = ctx.directory.root; original = (first/"experiment_config.yaml").read_bytes()
    with pytest.raises(ValueError): ctx.execute(["A07"])
    ctx.execute(["A06","A07"])
    assert ctx.directory.root != first and (first/"experiment_config.yaml").read_bytes() == original
    ctx.execute(["A01"])
    assert ctx.states["A05"]["status"] == "completed"
    ctx.update_parameters({"POST_DARK_TIME_S":.019,"RUN_TAG":"next"})
    impacts = set(stages.definition()["parameter_impacts"]["acquisition"]["POST_DARK_TIME_S"])
    assert all(ctx.states[i]["status"] == "stale" for i in impacts)
    # 应用而不重跑不能用下一轮 Pump 列表覆盖已经写入的末点电压。
    pump=rig.mapping["Pump_laser_power"]; previous=rig.generators[pump["resource"]].dc[pump["channel"]]
    ctx.update_parameters({"PUMP_POWER":[.02,.04]})
    ctx.finish()
    assert rig.generators[pump["resource"]].dc[pump["channel"]] == previous
    assert_preserved(rig)


def test_clock_failure_connection_cleanup_and_finish_error(rig):
    ctx=workflow.Workflow(rig.params,project_root=rig.root); ctx.execute(["A01","A02"])
    rig.clock_error=True
    with pytest.raises(RuntimeError) as failure: ctx.execute(["A03"])
    ctx.finish(failure.value)
    assert not rig.gs.set_current.called and all(g.disconnects == 1 for g in rig.generators.values())
    assert ctx.closed


def test_mode_drift_refuses_without_reconfiguring_outputs(rig):
    ctx=prepared(rig)
    gate=rig.mapping["Time_sequence"]
    dg=rig.generators[gate["resource"]]
    dg.burst[gate["channel"]]=False
    with pytest.raises(ValueError,match="Burst 模式已改变"): ctx.execute(["A07"])
    assert rig.reads==0 and dg.burst[gate["channel"]] is False
    ctx.finish()


def test_optional_tec_connection_failure_skips_without_waiting(rig):
    rig.tec.connect.side_effect=OSError("串口占用")
    ctx=prepared(rig)
    assert ctx.devices["tec"] is None
    assert ctx.state["temperature_control"]["control_source"]=="external_software"
    assert ctx.state["temperature_control"]["actual_temperature_c"] is None
    ctx.finish()


def test_parameters_defaults_migration_and_yaml(tmp_path):
    from .definition import ADAPTER
    p=BBPowerScanBurstParams.from_yaml(ROOT/"params/experiments/bb-power-scan-burst.yaml")
    preparation.preflight(p,ROOT)
    p.save_yaml(tmp_path/"saved.yaml")
    assert BBPowerScanBurstParams.from_yaml(tmp_path/"saved.yaml").to_external() == p.to_external()
    assert BBPowerScanBurstParams.from_external({"TEMP_SWITCH_OFF_DELAY_S":.8}).system_settle_s == .8
    assert BBPowerScanBurstParams.from_external({"TEMP_SWITCH_OFF_DELAY_S":.8,"SYSTEM_SETTLE_S":.3}).system_settle_s == .3
    assert ADAPTER._resolved({"TEMP_SWITCH_OFF_DELAY_S":.8}).system_settle_s == .8
    original=ADAPTER.defaults_path
    try:
        ADAPTER.defaults_path=tmp_path/"unsafe.yaml"
        replace(p,pump_power=[99]).save_yaml(ADAPTER.defaults_path)
        assert ADAPTER.preflight({})
    finally: ADAPTER.defaults_path=original


@pytest.mark.parametrize("values",[{"PUMP_POWER":[]},{"PUMP_POWER":[2]},{"PUMP_POWER":[float("nan")]},
    {"SDS_PD_CHANNEL":4},{"FIXED_DUTY_CYCLE":60},{"TEMP_SWITCH_ON_V":4},{"BURST_BURST_PERIOD_S":.001},
    {"AOM_CARRIER_OFFSET_V":1},{"ACQ_REPEATS":True},{"SYSTEM_SETTLE_S":-1}])
def test_invalid_parameters_before_connect(tmp_path, monkeypatch, values):
    copy_config(tmp_path)
    connect=Mock(side_effect=AssertionError("预检不得连接仪器"))
    monkeypatch.setattr(preparation,"GS200Instrument",connect)
    with pytest.raises((ValueError,TypeError)):
        workflow.Workflow(BBPowerScanBurstParams.from_external(values),project_root=tmp_path)
    connect.assert_not_called()


def test_real_full_subprocess(tmp_path):
    copy_config(tmp_path)
    env=os.environ | {"BB_POWER_TEST_ROOT":str(tmp_path),"BB_POWER_TEST_FULL":"1", "PYTHONUTF8":"1", "MPLBACKEND":"Agg"}
    result=subprocess.run([sys.executable,"-m",__name__],cwd=ROOT,env=env,text=True,encoding="utf-8",capture_output=True,timeout=60)
    assert result.returncode == 0,result.stdout+result.stderr
    runs=list((tmp_path/"data/bb_rpump_power_scan_burst").iterdir())
    assert len(runs) == 1 and (runs[0]/"raw/shot_p01_avg.npz").is_file()


if __name__ == "__main__":
    from .session import serve
    from pytest import MonkeyPatch
    with MonkeyPatch.context() as mp:
        root=Path(os.environ["BB_POWER_TEST_ROOT"])
        params=small_params()
        payload=json.loads(os.environ.get("LAB_TYPED_PARAMETERS","{}"))
        session_id=payload.pop("session_id",None)
        if payload: params=BBPowerScanBurstParams.from_external(payload)
        install_rig(root,mp,params,progress=True)
        if os.environ.get("BB_POWER_TEST_FULL") == "1":
            os.environ["LAB_TYPED_PARAMETERS"]=json.dumps(params.to_external())
            workflow.main()
        else: serve(workflow.Workflow(params,project_root=root,session_id=session_id),sys.stdin)
