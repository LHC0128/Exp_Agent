"""闭环分析目录隔离、历史版本、内容指纹和频带端点回归。"""
from dataclasses import replace
import hashlib
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


REGRESSION_CASES = {
    "z-aw-closed-loop-waveform-correction": {
        "analysis": {
            "sources": [
                "lab_workflows/analysis_execution.py",
                "lab_workflows/experiment_modules/z_aw_closed_loop_waveform_correction/__init__.py",
                "lab_workflows/experiment_modules/z_aw_closed_loop_waveform_correction/analysis.py"
            ],
            "tests": [
                "test_staged_execution_contract_without_plot",
                "test_rerun_and_content_drift_preserve_frozen_inputs_without_plot",
                "test_invalid_inputs_preserve_previous_science_without_plot",
                "test_failure_cancellation_and_persist_contract_without_plot",
                "test_full_staged_science_matches_original_algorithm_and_keeps_frozen_inputs",
                "test_input_and_product_content_drift_invalidates_even_with_same_stat",
                "test_bad_or_missing_input_does_not_archive_existing_science",
                "test_format_qualification_is_independent_from_parameter_version_and_rejects_unknown",
                "test_quality_failure_still_exports_diagnostic_and_keeps_directory_options_isolated",
                "test_real_plot_subprocess_requires_explicit_run_dir_and_writes_only_temporary_run",
                "test_fft_roundoff_at_reliable_endpoints_and_cutoff_never_extrapolates",
                "test_actual_capture_rejects_nyquist_equality"
            ]
        }
    }
}


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


def stub_report_without_plot(monkeypatch):
    """只隔离绘图/数值评估，真实资格校验、归档和保存仍由工作流执行。"""
    calls = []

    def report(path):
        calls.append(path)
        (path / "results/final_waveform_report.yaml").write_text(
            "success: true\ntarget_reached: true\n", encoding="utf-8")
        (path / "results/diagnosis.md").write_text("saved diagnostic\n", encoding="utf-8")
        return dict(success=True, target_reached=True, iteration_count=1,
            best_relative_rms_error=np.float64(.02), nonfinite=np.array([np.nan, np.inf, -np.inf]),
            notes=(np.int64(3),))

    monkeypatch.setattr(analysis_steps, "analyze", report)
    return calls


