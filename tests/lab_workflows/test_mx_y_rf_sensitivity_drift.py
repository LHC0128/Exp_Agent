"""Mx Y RF 灵敏度长飘实验测试。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
import yaml

from lab_workflows.experiment_modules.mx_y_rf_sensitivity_drift.analysis import (
    refresh_trend,
)
from lab_workflows.experiment_modules.mx_y_rf_sensitivity_drift.models import (
    MxYRFDriftParams,
)
from lab_workflows.experiment_modules.mx_y_rf_sensitivity_drift.workflow import (
    _planned_records,
)
from lab_workflows.experiments import get_experiment
from lab_workflows.experiments.catalog import apply_parameter_layout


@pytest.fixture
def local_tmp_path() -> Path:
    path = Path(__file__).resolve().parents[2] / "data" / f".test_mx_y_rf_drift_{uuid4().hex}"
    path.mkdir(parents=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def test_defaults_and_schedule() -> None:
    params = MxYRFDriftParams.from_yaml(
        Path(__file__).resolve().parents[2]
        / "params"
        / "experiments"
        / "mx-y-rf-sensitivity-drift.yaml"
    )
    assert params.validate() == []
    assert params.planned_cycle_count == 48
    records = _planned_records(params)
    assert len(records) == 48
    assert records[0]["scheduled_offset_s"] == 0.0
    assert records[-1]["scheduled_offset_s"] == 84600.0
    definition = get_experiment("mx-y-rf-sensitivity-drift")
    assert definition.execution_mode == "typed_workflow"
    schema = definition.schema()
    assert schema["fields"][-1]["name"] == "DRIFT_START_IMMEDIATELY"
    categories = {definition.id: definition.category}
    laid_out = apply_parameter_layout(definition.id, schema, categories)
    layout = laid_out["parameter_layout"]
    assert "DRIFT_INTERVAL_S" in layout["basic"]
    assert "DRIFT_DURATION_S" in layout["basic"]
    assert "DRIFT_START_IMMEDIATELY" in layout["basic"]


def test_delayed_start_schedule_uses_half_open_duration() -> None:
    params = MxYRFDriftParams(
        drift_interval_s=1800.0,
        drift_duration_s=86400.0,
        drift_start_immediately=False,
    )
    records = _planned_records(params)
    assert len(records) == 47
    assert records[0]["scheduled_offset_s"] == 1800.0
    assert records[-1]["scheduled_offset_s"] == 84600.0


def test_refresh_trend_reads_child_analysis_and_writes_artifacts(local_tmp_path: Path) -> None:
    raw_dir = local_tmp_path / "raw"
    results_dir = local_tmp_path / "results"
    child_results = local_tmp_path / "child" / "results"
    raw_dir.mkdir()
    results_dir.mkdir()
    child_results.mkdir(parents=True)
    analysis = {
        "success": True,
        "flat_detection": {"success": True},
        "flat_median_ft_per_sqrt_hz": 12.3,
        "zero_point_method": {
            "flat_median_ft_per_sqrt_hz": 13.1,
            "slope_v_per_vpp": 2.0,
        },
        "linewidth": {
            "hwhm_hz": 4.0,
            "linewidth_kind": "amplitude_equivalent",
        },
        "primary_slope_v_per_vpp": 1.0,
        "response_fit": {"r_squared": 0.99, "excluded_point_count": 0},
        "flat_band_hz": [4.0, 5.0],
        "warnings": [],
    }
    (child_results / "analysis.json").write_text(
        json.dumps(analysis), encoding="utf-8"
    )
    child_dir = (local_tmp_path / "child").resolve()
    (raw_dir / "drift_manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "records": [
                    {
                        "cycle_index": 0,
                        "scheduled_offset_s": 0.0,
                        "actual_start_utc": "2026-08-06T00:00:00+00:00",
                        "start_lag_s": 0.2,
                        "child_run_dir": str(child_dir),
                        "status": "completed",
                        "error": None,
                    }
                ]
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    result = refresh_trend(local_tmp_path)

    assert result["cycle_count"] == 1
    assert (results_dir / "drift_summary.csv").is_file()
    assert (results_dir / "drift_summary.json").is_file()
    assert (results_dir / "drift_trend.png").is_file()
    assert (results_dir / "drift_trend_clock_time.png").is_file()
    assert "drift_trend_clock_time.png" in result["files"]
    rows = json.loads((results_dir / "drift_summary.json").read_text(encoding="utf-8"))
    assert rows[0]["valid"] is True
    assert rows[0]["actual_elapsed_s"] == pytest.approx(0.2)
    assert rows[0]["linewidth_hwhm_hz"] == 4.0


def _patch_drift_run(monkeypatch: pytest.MonkeyPatch, root: Path, events: list[str]):
    import lab_workflows.experiment_modules.mx_y_rf_sensitivity_drift.workflow as workflow

    created = 0

    class FakeRunDirectory:
        def __init__(self, path: Path) -> None:
            self.root = path
            self.raw = path / "raw"
            self.results = path / "results"
            self.config_path = path / "experiment_config.yaml"
            self.raw.mkdir(parents=True)
            self.results.mkdir()
            self.config_path.write_text("{}", encoding="utf-8")

        def update_config(self, **updates):
            self.config_path.write_text(
                yaml.safe_dump(updates, allow_unicode=True), encoding="utf-8"
            )
            return updates

    def create_run_directory(*args, **kwargs):
        nonlocal created
        path = root / f"run_{created}"
        created += 1
        return FakeRunDirectory(path)

    monkeypatch.setattr(workflow, "find_project_root", lambda: root)
    monkeypatch.setattr(
        workflow,
        "load_mapping",
        lambda _root: {"lockin_r": {"device_id": "dev"}},
    )
    monkeypatch.setattr(workflow, "create_run_directory", create_run_directory)
    monkeypatch.setattr(workflow, "DeviceSession", lambda: SimpleNamespace(cleanup_connection_failure=lambda: None))
    monkeypatch.setattr(workflow, "connect_mx_y_rf_devices", lambda *args: ({}, {}))
    monkeypatch.setattr(
        workflow,
        "configure_mx_y_rf_outputs",
        lambda *args: (1000.0, {}),
    )
    monkeypatch.setattr(workflow, "snapshot_mx_y_rf_state", lambda *args: {})
    monkeypatch.setattr(workflow, "check_cancelled", lambda: None)
    monkeypatch.setattr(workflow, "_sleep_until", lambda deadline: events.append(f"wait:{deadline:g}"))
    monkeypatch.setattr(workflow.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(workflow, "refresh_trend", lambda path: events.append("refresh"))
    monkeypatch.setattr(
        workflow,
        "safe_shutdown",
        lambda *args: SimpleNamespace(errors=(), to_dict=lambda: {}),
    )
    return workflow


def test_run_analyzes_before_waiting_for_next_cycle(
    monkeypatch: pytest.MonkeyPatch,
    local_tmp_path: Path,
) -> None:
    events: list[str] = []
    workflow = _patch_drift_run(monkeypatch, local_tmp_path, events)
    cycle = 0

    def acquire(*args, **kwargs):
        nonlocal cycle
        events.append(f"acquire:{cycle}")
        cycle += 1
        return {
            "actual_response_rate_sa_s": 1000.0,
            "actual_noise_rate_sa_s": 50000.0,
            "frequency_gate_fit": None,
        }

    analyzed = 0

    def analyze(_path):
        nonlocal analyzed
        events.append(f"analyze:{analyzed}")
        analyzed += 1
        return {"success": True}

    monkeypatch.setattr(workflow, "acquire_mx_y_rf_point", acquire)
    monkeypatch.setattr(workflow, "analyze_single", analyze)

    workflow.run(
        MxYRFDriftParams(
            drift_interval_s=10.0,
            drift_duration_s=20.0,
        )
    )

    assert cycle == 2
    assert events.index("acquire:0") < events.index("analyze:0")
    assert events.index("analyze:0") < events.index("wait:110")


def test_analysis_failure_continues_but_acquisition_failure_stops(
    monkeypatch: pytest.MonkeyPatch,
    local_tmp_path: Path,
) -> None:
    events: list[str] = []
    workflow = _patch_drift_run(monkeypatch, local_tmp_path, events)
    acquire_count = 0

    def acquire(*args, **kwargs):
        nonlocal acquire_count
        acquire_count += 1
        if acquire_count == 3:
            raise RuntimeError("hardware failure")
        return {
            "actual_response_rate_sa_s": 1000.0,
            "actual_noise_rate_sa_s": 50000.0,
            "frequency_gate_fit": None,
        }

    analyze_count = 0

    def analyze(_path):
        nonlocal analyze_count
        analyze_count += 1
        if analyze_count == 1:
            raise RuntimeError("fit failure")
        return {"success": True}

    monkeypatch.setattr(workflow, "acquire_mx_y_rf_point", acquire)
    monkeypatch.setattr(workflow, "analyze_single", analyze)

    with pytest.raises(RuntimeError, match="hardware failure"):
        workflow.run(
            MxYRFDriftParams(
                drift_interval_s=10.0,
                drift_duration_s=30.0,
            )
        )

    assert acquire_count == 3
    assert analyze_count == 2
