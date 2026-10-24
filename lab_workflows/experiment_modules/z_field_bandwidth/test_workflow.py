"""生产业务配假仪器与真实文件：六步时序、模式、故障及入口回归。"""
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
from lockin_amplifier.hf2 import HF2Instrument
from ...common import load_mapping, WorkflowCancelled
from ...steps import routed_signal_generator
from .models import ZFieldBandwidthParams
from . import acquisition, excitation, preparation, workflow, stages

ROOT = Path(__file__).resolve().parents[3]
ACQUISITION_CASES = {"z-field-bandwidth": {"mode": "single", "cases": {
    "order": ["test_six_step_order_slow_disk_baseline_and_shutdown"],
    "slow_disk": ["test_six_step_order_slow_disk_baseline_and_shutdown"],
    "cancel": ["test_cancel_after_y_saves_point_before_stopping"],
    "acquire_failure": ["test_rf_failure_saves_y_and_partial_samples", "test_nonfinite_readings_are_rescued_not_silently_lost"],
    "restore_failure": ["test_restore_failure_rescues_and_preserves_primary"],
    "save_failure": ["test_atomic_replace_failure_preserves_old_and_rescues"],
    "partial_save": ["test_rf_failure_saves_y_and_partial_samples", "test_nonfinite_readings_are_rescued_not_silently_lost"],
    "entrypoints": ["test_real_full_subprocess"]},
    "sources": ["lab_workflows/experiment_modules/z_field_bandwidth/workflow.py",
        "lab_workflows/experiment_modules/z_field_bandwidth/preparation.py",
        "lab_workflows/experiment_modules/z_field_bandwidth/excitation.py",
        "lab_workflows/experiment_modules/z_field_bandwidth/acquisition.py",
        "lab_workflows/experiment_modules/z_field_bandwidth/stages.py",
        "experiments/Z_Field_Bandwidth_Measurement.py"]}}
REGRESSION_CASES = {"z-field-bandwidth": {"parameters": {
    "sources": ["lab_workflows/experiment_modules/z_field_bandwidth/models.py",
        "params/experiments/z-field-bandwidth.yaml"],
    "tests": ["test_parameter_compatibility_yaml_and_defaults", "test_invalid_parameters_before_connection"]}}}


def small_params():
    return replace(ZFieldBandwidthParams(), z_freq_stop=200, z_freq_points=3,
        hf2_demod0_rate=1024, point_duration_s=0.25, demod_r_n_avg=3,
        demod_r_avg_interval=0, system_settle_s=0, temp_switch_on_lag=0)


def copy_config(root):
    shutil.copytree(ROOT / "params", root / "params")
    path = root / "params/mapping.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    for key in ("lockin_r", "lockin_xy"):
        config["mapping"][key]["device_id"] = "hf2_dev18246"
    path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")


