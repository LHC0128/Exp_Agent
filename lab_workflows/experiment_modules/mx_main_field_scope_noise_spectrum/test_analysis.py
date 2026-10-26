"""真实波形、科学计算、分步归档和子进程入口回归。"""
from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import numpy as np
import pytest
import yaml

from ...common import WorkflowCancelled, find_project_root
from . import analysis, analysis_steps as steps, run_format
from .test_workflow import small_params


REGRESSION_CASES = {
    "mx-main-field-scope-noise-spectrum": {
        "analysis": {
            "sources": [
                "lab_workflows/experiment_modules/mx_main_field_scope_noise_spectrum/__init__.py",
                "lab_workflows/experiment_modules/mx_main_field_scope_noise_spectrum/analysis.py"
            ],
            "tests": [
                "test_full_and_separate_scientific_arrays_match",
                "test_analysis_overrides_and_archives_only_scientific_outputs",
                "test_content_fingerprints_detect_same_stat_changes",
                "test_bad_input_does_not_move_existing_results",
                "test_legacy_format_qualification_and_running_rejection",
                "test_results_directory_junction_cannot_archive_raw",
                "test_global_numeric_decomposition_and_real_figures",
                "test_cancelled_scientific_stage_keeps_original_exception",
                "test_real_subprocess_outputs_and_explicit_thin_entry"
            ]
        }
    }
}


def synthetic_run(path, *, global_analysis=False, schema=8):
    path = Path(path)
    (path / "raw").mkdir(parents=True)
    params = replace(small_params(), control_frequency_stop_hz=2100, control_frequency_points=21,
        global_2d_analysis_enabled=global_analysis, global_fit_frequency_max_hz=2000)
    config = dict(experiment_id=run_format.EXPERIMENT_ID, data_type=run_format.DATA_TYPE,
        schema_version=schema, parameters=params.to_external(), completion_status="completed", run_tag="test")
    (path / "experiment_config.yaml").write_text(yaml.safe_dump(config), encoding="utf8")
    rng = np.random.default_rng(1023)
    t = np.arange(params.scope_requested_points) / params.scope_sample_rate_sa_s
    f = np.fft.rfftfreq(len(t), 1 / params.scope_sample_rate_sa_s)
    for i, control in enumerate(np.linspace(100, 2100, 21)):
        gamma = 100
        psd = 1e-6 + (gamma**2 + f**2) / ((gamma**2 - f**2 + control**2)**2 + 4 * f**2 * gamma**2)
        voltage = np.fft.irfft(np.sqrt(psd * len(t) * params.scope_sample_rate_sa_s / 2) * np.exp(1j * rng.uniform(0, 2*np.pi, len(f))), n=len(t))
        np.savez(path / "raw" / f"waveform_I{i:04d}.npz", time_s=t, voltage_v=voltage,
            point_index=i, control_frequency_hz=control, larmor_frequency_hz=control,
            main_field_current_ma=params.current_for_control_frequency(control),
            actual_rate_sa_s=params.scope_sample_rate_sa_s, scale_used_v_div=0.1)
    return path


def numerical_products(run):
    products = {}
    for path in (run / "results").glob("*.npz"):
        with np.load(path, allow_pickle=False) as arrays:
            products[path.name] = {key: arrays[key].copy() for key in arrays.files}
    return products


@pytest.fixture
def fast_plots(monkeypatch):
    # 数值与归档测试仅省略渲染；另有真实 PNG/PDF 和子进程检查。
    from . import analysis_plotting
    import matplotlib.pyplot as plt
    def save(figure, path, **kwargs):
        path = Path(path)
        path.write_bytes(b"test figure")
        path.with_suffix(".pdf").write_bytes(b"test figure pdf")
        plt.close(figure)
    monkeypatch.setattr(analysis_plotting, "save_figure", save)


