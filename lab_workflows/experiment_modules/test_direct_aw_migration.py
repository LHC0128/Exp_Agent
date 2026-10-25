"""两个 DirectAW 真实业务步骤的假仪器自检；不启动任何用户服务。"""
import importlib
import json
import os
from pathlib import Path
from queue import Queue, Empty
import shutil
import subprocess
import sys
from threading import Thread
import time
from types import SimpleNamespace

import numpy as np
import pytest
import yaml

from ..common import WorkflowCancelled
from ..experiment_runtime import check_cancelled

MODULES = ("rf_sensitivity_direct_aw_frequency", "xy_direct_aw_dc_calibration")
ROOT = Path(__file__).resolve().parents[2]
ACQUISITION_CASES = {
    "rf-sensitivity-direct-aw-frequency": {"mode": "single", "sources": [
        "lab_workflows/experiment_modules/rf_sensitivity_direct_aw_frequency/workflow.py",
        "lab_workflows/experiment_modules/rf_sensitivity_direct_aw_frequency/acquisition.py",
    ], "cases": {
        "order": ["test_real_full_and_stepwise_match"], "slow_disk": ["test_real_full_and_stepwise_match"],
        "cancel": ["test_faults_restore_rescue_and_keep_original", "test_real_session_cancel_eof_and_finish"],
        "acquire_failure": ["test_faults_restore_rescue_and_keep_original"],
        "restore_failure": ["test_faults_restore_rescue_and_keep_original"],
        "save_failure": ["test_faults_restore_rescue_and_keep_original"],
        "partial_save": ["test_faults_restore_rescue_and_keep_original"],
        "entrypoints": ["test_real_full_and_stepwise_match", "test_real_session_cancel_eof_and_finish"],
    }},
    "xy-direct-aw-dc-calibration": {"mode": "single", "sources": [
        "lab_workflows/experiment_modules/xy_direct_aw_dc_calibration/workflow.py",
        "lab_workflows/experiment_modules/xy_direct_aw_dc_calibration/acquisition.py",
    ], "cases": {
        "order": ["test_real_full_and_stepwise_match"], "slow_disk": ["test_real_full_and_stepwise_match"],
        "cancel": ["test_faults_restore_rescue_and_keep_original", "test_real_session_cancel_eof_and_finish"],
        "acquire_failure": ["test_faults_restore_rescue_and_keep_original"],
        "restore_failure": ["test_faults_restore_rescue_and_keep_original"],
        "save_failure": ["test_faults_restore_rescue_and_keep_original"],
        "partial_save": ["test_faults_restore_rescue_and_keep_original"],
        "entrypoints": ["test_real_full_and_stepwise_match", "test_real_session_cancel_eof_and_finish"],
    }},
}


def copy_configuration(root):
    (root / "params").mkdir(parents=True, exist_ok=True)
    for name in ("devices.yaml", "mapping.yaml", "safety_limits.yaml"):
        shutil.copyfile(ROOT / "params" / name, root / "params" / name)
    mapping_path = root / "params/mapping.yaml"
    mapping = yaml.safe_load(mapping_path.read_text(encoding="utf-8"))
    for key in ("lockin_r", "lockin_xy"):
        mapping["mapping"][key]["device_id"] = "hf2_dev18246"
    mapping_path.write_text(yaml.safe_dump(mapping, allow_unicode=True), encoding="utf-8")
    (root / "experiments").mkdir(exist_ok=True)
    t = np.arange(2000) / 1e6
    np.savetxt(root / "experiments/fake_control.csv", np.c_[t, np.full(len(t), 500.0)],
               delimiter=",", header="time_s,Omega_ctrl_Hz", comments="")


def small_params(module):
    if module == "t2_calibration":
        from .t2_calibration.models import T2CalibrationParams
        return T2CalibrationParams(do_power_scan=False, acq_repeats=2, scope_duration=.002, system_settle_s=0)
    if module.startswith("rf_"):
        from .rf_sensitivity_direct_aw_frequency.models import RFDirectAWFrequencyParams
        return RFDirectAWFrequencyParams(arb_waveform_source="csv", arb_waveform_file="fake_control.csv",
            a_env_freq=500, xy_ctrl_k_hz_per_v=10000, xy_ctrl_b_hz=0, z_rf_freq_start=500,
            z_rf_freq_stop=1000, z_rf_freq_offset_hz=0, z_rf_freq_points=2, phase_points=2,
            demod3_n_avg=2, system_settle_s=0, temp_switch_on_lag=0)
    from .xy_direct_aw_dc_calibration.models import XYDirectAWDCCalibrationParams
    return XYDirectAWDCCalibrationParams(xy_env_voltage_start_v=.05, xy_env_voltage_stop_v=.1,
        xy_env_voltage_points=2, z_rf_freq_start_hz=500, z_rf_freq_stop_hz=1000,
        z_rf_freq_step_hz=500, xy_aw_points=1000, demod3_n_avg=2, system_settle_s=0)


