"""真实准备、SDS 读取和写盘链的假仪器回归；不连接硬件。"""
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
import time

import numpy as np
import pytest
import yaml

from ...common import WorkflowCancelled, find_project_root, load_mapping
from ...steps import routed_signal_generator
from ..mx_main_field_calibration.test_workflow import FakeSignal, FakeGS200, FakeTEC
from . import workflow, preparation, acquisition, stages
from .models import MxMainFieldScopeNoiseSpectrumParams

ACQUISITION_CASES = {
    "mx-main-field-scope-noise-spectrum": {"mode": "single", "cases": {
        "order": ["test_real_order_and_slow_disk"],
        "slow_disk": ["test_real_order_and_slow_disk"],
        "cancel": ["test_failure_keeps_previous_points", "test_later_range_attempt_rescues_earlier_frame"],
        "acquire_failure": ["test_failure_keeps_previous_points"],
        "restore_failure": ["test_restore_failure_rescues_read_frame"],
        "save_failure": ["test_disk_failure_preserves_primary_error"],
        "partial_save": ["test_failure_keeps_previous_points", "test_restore_failure_rescues_read_frame", "test_later_range_attempt_rescues_earlier_frame"],
        "entrypoints": ["test_stepwise_rerun_and_sealed_configuration_are_immutable"],
    }, "sources": [
        "lab_workflows/experiment_modules/mx_main_field_scope_noise_spectrum/preparation.py",
        "lab_workflows/experiment_modules/mx_main_field_scope_noise_spectrum/acquisition.py",
        "lab_workflows/experiment_modules/mx_main_field_scope_noise_spectrum/workflow.py",
        "lab_workflows/experiment_modules/mx_main_field_scope_noise_spectrum/session.py",
    ]},
}


def copy_config(root):
    (root / "params").mkdir(exist_ok=True)
    for name in ("devices.yaml", "mapping.yaml", "safety_limits.yaml"):
        (root / "params" / name).write_bytes((find_project_root() / "params" / name).read_bytes())


def small_params():
    return replace(MxMainFieldScopeNoiseSpectrumParams(), control_frequency_start_hz=100,
        control_frequency_stop_hz=1000, control_frequency_points=10, scope_sample_rate_sa_s=8192,
        scope_duration_s=0.125, scope_initial_scale_v_div=0.1, welch_nperseg=128,
        fit_half_width_hz=1000, fit_peak_margin_hz=1000, fit_gamma_guess_hz=100,
        global_2d_analysis_enabled=False, global_fit_frequency_min_hz=50,
        global_core_fit_frequency_min_hz=200, global_fit_frequency_max_hz=900,
        global_background_margin_hz=500, global_cv_folds=2, fit_example_plot_count=1,
        system_settle_s=0.001, temp_switch_on_settle_s=0)


class FakeScope:
    def __init__(self, rig):
        self.rig = rig
        self.scale, self.offset, self.coupling = 0.1, 0.0, "DC"
        self.stopped = True

    def connect(self): pass
    def disconnect(self): pass
    def idn(self): return "SDS-FAKE"
    def get_memory_management(self): return "FSRate"
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
    def set_channel_coupling(self, channel, value): self.coupling = value
    def trigger_run(self): self.stopped = False
    def trigger_stop(self): self.stopped = True
    def set_trigger_mode(self, value): pass
    def get_trigger_mode(self): return "AUTO"
    def trigger_status(self): return "STOP" if self.stopped else "RUN"


class FakeAcquirer:
    def __init__(self, scope): self.scope = scope
    def apply_config(self, config):
        assert self.scope.rig.enabled
        channel = next(c for c in config.channels if c.enabled)
        self.scope.scale, self.scope.offset, self.scope.coupling = channel.scale, channel.offset, channel.coupling
        self.scope.rig.events.append("configure")
    def acquire_channel(self, *args, **kwargs):
        rig = self.scope.rig
        assert not rig.enabled
        rig.events.append("acquire")
        index = len(rig.captures)
        rig.captures.append(index)
        if index == rig.fault_index:
            raise rig.acquire_error or WorkflowCancelled("模拟取消")
        time_s = np.arange(rig.params.scope_requested_points) / rig.params.scope_sample_rate_sa_s
        voltage = rig.waveform_amplitude * np.sin(2 * np.pi * 256 * time_s)
        return SimpleNamespace(time=time_s, voltage=voltage, preamble_dict={"point_num": len(time_s)})


