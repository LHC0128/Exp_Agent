"""真实科学拟合、文件产物、历史资格与目录隔离的无硬件回归。"""
from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest
import yaml

from ...current_feedback import load_current_coupling_calibration, sha256_file
from ...experiment_runtime import WorkflowCancelled
from ..mx_y_rf_sensitivity.analysis_core import lorentzian_response
from . import analysis, analysis_steps, workflow
from .run_format import DATA_TYPE, EXPERIMENT_ID, analysis_block_reason
from .stages import ANALYSIS, ANALYSIS_KEYS, Stage, definition, validate_selection
from .test_workflow import copy_config, install_rig, small_params


REGRESSION_CASES = {
    "mx-z-current-coupling-calibration": {
        "analysis": {
            "sources": [
                "lab_workflows/experiment_modules/mx_z_current_coupling_calibration/__init__.py",
                "lab_workflows/experiment_modules/mx_z_current_coupling_calibration/analysis.py"
            ],
            "tests": [
                "test_real_acquisition_full_and_stages_have_identical_scientific_results",
                "test_weighted_current_voltage_hysteresis_repeatability_and_legacy_exports",
                "test_signed_coefficients_and_uncertainties_match_independent_weighted_sums",
                "test_known_raw_format_is_independent_of_parameter_schema_and_optional_legacy_fields",
                "test_unknown_identity_format_and_active_acquisition_are_rejected",
                "test_raw_qualification_failure_retains_diagnostic_and_blocks_fit",
                "test_missing_prerequisites_duplicate_selection_and_revision_do_not_execute",
                "test_quality_failure_exports_both_diagnostic_plots_and_strict_json",
                "test_interrupted_real_acquisition_uses_only_completed_z_summaries",
                "test_analysis_override_isolated_from_snapshot_defaults_and_other_directories",
                "test_invalid_or_acquisition_only_overrides_are_rejected_without_writes",
                "test_changed_inputs_and_artifacts_are_not_reused_even_with_same_stat",
                "test_cancel_during_real_fit_retains_completed_curves_and_blocks_linear_fit",
                "test_raw_change_during_one_selected_command_blocks_later_stages",
                "test_stored_override_without_matching_parameter_fingerprint_is_not_reused",
                "test_report_write_failure_keeps_predecessors_and_original_exception",
                "test_status_write_failure_does_not_truncate_acquisition_config",
                "test_module_and_existing_plot_cli_execute_real_analysis"
            ]
        }
    }
}


def read_json(path):
    def reject(value):
        raise AssertionError("JSON 不允许非有限常量：" + value)
    return json.loads(path.read_text(encoding="utf-8"), parse_constant=reject)


def read_config(run_dir):
    return yaml.safe_load((run_dir / "experiment_config.yaml").read_text(encoding="utf-8"))


def write_config(run_dir, config):
    (run_dir / "experiment_config.yaml").write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")


def change_npz(path, *, remove=(), **changes):
    with np.load(path, allow_pickle=False) as data:
        values = {key: data[key].copy() for key in data.files if key not in remove}
    np.savez(path, **(values | changes))


