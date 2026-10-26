"""明确临时目录的科学等价、分步分析、指纹及历史兼容回归。"""
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
from ..mx_y_rf_grid_analysis import analyze_mx_y_rf_grid
from ..mx_y_rf_sensitivity.analysis_core import absolute_dispersive_response
from ..mx_y_rf_sensitivity.analysis_plotting import _plot_full_analysis
from ..mx_y_rf_sensitivity.point_analysis import evaluate_mx_y_rf_point
from . import analysis, analysis_steps, run_format
from .models import MxYRFProbeDetuningOptimizationParams


REGRESSION_CASES = {
    "mx-y-rf-probe-detuning-optimization": {
        "analysis": {
            "sources": [
                "lab_workflows/experiment_modules/mx_y_rf_probe_detuning_optimization/__init__.py",
                "lab_workflows/experiment_modules/mx_y_rf_probe_detuning_optimization/analysis.py"
            ],
            "tests": [
                "test_original_grid_and_new_stepwise_science_are_identical",
                "test_options_are_directory_local_and_archive_only_dependants",
                "test_content_fingerprint_detects_same_size_same_mtime_changes",
                "test_bad_input_is_rejected_before_old_results_are_archived",
                "test_explicit_legacy_format_and_parameter_wait_migration",
                "test_unknown_identity_format_and_active_run_are_rejected",
                "test_quality_failure_retains_diagnostics_and_excludes_invalid_points",
                "test_cancel_is_not_swallowed_as_a_bad_grid_point",
                "test_invalid_analysis_overrides_are_rejected",
                "test_input_change_during_science_is_not_marked_completed",
                "test_results_alias_to_raw_cannot_move_acquisition_files",
                "test_real_subprocess_and_thin_entrypoint_require_explicit_directory"
            ]
        }
    }
}


