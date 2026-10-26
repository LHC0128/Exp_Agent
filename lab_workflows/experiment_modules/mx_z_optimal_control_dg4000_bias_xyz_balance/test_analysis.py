"""只在显式临时 run_dir 验证原复数科学算法、历史、产物和真实离线入口。"""
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
from .models import MxZOptimalControlDG4000BiasXYZBalanceParams


REGRESSION_CASES = {
    "mx-z-optimal-control-dg4000-bias-xyz-balance": {
        "analysis": {
            "sources": [
                "lab_workflows/experiment_modules/mx_z_optimal_control_dg4000_bias_xyz_balance/__init__.py",
                "lab_workflows/experiment_modules/mx_z_optimal_control_dg4000_bias_xyz_balance/analysis.py",
                "lab_workflows/experiment_modules/mx_z_optimal_control_xyz_balance/__init__.py"
            ],
            "tests": [
                "test_original_complex_fit_measured_tie_and_full_staged_match",
                "test_content_fingerprints_product_stale_revision_and_archive",
                "test_bad_inputs_do_not_move_previous_products_or_run_successor",
                "test_history_qualification_parameter_version_independent_of_raw",
                "test_missing_data_invalid_calibration_and_options_rejected",
                "test_single_z_plane_and_insufficient_xy_keep_original_rule",
                "test_actual_module_and_thin_entry_require_explicit_temporary_directory"
            ]
        }
    }
}


