"""真实 DC 业务配假仪器；所有文件只写入临时目录。"""
from contextlib import contextmanager
from dataclasses import replace
import json
import os
from pathlib import Path
import time
from unittest.mock import patch

import numpy as np
import pytest
import yaml

from ...common import WorkflowCancelled
from ...experiment_runtime import load_runtime_params
from ...steps import dispersion
from lockin_amplifier import daq
from ..static_sensitivity import acquisition as dc, workflow as point_workflow
from ..static_sensitivity.test_support import fake_rig
from . import acquisition, preparation, run_format, workflow
from .definition import ADAPTER
from .models import StaticSensitivityOptimizeLocalParams as Params
from .stages import ACQUISITION, FINISH_ID, definition

ACQUISITION_CASES = {
    "static-sensitivity-optimize-local": {
        "mode": "grouped",
        "sources": ["lab_workflows/experiment_modules/static_sensitivity_optimize_local/workflow.py",
            "lab_workflows/experiment_modules/static_sensitivity_optimize_local/acquisition.py",
            "lab_workflows/experiment_modules/static_sensitivity_optimize_local/preparation.py",
            "lab_workflows/experiment_modules/static_sensitivity/workflow.py"],
        "cases": {
            "order": ["test_real_grouped_dc_order_and_slow_disk"],
            "slow_disk": ["test_real_grouped_dc_order_and_slow_disk"],
            "group_change": ["test_real_grouped_dc_order_and_slow_disk"],
            "last_group": ["test_real_grouped_dc_order_and_slow_disk"],
            "cancel": ["test_real_failure_partial_save_and_original_error", "test_noise_failure_saves_read_records_and_stops_candidates"],
            "acquire_failure": ["test_real_failure_partial_save_and_original_error", "test_noise_failure_saves_read_records_and_stops_candidates"],
            "restore_failure": ["test_real_failure_partial_save_and_original_error", "test_noise_failure_saves_read_records_and_stops_candidates"],
            "save_failure": ["test_real_failure_partial_save_and_original_error", "test_noise_failure_saves_read_records_and_stops_candidates"],
            "partial_save": ["test_real_failure_partial_save_and_original_error", "test_noise_failure_saves_read_records_and_stops_candidates"],
            "entrypoints": ["test_complete_and_staged_identical_and_public_child_progress"],
        },
    },
}
REGRESSION_CASES = {
    "static-sensitivity-optimize-local": {"parameters": {
        "sources": ["lab_workflows/experiment_modules/static_sensitivity_optimize_local/models.py",
            "lab_workflows/experiment_modules/static_sensitivity_optimize_local/definition.py",
            "params/experiments/static-sensitivity-optimize-local.yaml"],
        "tests": ["test_actual_defaults_migration_roundtrip_and_candidate_safety"],
    }},
}


def small_params(values=(.3,)):
    return Params.from_external({"OPTIMIZE_SEQUENCE": [{"parameter": "Pump_laser_power", "values": list(values)}],
        "DISPERSION_STEPS": 9, "DISPERSION_DURATION_S": .1, "RAMP_LOW": -.1, "RAMP_HIGH": .1,
        "NOISE_N_AVG": 1, "NOISE_DURATION": .1, "HF2_NOISE_RATE": 10000.,
        "DISPERSION_TEMP_SWITCH_INTERVAL_POINTS": 4})


@contextmanager
def local_rig(root, params=None, *, real_analysis=False):
    params = params or small_params()
    with fake_rig(root, params.point_parameters()) as rig:
        rig.local_params = params
        with patch.object(workflow, "find_project_root", return_value=Path(root)):
            if real_analysis:
                yield rig
            else:
                with patch.object(acquisition, "analyze_point", return_value=dict(success=True, fit_valid=True,
                        sensitivity_flat_fT_per_sqrt_Hz=100., fit_r_squared=.99, fit_gamma_fT=1e7, fit_slope_V_per_fT=1e-8)):
                    yield rig


def through(ctx, last="A05"):
    return ctx.execute([stage.id for stage in ACQUISITION if stage.id <= last])


