"""独立公式、内容指纹、归档前资格与参数/历史只读边界。"""
from pathlib import Path
import hashlib
import os
import shutil
import numpy as np
import pytest
import yaml
from . import analysis, analysis_steps, run_format
from .models import MxYOptimalControlRFFrequencyResponseParams


REGRESSION_CASES = {
    "mx-y-optimal-control-rf-frequency-response": {
        "analysis": {
            "sources": [
                "lab_workflows/experiment_modules/mx_y_optimal_control_rf_frequency_response/__init__.py",
                "lab_workflows/experiment_modules/mx_y_optimal_control_rf_frequency_response/analysis.py"
            ],
            "tests": [
                "test_full_and_stepwise_preserve_independent_phase_median_and_constant_curve",
                "test_content_fingerprint_detects_same_size_same_timestamp_and_preserves_previous_products",
                "test_quality_failure_keeps_diagnostics_and_blocks_report",
                "test_format_rejects_ambiguous_identity_and_unsealed_inputs",
                "test_parameter_schema_independent_of_explicit_raw_format",
                "test_real_module_outside_repository_uses_explicit_run"
            ]
        }
    }
}


def build_run(root, comparison=True, schema=3, raw_version=1):
    directory = root / "data" / run_format.DATA_TYPE / "1015_1200_fixture"
    (directory / "raw").mkdir(parents=True)
    config = dict(experiment_id=run_format.EXPERIMENT_ID, data_type=run_format.DATA_TYPE, schema_version=schema,
        completion_status="completed", parameters=MxYOptimalControlRFFrequencyResponseParams().to_external(),
        comparison={"enabled": comparison}, timestamp="2026-10-15", control_source={"run": "frozen", "amplitude_vpp": .2, "offset_v": 0.})
    if raw_version is not None:
        config["raw_format_version"] = raw_version
    (directory / "experiment_config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    matrix = np.array([[1., 4., 2.], [3., np.nan, 7.], [8., 5., 9.]])
    np.savez(directory / "raw" / analysis_steps.OPTIMAL_CONTROL_NPZ, frequency_hz=[100., 200., 300.], phase_deg=[0., 90., 180.],
        r_mean_v=matrix, r_std_v=np.zeros((3, 3)), accepted_attempt_index=np.zeros((3, 3), dtype=int), accepted_file=np.full((3, 3), "point.npz"))
    if comparison:
        np.savez(directory / "raw" / analysis_steps.CONSTANT_CONTROL_NPZ, frequency_hz=[100., 200., 300.], phase_deg=[0., 0., 0.],
            r_mean_v=[.1, .2, .3], r_std_v=np.zeros(3), accepted_attempt_index=np.zeros(3, dtype=int), accepted_file=["point.npz"] * 3)
    return directory


def test_full_and_stepwise_preserve_independent_phase_median_and_constant_curve(tmp_path):
    one = build_run(tmp_path / "one")
    two = build_run(tmp_path / "two")
    original = yaml.safe_load((one / "experiment_config.yaml").read_text(encoding="utf-8"))["parameters"]
    complete = analysis.analyze(one)
    stepwise = analysis.AnalysisWorkflow(two)
    with pytest.raises(ValueError, match="缺少前置"):
        stepwise.execute(["B03"])
    for stage in stepwise.stages:
        stepwise.execute([stage.id])
    assert complete["success"] and stepwise.options == {}
    for directory in (one, two):
        with np.load(directory / "results/optimal_control_median_response.npz") as data:
            np.testing.assert_array_equal(data["response_median_v"], [2., 5., 8.])
            np.testing.assert_array_equal(data["valid_phase_count"], [3, 2, 3])
        report = yaml.safe_load((directory / "results/analysis.yaml").read_text(encoding="utf-8"))
        assert report["constant_control"]["response_v"] == [.1, .2, .3]
        assert (directory / "results/rf_frequency_response_comparison.pdf").is_file()
    assert yaml.safe_load((one / "experiment_config.yaml").read_text(encoding="utf-8"))["parameters"] == original
    with pytest.raises(ValueError, match="没有可调"):
        analysis.AnalysisWorkflow(one, {"FIT_R_SQUARED_MIN": .1})


def test_content_fingerprint_detects_same_size_same_timestamp_and_preserves_previous_products(tmp_path):
    directory = build_run(tmp_path)
    analysis.analyze(directory)
    path = directory / "raw" / analysis_steps.OPTIMAL_CONTROL_NPZ
    stat = path.stat()
    with np.load(path, allow_pickle=False) as data:
        values = {key: data[key] for key in data.files}
    values["r_mean_v"][0, 0] = 11.
    np.savez(path, **values)
    assert path.stat().st_size == stat.st_size
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    fresh = analysis.AnalysisWorkflow(directory)
    assert fresh.states["B01"]["status"] == "stale"
    old = (directory / "results/analysis.yaml").read_bytes()
    fresh.execute(["B01", "B02", "B03"])
    backups = list((directory / "results").glob("before_*/analysis.yaml"))
    assert any(path.read_bytes() == old for path in backups)
    median = directory / "results/optimal_control_median_response.npz"
    median_bytes = median.read_bytes()
    fresh = analysis.AnalysisWorkflow(directory)
    fresh.execute(["B03"])
    assert median.read_bytes() == median_bytes
    with np.load(path, allow_pickle=False) as data:
        values = {key: data[key] for key in data.files}
    values["frequency_hz"] = np.array([100., 100., 300.])
    np.savez(path, **values)
    before = sorted(str(path) for path in (directory / "results").glob("before_*"))
    with pytest.raises(ValueError, match="频率轴"):
        analysis.AnalysisWorkflow(directory).execute(["B01"])
    assert sorted(str(path) for path in (directory / "results").glob("before_*")) == before


def test_quality_failure_keeps_diagnostics_and_blocks_report(tmp_path):
    directory = build_run(tmp_path)
    path = directory / "raw" / analysis_steps.OPTIMAL_CONTROL_NPZ
    with np.load(path, allow_pickle=False) as data:
        values = {key: data[key] for key in data.files}
    values["r_mean_v"][1] = np.nan
    np.savez(path, **values)
    ctx = analysis.AnalysisWorkflow(directory)
    ctx.execute(["B01"])
    with pytest.raises(analysis_steps.AnalysisQualityError):
        ctx.execute(["B02", "B03"])
    assert ctx.states["B02"]["status"] == "quality_failed"
    assert (directory / "results/median_diagnostics.yaml").is_file()
    with pytest.raises(ValueError, match="缺少前置"):
        ctx.execute(["B03"])


@pytest.mark.parametrize("key,value", [("raw_format_version", True), ("raw_format_version", 1.0), ("raw_format_version", "1"),
    ("raw_format_version", 2), ("experiment_id", "other"), ("data_type", "other"), ("completion_status", "running")])
def test_format_rejects_ambiguous_identity_and_unsealed_inputs(tmp_path, key, value):
    directory = build_run(tmp_path)
    path = directory / "experiment_config.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config[key] = value
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    with pytest.raises(ValueError):
        analysis.AnalysisWorkflow(directory)


@pytest.mark.parametrize("schema,version,allowed", [(1, None, False), (2, None, True), (3, None, True), (99, None, False), (99, 1, True)])
def test_parameter_schema_independent_of_explicit_raw_format(tmp_path, schema, version, allowed):
    directory = build_run(tmp_path, comparison=False, schema=schema, raw_version=version)
    if allowed:
        analysis.analyze(directory)
        assert not (directory / "results/rf_frequency_response_comparison.png").exists()
    else:
        with pytest.raises(ValueError):
            analysis.AnalysisWorkflow(directory)


@pytest.mark.parametrize("mode", ["explicit", "missing", "nonexistent"])
def test_real_module_outside_repository_uses_explicit_run(tmp_path, mode):
    from lab_workflows.experiments.test_analysis_imports import check_real_analysis_module

    run = build_run(tmp_path)
    check_real_analysis_module(run, __package__ + ".analysis", "optimal_control_median_response.npz", mode)
