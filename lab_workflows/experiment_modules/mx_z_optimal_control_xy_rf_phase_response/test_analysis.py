"""离线阶段等价、格式资格、内容指纹、产物和覆盖值隔离。"""
from pathlib import Path
from copy import deepcopy
import hashlib
import json
import os
import subprocess
import sys
import numpy as np
import pytest
import yaml
from . import analysis, analysis_steps, run_format
from .models import MxZOptimalControlXYRFPhaseResponseParams
from .phase import dispersion_phase_response
from .scan import build_axes
from .stages import ANALYSIS_KEYS
from .test_workflow import parameters


def synthetic_run(root, name="1016_historical"):
    directory = root / name
    (directory / "raw").mkdir(parents=True)
    params = parameters(root)
    x, y, configured, phase = build_axes(params)
    shape = (1, 1)
    data = {key: np.zeros(shape) for key in (
        "baseline_v", "amplitude_v", "phase_zero_deg", "selected_phase_deg", "r_squared",
        "diagnostic_baseline_v", "diagnostic_amplitude_v", "diagnostic_phase_zero_deg",
        "diagnostic_baseline_uncertainty_v", "diagnostic_amplitude_uncertainty_v", "diagnostic_phase_zero_uncertainty_deg",
        "diagnostic_selected_phase_deg", "diagnostic_observed_max_phase_deg", "diagnostic_r_squared")}
    mean = dispersion_phase_response(phase, .0002, .02, .007, .012, 30., params.phase_cal_rf_amplitude_vpp)
    data.update(x_field_v=x, y_field_v=y, configured_phase_deg=configured, acquisition_phase_deg=phase,
        r_mean_v=mean.reshape(1, 1, -1), r_std_v=np.full((1, 1, phase.size), 1e-6),
        acquisition_order=np.array([[0]]), fit_accepted=np.ones(shape, dtype=bool),
        fit_rejection_reason=np.full(shape, ""), diagnostic_fit_accepted=np.ones(shape, dtype=bool),
        diagnostic_fit_rejection_reason=np.full(shape, ""), diagnostic_model=np.full(shape, "C + A*abs(sin(phi-phi0))"),
        actual_rate_sa_s=np.float64(1000.))
    np.savez(directory / "raw/xy_rf_phase_response_scan.npz", **data)
    config = dict(experiment_id=run_format.EXPERIMENT_ID, data_type=run_format.DATA_TYPE, schema_version=2,
        raw_format_version=1, measurement_mode="xy_grid_rf_phase_response", completion_status="completed",
        parameters=params.to_external() | {"RUN_TAG": "historical"},
        safety_shutdown={"completed": True}, main_field_restore={"success": True})
    (directory / "experiment_config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    return directory, data


def rewrite(directory, **changes):
    path = directory / "raw/xy_rf_phase_response_scan.npz"
    with np.load(path) as source:
        values = {key: source[key] for key in source.files}
    np.savez(path, **(values | changes))


def test_full_and_staged_same_scientific_results_and_readonly_snapshot(tmp_path):
    full, _ = synthetic_run(tmp_path, "full")
    staged, _ = synthetic_run(tmp_path, "staged")
    before = yaml.safe_load((staged / "experiment_config.yaml").read_text(encoding="utf-8"))["parameters"]
    expected = analysis.analyze(full)
    ctx = analysis.AnalysisWorkflow(staged)
    for step in ("B01", "B02", "B03"):
        ctx.execute([step])
    actual = json.loads((staged / "results/analysis.json").read_text(encoding="utf-8"))
    assert {key: value for key, value in expected.items() if key != "run_dir"} == {
        key: value for key, value in actual.items() if key != "run_dir"}
    assert "best_xy" not in actual
    assert ctx.summary["status"] == "completed"
    assert all(state["status"] == "completed" for state in ctx.states.values())
    assert len(list((staged / "results").glob("*.png"))) == 3
    assert len(list((staged / "results").glob("*.pdf"))) == 3
    state = (staged / "analysis_state.json").read_bytes()
    stat = (staged / "analysis_state.json").stat()
    for _ in range(2):
        analysis.AnalysisWorkflow(staged).snapshot()
    assert (staged / "analysis_state.json").read_bytes() == state
    assert (staged / "analysis_state.json").stat().st_mtime_ns == stat.st_mtime_ns
    assert yaml.safe_load((staged / "experiment_config.yaml").read_text(encoding="utf-8"))["parameters"] == before


def test_dependencies_overrides_and_finite_validation_do_not_change_acquisition(tmp_path):
    directory, _ = synthetic_run(tmp_path)
    before = yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf-8"))["parameters"]
    ctx = analysis.AnalysisWorkflow(directory, {ANALYSIS_KEYS[0]: .999999, ANALYSIS_KEYS[1]: 1000000000.})
    with pytest.raises(ValueError, match="缺少前置"):
        ctx.execute(["B02"])
    ctx.execute(["B01", "B02", "B03"])
    report = json.loads((directory / "results/analysis.json").read_text(encoding="utf-8"))
    assert report["nonlinear_fit_accepted_count"] == 0
    assert report["nonlinear_invalid_points"] and report["nonlinear_parameter_maps"]["scale"]
    assert yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf-8"))["parameters"] == before
    assert analysis.AnalysisWorkflow(directory).options[ANALYSIS_KEYS[1]] == 1000000000.
    for values in ({"RUN_TAG": "wrong"}, {ANALYSIS_KEYS[0]: float("nan")}, {ANALYSIS_KEYS[1]: -1.}, {ANALYSIS_KEYS[0]: True}):
        with pytest.raises(ValueError):
            analysis.AnalysisWorkflow(directory, values)


@pytest.mark.parametrize("version", [1, 2])
def test_explicit_legacy_qualification_and_no_external_sources(tmp_path, version):
    directory, _ = synthetic_run(tmp_path)
    path = directory / "experiment_config.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config.pop("raw_format_version")
    config["schema_version"] = version
    config["parameters"]["CONTROL_RESULTS_ROOT"] = "missing-forever"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    analysis.AnalysisWorkflow(directory).execute(["B01"])
    assert not run_format.analysis_block_reason(config)


@pytest.mark.parametrize("change", [{"raw_format_version": True}, {"raw_format_version": 99},
    {"completion_status": "running"}, {"experiment_id": "other"}, {"safety_shutdown": {"completed": False}},
    {"main_field_restore": {"success": False}}])
def test_invalid_run_rejected_before_any_output(tmp_path, change):
    directory, _ = synthetic_run(tmp_path)
    path = directory / "experiment_config.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8")) | change
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    with pytest.raises(ValueError):
        analysis.AnalysisWorkflow(directory)
    assert not (directory / "results").exists()


