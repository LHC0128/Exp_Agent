"""本 ID 的真实父子编排、标准采集与真实 writer；仪器全部替换为假设备。"""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import json
import time
import numpy as np
import pytest
import yaml
from ...common import WorkflowCancelled
from ..mx_y_rf_sensitivity import acquisition_steps as single_acquisition, excitation as single_excitation
from ..mx_y_rf_sensitivity import preparation as single_preparation
from ..mx_y_rf_sensitivity.test_workflow import copy_config, install_rig, assert_safe
from . import workflow, acquisition, preparation, scheduling, run_format
from .models import MxYRFDriftParams
from .stages import ACQUISITION, FINISH_ID, definition

ACQUISITION_CASES = {
    "mx-y-rf-sensitivity-drift": {"mode": "single", "cases": {
        "order": ["test_real_two_round_order_slow_disk_and_final_points"],
        "slow_disk": ["test_real_two_round_order_slow_disk_and_final_points"],
        "cancel": ["test_real_failure_preserves_partial_child_and_stops_later_rounds", "test_cancel_absolute_wait_without_creating_child"],
        "acquire_failure": ["test_real_failure_preserves_partial_child_and_stops_later_rounds"],
        "restore_failure": ["test_restore_failure_rescues_payload_before_stopping", "test_acquire_and_temperature_restore_failures_keep_original", "test_successful_capture_but_single_point_cleanup_failure_stops_parent", "test_quality_failure_plus_cleanup_failure_must_stop_later_cycles"],
        "save_failure": ["test_real_save_failure_stops_after_temperature_restore"],
        "partial_save": ["test_real_failure_preserves_partial_child_and_stops_later_rounds", "test_restore_failure_rescues_payload_before_stopping"],
        "entrypoints": ["test_full_and_stepwise_share_science_and_rerun_preserves_prior_directories", "test_sealed_stage_snapshot_is_complete_and_finish_keeps_executed_parameters"],
    }, "sources": [
        "lab_workflows/experiment_modules/mx_y_rf_sensitivity_drift/workflow.py",
        "lab_workflows/experiment_modules/mx_y_rf_sensitivity_drift/acquisition.py",
        "lab_workflows/experiment_modules/mx_y_rf_sensitivity_drift/preparation.py",
        "lab_workflows/experiment_modules/mx_y_rf_sensitivity_drift/scheduling.py",
        "lab_workflows/experiment_modules/mx_y_rf_sensitivity/acquisition_steps.py",
        "lab_workflows/experiment_modules/mx_y_rf_sensitivity/excitation.py",
        "lab_workflows/experiment_modules/mx_y_rf_sensitivity/preparation.py",
    ]},
}


@pytest.fixture
def rig(tmp_path, monkeypatch):
    copy_config(tmp_path)
    params = replace(MxYRFDriftParams(), y_rf_amp_points=5, noise_n_avg=2, noise_rate_sa_s=1000,
        noise_duration_s=0.256, response_duration_s=0.016, system_settle_s=0.001,
        temp_switch_on_lag_s=0, y_rf_nt_per_vpp=10, drift_interval_s=2, drift_duration_s=4)
    rig = install_rig(tmp_path, monkeypatch, params)
    rig.clock, rig.deadlines, rig.analyzed, rig.refreshed = 100.0, [], [], []
    monkeypatch.setattr(workflow, "find_project_root", lambda: tmp_path)
    def check():
        if rig.cancel:
            raise WorkflowCancelled("模拟取消")
    def sleep(seconds):
        rig.clock += seconds
    monkeypatch.setattr(workflow, "time", SimpleNamespace(monotonic=lambda: rig.clock, sleep=sleep))
    for module in (workflow, acquisition, scheduling):
        monkeypatch.setattr(module, "check_cancelled", check)
    wait = workflow._sleep_until
    def wait_until(deadline):
        rig.deadlines.append(deadline)
        wait(deadline)
    monkeypatch.setattr(workflow, "_sleep_until", wait_until)
    monkeypatch.setattr(workflow, "report_runtime_progress", lambda *args, **kwargs: None)
    def analyze(path):
        assert rig.enabled
        rig.analyzed.append(Path(path))
        return {"success": True}
    def refresh(path):
        assert rig.enabled or rig.restore_error
        rig.refreshed.append(Path(path))
    monkeypatch.setattr(workflow, "analyze_single", analyze)
    monkeypatch.setattr(workflow, "refresh_trend", refresh)
    return rig


