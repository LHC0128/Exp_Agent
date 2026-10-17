"""闭环分析目录隔离、历史版本、内容指纹和频带端点回归。"""
from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
import pytest
import yaml
from . import analysis, analysis_steps, capture, run_format
from .harmonic_feedback import select_harmonics
from .response_filter import interpolate_in_run
from .test_workflow import ROOT, scan_params


def synthetic_run(root, name="history", method="time_domain"):
    path = root / "data" / run_format.DATA_TYPE / name
    raw, results = path / "raw", path / "results"
    raw.mkdir(parents=True); results.mkdir()
    t = np.arange(64) / 64000
    target = .003 * np.sin(2 * np.pi * 1000 * t)
    measured, command = .98 * target, target / .01
    score = float(np.sqrt(np.mean((target - measured) ** 2)) / np.sqrt(np.mean(target ** 2)))
    row = dict(iteration=0, relative_rms_error=score, feedback_relative_rms_error=score,
        holdout_relative_rms_error=score, error_rms_a=float(np.sqrt(np.mean((target-measured)**2))),
        in_band_error_rms_a=float(np.sqrt(np.mean((target-measured)**2))), outside_band_error_rms_a=0.)
    config = dict(experiment_id=run_format.EXPERIMENT_ID, data_type=run_format.DATA_TYPE,
        raw_format_version=1, schema_version=13, correction_method=method,
        parameters=scan_params(method).to_external(), completion_status="completed", target_reached=True,
        stop_reason="达标", current_frequency_response={"effective_cutoff_hz": 4000.}, final_reload_validated=method=="harmonic_jacobian")
    (path / "experiment_config.yaml").write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    for directory in (raw, results):
        (directory / "iteration_summary.yaml").write_text(yaml.safe_dump([row]), encoding="utf-8")
    directory = raw / "iteration_000/holdout"
    directory.mkdir(parents=True)
    np.savez(directory / "feedback.npz", time_s=t, target_current_a=target, measured_current_a=measured,
        command_voltage_v=command, learning_band=np.fft.rfftfreq(64,1/64000)<=4000.)
    np.savez(results / "corrected_control_waveform.npz", format_version=4 if method=="harmonic_jacobian" else 3,
        time_s=t, target_current_a=target, measured_current_a=measured, voltage_v=command,
        best_iteration=0, relative_rms_error=score, measurement_path="iteration_000/holdout/feedback.npz")
    (results / "corrected_control_waveform.csv").write_text("frozen acquisition artifact\n", encoding="utf-8")
    return path


@pytest.mark.parametrize("method", ["time_domain", "response_filtered_feedback", "harmonic_jacobian"])
def test_full_staged_science_matches_original_algorithm_and_keeps_frozen_inputs(tmp_path, method):
    full = synthetic_run(tmp_path, "full", method)
    staged = synthetic_run(tmp_path, "staged", method)
    frozen = {name: (full / "results" / name).read_bytes() for name in (
        "iteration_summary.yaml", "corrected_control_waveform.npz", "corrected_control_waveform.csv")}
    expected = analysis_steps.analyze(full)
    ctx = analysis.AnalysisWorkflow(staged)
    with pytest.raises(ValueError, match="缺少前置"):
        ctx.execute(["B02"])
    ctx.execute(["B01"])
    actual = ctx.execute(["B02"])
    assert {key: value for key, value in actual.items() if key not in ("status", "options")} == expected
    analysis.analyze(full)
    for name, content in frozen.items():
        assert (full / "results" / name).read_bytes() == content
    ctx.execute(["B02"])
    assert (staged / "results/analysis_inputs.json").is_file()
    assert list((staged / "results").glob("before_*/final_waveform_report.yaml"))
    assert analysis.AnalysisWorkflow(staged).states["B02"]["status"] == "completed"


def test_input_and_product_content_drift_invalidates_even_with_same_stat(tmp_path):
    path = synthetic_run(tmp_path)
    analysis.analyze(path)
    report = path / "results/final_waveform_report.yaml"
    old = report.stat()
    content = report.read_bytes().replace(b"true", b"True", 1)
    report.write_bytes(content); os.utime(report, ns=(old.st_atime_ns, old.st_mtime_ns))
    assert analysis.AnalysisWorkflow(path).states["B02"]["status"] == "stale"
    analysis.analyze(path)
    source = path / "results/iteration_summary.yaml"
    old = source.stat(); source.write_bytes(source.read_bytes().replace(b"0.02", b"0.03", 1))
    os.utime(source, ns=(old.st_atime_ns, old.st_mtime_ns))
    assert analysis.AnalysisWorkflow(path).states["B01"]["status"] == "stale"


