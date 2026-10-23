"""用真实原始文件核对原算法、分步分析、独立覆盖及只读历史边界。"""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys
import numpy as np
import pytest
import yaml
from ..mx_y_rf_grid_analysis import analyze_mx_y_rf_grid
from ..mx_y_rf_sensitivity.analysis_plotting import _plot_full_analysis
from ..mx_y_rf_sensitivity.point_analysis import evaluate_mx_y_rf_point
from . import analysis, analysis_steps, workflow
from .models import MxYRFPowerOptimizationParams
from .test_workflow import rig


@pytest.fixture
def run_dir(rig):
    return workflow.run(rig.params)


def digest_tree(path):
    return {p.relative_to(path).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in path.rglob("*") if p.is_file()}


def test_original_full_and_staged_science_are_identical_and_reads_do_not_write(run_dir):
    captured = (run_dir / "experiment_config.yaml").read_bytes()
    raw = digest_tree(run_dir / "raw")
    old = analyze_mx_y_rf_grid(run_dir, params_type=MxYRFPowerOptimizationParams,
        spec=analysis.GRID_SPEC, evaluate_point=evaluate_mx_y_rf_point, plot_full_analysis=_plot_full_analysis)
    with np.load(run_dir / "results/optimization.npz") as data:
        expected = {key: data[key].copy() for key in data.files}
    ctx = analysis.AnalysisWorkflow(run_dir)
    assert not ctx.state_path.exists()
    with pytest.raises(ValueError, match="前置"):
        ctx.execute(["B03"])
    ctx.execute(["B01"])
    ctx = analysis.AnalysisWorkflow(run_dir)
    ctx.execute(["B02"])
    ctx = analysis.AnalysisWorkflow(run_dir)
    result = ctx.execute(["B03"])
    assert result["ranking"] == old["ranking"] and result["best_point"] == old["best_point"]
    assert result["zero_point_method"] == old["zero_point_method"]
    with np.load(run_dir / "results/optimization.npz") as data:
        assert set(data.files) == set(expected)
        for key, value in expected.items():
            if value.dtype.kind in "biufc":
                np.testing.assert_allclose(data[key], value, rtol=1e-12, atol=1e-18, equal_nan=True)
            else:
                np.testing.assert_array_equal(data[key], value)
    original_products = {name: (run_dir / "results" / name).read_bytes()
        for name in ctx.states["B02"]["artifacts"]}
    state = ctx.state_path.read_bytes()
    analysis.AnalysisWorkflow(run_dir).snapshot()
    assert ctx.state_path.read_bytes() == state
    ctx.execute(["B03"])
    assert all((run_dir / "results" / name).read_bytes() == value for name, value in original_products.items())
    assert (run_dir / "experiment_config.yaml").read_bytes() == captured
    assert digest_tree(run_dir / "raw") == raw


def test_independent_options_invalidate_only_calculation_and_keep_capture_snapshot(run_dir):
    config = (run_dir / "experiment_config.yaml").read_bytes()
    ctx = analysis.AnalysisWorkflow(run_dir)
    ctx.execute(["B01"])
    revision = ctx.revision
    ctx = analysis.AnalysisWorkflow(run_dir, {"LOW_FREQ_SKIP_HZ": 7, "Y_RF_NT_PER_VPP": 120})
    assert ctx.states["B01"]["status"] == "completed"
    assert ctx.options["LOW_FREQ_SKIP_HZ"] == 7
    result = ctx.execute(["B02", "B03"], revision)
    assert result["analysis_parameters"]["LOW_FREQ_SKIP_HZ"] == 7
    assert ctx.states["B02"]["parameters"]["Y_RF_NT_PER_VPP"] == 120
    assert (run_dir / "experiment_config.yaml").read_bytes() == config
    assert analysis.AnalysisWorkflow(run_dir).options["LOW_FREQ_SKIP_HZ"] == 7
    for invalid in ({"RUN_TAG": "unsafe"}, {"LOW_FREQ_SKIP_HZ": True}, {"LOW_FREQ_SKIP_HZ": float("nan")}):
        with pytest.raises(ValueError):
            analysis.AnalysisWorkflow(run_dir, invalid)