class FakeDG:
    def __init__(self, rig, resource):
        self.rig, self.resource = rig, resource
        self.clock, self.output, self.dc, self.freq, self.phase = "INT", {}, {}, {}, {}
        self.connections, self.disconnects = 0, 0
        self.burst, self.mod = {}, {}

    def connect(self): self.connections += 1
    def disconnect(self): self.disconnects += 1
    def idn(self): return "Fake DG"
    def get_ref_clock_source(self): return self.clock
    def set_ref_clock_source(self, value): self.clock = value
    def set_burst_state(self, value, *, channel): self.burst[channel] = value
    def set_mod_state(self, value, *, channel): self.mod[channel] = value
    def set_mod_type(self, *args, **kwargs): pass
    def set_mod_am_source(self, *args, **kwargs): pass
    def set_mod_am_depth(self, *args, **kwargs): pass
    def set_pulse_dcycle(self, *args, **kwargs): pass
    def set_sync_state(self, *args, **kwargs): pass
    def set_burst_mode(self, value, *, channel): raise AssertionError("Z 连续正弦不能配置 Burst 模式")
    def set_burst_trigger_source(self, value, *, channel): raise AssertionError("Z 连续正弦不能配置外触发")
    def set_phase_adjust(self, value, *, channel): self.phase[channel] = value

    def set_output(self, value, *, channel):
        temp = self.rig.mapping["Temp_Switch"]
        if self.resource == temp["resource"] and channel == temp["channel"]:
            assert value, "温控必须用 DC 电平切换，不允许 Output OFF"
        self.output[channel] = bool(value)

    def setup_dc(self, value, *, channel):
        rig, temp, z = self.rig, self.rig.mapping["Temp_Switch"], self.rig.mapping["Z_magnetic_field"]
        if self.resource == temp["resource"] and channel == temp["channel"]:
            rig.events.append("on" if value == 5 else "off")
            if value == 5 and rig.restore_error:
                rig.restore_attempted = True
                raise OSError("温控恢复失败")
            rig.enabled = value == 5
        if self.resource == z["resource"] and channel == z["channel"]:
            rig.events.append("configure")
            rig.frequency = 0
        self.dc[channel], self.output[channel] = value, True

    def setup_sine(self, freq, amplitude, *, channel, phase=0, offset=0):
        self.freq[channel], self.phase[channel], self.output[channel] = freq, phase, True
        z = self.rig.mapping["Z_magnetic_field"]
        if self.resource == z["resource"] and channel == z["channel"]:
            self.rig.frequency = freq
            self.rig.events.append("configure")

    def set_shape(self, value, *, channel): assert value == "DC"
    def set_dc_voltage(self, value, *, channel):
        self.dc[channel] = value
        z = self.rig.mapping["Z_magnetic_field"]
        if self.resource == z["resource"] and channel == z["channel"]:
            self.rig.events.append("configure"); self.rig.frequency = 0

    def setup_pulse(self, freq, amplitude, *, channel, **kwargs): self.output[channel] = True
    def setup_square(self, freq, amplitude, *, channel, **kwargs): self.output[channel] = True


class FakeLockin:
    model, device_id, acquisition_mode = "HF2", "dev18246", "auto"
    capabilities, clockbase = HF2Instrument._capabilities(), 210000000
    _acquisition_stream_settings = None

    def __init__(self, rig): self.rig, self.nodes, self.extclk = rig, {}, False
    def connect(self): pass
    def disconnect(self): self.rig.events.append("lockin-disconnect")
    def get_extclk(self): return self.extclk and not self.rig.clock_error
    def set_extclk(self, value): self.extclk = bool(value)
    def sig_in_path(self, index): return f"/{self.device_id}/sigins/{index}"
    def demod_path(self, index): return f"/{self.device_id}/demods/{index}"
    def osc_path(self, index): return f"/{self.device_id}/oscs/{index}"
    def aux_out_path(self, index): return f"/{self.device_id}/auxouts/{index}"
    def set_int(self, path, value): self.nodes[path] = value
    def set_double(self, path, value): self.nodes[path] = value
    def get_int(self, path): return int(self.nodes.get(path, 0))
    def get_double(self, path): return float(self.nodes.get(path, 0))
    def sync(self): pass

    def get_sample(self, path):
        if "/demods/3/" in path:
            self.rig.r_reads += 1
            self.rig.events.append("r")
            if self.rig.r_error_at == self.rig.r_reads:
                raise RuntimeError("RF 后半段失败")
        elif self.rig.phase_error or (self.rig.root / "phase_quality_failure").exists():
            return {"x": [0], "y": [1]}
        return {"x": [0.001], "y": [0], "timestamp": [210000000]}


