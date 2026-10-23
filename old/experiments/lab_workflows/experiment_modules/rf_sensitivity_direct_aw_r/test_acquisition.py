"""R 变体真实工作流、假仪器与真实 writer 的采集时序证据。"""
from dataclasses import replace
from pathlib import Path
import numpy as np
import pytest
import yaml

from ..rf_sensitivity_direct_aw import acquisition, phase, _helpers
from ...common import WorkflowCancelled
from . import workflow
from .test_workflow import rig, control_source, rrig, through, assert_shutdown


ACQUISITION_CASES = {
    "rf-sensitivity-aw-r": {
        "mode": "grouped",
        "cases": {
            "order": ["test_real_r_response_and_x_noise_order_with_slow_disk", "test_real_phase_windows_restore_before_writes"],
            "slow_disk": ["test_real_r_response_and_x_noise_order_with_slow_disk"],
            "cancel": ["test_capture_failure_preserves_partial_data_and_primary_error", "test_phase_read_or_cancel_preserves_primary_on_restore_failure"],
            "acquire_failure": ["test_capture_failure_preserves_partial_data_and_primary_error", "test_phase_read_or_cancel_preserves_primary_on_restore_failure", "test_read_restore_and_write_failures_preserve_primary_and_previous_complete_group"],
            "restore_failure": ["test_restore_or_save_failure_stops_next_group_and_salvages_reads", "test_phase_read_or_cancel_preserves_primary_on_restore_failure", "test_read_restore_and_write_failures_preserve_primary_and_previous_complete_group"],
            "save_failure": ["test_restore_or_save_failure_stops_next_group_and_salvages_reads", "test_failed_response_replacement_preserves_last_complete_snapshot", "test_xy_phase_interrupted_atomic_save_keeps_initial_waveform", "test_read_restore_and_write_failures_preserve_primary_and_previous_complete_group"],
            "partial_save": ["test_capture_failure_preserves_partial_data_and_primary_error", "test_restore_or_save_failure_stops_next_group_and_salvages_reads", "test_failed_response_replacement_preserves_last_complete_snapshot", "test_read_restore_and_write_failures_preserve_primary_and_previous_complete_group"],
            "group_change": ["test_real_r_response_and_x_noise_order_with_slow_disk"],
            "last_group": ["test_real_r_response_and_x_noise_order_with_slow_disk"],
        },
        "sources": [
            "lab_workflows/experiment_modules/rf_sensitivity_direct_aw_r/workflow.py",
            "lab_workflows/experiment_modules/rf_sensitivity_direct_aw_r/session.py",
            "experiments/RF_Field_Sensitivity_AW_R.py",
        ],
    },
}


def artifact_path(value):
    path = Path(value.name if hasattr(value, "write") else value)
    return path.with_suffix("") if path.suffix == ".tmp" else path


def watch_writes(ctx, monkeypatch, enabled, on_write):
    """记录仍调用真实 NPZ 和配置 writer，避免 mock 掩盖窗口内写盘。"""
    real_save, real_update = np.savez, type(ctx.directory).update_config
    def save(path, *args, **kwargs):
        assert enabled[0], "恢复温控前不能保存波形"
        on_write(artifact_path(path))
        return real_save(path, *args, **kwargs)
    def update(directory, **kwargs):
        assert enabled[0], "恢复温控前不能保存采集元数据"
        on_write(directory.root / "experiment_config.yaml")
        return real_update(directory, **kwargs)
    monkeypatch.setattr(np, "savez", save)
    monkeypatch.setattr(type(ctx.directory), "update_config", update)