def test_nested_content_and_products_cannot_reuse_stale_predecessors(run_dir):
    analysis.analyze(run_dir)
    state_bytes = (run_dir / "analysis_state.json").read_bytes()
    product = run_dir / "results/optimization.json"
    original, stat = product.read_bytes(), product.stat()
    changed = original.replace(b'"success": true', b'"success": null', 1)
    assert len(changed) == len(original) and changed != original
    product.write_bytes(changed)
    os.utime(product, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    ctx = analysis.AnalysisWorkflow(run_dir)
    assert ctx.states["B01"]["status"] == "completed"
    assert ctx.states["B02"]["status"] == ctx.states["B03"]["status"] == "stale"
    with pytest.raises(ValueError, match="前置"):
        ctx.execute(["B03"])
    product.write_bytes(original)
    response = next((run_dir / "raw/points").glob("*/amplitude_scan.npz"))
    with np.load(response) as data:
        payload = dict(data)
    payload["r_std_v"] = payload["r_std_v"] + 1e-12
    np.savez(response, **payload)
    ctx = analysis.AnalysisWorkflow(run_dir)
    assert ctx.states["B01"]["status"] == "stale"
    assert (run_dir / "analysis_state.json").read_bytes() == state_bytes
    with pytest.raises(ValueError, match="前置"):
        ctx.execute(["B02"])
    before = digest_tree(run_dir / "results")
    manifest_path = run_dir / "raw/point_manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["points"]["../escape"] = manifest["points"].pop(next(iter(manifest["points"])))
    manifest_path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="索引"):
        ctx.execute(["B01", "B02"])
    assert digest_tree(run_dir / "results") == before


def test_no_valid_point_keeps_diagnostics_and_blocks_report(run_dir):
    for path in (run_dir / "raw/points").glob("*/amplitude_scan.npz"):
        with np.load(path) as data:
            payload = dict(data)
        payload["r_mean_v"] = payload["r_scalar_mean_v"] = np.zeros_like(payload["r_mean_v"])
        np.savez(path, **payload)
    ctx = analysis.AnalysisWorkflow(run_dir)
    with pytest.raises(analysis.AnalysisQualityError):
        ctx.execute(["B01", "B02", "B03"])
    assert ctx.states["B02"]["status"] == "quality_failed"
    assert ctx.states["B03"]["status"] == "pending"
    assert (run_dir / "results/optimization.npz").is_file()
    result = yaml.safe_load((run_dir / "results/optimization.yaml").read_text(encoding="utf-8"))
    assert result["success"] is False and result["valid_point_count"] == 0
    with pytest.raises(ValueError, match="前置"):
        analysis.AnalysisWorkflow(run_dir).execute(["B03"])


def test_partial_legacy_grid_and_format_identity(run_dir):
    path = run_dir / "experiment_config.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config.pop("raw_format_version")
    config["schema_version"] = 1
    config["completion_status"] = "cancelled"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    noises = sorted((run_dir / "raw/points").glob("*/noise_000.npz"))
    noises[0].unlink()
    noises[1].write_bytes(b"corrupt waveform")
    result = analysis.analyze(run_dir)
    assert result["total_point_count"] == 4 and 0 < result["valid_point_count"] <= 2
    assert not result["points"][0]["valid"] and not result["points"][1]["valid"]
    assert all(point["invalid_reasons"] for point in result["points"][:2])
    for key, value in (("experiment_id", "other"), ("raw_format_version", 99), ("completion_status", "running")):
        rejected = config | {key: value}
        path.write_text(yaml.safe_dump(rejected), encoding="utf-8")
        with pytest.raises(ValueError):
            analysis.AnalysisWorkflow(run_dir)
    assert (run_dir / "results/optimization.npz").is_file()


def test_cancel_is_not_swallowed_as_an_invalid_grid_point(run_dir, monkeypatch):
    from ...common import WorkflowCancelled
    failure = WorkflowCancelled("分析取消")
    def cancel():
        raise failure
    params, _ = analysis_steps.load_inputs(run_dir)
    monkeypatch.setattr(analysis_steps, "check_cancelled", cancel)
    with pytest.raises(WorkflowCancelled) as caught:
        analysis_steps.evaluate(run_dir, params)
    assert caught.value is failure
    assert not (run_dir / "results/optimization.npz").exists()


def test_thin_entry_requires_explicit_directory_and_runs_real_child(run_dir):
    entry = Path(__file__).resolve().parents[3] / "experiments/Mx_Y_RF_Power_Optimization_plot.py"
    rejected = subprocess.run([sys.executable, str(entry)], capture_output=True, text=True)
    assert rejected.returncode != 0 and "run_dir" in rejected.stderr
    assert not (run_dir / "results/optimization.npz").exists()
    child = subprocess.run([sys.executable, str(entry), str(run_dir)], capture_output=True, text=True, timeout=90)
    assert child.returncode == 0, child.stdout + child.stderr
    assert (run_dir / "results/optimization.npz").is_file()
    assert json.loads((run_dir / "analysis_state.json").read_text(encoding="utf-8"))["steps"]["B03"]["status"] == "completed"