def synthetic_run(root):
    root.mkdir(parents=True)
    params = replace(MxYRFProbeDetuningOptimizationParams(), probe_power_start_v=0.3,
        probe_power_stop_v=0.3, probe_power_points=1, pzt_voltage_points=2,
        pzt_voltage_start_v=40, pzt_voltage_stop_v=50, y_rf_amp_points=41,
        noise_n_avg=6, noise_rate_sa_s=10000, noise_duration_s=0.8192)
    config = dict(experiment_id=run_format.EXPERIMENT_ID, data_type=run_format.DATA_TYPE,
        schema_version=2, raw_format_version=1, parameters=params.to_external(), completion_status="completed")
    (root / "experiment_config.yaml").write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    manifest = dict(pzt_voltage_v=[40.0, 50.0], probe_power_v=[0.3], points={})
    axis = np.linspace(-0.2, 0.2, 41)
    rng = np.random.default_rng(7)
    for index, pzt in enumerate((40, 50)):
        key = f"pzt_{index:03d}_probe_000"
        point = root / "raw/points" / key
        point.mkdir(parents=True)
        manifest["points"][key] = dict(pzt_index=index, probe_index=0, pzt_voltage_v=pzt,
            probe_power_v=0.3, status="completed", reason=None,
            pzt_voltage_readback_v=pzt, pzt_actual_v=pzt + 0.01, pzt_setpoint_error_v=0)
        np.savez(point / "amplitude_scan.npz", signed_amplitude_vpp=axis,
            r_mean_v=absolute_dispersive_response(axis, 0.01, 0.05, 0, 0.1), r_std_v=np.full(axis.shape, 1e-6))
        for i in range(params.noise_n_avg):
            np.savez(point / f"noise_{i:03d}.npz", r_v=0.1 + rng.normal(0, 1e-5 * (1 + index), 8192), actual_rate_sa_s=10000.)
    (root / "raw/point_manifest.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    return root


@pytest.fixture
def run_dir(tmp_path):
    return synthetic_run(tmp_path / "run")


def raw_bytes(path):
    return {str(p.relative_to(path)): p.read_bytes() for p in (path / "raw").rglob("*") if p.is_file()}


def test_original_grid_and_new_stepwise_science_are_identical(run_dir, tmp_path):
    old = tmp_path / "old"
    shutil.copytree(run_dir, old)
    expected = analyze_mx_y_rf_grid(old, params_type=MxYRFProbeDetuningOptimizationParams,
        spec=analysis.GRID_SPEC, evaluate_point=evaluate_mx_y_rf_point, plot_full_analysis=_plot_full_analysis)
    before, parameters = raw_bytes(run_dir), (run_dir / "experiment_config.yaml").read_bytes()
    actual = analysis.analyze(run_dir)
    assert actual["best_point"] == expected["best_point"]
    assert actual["ranking"] == expected["ranking"]
    assert json.dumps(actual["points"], sort_keys=True) == json.dumps(expected["points"], sort_keys=True)
    assert actual["zero_point_method"] == expected["zero_point_method"]
    with np.load(old / "results/optimization.npz") as a, np.load(run_dir / "results/optimization.npz") as b:
        assert a.files == b.files
        for key in a.files:
            np.testing.assert_array_equal(a[key], b[key])
    ctx = analysis.AnalysisWorkflow(run_dir)
    ctx.execute(["B01"])
    inputs = (run_dir / "results/grid_inputs.json").read_bytes()
    ctx.execute(["B02"])
    assert ctx.summary["ranking"] == actual["ranking"]
    assert (run_dir / "results/grid_inputs.json").read_bytes() == inputs
    assert list((run_dir / "results").glob("before_*/optimization.json"))
    assert raw_bytes(run_dir) == before
    assert (run_dir / "experiment_config.yaml").read_bytes() == parameters


def test_options_are_directory_local_and_archive_only_dependants(run_dir, tmp_path):
    analysis.analyze(run_dir)
    other = tmp_path / "other"
    shutil.copytree(run_dir, other)
    ctx = analysis.AnalysisWorkflow(run_dir, {"LOW_FREQ_SKIP_HZ": 5, "FIT_R_SQUARED_MIN": 0.8})
    assert ctx.states["B01"]["status"] == "completed" and ctx.states["B02"]["status"] == "stale"
    before = (run_dir / "results/grid_inputs.json").read_bytes()
    ctx.execute(["B02"])
    assert (run_dir / "results/grid_inputs.json").read_bytes() == before
    assert not list((run_dir / "results").glob("before_*/grid_inputs.json"))
    assert analysis.AnalysisWorkflow(other).options["LOW_FREQ_SKIP_HZ"] == 3
    assert analysis.AnalysisWorkflow(run_dir).options["LOW_FREQ_SKIP_HZ"] == 5
    config = yaml.safe_load((run_dir / "experiment_config.yaml").read_text(encoding="utf-8"))
    assert config["parameters"]["LOW_FREQ_SKIP_HZ"] == 3


@pytest.mark.parametrize("kind", ["raw", "artifact"])
def test_content_fingerprint_detects_same_size_same_mtime_changes(run_dir, kind):
    ctx = analysis.AnalysisWorkflow(run_dir)
    ctx.execute(["B01"])
    if kind == "raw":
        path = run_dir / "raw/point_manifest.yaml"
        payload = path.read_bytes().replace(b"reason: null", b"reason: none", 1)
    else:
        path = run_dir / "results/grid_inputs.json"
        payload = path.read_bytes().replace(b"shape", b"shapX", 1)
    stat = path.stat()
    assert len(payload) == stat.st_size and payload != path.read_bytes()
    path.write_bytes(payload)
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert analysis.AnalysisWorkflow(run_dir).states["B01"]["status"] == "stale"


@pytest.mark.parametrize("problem", ["missing_noise", "wrong_shape", "unsafe_key", "out_of_grid"])
def test_bad_input_is_rejected_before_old_results_are_archived(run_dir, problem):
    results = run_dir / "results"
    results.mkdir()
    old = results / "optimization.json"
    old.write_text('{"old": true}', encoding="utf-8")
    point = run_dir / "raw/points/pzt_000_probe_000"
    if problem == "missing_noise":
        (point / "noise_000.npz").unlink()
    elif problem == "wrong_shape":
        np.savez(point / "amplitude_scan.npz", signed_amplitude_vpp=np.arange(5), r_mean_v=np.arange(4), r_std_v=np.arange(5))
    else:
        path = run_dir / "raw/point_manifest.yaml"
        manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
        if problem == "unsafe_key":
            manifest["points"]["../../outside"] = manifest["points"].pop("pzt_000_probe_000")
        else:
            manifest["points"]["pzt_000_probe_000"]["pzt_index"] = 20
        path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    ctx = analysis.AnalysisWorkflow(run_dir)
    with pytest.raises((ValueError, OSError)):
        ctx.execute(["B01", "B02"])
    assert old.read_text(encoding="utf-8") == '{"old": true}'
    assert not list(results.glob("before_*"))


@pytest.mark.parametrize("version", [1, 2])
def test_explicit_legacy_format_and_parameter_wait_migration(run_dir, version):
    path = run_dir / "experiment_config.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config.pop("raw_format_version")
    config["schema_version"] = version
    config["parameters"].pop("SYSTEM_SETTLE_S")
    config["parameters"]["RESPONSE_SETTLE_TIME_S"] = 0.7
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    ctx = analysis.AnalysisWorkflow(run_dir)
    assert ctx.base_params.system_settle_s == 0.7
    ctx.execute(["B01"])


@pytest.mark.parametrize("change", [dict(experiment_id="other"), dict(raw_format_version=3), dict(completion_status="running"), dict(raw_format_version=None, schema_version=99)])
def test_unknown_identity_format_and_active_run_are_rejected(run_dir, change):
    path = run_dir / "experiment_config.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8")) | change
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    with pytest.raises(ValueError):
        analysis.AnalysisWorkflow(run_dir)