def synthetic_run(root, name="1014_120000_mx_current", *, legacy=False):
    run_dir = root / "data" / DATA_TYPE / name
    (run_dir / "raw").mkdir(parents=True)
    (run_dir / "results").mkdir()
    params = replace(small_params(), linear_r_squared_min=.8)
    config = dict(experiment_id=EXPERIMENT_ID, experiment_type=DATA_TYPE, data_type=DATA_TYPE,
        schema_version=1 if legacy else params.schema_version, parameters=params.to_external(),
        completion_status="completed", geometry=dict(main_field="Z", pump="Z", probe="X", rf_field="Y",
                                                     z_dc_scan="forward_then_reverse"))
    if not legacy:
        config["raw_format_version"] = 1
    write_config(run_dir, config)
    currents = np.asarray([-.02, -.01, 0, .01, .02, .01, 0, -.01, -.02])
    summaries = []
    for index, current in enumerate(currents):
        reverse = index >= 5
        center = 90000 + current * 50000 + (8 + 200 * current if reverse else 0)
        frequency = center + np.linspace(-1200, 1200, 41)
        response = lorentzian_response(frequency, .5, 250, center, .1)
        response += .0001 * (index + 1) * np.sin(np.arange(frequency.size) * .8)
        point_dir = run_dir / "raw" / f"z_{index:03d}"
        point_dir.mkdir()
        summary = point_dir / "frequency_scan.npz"
        np.savez(summary, z_bias_v=np.float64(index / 10), predicted_center_hz=np.float64(center),
            frequency_hz=frequency, r_mean_v=response, r_scalar_mean_v=response,
            r_std_v=np.full(41, .002), current_mean_a=np.float64(current),
            current_std_a=np.float64(.0001 + index * .00001),
            sense_voltage_mean_v=np.float64(current * params.sense_resistor_ohm),
            sense_voltage_std_v=np.float64(.001 + index * .0001),
            sense_display_edge_fraction=np.float64(.2), scan_direction=np.asarray("reverse" if reverse else "forward"))
        summaries.append(summary.relative_to(run_dir).as_posix())
    np.savez(run_dir / "raw/z_scan_index.npz", z_bias_v=np.arange(9) / 10,
        predicted_center_hz=np.full(9, 90000), summary_file=np.asarray(summaries))
    return run_dir


@pytest.fixture(autouse=True)
def quiet_progress(monkeypatch):
    monkeypatch.setattr(analysis, "report_runtime_progress", lambda *args, **kwargs: None)


def test_real_acquisition_full_and_stages_have_identical_scientific_results(tmp_path, monkeypatch):
    copy_config(tmp_path)
    rig = install_rig(tmp_path, monkeypatch)
    run_dir = workflow.run(rig.params)
    raw = {path: sha256_file(path) for path in (run_dir / "raw").rglob("*.npz")}
    acquisition_parameters = read_config(run_dir)["parameters"]
    complete = analysis.analyze(run_dir)
    assert complete["success"]
    assert complete["K_Z_Hz_per_A"] == pytest.approx(100000, rel=1e-6)
    assert complete["K_Z_Hz_per_Vsense"] == pytest.approx(10000, rel=1e-6)
    assert complete["f_0_at_zero_current_hz"] == pytest.approx(89900, abs=1e-5)
    assert complete["hysteresis"]["available"]
    assert complete["total_curve_count"] == complete["accepted_curve_count"] == 5
    with np.load(run_dir / "results/calibration_results.npz") as data:
        expected = {key: data[key].copy() for key in data.files}
    for stage in ANALYSIS:
        ctx = analysis.AnalysisWorkflow(run_dir)
        ctx.execute([stage.id], expected_revision=ctx.revision)
    assert read_json(run_dir / "results/analysis.json") == complete
    with np.load(run_dir / "results/calibration_results.npz") as actual:
        assert set(actual.files) == set(expected)
        for key, value in expected.items():
            np.testing.assert_array_equal(actual[key], value)
    assert {path: sha256_file(path) for path in raw} == raw
    assert read_config(run_dir)["parameters"] == acquisition_parameters
    for filename in ("current_coupling_calibration.png", "frequency_response_fits.png"):
        assert (run_dir / "results" / filename).stat().st_size > 1000
    loaded = load_current_coupling_calibration(tmp_path, run_dir.name)
    assert loaded.slope_hz_per_a == complete["K_Z_Hz_per_A"]