def install_rig(root, monkeypatch, params=None, *, progress=False):
    rig = SimpleNamespace(root=root, params=params or small_params(), mapping=load_mapping(root), events=[],
        enabled=True, restore_attempted=False, restore_error=False, frequency=0, y_reads=0, r_reads=0,
        r_error_at=-1, clock_error=False, phase_error=False, cancel=False, cancel_after_y=False, generators={})
    for key in preparation.SIGNALS:
        resource = rig.mapping[key]["resource"]
        rig.generators.setdefault(resource, FakeDG(rig, resource))
    rig.gs, rig.tec, rig.hfi = Mock(), Mock(), FakeLockin(rig)
    monkeypatch.setattr(preparation, "GS200Instrument", lambda *a: rig.gs)
    monkeypatch.setattr(preparation, "TECInstrument", lambda **kw: rig.tec)
    monkeypatch.setattr(preparation, "create_lockin", lambda cfg, **kw: rig.hfi)
    monkeypatch.setattr(routed_signal_generator, "create_signal_generator", lambda cfg: rig.generators[cfg["resource"]])
    original_check = workflow.check_cancelled
    def check():
        original_check()
        if rig.cancel: raise WorkflowCancelled("模拟取消")
    def wait(seconds):
        check()
        rig.events.append("settle" if not rig.enabled else "recovery")
        if rig.cancel_after_y and rig.y_reads:
            rig.cancel = True
        check()
    monkeypatch.setattr(workflow, "check_cancelled", check)
    monkeypatch.setattr(acquisition, "check_cancelled", check)
    monkeypatch.setattr(acquisition, "wait", wait)
    monkeypatch.setattr(excitation, "wait", wait)
    def acquire(hfi, config, **kwargs):
        assert not rig.enabled
        kwargs["cancellation_check"]()
        rig.events.append("y"); rig.y_reads += 1
        for key in ("X_magnetic_field", "Y_magnetic_field"):
            cfg = rig.mapping[key]
            assert not rig.generators[cfg["resource"]].output[cfg["channel"]], "本实验 XY 必须保持 OFF"
        z = rig.mapping["Z_magnetic_field"]
        assert not rig.generators[z["resource"]].burst.get(z["channel"], False), "Z 扫频应为连续正弦"
        rate = kwargs["actual_rate"]
        t = np.arange(config.grid_cols)/rate
        values = 0.01*np.sin(2*np.pi*rig.frequency*t)+np.random.default_rng(rig.y_reads).normal(0,1e-6,len(t))
        snapshot = dict(acquisition_module="daq", requested_rate=kwargs["requested_rate"], actual_rate=rate,
            demod_stream_rate=rate, actual_duration=len(t)/rate, time_source="device")
        if rig.cancel_after_y:
            rig.cancel = True
        return [SimpleNamespace(signal_name="sample.y", values=values, time=t, config_snapshot=snapshot)]
    monkeypatch.setattr(acquisition.daq, "acquire_data", acquire)
    monkeypatch.setattr(workflow, "find_project_root", lambda: root)
    if not progress:
        for module in (acquisition, workflow):
            monkeypatch.setattr(module, "report_runtime_progress", lambda *a, **kw: None)
    return rig


@pytest.fixture
def rig(tmp_path, monkeypatch):
    copy_config(tmp_path)
    return install_rig(tmp_path, monkeypatch)


def through(last):
    ids = [s.id for s in stages.ACQUISITION if s.id != stages.FINISH_ID]
    return ids[:ids.index(last)+1]


def prepared(rig):
    ctx = workflow.Workflow(rig.params, project_root=rig.root)
    ctx.execute(through("A08"))
    rig.events.clear()
    return ctx


