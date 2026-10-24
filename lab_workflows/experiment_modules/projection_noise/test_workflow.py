"""真实准备、SDS 读数与真实写盘的假仪器回归，不连接硬件。"""
from dataclasses import replace
import json
import os
from pathlib import Path
from types import SimpleNamespace
import time
import numpy as np
import pytest
import yaml
from sds_acquisition import AcquisitionResult, load_npz
from ...common import WorkflowCancelled, find_project_root, load_mapping
from ...steps import routed_signal_generator
from ..mx_main_field_calibration.test_workflow import FakeSignal, FakeGS200, FakeTEC
from . import workflow, preparation, acquisition, stages
from .models import ProjectionNoiseParams

ACQUISITION_CASES = {
    "projection-noise": {"mode": "single", "cases": {
        "order": ["test_real_order_slow_disk_and_untouched_pump"],
        "slow_disk": ["test_real_order_slow_disk_and_untouched_pump"],
        "cancel": ["test_failure_preserves_off_background_and_primary_error", "test_later_range_failure_rescues_last_read"],
        "acquire_failure": ["test_failure_preserves_off_background_and_primary_error"],
        "restore_failure": ["test_restore_failure_rescues_frame_and_blocks_completion"],
        "save_failure": ["test_save_failure_rescues_frame_and_preserves_primary", "test_atomic_index_failure_preserves_previous_index"],
        "partial_save": ["test_failure_preserves_off_background_and_primary_error", "test_restore_failure_rescues_frame_and_blocks_completion", "test_later_range_failure_rescues_last_read"],
        "entrypoints": ["test_full_stepwise_rerun_and_sealed_history_are_immutable"],
    }, "sources": [
        "lab_workflows/experiment_modules/projection_noise/workflow.py",
        "lab_workflows/experiment_modules/projection_noise/preparation.py",
        "lab_workflows/experiment_modules/projection_noise/acquisition.py",
        "lab_workflows/experiment_modules/projection_noise/session.py",
    ]},
}


def copy_config(root):
    (root / "params").mkdir(exist_ok=True)
    for name in ("devices.yaml", "mapping.yaml", "safety_limits.yaml"):
        (root / "params" / name).write_bytes((find_project_root() / "params" / name).read_bytes())
    result = root / "data/Mx_Main_Field_Calibration/fake_main/results/analysis.yaml"
    result.parent.mkdir(parents=True)
    result.write_text(yaml.safe_dump(dict(success=True, K_f_Hz_per_mA=1000.0,
        f_0mA_Hz=0.0, frequency_linear_fit=dict(r_squared=1.0))), encoding="utf8")


def small_params():
    return replace(ProjectionNoiseParams(), main_field_calibration_source_run="fake_main",
        target_larmor_frequency_hz=1000, fit_half_width_hz=300, scope_sample_rate_sa_s=8192,
        scope_duration_s=0.125, scope_initial_scale_v_div=0.1, welch_nperseg=256, acq_repeats=2,
        system_settle_s=0.001, temp_switch_on_settle_s=0, main_field_settle_s=0)


class FakeScope:
    def __init__(self, rig):
        self.rig = rig
        self.scale, self.offset = 0.1, 0.0
        self.stopped, self.memory, self.trigger = True, "FSRate", "AUTO"
    def connect(self): pass
    def disconnect(self): self.rig.events.append("disconnect_scope")
    def idn(self): return "SDS-FAKE"
    def get_memory_management(self): return self.memory
    def get_sampling_rate(self): return self.rig.params.scope_sample_rate_sa_s
    def get_actual_points(self): return self.rig.params.scope_requested_points
    def get_memory_depth(self): return str(self.get_actual_points())
    def get_channel_scale(self, channel): return self.scale
    def get_channel_offset(self, channel): return self.offset
    def set_channel_scale(self, channel, value):
        assert self.rig.enabled
        self.scale = value
        self.rig.events.append("configure")
    def set_channel_offset(self, channel, value):
        assert self.rig.enabled
        self.offset = value
        self.rig.events.append("configure")
    def trigger_run(self): self.stopped = False
    def trigger_stop(self): self.stopped = True
    def set_trigger_mode(self, value): self.trigger = "AUTO" if value == "FTRIG" else value
    def get_trigger_mode(self): return self.trigger
    def trigger_status(self): return "STOP" if self.stopped else "RUN"