def synthetic_run(base, name="history"):
    directory = base / name
    (directory / "raw").mkdir(parents=True)
    config = dict(experiment_id=run_format.EXPERIMENT_ID, data_type=run_format.DATA_TYPE,
        schema_version=3, raw_format_version=1, scan_mode="nested_scan", completion_status="completed",
        z_control_calibration=dict(slope_hz_per_v=1000., intercept_hz=2., gamma_hz_per_nt=7.),
        parameters={"RUN_TAG": "historical", "CONTROL_RESULTS_ROOT": "missing-theory",
            "Z_CALIBRATION_SOURCE_RUN": "removed-calibration", "CONTROL_WAVEFORM_SOURCE": "corrected_run",
            "CORRECTED_CONTROL_SOURCE_RUN": "removed-frozen"})
    (directory / "experiment_config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    x, y, z = np.array([-.02, 0., .02]), np.array([-.02, 0., .02]), np.array([-.01, .01])
    u, v = np.meshgrid((x + .004) / .02, (y - .003) / .025, indexing="ij")
    # 独立公式，不调用被测模型生成响应。
    sx = .01 + (.3 * u + .04 * v) / (1 + u ** 2 + v ** 2)
    sy = -.02 + (-.05 * u + .2 * v) / (1 + u ** 2 + v ** 2)
    xm, ym = np.stack([sx, sx]), np.stack([sy, sy])
    r = np.hypot(xm, ym)
    r[1, 0, 2] = r[1, 2, 1] = 0.
    data = dict(x_field_v=x, y_field_v=y, z_bias_v=z, r_mean_v=r, x_mean_v=xm, y_mean_v=ym,
        r_std_v=np.full(r.shape, .001), x_std_v=np.full(r.shape, .001), y_std_v=np.full(r.shape, .001),
        complex_std_v=np.full(r.shape, .001 * np.sqrt(2)), acquisition_order=np.arange(r.size).reshape(r.shape),
        actual_rate_sa_s=np.float64(923.))
    np.savez(directory / "raw/xyz_balance_scan.npz", **data)
    return directory, data


def replace_npz(path, **changes):
    with np.load(path, allow_pickle=False) as data:
        values = {key: data[key] for key in data.files}
    np.savez(path, **(values | changes))


def test_original_complex_fit_measured_tie_and_full_staged_match(tmp_path):
    directory, data = synthetic_run(tmp_path)
    staged = tmp_path / "staged"
    shutil.copytree(directory, staged)
    before = yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf-8"))["parameters"]
    result = analysis.analyze(directory)
    ctx = analysis.AnalysisWorkflow(staged)
    for stage in ("B01", "B02", "B03"):
        ctx.execute([stage], ctx.revision)
    assert result["criterion"] == "robust complex Demod X/Y Bloch fit"
    assert result["measured_grid_minimum"]["acquisition_index"] == 11
    assert result["fitted_balance_point"]["z_bias_v"] == -.002
    np.testing.assert_allclose([result["fitted_balance_point"][k] for k in ("x_field_v", "y_field_v")],
        [-.004, .003], atol=1e-6)
    for key in ("measured_grid_minimum", "fitted_balance_point", "response_summary", "bloch_fit", "interpretation", "warnings"):
        assert result[key] == ctx.summary[key]
    assert result["interpretation"]["periodic_floquet_fit_available"] is False
    assert ctx.options == {}
    assert yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf-8"))["parameters"] == before
    for name in ("xyz_balance_slices", "xyz_balance_components", "bloch_fit_summary"):
        assert all((directory / "results" / (name + ext)).is_file() for ext in (".png", ".pdf"))
    with np.load(directory / "results/bloch_fit_analysis.npz") as fit:
        assert fit["fit_parameters"].shape == (2, 10)
        np.testing.assert_allclose(fit["robust_xy_center_v"], [-.004, .003], atol=1e-6)
    assert result["response_summary"]["actual_rate_sa_s"] == float(data["actual_rate_sa_s"])


def test_content_fingerprints_product_stale_revision_and_archive(tmp_path):
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
    {"x_mean_v": np.zeros((2, 2, 2))},
    {"y_std_v": np.full((2, 3, 3), -.01)},
    {"complex_std_v": np.full((2, 3, 3), np.inf)},
    {"acquisition_order": np.zeros((2, 3, 3), dtype=int)},
    {"actual_rate_sa_s": np.array([923.])},
    {"actual_rate_sa_s": np.float64(np.nan)},
    {"x_field_v": np.array([0., np.nan, .02])},
    {"y_field_v": np.array([.02, 0., -.02])},
    {"z_bias_v": np.array([.01, .01])},
])
def test_bad_inputs_do_not_move_previous_products_or_run_successor(tmp_path, changes):
    directory, _ = synthetic_run(tmp_path)
    (directory / "results").mkdir()
    previous = directory / "results/analysis.yaml"
    previous.write_text("previous: retained\n", encoding="utf-8")
    before = previous.read_bytes()
    replace_npz(directory / "raw/xyz_balance_scan.npz", **changes)
    ctx = analysis.AnalysisWorkflow(directory)
    with pytest.raises(ValueError):
        ctx.execute(["B01", "B02", "B03"])
    assert previous.read_bytes() == before and not list((directory / "results").glob("before_*"))
    assert ctx.states["B01"]["status"] == "failed" and ctx.states["B02"]["status"] != "completed"


@pytest.mark.parametrize("schema,raw,allowed", [(1, None, True), (2, None, True), (3, None, True),
    (4, None, False), (99, 1, True), (3, 2, False), (3, True, False)])
def test_history_qualification_parameter_version_independent_of_raw(schema, raw, allowed):
    config = dict(experiment_id=run_format.EXPERIMENT_ID, data_type=run_format.DATA_TYPE,
        schema_version=schema, scan_mode="nested_scan", parameters={}, completion_status="completed")
    if raw is not None:
        config["raw_format_version"] = raw
    assert (not run_format.analysis_block_reason(config)) is allowed
    config["experiment_id"] = "other"
    assert run_format.format_block_reason(config)