def test_bad_or_missing_input_does_not_archive_existing_science(tmp_path):
    path = synthetic_run(tmp_path)
    analysis.analyze(path)
    report = path / "results/final_waveform_report.yaml"
    before = report.read_bytes()
    (path / "raw/iteration_000/holdout/feedback.npz").unlink()
    with pytest.raises(FileNotFoundError):
        analysis.analyze(path)
    assert report.read_bytes() == before
    assert not list((path / "results").glob("before_*/final_waveform_report.yaml"))


def test_format_qualification_is_independent_from_parameter_version_and_rejects_unknown(tmp_path):
    path = synthetic_run(tmp_path)
    config = yaml.safe_load((path / "experiment_config.yaml").read_text(encoding="utf-8"))
    config["schema_version"] = 999
    assert not run_format.analysis_block_reason(config)
    for overrides in ({"experiment_id": "other"}, {"raw_format_version": True}, {"raw_format_version": 2},
                      {"completion_status": "running"}, {"parameters": []}):
        assert run_format.analysis_block_reason(config | overrides)
    with pytest.raises(ValueError, match="没有可调"):
        analysis.AnalysisWorkflow(path, {"TARGET_RELATIVE_RMS": .5})
    legacy = config.copy(); legacy.pop("raw_format_version")
    for version, method in ((2,None),(3,None),(10,"static_current_feedback"),(11,"response_filtered_feedback"),(12,"time_domain"),(13,"harmonic_jacobian")):
        assert not run_format.format_block_reason(legacy | {"schema_version":version,"correction_method":method})
    assert run_format.format_block_reason(legacy | {"schema_version":9})


def test_quality_failure_still_exports_diagnostic_and_keeps_directory_options_isolated(tmp_path):
    path = synthetic_run(tmp_path)
    (path / "results/corrected_control_waveform.npz").unlink()
    config = yaml.safe_load((path / "experiment_config.yaml").read_text(encoding="utf-8"))
    config.update(target_reached=False, completion_status="failed", stop_reason="辨识未通过")
    (path / "experiment_config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    report = analysis.analyze(path)
    assert report["status"] == "completed" and not report["target_reached"]
    assert report["completion_status"] == "failed"
    other = synthetic_run(tmp_path, "other")
    assert analysis.AnalysisWorkflow(other).snapshot()["options"] == {}
    assert not (other / "analysis_state.json").exists()
    assert yaml.safe_load((path / "experiment_config.yaml").read_text(encoding="utf-8"))["parameters"] == config["parameters"]


def test_real_plot_subprocess_requires_explicit_run_dir_and_writes_only_temporary_run(tmp_path):
    path = synthetic_run(tmp_path)
    entry = ROOT / "experiments/Z_AW_Closed_Loop_Waveform_Correction_plot.py"
    env = os.environ | {"PYTHONIOENCODING":"utf-8", "MPLBACKEND":"Agg"}
    missing = subprocess.run([sys.executable, str(entry)], cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert missing.returncode != 0 and "run_dir" in missing.stderr
    completed = subprocess.run([sys.executable, str(entry), str(path)], cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert (path / "results/final_waveform_report.yaml").is_file() and (path / "analysis_state.json").is_file()


@pytest.mark.parametrize("endpoint", [1000., 40000., 500000.])
def test_fft_roundoff_at_reliable_endpoints_and_cutoff_never_extrapolates(endpoint):
    frequency, transfer = np.array([endpoint, 2*endpoint]), np.array([.01+0j,.02+0j])
    np.testing.assert_allclose(interpolate_in_run(frequency, transfer, np.nextafter(endpoint,-np.inf)), transfer[0], rtol=1e-15)
    np.testing.assert_allclose(interpolate_in_run(frequency, transfer, np.nextafter(2*endpoint,np.inf)), transfer[1], rtol=1e-15)
    assert interpolate_in_run(frequency, transfer, endpoint*.999) is None
    assert interpolate_in_run(frequency, transfer, endpoint*2.001) is None
    bins = np.arange(33,dtype=float)*endpoint
    bins[1] = np.nextafter(endpoint,np.inf)
    target = np.sin(2*np.pi*np.arange(64)/64)
    np.testing.assert_array_equal(select_harmonics(target,bins,replace(scan_params(),error_cutoff_hz=endpoint)),[1])


def test_actual_capture_rejects_nyquist_equality():
    params = replace(scan_params(), error_cutoff_hz=32000.)
    frame = dict(time_s=np.arange(64)/64000, measured_voltage_v=np.zeros(64),
        trigger_time_s=np.arange(64)/64000, trigger_voltage_v=np.zeros(64), scale_used_v_div=.1, offset_used_v=0.)
    with pytest.raises(ValueError, match="严格低于"):
        capture.validate_frame(frame,params)