def test_weighted_current_voltage_hysteresis_repeatability_and_legacy_exports(tmp_path):
    run_dir = synthetic_run(tmp_path)
    params, config = analysis._load_params(run_dir)
    entries = analysis._load_scan_index(run_dir / "raw")
    curves = [analysis._analyze_current_curve(params, run_dir, entry) for entry in entries]
    assert all(curve["fit"]["success"] for curve in curves)
    expected = analysis._weighted_linear_fit(
        np.array([curve["current_a"] for curve in curves]),
        np.array([curve["fit"]["parameters"][2] for curve in curves]),
        np.array([curve["fit"]["center_uncertainty_hz"] for curve in curves]))
    payload = analysis.analyze(run_dir)
    assert payload["K_Z_Hz_per_A"] == pytest.approx(expected["slope_hz_per_v"])
    assert payload["K_Z_Hz_per_Vsense"] * params.sense_resistor_ohm == pytest.approx(payload["K_Z_Hz_per_A"])
    assert payload["f_0_at_zero_current_hz"] == pytest.approx(expected["intercept_hz"])
    forward, reverse = payload["hysteresis"]["forward_fit"], payload["hysteresis"]["reverse_fit"]
    assert forward["slope_hz_per_v"] == pytest.approx(50000, rel=.001)
    assert reverse["slope_hz_per_v"] == pytest.approx(50200, rel=.001)
    assert payload["hysteresis"]["zero_current_center_difference_hz"] == pytest.approx(8, abs=.2)
    assert payload["repeatability"]["current_std_a_mean"] == pytest.approx(.00014)
    assert payload["repeatability"]["sense_voltage_std_v_mean"] == pytest.approx(.0014)
    assert payload["operating_point"]["geometry"] == config["geometry"]
    assert analysis._analyze_current_curve is analysis_steps._analyze_current_curve


@pytest.mark.parametrize("polarity", [-1, 1])
def test_signed_coefficients_and_uncertainties_match_independent_weighted_sums(tmp_path, polarity):
    run_dir = synthetic_run(tmp_path)
    for path in (run_dir / "raw").glob("z_*/frequency_scan.npz"):
        with np.load(path) as data:
            current = float(data["current_mean_a"]) * polarity
            voltage = float(data["sense_voltage_mean_v"]) * polarity
        change_npz(path, current_mean_a=np.float64(current), sense_voltage_mean_v=np.float64(voltage))
    raw = {path: sha256_file(path) for path in (run_dir / "raw").rglob("*.npz")}
    payload = analysis.analyze(run_dir)
    assert payload["success"] and payload["accepted_curve_count"] == 9
    assert np.sign(payload["K_Z_Hz_per_A"]) == polarity
    assert np.sign(payload["K_Z_Hz_per_Vsense"]) == polarity
    centers = np.asarray([curve["fit"]["parameters"][2] for curve in payload["curves"]])
    sigma = np.asarray([curve["fit"]["center_uncertainty_hz"] for curve in payload["curves"]])
    assert np.all(sigma > 0) and np.ptp(sigma) > 0
    weights = 1 / sigma**2
    for coordinate, name in (("current_a", "fit"), ("sense_voltage_v", "voltage_fit")):
        x = np.asarray([curve[coordinate] for curve in payload["curves"]])
        x_mean = np.average(x, weights=weights)
        y_mean = np.average(centers, weights=weights)
        sxx = np.sum(weights * (x - x_mean)**2)
        slope = np.sum(weights * (x - x_mean) * (centers - y_mean)) / sxx
        intercept = y_mean - slope * x_mean
        residual = centers - slope * x - intercept
        variance = np.sum(weights * residual**2) / (len(x) - 2)
        covariance = variance * np.asarray([[1 / sxx, -x_mean / sxx],
            [-x_mean / sxx, 1 / weights.sum() + x_mean**2 / sxx]])
        fit = payload["linearity"][name]
        assert fit["slope_hz_per_v"] == pytest.approx(slope, rel=1e-8)
        assert fit["intercept_hz"] == pytest.approx(intercept, abs=1e-7)
        np.testing.assert_allclose(fit["covariance"], covariance, rtol=1e-7, atol=1e-12)
        np.testing.assert_allclose(fit["uncertainties"], np.sqrt(np.diag(covariance)), rtol=1e-7)
        np.testing.assert_allclose(fit["residual_hz"], residual, rtol=1e-7, atol=1e-7)
    assert payload["hysteresis"]["available"]
    assert payload["repeatability"]["current_std_a_mean"] > 0
    with np.load(run_dir / "results/calibration_results.npz") as data:
        assert float(data["K_Z_Hz_per_A"]) == payload["K_Z_Hz_per_A"]
        assert float(data["K_Z_Hz_per_Vsense"]) == payload["K_Z_Hz_per_Vsense"]
        np.testing.assert_allclose(data["sense_voltage_v"], data["current_a"] * payload["sense_resistor_ohm"])
        np.testing.assert_allclose(data["center_uncertainty_hz"], sigma)
    assert {path: sha256_file(path) for path in raw} == raw


