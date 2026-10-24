"""显式临时目录中的原算法对照、格式资格、内容指纹和真实 CLI 回归。"""
from dataclasses import replace
from pathlib import Path
import json
import os
import subprocess
import sys
import numpy as np
import pytest
import yaml
from . import analysis, analysis_steps as steps, analysis_core, run_format
from .models import NoiseSpectrumXYDemod3RParams


def synthetic_run(path, *, schema=2, raw_version=1):
    path = Path(path)
    (path / "raw").mkdir(parents=True)
    (path / "results").mkdir()
    control = np.linspace(200, 2000, 61)
    envelope = (control + 100) / 1000
    frequencies = np.linspace(400, 1600, 9)
    matrix = np.column_stack([analysis_core.lorentzian_r_response(control, 80, 10000 + i * 100, 0.02, 0, f)
                              for i, f in enumerate(frequencies)])
    params = replace(NoiseSpectrumXYDemod3RParams(), control_freq_start_hz=200, control_freq_stop_hz=2000,
        control_freq_points=len(control), demod3_freq_start_hz=400, demod3_freq_stop_hz=1600,
        demod3_freq_points=len(frequencies), temp_recovery_batch_points=4, xy_ctrl_k_hz_per_v=1000, xy_ctrl_b_hz=-100)
    config = dict(experiment_id=run_format.EXPERIMENT_ID, experiment_type=run_format.DATA_TYPE,
        schema_version=schema, parameters=params.to_external(), completion_status="completed", run_tag="science")
    if raw_version is not None:
        config["raw_format_version"] = raw_version
    (path / "experiment_config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    np.savez(path / "raw" / run_format.RAW_FILE, r_mean_V=matrix, control_frequency_Hz=control,
             xy_envelope_voltage_V=envelope, demod3_frequency_Hz=frequencies)
    return path


def no_render(monkeypatch):
    """数值测试只省略渲染；完整出图由真实子进程测试另行覆盖。"""
    def render(run, data):
        for name in analysis.PLOTS:
            for suffix in ("png", "pdf"):
                (run / "results" / f"{name}.{suffix}").write_bytes(b"numerical-test")
    monkeypatch.setattr(analysis.analysis_plotting, "render", render)


def test_full_and_stepwise_original_scientific_results_and_readonly_config(tmp_path, monkeypatch):
    no_render(monkeypatch)
    full, split = synthetic_run(tmp_path / "full"), synthetic_run(tmp_path / "split")
    config = (full / "experiment_config.yaml").read_bytes()
    result = analysis.analyze(full)
    assert result["status"] == "completed" and result["fitted_frequency_points"] == 9
    ctx = analysis.AnalysisWorkflow(split)
    with pytest.raises(ValueError, match="缺少前置"):
        ctx.execute(["B02"])
    for item in ("B01", "B02", "B03"):
        ctx = analysis.AnalysisWorkflow(split)
        ctx.execute([item], ctx.revision)
    for name in ("demod3_r_matrix.npz", "calibration.npz", "r_fit_params.npz", "r_spectra.npz"):
        with np.load(full / "results" / name) as left, np.load(split / "results" / name) as right:
            assert left.files == right.files
            for key in left.files:
                np.testing.assert_equal(left[key], right[key])
    data = steps.load_data(full)
    expected = analysis_core.analyze_r_matrix(data["matrix"], data["envelope"], data["frequencies"])
    with np.load(full / "results/r_fit_params.npz") as fit:
        np.testing.assert_allclose(fit["parameters"], expected.fit_parameters, rtol=1e-12, atol=1e-15)
        np.testing.assert_array_equal(fit["fit_mask"], expected.fit_mask)
    assert "S_beta" not in result and "N_S1" not in result
    assert (full / "experiment_config.yaml").read_bytes() == config
    assert json.loads((full / "results/analysis.json").read_text())["units_note"].startswith("R is voltage")


