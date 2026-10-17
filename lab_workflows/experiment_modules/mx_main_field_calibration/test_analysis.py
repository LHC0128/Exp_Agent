"""真实历史原始文件上的整次、分阶段分析与归档回归。"""
import json
import numpy as np
import pytest
import yaml
from . import analysis, workflow, run_format
from .models import MxMainFieldCalibrationParams
from .test_workflow import rig


def test_complete_and_staged_analysis_share_calibration_and_archive_only_dependents(rig):
    directory = workflow.run(rig.params)
    original_parameters = yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf-8"))["parameters"]
    full = analysis.analyze(directory)
    assert full["success"]
    assert full["K_f_Hz_per_mA"] == pytest.approx(1000, rel=1e-4)
    with np.load(directory / "results/calibration_results.npz") as saved:
        original_arrays = {key: saved[key].copy() for key in saved.files}
    ctx = analysis.AnalysisWorkflow(directory)
    assert ctx.states["B04"]["status"] == "completed"
    ctx.execute(["B01", "B02", "B03", "B04"])
    split = json.loads((directory / "results/analysis.json").read_text(encoding="utf-8"))
    with np.load(directory / "results/calibration_results.npz") as saved:
        assert set(saved.files) == set(original_arrays)
        for key, values in original_arrays.items():
            np.testing.assert_allclose(saved[key], values, rtol=0, atol=0, equal_nan=True, err_msg=key)
    assert split["K_B_nT_per_mA"] == full["K_B_nT_per_mA"]
    curves = (directory / "results/curve_fits.json").read_bytes()
    archived_curves = set((directory / "results").glob("before_*/curve_fits.json"))
    ctx = analysis.AnalysisWorkflow(directory, {"GYROMAGNETIC_RATIO_HZ_PER_NT": 5})
    assert ctx.states["B02"]["status"] == "completed"
    assert ctx.states["B03"]["status"] == "stale"
    ctx.execute(["B03", "B04"])
    assert (directory / "results/curve_fits.json").read_bytes() == curves
    assert ctx.summary["K_B_nT_per_mA"] == pytest.approx(200, rel=1e-4)
    assert yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf-8"))["parameters"] == original_parameters
    assert list((directory / "results").glob("before_*/calibration_evaluation.json"))
    assert set((directory / "results").glob("before_*/curve_fits.json")) == archived_curves


def test_history_without_index_and_raw_version_is_supported_but_not_unknown_identity(rig):
    directory = workflow.run(rig.params)
    (directory / "raw/main_field_scan_index.npz").unlink()
    for summary in (directory / "raw").glob("current_*/frequency_scan.npz"):
        with np.load(summary) as source:
            payload = {key: source[key] for key in source.files}
        payload["r_scalar_mean_v"] = payload.pop("r_mean_v")
        np.savez(summary, **payload)
    path = directory / "experiment_config.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config.pop("raw_format_version")
    path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    assert analysis.analyze(directory)["success"]
    assert "raw_format_version" not in yaml.safe_load(path.read_text(encoding="utf-8"))
    with np.load(directory / "raw/current_000/frequency_scan.npz") as saved:
        assert "r_scalar_mean_v" in saved and "r_mean_v" not in saved
    config["raw_format_version"] = 99
    assert "不支持" in run_format.analysis_block_reason(config)
    config.pop("raw_format_version")
    config.pop("experiment_id")
    assert run_format.analysis_block_reason(config) == ""
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
    (directory / "raw/current_000/frequency_scan.npz").unlink()
    renewed = analysis.AnalysisWorkflow(directory)
    assert renewed.states["B01"]["status"] == "stale"
    with pytest.raises(ValueError, match="无效的原始数据路径"):
        renewed.execute(["B01"])
    assert renewed.states["B01"]["status"] == "failed"


def test_quality_failure_still_writes_diagnostics_and_original_nan_arrays(rig):
    directory = workflow.run(rig.params)
    for path in (directory / "raw").glob("current_*/frequency_scan.npz"):
        with np.load(path) as source:
            payload = {key: source[key] for key in source.files}
        payload["r_mean_v"][:] = np.nan
        np.savez(path, **payload)
    report = analysis.analyze(directory)
    assert not report["success"] and report["rejection_reasons"]
    assert (directory / "results/main_field_frequency_calibration.png").is_file()
    with np.load(directory / "results/calibration_results.npz") as saved:
        assert not saved["calibration_success"]
        assert np.isnan(saved["K_f_Hz_per_mA"])
    assert analysis.AnalysisWorkflow(directory).snapshot()["summary"]["status"] == "completed"


def test_model_old_wait_migration_and_visible_parameter_coverage():
    old = MxMainFieldCalibrationParams.from_external({"SETTLE_TIME_S": 0.4})
    explicit = MxMainFieldCalibrationParams.from_external({"SETTLE_TIME_S": 0.4, "SYSTEM_SETTLE_S": 0.2})
    assert old.system_settle_s == 0.4 and explicit.system_settle_s == 0.2
    assert MxMainFieldCalibrationParams.from_external(old.to_external()) == old
    from .definition import ADAPTER, DEFINITION
    from .stages import definition
    fields = {field["name"] for field in ADAPTER.schema()["fields"]}
    visible = {key for column in ("acquisition", "analysis") for stage in definition()[column] for key in stage["display_parameters"]}
    assert fields == visible
    assert DEFINITION.required_mapping_keys == run_format.REQUIRED_MAPPING_KEYS


def test_raw_and_config_fingerprints_force_revalidation_and_keep_old_exports(rig):
    from . import analysis_steps
    directory = workflow.run(rig.params)
    analysis.analyze(directory)
    for name in ("_builtin", "_load_params", "_load_scan_index", "_discrete_peak_frequency",
                 "_analyze_curve", "_failed_linear_fit", "_weighted_linear_fit"):
        assert getattr(analysis, name) is getattr(analysis_steps, name)
    path = directory / "raw/current_000/frequency_scan.npz"
    with np.load(path) as source:
        payload = {key: source[key] for key in source.files}
    payload["r_mean_v"] = payload["r_mean_v"] + 0.001
    np.savez(path, **payload)
    ctx = analysis.AnalysisWorkflow(directory)
    assert all(ctx.states[item]["status"] == "stale" for item in ("B01", "B02", "B03", "B04"))
    ctx.execute(["B01", "B02", "B03", "B04"])
    config_path = directory / "experiment_config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["parameters"]["GYROMAGNETIC_RATIO_HZ_PER_NT"] = 6
    config_path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    renewed = analysis.AnalysisWorkflow(directory)
    assert renewed.states["B01"]["status"] == "stale"


@pytest.mark.parametrize(("override", "error"), [({"GYROMAGNETIC_RATIO_HZ_PER_NT": 0}, ValueError),
    ({"GYROMAGNETIC_RATIO_HZ_PER_NT": float("nan")}, ValueError),
    ({"LINEAR_R_SQUARED_MIN": True}, TypeError), ({"RUN_TAG": "wrong"}, ValueError)])
def test_invalid_analysis_overrides_do_not_rewrite_history(rig, override, error):
    directory = workflow.run(rig.params)
    original = (directory / "experiment_config.yaml").read_bytes()
    with pytest.raises(error):
        analysis.AnalysisWorkflow(directory, override)
    assert (directory / "experiment_config.yaml").read_bytes() == original