def install_fake(module, root, monkeypatch):
    workflow = importlib.import_module(f"lab_workflows.experiment_modules.{module}.workflow")
    prep, acq = workflow.preparation, workflow.acquisition
    state = dict(enabled=True, events=[], fault=None, reads=0, saves=0, recovery_saves=0, original=OSError("原始读取错误"))
    cancel = root / "cancel.flag"
    monkeypatch.setenv("LAB_CANCEL_FILE", str(cancel))
    monkeypatch.setattr(workflow, "find_project_root", lambda: root)
    from .. import common, experiment_params
    monkeypatch.setattr(common, "find_project_root", lambda *args: root)
    monkeypatch.setattr(experiment_params, "find_project_root", lambda *args: root)
    models = importlib.import_module(f"lab_workflows.experiment_modules.{module}.models")
    if hasattr(models, "find_project_root"):
        monkeypatch.setattr(models, "find_project_root", lambda: root)

    class Generator:
        model = "DG4000"
        def __init__(self, key="generic", *args, **kwargs):
            self.key = key
        def connect(self): state["events"].append("connect")
        def disconnect(self): state["events"].append("disconnect")
        def idn(self): return "FAKE"
        def setup_dc(self, value, **kwargs):
            if self.key == "Temp_Switch":
                enabled = float(value) == 5.0
                state["events"].append("on" if enabled else "off")
                if enabled and state["reads"] and state["fault"] in ("restore", "read_restore"):
                    raise OSError("温控恢复错误")
                state["enabled"] = enabled
            else:
                state["events"].append("configure")
        def __getattr__(self, name):
            if name.startswith(("set_", "setup_", "phase_init", "apply_wave")):
                def command(*args, **kwargs):
                    state["events"].append("configure")
                    if name == "setup_pulse" and state["fault"] == "prepare":
                        raise state["original"]
                return command
            raise AttributeError(name)

    class TEC(Generator):
        def set_enable(self, *args):
            raise AssertionError("普通采集不得启停 TEC")

    class Lockin:
        model, acquisition_mode, device_id, clockbase = "HF2", "auto", "dev18246", 210e6
        def __init__(self): self.nodes = {}
        def connect(self): state["events"].append("connect")
        def disconnect(self): state["events"].append("disconnect")
        def idn(self): return "FAKE HF2"
        def sig_in_path(self, i): return f"/dev18246/sigins/{i}"
        def osc_path(self, i): return f"/dev18246/oscs/{i}"
        def demod_path(self, i): return f"/dev18246/demods/{i}"
        def aux_out_path(self, i): return f"/dev18246/auxouts/{i}"
        def set_double(self, path, value): self.nodes[path] = float(value)
        def set_int(self, path, value): self.nodes[path] = int(value)
        def get_double(self, path): return float(self.nodes.get(path, 1.0))
        def get_int(self, path): return int(self.nodes.get(path, 0))
        def sync(self): pass
        def get_sample(self, path):
            if "/demods/3/" in path:
                assert not state["enabled"], "业务采样必须在关闭温控窗口内"
                state["events"].append("read")
                state["reads"] += 1
                if state["fault"] in ("read", "read_restore") and state["reads"] == 2:
                    raise state["original"]
                if state["fault"] == "cancel": cancel.touch()
            return {"x": np.array([.1]), "y": np.array([0.0]), "timestamp": np.array([1])}

    from ..steps import routed_signal_generator as routes
    monkeypatch.setattr(routes, "create_signal_generator", lambda config: Generator(config["mapping_key"]))
    if hasattr(prep, "create_signal_generator"):
        monkeypatch.setattr(prep, "create_signal_generator", lambda config: Generator(config["mapping_key"]))
    monkeypatch.setattr(prep, "GS200Instrument", lambda *args: Generator("GS200"))
    monkeypatch.setattr(prep, "TECInstrument", TEC)
    monkeypatch.setattr(prep, "create_lockin", lambda *args: Lockin())
    monkeypatch.setattr(prep, "initialize_lockin_inputs", lambda device: None)
    class Scope(Generator):
        def get_sampling_rate(self): return 1e6
        def get_memory_depth(self): return "100K"
        def get_actual_points(self): return 2000
        def trigger_stop(self): state["events"].append("trigger_stop")
        def trigger_run(self): assert state["enabled"]
        def wait_for_trigger(self, **kwargs): return True
    class Acquirer:
        def __init__(self, *args): pass
        def apply_config(self, *args): assert state["enabled"]
        def acquire_channel(self, *args, **kwargs):
            assert not state["enabled"]
            state["reads"] += 1; state["events"].append("read")
            t=np.arange(-20,1980)/1e6
            return SimpleNamespace(time=t,voltage=.2*np.exp(-np.maximum(t,0)/.0005)*np.cos(2*np.pi*90000*t))
    if hasattr(prep, "SDSInstrument"):
        monkeypatch.setattr(prep, "SDSInstrument", Scope)
    if hasattr(prep, "SDSAcquisition"):
        monkeypatch.setattr(prep, "SDSAcquisition", Acquirer)
    def clocks(*args):
        state["events"].append("clocks")
        return {}
    monkeypatch.setattr(prep, "synchronize_connected_clocks", clocks)
    def wait(self, seconds):
        state["events"].append("wait")
        check_cancelled()
    if module == "t2_calibration":
        monkeypatch.setattr(workflow, "synchronize_connected_clocks", clocks)
        monkeypatch.setattr(workflow, "_sleep", lambda seconds: wait(None, seconds))
    else:
        monkeypatch.setattr(workflow.Workflow, "wait", wait)
    original_save = np.savez
    def save(path, *args, **kwargs):
        assert state["enabled"] or state["events"][-1] == "on", "必须先尝试恢复温控再写文件"
        state["events"].append("save")
        state["saves"] += 1
        if "recovery" in Path(path).parts:
            state["recovery_saves"] += 1
            if state["fault"] == "save" and state["recovery_saves"] == 2:
                raise OSError("保存错误")
        time.sleep(.002)
        return original_save(path, *args, **kwargs)
    monkeypatch.setattr(np, "savez", save)
    def daq_read(*args, **kwargs):
        assert not state["enabled"]
        state["events"].append("daq_read")
        return [SimpleNamespace(signal_name="sample.y", values=np.arange(32, dtype=float),
                                time=np.arange(32) / 1000, config_snapshot={"actual_rate": 1000.0})]
    if hasattr(acq, "daq"):
        monkeypatch.setattr(acq.daq, "acquire_data", daq_read)
    return SimpleNamespace(workflow=workflow, state=state, cancel=cancel)


