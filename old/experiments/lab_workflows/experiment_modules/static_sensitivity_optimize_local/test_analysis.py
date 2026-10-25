"""原决策数值、DC 子分析、旧批次与指纹隔离的正式回归。"""
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from unittest.mock import patch

import numpy as np
import pytest
import yaml

from ...common import find_project_root
from ..noise_spectrum_xy.state_io import write_state
from ..static_sensitivity.test_dc_scan import make_run
from . import analysis, analysis_steps, acquisition, preparation, run_format, workflow
from .models import StaticSensitivityOptimizeLocalParams as Params
from .stages import ANALYSIS
from .test_workflow import local_rig, small_params

REGRESSION_CASES = {
    "static-sensitivity-optimize-local": {"analysis": {
        "sources": ["lab_workflows/experiment_modules/static_sensitivity_optimize_local/analysis.py",
            "lab_workflows/experiment_modules/static_sensitivity_optimize_local/analysis_steps.py",
            "lab_workflows/experiment_modules/static_sensitivity_optimize_local/analysis_plotting.py",
            "lab_workflows/experiment_modules/static_sensitivity_optimize_local/run_format.py",
            "experiments/Static_Magnetic_Field_Sensitivity_Optimize_Local_plot.py"],
        "tests": ["test_original_decision_thresholds_and_all_invalid_diagnostics", "test_dc_point_real_analysis_and_batch_export",
            "test_historical_summary_numerically_unchanged_readonly_source", "test_staged_fingerprints_archives_and_no_overrides",
            "test_manifest_format_and_quality_flags_are_strict"],
    }},
}