@pytest.mark.parametrize("change", [{"r_mean_v": np.full((1, 1, 8), np.nan)}, {"r_std_v": np.full((1, 1, 8), -1.)},
    {"actual_rate_sa_s": np.float64(0)}, {"acquisition_order": np.array([[2]])}, {"cross_phase_reacquired": np.ones((2, 1, 8))},
    {"acquisition_phase_deg": np.arange(8) * 45.}, {"fit_accepted": np.full((1, 1), np.nan)},
    {"cross_phase_reacquired": np.full((1, 1, 8), 2)}])
def test_bad_raw_data_preserves_last_report(tmp_path, change):
    directory, _ = synthetic_run(tmp_path)
    results = directory / "results"
    results.mkdir()
    report = results / "analysis.json"
    report.write_text('{"old": true}', encoding="utf-8")
    before = report.read_bytes()
    rewrite(directory, **change)
    with pytest.raises(ValueError):
        analysis.AnalysisWorkflow(directory).execute(["B01"])
    assert report.read_bytes() == before
    assert not list(results.glob("before_*"))


def test_content_and_product_fingerprints_rerun_archive_and_stale_revision(tmp_path):
    directory, values = synthetic_run(tmp_path)
    ctx = analysis.AnalysisWorkflow(directory)
    ctx.execute(["B01", "B02", "B03"])
    b01 = deepcopy(ctx.states["B01"])
    report = (directory / "results/analysis.json").read_bytes()
    changed = analysis.AnalysisWorkflow(directory, {ANALYSIS_KEYS[0]: .2})
    assert changed.states["B01"] == b01 and changed.states["B02"]["status"] == "stale"
    changed.execute(["B02", "B03"])
    assert changed.states["B01"] == b01
    assert any(path.read_bytes() == report for folder in (directory / "results").glob("before_*") for path in folder.glob("analysis.json"))
    figure = directory / "results/xy_rf_phase_response_maps.png"
    stat = figure.stat()
    content = bytearray(figure.read_bytes())
    content[-1] ^= 1
    figure.write_bytes(content)
    os.utime(figure, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    stale = analysis.AnalysisWorkflow(directory)
    assert stale.states["B02"]["status"] == "stale" and stale.states["B03"]["status"] == "stale"
    path = directory / "raw/xy_rf_phase_response_scan.npz"
    stat = path.stat()
    rewrite(directory, r_mean_v=values["r_mean_v"] + 1e-5)
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    stale = analysis.AnalysisWorkflow(directory)
    assert stale.states["B01"]["status"] == "stale"
    with pytest.raises(ValueError, match="修订"):
        stale.execute(["B01"], 0)


def test_changed_raw_during_command_stops_later_stage(tmp_path, monkeypatch):
    directory, values = synthetic_run(tmp_path)
    original = analysis_steps.load_inputs
    count = 0
    def mutate(path):
        nonlocal count
        inputs = original(path)
        count += 1
        if count == 1:
            rewrite(directory, r_mean_v=values["r_mean_v"] + 1e-5)
        return inputs
    monkeypatch.setattr(analysis_steps, "load_inputs", mutate)
    ctx = analysis.AnalysisWorkflow(directory)
    with pytest.raises(ValueError, match="执行中改变"):
        ctx.execute(["B01", "B02", "B03"])
    assert ctx.states["B01"]["status"] == "failed" and ctx.states["B02"]["status"] == "pending"


def test_real_analysis_subprocess_produces_artifacts_and_requires_explicit_directory(tmp_path):
    from .definition import ADAPTER
    directory, _ = synthetic_run(tmp_path)
    root = Path(__file__).resolve().parents[3]
    result = ADAPTER.analyze(directory, None)
    assert result["run_dir"] == str(directory.resolve()) and result["artifacts"]
    assert (directory / "results/analysis.json").is_file()
    assert (directory / "results/xy_rf_phase_response_nonlinear_maps.pdf").is_file()
    result = subprocess.run([sys.executable, str(root / "experiments/Mx_Z_Optimal_Control_XY_RF_Phase_Response_plot.py")],
        capture_output=True, text=True, timeout=20)
    assert result.returncode != 0 and "run_dir" in result.stderr