def install_rig(root, monkeypatch, params=None, *, progress=False):
    rig = SimpleNamespace(root=root, params=params or small_params(), mapping=load_mapping(root),
        events=[], captures=[], generators={}, enabled=True, restore_error=False, clock_error=False,
        fault_index=None, acquire_error=None, cancel=False, waveform_amplitude=0.3, gs200=FakeGS200(), tec=FakeTEC())
    rig.scope = FakeScope(rig)
    for cfg in rig.mapping.values():
        if cfg["instrument"] == "signal_generator":
            rig.generators.setdefault(cfg["resource"], FakeSignal(cfg["resource"], rig))
    monkeypatch.setattr(routed_signal_generator, "create_signal_generator", lambda cfg: rig.generators[cfg["resource"]])
    monkeypatch.setattr(preparation, "create_signal_generator", lambda cfg: rig.generators[cfg["resource"]])
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
    old_set_current = rig.gs200.set_current
    def current(value):
        assert rig.enabled, "主场配置应在温控开启时"
        rig.events.append("configure")
        old_set_current(value)
    rig.gs200.set_current = current
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


def assert_safe(rig):
    assert rig.enabled and rig.scope.stopped and rig.scope.coupling == "DC"
    assert (rig.gs200.source, rig.gs200.current, rig.gs200.output, rig.gs200.range, rig.gs200.limit) == ("CURRent", 0.008, True, 0.01, 0.01)
    for key in ("Z_magnetic_field", "X_magnetic_field", "Y_magnetic_field"):
        cfg = rig.mapping[key]
        assert rig.generators[cfg["resource"]].output[cfg["channel"]] is False


def test_real_order_and_slow_disk(rig, monkeypatch):
    original = np.savez
    write_text = Path.write_text
    def save_text(path, *args, **kwargs):
        assert rig.enabled, "温控恢复之前不可写配置、元数据或会话快照"
        return write_text(path, *args, **kwargs)
    monkeypatch.setattr(Path, "write_text", save_text)
    def save(path, **data):
        assert rig.enabled, "温控恢复之前不可写波形、快照或元数据"
        if "waveform_I" in str(path):
            rig.events.append("save")
            time.sleep(0.001)
        return original(path, **data)
    monkeypatch.setattr(np, "savez", save)
    progress = []
    monkeypatch.setattr(acquisition, "report_runtime_progress", lambda *args, **kwargs: progress.append(kwargs["data"]))
    directory = workflow.run(rig.params)
    assert progress[0]["estimated_remaining_seconds"] is None
    assert progress[-1]["estimated_remaining_seconds"] == 0
    assert_safe(rig)
    for index, event in enumerate(rig.events):
        if event == "acquire":
            assert rig.events[index - 3:index] == ["off", "settle", "settle"]
            assert rig.events[index + 1:index + 3] == ["on", "save"]
    assert len(list((directory / "raw").glob("waveform_I*.npz"))) == 10
    config = yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf8"))
    assert config["completion_status"] == "completed" and config["raw_format_version"] == 1
    assert config["temperature_control"]["actual_temperature_c"] is None
    assert not list((directory / "raw").glob("recovered_*.npz"))


@pytest.mark.parametrize("failure", [WorkflowCancelled("模拟取消"), OSError("模拟读取失败")])
def test_failure_keeps_previous_points(rig, failure):
    rig.fault_index, rig.acquire_error = 1, failure
    ctx = workflow.Workflow(rig.params)
    with pytest.raises(type(failure), match=str(failure)):
        try:
            ctx.execute(through("A08"))
        finally:
            ctx.finish(failure)
    assert_safe(rig)
    assert len(list(ctx.directory.raw.glob("waveform_I*.npz"))) == 1
    assert ctx.state["failure_reason"] == str(failure)


def test_restore_failure_rescues_read_frame(rig):
    rig.restore_error = True
    ctx = workflow.Workflow(rig.params)
    with pytest.raises(OSError, match="温控恢复失败") as error:
        try:
            ctx.execute(through("A08"))
        finally:
            ctx.finish(OSError("模拟温控恢复失败"))
    recovered = list(ctx.directory.raw.glob("recovered_*.npz"))
    assert len(rig.captures) == 1 and len(recovered) == 1
    with np.load(recovered[0]) as frame:
        assert frame["voltage_v"].size == rig.params.scope_requested_points
        assert not frame["accepted"]
    assert ctx.state["safety_shutdown"]["completed"] is False
    assert ctx.state["completion_status"] != "completed"