class FakeAcquirer:
    def __init__(self, scope): self.scope = scope
    def apply_config(self, config):
        assert self.scope.rig.enabled
        channel = next(c for c in config.channels if c.enabled)
        self.scope.scale, self.scope.offset = channel.scale, channel.offset
        self.scope.rig.events.append("configure")
    def acquire_channel(self, channel, *args, **kwargs):
        rig = self.scope.rig
        assert not rig.enabled
        rig.events.append("acquire")
        index = len(rig.captures)
        rig.captures.append(dict(index=index, output=rig.gs200.output, current=rig.gs200.current))
        if index == rig.fault_index:
            raise rig.acquire_error or WorkflowCancelled("模拟取消")
        t = np.arange(rig.params.scope_requested_points) / rig.params.scope_sample_rate_sa_s
        raw = np.rint(rig.waveform_amplitude * np.sin(2 * np.pi * 1000 * t) / 1e-5).astype(np.int16)
        return AcquisitionResult(channel=channel, raw_data=raw, time=t, voltage=raw.astype(float) * 1e-5,
            config_snapshot={}, preamble_dict=dict(point_num=len(t), horiz_interval=1 / rig.params.scope_sample_rate_sa_s,
                horiz_offset=0.0, vertical_gain=1e-5, vertical_offset=0.0, code_per_div=1))


def install_rig(root, monkeypatch, params=None, *, progress=False):
    rig = SimpleNamespace(root=root, params=params or small_params(), mapping=load_mapping(root), events=[],
        generators={}, enabled=True, restore_error=False, clock_error=False, fault_index=None, acquire_error=None,
        cancel=False, waveform_amplitude=0.3, captures=[], gs200=FakeGS200(), tec=FakeTEC(), connected=[])
    rig.scope = FakeScope(rig)
    forbidden = {rig.mapping[key]["resource"] for key in ("Pump_modulation", "Time_sequence")}
    def signal(cfg):
        assert cfg["resource"] not in forbidden, "不得连接专用 Pump 载波/门控设备"
        rig.connected.append(cfg["resource"])
        return rig.generators.setdefault(cfg["resource"], FakeSignal(cfg["resource"], rig))
    monkeypatch.setattr(routed_signal_generator, "create_signal_generator", signal)
    monkeypatch.setattr(preparation, "GS200Instrument", lambda resource: rig.gs200)
    monkeypatch.setattr(preparation, "SDSInstrument", lambda resource: rig.scope)
    monkeypatch.setattr(preparation, "SDSAcquisition", FakeAcquirer)
    monkeypatch.setattr(preparation, "TECInstrument", lambda **kwargs: rig.tec)
    monkeypatch.setattr(workflow, "find_project_root", lambda: root)
    original_check = acquisition.check_cancelled
    def check():
        original_check()
        if rig.cancel:
            raise WorkflowCancelled("模拟取消")
    def wait(seconds):
        rig.events.append("settle")
        check()
    for module in (workflow, acquisition, preparation):
        monkeypatch.setattr(module, "check_cancelled", check)
    monkeypatch.setattr(acquisition, "_sleep", wait)
    monkeypatch.setattr(preparation, "_sleep", lambda seconds: check())
    original_current = rig.gs200.set_current
    def current(value):
        assert rig.enabled
        rig.events.append("configure")
        original_current(value)
    rig.gs200.set_current = current
    if not progress:
        for module in (workflow, acquisition):
            monkeypatch.setattr(module, "report_runtime_progress", lambda *a, **kw: None)
    return rig


@pytest.fixture
def rig(tmp_path, monkeypatch):
    copy_config(tmp_path)
    return install_rig(tmp_path, monkeypatch)


def through(last):
    ids = [s.id for s in stages.ACQUISITION if s.id != stages.FINISH_ID]
    return ids[:ids.index(last) + 1]


def assert_safe(rig):
    assert rig.enabled and rig.scope.stopped
    assert (rig.gs200.source, rig.gs200.current, rig.gs200.output, rig.gs200.range, rig.gs200.limit) == ("CURRent", 0.008, True, 0.01, 0.01)
    for key in ("X_magnetic_field", "Y_magnetic_field", "Z_magnetic_field"):
        cfg = rig.mapping[key]
        assert rig.generators[cfg["resource"]].output[cfg["channel"]] is False
    assert not ({rig.mapping[k]["resource"] for k in ("Pump_modulation", "Time_sequence")} & set(rig.connected))


