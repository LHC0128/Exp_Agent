"""真实科学计算、历史兼容、指纹/归档以及显式薄入口回归。"""
from dataclasses import replace
from pathlib import Path
import json
import os
import shutil
import subprocess
import sys
import numpy as np
import pytest
import yaml
from ...common import WorkflowCancelled
from . import analysis, analysis_steps as steps, acquisition, run_format
from .models import MxXYResidualFieldCalibrationParams
from .test_workflow import small_params


REGRESSION_CASES = {
    "mx-xy-residual-field-calibration": {
        "analysis": {
            "sources": [
                "lab_workflows/experiment_modules/mx_main_field_scope_noise_spectrum/__init__.py",
                "lab_workflows/experiment_modules/mx_xy_residual_field_calibration/__init__.py",
                "lab_workflows/experiment_modules/mx_xy_residual_field_calibration/analysis.py"
            ],
            "tests": [
                "test_known_model_and_no_signal_diagnostic",
                "test_complete_and_steps_match_and_plot_rerun_keeps_numeric_inputs",
                "test_legacy_raw_and_parameter_migration",
                "test_bad_inputs_do_not_archive_old_results",
                "test_raw_content_change_and_missing_artifact_invalidate_dependents",
                "test_failed_fit_and_cancel_block_successors_and_save_diagnostics",
                "test_identity_format_and_busy_run_rejected",
                "test_no_analysis_overrides_and_revision_conflict",
                "test_actual_cli_produces_results_and_requires_explicit_run",
                "test_results_junction_to_raw_is_rejected_without_moving_inputs"
            ]
        }
    }
}


def synthetic_run(run_dir, *, schema=3, flat=False):
    run_dir = Path(run_dir)
    (run_dir / "raw").mkdir(parents=True)
    (run_dir / "results").mkdir()
    params = replace(small_params(), x_field_points=5, y_field_points=9,
        scope_duration_s=32 / 8192)
    config = dict(experiment_id=run_format.EXPERIMENT_ID, data_type=run_format.DATA_TYPE,
        schema_version=schema, parameters=params.to_external(), completion_status="completed")
    if schema == 3:
        config["raw_format_version"] = 1
    (run_dir / "experiment_config.yaml").write_text(yaml.safe_dump(config), encoding="utf8")
    x = np.linspace(params.x_field_start_v, params.x_field_stop_v, params.x_field_points)
    y = np.linspace(params.y_field_start_v, params.y_field_stop_v, params.y_field_points)
    np.savez(run_dir / "raw/xy_residual_scan_axes.npz", x_field_v=x, y_field_v=y,
        order="x_outer_y_forward", scan_main_field_ma=params.scan_main_field_ma)
    t = np.arange(32) / 8192
    for ix, xv in enumerate(x):
        for iy, yv in enumerate(y):
            seq = ix * len(y) + iy
            mean = 0.2 if flat else float(analysis.bloch_2d_surface(xv, yv, 0.2, 0.05, 0.004, -0.003, 0.026, 0.024)) + 1e-5 * seq
            voltage = mean + 0.001 * np.sin(2 * np.pi * np.arange(32) / 32)
            payload = dict(time_s=t, voltage_v=voltage, capture_started_unix_s=1700000000 + seq,
                capture_ended_unix_s=1700000000 + seq + 0.1, capture_midpoint_unix_s=1700000000 + seq + 0.05,
                pd_mean_v=float(np.mean(voltage)), pd_std_v=float(np.std(voltage)),
                actual_rate_sa_s=8192, actual_duration_s=t[-1], scale_used_v_div=0.1,
                offset_used_v=0, next_scale_v_div=0.1, next_offset_v=0, attempt_count=1)
            acquisition._save_capture(run_dir / f"raw/grid_X{ix:03d}_Y{iy:03d}_R000.npz", payload,
                phase="grid", sequence_index=seq, repeat_index=0, main_field_ma=params.scan_main_field_ma,
                x_v=float(xv), y_v=float(yv), x_index=ix, y_index=iy)
    return run_dir