def test_full_and_separate_scientific_arrays_match(tmp_path, fast_plots):
    first = synthetic_run(tmp_path / "full")
    second = tmp_path / "separate"
    shutil.copytree(first, second)
    original = (first / "experiment_config.yaml").read_bytes()
    a = analysis.analyze(first)
    ctx = analysis.AnalysisWorkflow(second)
    with pytest.raises(ValueError, match="缺少前置"):
        ctx.execute(["B02"])
    ctx.execute(["B01"])
    ctx = analysis.AnalysisWorkflow(second)
    b = ctx.execute(["B02"])
    assert a["fit"] == b["fit"] and a["global_2d_fit"] == b["global_2d_fit"]
    for name, values in numerical_products(first).items():
        for key, value in values.items():
            other = numerical_products(second)[name][key]
            if value.dtype.kind in "OUS":
                np.testing.assert_array_equal(value, other)
            else:
                np.testing.assert_allclose(value, other, rtol=1e-12, atol=1e-15, equal_nan=True)
    assert (first / "experiment_config.yaml").read_bytes() == original


def test_analysis_overrides_and_archives_only_scientific_outputs(tmp_path, fast_plots):
    run = synthetic_run(tmp_path / "run")
    other = synthetic_run(tmp_path / "other")
    analysis.analyze(run)
    input_bytes = (run / "results/scope_noise_inputs.json").read_bytes()
    config_bytes = (run / "experiment_config.yaml").read_bytes()
    ctx = analysis.AnalysisWorkflow(run, {"FIT_EXAMPLE_PLOT_COUNT": 2})
    assert ctx.states["B01"]["status"] == "completed" and ctx.states["B02"]["status"] == "stale"
    ctx.execute(["B02"])
    assert (run / "results/scope_noise_inputs.json").read_bytes() == input_bytes
    assert list((run / "results").glob("before_*/analysis.json"))
    assert not list((run / "results").glob("before_*/scope_noise_inputs.json"))
    assert (run / "experiment_config.yaml").read_bytes() == config_bytes
    assert analysis.AnalysisWorkflow(run).options["FIT_EXAMPLE_PLOT_COUNT"] == 2
    assert analysis.AnalysisWorkflow(other).options["FIT_EXAMPLE_PLOT_COUNT"] == 1
    for options in ({"SCOPE_AC_COUPLING": True}, {"WELCH_NPERSEG": 1}, {"GLOBAL_2D_ANALYSIS_ENABLED": 1}, {"FIT_GAMMA_GUESS_HZ": float("nan")}):
        with pytest.raises((ValueError, TypeError)):
            analysis.AnalysisWorkflow(run, options)