@pytest.fixture(params=MODULES)
def rig(request, tmp_path, monkeypatch):
    copy_configuration(tmp_path)
    result = install_fake(request.param, tmp_path, monkeypatch)
    result.params, result.root, result.module = small_params(request.param), tmp_path, request.param
    return result


def core_arrays(directory):
    result = {}
    for path in (directory / "raw").rglob("*.npz"):
        if "recovery" in path.parts or "index" in path.name: continue
        with np.load(path, allow_pickle=False) as data:
            for key in data.files:
                if data[key].dtype != object:
                    result[(path.relative_to(directory).as_posix(), key)] = data[key].copy()
    return result


def test_real_full_and_stepwise_match(rig):
    full = rig.workflow.run(rig.params)
    expected = core_arrays(full)
    assert expected and rig.state["enabled"]
    events = rig.state["events"]
    assert events.index("clocks") < events.index("off")
    for i, event in enumerate(events):
        if event != "read": continue
        assert "off" in events[:i] and events[i:].index("on") < events[i:].index("save")
    ctx = rig.workflow.Workflow(rig.params)
    for stage in ctx.stages:
        ctx.execute([stage.id])
    actual = core_arrays(ctx.directory.root)
    assert expected.keys() == actual.keys()
    for key in actual:
        np.testing.assert_equal(actual[key], expected[key])
    assert ctx.closed and ctx.directory.root != full
    for directory in (full, ctx.directory.root):
        config = yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf-8"))
        assert config["completion_status"] == "completed" and config["safety_shutdown"]["completed"]
        assert config["temperature_control"]["actual_C"] is None
        assert config["temperature_control"]["controlled_by_experiment"] is True
        assert config["temperature_control"]["control_source"]


def test_parameter_impacts_no_automatic_output_and_new_directory(rig):
    ctx = rig.workflow.Workflow(rig.params)
    ctx.execute([s.id for s in ctx.stages if s.id != "A07"])
    first, before = ctx.directory.root, core_arrays(ctx.directory.root)
    n = len(rig.state["events"])
    ctx.update_parameters({"SYSTEM_SETTLE_S": .01})
    assert len(rig.state["events"]) == n, "应用参数不应自动设置输出"
    impacts = importlib.import_module(f"lab_workflows.experiment_modules.{rig.module}.stages").definition()["parameter_impacts"]["acquisition"]["SYSTEM_SETTLE_S"]
    assert [key for key, state in ctx.states.items() if state["status"] == "stale"] == impacts
    with pytest.raises(ValueError, match="缺少前置"):
        ctx.execute(["A06"])
    ctx.execute(["A04", "A05", "A06"])
    assert ctx.directory.root != first
    for key, value in before.items(): np.testing.assert_equal(core_arrays(first)[key], value)
    ctx.finish()