def test_content_fingerprint_and_missing_artifact_invalidate(tmp_path, monkeypatch):
    no_render(monkeypatch)
    run = synthetic_run(tmp_path / "fingerprint")
    analysis.analyze(run)
    file = run / "results/r_fit_params.npz"
    original = file.stat()
    content = bytearray(file.read_bytes()); content[-10] ^= 1
    file.write_bytes(content)
    os.utime(file, ns=(original.st_atime_ns, original.st_mtime_ns))
    ctx = analysis.AnalysisWorkflow(run)
    assert ctx.states["B01"]["status"] == "completed" and ctx.states["B02"]["status"] == "stale"
    with pytest.raises(ValueError, match="修订"):
        ctx.execute(["B02"], ctx.revision - 1)
    analysis.analyze(run)
    file = run / "raw" / run_format.RAW_FILE
    original = file.stat()
    with np.load(file) as source:
        data = {key: source[key] for key in source.files}
    data["r_mean_V"][0, 0] += 0.01
    np.savez(file, **data)
    os.utime(file, ns=(original.st_atime_ns, original.st_mtime_ns))
    ctx = analysis.AnalysisWorkflow(run)
    assert ctx.states["B01"]["status"] == "stale" and ctx.summary["status"] == "stale"


def test_rerun_archives_only_affected_outputs(tmp_path, monkeypatch):
    no_render(monkeypatch)
    run = synthetic_run(tmp_path / "archive")
    analysis.analyze(run)
    inputs = (run / "results/demod3_r_matrix.npz").read_bytes()
    ctx = analysis.AnalysisWorkflow(run)
    ctx.execute(["B02"])
    backup = run / "results" / ctx.summary["backup_directory"]
    assert (backup / "r_fit_params.npz").exists() and (backup / "analysis.json").exists()
    assert not (backup / "demod3_r_matrix.npz").exists()
    assert (run / "results/demod3_r_matrix.npz").read_bytes() == inputs
    assert ctx.states["B03"]["status"] == "stale"


def test_bad_raw_preserves_previous_outputs_and_reports_quality(tmp_path, monkeypatch):
    no_render(monkeypatch)
    run = synthetic_run(tmp_path / "bad")
    analysis.analyze(run)
    old = {p.name:p.read_bytes() for p in (run / "results").iterdir() if p.is_file()}
    np.savez(run / "raw" / run_format.RAW_FILE, r_mean_V=np.ones((1, 1)),
        control_frequency_Hz=[1, 2], xy_envelope_voltage_V=[1, 2], demod3_frequency_Hz=[1, 2])
    with pytest.raises(ValueError, match="形状"):
        analysis.analyze(run)
    assert not list((run / "results").glob("before_*"))
    assert {name:(run / "results" / name).read_bytes() for name in old} == old
    state = json.loads((run / "analysis_state.json").read_text(encoding="utf-8"))
    assert state["summary"]["status"] == "quality_failed"


@pytest.mark.parametrize("invalid", [np.nan, np.inf])
def test_invalid_columns_preserve_masks_not_interpolation(tmp_path, monkeypatch, invalid):
    no_render(monkeypatch)
    run = synthetic_run(tmp_path / "partial")
    file = run / "raw" / run_format.RAW_FILE
    with np.load(file) as source:
        data = {key: source[key] for key in source.files}
    data["r_mean_V"][:, 0] = invalid
    np.savez(file, **data)
    result = analysis.analyze(run)
    assert result["fitted_frequency_points"] == 8
    with np.load(run / "results/r_fit_params.npz") as fit:
        assert not fit["fit_mask"][0] and np.isnan(fit["parameters"][0]).all()


def test_no_calibration_inliers_blocks_successors_but_keeps_matrix(tmp_path):
    run = synthetic_run(tmp_path / "no-calibration")
    file = run / "raw" / run_format.RAW_FILE
    with np.load(file) as source:
        data = {key: source[key] for key in source.files}
    data["r_mean_V"][:] = np.nan
    np.savez(file, **data)
    ctx = analysis.AnalysisWorkflow(run)
    ctx.execute(["B01"])
    with pytest.raises(ValueError):
        ctx.execute(["B02"])
    assert ctx.states["B02"]["status"] == "quality_failed"
    assert (run / "results/demod3_r_matrix.npz").exists()
    with pytest.raises(ValueError, match="缺少前置"):
        ctx.execute(["B03"])


