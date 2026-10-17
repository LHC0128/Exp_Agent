"""真实历史原始文件上的整次、分阶段分析与归档回归。"""
import json
import numpy as np
import pytest
import yaml
from . import analysis, workflow, run_format
from .models import MxZFieldCalibrationParams
from .test_workflow import rig


def test_complete_and_staged_analysis_share_calibration_and_archive_only_dependents(rig):
    directory = workflow.run(rig.params)
    original_parameters = yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf-8"))["parameters"]
    full = analysis.analyze(directory)
    assert full["success"]
    assert full["K_Z_Hz_per_V"] == pytest.approx(1000, rel=1e-4)
    ctx = analysis.AnalysisWorkflow(directory)
    assert ctx.states["B04"]["status"] == "completed"
    for step in ("B01", "B02", "B03", "B04"):
        ctx = analysis.AnalysisWorkflow(directory)
        ctx.execute([step])
    split = json.loads((directory / "results/analysis.json").read_text(encoding="utf-8"))
    assert split["f_0V_Hz"] == full["f_0V_Hz"]
    assert split["K_Z_Hz_per_V"] == full["K_Z_Hz_per_V"]
    np.testing.assert_array_equal(split["linear_fit"]["residual_hz"], full["linear_fit"]["residual_hz"])
    curves = (directory / "results/curve_fits.json").read_bytes()
    archived_curves = set((directory / "results").glob("before_*/curve_fits.json"))
    ctx = analysis.AnalysisWorkflow(directory, {"LINEAR_R_SQUARED_MIN": 0.9999})
    assert ctx.states["B02"]["status"] == "completed"
    assert ctx.states["B03"]["status"] == "stale"
    ctx.execute(["B03", "B04"])
    assert (directory / "results/curve_fits.json").read_bytes() == curves
    assert ctx.summary["f_0V_Hz"] == pytest.approx(90000, rel=1e-4)
    assert yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf-8"))["parameters"] == original_parameters
    assert list((directory / "results").glob("before_*/calibration_evaluation.json"))
    assert set((directory / "results").glob("before_*/curve_fits.json")) == archived_curves


def test_history_without_index_and_raw_version_is_supported_but_not_unknown_identity(rig):
    directory = workflow.run(rig.params)
    (directory / "raw/z_scan_index.npz").unlink()
    path = directory / "experiment_config.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config.pop("raw_format_version")
    path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    assert analysis.analyze(directory)["success"]
    config.pop("experiment_id")
    config.pop("data_type")
    assert "无法识别" in run_format.analysis_block_reason(config)


def test_missing_raw_and_modified_stage_artifacts_cannot_be_reused(rig):
    directory = workflow.run(rig.params)
    ctx = analysis.AnalysisWorkflow(directory)
    with pytest.raises(ValueError, match="缺少前置"):
        ctx.execute(["B03"])
    ctx.execute(["B01", "B02"])
    (directory / "results/curve_fits.json").write_text("[]", encoding="utf-8")
    renewed = analysis.AnalysisWorkflow(directory)
    assert renewed.states["B02"]["status"] == "stale"
    with pytest.raises(ValueError, match="缺少前置"):
        renewed.execute(["B03"])
    (directory / "raw/z_000/frequency_scan.npz").unlink()
    renewed = analysis.AnalysisWorkflow(directory)
    assert renewed.states["B01"]["status"] == "stale"
    with pytest.raises(ValueError, match="无效的原始数据路径"):
        renewed.execute(["B01"])
    assert renewed.states["B01"]["status"] == "failed"


def test_quality_failure_still_writes_diagnostics_and_original_nan_arrays(rig):
    directory = workflow.run(rig.params)
    for path in (directory / "raw").glob("z_*/frequency_scan.npz"):
        with np.load(path) as source:
            payload = {key: source[key] for key in source.files}
        payload["r_mean_v"][:] = np.nan
        np.savez(path, **payload)
    report = analysis.analyze(directory)
    assert not report["success"] and report["rejection_reasons"]
    assert (directory / "results/z_frequency_calibration.png").is_file()
    with np.load(directory / "results/calibration_results.npz") as saved:
        assert not saved["calibration_success"]
        assert np.isnan(saved["K_Z_Hz_per_V"])
    assert analysis.AnalysisWorkflow(directory).snapshot()["summary"]["status"] == "completed"


def test_model_old_wait_migration_and_visible_parameter_coverage():
    old = MxZFieldCalibrationParams.from_external({"FREQUENCY_SETTLE_TIME_S": 0.4})
    explicit = MxZFieldCalibrationParams.from_external({"FREQUENCY_SETTLE_TIME_S": 0.4, "SYSTEM_SETTLE_S": 0.2})
    assert old.system_settle_s == 0.4 and explicit.system_settle_s == 0.2
    assert MxZFieldCalibrationParams.from_external(old.to_external()) == old
    from .definition import ADAPTER, DEFINITION
    from .stages import definition
    fields = {field["name"] for field in ADAPTER.schema()["fields"]}
    visible = {key for column in ("acquisition", "analysis") for stage in definition()[column] for key in stage["display_parameters"]}
    assert fields == visible
    assert DEFINITION.required_mapping_keys == run_format.REQUIRED_MAPPING_KEYS


