"""显式临时 run_dir 的离线科学、历史格式、覆盖值与指纹回归。"""
from dataclasses import replace
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys
import numpy as np
import pytest
import yaml
from . import analysis, analysis_steps, acquisition, run_format
from .models import MxZOptimalControlXYNoiseSpectrumParams
from .stages import ANALYSIS


REGRESSION_CASES = {
    "mx-z-optimal-control-xy-noise-spectrum": {
        "analysis": {
            "sources": [
                "lab_workflows/experiment_modules/mx_z_optimal_control_xy_noise_spectrum/__init__.py",
                "lab_workflows/experiment_modules/mx_z_optimal_control_xy_noise_spectrum/analysis.py"
            ],
            "tests": [
                "test_full_and_stepwise_match_independent_fft_and_actual_rate",
                "test_analysis_override_isolation_readonly_get_and_product_archival",
                "test_content_fingerprint_detects_same_size_same_mtime_changes",
                "test_bad_input_does_not_move_previous_products",
                "test_partial_run_and_verified_legacy_output_directory",
                "test_schema_and_raw_version_are_independent",
                "test_parameter_migration_roundtrip_and_invalid_overrides",
                "test_real_thin_plot_subprocess_has_explicit_temporary_run_dir",
                "test_offline_analysis_uses_raw_without_original_control_sources"
            ]
        }
    }
}


def synthetic_run(root, name="synthetic", *, legacy=False):
    run = root / name
    (run / "raw/points").mkdir(parents=True)
    params = replace(MxZOptimalControlXYNoiseSpectrumParams(), noise_n_avg=2, noise_rate_sa_s=2048.,
        noise_duration_s=.5, x_field_start_v=-.01, x_field_stop_v=.01, x_field_points=2,
        y_field_start_v=-.02, y_field_stop_v=.02, y_field_points=2)
    config = dict(experiment_id=run_format.EXPERIMENT_ID, experiment_type=run_format.DATA_TYPE,
        schema_version=1, parameters=params.to_external(), measurement_mode="xy_bias_noise_spectrum",
        completion_status="completed")
    if legacy:
        config.update(experiment_id="mx-z-optimal-control-xy-rf-sensitivity",
            experiment_type="Mx_Z_Optimal_Control_XY_RF_Sensitivity", measurement_mode="xy_bias_grid_rf_sensitivity")
    (run / "experiment_config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    points = acquisition._grid_points(params)
    acquisition._write_manifest(run / "raw/point_manifest.yaml", acquisition._initial_manifest(params, points))
    manifest = yaml.safe_load((run / "raw/point_manifest.yaml").read_text(encoding="utf-8"))
    for point in points:
        manifest["points"][point["key"]]["status"] = "completed"
        directory = run / "raw/points" / point["key"]
        directory.mkdir()
        t = np.arange(512) / 512.
        scale = 1 + point["sequence_index"]
        for i in range(2):
            wave = .01 + scale * (1 + i) * 1e-5 * np.random.default_rng(i).normal(size=t.size)
            wave += scale * 2e-4 * np.cos(2 * np.pi * 100 * t) + scale * 3e-4 * np.sin(2 * np.pi * 150 * t)
            np.savez(directory / f"noise_{i:03}.npz", r_v=wave, actual_rate_sa_s=512., time_s=t)
    acquisition._write_manifest(run / "raw/point_manifest.yaml", manifest)
    return run, params


def science(path):
    with np.load(path / "results/noise_median_matrix.npz") as data:
        return {key: data[key] for key in data.files}


def test_full_and_stepwise_match_independent_fft_and_actual_rate(tmp_path):
    full, params = synthetic_run(tmp_path)
    staged, _ = synthetic_run(tmp_path, "staged")
    result = analysis.analyze(full)
    ctx = analysis.AnalysisWorkflow(staged)
    for stage in ANALYSIS:
        ctx = analysis.AnalysisWorkflow(staged)
        ctx.execute([stage.id], ctx.revision)
    for key, value in science(full).items():
        np.testing.assert_array_equal(value, science(staged)[key])
    assert result["best_point"]["key"] == "x_000_y_000"
    assert result["narrowband_lines_included"] is True
    assert result["units"] == "V/sqrt(Hz)"
    frequency = np.fft.rfftfreq(512, 1 / 512.)
    window = .5 - .5 * np.cos(2 * np.pi * np.arange(512) / 512)
    psds = []
    for path in sorted((full / "raw/points/x_000_y_000").glob("noise_*.npz")):
        with np.load(path) as raw:
            values = raw["r_v"]
        psd = abs(np.fft.rfft((values - values.mean()) * window)) ** 2 / (512 * (window ** 2).sum())
        psd[1:-1] *= 2
        psds.append(psd)
    asd = np.sqrt(np.mean(psds, axis=0))
    band = (frequency >= params.low_freq_skip_hz) & (frequency <= params.noise_band_max_hz)
    np.testing.assert_allclose(science(full)["best_asd_v_per_sqrt_hz"], asd, rtol=1e-12, atol=1e-18)
    np.testing.assert_allclose(result["best_point"]["noise_median_r_asd_v_per_sqrt_hz"], np.median(asd[band]), rtol=1e-12)
    assert result["best_point"]["noise_peak_frequency_hz"] == 150.


def test_analysis_override_isolation_readonly_get_and_product_archival(tmp_path):
    run, _ = synthetic_run(tmp_path)
    analysis.analyze(run)
    parameters = yaml.safe_load((run / "experiment_config.yaml").read_text(encoding="utf-8"))["parameters"]
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in run.rglob("*") if p.is_file()}
    ctx = analysis.AnalysisWorkflow(run)
    assert ctx.snapshot()["options"] == {"LOW_FREQ_SKIP_HZ": 3., "NOISE_BAND_MAX_HZ": 200.}
    assert before == {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in run.rglob("*") if p.is_file()}
    ctx = analysis.AnalysisWorkflow(run, {"LOW_FREQ_SKIP_HZ": 20., "NOISE_BAND_MAX_HZ": 180.})
    assert ctx.states["B01"]["status"] == "completed" and ctx.states["B02"]["status"] == "stale"
    ctx.execute(["B02", "B03"], ctx.revision)
    assert list((run / "results").glob("before_*/noise_median_matrix.npz"))
    assert analysis.AnalysisWorkflow(run).options["LOW_FREQ_SKIP_HZ"] == 20.
    assert yaml.safe_load((run / "experiment_config.yaml").read_text(encoding="utf-8"))["parameters"] == parameters


