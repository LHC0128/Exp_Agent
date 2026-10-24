"""真实业务、假仪器和真实文件；完整采集安全回归，不连接硬件。"""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import json
import os
import shutil
import sys
import numpy as np
import pytest
import yaml
from ...common import WorkflowCancelled, load_mapping
from ...steps import routed_signal_generator
from . import acquisition, preparation, excitation, workflow, stages, run_format
from .models import BBDutyScanBurstParams

ACQUISITION_CASES = {
    "bb-duty-scan-burst": {"mode": "single", "cases": {
        "order": ["test_each_shot_six_steps_real_files_and_locked_repeats"],
        "slow_disk": ["test_each_shot_six_steps_real_files_and_locked_repeats"],
        "cancel": ["test_cancel_after_read_keeps_frame_and_stops"],
        "acquire_failure": ["test_acquire_failure_keeps_previous_and_primary_error"],
        "restore_failure": ["test_restore_failure_keeps_read_frame"],
        "save_failure": ["test_save_failure_keeps_previous_and_rejects_completion"],
        "partial_save": ["test_cancel_after_read_keeps_frame_and_stops", "test_restore_failure_keeps_read_frame"],
        "entrypoints": ["test_full_stepwise_rerun_and_frozen_snapshot"],
    }, "sources": [
        "lab_workflows/experiment_modules/bb_duty_scan_burst/preparation.py",
        "lab_workflows/experiment_modules/bb_duty_scan_burst/excitation.py",
        "lab_workflows/experiment_modules/bb_duty_scan_burst/acquisition.py",
        "lab_workflows/experiment_modules/bb_duty_scan_burst/workflow.py",
        "lab_workflows/experiment_modules/bb_duty_scan_burst/session.py",
    ]},
}
REGRESSION_CASES = {
    "bb-duty-scan-burst": {"parameters": {
        "sources": ["lab_workflows/experiment_modules/bb_duty_scan_burst/models.py", "params/experiments/bb-duty-scan-burst.yaml"],
        "tests": ["test_invalid_parameters_before_connection", "test_wait_migration_yaml_and_actual_defaults"],
    }},
}
ROOT = Path(__file__).resolve().parents[3]


def copy_config(root):
    (root / "params").mkdir(parents=True, exist_ok=True)
    for name in ("devices.yaml", "mapping.yaml", "safety_limits.yaml"):
        shutil.copyfile(ROOT / "params" / name, root / "params" / name)


def small_params():
    return replace(BBDutyScanBurstParams(), duty_cycle_list=[0.02, 0.1, 0.2], acq_repeats=2,
                   burst_cycles=300, pre_baseline_time_s=0.001, post_dark_time_s=0.003,
                   system_settle_s=0.001, temp_switch_on_delay_s=0)


def signal(p, duty):
    rate = p.sds_sample_rate
    t = np.arange(round(p.total_acq_s * rate)) / rate - p.total_acq_s / 2
    tau = 0.0005 / (1 + duty * 3)
    build = 0.12 * (1 - np.exp(-np.maximum(t, 0) / tau))
    dark = 0.12 * (1 - np.exp(-p.burst_duration_s / tau)) * np.exp(-np.maximum(t - p.burst_duration_s, 0) / 0.0012)
    envelope = np.where(t < 0, 0, np.where(t <= p.burst_duration_s, build, dark))
    y = envelope * np.sin(2 * np.pi * p.modulation_frequency_hz * t) + 1e-5 * np.sin(2 * np.pi * 1307 * t)
    return t, y