@pytest.mark.parametrize("control", [False, True])
def test_real_order_slow_disk_and_untouched_pump(rig, monkeypatch, control):
    rig.params = replace(rig.params, measure_field_off_control=control)
    original, original_index, write_text, path_open = np.savez_compressed, np.savez, Path.write_text, Path.open
    def save_text(path, *args, **kwargs):
        assert rig.enabled
        return write_text(path, *args, **kwargs)
    def open_file(path, mode="r", *args, **kwargs):
        if any(c in mode for c in "wax+"):
            assert rig.enabled, "关温控窗口不能写波形、元数据、配置或状态"
        return path_open(path, mode, *args, **kwargs)
    def save(path, **data):
        assert rig.enabled
        rig.events.append("save")
        time.sleep(0.001)
        return original(path, **data)
    monkeypatch.setattr(Path, "open", open_file)
    monkeypatch.setattr(Path, "write_text", save_text)
    monkeypatch.setattr(np, "savez_compressed", save)
    def save_index(*args, **kwargs):
        assert rig.enabled, "恢复温控前不得写采集索引"
        return original_index(*args, **kwargs)
    monkeypatch.setattr(np, "savez", save_index)
    progress = []
    monkeypatch.setattr(acquisition, "report_runtime_progress", lambda *args, **kw: progress.append(kw))
    run = workflow.run(rig.params)
    assert_safe(rig)
    for i, event in enumerate(rig.events):
        if event == "acquire":
            assert rig.events[i - 3:i] == ["off", "settle", "settle"]
            assert rig.events[i + 1:i + 3] == ["on", "save"]
    assert len(rig.captures) == (4 if control else 2)
    assert [c["output"] for c in rig.captures] == ([False, False, True, True] if control else [True, True])
    with np.load(run / "raw/scope_acquisition_index.npz") as data:
        assert len(data["phase"]) == len(rig.captures)
    frame, _ = load_npz(str(run / "raw/field_on/waveform_0000.npz"))
    assert len(frame[0].raw_data) == rig.params.scope_requested_points
    config = yaml.safe_load((run / "experiment_config.yaml").read_text(encoding="utf8"))
    assert config["completion_status"] == "completed" and config["raw_format_version"] == 1
    assert config["temperature_control"]["actual_temperature_c"] is None
    assert progress[0].get("estimated_remaining_seconds") is None
    assert progress[-1]["estimated_remaining_seconds"] == 0


@pytest.mark.parametrize("failure", [WorkflowCancelled("模拟取消"), OSError("模拟读取失败")])
def test_failure_preserves_off_background_and_primary_error(rig, failure):
    rig.fault_index, rig.acquire_error = 2, failure
    ctx = workflow.Workflow(rig.params)
    with pytest.raises(type(failure)) as caught:
        try:
            ctx.execute(through("A07"))
        finally:
            ctx.finish(failure)
    assert caught.value is failure
    assert_safe(rig)
    assert len(list(ctx.directory.raw.glob("field_off/waveform_*.npz"))) == 2
    with np.load(ctx.directory.raw / "scope_acquisition_index.npz") as data:
        assert data["phase"].tolist() == ["field_off", "field_off"]
    assert ctx.state["failure_reason"] == str(failure)


def test_restore_failure_rescues_frame_and_blocks_completion(rig):
    rig.restore_error = True
    ctx = workflow.Workflow(rig.params)
    with pytest.raises(OSError, match="温控恢复失败") as caught:
        try:
            ctx.execute(through("A07"))
        finally:
            ctx.finish(OSError("模拟温控恢复失败"))
    recovered = list(ctx.directory.raw.rglob("recovered_*.npz"))
    assert len(rig.captures) == len(recovered) == 1
    with np.load(recovered[0]) as data:
        assert data["raw_data"].size == rig.params.scope_requested_points and not data["accepted"]
    assert not ctx.state["safety_shutdown"]["completed"] and ctx.state["completion_status"] != "completed"


def test_save_failure_rescues_frame_and_preserves_primary(rig, monkeypatch):
    error = OSError("模拟写盘失败")
    def fail(*args, **kwargs): raise error
    monkeypatch.setattr(acquisition, "_save_frame", fail)
    with pytest.raises(OSError) as caught:
        workflow.run(rig.params)
    assert caught.value is error
    assert_safe(rig)
    run = next((rig.root / "data/Projection_noise").iterdir())
    assert len(list((run / "raw").rglob("recovered_*.npz"))) == 1


