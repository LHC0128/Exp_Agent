"""T2 真实采集与写盘的无硬件故障回归。"""

from pathlib import Path
import shutil
import time
from types import SimpleNamespace

import yaml

import numpy as np
import pytest

from . import workflow
from .models import T2CalibrationParams
from ...common import WorkflowCancelled
from ...steps.run_directory import RunDirectory


ACQUISITION_CASES = {
    "t2-calibration": {"mode": "single", "sources": [
        "lab_workflows/experiment_modules/t2_calibration/workflow.py",
    ], "cases": {
        "order": ["test_run_order_slow_disk_and_unchanged_fid"],
        "slow_disk": ["test_run_order_slow_disk_and_unchanged_fid"],
        "cancel": ["test_cancel_after_first_read_preserves_data", "test_cancel_during_settle_restores_before_shutdown"],
        "acquire_failure": ["test_read_and_restore_failures_keep_original_error", "test_later_read_failure_keeps_first_repeat"],
        "restore_failure": ["test_restore_failure_rescues_already_read_frame", "test_read_and_restore_failures_keep_original_error"],
        "save_failure": ["test_save_failure_preserves_prior_frame_and_stops"],
        "partial_save": ["test_restore_failure_rescues_already_read_frame", "test_later_read_failure_keeps_first_repeat",
                         "test_cancel_after_first_read_preserves_data", "test_save_failure_preserves_prior_frame_and_stops"],
        "entrypoints": ["test_main_uses_the_real_workflow", "test_full_and_stepwise_use_identical_fid"],
    }},
}