class FakeDG:
    def __init__(self, rig, resource):
        self.rig, self.resource = rig, resource
        self.values, self.clock = {}, "INT"
        self.connections = self.disconnections = 0

    def connect(self): self.connections += 1
    def disconnect(self): self.disconnections += 1
    def get_ref_clock_source(self): return "INT" if self.rig.clock_error else self.clock
    def set_ref_clock_source(self, value): self.clock = value

    def setup_dc(self, value, *, channel):
        cfg = self.rig.mapping["Temp_Switch"]
        if cfg["resource"] == self.resource and cfg["channel"] == channel:
            self.rig.events.append(("on" if value else "off", value))
            if value:
                self.rig.restore_attempted = True
                if self.rig.restore_error and not self.rig.enabled:
                    raise OSError("模拟温控恢复失败")
            else:
                self.rig.restore_attempted = False
            self.rig.enabled = bool(value)
        else:
            assert self.rig.enabled or self.rig.restore_attempted
        self.values["dc", channel] = value
        self.set_output(True, channel=channel)

    def apply_wave(self, shape, *, channel, **values):
        assert self.rig.enabled
        self.values["shape", channel] = shape
        self.values["wave", channel] = values

    def set_pulse_dcycle(self, value, *, channel):
        assert self.rig.enabled, "占空比设置必须在关温控之前"
        self.rig.duty = value / 100
        self.rig.events.append(("configure", value))
        self.values["pulse_dcycle", channel] = value

    def __getattr__(self, name):
        supported = {"output", "burst_state", "mod_state", "sync_state", "sync_polarity", "high_level", "low_level",
                     "burst_mode", "burst_ncycles", "burst_period", "burst_trigger_source"}
        action, _, key = name.partition("_")
        if key not in supported or action not in ("get", "set"):
            raise AttributeError(name)
        if action == "get":
            return lambda *, channel: self.values.get((key, channel), False)
        def setter(value, *, channel):
            temp = self.rig.mapping["Temp_Switch"]
            is_temp_output = key == "output" and value is True and self.resource == temp["resource"] and channel == temp["channel"]
            assert self.rig.enabled or self.rig.restore_attempted or is_temp_output
            self.values[key, channel] = value
        return setter


class FakeScope:
    def __init__(self, rig):
        self.rig, self.scales, self.values = rig, {}, {}
        self.connections = self.disconnections = 0
        self.stopped = False
    def connect(self): self.connections += 1
    def disconnect(self): self.disconnections += 1
    def trigger_run(self):
        assert self.rig.enabled
        self.stopped = False
        self.rig.events.append(("arm", None))
    def trigger_stop(self):
        self.stopped = True
        self.rig.events.append(("stop", None))
    def wait_for_trigger(self, **kwargs): return not self.rig.trigger_timeout
    def get_actual_points(self): return round(self.rig.params.total_acq_s * self.rig.params.sds_sample_rate)
    def get_sampling_rate(self): return self.rig.params.sds_sample_rate
    def get_timebase_scale(self): return self.rig.params.total_acq_s / 10
    def get_channel_scale(self, channel): return self.scales[channel]
    def set_channel_scale(self, channel, value):
        assert self.rig.enabled
        self.scales[channel] = value
    def __getattr__(self, name):
        allowed = {"set_sampling_rate", "set_timebase_scale", "set_timebase_delay", "set_acquire_type", "set_channel_state",
            "set_channel_offset", "set_channel_coupling", "set_channel_impedance", "set_channel_probe", "set_trigger_mode",
            "set_trigger_type", "set_trigger_source", "set_trigger_slope", "set_trigger_level"}
        if name not in allowed: raise AttributeError(name)
        def setter(*args):
            assert self.rig.enabled
            self.values[name] = args
        return setter