def test_known_model_and_no_signal_diagnostic():
    x, y = np.linspace(-0.05, 0.05, 5), np.linspace(-0.05, 0.05, 9)
    xx, yy = np.meshgrid(x, y, indexing="ij")
    matrix = analysis.bloch_2d_surface(xx, yy, 0.2, 0.05, 0.004, -0.003, 0.026, 0.024)
    fit = analysis.fit_bloch_2d(x, y, matrix)
    assert fit.valid_for_selection
    assert fit.balance_x_v == pytest.approx(0.004, abs=1e-6)
    assert fit.balance_y_v == pytest.approx(-0.003, abs=1e-6)
    assert not analysis.fit_bloch_2d(x, y, np.ones(matrix.shape)).valid_for_selection


def test_complete_and_steps_match_and_plot_rerun_keeps_numeric_inputs(tmp_path):
    run = synthetic_run(tmp_path / "full")
    split = tmp_path / "split"
    shutil.copytree(run, split)
    before = (run / "experiment_config.yaml").read_bytes()
    full = analysis.analyze(run)
    assert full["fitted_x_balance_v"] == pytest.approx(0.004, abs=1e-6)
    assert full["fitted_y_balance_v"] == pytest.approx(-0.003, abs=1e-6)
    ctx = analysis.AnalysisWorkflow(split)
    with pytest.raises(ValueError, match="缺少前置"):
        ctx.execute(["B02"])
    for item in ("B01", "B02", "B03"):
        ctx = analysis.AnalysisWorkflow(split)
        result = ctx.execute([item])
    assert {k:v for k,v in full.items() if k not in ("status",)} == {k:v for k,v in result.items() if k not in ("status",)}
    with np.load(run / "results/calibration_results.npz") as left, np.load(split / "results/calibration_results.npz") as right:
        assert left.files == right.files
        for name in left.files:
            np.testing.assert_allclose(left[name], right[name], rtol=1e-12, atol=1e-15, equal_nan=True)
    states = analysis.AnalysisWorkflow(split).snapshot()["steps"]
    assert all(s["status"] == "completed" for s in states.values())
    numeric = (split / "results/calibration_results.npz").read_bytes()
    analysis.analyze(split, step_ids=["B03"])
    assert (split / "results/calibration_results.npz").read_bytes() == numeric
    backups = list((split / "results").glob("before_*"))
    assert backups and not any((p / "calibration_results.npz").exists() for p in backups)
    assert (run / "experiment_config.yaml").read_bytes() == before
    assert (run / "results/bloch_2d_fit.pdf").stat().st_size > 1000


@pytest.mark.parametrize("schema", [1, 2, 3])
def test_legacy_raw_and_parameter_migration(tmp_path, schema):
    run = synthetic_run(tmp_path / "legacy", schema=schema)
    path = run / "experiment_config.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf8"))
    config["parameters"].pop("SYSTEM_SETTLE_S")
    config["parameters"]["TEMP_SWITCH_OFF_SETTLE_S"] = 0.8
    if schema == 1:
        config["parameters"].update(REFERENCE_MAIN_FIELD_MA=5, REFERENCE_REPEATS=3)
    path.write_text(yaml.safe_dump(config), encoding="utf8")
    original = path.read_bytes()
    ctx = analysis.AnalysisWorkflow(run)
    assert ctx.params.system_settle_s == 0.8
    ctx.execute(["B01"])
    assert path.read_bytes() == original


@pytest.mark.parametrize("damage", ["missing", "mean", "rate", "index", "axis"])
def test_bad_inputs_do_not_archive_old_results(tmp_path, damage):
    run = synthetic_run(tmp_path / "broken")
    report = run / "results/analysis.json"
    report.write_text('{"old": true}', encoding="utf8")
    path = run / "raw/grid_X000_Y000_R000.npz"
    if damage == "missing":
        path.unlink()
    else:
        if damage == "axis":
            path = run / "raw/xy_residual_scan_axes.npz"
        with np.load(path) as values:
            copied = {key: values[key] for key in values.files}
        key, value = {"mean": ("pd_mean_v", 123.0), "rate": ("actual_rate_sa_s", 1),
                      "index": ("sequence_index", 5), "axis": ("y_field_v", np.arange(9))}[damage]
        copied[key] = value
        np.savez_compressed(path, **copied)
    with pytest.raises((ValueError, OSError)):
        analysis.analyze(run)
    assert report.read_text(encoding="utf8") == '{"old": true}'
    assert not list((run / "results").glob("before_*"))