@pytest.mark.parametrize("fault", ["read", "restore", "read_restore", "save", "cancel"])
def test_faults_restore_rescue_and_keep_original(rig, fault):
    rig.state["fault"] = fault
    with pytest.raises((OSError, WorkflowCancelled)) as caught:
        rig.workflow.run(rig.params)
    if fault in ("read", "read_restore"):
        assert caught.value is rig.state["original"]
    recovered = list((rig.root / "data").rglob("recovery/*.npz"))
    assert recovered, "任何后续失败都不能丢弃已经读取的完整样本"
    first_read = rig.state["events"].index("read")
    assert rig.state["events"].index("on", first_read) < rig.state["events"].index("save", first_read)
    with np.load(recovered[0], allow_pickle=False) as data:
        assert any("values_V" in key for key in data.files)
    config = yaml.safe_load(next((rig.root / "data" / rig.workflow.DATA_TYPE).glob("*/experiment_config.yaml")).read_text(encoding="utf-8"))
    assert config["completion_status"] == "failed"


def test_both_mode_preserves_daq_before_later_sample_failure(rig):
    rig.params.acquisition_mode = "both"
    rig.state["fault"] = "read"
    with pytest.raises(OSError):
        rig.workflow.run(rig.params)
    path = next((rig.root / "data").rglob("recovery/*.npz"))
    with np.load(path, allow_pickle=False) as data:
        np.testing.assert_array_equal(data["y_V"], np.arange(32))


def test_preparation_failure_still_shuts_down(rig):
    rig.state["fault"] = "prepare"
    with pytest.raises(OSError) as caught:
        rig.workflow.run(rig.params)
    assert caught.value is rig.state["original"] and rig.state["enabled"]
    config = yaml.safe_load(next((rig.root/"data"/rig.workflow.DATA_TYPE).glob("*/experiment_config.yaml")).read_text(encoding="utf-8"))
    assert config["completion_status"] == "failed" and config["safety_shutdown"]["completed"]


@pytest.mark.parametrize("module", MODULES)
@pytest.mark.parametrize("action", ["finish", "cancel", "eof"])
def test_real_session_cancel_eof_and_finish(tmp_path, module, action):
    copy_configuration(tmp_path)
    params = small_params(module)
    env = os.environ | {"MIGRATION_TEST_MODULE": module, "MIGRATION_TEST_ROOT": str(tmp_path),
        "LAB_TYPED_PARAMETERS": json.dumps(params.to_external() | {"session_id": "fake_session"}),
        "LAB_PROGRESS_PROTOCOL": "jsonl", "LAB_CANCEL_FILE": str(tmp_path/"cancel.flag"), "MPLBACKEND": "Agg"}
    process = subprocess.Popen([sys.executable, "-X", "utf8", "-u", "-m", __name__],
        cwd=ROOT, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8")
    lines = Queue()
    def read():
        for line in process.stdout: lines.put(line)
    Thread(target=read, daemon=True).start()
    transcript=[]
    def until(predicate):
        deadline=time.monotonic()+45
        while time.monotonic()<deadline:
            try: line=lines.get(timeout=.1)
            except Empty:
                if process.poll() is not None: break
                continue
            transcript.append(line)
            if line.startswith("__LAB_PROGRESS__ "):
                data=json.loads(line.removeprefix("__LAB_PROGRESS__ "))["data"]
                if predicate(data): return data
        raise AssertionError("子进程未到达预期状态\n"+"".join(transcript))
    try:
        until(lambda d: "workflow_session" in d)
        process.stdin.write(json.dumps(dict(request_id="prepare", revision=0, action="steps", step_ids=["A01","A02","A03","A04","A05"]))+"\n")
        process.stdin.flush()
        state=until(lambda d: d.get("command_result",{}).get("request_id")=="prepare")["workflow_session"]
        assert state["steps"]["A05"]["status"] == "completed"
        if action=="finish":
            process.stdin.write(json.dumps(dict(request_id="end", revision=state["revision"], action="finish"))+"\n")
            process.stdin.flush()
        elif action=="cancel": (tmp_path/"cancel.flag").touch()
        else: process.stdin.close()
        process.wait(timeout=45)
        saved=json.loads((tmp_path/"data/.gui_jobs/fake_session/session.json").read_text(encoding="utf-8"))
        assert saved["status"] == "closed" and saved["safety_shutdown"]["completed"]
        assert saved["outputs"]["温控"] == "5 V DC + ON"
    finally:
        if process.poll() is None:
            process.stdin.close(); process.wait(timeout=45)
        process.stdout.close()


if __name__ == "__main__":
    module = os.environ["MIGRATION_TEST_MODULE"]
    with pytest.MonkeyPatch.context() as patch:
        install_fake(module, Path(os.environ["MIGRATION_TEST_ROOT"]), patch)
        importlib.import_module(f"lab_workflows.experiment_modules.{module}.session").main()
