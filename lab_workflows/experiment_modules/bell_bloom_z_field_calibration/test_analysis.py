"""真实历史原始文件上的整次、分阶段分析与归档回归。"""
import json
import numpy as np
import pytest
import yaml
from . import analysis, workflow, run_format
from .models import BellBloomZFieldCalibrationParams
from .test_workflow import rig


def test_complete_and_staged_analysis_share_calibration_and_archive_only_dependents(rig):
    directory = workflow.run(rig.params)
    original_parameters = yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf-8"))["parameters"]
    full = analysis.analyze(directory)
    assert full["success"]
    assert full["K_Z_Hz_per_V"] == pytest.approx(1000, rel=1e-4)
    ctx = analysis.AnalysisWorkflow(directory)
    assert ctx.states["B04"]["status"] == "completed"
    ctx.execute(["B01", "B02", "B03", "B04"])
    split = json.loads((directory / "results/analysis.json").read_text(encoding="utf-8"))
    assert split["f_0V_Hz"] == full["f_0V_Hz"]
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
    assert "不是 Bell Bloom" in run_format.analysis_block_reason(config)


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
    old = BellBloomZFieldCalibrationParams.from_external({"FREQUENCY_SETTLE_TIME_S": 0.4})
    explicit = BellBloomZFieldCalibrationParams.from_external({"FREQUENCY_SETTLE_TIME_S": 0.4, "SYSTEM_SETTLE_S": 0.2})
    assert old.system_settle_s == 0.4 and explicit.system_settle_s == 0.2
    assert BellBloomZFieldCalibrationParams.from_external(old.to_external()) == old
    from .definition import ADAPTER, DEFINITION
    from .stages import definition
    fields = {field["name"] for field in ADAPTER.schema()["fields"]}
    visible = {key for column in ("acquisition", "analysis") for stage in definition()[column] for key in stage["display_parameters"]}
    assert fields == visible
    assert DEFINITION.required_mapping_keys == run_format.REQUIRED_MAPPING_KEYS


def test_raw_format_is_independent_of_parameter_schema_and_rejects_invalid_markers():
    config = {"experiment_id": "bell-bloom-z-field-calibration", "parameters": {}, "schema_version": 1,
        "raw_format_version": 1, "completion_status": "completed"}
    assert not run_format.analysis_block_reason(config)
    config["schema_version"] = BellBloomZFieldCalibrationParams.schema_version
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


def test_content_fingerprints_detect_same_size_and_timestamp_changes(rig):
    import os
    directory = workflow.run(rig.params)
    analysis.analyze(directory)
    path = directory / "results/curve_fits.json"
    original = path.stat()
    data = path.read_bytes()
    assert b"89000" in data
    path.write_bytes(data.replace(b"89000", b"89001", 1))
    os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))
    assert path.stat().st_size == original.st_size
    assert analysis.AnalysisWorkflow(directory).states["B02"]["status"] == "stale"
    raw = directory / "raw/z_000/frequency_scan.npz"
    original = raw.stat()
    with np.load(raw) as source:
        payload = {key: source[key] for key in source.files}
    payload["r_mean_v"][0] += 0.0001
    np.savez(raw, **payload)
    os.utime(raw, ns=(original.st_atime_ns, original.st_mtime_ns))
    assert raw.stat().st_size == original.st_size
    ctx = analysis.AnalysisWorkflow(directory)
    assert ctx.states["B01"]["status"] == "stale"
    with pytest.raises(ValueError, match="缺少前置"):
        ctx.execute(["B02"])


def test_stage_records_keep_real_inputs_options_revision_and_artifacts(rig):
    directory = workflow.run(rig.params)
    ctx = analysis.AnalysisWorkflow(directory, {"LINEAR_R_SQUARED_MIN": 0.95})
    ctx.execute(["B01", "B02", "B03", "B04"])
    record = ctx.snapshot()["source_signature"]["B03"]
    assert record["analysis_parameters"] == {"LINEAR_R_SQUARED_MIN": 0.95}
    assert record["revision"] == ctx.revision
    import hashlib
    assert record["input_fingerprint"] == hashlib.sha256(json.dumps(ctx.fingerprint, sort_keys=True).encode()).hexdigest()
    assert len(record["artifacts"]["calibration_evaluation.json"]) == 33


def test_signed_calibration_uncertainty_and_quality_override_use_real_numeric_chain(rig):
    directory = workflow.run(rig.params)
    config_path = directory / "experiment_config.yaml"
    original_parameters = yaml.safe_load(config_path.read_text(encoding="utf-8"))["parameters"]
    for path in (directory / "raw").glob("z_*/frequency_scan.npz"):
        with np.load(path) as source:
            payload = {key: source[key] for key in source.files}
        z = float(payload["z_bias_v"])
        center = 90000 - 1000 * z + 40 * z**2
        frequency = np.linspace(center - 1000, center + 1000, 17)
        payload.update(frequency_hz=frequency,
            r_mean_v=0.1 + 0.5 * 300**2 / ((frequency - center)**2 + 300**2),
            r_std_v=np.full(frequency.size, 1e-5))
        np.savez(path, **payload)
    ctx = analysis.AnalysisWorkflow(directory, {"LINEAR_R_SQUARED_MIN": 0.9999})
    ctx.execute(["B01", "B02", "B03", "B04"])
    report = json.loads((directory / "results/analysis.json").read_text(encoding="utf-8"))
    assert not report["success"] and report["rejection_reasons"]
    assert report["K_Z_Hz_per_V"] == pytest.approx(-1000, rel=1e-4)
    assert report["f_0V_Hz"] == pytest.approx(90000 + 80 / 3, abs=1)
    assert np.isfinite(report["linear_fit"]["uncertainties"]).all()
    assert report["linear_fit"]["uncertainties"][0] > 0
    curves = (directory / "results/curve_fits.json").read_bytes()
    relaxed = analysis.AnalysisWorkflow(directory, {"LINEAR_R_SQUARED_MIN": 0.9})
    relaxed.execute(["B03", "B04"])
    report = json.loads((directory / "results/analysis.json").read_text(encoding="utf-8"))
    assert report["success"] and report["K_Z_Hz_per_V"] < 0
    assert (directory / "results/curve_fits.json").read_bytes() == curves
    assert yaml.safe_load(config_path.read_text(encoding="utf-8"))["parameters"] == original_parameters
    assert len(list((directory / "results").glob("*.png"))) == 3