@pytest.mark.parametrize("stage", ["A13", "A14"])
def test_real_r_response_and_x_noise_order_with_slow_disk(rrig, monkeypatch, stage):
    rrig.params = replace(rrig.params, rf_amp_points=7, temp_group_size=3, noise_n_avg=3)
    ctx = workflow.Workflow(rrig.params)
    ctx.execute(through(stage)[:-1])
    enabled, clock, windows, reads, events = [True], [0.0], [], [], []
    switch = _helpers.set_temperature_switch
    def set_switch(device, value, **kwargs):
        switch(device, value, **kwargs)
        route = rrig.mapping["Temp_Switch"]
        source = rrig.generators[route["resource"]]
        assert source.dc[route["channel"]] == (5.0 if value else 0.0)
        assert source.output[route["channel"]] is True
        if not value:
            windows.append([clock[0], None])
        elif not enabled[0]:
            windows[-1][1] = clock[0]
        enabled[0] = value
        events.append("on" if value else "off")
    for module in (phase, acquisition, _helpers):
        monkeypatch.setattr(module, "set_temperature_switch", set_switch)
    def wait(seconds):
        if not enabled[0]:
            assert seconds == rrig.params.system_settle_s
            events.append("settle")
        clock[0] += seconds
    monkeypatch.setattr(acquisition, "wait", wait)
    real_z = acquisition.set_z_rf
    def set_z(*args, **kwargs):
        if stage == "A13" and len(reads) % 3 == 0:
            assert enabled[0], "组首实际配置必须在关温控之前"
        events.append("configure")
        return real_z(*args, **kwargs)
    monkeypatch.setattr(acquisition, "set_z_rf", set_z)
    def acquire(*args, **kwargs):
        assert not enabled[0] and events[-1] == "settle"
        reads.append(clock[0])
        events.append("read")
        result = rrig.acquire(*args, **kwargs)
        clock[0] += 1
        return result
    monkeypatch.setattr(acquisition.daq, "acquire_data", acquire)
    def writing(path):
        events.append("save")
        clock[0] += 30
    watch_writes(ctx, monkeypatch, enabled, writing)
    try:
        ctx.execute([stage])
        assert len(reads) == (7 if stage == "A13" else 3)
        sizes = [3, 3, 1] if stage == "A13" else [1, 1, 1]
        assert [end - start for start, end in windows] == pytest.approx([
            size * (rrig.params.system_settle_s + 1) for size in sizes])
        assert enabled[0] and events.index("configure") < events.index("off")
        config = yaml.safe_load((ctx.directory.root / "experiment_config.yaml").read_text(encoding="utf-8"))
        assert config["response_points_completed" if stage == "A13" else "noise_records_completed"] == len(reads)
        if stage == "A13":
            with np.load(ctx.directory.raw / "response_data.npz") as saved:
                assert saved["demod3_r_series_V"].shape[0] == 7
                assert saved["z_rf_freq_Hz"] == 3020
        else:
            for path in ctx.directory.raw.glob("noise_D3_*.npz"):
                with np.load(path) as saved:
                    assert saved["z_rf_freq_Hz"] == saved["demod3_freq_Hz"] == 3000
    finally:
        ctx.finish()


@pytest.mark.parametrize("stage", ["A08", "A09", "A12"])
def test_real_phase_windows_restore_before_writes(rrig, monkeypatch, stage):
    ctx = workflow.Workflow(rrig.params)
    ctx.execute(through(stage)[:-1])
    enabled, records = [True], []
    switch = _helpers.set_temperature_switch
    def set_switch(device, value, **kwargs):
        switch(device, value, **kwargs)
        enabled[0] = value
        records.append("on" if value else "off")
    for module in (phase, acquisition, _helpers):
        monkeypatch.setattr(module, "set_temperature_switch", set_switch)
    read = ctx.devices["hf2"].get_sample
    def get_sample(path):
        assert not enabled[0]
        records.append("read")
        return read(path)
    monkeypatch.setattr(ctx.devices["hf2"], "get_sample", get_sample)
    watch_writes(ctx, monkeypatch, enabled, lambda path: records.append("save"))
    try:
        ctx.execute([stage])
        assert records.index("off") < records.index("read") < records.index("on")
        assert "save" in records[records.index("on") + 1:]
    finally:
        ctx.finish()


@pytest.mark.parametrize("stage", ["A13", "A14"])
@pytest.mark.parametrize("cancel,restore_error", [(True, False), (False, False), (False, True)])
def test_capture_failure_preserves_partial_data_and_primary_error(rrig, monkeypatch, stage, cancel, restore_error):
    rrig.params = replace(rrig.params, temp_group_size=2, noise_n_avg=3)
    ctx = workflow.Workflow(rrig.params)
    ctx.execute(through(stage)[:-1])
    primary = WorkflowCancelled("R 变体采集取消") if cancel else RuntimeError("R 变体采集主错误")
    attempts, reads, saved, failed = [], [], [], [False]
    switch, save = _helpers.set_temperature_switch, np.savez
    def set_switch(device, enabled, **kwargs):
        switch(device, enabled, **kwargs)
        attempts.append(enabled)
        if enabled and restore_error and failed[0]:
            raise OSError("恢复温控故障")
    for module in (phase, acquisition, _helpers):
        monkeypatch.setattr(module, "set_temperature_switch", set_switch)
    def acquire(*args, **kwargs):
        if reads:
            failed[0] = True
            raise primary
        result = rrig.acquire(*args, **kwargs)
        reads.append(1)
        return result
    monkeypatch.setattr(acquisition.daq, "acquire_data", acquire)
    def persist(path, *args, **kwargs):
        assert attempts[-1] is True, "必须先尝试恢复温控再抢救部分数据"
        result = save(path, *args, **kwargs)
        saved.append(artifact_path(path))
        return result
    monkeypatch.setattr(np, "savez", persist)
    try:
        with pytest.raises(type(primary)) as caught:
            ctx.execute([stage])
        assert caught.value is primary
        assert len(reads) == 1 and saved
        with np.load(saved[-1]) as data:
            assert data["amplitudes_V"].shape == (1,) if stage == "A13" else data["noise_x_V"].size >= 2
    finally:
        ctx.finish(primary)
    assert ctx.state["failure_reason"] == str(primary)
    assert_shutdown(rrig)