@pytest.mark.parametrize("legacy", [False, True])
def test_known_raw_format_is_independent_of_parameter_schema_and_optional_legacy_fields(tmp_path, legacy):
    run_dir = synthetic_run(tmp_path, legacy=legacy)
    for path in (run_dir / "raw").glob("z_*/frequency_scan.npz"):
        change_npz(path, remove=("r_mean_v", "sense_display_edge_fraction", "scan_direction"))
    payload = analysis.analyze(run_dir)
    assert payload["success"]
    assert all(curve["display_edge_fraction"] is None and curve["scan_direction"] == "forward"
               for curve in payload["curves"])
    assert not payload["hysteresis"]["available"]
    assert read_config(run_dir)["schema_version"] == (1 if legacy else 2)
    if legacy:
        assert "raw_format_version" not in read_config(run_dir)


@pytest.mark.parametrize("change", [
    {"experiment_id": "mx-z-field-calibration"}, {"data_type": "Mx_Z_Field_Calibration"},
    {"raw_format_version": 2}, {"raw_format_version": True},
    {"raw_format_version": None, "geometry": {}},
    {"completion_status": "running"}, {"completion_status": "acquired"},
])
def test_unknown_identity_format_and_active_acquisition_are_rejected(tmp_path, change):
    run_dir = synthetic_run(tmp_path)
    config = read_config(run_dir) | change
    write_config(run_dir, config)
    assert analysis_block_reason(config)
    with pytest.raises(ValueError):
        analysis.AnalysisWorkflow(run_dir)
    assert not (run_dir / "analysis_state.json").exists()


@pytest.mark.parametrize("kind", ["missing_current", "shape", "outside", "duplicate", "empty"])
def test_raw_qualification_failure_retains_diagnostic_and_blocks_fit(tmp_path, kind):
    run_dir = synthetic_run(tmp_path)
    path = run_dir / "raw/z_000/frequency_scan.npz"
    if kind == "missing_current":
        change_npz(path, remove=("current_mean_a",))
    elif kind == "shape":
        change_npz(path, r_std_v=np.zeros(2))
    elif kind == "empty":
        change_npz(path, frequency_hz=np.array([]), r_mean_v=np.array([]), r_std_v=np.array([]))
    else:
        with np.load(run_dir / "raw/z_scan_index.npz") as data:
            files = data["summary_file"].astype("U200")
        files[0] = "../outside.npz" if kind == "outside" else files[1]
        change_npz(run_dir / "raw/z_scan_index.npz", summary_file=files)
    ctx = analysis.AnalysisWorkflow(run_dir)
    with pytest.raises(ValueError):
        ctx.execute(["B01", "B02"])
    state = read_json(run_dir / "analysis_state.json")
    assert state["steps"]["B01"]["status"] == "quality_failed"
    assert state["summary"]["error"]
    with pytest.raises(ValueError, match="缺少前置"):
        ctx.execute(["B02"])
    assert not (run_dir / "results/curve_fits.json").exists()


def test_missing_prerequisites_duplicate_selection_and_revision_do_not_execute(tmp_path):
    run_dir = synthetic_run(tmp_path)
    ctx = analysis.AnalysisWorkflow(run_dir)
    for selected in (["B03"], [], ["B01", "B01"]):
        with pytest.raises(ValueError):
            ctx.execute(selected)
    with pytest.raises(ValueError, match="修订"):
        ctx.execute(["B01"], expected_revision=999)
    assert not (run_dir / "analysis_state.json").exists()


