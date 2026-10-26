"""显式临时 run_dir：原科学约定、格式资格与分阶段产物保护。"""
from pathlib import Path
import json
import os
import shutil
import subprocess
import sys
import numpy as np
import pytest
import yaml
from scipy.signal import welch
from . import workflow, analysis, analysis_steps, run_format
from .models import MxZOptimalControlXYRFSensitivityParams
from .test_workflow import copy_config, install_rig, parameters


REGRESSION_CASES = {
    "mx-z-optimal-control-xy-rf-sensitivity": {
        "analysis": {
            "sources": [
                "lab_workflows/experiment_modules/mx_z_optimal_control_xy_rf_sensitivity/__init__.py",
                "lab_workflows/experiment_modules/mx_z_optimal_control_xy_rf_sensitivity/analysis.py"
            ],
            "tests": [
                "test_full_staged_science_and_independent_welch",
                "test_nested_content_and_product_fingerprints_and_archive",
                "test_bad_input_cannot_move_previous_scientific_products",
                "test_invalid_grid_keeps_diagnostics_and_blocks_successor",
                "test_explicit_history_and_independent_raw_qualification",
                "test_real_analysis_subprocess_requires_explicit_temp_directory"
            ]
        }
    }
}


@pytest.fixture
def run_dir(tmp_path, monkeypatch):
    copy_config(tmp_path)
    rig = install_rig(tmp_path, monkeypatch, parameters(x_field_start_v=0., x_field_stop_v=0., x_field_points=1,
        y_field_start_v=0., y_field_stop_v=0., y_field_points=1))
    return workflow.run(rig.params)


def replace_npz(path, **changes):
    with np.load(path) as data:
        values = {key: data[key] for key in data.files}
    np.savez(path, **(values | changes))


def test_full_staged_science_and_independent_welch(run_dir, tmp_path):
    staged = tmp_path / "staged"
    shutil.copytree(run_dir, staged)
    full = analysis.analyze(run_dir)
    ctx = analysis.AnalysisWorkflow(staged)
    for key in ("B01", "B02", "B03"):
        ctx.execute([key], ctx.revision)
    assert full["status"] == ctx.summary["status"] == "completed"
    assert full["best_point"]["key"] == "x_000_y_000"
    assert ctx.options == {}
    with np.load(run_dir / "results/optimization.npz") as left, np.load(staged / "results/optimization.npz") as right:
        for key in left.files:
            if left[key].dtype.kind in "biufc":
                np.testing.assert_allclose(left[key], right[key], rtol=1e-12, atol=1e-12, equal_nan=True)
            else:
                np.testing.assert_array_equal(left[key], right[key])
    point = run_dir / "raw/points/x_000_y_000"
    values, rates = [], []
    for i in range(2):
        with np.load(point / f"noise_{i:03d}.npz") as raw:
            rate = float(raw["actual_rate_sa_s"])
            frequency, psd = welch(raw["r_v"], fs=rate, nperseg=raw["r_v"].size, detrend="constant", scaling="density")
            values.append(psd)
            rates.append(rate)
    params = parameters(x_field_start_v=0., x_field_stop_v=0., x_field_points=1,
        y_field_start_v=0., y_field_stop_v=0., y_field_points=1)
    evaluation = analysis_steps._evaluate_xy_point(point, params)
    np.testing.assert_allclose(evaluation["frequency_hz"], frequency)
    np.testing.assert_allclose(evaluation["psd_r"], np.mean(values, axis=0), rtol=1e-12, atol=1e-20)
    raw_equivalent = np.sqrt(np.mean(values, axis=0)) / abs(evaluation["primary_slope"])
    correction = np.sqrt(1 + (frequency / evaluation["hwhm_hz"]) ** 2)
    np.testing.assert_allclose(evaluation["sensitivity"]["corrected_ft_per_sqrt_hz"],
        raw_equivalent * correction * params.y_rf_nt_per_vpp * 1e6, rtol=1e-12)
    assert (run_dir / "results/best_point_full_analysis.png").is_file()
    assert (run_dir / "results/points/x_000_y_000/full_analysis.png").is_file()