@pytest.mark.parametrize("schema,raw", [(1, None), (2, None), (1, 1), (2, 1)])
def test_parameter_schema_independent_of_raw_version(tmp_path, schema, raw):
    run = synthetic_run(tmp_path / "format", schema=schema, raw_version=raw)
    config = (run / "experiment_config.yaml").read_bytes()
    assert steps.load_data(run)["matrix"].shape == (61, 9)
    assert (run / "experiment_config.yaml").read_bytes() == config


@pytest.mark.parametrize("key,value", [("experiment_id", "other"), ("experiment_type", "Other"),
    ("raw_format_version", 2), ("raw_format_version", True), ("completion_status", "running")])
def test_bad_identity_format_and_running_are_rejected(tmp_path, key, value):
    run = synthetic_run(tmp_path / "reject")
    path = run / "experiment_config.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8")); config[key] = value
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(ValueError):
        analysis.AnalysisWorkflow(run)
    assert path.read_bytes() == before and not list((run / "results").iterdir())


def test_explicit_recovery_matrix_is_loaded(tmp_path):
    run = synthetic_run(tmp_path / "recovery")
    source = run / "raw" / run_format.RAW_FILE
    source.rename(run / "raw/demod3_r_mean_matrix_recovered.npz")
    assert steps.load_data(run)["matrix"].shape == (61, 9)


def test_results_directory_link_to_raw_is_rejected(tmp_path):
    run = synthetic_run(tmp_path / "link")
    raw = (run / "raw" / run_format.RAW_FILE).read_bytes()
    (run / "results").rmdir()
    # Windows 使用无需开发者模式的目录联接，Unix 使用符号链接。
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(run / "results"), str(run / "raw")], check=True, capture_output=True)
    else:
        (run / "results").symlink_to(run / "raw", target_is_directory=True)
    with pytest.raises(ValueError, match="链接|联接"):
        analysis.AnalysisWorkflow(run)
    assert (run / "raw" / run_format.RAW_FILE).read_bytes() == raw


def test_real_subprocess_full_plots_and_required_run_directory(tmp_path):
    run = synthetic_run(tmp_path / "cli")
    root = Path(__file__).resolve().parents[3]
    env = dict(os.environ, MPLBACKEND="Agg", LAB_RUN_DIR=str(run), LAB_TYPED_PARAMETERS="{}")
    env.pop("LAB_CANCEL_FILE", None)
    result = subprocess.run([sys.executable, "-X", "utf8", "-m", analysis.__name__], cwd=root, env=env,
        capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    for name in analysis.PLOTS:
        assert (run / "results" / (name + ".png")).read_bytes().startswith(b"\x89PNG")
        assert (run / "results" / (name + ".pdf")).read_bytes().startswith(b"%PDF")
    result = subprocess.run([sys.executable, "-X", "utf8", str(root / "experiments/Noise_Spectrum_XY_Demod3_R_plot.py")],
        cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert result.returncode != 0 and "run_dir" in result.stderr


def test_known_raw_does_not_bypass_future_parameter_schema(tmp_path):
    run = synthetic_run(tmp_path / "future", schema=99, raw_version=1)
    config = yaml.safe_load((run / "experiment_config.yaml").read_text(encoding="utf-8"))
    assert run_format.format_block_reason(config) == ""
    with pytest.raises(ValueError, match="schema_version"):
        steps.load_data(run)


def test_analysis_options_rejected_before_writes(tmp_path):
    run = synthetic_run(tmp_path / "options")
    with pytest.raises(ValueError, match="没有分析覆盖"):
        analysis.AnalysisWorkflow(run, {"SYSTEM_SETTLE_S": 1})
    assert not (run / "analysis_state.json").exists()
