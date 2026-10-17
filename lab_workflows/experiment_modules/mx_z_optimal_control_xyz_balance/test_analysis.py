"""明确临时 run_dir 的原科学规则、历史资格、指纹和真实子进程回归。"""

from pathlib import Path
import json
import os
import shutil
import subprocess
import sys

import numpy as np
import pytest
import yaml

from . import analysis, analysis_steps, run_format
from .models import MxZOptimalControlXYZBalanceParams


def synthetic_run(base, name="history"):
    directory = base / name
    (directory / "raw").mkdir(parents=True)
    config = dict(experiment_id=run_format.EXPERIMENT_ID, data_type=run_format.DATA_TYPE,
        schema_version=2, raw_format_version=1, scan_mode="nested_scan", completion_status="completed",
        parameters={"RUN_TAG": "historical", "CONTROL_RESULTS_ROOT": "nonexistent-theory",
            "Z_CALIBRATION_SOURCE_RUN": "removed-calibration"})
    (directory / "experiment_config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    r_mean = np.array([
        [[3., 2., 1.], [2., 2., 2.], [4., 3., 2.]],
        [[2., 1., 0.], [1., 2., 3.], [3., 0., 2.]],
    ])
    data = dict(
        x_field_v=np.array([-.02, 0., .02]),
        y_field_v=np.array([-.02, 0., .02]),
        z_field_ma=np.array([-.01, .01]),
        r_mean_v=r_mean,
        r_std_v=np.full(r_mean.shape, .01),
        acquisition_order=np.arange(r_mean.size).reshape(r_mean.shape),
        actual_rate_sa_s=np.float64(923.),
    )
    np.savez(directory / "raw/xyz_balance_scan.npz", **data)
    return directory, data


def replace_npz(path, **changes):
    with np.load(path, allow_pickle=False) as data:
        values = {key: data[key] for key in data.files}
    np.savez(path, **(values | changes))


def test_original_minimum_tie_breaking_staged_and_full_science(tmp_path):
    directory, data = synthetic_run(tmp_path)
    staged = tmp_path / "staged"
    shutil.copytree(directory, staged)
    original_params = yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf-8"))["parameters"]
    result = analysis.analyze(directory)
    ctx = analysis.AnalysisWorkflow(staged)
    for step in ("B01", "B02", "B03"):
        ctx.execute([step], ctx.revision)
    # 两个 0 平局：采集序号 11 早于 16，选择 z=1/x=0/y=2。
    expected = dict(z_index=1, x_index=0, y_index=2, z_field_ma=.01, x_field_v=-.02, y_field_v=.02,
        r_mean_v=0., r_std_v=.01, acquisition_index=11, remeasured=False)
    assert result["measured_grid_minimum"] == ctx.summary["measured_grid_minimum"] == expected
    assert result["response_summary"]["mean_r_v"] == np.mean(data["r_mean_v"])
    assert result["response_summary"]["actual_rate_sa_s"] == 923.
    assert result["interpretation"] == dict(continuous_fit_performed=True,
        fit_method="per_layer_1d_vshape", field_calibration_performed=False, absolute_field_reported=False)
    assert ctx.options == {}
    for name in ("xyz_balance_slices.png", "xyz_balance_slices.pdf", "xyz_balance_grid.csv",
            "balance_results.npz", "balance_linear_fit.npz", "xyz_balance_linear_fit.png",
            "xyz_balance_linear_fit.pdf", "evaluation.json"):
        assert (directory / "results" / name).is_file(), name
    assert yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf-8"))["parameters"] == original_params
    for key in ("response_summary", "scan_shape_zyx", "scan_completed", "interpretation", "warnings"):
        assert result[key] == ctx.summary[key]
    with np.load(directory / "results/balance_linear_fit.npz") as fit:
        assert fit["z_field_ma"].shape == (2,) and fit["outlier_mask"].shape == (2, 3, 3)