def test_six_step_order_slow_disk_baseline_and_shutdown(rig, monkeypatch):
    ctx = prepared(rig)
    original, savez = Path.open, np.savez
    def opened(path, mode="r", *args, **kwargs):
        if any(c in mode for c in "wax+"):
            assert rig.enabled, f"关闭窗口禁止写盘 {path}"
        return original(path, mode, *args, **kwargs)
    def save(*args, **kwargs):
        assert rig.enabled
        rig.events.append("save"); time.sleep(0.002)
        return savez(*args, **kwargs)
    monkeypatch.setattr(Path, "open", opened); monkeypatch.setattr(np, "savez", save)
    ctx.execute(["A09"])
    assert rig.events[:7] == ["configure", "off", "settle", "y", "on", "recovery", "save"]
    assert rig.y_reads == 3 and rig.r_reads == 6
    assert len(list(ctx.directory.raw.glob("freq_*.npz"))) == 3
    assert ctx.state["completion_status"] == "completed" and ctx.state["safety_shutdown"]["completed"]
    assert all(g.connections == 1 for g in rig.generators.values())
    for key in ("X_magnetic_field", "Y_magnetic_field", "Z_magnetic_field"):
        cfg = rig.mapping[key]
        assert not rig.generators[cfg["resource"]].output[cfg["channel"]]
    for key in ("Pump_laser_power", "Probe_laser_power", "Pump_modulation", "Time_sequence", "Time_sequence_2", "Temp_Switch"):
        cfg = rig.mapping[key]
        assert rig.generators[cfg["resource"]].output[cfg["channel"]]
    ctx.finish()
    rig.tec.set_enable.assert_not_called(); rig.tec.get_temperature.assert_not_called()
    assert ctx.state["temperature_control"]["actual_temperature_c"] is None


def test_cancel_after_y_saves_point_before_stopping(rig):
    rig.params = replace(rig.params, z_freq_start=100)
    ctx = prepared(rig)
    rig.cancel_after_y = True
    with pytest.raises(WorkflowCancelled, match="模拟取消") as failure:
        ctx.execute(["A09"])
    ctx.finish(failure.value)
    assert rig.y_reads == 1 and rig.enabled
    assert len(list(ctx.directory.raw.glob("freq_*.npz"))) == 1
    assert ctx.state["completion_status"] == "cancelled"


def test_rf_failure_saves_y_and_partial_samples(rig):
    ctx = prepared(rig); rig.r_error_at = 2
    with pytest.raises(RuntimeError, match="RF 后半段失败") as failure:
        ctx.execute(["A09"])
    ctx.finish(failure.value)
    with np.load(ctx.directory.raw / "freq_0001_100.000Hz.npz") as point:
        assert point["y_V"].shape == (256,) and len(point["demod_r_x_values_V"]) == 1
        assert not bool(point["acquisition_complete"])
    assert rig.y_reads == 2 and rig.enabled
    assert len(json.loads((ctx.directory.raw / "frequency_index.json").read_text(encoding="utf-8"))) == 2
    config = yaml.safe_load(ctx.directory.config_path.read_text(encoding="utf-8"))
    assert config["freq_sweep"]["actual_freq_list_Hz"] == [0.0, 100.0]


@pytest.mark.parametrize("primary", [False, True])
def test_restore_failure_rescues_and_preserves_primary(rig, primary):
    if primary:
        # 正频率在 Y 已读、RF 读取前出现原始取消，再叠加温控恢复失败。
        rig.params = replace(rig.params, z_freq_start=100)
    ctx = prepared(rig); rig.restore_error = True
    if primary:
        rig.cancel_after_y = True
    with pytest.raises(WorkflowCancelled if primary else OSError) as failure:
        ctx.execute(["A09"])
    ctx.finish(failure.value)
    assert rig.restore_attempted and rig.y_reads == 1
    assert len(list(ctx.directory.raw.glob("freq_*.npz"))) == 1
    assert not ctx.state["safety_shutdown"]["completed"]


def test_atomic_replace_failure_preserves_old_and_rescues(rig, monkeypatch):
    ctx = prepared(rig)
    target = ctx.directory.raw / "freq_0000_0Hz_BASELINE.npz"
    acquisition.save_npz(target, old=np.array([42]))
    old, original = target.read_bytes(), Path.replace
    def replace_file(source, dest):
        if Path(dest) == target: raise OSError("原子替换失败")
        return original(source, dest)
    monkeypatch.setattr(Path, "replace", replace_file)
    with pytest.raises(OSError, match="原子替换失败") as failure:
        ctx.execute(["A09"])
    ctx.finish(failure.value)
    assert target.read_bytes() == old and rig.y_reads == 1 and rig.enabled
    with np.load(next(ctx.directory.raw.glob("freq_0000_0Hz_BASELINE_rescue_*.npz"))) as record:
        assert len(record["y_V"]) == 256