def through(last):
    ids = [stage.id for stage in ACQUISITION if stage.id != FINISH_ID]
    return ids[:ids.index(last) + 1]


def config(path):
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def parent_and_records(rig):
    parent = next((rig.root / "data" / run_format.DATA_TYPE).iterdir())
    return parent, config(parent / run_format.MANIFEST)["records"]


def test_real_two_round_order_slow_disk_and_final_points(rig, monkeypatch):
    writer = np.savez
    def save(path, **data):
        assert rig.enabled, "恢复温控前不能写盘"
        if "attempt" in str(path) or Path(path).name.startswith("noise_"):
            rig.events.append("save")
            time.sleep(0.002)
        writer(path, **data)
    monkeypatch.setattr(np, "savez", save)
    parent = workflow.run(rig.params)
    assert_safe(rig)
    _, records = parent_and_records(rig)
    assert [record["status"] for record in records] == ["completed", "completed"]
    assert rig.deadlines == [100.0, 102.0]
    assert rig.analyzed == [Path(record["child_run_dir"]) for record in records]
    for index, event in enumerate(rig.events):
        if event == "acquire":
            assert rig.events[index - 2:index] == ["off", "settle"]
            assert rig.events[index + 1:index + 3] == ["on", "save"]
    for record in records:
        child = Path(record["child_run_dir"])
        raw = child / "raw"
        assert len(list(raw.glob("amplitude_*_attempt_00.npz"))) == rig.params.y_rf_amp_points
        assert len(list(raw.glob("noise_*.npz"))) == rig.params.noise_n_avg
        with np.load(raw / "amplitude_scan.npz") as data:
            assert data["signed_amplitude_vpp"][-1] == rig.params.y_rf_amp_stop_vpp
            np.testing.assert_array_equal(data["hardware_phase_deg"], [180, 180, 0, 0, 0])
        child_config = config(child / "experiment_config.yaml")
        assert child_config["experiment_id"] == run_format.CHILD_EXPERIMENT_ID
        assert child_config["raw_format_version"] == 1
        assert child_config["temperature_control"]["actual_temperature_c"] is None
        assert child_config["temperature_control"]["target_temperature_c"] == rig.params.temperature_c
    assert config(parent / "experiment_config.yaml")["completion_status"] == "completed"


@pytest.mark.parametrize("failure", [WorkflowCancelled("模拟取消"), OSError("模拟采集失败")])
def test_real_failure_preserves_partial_child_and_stops_later_rounds(rig, failure):
    rig.fault_index, rig.acquire_error = 1, failure
    with pytest.raises(type(failure)) as caught:
        workflow.run(rig.params)
    assert caught.value is failure
    assert_safe(rig)
    parent, records = parent_and_records(rig)
    assert records[1]["child_run_dir"] is None
    raw = Path(records[0]["child_run_dir"]) / "raw"
    assert (raw / "amplitude_0000_attempt_00.npz").is_file()
    assert not (raw / "amplitude_0001_attempt_00.npz").exists()
    status = "cancelled" if isinstance(failure, WorkflowCancelled) else "failed"
    assert config(parent / "experiment_config.yaml")["completion_status"] == status
    assert config(raw.parent / "experiment_config.yaml")["completion_status"] == status


def test_restore_failure_rescues_payload_before_stopping(rig):
    rig.restore_error = True
    with pytest.raises(OSError, match="温控恢复失败"):
        workflow.run(rig.params)
    parent, records = parent_and_records(rig)
    with np.load(Path(records[0]["child_run_dir"]) / "raw/amplitude_0000_attempt_00.npz") as data:
        assert data["r_v"].size >= 8
    assert len(rig.captures) == 1
    assert records[1]["status"] == "pending"
    assert not config(parent / "experiment_config.yaml")["safety_shutdown"]["completed"]


def test_acquire_and_temperature_restore_failures_keep_original(rig, monkeypatch):
    failure = OSError("原始采集失败")
    rig.fault_index, rig.acquire_error = 1, failure
    reader = single_acquisition.acquire_r
    def fail_restore_on_failed_read(*args, **kwargs):
        try:
            return reader(*args, **kwargs)
        finally:
            if len(rig.captures) > 1:
                rig.restore_error = True
    monkeypatch.setattr(single_acquisition, "acquire_r", fail_restore_on_failed_read)
    with pytest.raises(OSError) as caught:
        workflow.run(rig.params)
    assert caught.value is failure
    _, records = parent_and_records(rig)
    assert (Path(records[0]["child_run_dir"]) / "raw/amplitude_0000_attempt_00.npz").is_file()
    assert records[1]["child_run_dir"] is None