@pytest.mark.parametrize("kind", ["saturated", "threshold", "all_excluded"])
def test_quality_failure_exports_both_diagnostic_plots_and_strict_json(tmp_path, kind):
    run_dir = synthetic_run(tmp_path)
    options = {"LINEAR_R_SQUARED_MIN": 1.0} if kind == "threshold" else None
    if kind == "saturated":
        change_npz(run_dir / "raw/z_000/frequency_scan.npz", sense_display_edge_fraction=np.float64(.98))
    if kind == "all_excluded":
        for path in (run_dir / "raw").glob("z_*/frequency_scan.npz"):
            with np.load(path) as data:
                changes = {key: data[key][:4] for key in ("frequency_hz", "r_mean_v", "r_scalar_mean_v", "r_std_v")}
            change_npz(path, **changes)
    payload = analysis.analyze(run_dir, analysis_parameters=options)
    assert payload["success"] is False and payload["rejection_reasons"]
    ctx = analysis.AnalysisWorkflow(run_dir)
    assert ctx.states["B04"]["status"] == "quality_failed"
    assert ctx.summary["status"] == "quality_failed"
    stages = (*ANALYSIS, Stage("B05", "仅验证依赖阻断", "测试", ("B04",)))
    with pytest.raises(ValueError, match="缺少前置"):
        validate_selection(stages, ctx.states, ["B05"])
    for stage in ANALYSIS:
        for name in stage.artifacts:
            assert (run_dir / "results" / name).is_file()
    for path in (run_dir / "results").glob("*.json"):
        read_json(path)
    read_json(run_dir / "analysis_state.json")
    if kind == "all_excluded":
        assert payload["accepted_curve_count"] == 0 and payload["K_Z_Hz_per_A"] is None
        with np.load(run_dir / "results/calibration_results.npz") as data:
            assert np.isnan(data["K_Z_Hz_per_A"]) and np.isnan(data["K_Z_Hz_per_Vsense"])
            assert np.all(np.isnan(data["center_uncertainty_hz"]))
        with pytest.raises(ValueError, match="未通过质量检查"):
            load_current_coupling_calibration(tmp_path, run_dir.name)


def test_interrupted_real_acquisition_uses_only_completed_z_summaries(tmp_path, monkeypatch):
    copy_config(tmp_path)
    rig = install_rig(tmp_path, monkeypatch)
    rig.fault_index, rig.acquire_error = 10, OSError("下一 Z 频扫中断")
    with pytest.raises(OSError, match="下一 Z"):
        workflow.run(rig.params)
    run_dir = next((tmp_path / "data" / DATA_TYPE).iterdir())
    raw = {path: sha256_file(path) for path in (run_dir / "raw").rglob("*.npz")}
    payload = analysis.analyze(run_dir)
    assert payload["total_curve_count"] == 1 and payload["success"] is False
    assert not read_json(run_dir / "results/analysis_inputs.json")["scan_index_present"]
    assert not (run_dir / "raw/z_001/frequency_scan.npz").exists()
    assert {path: sha256_file(path) for path in raw} == raw


def test_analysis_override_isolated_from_snapshot_defaults_and_other_directories(tmp_path):
    run_dir = synthetic_run(tmp_path)
    other = synthetic_run(tmp_path, "1014_120001_other")
    defaults = tmp_path / "params/experiments" / (EXPERIMENT_ID + ".yaml")
    defaults.parent.mkdir(parents=True)
    defaults.write_text("parameters:\n  LINEAR_R_SQUARED_MIN: 0.2\n", encoding="utf-8")
    saved_defaults = defaults.read_bytes()
    historical = read_config(run_dir)
    analysis.analyze(run_dir)
    fits = sha256_file(run_dir / "results/curve_fits.json")
    ctx = analysis.AnalysisWorkflow(run_dir, {"LINEAR_R_SQUARED_MIN": 1.0})
    assert ctx.states["B01"]["status"] == ctx.states["B02"]["status"] == "completed"
    assert ctx.states["B03"]["status"] == ctx.states["B04"]["status"] == "stale"
    result = ctx.execute(["B03", "B04"], expected_revision=ctx.revision)
    assert result["status"] == "quality_failed"
    backup = run_dir / "results" / result["backup_directory"]
    assert (backup / "calibration_evaluation.json").exists()
    assert (backup / "analysis_state.json").exists()
    assert not (backup / "curve_fits.json").exists()
    assert sha256_file(run_dir / "results/curve_fits.json") == fits
    assert read_config(run_dir)["parameters"] == historical["parameters"]
    assert defaults.read_bytes() == saved_defaults
    assert analysis.AnalysisWorkflow(other).options == {"LINEAR_R_SQUARED_MIN": .8}
    assert analysis.AnalysisWorkflow(run_dir).options == {"LINEAR_R_SQUARED_MIN": 1.0}
    assert definition()["parameter_impacts"]["analysis"] == {"LINEAR_R_SQUARED_MIN": ["B03", "B04"]}
    assert ANALYSIS_KEYS == ("LINEAR_R_SQUARED_MIN",)