def install_rig(root, monkeypatch, params=None):
    rig = SimpleNamespace(params=params or small_params(), mapping=load_mapping(root), enabled=True, restore_attempted=True,
        events=[], duty=0.02, reads=0, cancel=False, cancel_after_read=False, restore_error=False,
        acquire_error=None, fail_at=2, clock_error=False, trigger_timeout=False, bad_record=False)
    rig.generators = {cfg["resource"]: FakeDG(rig, cfg["resource"]) for cfg in rig.mapping.values() if cfg["instrument"] == "signal_generator"}
    rig.scope, rig.gs, rig.tec = FakeScope(rig), Mock(), Mock()
    rig.tec.set_enable.side_effect = AssertionError("普通采集不能启停 TEC")
    rig.tec.get_temperature.side_effect = AssertionError("普通采集不能判稳")
    monkeypatch.setattr(preparation, "GS200Instrument", lambda *a: rig.gs)
    monkeypatch.setattr(preparation, "TECInstrument", lambda **kw: rig.tec)
    monkeypatch.setattr(preparation, "SDSInstrument", lambda *a: rig.scope)
    monkeypatch.setattr(routed_signal_generator, "create_signal_generator", lambda cfg: rig.generators[cfg["resource"]])
    original_check = workflow.check_cancelled
    def check():
        original_check()
        if rig.cancel: raise WorkflowCancelled("模拟采集取消")
    def wait(seconds, *, cancellable=True):
        rig.events.append(("settle" if not rig.enabled else "recovery", seconds))
        if cancellable: check()
    monkeypatch.setattr(preparation, "sleep", wait)
    monkeypatch.setattr(workflow, "check_cancelled", check)
    monkeypatch.setattr(acquisition, "check_cancelled", check)
    def read(acquirer, *args, **kwargs):
        assert not rig.enabled and rig.scope.stopped
        rig.reads += 1
        rig.events.append(("acquire", rig.reads))
        if rig.acquire_error is not None and rig.reads == rig.fail_at:
            raise rig.acquire_error
        t, y = signal(rig.params, rig.duty)
        if rig.bad_record: y[5] = np.nan
        if rig.cancel_after_read: rig.cancel = True
        return SimpleNamespace(time=t, voltage=y)
    monkeypatch.setattr(preparation.SDSAcquisition, "acquire_channel", read)
    original_save, original_text = np.savez, Path.write_text
    def save(path, *args, **kwargs):
        assert rig.enabled or rig.restore_attempted, "波形写盘不得早于温控恢复尝试"
        rig.events.append(("save", Path(path).name))
        return original_save(path, *args, **kwargs)
    def text(path, *args, **kwargs):
        assert rig.enabled or rig.restore_attempted, "配置、状态和分析写入不得藏在关温控窗口"
        return original_text(path, *args, **kwargs)
    monkeypatch.setattr(np, "savez", save)
    monkeypatch.setattr(Path, "write_text", text)
    return rig


@pytest.fixture
def rig(tmp_path, monkeypatch):
    copy_config(tmp_path)
    return install_rig(tmp_path, monkeypatch)


def prepare(root, rig):
    ctx = workflow.Workflow(rig.params, project_root=root)
    ctx.execute([f"A{i:02d}" for i in range(1, 7)])
    return ctx


def test_each_shot_six_steps_real_files_and_locked_repeats(tmp_path, monkeypatch, rig):
    ctx = prepare(tmp_path, rig)
    original_save = np.savez
    def slow(*args, **kwargs):
        assert rig.enabled
        result = original_save(*args, **kwargs)
        # 慢磁盘发生在恢复后，不能延长关温控窗口。
        import time
        time.sleep(0.002)
        return result
    monkeypatch.setattr(np, "savez", slow)
    ctx.execute(["A07"])
    for i, duty in enumerate(rig.params.duty_cycle_list):
        with np.load(ctx.directory.raw / f"shot_d{i:02d}_avg.npz") as average:
            parts = []
            for rep in range(rig.params.acq_repeats):
                with np.load(ctx.directory.raw / f"shot_d{i:02d}_r{rep}.npz") as shot:
                    assert shot["temperature_restored"] and shot["accepted"]
                    assert float(shot["scale_used"]) == float(average["scale_used"])
                    parts.append(shot["signal"])
            np.testing.assert_array_equal(average["signal"], np.mean(parts, axis=0))
            assert float(average["duty_cycle"]) == duty
    for pos, (kind, _) in enumerate(rig.events):
        if kind != "off": continue
        on = next(i for i in range(pos + 1, len(rig.events)) if rig.events[i][0] == "on")
        assert "save" not in [event[0] for event in rig.events[pos:on]]
        assert rig.events[pos - 1][0] == "recovery"
        assert any(event[0] == "configure" for event in rig.events[:pos])
        assert rig.events[pos + 1] == ("settle", rig.params.system_settle_s)
        assert any(event[0] == "acquire" for event in rig.events[pos:on])
    assert all(d.connections == 1 for d in rig.generators.values() if d.connections)
    ctx.finish()
    gate = rig.mapping["Time_sequence"]
    actual = rig.generators[gate["resource"]]
    assert actual.get_burst_state(channel=gate["channel"]) and actual.get_output(channel=gate["channel"])
    assert actual.get_sync_state(channel=gate["channel"]) and rig.enabled and rig.scope.stopped
    assert ctx.state["safety_shutdown"]["completed"]
    rig.tec.set_target_temperature.assert_called_once_with(rig.params.tec_temperature, channel=1)