@pytest.mark.parametrize("failure", [WorkflowCancelled("后续量程取消"), OSError("后续量程读取失败")])
def test_later_range_failure_rescues_last_read(rig, failure):
    rig.waveform_amplitude, rig.fault_index, rig.acquire_error = 0.05, 1, failure
    with pytest.raises(type(failure)) as caught:
        workflow.run(rig.params)
    assert caught.value is failure
    assert_safe(rig)
    run = next((rig.root / "data/Projection_noise").iterdir())
    assert len(list((run / "raw").rglob("recovered_*.npz"))) == 1
    assert not list((run / "raw").rglob("waveform_*.npz"))


def test_atomic_index_failure_preserves_previous_index(rig, monkeypatch):
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A06"))
    acquisition._save_acquisition_index(ctx.directory, [])
    index = ctx.directory.raw / "scope_acquisition_index.npz"
    original, replace_path = index.read_bytes(), Path.replace
    def fail(source, target):
        if Path(target) == index:
            raise OSError("模拟索引替换失败")
        return replace_path(source, target)
    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(OSError, match="索引替换失败") as caught:
        try:
            ctx.execute(["A07"])
        finally:
            ctx.finish(OSError("模拟索引替换失败"))
    assert index.read_bytes() == original
    assert list(ctx.directory.raw.rglob("waveform_*.npz"))
    assert list(ctx.directory.raw.glob(".pending_scope_acquisition_index_*.npz"))
    assert any("索引抢救" in note for note in caught.value.__notes__)


def test_full_stepwise_rerun_and_sealed_history_are_immutable(rig):
    first = workflow.run(rig.params)
    before = {p: p.read_bytes() for p in first.rglob("*") if p.is_file()}
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A06")); ctx.execute(["A07"])
    second = ctx.directory.root
    ctx.execute(["A07"])
    assert ctx.directory.root != second
    ctx.execute(["A08"])
    sealed = ctx.directory.config_path.read_bytes()
    with pytest.raises(ValueError, match="缺少前置"):
        ctx.execute(["A07"])
    ctx.update_parameters({"FIXED_PARAMS.Pump_laser_power": 0.1})
    ctx.finish()
    assert ctx.directory.config_path.read_bytes() == sealed
    assert {p: p.read_bytes() for p in before} == before
    assert_safe(rig)


def test_dependencies_impacts_preflight_quality_and_mode(rig):
    ctx = workflow.Workflow(rig.params)
    with pytest.raises(ValueError, match="缺少前置"):
        ctx.execute(["A07"])
    assert not rig.events
    ctx.execute(through("A06"))
    states = ctx.snapshot()["steps"]
    ctx.execute(["A01"])
    assert ctx.snapshot()["steps"] == states
    changes = {"ACQ_REPEATS": 3, "WELCH_NPERSEG": 128}
    impacts = stages.definition()["parameter_impacts"]["acquisition"]
    affected = {i for key in changes for i in impacts[key]}
    ctx.update_parameters(changes)
    assert {key for key, value in ctx.states.items() if value["status"] == "stale"} == affected & set(states) - {"A07", "A08"}
    ctx.execute(["A06"])
    rig.scope.memory = "AUTO"
    with pytest.raises(ValueError, match="FSRate") as caught:
        ctx.execute(["A07"])
    assert ctx.states["A07"]["status"] == "quality_failed" and not rig.captures
    with pytest.raises(ValueError, match="缺少前置"):
        ctx.execute(["A08"])
    ctx.finish(caught.value)
    assert_safe(rig)


def test_clock_failure_preserves_original_and_disconnects(rig):
    rig.clock_error = True
    with pytest.raises(Exception, match="时钟"):
        workflow.run(rig.params)
    assert "disconnect_scope" in rig.events
    assert_safe(rig)


def test_main_field_restoration_is_registered_for_each_round(rig):
    ctx = workflow.Workflow(rig.params)
    for _ in range(2):
        ctx.execute(through("A08"))
        assert_safe(rig)
    ctx.finish()


def test_pump_drafts_never_change_executed_output_during_finish(rig):
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A06"))
    cfg = rig.mapping["Pump_laser_power"]
    previous = rig.generators[cfg["resource"]].dc[cfg["channel"]]
    ctx.update_parameters({"FIXED_PARAMS.Pump_laser_power": 0.8})
    ctx.finish()
    assert rig.generators[cfg["resource"]].dc[cfg["channel"]] == previous
    assert_safe(rig)