@pytest.mark.parametrize("kind", ["raw", "config", "product"])
def test_content_fingerprint_detects_same_size_same_mtime_changes(tmp_path, kind):
    run, _ = synthetic_run(tmp_path)
    analysis.analyze(run)
    path = run / {"raw": "raw/points/x_000_y_000/noise_000.npz", "config": "experiment_config.yaml",
        "product": "results/noise_metrics.json"}[kind]
    old = path.stat()
    if kind == "raw":
        with np.load(path) as data:
            payload = {key: data[key] for key in data.files}
        payload["r_v"][0] += 1e-6
        np.savez(path, **payload)
    elif kind == "config":
        path.write_bytes(path.read_bytes().replace(b"200.0", b"190.0"))
    else:
        path.write_bytes(path.read_bytes().replace(b"200.0", b"190.0"))
    assert path.stat().st_size == old.st_size
    os.utime(path, ns=(old.st_atime_ns, old.st_mtime_ns))
    ctx = analysis.AnalysisWorkflow(run)
    assert ctx.states["B01" if kind != "product" else "B02"]["status"] == "stale"
    with pytest.raises(ValueError, match="缺少前置"):
        ctx.execute(["B03"])


@pytest.mark.parametrize("kind", ["missing", "index", "path", "coordinate", "nyquist", "rate", "frequency_axis"])
def test_bad_input_does_not_move_previous_products(tmp_path, kind):
    run, _ = synthetic_run(tmp_path)
    analysis.analyze(run)
    before = (run / "results/noise_median_matrix.npz").read_bytes()
    path = run / "raw/points/x_000_y_000/noise_000.npz"
    if kind == "missing":
        path.unlink()
    elif kind in ("index", "path", "coordinate"):
        manifest_path = run / "raw/point_manifest.yaml"
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        if kind == "index":
            manifest["points"]["x_000_y_000"]["x_index"] = -1
        elif kind == "path":
            manifest["points"]["../../escape"] = manifest["points"].pop("x_000_y_000")
        else:
            manifest["points"]["x_000_y_000"]["x_field_v"] = 100.
        acquisition._write_manifest(manifest_path, manifest)
    elif kind == "frequency_axis":
        for path in (run / "raw/points/x_000_y_000").glob("noise_*.npz"):
            with np.load(path) as data:
                payload = {key: data[key] for key in data.files}
            payload["r_v"] = payload["r_v"][:256]
            np.savez(path, **payload)
    else:
        with np.load(path) as data:
            payload = {key: data[key] for key in data.files}
        payload["actual_rate_sa_s"] = 300. if kind == "nyquist" else float("nan")
        np.savez(path, **payload)
    with pytest.raises((ValueError, FileNotFoundError)):
        analysis.AnalysisWorkflow(run).execute(["B01", "B02", "B03"])
    assert (run / "results/noise_median_matrix.npz").read_bytes() == before
    assert not list((run / "results").glob("before_*"))
    assert json.loads((run / "analysis_state.json").read_text(encoding="utf-8"))["summary"]["status"] == "failed"