def test_raw_content_change_and_missing_artifact_invalidate_dependents(tmp_path):
    run = synthetic_run(tmp_path / "fingerprint")
    analysis.analyze(run, step_ids=["B01", "B02"])
    (run / "results/xy_residual_fit.json").write_text("{}", encoding="utf8")
    ctx = analysis.AnalysisWorkflow(run)
    assert ctx.states["B01"]["status"] == "completed"
    assert ctx.states["B02"]["status"] == "stale"
    marker = run / "raw/content.bin"
    marker.write_bytes(b"original")
    ctx = analysis.AnalysisWorkflow(run)
    ctx.execute(["B01"])
    old_time = marker.stat().st_mtime_ns
    marker.write_bytes(b"modified")
    os.utime(marker, ns=(old_time, old_time))
    assert analysis.AnalysisWorkflow(run).states["B01"]["status"] == "stale"


def test_failed_fit_and_cancel_block_successors_and_save_diagnostics(tmp_path, monkeypatch):
    run = synthetic_run(tmp_path / "flat", flat=True)
    ctx = analysis.AnalysisWorkflow(run)
    ctx.execute(["B01"])
    with pytest.raises(ValueError, match="拟合失败"):
        ctx.execute(["B02"])
    assert ctx.states["B02"]["status"] == "quality_failed"
    with pytest.raises(ValueError, match="缺少前置"):
        ctx.execute(["B03"])
    other = synthetic_run(tmp_path / "cancelled")
    def cancelled(*args, **kwargs):
        raise WorkflowCancelled("模拟分析取消")
    monkeypatch.setattr(steps, "fit_bloch_2d", cancelled)
    with pytest.raises(WorkflowCancelled, match="模拟分析取消"):
        analysis.analyze(other)
    state = json.loads((other / "analysis_state.json").read_text(encoding="utf8"))
    assert state["steps"]["B02"]["status"] == "cancelled"
    assert not (other / "results/analysis.json").exists()


@pytest.mark.parametrize("field,value", [("experiment_id", "other"), ("raw_format_version", 2), ("completion_status", "running")])
def test_identity_format_and_busy_run_rejected(tmp_path, field, value):
    run = synthetic_run(tmp_path / "bad")
    path = run / "experiment_config.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf8"))
    config[field] = value
    path.write_text(yaml.safe_dump(config), encoding="utf8")
    with pytest.raises(ValueError):
        analysis.AnalysisWorkflow(run)


def test_no_analysis_overrides_and_revision_conflict(tmp_path):
    run = synthetic_run(tmp_path / "options")
    with pytest.raises(ValueError, match="没有独立分析覆盖"):
        analysis.AnalysisWorkflow(run, {"POINT_REPEATS": 2})
    ctx = analysis.AnalysisWorkflow(run)
    with pytest.raises(ValueError, match="修订"):
        ctx.execute(["B01"], expected_revision=ctx.revision + 1)
    assert ctx.snapshot()["options"] == {}


def test_actual_cli_produces_results_and_requires_explicit_run(tmp_path):
    run = synthetic_run(tmp_path / "subprocess")
    script = Path(__file__).resolve().parents[3] / "experiments/Mx_XY_Residual_Field_Calibration_plot.py"
    env = os.environ | {"MPLBACKEND": "Agg", "PYTHONIOENCODING": "utf8"}
    result = subprocess.run([sys.executable, str(script), str(run)], env=env, capture_output=True, text=True, encoding="utf8", timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (run / "results/calibration_results.npz").is_file()
    assert (run / "results/bloch_2d_fit.png").stat().st_size > 1000
    result = subprocess.run([sys.executable, str(script)], env=env, capture_output=True, timeout=30)
    assert result.returncode != 0 and b"run_dir" in result.stderr


def test_results_junction_to_raw_is_rejected_without_moving_inputs(tmp_path):
    run = synthetic_run(tmp_path / "junction")
    (run / "results").rmdir()
    if os.name == "nt":
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(run / "results"), str(run / "raw")], capture_output=True)
        assert result.returncode == 0, result.stderr
    else:
        (run / "results").symlink_to(run / "raw", target_is_directory=True)
    original = {p.name: p.read_bytes() for p in (run / "raw").glob("*")}
    try:
        with pytest.raises(ValueError, match="联接"):
            analysis.AnalysisWorkflow(run)
        assert {p.name: p.read_bytes() for p in (run / "raw").glob("*")} == original
    finally:
        if os.name == "nt":
            os.rmdir(run / "results")
        else:
            (run / "results").unlink()