def test_actual_defaults_migration_roundtrip_and_candidate_safety(tmp_path, monkeypatch):
    saved = Params.from_yaml(ADAPTER.defaults_path)
    from ...lockin_acquisition import experiment_acquisition_requests
    requests = experiment_acquisition_requests("static-sensitivity-optimize-local", saved.to_external())
    assert len(requests) == 2 and requests[0].duration == saved.dispersion_duration_s
    assert requests[0].signals == ("sample.x", "sample.y")
    assert requests[0].trigger_type == 0 and requests[0].extra_paths == ()
    with patch.object(point_workflow.preparation, "connect_devices", side_effect=AssertionError("预检不得连接")):
        assert ADAPTER.preflight({}) == []
        for params in (replace(saved, baseline_params={**saved.baseline_params, "Pump_laser_power": 2.}),
                replace(saved, optimize_sequence=json.dumps([{"parameter": "Probe_laser_power", "values": [2.]}])),
                replace(saved, dispersion_steps=6), replace(saved, dispersion_duration_s=0),
                replace(saved, gs200_current_range_a=.0001)):
            assert params.validate()
    old = saved.to_external()
    old.pop("SYSTEM_SETTLE_S")
    old["OPTIMIZE_SEQUENCE"] = [{"parameter": "Pump_laser_power", "values": [.3], "stabilize_time": 4.}]
    old["BASELINE_PARAMS"] |= {"Temp_Switch": 5., "Time_sequence": 10., "Time_sequence_2": 0.}
    migrated = Params.from_external(old, schema_version=2)
    assert migrated.system_settle_s == 4. and "Time_sequence" not in migrated.baseline_params
    assert Params.from_external(old | {"SYSTEM_SETTLE_S": .8}).system_settle_s == .8
    assert ADAPTER._resolved({"OPTIMIZE_SEQUENCE": old["OPTIMIZE_SEQUENCE"]}).system_settle_s == 4.
    path = tmp_path / "defaults.yaml"
    migrated.save_yaml(path)
    assert Params.from_yaml(path).to_external() == migrated.to_external()
    unsafe = replace(migrated, optimize_sequence=json.dumps([{"parameter": "Pump_laser_power", "values": [99.]}]))
    unsafe.save_yaml(path)
    monkeypatch.setattr(ADAPTER, "defaults_path", path)
    assert ADAPTER.preflight({})
    with patch.object(ADAPTER, "_execute", side_effect=AssertionError("不能启动子进程")), pytest.raises(ValueError):
        ADAPTER.run({}, None, None)


@pytest.mark.parametrize("delay", [0., .002])
def test_real_grouped_dc_order_and_slow_disk(tmp_path, monkeypatch, delay):
    with local_rig(tmp_path) as rig:
        params = rig.local_params
        events = []
        temp_key = rig.mapping["Temp_Switch"]
        temp = rig.generators[temp_key["resource"]]
        temp_channel = temp_key["channel"]
        original_temp = temp.setup_dc
        def temp_dc(value, **kwargs):
            events.append(("gate", value))
            return original_temp(value, **kwargs)
        monkeypatch.setattr(temp, "setup_dc", temp_dc)
        point = dc.acquire_dispersion_point.side_effect
        def read(*args, **kwargs):
            assert temp.dc[temp_channel] == 0
            events.append(("read", kwargs["offset_v"]))
            return point(*args, **kwargs)
        dc.acquire_dispersion_point.side_effect = read
        original_scan, original_save = dispersion.save_scan, np.save
        def scan(path, data):
            assert temp.dc[temp_channel] == 5.
            events.append(("write", Path(path).name))
            time.sleep(delay)
            return original_scan(path, data)
        def noise(path, data, *args, **kwargs):
            assert temp.dc[temp_channel] == 5.
            events.append(("noise_write", str(path)))
            time.sleep(delay)
            return original_save(path, data, *args, **kwargs)
        monkeypatch.setattr(dispersion, "save_scan", scan)
        monkeypatch.setattr(dc, "save_scan", scan)
        monkeypatch.setattr(np, "save", noise)
        ctx = workflow.Workflow(params, project_root=tmp_path)
        try:
            through(ctx)
        finally:
            ctx.finish()
        rows = json.loads((ctx.directory.root / run_format.MANIFEST).read_text(encoding="utf-8"))["records"]
        child = ctx.directory.root.parent / rows[0]["run_id"]
        with np.load(child / "raw/dispersion_scan.npz") as scan_data:
            np.testing.assert_allclose(scan_data["offsets_v"], np.linspace(-.1, .1, 9))
            assert scan_data["completed_mask"].all()
        assert len(list((child / "raw/dispersion_steps").glob("point_*.npz"))) == 9
        segments, current = [], None
        for kind, value in events:
            if kind == "gate":
                if value == 0: current = []
                elif current is not None: segments.append(current); current = None
            elif current is not None:
                current.append(kind)
        assert [part.count("read") for part in segments if "read" in part] == [4, 4, 1]
        assert not any("write" in part or "noise_write" in part for part in segments)
        assert any(kind == "noise_write" for kind, _ in events)
        timing = rig.mapping["Time_sequence_2"]
        assert not [event for event in rig.events if (event[0], event[2]) == (timing["resource"], timing["channel"])]
        assert not [event for event in rig.events if event[0] == temp.resource and event[1:] == ("set_output", temp_channel, False)]
        rig.tec.set_enable.assert_not_called()
        rig.tec.disconnect.assert_called_once()