def test_real_save_failure_stops_after_temperature_restore(rig, monkeypatch):
    writer, failure = np.savez, OSError("模拟磁盘失败")
    def save(path, **data):
        assert rig.enabled
        if "amplitude_0001_attempt" in str(path):
            raise failure
        writer(path, **data)
    monkeypatch.setattr(np, "savez", save)
    with pytest.raises(OSError) as caught:
        workflow.run(rig.params)
    assert caught.value is failure
    assert_safe(rig)
    _, records = parent_and_records(rig)
    assert (Path(records[0]["child_run_dir"]) / "raw/amplitude_0000_attempt_00.npz").is_file()
    assert records[1]["child_run_dir"] is None


def test_shared_single_point_cleanup_does_not_replace_acquisition_error(rig, monkeypatch, capsys):
    failure, cleanup = OSError("原始标准单轮采集失败"), OSError("单轮 Y RF 归零另失败")
    rig.fault_index, rig.acquire_error = 1, failure
    def fail_cleanup(*args):
        raise cleanup
    monkeypatch.setattr(single_acquisition, "_set_y_rf_zero_off_with_temperature_sequence", fail_cleanup)
    with pytest.raises(OSError) as caught:
        workflow.run(rig.params)
    assert caught.value is failure
    assert str(cleanup) in capsys.readouterr().out
    if hasattr(failure, "add_note"):
        assert any(str(cleanup) in note for note in failure.__notes__)


def test_successful_capture_but_single_point_cleanup_failure_stops_parent(rig, monkeypatch):
    failure = OSError("单轮最后归零失败")
    def fail_cleanup(*args):
        raise failure
    monkeypatch.setattr(single_acquisition, "_set_y_rf_zero_off_with_temperature_sequence", fail_cleanup)
    with pytest.raises(OSError) as caught:
        workflow.run(rig.params)
    assert caught.value is failure
    parent, records = parent_and_records(rig)
    child = Path(records[0]["child_run_dir"])
    assert (child / "raw/amplitude_scan.npz").is_file()
    assert (child / "raw/noise_001.npz").is_file()
    assert records[0]["status"] == "acquisition_failed"
    assert records[1]["child_run_dir"] is None
    assert config(child / "experiment_config.yaml")["completion_status"] == "failed"
    assert config(parent / "experiment_config.yaml")["completion_status"] == "failed"
    assert_safe(rig)


def test_cancel_absolute_wait_without_creating_child(rig, monkeypatch):
    ctx = workflow.Workflow(replace(rig.params, drift_start_immediately=False, drift_duration_s=5))
    ctx.execute(through("A08"))
    rig.cancel = True
    with pytest.raises(WorkflowCancelled) as caught:
        ctx.execute(["A09"])
    ctx.finish(caught.value)
    assert not rig.captures
    assert all(record["child_run_dir"] is None for record in ctx.records)
    assert_safe(rig)


def test_full_and_stepwise_share_science_and_rerun_preserves_prior_directories(rig):
    full = workflow.run(rig.params)
    original = (full / "experiment_config.yaml").read_bytes()
    full_records = config(full / run_format.MANIFEST)["records"]
    rig.captures.clear()
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A07"))
    ctx.execute(["A08", "A09", "A10"])
    split, child_before = ctx.directory.root, Path(ctx.records[0]["child_run_dir"])
    for first, second in zip(full_records, ctx.records):
        with np.load(Path(first["child_run_dir"]) / "raw/amplitude_scan.npz") as a, np.load(Path(second["child_run_dir"]) / "raw/amplitude_scan.npz") as b:
            np.testing.assert_allclose(a["r_mean_v"], b["r_mean_v"])
    split_bytes = (split / "experiment_config.yaml").read_bytes()
    child_bytes = (child_before / "raw/amplitude_scan.npz").read_bytes()
    with pytest.raises(ValueError, match="缺少前置"):
        ctx.execute(["A09"])
    ctx.execute(["A07", "A08", "A09", "A10"])
    assert ctx.directory.root not in (full, split)
    assert Path(ctx.records[0]["child_run_dir"]) != child_before
    assert (full / "experiment_config.yaml").read_bytes() == original
    assert (split / "experiment_config.yaml").read_bytes() == split_bytes
    assert (child_before / "raw/amplitude_scan.npz").read_bytes() == child_bytes
    ctx.finish()


