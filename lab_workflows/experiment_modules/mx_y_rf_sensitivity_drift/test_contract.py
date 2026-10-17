"""父清单资格、参数迁移与半开时间边界；不读取或改写真实历史。"""
from copy import deepcopy
from dataclasses import replace
import math
import pytest
import yaml
from .models import MxYRFDriftParams
from .scheduling import _planned_records, _child_parameters, _write_manifest
from . import run_format


def legacy_config():
    return dict(experiment_id=run_format.EXPERIMENT_ID, data_type=run_format.DATA_TYPE,
        parameters=MxYRFDriftParams().to_external(), schema_version=2, completion_status="completed",
        scan_mode="repeated_measurement", child_experiment_id=run_format.CHILD_EXPERIMENT_ID,
        manifest=run_format.MANIFEST)


@pytest.mark.parametrize("immediate,duration,interval,expected", [
    (True, 4, 2, [0, 2]), (False, 4, 2, [2]), (True, 5, 2, [0, 2, 4]),
    (False, 2, 2, []), (True, 0.07, 0.01, [0, .01, .02, .03, .04, .05, .06]),
])
def test_half_open_plan_respects_immediate_choice_and_floating_endpoint(immediate, duration, interval, expected):
    params = replace(MxYRFDriftParams(), drift_start_immediately=immediate,
        drift_duration_s=duration, drift_interval_s=interval)
    records = _planned_records(params)
    assert [record["scheduled_offset_s"] for record in records] == pytest.approx(expected)
    assert len(records) == params.planned_cycle_count
    assert all(record["scheduled_offset_s"] < duration for record in records)


@pytest.mark.parametrize("interval,duration", [(0, 4), (2, 0), (-1, 4), (math.nan, 4), (2, math.inf)])
def test_bad_schedule_validation_reports_without_dividing_or_converting_nonfinite(interval, duration):
    params = replace(MxYRFDriftParams(), drift_interval_s=interval, drift_duration_s=duration)
    assert params.planned_cycle_count == 0
    assert params.validate_model()


def test_old_wait_migration_hidden_keys_and_current_yaml_roundtrip(tmp_path):
    old = dict(RESPONSE_SETTLE_TIME_S=.2, FREQUENCY_SETTLE_TIME_S=.4, TEMP_SWITCH_OFF_LEAD_S=.1,
        FREQUENCY_START_HZ=8001, DRIFT_INTERVAL_S=120, Y_RF_NT_PER_VPP=10)
    params = MxYRFDriftParams.from_external(old, schema_version=1)
    assert params.system_settle_s == .4
    assert MxYRFDriftParams.from_external(old | {"SYSTEM_SETTLE_S": .15}).system_settle_s == .15
    assert params.frequency_start_hz == 8001
    child = _child_parameters(params)
    assert child["FREQUENCY_START_HZ"] == 8001
    assert "DRIFT_INTERVAL_S" not in child
    path = params.save_yaml(tmp_path / "params.yaml")
    assert MxYRFDriftParams.from_yaml(path).to_external() == params.to_external()
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["schema_version"] == 2


def test_new_manifest_marker_is_independent_of_parameter_schema(tmp_path):
    path = tmp_path / "drift_manifest.yaml"
    records = _planned_records(replace(MxYRFDriftParams(), drift_duration_s=4, drift_interval_s=2))
    _write_manifest(path, records)
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == payload["raw_format_version"] == 1
    cfg = legacy_config() | {"raw_format_version": 1, "schema_version": 999}
    assert run_format.analysis_block_reason(cfg, payload) == ""
    assert "格式" in run_format.analysis_block_reason(cfg | {"raw_format_version": 99}, payload)
    assert "执行" in run_format.analysis_block_reason(cfg | {"completion_status": "running"}, payload)
    assert run_format.analysis_block_reason(cfg | {"completion_status": "running"}, payload, allow_running=True) == ""


def test_evidenced_legacy_sparse_manifest_is_kept_without_relaxing_new_format():
    cfg = legacy_config()
    records = _planned_records(replace(MxYRFDriftParams(), drift_duration_s=6, drift_interval_s=2))
    records[-1].update(cycle_index=81, scheduled_offset_s=162)
    payload = dict(schema_version=1, records=records)
    assert run_format.analysis_block_reason(cfg, payload) == ""
    assert run_format.analysis_block_reason(cfg | {"raw_format_version": 1}, payload)
    for key in ("experiment_id", "data_type", "scan_mode", "child_experiment_id", "manifest"):
        damaged = dict(cfg)
        damaged.pop(key)
        assert run_format.analysis_block_reason(damaged, payload)
    for mutate in (lambda values: values[1].update(cycle_index=0),
                   lambda values: values[1].update(scheduled_offset_s=float("nan")),
                   lambda values: values[1].update(status="unknown"),
                   lambda values: values[1].pop("child_run_dir")):
        damaged = deepcopy(payload)
        mutate(damaged["records"])
        assert run_format.analysis_block_reason(cfg, damaged)