@pytest.fixture
def rig(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[3]
    (tmp_path / "params").mkdir()
    for name in ("devices.yaml", "mapping.yaml", "safety_limits.yaml"):
        shutil.copyfile(root / "params" / name, tmp_path / "params" / name)
    events = []
    state = {"enabled": True, "reads": 0, "fault": None, "original": OSError("原始采集错误")}
    time = np.arange(-20, 1980, dtype=float) / 1e6
    signal = 0.2 * np.exp(-np.maximum(time, 0) / 0.0005) * np.cos(2 * np.pi * 90000 * time)

    class Generator:
        def setup_dc(self, value, **kwargs):
            assert state["enabled"]
            events.append("configure")

        def set_output(self, enabled, **kwargs):
            assert state["enabled"]
            events.append("initial_output")

    class Scope:
        def wait_for_trigger(self, **kwargs):
            assert not state["enabled"]
            events.append("trigger")
            return True

        def trigger_stop(self):
            events.append("trigger_stop")

        def trigger_run(self):
            assert state["enabled"]
            events.append("trigger_run")

        def set_channel_scale(self, channel, scale):
            assert state["enabled"]
            events.append("scale")

    class Acquirer:
        def acquire_channel(self, *args, **kwargs):
            assert not state["enabled"]
            assert kwargs["trim_points"] == 0
            events.append("read")
            state["reads"] += 1
            if state["fault"] == "read_restore" or state["fault"] == "later_read" and state["reads"] == 2:
                raise state["original"]
            result = SimpleNamespace(time=time.copy(), voltage=signal.copy())
            state["last_result"] = result
            return result

    devices = {"laser": Generator(), "scope": Scope(), "acquirer": Acquirer(),
               "temp_switch": object(), "x_field": Generator(), "y_field": Generator(), "z_field": Generator()}
    channels = {"probe": 2, "temp_switch": 2, "x_field": 1, "y_field": 2, "z_field": 1}
    config = SimpleNamespace(acquire_delay=0.002, timebase_scale=0.0002, horizontal_divisions=10,
                             channels=[SimpleNamespace(scale=0.1) for _ in range(4)])

    def switch(device, enabled, **kwargs):
        events.append("on" if enabled else "off")
        if enabled and state["reads"] and state["fault"] in ("restore", "read_restore"):
            raise OSError("温控恢复错误")
        state["enabled"] = enabled

    def shutdown(*args):
        events.append("shutdown")
        state["enabled"] = True
        return SimpleNamespace(errors=[], disconnect_errors=[], to_dict=lambda: {"errors": []})

    original_savez = np.savez

    def save(path, *args, **kwargs):
        # 恢复失败仍需先尝试恢复，再抢救已经读取的帧。
        assert state["enabled"] or events[-1] == "on"
        events.append("save")
        return original_savez(path, *args, **kwargs)

    monkeypatch.setattr(workflow, "find_project_root", lambda: tmp_path)
    monkeypatch.setattr(workflow, "require_lockin_compatibility", lambda *args, **kwargs: None)
    monkeypatch.setattr(workflow, "_connect_devices", lambda *args: (devices, channels))
    monkeypatch.setattr(workflow, "synchronize_connected_clocks", lambda *args: {})
    monkeypatch.setattr(workflow, "_device_snapshot", lambda *args: {})
    monkeypatch.setattr(workflow, "_configure_outputs", lambda *args: {"hf2": {"actual_rate_readback_Sa_s": 10000}})
    monkeypatch.setattr(workflow, "_configure_scope", lambda *args: (config, {"actual_sample_rate_Sa_s": 1e6}))
    monkeypatch.setattr(workflow, "set_temperature_switch", switch)
    monkeypatch.setattr(workflow, "safe_shutdown", shutdown)
    monkeypatch.setattr(workflow, "_sleep", lambda seconds: events.append(("wait", seconds)))
    monkeypatch.setattr(workflow.np, "savez", save)
    params = T2CalibrationParams(do_power_scan=False, acq_repeats=2, scope_duration=0.002,
                                 system_settle_s=0.7, run_tag="fake_t2")
    return SimpleNamespace(root=tmp_path, events=events, state=state, params=params, time=time,
                           signal=signal, devices=devices, channels=channels, config=config)


def test_restore_failure_rescues_already_read_frame(rig):
    rig.state["fault"] = "restore"
    with pytest.raises(OSError, match="温控恢复错误"):
        workflow.run(rig.params)
    recovered = list((rig.root / "data").rglob("recovery/**/*.npz"))
    assert recovered, "已经读取的 FID 帧必须在尝试恢复温控后抢救到磁盘"
    assert rig.state["reads"] == 1
    assert rig.events.index("on", rig.events.index("read")) < rig.events.index("save")
    with np.load(recovered[0]) as saved:
        np.testing.assert_array_equal(saved["waveform_v"], rig.signal)
        np.testing.assert_array_equal(saved["time_s"], rig.time)


def test_read_and_restore_failures_keep_original_error(rig):
    rig.state["fault"] = "read_restore"
    with pytest.raises(OSError) as caught:
        workflow.run(rig.params)
    assert caught.value is rig.state["original"]
    assert rig.events[-1] == "shutdown"


def test_later_read_failure_keeps_first_repeat(rig):
    rig.state["fault"] = "later_read"
    with pytest.raises(OSError) as caught:
        workflow.run(rig.params)
    assert caught.value is rig.state["original"]
    recovered = list((rig.root / "data").rglob("recovery/**/*.npz"))
    assert len(recovered) == 1
    with np.load(recovered[0]) as saved:
        np.testing.assert_array_equal(saved["waveform_v"], rig.signal)


@pytest.mark.parametrize("power_scan", [False, True])
def test_run_order_slow_disk_and_unchanged_fid(rig, monkeypatch, power_scan):
    rig.params.do_power_scan = power_scan
    rig.params.probe_power_points = 2
    save = workflow.np.savez
    config_write = RunDirectory.update_config

    def slow_save(*args, **kwargs):
        assert rig.state["enabled"]
        time.sleep(0.01)
        return save(*args, **kwargs)

    def checked_config(self, **kwargs):
        assert rig.state["enabled"], "窗口内不能写入配置快照"
        rig.events.append("config_save")
        return config_write(self, **kwargs)

    monkeypatch.setattr(workflow.np, "savez", slow_save)
    monkeypatch.setattr(RunDirectory, "update_config", checked_config)
    directory = workflow.run(rig.params)
    expected_reads = 4 if power_scan else 2
    assert rig.state["reads"] == expected_reads
    assert len(list((directory / "raw/recovery").rglob("*.npz"))) == expected_reads
    for index, event in enumerate(rig.events):
        if event != "off":
            continue
        end = rig.events.index("on", index + 1)
        window = rig.events[index + 1:end]
        assert window == [("wait", 0.7), "trigger", ("wait", 0.002), "trigger_stop", "read"]
        assert rig.events[end + 1] == "save"
        assert "configure" in rig.events[:index]
    names = ["fid_p00.npz", "fid_p01.npz"] if power_scan else ["fid_waveforms.npz"]
    mask = rig.time >= -0.25e-6
    for name in names:
        with np.load(directory / "raw" / name) as saved:
            np.testing.assert_array_equal(saved["time"], rig.time[mask])
            np.testing.assert_array_equal(saved["avg_waveform"], rig.signal[mask])
            np.testing.assert_array_equal(saved["waveforms"], [rig.signal[mask]] * 2)
    config = yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf-8"))
    assert config["completion_status"] == "completed"
    assert config["recovery_directory"] == "raw/recovery"
    assert all("recovery" not in name for name in config["data_files"])
    assert rig.events.count("shutdown") == 1


def test_cancel_after_first_read_preserves_data(rig, monkeypatch):
    original = WorkflowCancelled("读取后取消")

    def check():
        if rig.state["reads"]:
            raise original

    monkeypatch.setattr(workflow, "check_cancelled", check)
    with pytest.raises(WorkflowCancelled) as caught:
        workflow.run(rig.params)
    assert caught.value is original
    assert rig.state["reads"] == 1
    assert len(list((rig.root / "data").rglob("recovery/**/*.npz"))) == 1
    assert rig.events[-1] == "shutdown"


def test_cancel_during_settle_restores_before_shutdown(rig, monkeypatch):
    original = WorkflowCancelled("稳定等待取消")

    def wait(seconds):
        if not rig.state["enabled"] and seconds == rig.params.system_settle_s:
            raise original

    monkeypatch.setattr(workflow, "_sleep", wait)
    with pytest.raises(WorkflowCancelled) as caught:
        workflow.run(rig.params)
    assert caught.value is original
    assert rig.state["reads"] == 0
    assert rig.events[-2:] == ["on", "shutdown"]
    assert not list((rig.root / "data").rglob("*.npz"))


def test_save_failure_preserves_prior_frame_and_stops(rig, monkeypatch):
    original = OSError("第二帧写盘失败")
    save = workflow.np.savez

    def failing_save(path, *args, **kwargs):
        if Path(path).name == "frame_r001_a0.npz":
            assert rig.state["enabled"]
            raise original
        return save(path, *args, **kwargs)

    monkeypatch.setattr(workflow.np, "savez", failing_save)
    with pytest.raises(OSError) as caught:
        workflow.run(rig.params)
    assert caught.value is original
    recovered = list((rig.root / "data").rglob("recovery/**/*.npz"))
    assert len(recovered) == 1
    with np.load(recovered[0]) as saved:
        np.testing.assert_array_equal(saved["waveform_v"], rig.signal)
    assert rig.events[-1] == "shutdown"


@pytest.mark.parametrize("secondary", ["shutdown", "config"])
def test_original_read_error_survives_secondary_cleanup_failure(rig, monkeypatch, secondary):
    rig.state["fault"] = "later_read"
    if secondary == "shutdown":
        def fail_shutdown(*args):
            raise OSError("二次收尾错误")
        monkeypatch.setattr(workflow, "safe_shutdown", fail_shutdown)
    else:
        update = RunDirectory.update_config

        def fail_config(self, **kwargs):
            if "safety_shutdown" in kwargs:
                raise OSError("二次配置保存错误")
            return update(self, **kwargs)

        monkeypatch.setattr(RunDirectory, "update_config", fail_config)
    with pytest.raises(OSError) as caught:
        workflow.run(rig.params)
    assert caught.value is rig.state["original"]
    assert len(list((rig.root / "data").rglob("recovery/**/*.npz"))) == 1


def test_shutdown_failure_is_not_reported_as_completed(rig, monkeypatch):
    monkeypatch.setattr(workflow, "safe_shutdown", lambda *args: SimpleNamespace(
        errors=["辅助输出关闭失败"], disconnect_errors=[], to_dict=lambda: {"errors": ["辅助输出关闭失败"]}))
    with pytest.raises(RuntimeError, match="安全关闭失败"):
        workflow.run(rig.params)
    config_path = next(rig.root.glob("data/T2_Calibration/*/experiment_config.yaml"))
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert config["completion_status"] == "failed"
    assert "辅助输出关闭失败" in config["failure_reason"]
    assert (config_path.parent / "raw/fid_waveforms.npz").is_file()


def test_restore_cannot_mutate_frozen_frame_or_averaged_fid(rig, monkeypatch):
    switch = workflow.set_temperature_switch

    def mutate_after_restore(device, enabled, **kwargs):
        switch(device, enabled, **kwargs)
        if enabled and "last_result" in rig.state:
            rig.state["last_result"].voltage[:] = 0
            rig.state["last_result"].time[:] = 0

    monkeypatch.setattr(workflow, "set_temperature_switch", mutate_after_restore)
    directory = workflow.run(rig.params)
    with np.load(directory / "raw/fid_waveforms.npz") as saved:
        np.testing.assert_array_equal(saved["avg_waveform"], rig.signal[rig.time >= 0])
    for path in (directory / "raw/recovery").rglob("*.npz"):
        with np.load(path) as saved:
            np.testing.assert_array_equal(saved["waveform_v"], rig.signal)
            np.testing.assert_array_equal(saved["time_s"], rig.time)


def test_auto_range_retries_only_after_restore_and_keeps_every_attempt(rig, monkeypatch):
    rig.params.acq_repeats = 1
    acquire = rig.devices["acquirer"].acquire_channel

    def read(*args, **kwargs):
        result = acquire(*args, **kwargs)
        result.voltage *= 0.005 if rig.state["reads"] == 1 else 0.75
        return result

    monkeypatch.setattr(rig.devices["acquirer"], "acquire_channel", read)
    directory = workflow.run(rig.params)
    assert rig.state["reads"] == 2
    assert len(list((directory / "raw/recovery").rglob("*.npz"))) == 2
    assert rig.events.index("on", rig.events.index("off")) < rig.events.index("scale")
    with np.load(directory / "raw/fid_waveforms.npz") as saved:
        np.testing.assert_array_equal(saved["avg_waveform"], (rig.signal * 0.75)[rig.time >= 0])


def test_trigger_timeout_never_reads_stale_frame(rig, monkeypatch):
    monkeypatch.setattr(rig.devices["scope"], "wait_for_trigger", lambda **kwargs: False)
    with pytest.raises(RuntimeError, match="未采集到有效 FID"):
        workflow.run(rig.params)
    assert rig.state["reads"] == 0
    assert rig.events.count("off") == rig.events.count("on") - 1
    assert rig.events[-1] == "shutdown"


def test_main_uses_the_real_workflow(rig, monkeypatch):
    monkeypatch.setattr(workflow, "load_runtime_params", lambda cls: rig.params)
    assert workflow.main() == 0
    assert len(list(rig.root.glob("data/T2_Calibration/*/raw/fid_waveforms.npz"))) == 1


def test_full_and_stepwise_use_identical_fid(rig):
    complete = workflow.run(rig.params)
    ctx = workflow.Workflow(rig.params)
    with pytest.raises(ValueError, match="缺少前置"):
        ctx.execute(["A06"])
    assert not ctx.devices
    for stage in ctx.stages:
        ctx.execute([stage.id])
    assert ctx.closed and ctx.directory.root != complete
    with np.load(complete / "raw/fid_waveforms.npz") as left, np.load(ctx.directory.raw / "fid_waveforms.npz") as right:
        assert left.files == right.files
        for key in left.files:
            np.testing.assert_array_equal(left[key], right[key])


def test_parameter_impacts_and_rerun_preserve_previous_parameters(rig):
    from .stages import definition
    ctx = workflow.Workflow(rig.params)
    ctx.execute([s.id for s in ctx.stages if s.id != "A07"])
    first = ctx.directory.root
    before = (first / "raw/fid_waveforms.npz").read_bytes()
    reads = rig.state["reads"]
    ctx.update_parameters({"ACQ_REPEATS": 3})
    impacts = definition()["parameter_impacts"]["acquisition"]["ACQ_REPEATS"]
    assert [key for key, state in ctx.states.items() if state["status"] == "stale"] == impacts == ["A06"]
    assert rig.state["reads"] == reads
    ctx.execute(["A06"])
    ctx.finish()
    assert ctx.directory.root != first and (first / "raw/fid_waveforms.npz").read_bytes() == before
    config = yaml.safe_load((first / "experiment_config.yaml").read_text(encoding="utf-8"))
    assert config["parameters"]["ACQ_REPEATS"] == 2


def test_hidden_scope_parameter_also_invalidates_preparation(rig):
    ctx = workflow.Workflow(rig.params)
    ctx.execute([s.id for s in ctx.stages if s.id != "A07"])
    ctx.update_parameters({"SCOPE_TRIG_SLOPE": "RISing"})
    assert ctx.states["A04"]["status"] == ctx.states["A05"]["status"] == ctx.states["A06"]["status"] == "stale"
    ctx.finish()