def test_dependencies_no_precondition_finish_and_parameter_impacts_match_real_update(rig):
    ctx = workflow.Workflow(rig.params)
    with pytest.raises(ValueError, match="缺少前置"):
        ctx.execute(["A09"])
    assert not rig.events
    ctx.execute(through("A08"))
    previous = deepcopy(ctx.states)
    ctx.execute(["A01"])
    assert ctx.states == previous
    events = list(rig.events)
    ctx.update_parameters({"DRIFT_INTERVAL_S": 1.0})
    assert rig.events == events
    expected = set(definition()["parameter_impacts"]["acquisition"]["DRIFT_INTERVAL_S"])
    assert {key for key, value in ctx.states.items() if value["status"] == "stale"} == expected.intersection(previous_key for previous_key, value in previous.items() if value["status"] == "completed")
    assert ctx.states["A07"]["status"] == "completed"
    ctx.finish()
    empty = workflow.Workflow(rig.params)
    empty.execute([FINISH_ID])
    assert empty.closed and empty.directory is None
    assert set(empty.outputs.values()) == {"未知"}


def test_all_visible_editors_are_real_and_initialization_is_not_repeated(rig):
    schema = rig.params.schema(rig.root)
    visible = {item["name"] for item in schema["fields"]}
    shown = [key for stage in definition()["acquisition"] for key in stage["display_parameters"]]
    assert set(shown) == visible
    for key in ("FIXED_PARAMS.main_magnetic_field", "FIXED_PARAMS.temperature", "PUMP_CARRIER_FREQUENCY_HZ", "DRIFT_INTERVAL_S"):
        assert shown.count(key) == 1
    for key in ("FREQUENCY_START_HZ", "FREQUENCY_STOP_HZ", "FREQUENCY_POINTS", "FREQUENCY_DURATION_S"):
        assert key not in visible
        assert key in rig.params.to_external()
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A08"))
    old_plan = (ctx.manifest_path).read_bytes()
    ctx.update_parameters({"DRIFT_START_IMMEDIATELY": False, "DRIFT_DURATION_S": 7.0})
    ctx.execute(["A08", "A09", "A10"])
    assert [record["scheduled_offset_s"] for record in ctx.records] == [2, 4, 6]
    assert old_plan != ctx.manifest_path.read_bytes()
    ctx.finish()


@pytest.mark.parametrize("immediate,expected", [(True, [0, 2, 4]), (False, [2, 4])])
def test_absolute_overdue_cycles_are_not_skipped(rig, monkeypatch, immediate, expected):
    params = replace(rig.params, drift_duration_s=5, drift_start_immediately=immediate)
    collector = workflow.acquire_mx_y_rf_point
    def slow_cycle(*args, **kwargs):
        point = collector(*args, **kwargs)
        rig.clock += 5
        return point
    monkeypatch.setattr(workflow, "acquire_mx_y_rf_point", slow_cycle)
    parent = workflow.run(params)
    records = config(parent / run_format.MANIFEST)["records"]
    assert [record["scheduled_offset_s"] for record in records] == expected
    assert rig.deadlines == [100 + offset for offset in expected]
    assert len(rig.analyzed) == len(expected)
    assert records[-1]["start_lag_s"] > 0


@pytest.mark.parametrize("bad_round", ["r_quality", "fit", "analysis_error"])
def test_invalid_round_is_recorded_then_next_real_cycle_runs(rig, monkeypatch, bad_round):
    if bad_round == "r_quality":
        reader = single_acquisition.acquire_r
        def first_round_bad(*args, **kwargs):
            payload = reader(*args, **kwargs)
            if len(rig.captures) <= rig.params.r_point_max_attempts:
                payload["r"] = np.arange(payload["r"].size, dtype=float)
            return payload
        monkeypatch.setattr(single_acquisition, "acquire_r", first_round_bad)
    else:
        def analyze(path):
            rig.analyzed.append(Path(path))
            if len(rig.analyzed) == 1 and bad_round == "analysis_error":
                raise ValueError("模拟单轮拟合失败")
            return {"success": len(rig.analyzed) > 1}
        monkeypatch.setattr(workflow, "analyze_single", analyze)
    parent = workflow.run(rig.params)
    records = config(parent / run_format.MANIFEST)["records"]
    assert records[0]["status"] == {"r_quality": "invalid_quality", "fit": "invalid", "analysis_error": "analysis_failed"}[bad_round]
    assert records[1]["status"] == "completed"
    assert config(parent / "experiment_config.yaml")["completion_status"] == "completed"
    assert_safe(rig)