def test_partial_run_and_verified_legacy_output_directory(tmp_path):
    run, _ = synthetic_run(tmp_path)
    manifest_path = run / "raw/point_manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["points"]["x_001_y_000"]["status"] = "cancelled"
    acquisition._write_manifest(manifest_path, manifest)
    (run / "raw/points/x_001_y_000/noise_000.npz").unlink()
    result = analysis.analyze(run)
    assert result["point_count"] == 3 and np.isnan(science(run)["noise_median_matrix"][1, 0])
    legacy, _ = synthetic_run(tmp_path, "legacy", legacy=True)
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in legacy.rglob("*") if p.is_file()}
    output = tmp_path / "legacy-results"
    assert analysis.analyze_legacy_xy_rf_noise_run(legacy, output)["point_count"] == 4
    assert before == {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in legacy.rglob("*") if p.is_file()}
    assert (output / "noise_median_matrix.npz").is_file()
    with pytest.raises(ValueError, match="其他实验"):
        analysis.AnalysisWorkflow(legacy)


@pytest.mark.parametrize("schema,raw,expected", [(1,None,True),(2,None,True),(9,None,False),(9,1,True),(2,2,False)])
def test_schema_and_raw_version_are_independent(tmp_path, schema, raw, expected):
    run, _ = synthetic_run(tmp_path)
    config = yaml.safe_load((run / "experiment_config.yaml").read_text(encoding="utf-8"))
    config["schema_version"] = schema
    if raw is not None:
        config["raw_format_version"] = raw
    assert (not run_format.analysis_block_reason(config)) is expected


def test_parameter_migration_roundtrip_and_invalid_overrides(tmp_path):
    p = MxZOptimalControlXYNoiseSpectrumParams.from_external({"TEMP_SWITCH_OFF_LEAD_S": .8, "SYSTEM_SETTLE_S": .3})
    assert p.system_settle_s == .3
    assert MxZOptimalControlXYNoiseSpectrumParams.from_external({"TEMP_SWITCH_OFF_LEAD_S": .8}).system_settle_s == .8
    assert MxZOptimalControlXYNoiseSpectrumParams.from_external(p.to_external()).to_external() == p.to_external()
    run, _ = synthetic_run(tmp_path)
    for values in ({"LOW_FREQ_SKIP_HZ": float("nan")}, {"LOW_FREQ_SKIP_HZ": True}, {"other": 1},
            {"LOW_FREQ_SKIP_HZ": 250., "NOISE_BAND_MAX_HZ": 200.}):
        with pytest.raises((ValueError, TypeError)):
            analysis.AnalysisWorkflow(run, values)


def test_real_thin_plot_subprocess_has_explicit_temporary_run_dir(tmp_path):
    run, _ = synthetic_run(tmp_path)
    env = os.environ | {"LAB_RUN_DIR": str(run), "MPLBACKEND": "Agg", "PYTHONIOENCODING": "utf-8"}
    root = Path(__file__).resolve().parents[3]
    result = subprocess.run([sys.executable, "experiments/Mx_Z_Optimal_Control_XY_Noise_Spectrum_plot.py", str(run)],
        cwd=root, env=env, capture_output=True, text=True, encoding="utf-8", timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (run / "results/analysis.json").is_file()
    assert science(run)["noise_median_matrix"].shape == (2, 2)
    result = subprocess.run([sys.executable, "experiments/Mx_Z_Optimal_Control_XY_Noise_Spectrum_plot.py"],
        cwd=root, env=env, capture_output=True, text=True, encoding="utf-8", timeout=15)
    assert result.returncode == 2 and "run_dir" in result.stderr


def test_offline_analysis_uses_raw_without_original_control_sources(tmp_path, monkeypatch):
    from .. import mx_z_control_params
    run, _ = synthetic_run(tmp_path)
    def unexpected(*args, **kwargs):
        raise AssertionError("离线分析不应重载采集时的控制来源")
    monkeypatch.setattr(mx_z_control_params, "load_corrected_control_waveform", unexpected)
    monkeypatch.setattr(mx_z_control_params, "load_theory_control", unexpected)
    assert analysis.analyze(run)["point_count"] == 4