def test_dependencies_impacts_and_no_connection_preflight(rig):
    ctx = workflow.Workflow(rig.params, project_root=rig.root)
    with pytest.raises(ValueError, match="缺少前置"):
        ctx.execute(["A09"])
    assert not ctx.devices
    flow = stages.definition()
    assert set(rig.params.to_external(include_hidden=False)) == {key for s in flow["acquisition"]+flow["analysis"] for key in s["display_parameters"]}
    for values in ({"SYSTEM_SETTLE_S":0.1}, {"Z_FREQ_STOP":300, "DEMOD_R_N_AVG":4}, {"MAX_PICK_FREQ_ERROR_HZ":10}):
        ctx.states = {s.id:dict(status="completed",error="") for s in stages.ACQUISITION}
        ctx.update_parameters(values)
        affected = {sid for key in values for sid in flow["parameter_impacts"]["acquisition"].get(key, [])}
        assert {key for key, v in ctx.states.items() if v["status"] == "stale"} == affected
        before = ctx.snapshot()["steps"]
        ctx.recheck(); assert ctx.snapshot()["steps"] == before
    assert not rig.events


def test_repeated_scan_new_directory_restore_requires_prepare_and_frozen_snapshot(rig):
    ctx = prepared(rig); ctx.execute(["A09"])
    first = ctx.directory.root
    before = {p:p.read_bytes() for p in first.rglob("*") if p.is_file()}
    with pytest.raises(ValueError, match="缺少前置"):
        ctx.execute(["A09"])
    ctx.execute(["A06","A07","A08","A09"])
    assert ctx.directory.root != first
    assert {p:p.read_bytes() for p in before} == before
    ctx.update_parameters({"FIXED_PARAMS.Pump_laser_power":0.4, "SYSTEM_SETTLE_S":0.5})
    cfg = rig.mapping["Pump_laser_power"]
    ctx.finish()
    assert rig.generators[cfg["resource"]].dc[cfg["channel"]] == rig.params.pump_laser_power


def test_mode_drift_and_configuration_revisions(rig):
    ctx = prepared(rig)
    rig.hfi.nodes[rig.hfi.demod_path(0)+"/timeconstant"] *= 2
    with pytest.raises(ValueError, match="已改变"):
        ctx.execute(["A09"])
    assert rig.y_reads == 0
    path = rig.root / "params/safety_limits.yaml"
    path.write_text(path.read_text(encoding="utf-8")+"\n",encoding="utf-8")
    with pytest.raises(ValueError, match="已改变"):
        ctx.recheck()
    ctx.finish()


def test_phase_quality_failure_blocks_successor_and_restores_temperature(rig):
    ctx = workflow.Workflow(rig.params, project_root=rig.root); ctx.execute(through("A06"))
    rig.phase_error = True
    with pytest.raises(RuntimeError): ctx.execute(["A07"])
    assert ctx.states["A07"]["status"] == "quality_failed" and rig.enabled
    with pytest.raises(ValueError, match="缺少前置"): ctx.execute(["A08"])
    ctx.finish()


@pytest.mark.parametrize("component", ["y", "r"])
def test_nonfinite_readings_are_rescued_not_silently_lost(rig, monkeypatch, component):
    rig.params=replace(rig.params,z_freq_start=100)
    ctx=prepared(rig)
    if component=="y":
        original=acquisition.daq.acquire_data
        def bad_y(*args, **kwargs):
            results=original(*args, **kwargs); results[0].values[0]=np.nan; return results
        monkeypatch.setattr(acquisition.daq,"acquire_data",bad_y)
    else:
        original=rig.hfi.get_sample
        def bad_r(path):
            sample=original(path)
            if "/demods/3/" in path: sample["x"]=[float("nan")]
            return sample
        monkeypatch.setattr(rig.hfi,"get_sample",bad_r)
    with pytest.raises(ValueError) as failure: ctx.execute(["A09"])
    ctx.finish(failure.value)
    assert rig.enabled and rig.y_reads==1
    with np.load(next(ctx.directory.raw.glob("freq_*.npz"))) as record:
        assert not bool(record["acquisition_complete"])
        assert np.isnan(record["y_V"][0] if component=="y" else record["demod_r_x_values_V"][0])
    assert ctx.state["completion_status"]=="failed"