def test_last_executed_pump_and_response_configuration_survives_unexecuted_apply(rig, monkeypatch):
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A09"))
    expected_pump, expected_response = ctx.pump_parameters, ctx.response_parameters
    parameters = dict(ctx.run_parameters)
    raw = (Path(ctx.records[0]["child_run_dir"]) / "raw/amplitude_scan.npz").read_bytes()
    events = list(rig.events)
    ctx.update_parameters({"PUMP_CARRIER_FREQUENCY_HZ": 99e6, "PUMP_CARRIER_AMPLITUDE_VPP": .15,
        "Y_RF_FREQUENCY_HZ": 11000, "RESPONSE_RATE_SA_S": 2000})
    assert rig.events == events
    restore = Mock(wraps=preparation._restore_response_acquisition_state)
    shutdown = Mock(wraps=workflow.safe_shutdown)
    monkeypatch.setattr(preparation, "_restore_response_acquisition_state", restore)
    monkeypatch.setattr(workflow, "safe_shutdown", shutdown)
    ctx.finish()
    assert restore.call_args.args[0] is expected_response
    assert shutdown.call_args.args[2] is expected_pump
    assert ctx.devices["pump_rf"].frequency[ctx.channels["pump_carrier"]] == expected_pump.pump_carrier_frequency_hz
    hf2 = ctx.devices["hf2"]
    assert hf2.get_double(hf2.osc_path(expected_response.demod_osc_idx) + "/freq") == expected_response.y_rf_frequency_hz
    assert hf2.get_double(hf2.demod_path(expected_response.demod_idx) + "/rate") == expected_response.response_rate_sa_s
    assert config(ctx.directory.config_path)["parameters"] == parameters
    assert (Path(ctx.records[0]["child_run_dir"]) / "raw/amplitude_scan.npz").read_bytes() == raw
    assert_safe(rig)


def test_clock_failure_stops_before_capture_and_releases_after_safe_shutdown(rig):
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A07"))
    rig.clock_error = True
    with pytest.raises(RuntimeError, match="时钟源设置失败") as caught:
        ctx.execute(["A03"])
    ctx.finish(caught.value)
    assert not rig.captures
    assert ctx.states[FINISH_ID]["status"] == "completed"
    assert "disconnect" in rig.events
    assert_safe(rig)


def test_real_single_analysis_and_parent_trends_are_called_for_each_completed_child(rig, monkeypatch):
    from ..mx_y_rf_sensitivity.analysis import analyze
    from .analysis import refresh_trend
    rig.params = replace(rig.params, y_rf_amp_points=21)
    calls = []
    def real_analysis(path):
        assert rig.enabled
        calls.append(Path(path))
        return analyze(path)
    monkeypatch.setattr(workflow, "analyze_single", real_analysis)
    monkeypatch.setattr(workflow, "refresh_trend", refresh_trend)
    parent = workflow.run(rig.params)
    records = config(parent / run_format.MANIFEST)["records"]
    assert calls == [Path(record["child_run_dir"]) for record in records]
    for record in records:
        child = Path(record["child_run_dir"])
        assert (child / "results/analysis.json").is_file()
        assert (child / "results/response_fit.npz").is_file()
    rows = json.loads((parent / "results/drift_summary.json").read_text(encoding="utf-8"))
    assert len(rows) == len(records)
    assert (parent / "results/drift_trend.png").is_file()
    assert (parent / "results/drift_trend_clock_time.png").is_file()