def test_content_fingerprints_detect_same_stat_changes(tmp_path, fast_plots):
    run = synthetic_run(tmp_path / "run")
    analysis.analyze(run)
    artifact = run / "results/analysis.json"
    st = artifact.stat()
    artifact.write_bytes(artifact.read_bytes().replace(b"completed_points", b"completed_pointx"))
    os.utime(artifact, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert analysis.AnalysisWorkflow(run).states["B02"]["status"] == "stale"
    raw = run / "raw/waveform_I0000.npz"
    st = raw.stat()
    with np.load(raw) as data:
        values = {key: data[key] for key in data.files}
    values["voltage_v"] = values["voltage_v"] + 0.001
    np.savez(raw, **values)
    assert raw.stat().st_size == st.st_size
    os.utime(raw, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert analysis.AnalysisWorkflow(run).states["B01"]["status"] == "stale"


@pytest.mark.parametrize("problem", ["time", "shape", "calibration", "rate", "index", "missing", "nan"])
def test_bad_input_does_not_move_existing_results(tmp_path, problem):
    run = synthetic_run(tmp_path / problem)
    (run / "results").mkdir()
    previous = run / "results/analysis.json"
    previous.write_text('{"old":true}', encoding="utf8")
    path = run / "raw/waveform_I0000.npz"
    with np.load(path) as data:
        values = {key: data[key] for key in data.files}
    if problem == "time": values["time_s"][5] = values["time_s"][4]
    if problem == "shape": values["voltage_v"] = values["voltage_v"].reshape(2, -1)
    if problem == "calibration": values["main_field_current_ma"] += 1
    if problem == "rate": values["actual_rate_sa_s"] *= 2
    if problem == "index": values["point_index"] = np.array(1)
    if problem == "missing": del values["time_s"]
    if problem == "nan": values["voltage_v"][5] = np.nan
    np.savez(path, **values)
    with pytest.raises((ValueError, KeyError)):
        analysis.analyze(run)
    assert previous.read_text(encoding="utf8") == '{"old":true}'
    assert not list((run / "results").glob("before_*"))


def test_legacy_format_qualification_and_running_rejection(tmp_path):
    run = synthetic_run(tmp_path / "legacy", schema=3)
    config_path = run / "experiment_config.yaml"
    original = yaml.safe_load(config_path.read_text(encoding="utf8"))
    assert steps.load_inputs(run)["completed_points"] == 21
    for changes in ({"experiment_id": "other"}, {"raw_format_version": 99}, {"raw_format_version": True}, {"completion_status": "running"}, {"schema_version": 99}):
        config_path.write_text(yaml.safe_dump(original | changes), encoding="utf8")
        with pytest.raises(ValueError):
            analysis.AnalysisWorkflow(run)
    config_path.write_text(yaml.safe_dump(original | {"raw_format_version": 1}), encoding="utf8")
    assert steps.load_inputs(run)["raw_format_version"] == 1


def test_results_directory_junction_cannot_archive_raw(tmp_path):
    run = synthetic_run(tmp_path / "run")
    if os.name == "nt":
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(run / "results"), str(run / "raw")], capture_output=True)
        assert result.returncode == 0, result.stderr
    else:
        (run / "results").symlink_to(run / "raw", target_is_directory=True)
    raw = sorted((run / "raw").glob("*"))
    with pytest.raises(ValueError, match="联接|越出"):
        analysis.AnalysisWorkflow(run)
    assert sorted((run / "raw").glob("*")) == raw


def test_global_numeric_decomposition_and_real_figures(tmp_path):
    run = synthetic_run(tmp_path / "global", global_analysis=True)
    result = analysis.analyze(run)
    assert result["global_2d_fit"]["status"] == "completed"
    with np.load(run / "results/global_2d_noise_separation.npz") as data:
        assert data["model_matrix"].shape[0] == 21
        assert data["quantitative_valid_mask"].dtype == np.bool_
        assert data["frequency_hz"][0] >= 50
    assert (run / "results/global_2d_noise_spectra.png").read_bytes().startswith(b"\x89PNG")
    assert (run / "results/global_2d_noise_spectra.pdf").read_bytes().startswith(b"%PDF")


def test_cancelled_scientific_stage_keeps_original_exception(tmp_path, monkeypatch):
    run = synthetic_run(tmp_path / "cancelled")
    ctx = analysis.AnalysisWorkflow(run)
    ctx.execute(["B01"])
    def cancel(*args): raise WorkflowCancelled("模拟离线取消")
    monkeypatch.setattr(steps, "evaluate", cancel)
    with pytest.raises(WorkflowCancelled, match="模拟离线取消"):
        ctx.execute(["B02"])
    assert ctx.states["B02"]["status"] == "cancelled"


def test_real_subprocess_outputs_and_explicit_thin_entry(tmp_path):
    run = synthetic_run(tmp_path / "child")
    from .definition import ADAPTER
    ADAPTER._execute(ADAPTER.analysis_module, run_dir=run, parameters={}, progress=lambda event: None)
    assert (run / "results/psd_matrix.npz").is_file()
    assert (run / "results/analysis.json").is_file()
    assert json.loads((run / "analysis_state.json").read_text(encoding="utf8"))["steps"]["B02"]["status"] in ("completed", "quality_failed")
    result = subprocess.run([sys.executable, str(find_project_root() / "experiments/Mx_Main_Field_Scope_Noise_Spectrum_plot.py")], capture_output=True)
    assert result.returncode == 2 and b"run_dir" in result.stderr
