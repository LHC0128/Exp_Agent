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
from .models import MxZOptimalControlXYLeakageParams


def synthetic_run(base, name="history"):
    directory = base / name
    (directory / "raw").mkdir(parents=True)
    config = dict(experiment_id=run_format.EXPERIMENT_ID, data_type=run_format.DATA_TYPE, schema_version=2,
        raw_format_version=1, scan_mode="nested_scan", completion_status="completed",
        parameters={"RUN_TAG": "historical", "CONTROL_RESULTS_ROOT": "nonexistent-theory", "Z_CALIBRATION_SOURCE_RUN": "removed-calibration"})
    (directory / "experiment_config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    data = dict(x_signed_amplitude_vpp=np.array([-.02, 0., .02]), y_signed_amplitude_vpp=np.array([-.01, 0., .01]),
        r_mean_v=np.array([[2., 1., 0.], [1., 2., 0.], [3., 2., 1.]]), r_std_v=np.full((3, 3), .01),
        acquisition_order=np.array([[0, 1, 2], [5, 4, 3], [6, 7, 8]]), actual_rate_sa_s=np.float64(923.))
    np.savez(directory / "raw/xy_leakage_scan.npz", **data)
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
    expected = dict(x_index=0, y_index=2, x_signed_amplitude_vpp=-.02, y_signed_amplitude_vpp=.01,
        r_mean_v=0., r_std_v=.01, acquisition_index=2, combined_state_remeasured=False)
    assert result["measured_grid_minimum"] == ctx.summary["measured_grid_minimum"] == expected
    assert result["response_summary"]["mean_r_v"] == np.mean(data["r_mean_v"])
    assert result["response_summary"]["actual_rate_sa_s"] == 923.
    assert result["interpretation"] == dict(continuous_fit_performed=False, xy_field_calibration_performed=False,
        absolute_leakage_field_reported=False)
    assert ctx.options == {}
    assert (directory / "results/xy_leakage_response.png").is_file()
    assert (directory / "results/xy_leakage_response.pdf").is_file()
    assert yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf-8"))["parameters"] == original_params
    for key in ("response_summary", "scan_shape", "scan_completed", "interpretation", "warnings"):
        assert result[key] == ctx.summary[key]


def test_content_and_product_fingerprints_archive_and_stale_revision(tmp_path):
    directory, _ = synthetic_run(tmp_path)
    analysis.analyze(directory)
    raw = directory / "raw/xy_leakage_scan.npz"
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
    assert not list((directory / "results").glob("before_*/xy_leakage_scan.npz"))


@pytest.mark.parametrize("changes", [
    {"r_mean_v": np.full((3, 3), np.nan)}, {"r_std_v": np.zeros((2, 2))},
    {"r_std_v": np.full((3, 3), -.01)}, {"acquisition_order": np.zeros((3, 3))},
    {"actual_rate_sa_s": np.array([923.])}, {"actual_rate_sa_s": np.float64(np.nan)},
    {"x_signed_amplitude_vpp": np.array([0., -.02, .02])},
])
def test_bad_inputs_do_not_move_previous_products_and_block_successor(tmp_path, changes):
    directory, _ = synthetic_run(tmp_path)
    (directory / "results").mkdir()
    old = directory / "results/analysis.yaml"
    old.write_text("previous: retained\n", encoding="utf-8")
    before = old.read_bytes()
    replace_npz(directory / "raw/xy_leakage_scan.npz", **changes)
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
    (directory / "raw/xy_leakage_scan.npz").unlink()
    with pytest.raises(FileNotFoundError):
        analysis.analyze(directory)
    np.savez(directory / "raw/xy_leakage_scan.npz", r_mean_v=np.ones((1, 1)))
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
                    {"safety_shutdown": None}, {"main_field_restore": {"success": "false"}}):
        assert run_format.analysis_block_reason(config | changes)


def test_legacy_parameter_migration_explicit_key_priority_and_yaml_roundtrip():
    old = {"RESPONSE_SETTLE_TIME_S": .2, "TEMP_SWITCH_OFF_LEAD_S": .4,
        "TEMPERATURE_TIMEOUT_S": 100., "RUN_TAG": "historical"}
    migrated = MxZOptimalControlXYLeakageParams.from_external(old, schema_version=1)
    assert migrated.system_settle_s == .4
    current = MxZOptimalControlXYLeakageParams.from_external(old | {"SYSTEM_SETTLE_S": .01}, schema_version=1)
    assert current.system_settle_s == .01
    assert MxZOptimalControlXYLeakageParams.from_external(yaml.safe_load(yaml.safe_dump(current.to_external()))).to_external() == current.to_external()


def test_real_module_and_thin_entry_require_explicit_temporary_directory(tmp_path):
    directory, _ = synthetic_run(tmp_path)
    env = dict(os.environ, LAB_RUN_DIR=str(directory), LAB_TYPED_PARAMETERS="{}", MPLBACKEND="Agg", PYTHONIOENCODING="utf-8")
    args = dict(cwd=Path(__file__).resolve().parents[3], env=env, capture_output=True, text=True, encoding="utf-8", timeout=90)
    result = subprocess.run([sys.executable, "-m", analysis.__name__], **args)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads((directory / "results/analysis.json").read_text(encoding="utf-8"))["measured_grid_minimum"]["acquisition_index"] == 2
    env.pop("LAB_RUN_DIR")
    result = subprocess.run([sys.executable, "-m", analysis.__name__], **args)
    assert result.returncode != 0 and "LAB_RUN_DIR" in result.stderr
    script = Path(__file__).resolve().parents[3] / "experiments/Mx_Z_Optimal_Control_XY_Leakage_Response_plot.py"
    result = subprocess.run([sys.executable, str(script)], **args)
    assert result.returncode != 0 and "run_dir" in result.stderr
    result = subprocess.run([sys.executable, str(script), str(directory)], **args)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (directory / "results/analysis.json").is_file()