def test_cancel_after_read_keeps_frame_and_stops(tmp_path, rig):
    ctx = prepare(tmp_path, rig)
    rig.cancel_after_read = True
    with pytest.raises(WorkflowCancelled, match="模拟采集取消") as error:
        ctx.execute(["A07"])
    ctx.finish(error.value)
    assert rig.enabled and rig.reads == 1
    with np.load(ctx.directory.raw / "probe_d00_a0.npz") as saved:
        assert saved["temperature_restored"] and not saved["accepted"]
        assert len(saved["signal"]) > 200
    assert not list(ctx.directory.raw.glob("*_avg.npz"))
    assert ctx.state["completion_status"] == "cancelled"


def test_restore_failure_keeps_read_frame(tmp_path, rig):
    ctx = prepare(tmp_path, rig)
    rig.restore_error = True
    with pytest.raises(OSError, match="模拟温控恢复失败") as error:
        ctx.execute(["A07"])
    ctx.finish(error.value)
    with np.load(ctx.directory.raw / "probe_d00_a0.npz") as saved:
        assert not saved["temperature_restored"] and not saved["accepted"]
        assert len(saved["signal"]) > 200
    assert rig.reads == 1 and ctx.states["A08"]["status"] == "failed"
    assert not ctx.state["safety_shutdown"]["completed"]


def test_acquire_failure_keeps_previous_and_primary_error(tmp_path, rig):
    # 初始档位无需探测重采，确保第二次读取对应 r1，而非第二次探测。
    rig.params = replace(rig.params, sds_volt_scale_pd=0.05)
    ctx = prepare(tmp_path, rig)
    original = WorkflowCancelled("原始采集错误")
    rig.acquire_error = original
    # 第一帧先成功；第二帧采集和随后的恢复同时失败。
    old = preparation.set_temp
    def switch(ctx, enabled, **kw):
        if rig.reads >= 2: rig.restore_error = True
        return old(ctx, enabled, **kw)
    from unittest.mock import patch
    with patch.object(preparation, "set_temp", side_effect=switch):
        with pytest.raises(WorkflowCancelled) as error: ctx.execute(["A07"])
        assert error.value is original
        ctx.finish(original)
    assert (ctx.directory.raw / "shot_d00_r0.npz").is_file()
    assert not list(ctx.directory.raw.glob("*_avg.npz"))
    assert ctx.state["failure_reason"] == "原始采集错误"


def test_save_failure_keeps_previous_and_rejects_completion(tmp_path, monkeypatch, rig):
    ctx = prepare(tmp_path, rig)
    original = np.savez
    def fail(path, *args, **kwargs):
        if Path(path).name == "shot_d00_r1.npz": raise OSError("模拟慢盘写入失败")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(np, "savez", fail)
    with pytest.raises(OSError, match="模拟慢盘写入失败") as error: ctx.execute(["A07"])
    ctx.finish(error.value)
    assert rig.enabled and (ctx.directory.raw / "shot_d00_r0.npz").is_file()
    assert not ctx.round_sealed and ctx.state["completion_status"] == "failed"


def test_full_stepwise_rerun_and_frozen_snapshot(tmp_path, rig):
    ctx = prepare(tmp_path, rig)
    ctx.execute(["A07"])
    first = ctx.directory.root
    before = {p.relative_to(first).as_posix(): p.read_bytes() for p in [first / "experiment_config.yaml", *first.glob("raw/*")]}
    assert ctx.states["A06"]["status"] == "stale"
    with pytest.raises(ValueError, match="A06"): ctx.execute(["A07"])
    ctx.execute(["A06", "A07"])
    assert first != ctx.directory.root
    assert all((first / name).read_bytes() == data for name, data in before.items())
    ctx.finish()
    full = workflow.run(rig.params, project_root=tmp_path)
    for i in range(len(rig.params.duty_cycle_list)):
        with np.load(full / f"raw/shot_d{i:02d}_avg.npz") as a, np.load(first / f"raw/shot_d{i:02d}_avg.npz") as b:
            np.testing.assert_array_equal(a["signal"], b["signal"])