def test_content_and_product_fingerprints_archive_and_stale_revision(tmp_path):
    directory, _ = synthetic_run(tmp_path)
    analysis.analyze(directory)
    raw = directory / "raw/xyz_balance_scan.npz"
    content, stat = raw.read_bytes(), raw.stat()
    raw.write_bytes(content[:-1] + bytes([content[-1] ^ 1]))
    os.utime(raw, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    ctx = analysis.AnalysisWorkflow(directory)
    assert ctx.states["B01"]["status"] == "stale"
    with pytest.raises(ValueError, match="前置"):
        ctx.execute(["B03"])
    raw.write_bytes(content)
    product = directory / "results/evaluation.json"
    product.write_bytes(product.read_bytes() + b"\n")
    ctx = analysis.AnalysisWorkflow(directory)
    assert ctx.states["B02"]["status"] == "stale" and ctx.states["B01"]["status"] == "completed"
    with pytest.raises(ValueError, match="修订"):
        ctx.execute(["B02"], ctx.revision - 1)
    ctx.execute(["B02", "B03"], ctx.revision)
    assert list((directory / "results").glob("before_*/evaluation.json"))
    assert not list((directory / "results").glob("before_*/xyz_balance_scan.npz"))


@pytest.mark.parametrize("changes", [
    {"r_mean_v": np.full((2, 3, 3), np.nan)},
    {"r_std_v": np.zeros((2, 2, 2))},
    {"r_std_v": np.full((2, 3, 3), -.01)},
    {"acquisition_order": np.zeros((2, 3, 3), dtype=int)},
    {"actual_rate_sa_s": np.array([923.])},
    {"actual_rate_sa_s": np.float64(np.nan)},
    {"x_field_v": np.array([0., np.nan, .02])},
    {"z_field_ma": np.array([.01, .01])},
])
def test_bad_inputs_do_not_move_previous_products_and_block_successor(tmp_path, changes):
    directory, _ = synthetic_run(tmp_path)
    (directory / "results").mkdir()
    old = directory / "results/analysis.yaml"
    old.write_text("previous: retained\n", encoding="utf-8")
    before = old.read_bytes()
    replace_npz(directory / "raw/xyz_balance_scan.npz", **changes)
    ctx = analysis.AnalysisWorkflow(directory)
    with pytest.raises(ValueError):
        ctx.execute(["B01", "B02", "B03"])
    assert old.read_bytes() == before and not list((directory / "results").glob("before_*"))
    assert ctx.states["B01"]["status"] == "failed" and ctx.states["B02"]["status"] != "completed"


def test_missing_raw_and_rejected_analysis_options(tmp_path):
    directory, _ = synthetic_run(tmp_path)
    with pytest.raises(ValueError, match="覆盖"):
        analysis.AnalysisWorkflow(directory, {"RUN_TAG": "wrong"})
    assert not (directory / "analysis_state.json").exists()
    (directory / "raw/xyz_balance_scan.npz").unlink()
    with pytest.raises(FileNotFoundError):
        analysis.analyze(directory)
    np.savez(directory / "raw/xyz_balance_scan.npz", r_mean_v=np.ones((1, 1, 1)))
    with pytest.raises(ValueError, match="缺少字段"):
        analysis.analyze(directory)


@pytest.mark.parametrize("schema,raw,allowed", [(1, None, True), (2, None, True), (3, None, False),
    (99, 1, True), (2, 2, False), (2, True, False)])
def test_historical_schema_and_raw_format_are_independent(schema, raw, allowed):
    config = dict(experiment_id=run_format.EXPERIMENT_ID, data_type=run_format.DATA_TYPE, schema_version=schema,
        scan_mode="nested_scan", parameters={}, completion_status="completed")
    if raw is not None:
        config["raw_format_version"] = raw
    assert (not run_format.analysis_block_reason(config)) is allowed
    config["experiment_id"] = "other"
    assert run_format.format_block_reason(config)


def test_unsealed_or_failed_recovery_is_not_analyzable(tmp_path):
    directory, _ = synthetic_run(tmp_path)
    config = yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf-8"))
    for changes in ({"completion_status": "running"}, {"completion_status": "failed"},
                    {"safety_shutdown": {"completed": False}}, {"main_field_restore": {"success": False}},
                    {"xy_restore": {"success": False}}, {"safety_shutdown": None},
                    {"main_field_restore": {"success": "false"}}, {"scan_mode": "point_by_point"}):
        assert run_format.analysis_block_reason(config | changes)


def test_legacy_parameter_migration_explicit_key_priority_and_yaml_roundtrip():
    old = {"RESPONSE_SETTLE_TIME_S": .2, "TEMP_SWITCH_OFF_LEAD_S": .4,
        "TEMPERATURE_TIMEOUT_S": 100., "RUN_TAG": "historical"}
    migrated = MxZOptimalControlXYZBalanceParams.from_external(old, schema_version=1)
    assert migrated.system_settle_s == .4
    current = MxZOptimalControlXYZBalanceParams.from_external(old | {"SYSTEM_SETTLE_S": .01}, schema_version=1)
    assert current.system_settle_s == .01
    assert MxZOptimalControlXYZBalanceParams.from_external(
        yaml.safe_load(yaml.safe_dump(current.to_external()))).to_external() == current.to_external()


def test_single_point_axes_are_valid_and_reported(tmp_path):
    directory, _ = synthetic_run(tmp_path)
    replace_npz(directory / "raw/xyz_balance_scan.npz",
        x_field_v=np.array([0.]), r_mean_v=np.ones((2, 1, 3)), r_std_v=np.full((2, 1, 3), .01),
        acquisition_order=np.arange(6).reshape(2, 1, 3))
    result = analysis.analyze(directory)
    assert result["scan_shape_zyx"] == [2, 1, 3]
    assert result["measured_grid_minimum"]["x_field_v"] == 0.


def test_real_module_and_thin_entry_require_explicit_temporary_directory(tmp_path):
    directory, _ = synthetic_run(tmp_path)
    env = dict(os.environ, LAB_RUN_DIR=str(directory), LAB_TYPED_PARAMETERS="{}", MPLBACKEND="Agg", PYTHONIOENCODING="utf-8")
    args = dict(cwd=Path(__file__).resolve().parents[3], env=env, capture_output=True, text=True, encoding="utf-8", timeout=90)
    result = subprocess.run([sys.executable, "-m", analysis.__name__], **args)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads((directory / "results/analysis.json").read_text(encoding="utf-8"))["measured_grid_minimum"]["acquisition_index"] == 11
    env.pop("LAB_RUN_DIR")
    result = subprocess.run([sys.executable, "-m", analysis.__name__], **args)
    assert result.returncode != 0 and "LAB_RUN_DIR" in result.stderr
    script = Path(__file__).resolve().parents[3] / "experiments/Mx_Z_Optimal_Control_XYZ_Balance_plot.py"
    result = subprocess.run([sys.executable, str(script)], **args)
    assert result.returncode != 0 and "run_dir" in result.stderr
    result = subprocess.run([sys.executable, str(script), str(directory)], **args)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (directory / "results/analysis.json").is_file()
