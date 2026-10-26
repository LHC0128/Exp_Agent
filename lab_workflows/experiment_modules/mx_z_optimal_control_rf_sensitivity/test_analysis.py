"""明确临时 run_dir 的科学链、历史资格与分阶段缓存回归。"""
from dataclasses import replace
from pathlib import Path
import json
import shutil
import os
import subprocess
import numpy as np
import pytest
import yaml
from scipy.signal import welch
from . import workflow, analysis, analysis_steps, run_format
from .test_workflow import copy_config, install_rig, parameters


REGRESSION_CASES = {
    "mx-z-optimal-control-rf-sensitivity": {
        "analysis": {
            "sources": [
                "lab_workflows/experiment_modules/mx_z_optimal_control_rf_sensitivity/__init__.py",
                "lab_workflows/experiment_modules/mx_z_optimal_control_rf_sensitivity/analysis.py"
            ],
            "tests": [
                "test_full_and_staged_science_and_independent_welch",
                "test_archive_content_fingerprint_and_bad_input_preserve_products",
                "test_quality_failed_keeps_measured_plot_and_blocks_successor",
                "test_flat_residual_is_diagnostic_completion",
                "test_schema_and_raw_qualification",
                "test_identity_unsealed_options_and_subprocess_entry"
            ]
        }
    }
}


@pytest.fixture
def run_dir(tmp_path, monkeypatch):
    copy_config(tmp_path)
    rig = install_rig(tmp_path, monkeypatch)
    return workflow.run(rig.params)


def test_full_and_staged_science_and_independent_welch(run_dir, tmp_path):
    staged = tmp_path / "staged"
    shutil.copytree(run_dir, staged)
    full = analysis.analyze(run_dir)
    ctx = analysis.AnalysisWorkflow(staged)
    for key in ("B01", "B02", "B03"):
        ctx.execute([key], ctx.revision)
    assert full["status"] == ctx.summary["status"] == "completed"
    assert ctx.options == {}
    for name in ("response_fit.npz", "noise_psd.npz", "sensitivity.npz", "sensitivity_zero_point.npz"):
        if not (run_dir / "results" / name).is_file():
            continue
        with np.load(run_dir / "results" / name) as left, np.load(staged / "results" / name) as right:
            for key in left.files:
                if left[key].dtype.kind in "biufc":
                    np.testing.assert_allclose(left[key], right[key], rtol=1e-12, atol=1e-12, equal_nan=True)
                else:
                    np.testing.assert_array_equal(left[key], right[key])
    with np.load(run_dir / "raw/noise_000.npz") as raw:
        rate = float(raw["actual_rate_sa_s"])
        frequency, psd = welch(raw["r_v"], fs=rate, nperseg=raw["r_v"].size, detrend="constant", scaling="density")
    value = analysis_steps._decode(json.loads((run_dir / "results/evaluation.json").read_text(encoding="utf-8")))
    np.testing.assert_allclose(value["frequency_hz"], frequency)
    np.testing.assert_allclose(value["psd_r"], psd, rtol=1e-12, atol=1e-20)
    sensitivity = value["sensitivity"]
    raw_equivalent = np.sqrt(psd) / abs(value["primary_slope"])
    np.testing.assert_allclose(sensitivity["raw_vpp_per_sqrt_hz"], raw_equivalent, rtol=1e-12)
    correction = np.sqrt(1 + (frequency / value["hwhm_hz"]) ** 2)
    np.testing.assert_allclose(sensitivity["corrected_vpp_per_sqrt_hz"], raw_equivalent * correction, rtol=1e-12)
    np.testing.assert_allclose(sensitivity["corrected_ft_per_sqrt_hz"], raw_equivalent * correction * 1517.79147 * 1e6, rtol=1e-12)
    assert (run_dir / "results/phase_calibration.png").is_file()