def test_read_restore_and_write_failures_preserve_primary_and_previous_complete_group(rrig, monkeypatch):
    """读取、恢复与覆盖写盘同时失败，先前完整组和原始错误仍保留。"""
    rrig.params = replace(rrig.params, rf_amp_points=7, temp_group_size=3)
    ctx = workflow.Workflow(rrig.params)
    ctx.execute(through("A13")[:-1])
    primary = RuntimeError("第五点读取主错误")
    reads, events, snapshot = [], [], []
    real_switch, real_save = _helpers.set_temperature_switch, np.savez
    def switch(device, enabled, **kwargs):
        real_switch(device, enabled, **kwargs)
        events.append("on" if enabled else "off")
        if enabled and len(reads) == 5:
            raise OSError("部分组恢复温控故障")
    def acquire(*args, **kwargs):
        reads.append(1)
        if len(reads) == 5:
            raise primary
        return rrig.acquire(*args, **kwargs)
    def save(path, *args, **kwargs):
        assert events[-1] == "on"
        points = len(kwargs.get("amplitudes_V", ()))
        if points == 4:
            events.append("save_failed")
            path.write(b"interrupted fourth point")
            path.flush()
            raise OSError("部分组原子写盘故障")
        result = real_save(path, *args, **kwargs)
        if points == 3:
            path.flush()
            snapshot.append(Path(path.name).read_bytes())
        return result
    try:
        with monkeypatch.context() as scoped:
            for module in (phase, acquisition, _helpers):
                scoped.setattr(module, "set_temperature_switch", switch)
            scoped.setattr(acquisition.daq, "acquire_data", acquire)
            scoped.setattr(np, "savez", save)
            with pytest.raises(RuntimeError) as caught:
                ctx.execute(["A13"])
            assert caught.value is primary
        assert len(reads) == 5 and "save_failed" in events
        path = ctx.directory.raw / "response_data.npz"
        assert path.read_bytes() == snapshot[0]
        with np.load(path, allow_pickle=False) as saved:
            assert saved["amplitudes_V"].shape == (3,)
        assert not list(ctx.directory.raw.glob("*.tmp"))
        assert ctx.states["A13"]["status"] == "failed"
        with pytest.raises(ValueError, match="缺少前置"):
            ctx.execute(["A12"])
    finally:
        ctx.finish(primary)
    assert ctx.state["failure_reason"] == str(primary)
    assert_shutdown(rrig)


@pytest.mark.parametrize("stage", ["A13", "A14"])
@pytest.mark.parametrize("fault", ["restore", "save"])
def test_restore_or_save_failure_stops_next_group_and_salvages_reads(rrig, monkeypatch, stage, fault):
    rrig.params = replace(rrig.params, temp_group_size=2, noise_n_avg=3)
    ctx = workflow.Workflow(rrig.params)
    ctx.execute(through(stage)[:-1])
    attempts, captured, saved = [], [], []
    real_switch, real_save = _helpers.set_temperature_switch, np.savez
    disk_failed = False
    def switch(device, enabled, **kwargs):
        real_switch(device, enabled, **kwargs)
        attempts.append(enabled)
        if enabled and fault == "restore":
            raise OSError("R 温控恢复故障")
    def acquire(*args, **kwargs):
        result = rrig.acquire(*args, **kwargs)
        captured.append(1)
        return result
    def save(path, *args, **kwargs):
        nonlocal disk_failed
        assert attempts[-1] is True
        if fault == "save" and not disk_failed and (stage == "A13" or len(captured) == 2):
            disk_failed = True
            raise OSError("R 写盘故障")
        result = real_save(path, *args, **kwargs)
        saved.append(artifact_path(path))
        return result
    primary = None
    try:
        with monkeypatch.context() as scoped:
            for module in (phase, acquisition, _helpers):
                scoped.setattr(module, "set_temperature_switch", switch)
            scoped.setattr(acquisition.daq, "acquire_data", acquire)
            scoped.setattr(np, "savez", save)
            with pytest.raises(OSError, match="R 温控恢复故障|R 写盘故障") as caught:
                ctx.execute([stage])
            primary = caught.value
        assert len(captured) == (2 if stage == "A13" or fault == "save" else 1)
        assert saved
        with np.load(saved[-1]) as data:
            assert data.files
        assert ctx.states[stage]["status"] == "failed"
    finally:
        ctx.finish(primary)
    assert_shutdown(rrig)