def test_missing_data_invalid_calibration_and_options_rejected(tmp_path):
    directory, _ = synthetic_run(tmp_path)
    with pytest.raises(ValueError, match="覆盖"):
        analysis.AnalysisWorkflow(directory, {"RUN_TAG": "other"})
    assert not (directory / "analysis_state.json").exists()
    path = directory / "experiment_config.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    for changes in ({"completion_status": "running"}, {"completion_status": "failed"},
            {"xyz_restore": {"success": False}}, {"z_channel_restore": {"success": False}},
            {"trigger_channel_restore": {"success": False}}, {"safety_shutdown": None}):
        assert run_format.analysis_block_reason(config | changes)
    path.write_text(yaml.safe_dump(config | {"z_control_calibration": {}}), encoding="utf-8")
    with pytest.raises(ValueError, match="静态标定"):
        analysis_steps.load_inputs(directory)
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    (directory / "raw/xyz_balance_scan.npz").unlink()
    with pytest.raises(FileNotFoundError):
        analysis.analyze(directory)


def test_legacy_source_and_wait_migration_roundtrip_and_explicit_priority():
    old = {"RUN_TAG": "old", "RESPONSE_SETTLE_TIME_S": .2, "TEMP_SWITCH_OFF_LEAD_S": .4,
        "TEMPERATURE_TIMEOUT_S": 100.}
    value = MxZOptimalControlDG4000BiasXYZBalanceParams.from_external(old, schema_version=1)
    assert value.control_waveform_source == "theory" and value.system_settle_s == .4
    current = MxZOptimalControlDG4000BiasXYZBalanceParams.from_external(old | {
        "SYSTEM_SETTLE_S": .01, "CONTROL_WAVEFORM_SOURCE": "corrected_run"}, schema_version=1)
    assert current.system_settle_s == .01 and current.control_waveform_source == "corrected_run"
    assert MxZOptimalControlDG4000BiasXYZBalanceParams.from_external(
        yaml.safe_load(yaml.safe_dump(current.to_external()))).to_external() == current.to_external()


def test_single_z_plane_and_insufficient_xy_keep_original_rule(tmp_path):
    directory, data = synthetic_run(tmp_path)
    replace_npz(directory / "raw/xyz_balance_scan.npz", z_bias_v=np.array([0.]),
        **{k: v[:1] for k, v in data.items() if k.endswith("_mean_v") or k.endswith("_std_v") or k == "acquisition_order"})
    result = analysis.analyze(directory)
    assert result["bloch_fit"]["plane_count"] == 1
    replace_npz(directory / "raw/xyz_balance_scan.npz", x_field_v=np.array([0.]),
        **{k: v[:1, :1] for k, v in data.items() if k.endswith("_mean_v") or k.endswith("_std_v") or k == "acquisition_order"})
    with pytest.raises(ValueError, match="至少需要"):
        analysis_steps.load_inputs(directory)


def test_actual_module_and_thin_entry_require_explicit_temporary_directory(tmp_path):
    directory, _ = synthetic_run(tmp_path)
    env = dict(os.environ, LAB_RUN_DIR=str(directory), LAB_TYPED_PARAMETERS="{}", MPLBACKEND="Agg", PYTHONIOENCODING="utf-8")
    args = dict(cwd=Path(__file__).resolve().parents[3], env=env, capture_output=True, text=True, encoding="utf-8", timeout=90)
    result = subprocess.run([sys.executable, "-m", analysis.__name__], **args)
    assert result.returncode == 0, result.stderr
    assert (directory / "results/bloch_fit_analysis.npz").is_file()
    env.pop("LAB_RUN_DIR")
    result = subprocess.run([sys.executable, "-m", analysis.__name__], **args)
    assert result.returncode != 0 and "LAB_RUN_DIR" in result.stderr
    script = args["cwd"] / "experiments/Mx_Z_Optimal_Control_DG4000_Bias_XYZ_Balance_plot.py"
    result = subprocess.run([sys.executable, str(script)], **args)
    assert result.returncode != 0 and "run_dir" in result.stderr
    result = subprocess.run([sys.executable, str(script), str(directory)], **args)
    assert result.returncode == 0, result.stderr
    assert (directory / "results/analysis.json").is_file()