@pytest.mark.parametrize("value", [True, None, "0.8", float("nan"), float("inf"), -1, 1.1])
def test_invalid_or_acquisition_only_overrides_are_rejected_without_writes(tmp_path, value):
    run_dir = synthetic_run(tmp_path)
    with pytest.raises(ValueError):
        analysis.AnalysisWorkflow(run_dir, {"LINEAR_R_SQUARED_MIN": value})
    with pytest.raises(ValueError, match="不支持"):
        analysis.AnalysisWorkflow(run_dir, {"SENSE_RESISTOR_OHM": 2})
    assert not (run_dir / "analysis_state.json").exists()


@pytest.mark.parametrize("target", ["raw", "fits", "report", "config"])
def test_changed_inputs_and_artifacts_are_not_reused_even_with_same_stat(tmp_path, target):
    run_dir = synthetic_run(tmp_path)
    evidence = run_dir / "raw/evidence.bin"
    evidence.write_bytes(b"original")
    analysis.analyze(run_dir)
    ctx = analysis.AnalysisWorkflow(run_dir)
    if target == "config":
        config = read_config(run_dir)
        config["parameters"]["SENSE_RESISTOR_OHM"] = 12
        write_config(run_dir, config)
    else:
        path = {"raw": evidence, "fits": run_dir / "results/curve_fits.json",
                "report": run_dir / "results/analysis.json"}[target]
        stat, content = path.stat(), path.read_bytes()
        path.write_bytes(bytes([content[0] ^ 1]) + content[1:])
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    selected = ["B03"] if target != "report" else ["B04"]
    if target == "report":
        ctx.execute(selected)
        assert read_json(run_dir / "results/analysis.json")["success"]
    else:
        with pytest.raises(ValueError, match="缺少前置"):
            ctx.execute(selected)
        first_stale = "B02" if target == "fits" else "B01"
        assert ctx.states[first_stale]["status"] == "stale"


def test_cancel_during_real_fit_retains_completed_curves_and_blocks_linear_fit(tmp_path, monkeypatch):
    run_dir = synthetic_run(tmp_path)
    ctx = analysis.AnalysisWorkflow(run_dir)
    ctx.execute(["B01"])
    checks = []
    def cancel():
        checks.append(True)
        if len(checks) == 2:
            raise WorkflowCancelled("分析拟合取消")
    monkeypatch.setattr(analysis_steps, "check_cancelled", cancel)
    with pytest.raises(WorkflowCancelled, match="分析拟合取消"):
        ctx.execute(["B02", "B03"])
    assert len(read_json(run_dir / "results/curve_fits.json")) == 1
    assert read_json(run_dir / "analysis_state.json")["steps"]["B02"]["status"] == "cancelled"
    with pytest.raises(ValueError, match="缺少前置"):
        ctx.execute(["B03"])


def test_raw_change_during_one_selected_command_blocks_later_stages(tmp_path, monkeypatch):
    run_dir = synthetic_run(tmp_path)
    evidence = run_dir / "raw/evidence.bin"
    evidence.write_bytes(b"before")
    fit = analysis_steps.fit_curves
    def changed(*args):
        result = fit(*args)
        evidence.write_bytes(b"after!")
        return result
    monkeypatch.setattr(analysis_steps, "fit_curves", changed)
    ctx = analysis.AnalysisWorkflow(run_dir)
    with pytest.raises(ValueError, match="缺少前置"):
        ctx.execute([stage.id for stage in ANALYSIS])
    assert ctx.states["B01"]["status"] == ctx.states["B02"]["status"] == "stale"
    assert not (run_dir / "results/calibration_evaluation.json").exists()


