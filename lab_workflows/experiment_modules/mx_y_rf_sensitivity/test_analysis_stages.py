"""独立分析、历史兼容、产物指纹和阶段重算。"""
from pathlib import Path
from dataclasses import replace
import json
import os
import subprocess
import sys
import numpy as np
import pytest
import yaml
from . import workflow, analysis, analysis_steps
from .models import MxYRFParams
from .stages import ANALYSIS, definition
from .test_workflow import rig


REGRESSION_CASES = {
    "mx-y-rf-sensitivity": {
        "analysis": {
            "sources": [
                "lab_workflows/experiment_modules/mx_y_rf_sensitivity/analysis.py"
            ],
            "tests": [
                "test_full_and_stage_analysis_use_same_numbers_and_archive_only_dependants",
                "test_fingerprint_missing_files_and_legacy_format",
                "test_fit_quality_failure_saves_diagnostics_and_blocks_report",
                "test_analysis_real_module_entrypoint_writes_results_and_rejects_unknown_override",
                "test_real_module_outside_repository_uses_explicit_run",
                "test_analysis_model_bounds_without_hardware_revalidation",
                "test_real_caller_config_failure_preserves_bytes_and_original_error"
            ]
        }
    }
}


@pytest.mark.parametrize("linewidth_mode", ["amplitude_equivalent", "frequency_sweep"])
def test_full_and_stage_analysis_use_same_numbers_and_archive_only_dependants(rig, linewidth_mode):
    rig.params = replace(rig.params, linewidth_mode=linewidth_mode, frequency_start_hz=9000,
        frequency_stop_hz=11000, frequency_points=9)
    if linewidth_mode == "frequency_sweep":
        rig.params = replace(rig.params, noise_n_avg=12, noise_duration_s=4.096)
    directory = workflow.run(rig.params)
    original_parameters = yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf-8"))["parameters"]
    full = analysis.analyze(directory)
    with np.load(directory / "results/sensitivity.npz") as data:
        expected = data["corrected_vpp_per_sqrt_hz"].copy()
    ctx = analysis.AnalysisWorkflow(directory)
    ctx.execute(["B01", "B02"])
    evaluation = (directory / "results/evaluation.json").read_bytes()
    ctx.execute(["B03"])
    assert (directory / "results/evaluation.json").read_bytes() == evaluation
    with np.load(directory / "results/sensitivity.npz") as data:
        np.testing.assert_allclose(data["corrected_vpp_per_sqrt_hz"], expected)
    assert full["response_fit"] == ctx.summary["response_fit"]
    assert full["response_signal"] == full["noise_signal"] == "R"
    assert full["zero_point_method"]["name"] == "adaptive_zero_point_absolute_linear"
    with np.load(directory / "results/response_fit.npz") as data:
        assert data["primary_slope_v_per_vpp"] == full["primary_slope_v_per_vpp"]
        assert data["zero_point_slope_v_per_vpp"] == full["zero_point_method"]["slope_v_per_vpp"]
    for stem in ("amplitude_response", "noise_psd", "full_analysis", "full_analysis_zero_point"):
        assert (directory / f"results/{stem}.png").is_file()
        assert (directory / f"results/{stem}.pdf").is_file()
    if linewidth_mode == "frequency_sweep":
        gate = yaml.safe_load((directory / "results/frequency_gate.yaml").read_text(encoding="utf-8"))
        assert gate["success"] and gate["parameters"][1] == pytest.approx(300, rel=1e-5)
        assert (directory / "results/frequency_linewidth.png").is_file()
    override = analysis.AnalysisWorkflow(directory, {"LOW_FREQ_SKIP_HZ": 7})
    assert override.states["B01"]["status"] == "completed"
    assert override.states["B02"]["status"] == "stale"
    assert override.states["B03"]["status"] == "stale"
    assert set(definition()["parameter_impacts"]["analysis"]["LOW_FREQ_SKIP_HZ"]) == {"B02", "B03"}
    override.execute(["B02", "B03"])
    config = yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf-8"))
    assert config["parameters"] == original_parameters
    assert config["parameters"]["LOW_FREQ_SKIP_HZ"] != override.options["LOW_FREQ_SKIP_HZ"]
    assert list((directory / "results").glob("before_*/analysis.yaml"))


def test_fingerprint_missing_files_and_legacy_format(rig):
    directory = workflow.run(rig.params)
    config_path = directory / "experiment_config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config.pop("raw_format_version")
    config["schema_version"] = 2
    config_path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    ctx = analysis.AnalysisWorkflow(directory)
    ctx.execute(["B01", "B02"])
    (directory / "results/evaluation.json").unlink()
    refreshed = analysis.AnalysisWorkflow(directory)
    assert refreshed.states["B02"]["status"] == "stale"
    path = directory / "raw/noise_001.npz"
    path.unlink()
    refreshed = analysis.AnalysisWorkflow(directory)
    assert refreshed.states["B01"]["status"] == "stale"
    with pytest.raises(FileNotFoundError, match="noise_001"):
        refreshed.execute(["B01"])
    with pytest.raises(ValueError, match="缺少前置"):
        refreshed.execute(["B02"])