@pytest.mark.parametrize("fault", ["cancel", "read", "restore", "save"])
def test_real_failure_partial_save_and_original_error(tmp_path, monkeypatch, fault):
    with local_rig(tmp_path, small_params((.3, .4))) as rig:
        ctx = workflow.Workflow(rig.local_params, project_root=tmp_path)
        through(ctx, "A03")
        temp_cfg = rig.mapping["Temp_Switch"]
        temp = rig.generators[temp_cfg["resource"]]
        read = dc.acquire_dispersion_point.side_effect
        calls, restored, writes = [], [], []
        original_temp, original_save = temp.setup_dc, dispersion.save_scan
        def temp_dc(value, **kwargs):
            if value == 5 and calls:
                restored.append(True)
                if fault == "restore": raise RuntimeError("恢复原始错误")
            return original_temp(value, **kwargs)
        monkeypatch.setattr(temp, "setup_dc", temp_dc)
        def point(*args, **kwargs):
            if len(calls) == 1 and fault in ("cancel", "read"):
                raise WorkflowCancelled("取消原始错误") if fault == "cancel" else RuntimeError("读数原始错误")
            calls.append(kwargs["offset_v"])
            return read(*args, **kwargs)
        dc.acquire_dispersion_point.side_effect = point
        def save(path, data):
            assert restored, "即使恢复失败，也必须先尝试恢复，再抢救波形"
            writes.append(Path(path).name)
            if fault == "save" and Path(path).name == "point_0001.npz":
                raise OSError("写盘原始错误")
            return original_save(path, data)
        monkeypatch.setattr(dispersion, "save_scan", save)
        monkeypatch.setattr(dc, "save_scan", save)
        error = None
        try:
            with pytest.raises((WorkflowCancelled, RuntimeError, OSError), match="原始错误"):
                try:
                    ctx.execute(["A04"])
                except BaseException as exc:
                    error = exc
                    raise
        finally:
            ctx.finish(error)
        assert writes
        assert ctx.records[1]["status"] == "pending"
        row = ctx.records[0]
        child = ctx.directory.root.parent / row["run_id"]
        assert (child / "raw/dispersion_scan.npz").is_file()
        assert (child / "raw/dispersion_steps/point_0000.npz").is_file()
        config = yaml.safe_load((ctx.directory.root / "experiment_config.yaml").read_text(encoding="utf-8"))
        assert config["completion_status"] == ("cancelled" if fault == "cancel" else "failed")
        if fault == "restore":
            assert ctx.snapshot()["safety_shutdown"]["completed"] is False
        assert "原始错误" in config["failure_reason"]


def test_complete_and_staged_identical_and_public_child_progress(tmp_path, monkeypatch):
    data = []
    for index in range(2):
        root = tmp_path / str(index)
        root.mkdir()
        with local_rig(root) as rig:
            parent_events = []
            monkeypatch.setattr(workflow, "report_runtime_progress", lambda *args, **kwargs: parent_events.append(kwargs.get("data", {})))
            if index == 0:
                path = workflow.run(rig.local_params, project_root=root)
            else:
                ctx = workflow.Workflow(rig.local_params, project_root=root)
                for stage in ACQUISITION:
                    ctx.execute([stage.id])
                path = ctx.directory.root
            records = json.loads((path / run_format.MANIFEST).read_text(encoding="utf-8"))["records"]
            child = path.parent / records[0]["run_id"]
            with np.load(child / "raw/dispersion_scan.npz") as saved:
                data.append({key: saved[key] for key in ("offsets_v", "y_median", "completed_mask")})
            assert records[0]["status"] == "completed"
            assert all(set(event["workflow_session"]["steps"]) == {stage.id for stage in ACQUISITION}
                for event in parent_events if "workflow_session" in event)
    for key in data[0]:
        np.testing.assert_equal(data[0][key], data[1][key])