def test_stored_override_without_matching_parameter_fingerprint_is_not_reused(tmp_path):
    run_dir = synthetic_run(tmp_path)
    analysis.analyze(run_dir)
    state = read_json(run_dir / "analysis_state.json")
    state["options"]["LINEAR_R_SQUARED_MIN"] = 1.0
    (run_dir / "analysis_state.json").write_text(json.dumps(state, allow_nan=False), encoding="utf-8")
    ctx = analysis.AnalysisWorkflow(run_dir)
    assert ctx.states["B02"]["status"] == "completed"
    assert ctx.states["B03"]["status"] == ctx.states["B04"]["status"] == "stale"
    with pytest.raises(ValueError, match="缺少前置"):
        ctx.execute(["B04"])


def test_report_write_failure_keeps_predecessors_and_original_exception(tmp_path, monkeypatch):
    run_dir = synthetic_run(tmp_path)
    ctx = analysis.AnalysisWorkflow(run_dir)
    ctx.execute(["B01", "B02", "B03"])
    error = OSError("结果 NPZ 写盘失败")
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(analysis_steps.np, "savez", fail)
    with pytest.raises(OSError) as caught:
        ctx.execute(["B04"])
    assert caught.value is error
    state = read_json(run_dir / "analysis_state.json")
    assert state["steps"]["B04"]["status"] == "failed"
    assert state["steps"]["B03"]["status"] == "completed"
    assert (run_dir / "results/calibration_evaluation.json").exists()
    assert (run_dir / "results/analysis.json").exists()


def test_status_write_failure_does_not_truncate_acquisition_config(tmp_path, monkeypatch):
    run_dir = synthetic_run(tmp_path)
    config_path = run_dir / "experiment_config.yaml"
    before = config_path.read_bytes()
    ctx = analysis.AnalysisWorkflow(run_dir)
    write = Path.write_text
    error = OSError("配置状态写盘失败")
    def fail(path, value, *args, **kwargs):
        if path.name == "experiment_config.yaml.analysis.tmp":
            write(path, value[:8], *args, **kwargs)
            raise error
        return write(path, value, *args, **kwargs)
    monkeypatch.setattr(Path, "write_text", fail)
    with pytest.raises(OSError) as caught:
        ctx.execute(["B01"])
    assert caught.value is error and config_path.read_bytes() == before
    assert read_json(run_dir / "analysis_state.json")["steps"]["B01"]["status"] == "failed"


def test_module_and_existing_plot_cli_execute_real_analysis(tmp_path):
    run_dir = synthetic_run(tmp_path)
    root = Path(__file__).resolve().parents[3]
    environment = dict(os.environ, LAB_RUN_DIR=str(run_dir), LAB_TYPED_PARAMETERS="{}",
        PYTHONUTF8="1", PYTHONIOENCODING="utf-8", MPLBACKEND="Agg", LAB_PROGRESS_PROTOCOL="jsonl")
    environment.pop("LAB_CANCEL_FILE", None)
    module = "lab_workflows.experiment_modules.mx_z_current_coupling_calibration.analysis"
    result = subprocess.run([sys.executable, "-m", module], cwd=root, env=environment,
                            capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert read_json(run_dir / "results/analysis.json")["success"]
    for line in result.stdout.splitlines():
        if line.startswith("__LAB_PROGRESS__ "):
            json.loads(line.removeprefix("__LAB_PROGRESS__ "), parse_constant=lambda value: pytest.fail(value))
    result = subprocess.run([sys.executable, "experiments/Mx_Z_Current_Coupling_Calibration_plot.py", str(run_dir)],
        cwd=root, env=environment, capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    payload = read_json(run_dir / "results/analysis.json")
    assert payload["success"] and payload["total_curve_count"] == 9
    assert (run_dir / "results/frequency_response_fits.png").stat().st_size > 1000