def synthetic_batch(parent):
    parent = Path(parent)
    (parent / "raw").mkdir(parents=True)
    params = small_params()
    config = dict(experiment_id=run_format.EXPERIMENT_ID, schema_version=3,
        raw_format_version=1, acquisition_method="dc_step_local", completion_status="completed",
        timestamp="test", run_tag="local", parameters=params.to_external())
    (parent / "experiment_config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    rows = preparation.planned_records(params)
    row = rows[0]
    tag = f"{parent.name}_01_Pump_laser_power"
    child = parent.parent / (parent.name + "_point")
    child.mkdir()
    make_run(child)
    child_config = yaml.safe_load((child / "experiment_config.yaml").read_text(encoding="utf-8"))
    child_config.update(completion_status="completed", parameters=params.point_parameters("Pump_laser_power", .3, run_tag=tag).to_external())
    (child / "experiment_config.yaml").write_text(yaml.safe_dump(child_config), encoding="utf-8")
    row.update(run_id=child.name, status="completed", fit_is_valid=True, success=True,
        sens_flat_fT_per_sqrt_Hz=120., fit_r2=.99, gamma_nT=10., slope_V_per_fT=1e-8)
    write_state(parent / run_format.MANIFEST, dict(experiment_id=run_format.EXPERIMENT_ID,
        raw_format_version=1, records=rows, baseline_params=params.baseline_params, optimize_sequence=params.sequence()))
    return parent


def legacy_batch(parent):
    parent = Path(parent)
    parent.mkdir(parents=True)
    rows = [dict(iteration=index + 1, parameter="Pump_laser_power", value=.3,
        is_baseline=True, fit_is_valid=True, success=True, sens_flat_fT_per_sqrt_Hz=value,
        fit_r2=.99, gamma_nT=10., slope_V_per_fT=1e-8) for index, value in enumerate((100., 102., 98.))]
    payload = dict(batch_timestamp="0709_0951", baseline_params=dict(Params().baseline_params),
        optimize_sequence=[dict(parameter="Pump_laser_power", values=[.3, .3, .3])],
        results=rows, decision=analysis_steps.decide_best_result(rows))
    (parent / run_format.LEGACY_SUMMARY).write_text(yaml.safe_dump(payload), encoding="utf-8")
    return parent


def test_original_decision_thresholds_and_all_invalid_diagnostics(tmp_path):
    parent = legacy_batch(tmp_path / "0709_0951_opt_local")
    inputs = analysis_steps.load_inputs(parent)
    rows = inputs["rows"]
    assert analysis_steps.decide_best_result(rows)["baseline_median"] == 100.
    candidate = dict(rows[0], is_baseline=False, sens_flat_fT_per_sqrt_Hz=90.)
    assert analysis_steps.decide_best_result(rows + [candidate])["recommendation"] == "采用最佳扫描点"
    candidate["sens_flat_fT_per_sqrt_Hz"] = 90.01
    assert analysis_steps.decide_best_result(rows + [candidate])["recommendation"] == "维持当前基准"
    rows[0]["sens_flat_fT_per_sqrt_Hz"] = 60.
    assert analysis_steps.decide_best_result(rows)["baseline_drift_warning"]
    payload = yaml.safe_load((parent / run_format.LEGACY_SUMMARY).read_text(encoding="utf-8"))
    for row in payload["results"]:
        row.update(fit_is_valid=False, sens_flat_fT_per_sqrt_Hz="nan", error="无效拟合")
    (parent / run_format.LEGACY_SUMMARY).write_text(yaml.safe_dump(payload), encoding="utf-8")
    result = analysis.analyze(parent)
    assert result["success"] is False and result["best_run"] is None
    assert (parent / "results/optimization_local_summary.png").is_file()
    assert "NaN" not in (parent / "results/analysis.json").read_text(encoding="utf-8")


def test_dc_point_real_analysis_and_batch_export(tmp_path):
    with local_rig(tmp_path, real_analysis=True) as rig:
        directory = workflow.run(rig.local_params, project_root=tmp_path)
    records = json.loads((directory / run_format.MANIFEST).read_text(encoding="utf-8"))["records"]
    assert records[0]["success"] and records[0]["fit_is_valid"]
    child = directory.parent / records[0]["run_id"]
    science = json.loads((child / "results/analysis.json").read_text(encoding="utf-8"))
    assert records[0]["sens_flat_fT_per_sqrt_Hz"] == science["sensitivity_flat_fT_per_sqrt_Hz"]
    original = {path: path.read_bytes() for path in child.rglob("*") if path.is_file()}
    result = analysis.analyze(directory)
    assert result["baseline_median"] == science["sensitivity_flat_fT_per_sqrt_Hz"]
    assert all(path.read_bytes() == value for path, value in original.items())
    assert (directory / "results/optimization_local_summary.pdf").is_file()
    assert len(list((child / "raw/dispersion_steps").glob("point_*.npz"))) == rig.local_params.dispersion_steps


def test_historical_summary_numerically_unchanged_readonly_source(tmp_path):
    source = find_project_root() / "data/Static_Magnetic_Field_Sensitivity/0709_0951_opt_local/optimization_local_summary.yaml"
    parent = tmp_path / "historical"
    parent.mkdir()
    if source.is_file():
        shutil.copy2(source, parent / run_format.LEGACY_SUMMARY)
    else:
        parent.rmdir()
        legacy_batch(parent)
    original = (parent / run_format.LEGACY_SUMMARY).read_bytes()
    expected = yaml.safe_load(original)
    result = analysis.analyze(parent)
    for key in ("baseline_valid_count", "baseline_median", "baseline_drift_ratio", "baseline_drift_warning", "best_improvement_ratio", "recommendation"):
        assert result[key] == expected["decision"][key]
    exported = yaml.safe_load((parent / "results/optimization_local_summary.yaml").read_text(encoding="utf-8"))
    for actual, old in zip(exported["results"], expected["results"]):
        for key in ("sens_flat_fT_per_sqrt_Hz", "fit_r2", "gamma_nT", "slope_V_per_fT"):
            assert actual[key] == analysis_steps.finite(old[key])
    assert (parent / run_format.LEGACY_SUMMARY).read_bytes() == original
    assert not (parent / "experiment_config.yaml").exists()


def test_staged_fingerprints_archives_and_no_overrides(tmp_path):
    parent = synthetic_batch(tmp_path / "parent")
    original = (parent / "experiment_config.yaml").read_bytes()
    full = analysis.analyze(parent)
    for stage in ANALYSIS:
        ctx = analysis.AnalysisWorkflow(parent)
        ctx.execute([stage.id], ctx.revision)
    assert ctx.summary == full
    assert (parent / "experiment_config.yaml").read_bytes() == original
    frozen = (parent / "results/analysis_inputs.json").read_bytes()
    ctx = analysis.AnalysisWorkflow(parent)
    ctx.execute(["B03"])
    assert (parent / "results/analysis_inputs.json").read_bytes() == frozen
    assert not any((path / "analysis_inputs.json").exists() for path in sorted((parent / "results").glob("before_*"))[-1:])
    with pytest.raises(ValueError, match="无独立覆盖"):
        analysis.AnalysisWorkflow(parent, {"Z_V_TO_NT": 12.})
    product = parent / "results/optimization_decision.json"
    product.write_text("{}", encoding="utf-8")
    ctx = analysis.AnalysisWorkflow(parent)
    assert ctx.states["B01"]["status"] == "completed"
    assert ctx.states["B02"]["status"] == ctx.states["B03"]["status"] == "stale"
    with pytest.raises(ValueError, match="缺少前置"):
        ctx.execute(["B03"])
    source = parent / run_format.MANIFEST
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["records"][0]["run_id"] = "../escape"
    source.write_text(json.dumps(payload), encoding="utf-8")
    ctx = analysis.AnalysisWorkflow(parent)
    assert all(state["status"] == "stale" for state in ctx.states.values())
    with pytest.raises(ValueError, match="单个目录"):
        ctx.execute(["B01"])
    assert ctx.states["B01"]["status"] == "quality_failed"


@pytest.mark.parametrize("field,value", [("raw_format_version", True), ("raw_format_version", 2), ("fit_is_valid", "false"), ("is_baseline", 1)])
def test_manifest_format_and_quality_flags_are_strict(tmp_path, field, value):
    parent = synthetic_batch(tmp_path / "strict")
    path = parent / run_format.MANIFEST
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if field == "raw_format_version": manifest[field] = value
    else: manifest["records"][0][field] = value
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError):
        analysis_steps.load_inputs(parent)


def test_same_raw_format_independent_of_parameter_version_and_cli(tmp_path):
    parent = synthetic_batch(tmp_path / "parent")
    path = parent / "experiment_config.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config["schema_version"] = 4
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    assert analysis_steps.load_inputs(parent)["rows"][0]["fit_is_valid"]
    command = [sys.executable, "-m", "lab_workflows.experiment_modules.static_sensitivity_optimize_local.analysis"]
    env = dict(os.environ, LAB_RUN_DIR=str(parent), LAB_TYPED_PARAMETERS="{}", MPLBACKEND="Agg", PYTHONIOENCODING="utf-8")
    result = subprocess.run(command, env=env, text=True, capture_output=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stderr
    assert json.loads((parent / "results/analysis.json").read_text(encoding="utf-8"))["best_run"]["iteration"] == 1
    script = find_project_root() / "experiments/Static_Magnetic_Field_Sensitivity_Optimize_Local_plot.py"
    missing = subprocess.run([sys.executable, str(script)], env=env, capture_output=True, timeout=30)
    assert missing.returncode != 0