def test_phase_restore_failure_is_fatal_not_retryable(rig):
    ctx = workflow.Workflow(rig.params, project_root=rig.root); ctx.execute(through("A06"))
    rig.phase_error = rig.restore_error = True
    with pytest.raises(RuntimeError) as failure: ctx.execute(["A07"])
    assert not isinstance(failure.value, excitation.PhaseQualityError)
    ctx.finish(failure.value)
    assert ctx.closed and not ctx.state["safety_shutdown"]["completed"]


def test_clock_connection_and_optional_tec_failures(rig):
    rig.clock_error = True
    ctx = workflow.Workflow(rig.params,project_root=rig.root)
    with pytest.raises(RuntimeError) as failure: ctx.execute(through("A03"))
    ctx.finish(failure.value)
    rig.gs.set_current.assert_not_called(); rig.gs.disconnect.assert_called_once()
    assert all(g.disconnects == 1 for g in rig.generators.values())
    rig.clock_error = False; rig.tec.connect.side_effect = OSError("串口不可用")
    ctx = prepared(rig)
    assert ctx.devices["tec"] is None and not ctx.state["temperature_control"]["controlled_by_experiment"]
    ctx.finish()
    rig.hfi.connect = Mock(side_effect=OSError("连接中断"))
    ctx = workflow.Workflow(rig.params,project_root=rig.root)
    with pytest.raises(OSError) as failure: ctx.execute(through("A02"))
    ctx.finish(failure.value)
    assert not ctx.devices


@pytest.mark.parametrize("key,value", [("z_drive_amplitude",100), ("system_settle_s",-1),
    ("hf2_demod0_rate",float("nan")), ("auxout_scale",100), ("sigin2_index",0), ("temp_switch",0)])
def test_invalid_parameters_before_connection(rig, key, value):
    with pytest.raises(ValueError): workflow.Workflow(replace(rig.params, **{key:value}),project_root=rig.root)
    assert not rig.events


def test_mfli_rejected_without_fallback_or_connection(rig):
    path = rig.root / "params/mapping.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    for key in ("lockin_r", "lockin_xy"): config["mapping"][key]["device_id"] = "mfli_dev30661"
    path.write_text(yaml.safe_dump(config),encoding="utf-8")
    with pytest.raises(ValueError,match="MFLI"): workflow.Workflow(rig.params,project_root=rig.root)
    assert not rig.events


def test_parameter_compatibility_yaml_and_defaults(rig, monkeypatch):
    from .definition import ADAPTER
    assert ZFieldBandwidthParams.from_external({"FREQ_SETTLE_TIME":0.8}).system_settle_s == 0.8
    assert ZFieldBandwidthParams.from_external({"FREQ_SETTLE_TIME":0.8,"SYSTEM_SETTLE_S":0.1}).system_settle_s == 0.1
    path = rig.root / "params/experiments/z-field-bandwidth.yaml"
    defaults = ZFieldBandwidthParams.from_yaml(path)
    preparation.preflight(defaults, rig.root)
    unsafe = replace(defaults, z_drive_amplitude=100)
    unsafe.save_yaml(path)
    with pytest.raises(ValueError): preparation.preflight(ZFieldBandwidthParams.from_yaml(path), rig.root)
    rig.params.save_yaml(path)
    assert ZFieldBandwidthParams.from_yaml(path).to_external() == rig.params.to_external()
    monkeypatch.setattr(ADAPTER,"root",rig.root); monkeypatch.setattr(ADAPTER,"defaults_path",path)
    assert ADAPTER._resolved({"FREQ_SETTLE_TIME":0.8}).system_settle_s == 0.8
    assert not ADAPTER.preflight({})
    with pytest.raises(ValueError): ADAPTER.save_defaults({"Z_DRIVE_AMPLITUDE":100})
    assert ZFieldBandwidthParams.from_yaml(path).to_external() == rig.params.to_external()