def test_quality_failure_plus_cleanup_failure_must_stop_later_cycles(rig, monkeypatch):
    reader = single_acquisition.acquire_r
    def bad_r(*args, **kwargs):
        payload = reader(*args, **kwargs)
        payload["r"] = np.arange(payload["r"].size, dtype=float)
        return payload
    def fail_cleanup(*args):
        raise OSError("质量失败后最后归零失败")
    monkeypatch.setattr(single_acquisition, "acquire_r", bad_r)
    monkeypatch.setattr(single_acquisition, "_set_y_rf_zero_off_with_temperature_sequence", fail_cleanup)
    with pytest.raises(workflow.RPointQualityError):
        workflow.run(rig.params)
    parent, records = parent_and_records(rig)
    assert records[0]["status"] == "acquisition_failed"
    assert records[1]["child_run_dir"] is None
    assert config(parent / "experiment_config.yaml")["completion_status"] == "failed"
    assert records[0]["safety_cleanup_errors"] == ["质量失败后最后归零失败"]


def test_immediate_analysis_cancel_stops_without_leaving_running_record(rig, monkeypatch):
    failure = WorkflowCancelled("即时分析取消")
    def cancel(path):
        raise failure
    monkeypatch.setattr(workflow, "analyze_single", cancel)
    with pytest.raises(WorkflowCancelled) as caught:
        workflow.run(rig.params)
    assert caught.value is failure
    parent, records = parent_and_records(rig)
    assert records[0]["status"] == "cancelled"
    assert records[1]["child_run_dir"] is None
    child = Path(records[0]["child_run_dir"])
    assert config(child / "experiment_config.yaml")["analysis_status"] == "cancelled"
    assert (child / "raw/amplitude_scan.npz").is_file()
    assert config(parent / "experiment_config.yaml")["completion_status"] == "cancelled"
    assert_safe(rig)


def test_running_manifest_write_failure_marks_created_child_and_preserves_error(rig, monkeypatch):
    writer, failure = workflow._write_manifest, OSError("运行中清单磁盘失败")
    def fail_running_once(path, records):
        if records[0]["status"] == "running":
            raise failure
        writer(path, records)
    monkeypatch.setattr(workflow, "_write_manifest", fail_running_once)
    with pytest.raises(OSError) as caught:
        workflow.run(rig.params)
    assert caught.value is failure
    parent, records = parent_and_records(rig)
    assert not rig.captures
    assert records[0]["status"] == "acquisition_failed"
    child = Path(records[0]["child_run_dir"])
    assert config(child / "experiment_config.yaml")["completion_status"] == "failed"
    assert config(parent / "experiment_config.yaml")["completion_status"] == "failed"
    assert records[1]["child_run_dir"] is None


def test_finish_before_response_preparation_does_not_apply_unexecuted_response(rig):
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A05"))
    hf2 = ctx.devices["hf2"]
    before = dict(hf2.nodes)
    ctx.update_parameters({"Y_RF_FREQUENCY_HZ": 11000, "RESPONSE_RATE_SA_S": 2000})
    ctx.finish()
    assert hf2.nodes == before
    ctx.devices["tec"].set_enable.assert_not_called()
    assert ctx.state["temperature_control"]["actual_temperature_c"] is None


def test_running_stage_rejects_parameter_apply_without_touching_state(rig):
    ctx = workflow.Workflow(rig.params)
    ctx.current_step = "A09"
    original = ctx.params.to_external()
    with pytest.raises(ValueError, match="执行中"):
        ctx.update_parameters({"DRIFT_INTERVAL_S": 3})
    with pytest.raises(ValueError, match="正在执行"):
        ctx.execute(["A01"])
    assert ctx.params.to_external() == original
    ctx.current_step = None
    ctx.finish()


def test_sealed_stage_snapshot_is_complete_and_finish_keeps_executed_parameters(rig):
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A10"))
    path = ctx.directory.root / "acquisition_state.json"
    sealed = json.loads(path.read_text(encoding="utf-8"))
    assert sealed["steps"]["A10"]["status"] == "completed"
    assert sealed["current_step"] is None
    saved_config = ctx.directory.config_path.read_bytes()
    ctx.update_parameters({"DRIFT_INTERVAL_S": 3.0})
    assert json.loads(path.read_text(encoding="utf-8")) == sealed
    ctx.finish()
    final = json.loads(path.read_text(encoding="utf-8"))
    assert final["status"] == "closed" and final["current_step"] is None
    assert final["parameters"] == sealed["parameters"]
    assert final["steps"]["A10"] == sealed["steps"]["A10"]
    assert final["steps"][FINISH_ID]["status"] == "completed"
    assert ctx.directory.config_path.read_bytes() == saved_config