def test_failed_response_replacement_preserves_last_complete_snapshot(rrig, monkeypatch):
    """第二次真实写盘中断时，已有第一点快照必须仍可读取。"""
    rrig.params = replace(rrig.params, temp_group_size=2)
    ctx = workflow.Workflow(rrig.params)
    ctx.execute(through("A13")[:-1])
    real_save = np.savez
    primary = OSError("第二点落盘中断")
    def interrupted(path, *args, **kwargs):
        if len(kwargs.get("amplitudes_V", ())) == 2:
            if hasattr(path, "write"):
                path.write(b"incomplete npz")
                path.flush()
            else:
                Path(path).write_bytes(b"incomplete npz")
            raise primary
        return real_save(path, *args, **kwargs)
    monkeypatch.setattr(np, "savez", interrupted)
    try:
        with pytest.raises(OSError) as caught:
            ctx.execute(["A13"])
        assert caught.value is primary
        with np.load(ctx.directory.raw / "response_data.npz", allow_pickle=False) as saved:
            assert saved["amplitudes_V"].shape == (1,)
            assert np.all(np.isfinite(saved["demod3_r_V"]))
    finally:
        ctx.finish(primary)


@pytest.mark.parametrize("stage", ["A08", "A09", "A12"])
@pytest.mark.parametrize("cancel", [False, True])
def test_phase_read_or_cancel_preserves_primary_on_restore_failure(rrig, monkeypatch, stage, cancel):
    ctx = workflow.Workflow(rrig.params)
    ctx.execute(through(stage)[:-1])
    if stage == "A09":
        rrig.xy_angle = 0.2
    primary = WorkflowCancelled("R 校相取消") if cancel else RuntimeError("R 校相读取故障")
    enabled, failed, count, records = [True], [False], [0], []
    switch, sample = _helpers.set_temperature_switch, ctx.devices["hf2"].get_sample
    def set_switch(device, value, **kwargs):
        switch(device, value, **kwargs)
        enabled[0] = value
        records.append("on" if value else "off")
        if value and failed[0]:
            raise OSError("校相恢复温控故障")
    for module in (phase, acquisition, _helpers):
        monkeypatch.setattr(module, "set_temperature_switch", set_switch)
    def read(path):
        assert not enabled[0]
        count[0] += 1
        records.append("read")
        if count[0] == (2 if stage == "A09" else 1):
            failed[0] = True
            raise primary
        return sample(path)
    monkeypatch.setattr(ctx.devices["hf2"], "get_sample", read)
    watch_writes(ctx, monkeypatch, enabled, lambda path: records.append("save"))
    try:
        with pytest.raises(type(primary)) as caught:
            ctx.execute([stage])
        assert caught.value is primary
        assert records.index("off") < records.index("read") < records.index("on")
        assert "save" in records[records.index("on") + 1:]
        assert ctx.states[stage]["status"] == "failed"
        if stage == "A09":
            config = yaml.safe_load((ctx.directory.root / "experiment_config.yaml").read_text(encoding="utf-8"))
            assert len(config["xy_phase_calibration"]["history"]) == 1
            with np.load(ctx.directory.raw / "xy_aw_waveforms.npz") as saved:
                assert saved.files
    finally:
        ctx.finish(primary)
    assert ctx.state["failure_reason"] == str(primary)
    assert_shutdown(rrig)


def test_xy_phase_interrupted_atomic_save_keeps_initial_waveform(rrig, monkeypatch):
    ctx = workflow.Workflow(rrig.params)
    ctx.execute(through("A08"))
    save, switch = np.savez, _helpers.set_temperature_switch
    enabled, attempts = [True], []
    primary = OSError("XY 校相写盘中断")
    def set_switch(device, value, **kwargs):
        switch(device, value, **kwargs)
        enabled[0] = value
        attempts.append(value)
    for module in (phase, acquisition, _helpers):
        monkeypatch.setattr(module, "set_temperature_switch", set_switch)
    def interrupted(path, *args, **kwargs):
        assert enabled[0]
        if artifact_path(path).name == "xy_aw_waveforms.npz":
            assert attempts[-1] is True
            if hasattr(path, "write"):
                path.write(b"interrupted")
                path.flush()
            else:
                Path(path).write_bytes(b"interrupted")
            raise primary
        return save(path, *args, **kwargs)
    monkeypatch.setattr(np, "savez", interrupted)
    try:
        with pytest.raises(OSError) as caught:
            ctx.execute(["A09"])
        assert caught.value is primary
        with np.load(ctx.directory.raw / "xy_aw_waveforms_initial.npz") as saved:
            assert saved.files
        assert not (ctx.directory.raw / "xy_aw_waveforms.npz").exists()
        assert not list(ctx.directory.raw.glob("*.tmp"))
    finally:
        ctx.finish(primary)