def test_nested_content_and_product_fingerprints_and_archive(run_dir):
    analysis.analyze(run_dir)
    raw = run_dir / "raw/points/x_000_y_000/noise_000.npz"
    content, stat = raw.read_bytes(), raw.stat()
    raw.write_bytes(content[:-1] + bytes([content[-1] ^ 1]))
    os.utime(raw, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    ctx = analysis.AnalysisWorkflow(run_dir)
    assert ctx.states["B01"]["status"] == "stale"
    with pytest.raises(ValueError, match="缺少前置"):
        ctx.execute(["B03"])
    raw.write_bytes(content)
    ctx = analysis.AnalysisWorkflow(run_dir)
    product = run_dir / "results/points/x_000_y_000/analysis.yaml"
    product.write_text(product.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert analysis.AnalysisWorkflow(run_dir).states["B02"]["status"] == "stale"
    phase = (run_dir / "results/phase_calibration.yaml").read_bytes()
    ctx = analysis.AnalysisWorkflow(run_dir)
    ctx.execute(["B01", "B02", "B03"])
    assert list((run_dir / "results").glob("before_*/points/x_000_y_000/analysis.yaml"))
    assert (run_dir / "results/phase_calibration.yaml").read_bytes() == phase


def test_bad_input_cannot_move_previous_scientific_products(run_dir):
    analysis.analyze(run_dir)
    result = run_dir / "results/optimization.npz"
    before = result.read_bytes()
    replace_npz(run_dir / "raw/points/x_000_y_000/amplitude_scan.npz", r_std_v=np.zeros((2, 2)))
    ctx = analysis.AnalysisWorkflow(run_dir)
    with pytest.raises(ValueError, match="形状"):
        ctx.execute(["B01"])
    assert result.read_bytes() == before


@pytest.mark.parametrize("missing_noise", [False, True])
def test_invalid_grid_keeps_diagnostics_and_blocks_successor(run_dir, missing_noise):
    point = run_dir / "raw/points/x_000_y_000"
    if missing_noise:
        (point / "noise_000.npz").unlink()
    else:
        replace_npz(point / "amplitude_scan.npz", r_std_v=np.full(21, 999.))
    ctx = analysis.AnalysisWorkflow(run_dir)
    ctx.execute(["B01"])
    with pytest.raises(analysis_steps.AnalysisQualityError, match="未找到可用最优点"):
        ctx.execute(["B02", "B03"])
    assert ctx.states["B02"]["status"] == "quality_failed"
    assert ctx.states["B03"]["status"] != "completed"
    result = json.loads((run_dir / "results/analysis.json").read_text(encoding="utf-8"),
        parse_constant=lambda value: pytest.fail("JSON 不应包含 " + value))
    assert result["success"] is False and result["valid_point_count"] == 0
    assert result["points"][0]["invalid_reasons"]
    assert (run_dir / "results/sensitivity_heatmap.png").is_file()
    assert (run_dir / "results/optimization.npz").is_file()


@pytest.mark.parametrize("schema,raw,allowed", [(1, None, True), (2, None, True), (3, None, False),
    (99, 1, True), (2, 2, False), (2, True, False)])
def test_explicit_history_and_independent_raw_qualification(schema, raw, allowed):
    config = dict(experiment_id=run_format.EXPERIMENT_ID, data_type=run_format.DATA_TYPE,
        schema_version=schema, measurement_mode="xy_bias_grid_rf_sensitivity", parameters={}, completion_status="completed")
    if raw is not None:
        config["raw_format_version"] = raw
    assert (not run_format.analysis_block_reason(config)) is allowed
    config["experiment_id"] = "other"
    assert run_format.format_block_reason(config)


def test_parameters_migrate_without_changing_stable_keys():
    values = parameters().to_external()
    values.pop("SYSTEM_SETTLE_S")
    old = values | {"RESPONSE_SETTLE_TIME_S": .2, "FREQUENCY_SETTLE_TIME_S": .3, "TEMP_SWITCH_OFF_LEAD_S": .4}
    assert MxZOptimalControlXYRFSensitivityParams.from_external(old, schema_version=1).system_settle_s == .4
    current = MxZOptimalControlXYRFSensitivityParams.from_external(old | {"SYSTEM_SETTLE_S": .01}, schema_version=1)
    assert current.system_settle_s == .01
    assert MxZOptimalControlXYRFSensitivityParams.from_external(current.to_external(), schema_version=2).to_external() == current.to_external()


def test_real_analysis_subprocess_requires_explicit_temp_directory(run_dir, tmp_path):
    env = dict(os.environ, LAB_RUN_DIR=str(run_dir), LAB_TYPED_PARAMETERS="{}", PYTHONIOENCODING="utf-8", MPLBACKEND="Agg")
    result = subprocess.run([sys.executable, "-m", analysis.__name__], cwd=tmp_path, env=env,
        capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((run_dir / "results/analysis.json").read_text(encoding="utf-8"))
    assert report["success"] and report["best_point"]["key"] == "x_000_y_000"
    env.pop("LAB_RUN_DIR")
    result = subprocess.run([sys.executable, "-m", analysis.__name__], cwd=tmp_path, env=env,
        capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode != 0 and "LAB_RUN_DIR" in result.stderr