def test_fit_quality_failure_saves_diagnostics_and_blocks_report(rig):
    directory = workflow.run(rig.params)
    path = directory / "raw/amplitude_scan.npz"
    with np.load(path) as data:
        values = {key: data[key] for key in data.files}
    values["r_mean_v"] = np.full_like(values["r_mean_v"], 0.2)
    np.savez(path, **values)
    ctx = analysis.AnalysisWorkflow(directory)
    with pytest.raises(analysis_steps.AnalysisQualityError):
        ctx.execute(["B01", "B02", "B03"])
    assert ctx.states["B02"]["status"] == "quality_failed"
    assert (directory / "results/evaluation_diagnostics.yaml").is_file()
    assert (directory / "results/amplitude_response.png").is_file()
    with pytest.raises(ValueError, match="缺少前置"):
        ctx.execute(["B03"])


def test_analysis_real_module_entrypoint_writes_results_and_rejects_unknown_override(rig):
    directory = workflow.run(rig.params)
    environment = os.environ.copy()
    environment.update(LAB_RUN_DIR=str(directory), LAB_TYPED_PARAMETERS="{}", MPLBACKEND="Agg", PYTHONUTF8="1")
    completed = subprocess.run([sys.executable, "-m", "lab_workflows.experiment_modules.mx_y_rf_sensitivity.analysis"],
        env=environment, capture_output=True, text=True, encoding="utf-8", timeout=50)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert (directory / "results/analysis.json").is_file()
    assert (directory / "results/amplitude_response.png").is_file()
    assert json.loads((directory / "analysis_state.json").read_text(encoding="utf-8"))["summary"]["status"] == "completed"
    with pytest.raises(ValueError, match="不支持"):
        analysis.AnalysisWorkflow(directory, {"NOISE_N_AVG": 99})
    with pytest.raises(ValueError, match="有限"):
        analysis.AnalysisWorkflow(directory, {"LOW_FREQ_SKIP_HZ": float("nan")})


@pytest.mark.parametrize("mode", ["explicit", "missing", "nonexistent"])
def test_real_module_outside_repository_uses_explicit_run(rig, mode):
    from lab_workflows.experiments.test_analysis_imports import check_real_analysis_module

    run = workflow.run(rig.params)
    check_real_analysis_module(run, __package__ + ".analysis", "sensitivity.npz", mode)


@pytest.mark.parametrize("bound", ["minimum", "maximum"])
def test_analysis_model_bounds_without_hardware_revalidation(rig, monkeypatch, bound):
    from dataclasses import fields

    run = workflow.run(rig.params)
    path = run / "experiment_config.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    ctx = analysis.AnalysisWorkflow(run)
    params_type = type(ctx.params)
    assert params_type.from_external(config["parameters"]).validate(Path(__file__).resolve().parents[3]) == []
    config["parameters"]["FIXED_PARAMS.temperature"] = 1000.0
    assert params_type.from_external(config["parameters"]).validate(Path(__file__).resolve().parents[3])
    path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    before = path.read_bytes()
    item = next(f for f in fields(ctx.params) if f.metadata.get("external_name") == "FIT_R_SQUARED_MIN")
    endpoint = item.metadata[bound]

    def forbidden(*args, **kwargs):
        raise AssertionError("离线分析不能重新执行完整硬件预检")

    monkeypatch.setattr(params_type, "validate", forbidden)
    assert analysis.AnalysisWorkflow(run, {"FIT_R_SQUARED_MIN": endpoint}).options["FIT_R_SQUARED_MIN"] == endpoint
    with pytest.raises(ValueError, match="FIT_R_SQUARED_MIN"):
        analysis.AnalysisWorkflow(run, {"FIT_R_SQUARED_MIN": endpoint + (-1 if bound == "minimum" else 1)})
    assert path.read_bytes() == before


@pytest.mark.parametrize("fault", ["write", "replace"])
@pytest.mark.parametrize("original_error", [False, True])
def test_real_caller_config_failure_preserves_bytes_and_original_error(rig, monkeypatch, fault, original_error):
    from lab_workflows.experiments.test_analysis_imports import check_real_caller_config_failure
    from . import analysis_steps

    run = workflow.run(rig.params)
    check_real_caller_config_failure(analysis.AnalysisWorkflow(run), analysis_steps, monkeypatch, fault, original_error)