def test_quality_failure_retains_diagnostics_and_excludes_invalid_points(run_dir):
    path = run_dir / "raw/point_manifest.yaml"
    manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
    for record in manifest["points"].values():
        record.update(status="invalid_quality", reason="模拟不合格")
    # 有汇总的无效点仍沿用旧分析规则；令其拟合质量实际不合格。
    for point in (run_dir / "raw/points").iterdir():
        np.savez(point / "amplitude_scan.npz", signed_amplitude_vpp=np.linspace(-0.2, .2, 41),
            r_mean_v=np.full(41, 0.2), r_std_v=np.full(41, 1e-6))
    path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    ctx = analysis.AnalysisWorkflow(run_dir)
    with pytest.raises(analysis_steps.AnalysisQualityError):
        ctx.execute(["B01", "B02"])
    assert ctx.states["B02"]["status"] == "quality_failed"
    summary = json.loads((run_dir / "results/optimization.json").read_text(encoding="utf-8"))
    assert summary["valid_point_count"] == 0 and summary["best_point"] is None
    assert (run_dir / "results/sensitivity_heatmap.png").is_file()


def test_cancel_is_not_swallowed_as_a_bad_grid_point(run_dir, monkeypatch):
    ctx = analysis.AnalysisWorkflow(run_dir)
    ctx.execute(["B01"])
    failure = WorkflowCancelled("模拟分析取消")
    def cancel(*args, **kwargs):
        raise failure
    monkeypatch.setattr(analysis_steps, "evaluate_mx_y_rf_point", cancel)
    with pytest.raises(WorkflowCancelled) as caught:
        ctx.execute(["B02"])
    assert caught.value is failure and ctx.states["B02"]["status"] == "cancelled"


@pytest.mark.parametrize("options", [{"LOW_FREQ_SKIP_HZ": float("nan")}, {"FIT_R_SQUARED_MIN": 2}, {"Y_RF_NT_PER_VPP": 0}, {"NOISE_N_AVG": 99}, {"LOW_FREQ_SKIP_HZ": True}])
def test_invalid_analysis_overrides_are_rejected(run_dir, options):
    with pytest.raises((ValueError, TypeError)):
        analysis.AnalysisWorkflow(run_dir, options)


def test_input_change_during_science_is_not_marked_completed(run_dir, monkeypatch):
    ctx = analysis.AnalysisWorkflow(run_dir)
    ctx.execute(["B01"])
    evaluate = analysis_steps.evaluate

    def change_inputs(*args):
        result = evaluate(*args)
        path = run_dir / "raw/point_manifest.yaml"
        path.write_text(path.read_text(encoding="utf-8") + "\n# 计算期间输入变化\n", encoding="utf-8")
        return result

    monkeypatch.setattr(analysis_steps, "evaluate", change_inputs)
    with pytest.raises(analysis_steps.AnalysisQualityError, match="计算期间"):
        ctx.execute(["B02"])
    assert ctx.states["B02"]["status"] == "quality_failed"
    assert analysis.AnalysisWorkflow(run_dir).states["B01"]["status"] == "stale"


def test_results_alias_to_raw_cannot_move_acquisition_files(run_dir):
    results = run_dir / "results"
    results.mkdir()
    link = results / "points"
    if os.name == "nt":
        subprocess.run(["cmd", "/d", "/c", "mklink", "/J", str(link), str(run_dir / "raw/points")],
            check=True, capture_output=True)
    else:
        link.symlink_to(run_dir / "raw/points", target_is_directory=True)
    before = raw_bytes(run_dir)
    with pytest.raises(ValueError, match="符号链接/目录联接"):
        analysis.AnalysisWorkflow(run_dir).execute(["B01", "B02"])
    assert raw_bytes(run_dir) == before


def test_real_subprocess_and_thin_entrypoint_require_explicit_directory(run_dir):
    env = os.environ.copy() | {"LAB_RUN_DIR": str(run_dir), "LAB_TYPED_PARAMETERS": "{}", "PYTHONUTF8": "1", "MPLBACKEND": "Agg"}
    completed = subprocess.run([sys.executable, "-m", "lab_workflows.experiment_modules.mx_y_rf_probe_detuning_optimization.analysis"],
        env=env, capture_output=True, text=True, encoding="utf-8", timeout=90)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert (run_dir / "results/optimization.json").is_file()
    script = find_project_root() / "experiments/Mx_Y_RF_Probe_Detuning_Optimization_plot.py"
    missing = subprocess.run([sys.executable, str(script)], env=env, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert missing.returncode != 0 and "必须显式指定目录" in missing.stderr