def test_disk_failure_preserves_primary_error(rig, monkeypatch):
    original = acquisition._save_point
    def save(path, *args, **kwargs):
        if kwargs["point_index"] == 1:
            raise OSError("模拟慢盘写入失败")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(acquisition, "_save_point", save)
    ctx = workflow.Workflow(rig.params)
    with pytest.raises(OSError, match="模拟慢盘写入失败"):
        try:
            ctx.execute(through("A08"))
        finally:
            ctx.finish(OSError("模拟慢盘写入失败"))
    assert_safe(rig)
    assert len(list(ctx.directory.raw.glob("waveform_I*.npz"))) == 1


def test_stepwise_rerun_and_sealed_configuration_are_immutable(rig):
    ctx = workflow.Workflow(rig.params)
    try:
        with pytest.raises(ValueError, match="缺少前置"):
            ctx.execute(["A08"])
        assert not rig.events
        ctx.execute(through("A08"))
        old = ctx.directory.root
        saved = {p: p.read_bytes() for p in (old / "raw").glob("*")}
        ctx.execute(["A08"])
        assert ctx.directory.root != old
        assert {p: p.read_bytes() for p in saved} == saved
        ctx.execute(["A09"])
        sealed = ctx.directory.config_path.read_bytes()
        ctx.update_parameters({"PUMP_CARRIER_AMPLITUDE_VPP": 0.1})
        assert ctx.states["A06"]["status"] == "stale"
        assert ctx.directory.config_path.read_bytes() == sealed
    finally:
        ctx.finish()
    assert_safe(rig)
    assert ctx.directory.config_path.read_bytes() == sealed
    cfg = rig.mapping["Pump_modulation"]
    assert rig.generators[cfg["resource"]].amplitude[cfg["channel"]] == rig.params.pump_carrier_amplitude_vpp


def test_autorange_inherits_state_between_points(rig):
    rig.waveform_amplitude = 0.01
    directory = workflow.run(rig.params)
    with np.load(directory / "raw/waveform_I0000.npz") as first, np.load(directory / "raw/waveform_I0001.npz") as second:
        assert first["attempt_count"] > 1
        assert first["next_scale_v_div"] == second["attempt_scales_v_div"][0]


def test_preflight_parameter_impacts_and_mapping_drift(rig):
    ctx = workflow.Workflow(rig.params)
    visible = set(rig.params.to_external(include_hidden=False))
    owned = {key for column in ("acquisition", "analysis") for s in stages.definition()[column] for key in s["display_parameters"]}
    assert visible <= owned
    ctx.execute(through("A07"))
    ctx.update_parameters({"WELCH_NPERSEG": 256, "SYSTEM_SETTLE_S": 0.002})
    impacts = stages.definition()["parameter_impacts"]["acquisition"]
    expected = set(impacts["WELCH_NPERSEG"]) | set(impacts["SYSTEM_SETTLE_S"])
    assert {key for key, value in ctx.states.items() if value["status"] == "stale"} == expected & set(through("A07"))
    (rig.root / "params/safety_limits.yaml").write_text("changed", encoding="utf8")
    with pytest.raises(RuntimeError, match="已改变"):
        ctx.execute(["A07"])
    ctx.finish()
    assert_safe(rig)


def test_clock_failure_and_finish_without_predecessors(rig):
    ctx = workflow.Workflow(rig.params)
    ctx.execute([stages.FINISH_ID])
    assert ctx.closed and not rig.events
    ctx = workflow.Workflow(rig.params)
    rig.clock_error = True
    with pytest.raises(Exception, match="时钟|参考") as error:
        try:
            ctx.execute(through("A03"))
        finally:
            ctx.finish(OSError("参考时钟失败"))
    assert ctx.closed and ctx.clock_failed
    assert_safe(rig)