def test_staged_execution_contract_without_plot(tmp_path, monkeypatch):
    from lab_workflows.analysis_execution import AnalysisRunState
    from lab_workflows.workflow_state import read_state

    path = synthetic_run(tmp_path)
    original_config = yaml.safe_load((path / "experiment_config.yaml").read_text(encoding="utf-8"))
    original_raw = {name.relative_to(path): name.read_bytes() for name in (path / "raw").rglob("*") if name.is_file()}
    calls = stub_report_without_plot(monkeypatch)
    progress = []
    monkeypatch.setattr(analysis, "report_runtime_progress", lambda *args, **kwargs: progress.append((args, kwargs)))
    ctx = analysis.AnalysisWorkflow(path)
    assert isinstance(ctx._analysis, AnalysisRunState)
    assert not ctx.state_path.exists() and ctx.summary == {"status": "not_started"}
    assert not ({"states", "revision", "summary", "options", "fingerprint", "run_dir", "results", "state_path"} & ctx.__dict__.keys())
    assert set(ctx.snapshot()) == {"run_id", "revision", "options", "steps", "fingerprint", "summary"}
    assert ctx.snapshot()["run_id"] == path.name and ctx.options == {}
    with pytest.raises(ValueError, match="缺少前置"):
        ctx.execute(["B02"], expected_revision=0)
    assert ctx.revision == 0
    ctx.execute(["B01"], expected_revision=0)
    assert ctx.revision == 1 and calls == [] and ctx.summary["status"] == "partial"
    assert json.loads((ctx.results / "analysis_inputs.json").read_text(encoding="utf-8")) == analysis_steps.load_inputs(path)
    saved = ctx.state_path.read_bytes()
    with pytest.raises(ValueError, match="修订已改变"):
        ctx.execute(["B02"], expected_revision=0)
    assert ctx.revision == 1 and ctx.state_path.read_bytes() == saved
    result = ctx.execute(["B02"], expected_revision=1)
    assert result is ctx.summary and result["status"] == "completed" and calls == [path]
    assert ctx.revision == 2
    record = ctx.states["B02"]
    assert set(record) == {"status", "error", "artifacts", "files", "input_fingerprint", "parameters", "parameter_fingerprint", "revision"}
    assert record["artifacts"] == ["final_waveform_report.yaml", "diagnosis.md"]
    assert record["files"] == {name: [len((ctx.results / name).read_bytes()),
        hashlib.sha256((ctx.results / name).read_bytes()).hexdigest()] for name in record["artifacts"]}
    assert record["input_fingerprint"] == ctx.fingerprint and record["parameters"] == {} and record["revision"] == 2
    assert record["parameter_fingerprint"] == hashlib.sha256(b"{}").hexdigest()
    persisted = read_state(ctx.state_path)
    assert persisted == analysis._plain(ctx.snapshot())
    assert persisted["summary"]["nonfinite"] == [None, None, None] and persisted["summary"]["notes"] == [3]
    assert len(progress) == 4
    for args, kwargs in progress:
        assert args == ("analysis", "离线分析状态已保存") and set(kwargs) == {"data"}
        assert set(kwargs["data"]) == {"analysis_workflow"}
    assert [item[1]["data"]["analysis_workflow"]["summary"]["status"] for item in progress] == ["running", "partial", "running", "completed"]
    assert progress[-1][1]["data"]["analysis_workflow"] == persisted
    current_config = yaml.safe_load(ctx.config_path.read_text(encoding="utf-8"))
    assert {key: value for key, value in current_config.items() if key not in ("analysis_status", "analysis_error")} == original_config
    assert current_config["analysis_status"] == "completed" and current_config["analysis_error"] is None
    assert {name: (path / name).read_bytes() for name in original_raw} == original_raw
    before = ctx.state_path.read_bytes()
    restored = analysis.AnalysisWorkflow(path)
    assert restored.snapshot() == persisted and ctx.state_path.read_bytes() == before


def test_rerun_and_content_drift_preserve_frozen_inputs_without_plot(tmp_path, monkeypatch):
    path = synthetic_run(tmp_path)
    stub_report_without_plot(monkeypatch)
    ctx = analysis.AnalysisWorkflow(path)
    ctx.execute(["B01", "B02"])
    frozen_names = ("iteration_summary.yaml", "corrected_control_waveform.npz", "corrected_control_waveform.csv")
    frozen = {name: (ctx.results / name).read_bytes() for name in frozen_names}
    inputs = (ctx.results / "analysis_inputs.json").read_bytes()
    fingerprint = ctx.fingerprint
    (ctx.results / "phase_calibration.yaml").write_text("unrelated: changed\n", encoding="utf-8")
    assert analysis.AnalysisWorkflow(path).revision == 1
    assert ctx.input_fingerprint() == fingerprint
    ctx.execute(["B02"], expected_revision=1)
    assert ctx.revision == 2 and (ctx.results / "analysis_inputs.json").read_bytes() == inputs
    assert {name: (ctx.results / name).read_bytes() for name in frozen_names} == frozen
    assert list(ctx.results.glob("before_*/final_waveform_report.yaml"))
    assert list(ctx.results.glob("before_*/diagnosis.md"))
    assert not list(ctx.results.glob("before_*/corrected_control_waveform.*"))
    assert not list(ctx.results.glob("before_*/iteration_summary.yaml"))
    report = ctx.results / "final_waveform_report.yaml"
    old = report.stat()
    report.write_bytes(report.read_bytes().replace(b"true", b"True", 1))
    os.utime(report, ns=(old.st_atime_ns, old.st_mtime_ns))
    stale = analysis.AnalysisWorkflow(path)
    assert stale.revision == 3 and stale.states["B01"]["status"] == "completed"
    assert stale.states["B02"]["status"] == stale.summary["status"] == "stale"
    source = ctx.results / "iteration_summary.yaml"
    old = source.stat()
    changed = source.read_bytes().replace(b"iteration: 0", b"iteration: 1", 1)
    assert changed != source.read_bytes() and len(changed) == old.st_size
    source.write_bytes(changed)
    os.utime(source, ns=(old.st_atime_ns, old.st_mtime_ns))
    stale = analysis.AnalysisWorkflow(path)
    assert stale.revision == 3 and all(record["status"] == "stale" for record in stale.states.values())
    assert "results/phase_calibration.yaml" not in stale.fingerprint["raw_files"]
    assert set(stale.fingerprint["raw_files"]) == {
        "raw/iteration_summary.yaml", "raw/iteration_000/holdout/feedback.npz",
        "results/iteration_summary.yaml", "results/corrected_control_waveform.npz"}