def test_dependencies_impacts_preflight_and_mode_drift(tmp_path, rig):
    ctx = workflow.Workflow(rig.params, project_root=tmp_path)
    with pytest.raises(ValueError, match="前置"): ctx.execute(["A07"])
    assert not rig.gs.method_calls and not rig.scope.connections
    ctx.execute([f"A{i:02d}" for i in range(1, 7)])
    previous = ctx.snapshot()["steps"]
    ctx.execute(["A01"])
    assert ctx.snapshot()["steps"] == previous
    ctx.update_parameters({"POST_DARK_TIME_S": 0.002, "ACQ_REPEATS": 3})
    affected = set(stages.parameter_impacts(stages.ACQUISITION)["POST_DARK_TIME_S"])
    assert affected == {"A06", "A07"}
    assert ctx.states["A05"]["status"] == "completed" and ctx.states["A06"]["status"] == "stale"
    ctx.execute(["A06"])
    gate = rig.mapping["Time_sequence"]
    rig.generators[gate["resource"]].set_burst_state(False, channel=gate["channel"])
    with pytest.raises(ValueError, match="模式已改变"): ctx.execute(["A07"])
    ctx.finish()


def test_clock_failure_and_partial_connection_cleanup(tmp_path, rig):
    ctx = workflow.Workflow(rig.params, project_root=tmp_path)
    rig.clock_error = True
    with pytest.raises(RuntimeError): ctx.execute(["A01", "A02", "A03"])
    ctx.finish()
    assert not rig.gs.set_current.called and rig.scope.disconnections == 1
    assert all(d.disconnections == 1 for d in rig.generators.values() if d.connections)


def test_partial_connection_failure_closes_every_connected_resource(tmp_path, monkeypatch, rig):
    def fail():
        raise OSError("模拟 SDS 连接失败")
    monkeypatch.setattr(rig.scope, "connect", fail)
    with pytest.raises(OSError, match="模拟 SDS 连接失败"):
        workflow.run(rig.params, project_root=tmp_path)
    assert all(d.disconnections == 1 for d in rig.generators.values() if d.connections)
    assert not rig.gs.set_current.called


def test_optional_tec_failure_and_lockin_only_monitor(tmp_path, monkeypatch, rig):
    rig.tec.connect.side_effect = OSError("模拟串口不可用")
    rig.params = replace(rig.params, use_hf2_monitor=True)
    lockin = Mock(model="MFLI", capabilities={"voltage_inputs": 1})
    lockin.iter_physical_routes = None  # 锁相不是信号源路由代理，拒绝 Mock 伪造该接口。
    lockin.sig_in_path.side_effect = lambda i: f"/fake/sigins/{i}"
    lockin.get_int.return_value = 0
    lockin.get_extclk.return_value = True
    monkeypatch.setattr(preparation, "create_lockin", lambda *a: lockin)
    ctx = prepare(tmp_path, rig)
    assert ctx.state["temperature_control"]["control_source"] == "external_software"
    assert ctx.state["temperature_control"]["actual_temperature_c"] is None
    rig.tec.set_target_temperature.assert_not_called()
    lockin.connect.assert_called_once()
    lockin.set_int.assert_any_call("/fake/sigins/0/ac", 0)
    lockin.set_int.assert_any_call("/fake/sigins/0/imp50", 0)
    assert not any("demods" in str(call) for call in lockin.method_calls)
    ctx.finish()


def test_nonfinite_read_frame_is_preserved_but_not_averaged(tmp_path, rig):
    ctx = prepare(tmp_path, rig)
    rig.bad_record = True
    with pytest.raises(acquisition.AcquisitionQualityError, match="非有限") as error:
        ctx.execute(["A07"])
    ctx.finish(error.value)
    assert rig.enabled and rig.reads == 1
    with np.load(ctx.directory.raw / "probe_d00_a0.npz") as saved:
        assert not saved["accepted"] and np.isnan(saved["signal"]).any()
    assert not list(ctx.directory.raw.glob("*_avg.npz"))
    assert ctx.states["A07"]["status"] == "quality_failed"