@pytest.mark.parametrize("failure", [WorkflowCancelled("量程重试时取消"), OSError("量程重试时读取失败")])
def test_later_range_attempt_rescues_earlier_frame(rig, failure):
    rig.waveform_amplitude = 0.01
    rig.fault_index, rig.acquire_error = 1, failure
    ctx = workflow.Workflow(rig.params)
    with pytest.raises(type(failure), match=str(failure)):
        try:
            ctx.execute(through("A08"))
        finally:
            ctx.finish(failure)
    assert len(list(ctx.directory.raw.glob("recovered_I0000_*.npz"))) == 1
    assert not list(ctx.directory.raw.glob("waveform_I*.npz"))
    assert_safe(rig)


@pytest.mark.parametrize("stage", ["A07", "A08"])
def test_quality_gate_rejects_successor_and_preserves_diagnostics(rig, stage):
    ctx = workflow.Workflow(rig.params)
    if stage == "A07":
        rig.scope.get_sampling_rate = lambda: 1000
    else:
        rig.waveform_amplitude = np.nan
    with pytest.raises(ValueError, match="Nyquist|非有限"):
        ctx.execute(through(stage))
    assert ctx.states[stage]["status"] == "quality_failed"
    with pytest.raises(ValueError, match="缺少前置"):
        ctx.execute(["A08" if stage == "A07" else "A09"])
    ctx.finish()
    assert_safe(rig)


def test_unexecuted_drafts_cannot_change_shutdown_outputs(rig, monkeypatch):
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A07"))
    restored = []
    set_coupling = rig.scope.set_channel_coupling
    def coupling(channel, value):
        restored.append(channel)
        set_coupling(channel, value)
    monkeypatch.setattr(rig.scope, "set_channel_coupling", coupling)
    ctx.update_parameters({"PUMP_CARRIER_AMPLITUDE_VPP": 0.1, "SCOPE_PD_CHANNEL": 2})
    ctx.finish()
    assert restored == [1]
    cfg = rig.mapping["Pump_modulation"]
    assert rig.generators[cfg["resource"]].amplitude[cfg["channel"]] == rig.params.pump_carrier_amplitude_vpp
    assert_safe(rig)


def test_final_disconnect_error_is_not_reported_as_success(rig):
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A09"))
    sealed = ctx.directory.config_path.read_bytes()
    def fail():
        raise OSError("模拟 TEC 断开失败")
    rig.tec.disconnect = fail
    with pytest.raises(RuntimeError, match="TEC"):
        ctx.finish()
    assert ctx.closed and ctx.snapshot()["safety_shutdown"]["completed"] is False
    assert ctx.states[stages.FINISH_ID]["status"] == "failed"
    assert ctx.directory.config_path.read_bytes() == sealed


def test_legacy_parameters_and_current_yaml_roundtrip():
    values = MxMainFieldScopeNoiseSpectrumParams.from_external({"TEMP_SWITCH_OFF_SETTLE_S": 0.8}, schema_version=3)
    assert values.system_settle_s == 0.8 and values.x_dc_field_v == values.y_dc_field_v == 0
    values = MxMainFieldScopeNoiseSpectrumParams.from_external({"SYSTEM_SETTLE_S": 0.2, "TEMP_SWITCH_OFF_SETTLE_S": 0.8})
    assert values.system_settle_s == 0.2
    from .definition import ADAPTER
    assert not ADAPTER.preflight(ADAPTER.defaults().to_external())
    from .definition import preflight
    assert "CONTROL_FREQUENCY_POINTS" in preflight({"CONTROL_FREQUENCY_POINTS": 1})[0]
    from ..mx_xy_residual_field_calibration.workflow import _connect_devices, safe_shutdown
    assert _connect_devices is preparation._connect_devices and safe_shutdown is preparation.safe_shutdown


def fake_session_main():
    """仅 API 回归选择此子进程入口，不替换生产会话协议。"""
    import os
    import sys
    from pytest import MonkeyPatch
    from .session import serve
    payload = json.loads(os.environ["LAB_TYPED_PARAMETERS"])
    session_id = payload.pop("session_id")
    root = Path(os.environ["MX_SCOPE_TEST_ROOT"])
    params = MxMainFieldScopeNoiseSpectrumParams.from_external(payload)
    with MonkeyPatch.context() as patch:
        install_rig(root, patch, params, progress=True)
        ctx = workflow.Workflow(params, project_root=root, session_id=session_id)
        serve(ctx, sys.stdin)


if __name__ == "__main__":
    fake_session_main()