def rewrite_npz(path, **updates):
    with np.load(path, allow_pickle=False) as saved:
        payload = {name: saved[name] for name in saved.files}
    np.savez(path, **(payload | updates))


@pytest.mark.parametrize("case", ["missing_feedback", "bad_band", "escaping_feedback", "bad_waveform", "bad_summary", "input_changed", "running_format"])
def test_invalid_inputs_preserve_previous_science_without_plot(tmp_path, monkeypatch, case):
    path = synthetic_run(tmp_path)
    stub_report_without_plot(monkeypatch)
    analysis.analyze(path)
    report = path / "results/final_waveform_report.yaml"
    before = report.read_bytes()
    state_before = (path / "analysis_state.json").read_bytes()
    feedback = path / "raw/iteration_000/holdout/feedback.npz"
    frozen = path / "results/corrected_control_waveform.npz"
    if case == "missing_feedback":
        feedback.unlink()
    elif case == "bad_band":
        rewrite_npz(feedback, learning_band=np.ones(33, dtype=int))
    elif case == "escaping_feedback":
        rewrite_npz(frozen, measurement_path="../escape.npz")
    elif case == "bad_waveform":
        time = np.arange(64) / 64000
        time[1] = time[0]
        rewrite_npz(frozen, time_s=time)
    elif case == "bad_summary":
        (path / "raw/iteration_summary.yaml").write_text("- relative_rms_error: .nan\n", encoding="utf-8")
    elif case == "input_changed":
        load_inputs = analysis_steps.load_inputs

        def changed_after_read(run_dir):
            result = load_inputs(run_dir)
            summary = run_dir / "raw/iteration_summary.yaml"
            summary.write_bytes(summary.read_bytes() + b"# changed during execution\n")
            return result

        monkeypatch.setattr(analysis_steps, "load_inputs", changed_after_read)
    else:
        config = yaml.safe_load((path / "experiment_config.yaml").read_text(encoding="utf-8"))
        config["completion_status"] = "running"
        (path / "experiment_config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    with pytest.raises(FileNotFoundError if case == "missing_feedback" else ValueError):
        analysis.analyze(path)
    assert report.read_bytes() == before
    assert not list((path / "results").glob("before_*"))
    persisted = json.loads((path / "analysis_state.json").read_text(encoding="utf-8"))
    if case == "running_format":
        assert (path / "analysis_state.json").read_bytes() == state_before
    else:
        assert persisted["steps"]["B01"]["status"] == ("failed" if case == "missing_feedback" else "quality_failed")
        assert set(persisted["steps"]["B01"]) == {"status", "error"}


@pytest.mark.parametrize("case", ["quality", "ordinary", "cancelled", "keyboard", "system_exit", "missing_product", "first_persist", "final_persist", "cancel_before", "cancel_between", "quality_with_persist_error", "progress_error"])
def test_failure_cancellation_and_persist_contract_without_plot(tmp_path, monkeypatch, case):
    from lab_workflows.workflow_state import read_state

    path = synthetic_run(tmp_path)
    stub_report_without_plot(monkeypatch)
    ctx = analysis.AnalysisWorkflow(path)
    if case in ("cancel_before", "cancel_between"):
        error = analysis.WorkflowCancelled("阶段间取消")
        checks = []

        def cancel():
            checks.append(True)
            if len(checks) == (1 if case == "cancel_before" else 2):
                raise error

        monkeypatch.setattr(analysis, "check_cancelled", cancel)
        with pytest.raises(analysis.WorkflowCancelled) as caught:
            ctx.execute(["B01", "B02"])
        assert caught.value is error and ctx.revision == 1
        assert ctx.states["B02"] == {"status": "pending", "error": ""}
        if case == "cancel_before":
            assert ctx.states["B01"] == {"status": "pending", "error": ""} and not ctx.state_path.exists()
            assert ctx.summary == {"status": "not_started"}
        else:
            assert ctx.states["B01"]["status"] == "completed" and ctx.summary["status"] == "partial"
            assert read_state(ctx.state_path)["steps"]["B02"]["status"] == "pending"
        return
    if case in ("first_persist", "final_persist", "progress_error"):
        error = OSError("状态接线失败")
        calls = []
        persist = ctx.persist

        def broken_persist():
            calls.append(True)
            if len(calls) == (1 if case == "first_persist" else 2):
                raise error
            persist()

        def broken_progress(*args, **kwargs):
            calls.append(True)
            raise error

        if case == "progress_error":
            monkeypatch.setattr(analysis, "report_runtime_progress", broken_progress)
        else:
            monkeypatch.setattr(ctx, "persist", broken_persist)
        with pytest.raises(OSError) as caught:
            ctx.execute(["B01"])
        assert caught.value is error and len(calls) == 2 and ctx.revision == 1
        saved = read_state(ctx.state_path)
        config = yaml.safe_load(ctx.config_path.read_text(encoding="utf-8"))
        if case == "final_persist":
            assert ctx.states["B01"]["status"] == "completed" and saved["steps"]["B01"]["status"] == "running"
            assert config["analysis_status"] == "running"
            recovered = analysis.AnalysisWorkflow(path)
            assert recovered.revision == 2 and recovered.states["B01"]["status"] == "stale"
        else:
            assert ctx.states["B01"] == {"status": "failed", "error": str(error)}
            assert saved["steps"]["B01"] == ctx.states["B01"] and config["analysis_status"] == "failed"
            assert config["analysis_error"] == str(error)
        return
    ctx.execute(["B01"])
    errors = {"quality": ValueError("质量失败"), "ordinary": RuntimeError("计算失败"),
        "cancelled": analysis.WorkflowCancelled("执行中取消"), "keyboard": KeyboardInterrupt("键盘取消"),
        "system_exit": SystemExit("退出"), "quality_with_persist_error": ValueError("原始质量失败")}
    error = errors.get(case)

    def fail_analysis(run_dir):
        (run_dir / "results/diagnosis.md").write_text("partial diagnostic", encoding="utf-8")
        if error is not None:
            raise error
        return {}

    monkeypatch.setattr(analysis_steps, "analyze", fail_analysis)
    if case == "quality_with_persist_error":
        persist = ctx.persist
        calls = []

        def final_persist_fails():
            calls.append(True)
            if len(calls) == 2:
                raise OSError("不能覆盖原异常")
            persist()

        monkeypatch.setattr(ctx, "persist", final_persist_fails)
    with pytest.raises(RuntimeError if case == "missing_product" else type(error)) as caught:
        ctx.execute(["B02"], expected_revision=1)
    if error is not None:
        assert caught.value is error
    else:
        assert "分析阶段缺少产物" in str(caught.value)
    expected = ("cancelled" if case in ("cancelled", "keyboard") else
                "quality_failed" if case in ("quality", "quality_with_persist_error") else "failed")
    assert ctx.revision == 2 and ctx.states["B02"] == {"status": expected, "error": str(caught.value)}
    assert ctx.summary["status"] == expected and ctx.summary["error"] == str(caught.value)
    saved = read_state(ctx.state_path)
    if case == "quality_with_persist_error":
        assert len(calls) == 2 and saved["steps"]["B02"]["status"] == "running"
    else:
        assert saved["steps"]["B02"] == ctx.states["B02"]
        config = yaml.safe_load(ctx.config_path.read_text(encoding="utf-8"))
        assert config["analysis_status"] == expected and config["analysis_error"] == str(caught.value)