def test_partial_connection_disconnect_failure_is_not_safe(rig):
    rig.hfi.connect = Mock(side_effect=OSError("原始连接错误"))
    rig.gs.disconnect.side_effect = OSError("断开失败")
    ctx = workflow.Workflow(rig.params, project_root=rig.root)
    with pytest.raises(OSError, match="原始连接错误") as failure:
        ctx.execute(through("A02"))
    ctx.finish(failure.value)
    assert ctx.closed and not ctx.state["safety_shutdown"]["completed"]
    assert ctx.states["A10"]["status"] == "failed" and "断开失败" in str(ctx.state["safety_shutdown"])


def test_serial_duplicate_stale_commands_and_explicit_finish(rig, monkeypatch):
    import io
    from . import session
    monkeypatch.delenv("LAB_CANCEL_FILE", raising=False)
    events=[]
    monkeypatch.setattr(session, "report_runtime_progress", lambda *args, **kwargs: events.append(kwargs.get("data", {})))
    ctx=workflow.Workflow(rig.params, project_root=rig.root)
    commands=[dict(request_id="one",revision=0,action="steps",step_ids=["A01"]),
        dict(request_id="one",revision=1,action="steps",step_ids=["A01"]),
        dict(request_id="stale",revision=0,action="steps",step_ids=["A01"]),
        dict(request_id="end",revision=1,action="finish")]
    session.serve(ctx,io.StringIO("\n".join(json.dumps(command) for command in commands)+"\n"))
    errors=[event.get("command_result", {}).get("error") for event in events]
    assert ctx.closed and ctx.states["A10"]["status"]=="completed" and not ctx.devices
    assert any(error and "重复" in error for error in errors)
    assert any(error and "修订" in error for error in errors)


def test_real_full_subprocess(tmp_path):
    copy_config(tmp_path)
    env = dict(os.environ, Z_BANDWIDTH_TEST_ROOT=str(tmp_path), Z_BANDWIDTH_TEST_FULL="1", LAB_TYPED_PARAMETERS=json.dumps(small_params().to_external()))
    env.pop("LAB_CANCEL_FILE",None)
    result = subprocess.run([sys.executable,"-X","utf8","-m",__name__],cwd=ROOT,env=env,capture_output=True,text=True,encoding="utf-8",timeout=60)
    assert result.returncode == 0,result.stdout+result.stderr
    files = list((tmp_path / "data/Z_Field_Bandwidth_Measurement").glob("*/raw/frequency_index.json"))
    assert len(files) == 1 and len(json.loads(files[0].read_text(encoding="utf-8"))) == 3
    config = yaml.safe_load((files[0].parents[1] / "experiment_config.yaml").read_text(encoding="utf-8"))
    assert config["completion_status"] == "completed" and config["safety_shutdown"]["completed"]


def fake_session_main():
    from .session import serve
    payload = json.loads(os.environ["LAB_TYPED_PARAMETERS"])
    session_id = payload.pop("session_id",None)
    root = Path(os.environ["Z_BANDWIDTH_TEST_ROOT"])
    with pytest.MonkeyPatch.context() as patch:
        install_rig(root,patch,ZFieldBandwidthParams.from_external(payload),progress=True)
        if os.environ.get("Z_BANDWIDTH_TEST_FULL") == "1": workflow.main()
        else: serve(workflow.Workflow(ZFieldBandwidthParams.from_external(payload),project_root=root,session_id=session_id),sys.stdin)


if __name__ == "__main__":
    fake_session_main()