def test_parameter_migrations_nonfinite_and_calibration_revision(rig):
    values = ProjectionNoiseParams.from_external({"SYSTEM_SETTLE_S": 0.7, "TEMP_SWITCH_OFF_SETTLE_S": 0.8,
        "FIXED_PARAMS": {"Probe_laser_power": 0.2}, "NPERSEG": 128}, schema_version=2)
    assert values.system_settle_s == 0.7 and values.probe_laser_power_v == 0.2 and values.welch_nperseg == 128
    for field in ("scope_duration_s", "scope_sample_rate_sa_s", "target_larmor_frequency_hz", "fit_half_width_hz"):
        with pytest.raises(ValueError, match="有限"):
            preparation.preflight(replace(rig.params, **{field: float("nan")}), rig.root)
    ctx = workflow.Workflow(rig.params)
    source = rig.root / "data/Mx_Main_Field_Calibration/fake_main/results/analysis.yaml"
    source.write_text(source.read_text() + "\nrevision: 2\n")
    with pytest.raises(ValueError, match="来源已改变"):
        ctx.execute(["A01"])
    ctx.finish()


def test_cancel_during_system_settle_restores_without_reading(rig, monkeypatch):
    def cancel_wait(seconds):
        if not rig.enabled:
            raise WorkflowCancelled("稳定等待取消")
    monkeypatch.setattr(acquisition, "_sleep", cancel_wait)
    with pytest.raises(WorkflowCancelled, match="稳定等待取消"):
        workflow.run(rig.params)
    assert not rig.captures
    assert_safe(rig)


def test_protected_pump_channel_collision_rejected_before_connect(rig):
    path = rig.root / "params/mapping.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf8"))
    document["mapping"]["Temp_Switch"]["device_id"] = document["mapping"]["Pump_modulation"]["device_id"]
    document["mapping"]["Temp_Switch"]["endpoint"] = document["mapping"]["Pump_modulation"]["endpoint"]
    # 去除与本次测试无关的同机组；保留端点占用保护。
    document["constraints"]["colocation_groups"].pop("temperature_control", None)
    path.write_text(yaml.safe_dump(document), encoding="utf8")
    with pytest.raises(ValueError, match="通道|冲突"):
        preparation.preflight(rig.params, rig.root)
    assert not rig.events


def test_sealed_tec_disconnect_failure_not_reported_as_safe_success(rig):
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A08"))
    def fail(): raise OSError("模拟 TEC 断开失败")
    rig.tec.disconnect = fail
    with pytest.raises(RuntimeError, match="TEC 断开失败"):
        ctx.finish()
    assert ctx.snapshot()["steps"][stages.FINISH_ID]["status"] == "failed"
    assert not ctx.snapshot()["safety_shutdown"]["completed"]


def test_real_full_module_entry_in_isolated_fake_child(rig):
    import subprocess
    import sys
    environment = os.environ | dict(PROJECTION_TEST_ROOT=str(rig.root), PROJECTION_FULL_ENTRY="1",
        LAB_TYPED_PARAMETERS=json.dumps(rig.params.to_external()), LAB_CANCEL_FILE=str(rig.root / "cancel"), PYTHONUTF8="1")
    result = subprocess.run([sys.executable, "-m", __package__ + ".test_workflow"], env=environment,
                            capture_output=True, text=True, encoding="utf8", timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    run = next((rig.root / "data/Projection_noise").iterdir())
    config = yaml.safe_load((run / "experiment_config.yaml").read_text(encoding="utf8"))
    assert config["completion_status"] == "completed" and config["main_field_restored"]
    assert (run / "raw/scope_acquisition_index.npz").is_file()
    assert len(list((run / "raw").rglob("waveform_*.npz"))) == 4


def main():
    """仅供正式测试使用的隔离假仪器子进程；不改变生产入口。"""
    from .session import serve
    from ...experiment_runtime import load_runtime_params
    import sys
    root = Path(os.environ["PROJECTION_TEST_ROOT"])
    payload = json.loads(os.environ["LAB_TYPED_PARAMETERS"])
    session_id = payload.pop("session_id", None)
    os.environ["LAB_TYPED_PARAMETERS"] = json.dumps(payload)
    with pytest.MonkeyPatch.context() as patch:
        install_rig(root, patch, ProjectionNoiseParams.from_external(payload), progress=True)
        if os.environ.get("PROJECTION_FULL_ENTRY") == "1":
            from ... import common
            import runpy
            patch.setattr(common, "find_project_root", lambda: root)
            runpy.run_module(__package__ + ".workflow", run_name="__main__")
        else:
            serve(workflow.Workflow(load_runtime_params(ProjectionNoiseParams), project_root=root, session_id=session_id), sys.stdin)


if __name__ == "__main__":
    main()