def test_raw_format_is_independent_of_parameter_schema_and_rejects_invalid_markers():
    config = {"experiment_id": "mx-z-field-calibration", "parameters": {}, "schema_version": 1,
        "raw_format_version": 1, "completion_status": "completed"}
    assert not run_format.analysis_block_reason(config)
    config["schema_version"] = MxZFieldCalibrationParams.schema_version
    assert not run_format.analysis_block_reason(config)
    for marker in (True, "1", 2):
        assert run_format.analysis_block_reason(config | {"raw_format_version": marker})


def test_empty_scan_index_blocks_analysis_before_fitting(rig):
    directory = workflow.run(rig.params)
    np.savez(directory / "raw/z_scan_index.npz", z_bias_v=[], predicted_center_hz=[], summary_file=[])
    ctx = analysis.AnalysisWorkflow(directory)
    with pytest.raises(ValueError, match="没有已完成"):
        ctx.execute(["B01", "B02"])
    assert ctx.states["B01"]["status"] == "failed" and ctx.states["B02"]["status"] == "pending"


def test_finite_lorentzian_centers_use_uncertainty_weights_and_keep_failed_linear_diagnostics(rig):
    directory = workflow.run(rig.params)
    for index, path in enumerate(sorted((directory / "raw").glob("z_*/frequency_scan.npz"))):
        with np.load(path) as source:
            payload = {key: source[key] for key in source.files}
        frequency = payload["frequency_hz"]
        center = 90000 + 1000 * float(payload["z_bias_v"]) + (250 if index == 1 else 0)
        payload["r_mean_v"] = 0.1 + 0.5 * 300**2 / ((frequency - center)**2 + 300**2) + (index + 1) * 0.0005 * np.sin(np.arange(frequency.size))
        np.savez(path, **payload)
    ctx = analysis.AnalysisWorkflow(directory, {"LINEAR_R_SQUARED_MIN": 1.0})
    ctx.execute(["B01", "B02"])
    curves = json.loads((directory / "results/curve_fits.json").read_text(encoding="utf-8"))
    assert all(curve["fit"]["success"] for curve in curves)
    x = np.array([curve["z_bias_v"] for curve in curves])
    centers = np.array([curve["fit"]["parameters"][2] for curve in curves])
    sigma = np.array([curve["fit"]["center_uncertainty_hz"] for curve in curves])
    assert np.all(np.isfinite(sigma) & (sigma > 0)) and np.ptp(sigma) > 0
    design = np.column_stack([x, np.ones_like(x)])
    expected, *_ = np.linalg.lstsq(design / sigma[:, None], centers / sigma, rcond=None)
    ctx.execute(["B03", "B04"])
    report = json.loads((directory / "results/analysis.json").read_text(encoding="utf-8"))
    assert not report["success"] and any("R^2" in value for value in report["rejection_reasons"])
    np.testing.assert_allclose([report["K_Z_Hz_per_V"], report["f_0V_Hz"]], expected, rtol=1e-10)
    assert ctx.states["B04"]["status"] == "completed"
    for name in ("frequency_response_fits.png", "z_frequency_calibration.png", "z_frequency_residuals.png"):
        assert (directory / "results" / name).is_file()
    with np.load(directory / "results/calibration_results.npz") as saved:
        assert saved["curve_accepted"].all() and not saved["calibration_success"]
        np.testing.assert_allclose(saved["fitted_center_hz"], centers)


def test_raw_index_cannot_read_a_sibling_run_and_config_change_invalidates_all_stages(rig):
    directory = workflow.run(rig.params)
    ctx = analysis.AnalysisWorkflow(directory)
    ctx.execute(["B01", "B02"])
    path = directory / "experiment_config.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config["parameters"]["RUN_TAG"] = "changed-history"
    path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    renewed = analysis.AnalysisWorkflow(directory)
    assert renewed.states["B01"]["status"] == "stale" and renewed.states["B02"]["status"] == "stale"
    with pytest.raises(ValueError, match="缺少前置"):
        renewed.execute(["B02"])
    sibling = directory.parent / "sibling"
    sibling.mkdir()
    external = sibling / "frequency_scan.npz"
    external.write_bytes((directory / "raw/z_000/frequency_scan.npz").read_bytes())
    previous = external.read_bytes()
    np.savez(directory / "raw/z_scan_index.npz", z_bias_v=[-1], predicted_center_hz=[89000],
        summary_file=["../sibling/frequency_scan.npz"])
    with pytest.raises(ValueError, match="无效的原始数据路径"):
        analysis.AnalysisWorkflow(directory).execute(["B01", "B02"])
    assert external.read_bytes() == previous and not (sibling / "results").exists()