def test_dependency_impacts_new_directories_and_unapplied_hardware_values(tmp_path):
    with local_rig(tmp_path) as rig:
        ctx = workflow.Workflow(rig.local_params, project_root=tmp_path)
        with pytest.raises(ValueError, match="缺少前置"):
            ctx.execute(["A04"])
        assert not rig.events
        through(ctx)
        first = ctx.directory.root
        before = {path: path.read_bytes() for path in first.rglob("*") if path.is_file()}
        ctx.execute(["A01"])
        assert ctx.states["A04"]["status"] == "completed"
        ctx.update_parameters({"DISPERSION_DURATION_S": .2, "NOISE_N_AVG": 2})
        impacts = definition()["parameter_impacts"]["acquisition"]
        assert set(impacts["DISPERSION_DURATION_S"]) == {"A04", "A05"}
        assert ctx.states["A04"]["status"] == "stale"
        ctx.execute(["A04", "A05"])
        assert ctx.directory.root != first
        for path, content in before.items(): assert path.read_bytes() == content
        last = dict(rig.hf2.nodes)
        ctx.update_parameters({"HF2_DEMOD_IDX": 2, "HF2_DEMOD_TC": .3})
        ctx.finish()
        assert rig.hf2.get_double(rig.hf2.demod_path(2) + "/timeconstant") == last.get(rig.hf2.demod_path(2) + "/timeconstant", 0.)


def test_quality_failure_only_skips_point_not_hardware_errors(tmp_path):
    with local_rig(tmp_path, small_params((.3, .4))):
        with patch.object(acquisition, "analyze_point", side_effect=[ValueError("拟合质量失败"), dict(success=True, fit_valid=True, fit_gamma_fT=1e7)]):
            ctx = workflow.Workflow(small_params((.3, .4)), project_root=tmp_path)
            try:
                through(ctx)
                assert [row["status"] for row in ctx.records] == ["quality_failed", "completed"]
            finally:
                ctx.finish()


@pytest.mark.parametrize("fault", ["cancel", "read", "restore", "save"])
def test_noise_failure_saves_read_records_and_stops_candidates(tmp_path, monkeypatch, fault):
    params = replace(small_params((.3, .4)), noise_n_avg=2)
    with local_rig(tmp_path, params) as rig:
        ctx = workflow.Workflow(params, project_root=tmp_path)
        through(ctx, "A03")
        original_read = daq.acquire_data.side_effect
        temp_config = rig.mapping["Temp_Switch"]
        temp = rig.generators[temp_config["resource"]]
        original_temp, original_save = temp.setup_dc, np.save
        calls, attempts = [], []
        def read(*args, **kwargs):
            if calls and fault in ("read", "cancel"):
                raise WorkflowCancelled("噪声取消原始错误") if fault == "cancel" else RuntimeError("噪声读数原始错误")
            result = original_read(*args, **kwargs)
            calls.append(True)
            return result
        daq.acquire_data.side_effect = read
        def switch(value, **kwargs):
            if calls and value == 5.:
                attempts.append(True)
                if fault == "restore": raise RuntimeError("噪声恢复原始错误")
            return original_temp(value, **kwargs)
        def save(path, data, *args, **kwargs):
            assert attempts
            if fault == "save": raise OSError("噪声写盘原始错误")
            return original_save(path, data, *args, **kwargs)
        monkeypatch.setattr(temp, "setup_dc", switch)
        monkeypatch.setattr(np, "save", save)
        failure = None
        try:
            with pytest.raises((WorkflowCancelled, RuntimeError, OSError), match="原始错误"):
                try:
                    ctx.execute(["A04"])
                except BaseException as exc:
                    failure = exc
                    raise
        finally:
            ctx.finish(failure)
        assert ctx.records[1]["status"] == "pending" and attempts
        child = ctx.directory.root.parent / ctx.records[0]["run_id"]
        assert (child / "raw/dispersion_scan.npz").is_file()
        if fault != "save":
            assert len(list((child / "raw/noise_raw").rglob("noise_*.npy"))) == 1
        if fault == "restore":
            assert not ctx.snapshot()["safety_shutdown"]["completed"]


def fake_main():
    payload = os.environ["LAB_TYPED_PARAMETERS"]
    values = json.loads(payload)
    values.pop("session_id", None)
    os.environ["LAB_TYPED_PARAMETERS"] = json.dumps(values)
    params = load_runtime_params(Params)
    os.environ["LAB_TYPED_PARAMETERS"] = payload
    with local_rig(Path(os.environ["STATIC_LOCAL_TEST_ROOT"]), params) as rig:
        rig.block_noise = os.environ.get("STATIC_LOCAL_TEST_BLOCK_NOISE") == "1"
        rig.block_shutdown = os.environ.get("STATIC_LOCAL_TEST_BLOCK_SHUTDOWN") == "1"
        if "session_id" in json.loads(payload):
            from .session import main
            main()
        else:
            workflow.main()


if __name__ == "__main__":
    fake_main()