def test_invalid_parameters_before_connection(tmp_path, rig):
    for changes in ({"duty_cycle_list": [0.9]}, {"duty_cycle_list": []}, {"fixed_pump_power_voltage": 10},
                    {"sds_pd_channel": 4}, {"sds_sample_rate": 100}, {"system_settle_s": float("nan")},
                    {"pump_rf_gate_low_v": 1}, {"temp_switch_on_v": 4}, {"run_tag": "../escape"}):
        with pytest.raises((ValueError, TypeError)):
            workflow.Workflow(replace(rig.params, **changes), project_root=tmp_path)
    assert not rig.gs.method_calls and not rig.scope.connections


def test_wait_migration_yaml_and_actual_defaults(tmp_path, monkeypatch):
    from .definition import ADAPTER
    p = BBDutyScanBurstParams.from_external({"TEMP_SWITCH_OFF_DELAY_S": 0.7})
    assert p.system_settle_s == 0.7
    assert BBDutyScanBurstParams.from_external({"TEMP_SWITCH_OFF_DELAY_S": 0.7, "SYSTEM_SETTLE_S": 0}).system_settle_s == 0
    path = tmp_path / "defaults.yaml"
    p.save_yaml(path)
    assert BBDutyScanBurstParams.from_yaml(path).to_external() == p.to_external()
    saved = ROOT / "params/experiments/bb-duty-scan-burst.yaml"
    actual = BBDutyScanBurstParams.from_yaml(saved)
    preparation.preflight(actual, ROOT)
    copy_config(tmp_path)
    actual.save_yaml(path)
    monkeypatch.setattr(ADAPTER, "root", tmp_path)
    monkeypatch.setattr(ADAPTER, "defaults_path", path)
    assert ADAPTER.preflight({}) == []
    assert ADAPTER._resolved({"TEMP_SWITCH_OFF_DELAY_S": 0.9}).system_settle_s == 0.9
    replace(actual, duty_cycle_list=[0.9]).save_yaml(path)
    assert ADAPTER.preflight({})
    fields = actual.schema(ROOT)["fields"]
    owned = {key for s in (*stages.ACQUISITION, *stages.ANALYSIS) for key in (s.parameters if s.display_parameters is None else s.display_parameters)}
    assert owned == {f["name"] for f in fields}


def check_shot_restore_contract(root):
    """供原共享回归继续覆盖本实验；旧 PowerScan 的 AST 回归保持不变。"""
    for fault in (None, "restore", "acquire"):
        with pytest.MonkeyPatch.context() as mp:
            directory = root / str(fault)
            copy_config(directory)
            rig = install_rig(directory, mp)
            ctx = prepare(directory, rig)
            rig.restore_error = fault == "restore"
            rig.acquire_error, rig.fail_at = (WorkflowCancelled("模拟采集取消") if fault == "acquire" else None), 1
            if fault:
                with pytest.raises((OSError, WorkflowCancelled)) as error:
                    acquisition.capture(ctx, 0.02, "raw/recovery.npz")
                ctx.finish(error.value)
            else:
                acquisition.capture(ctx, 0.02, "raw/recovery.npz")
                ctx.finish()
            assert (ctx.directory.raw / "recovery.npz").is_file() == (fault != "acquire")
            assert rig.reads == 1
            kinds = [kind for kind, _ in rig.events]
            assert kinds.index("arm") < kinds.index("off") < kinds.index("acquire")
            assert kinds.index("acquire") < kinds.index("on", kinds.index("off"))


def fake_main():
    root = Path(os.environ["BB_DUTY_TEST_ROOT"])
    payload = json.loads(os.environ["LAB_TYPED_PARAMETERS"])
    session_id = payload.pop("session_id", None)
    params = BBDutyScanBurstParams.from_external(payload)
    with pytest.MonkeyPatch.context() as mp:
        install_rig(root, mp, params)
        if os.environ.get("BB_DUTY_TEST_FULL") == "1":
            workflow.run(params, project_root=root)
        else:
            from .session import serve
            serve(workflow.Workflow(params, project_root=root, session_id=session_id), sys.stdin)


if __name__ == "__main__":
    fake_main()