def test_archive_content_fingerprint_and_bad_input_preserve_products(run_dir):
    analysis.analyze(run_dir)
    result = run_dir / "results/response_fit.npz"
    before = result.read_bytes()
    raw = run_dir / "raw/noise_000.npz"
    raw_bytes, stat = raw.read_bytes(), raw.stat()
    # 同大小同 mtime 的内容替换不能继续复用旧科学前置。
    raw.write_bytes(raw_bytes[:-1] + bytes([raw_bytes[-1] ^ 1]))
    os.utime(raw, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    ctx = analysis.AnalysisWorkflow(run_dir)
    assert ctx.states["B01"]["status"] == "stale"
    with pytest.raises(ValueError, match="缺少前置"):
        ctx.execute(["B03"])
    raw.write_bytes(raw_bytes)
    ctx = analysis.AnalysisWorkflow(run_dir)
    raw.unlink()
    with pytest.raises(FileNotFoundError):
        ctx.execute(["B01"])
    assert result.read_bytes() == before
    raw.write_bytes(raw_bytes)
    ctx = analysis.AnalysisWorkflow(run_dir)
    ctx.execute(["B01", "B02", "B03"])
    assert list((run_dir / "results").glob("before_*"))
    assert (run_dir / "results/phase_calibration.yaml").is_file()


def test_quality_failed_keeps_measured_plot_and_blocks_successor(run_dir):
    raw = run_dir / "raw/amplitude_scan.npz"
    with np.load(raw) as data:
        values = {key: data[key] for key in data.files}
    values["r_std_v"] = np.full_like(values["r_std_v"], 999.)
    np.savez(raw, **values)
    ctx = analysis.AnalysisWorkflow(run_dir)
    ctx.execute(["B01"])
    with pytest.raises(analysis_steps.AnalysisQualityError):
        ctx.execute(["B02", "B03"])
    assert ctx.states["B02"]["status"] == "quality_failed"
    assert ctx.states["B03"]["status"] != "completed"
    assert (run_dir / "results/amplitude_response.png").is_file()
    assert (run_dir / "results/evaluation_diagnostics.yaml").is_file()


def test_flat_residual_is_diagnostic_completion(tmp_path, monkeypatch):
    copy_config(tmp_path)
    rig = install_rig(tmp_path, monkeypatch, parameters(phase_cal_rf_amplitude_vpp=0.))
    directory = workflow.run(rig.params)
    result = analysis.analyze(directory)
    assert result["status"] == "completed"
    assert result["measurement_mode"] == "residual_control_phase_scan"
    assert result["scan_completed"] is True
    assert result["fit_accepted"] is False
    assert not (directory / "results/sensitivity.npz").exists()


@pytest.mark.parametrize("schema,raw,allowed", [(5, None, True), (6, None, True), (4, None, False), (6, 1, True), (6, 2, False)])
def test_schema_and_raw_qualification(run_dir, schema, raw, allowed):
    path = run_dir / "experiment_config.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config["schema_version"] = schema
    if raw is None:
        config.pop("raw_format_version", None)
    else:
        config["raw_format_version"] = raw
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    before = {p.name: p.read_bytes() for p in (run_dir / "results").glob("*") if p.is_file()}
    if allowed:
        analysis.AnalysisWorkflow(run_dir).execute(["B01"])
    else:
        with pytest.raises(ValueError):
            analysis.AnalysisWorkflow(run_dir)
        assert before == {p.name: p.read_bytes() for p in (run_dir / "results").glob("*") if p.is_file()}


def test_identity_unsealed_options_and_subprocess_entry(run_dir):
    config_path = run_dir / "experiment_config.yaml"
    original = config_path.read_bytes()
    config = yaml.safe_load(original)
    for changes in ({"experiment_id": "other"}, {"completion_status": "running"}):
        config_path.write_text(yaml.safe_dump(config | changes), encoding="utf-8")
        with pytest.raises(ValueError):
            analysis.AnalysisWorkflow(run_dir)
    config_path.write_bytes(original)
    with pytest.raises(ValueError, match="覆盖"):
        analysis.AnalysisWorkflow(run_dir, {"LOW_FREQ_SKIP_HZ": 9})
    env = os.environ | {"LAB_RUN_DIR": str(run_dir), "LAB_TYPED_PARAMETERS": "{}", "MPLBACKEND": "Agg", "PYTHONIOENCODING": "utf-8"}
    import sys
    result = subprocess.run([sys.executable, "-m", "lab_workflows.experiment_modules.mx_z_optimal_control_rf_sensitivity.analysis"],
        env=env, capture_output=True, timeout=60)
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    assert (run_dir / "results/analysis.json").is_file()
    assert (run_dir / "results/sensitivity.npz").is_file()
