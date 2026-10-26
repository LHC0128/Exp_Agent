"""独立目录、原 Welch/洛伦兹科学结果、格式兼容及真实离线入口回归。"""
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
import numpy as np
import pytest
import yaml
from scipy import signal
from sds_acquisition import AcquisitionResult, save_to_npz, load_npz
from . import analysis, analysis_steps as steps, stages
from .models import ProjectionNoiseParams
from .test_workflow import small_params


REGRESSION_CASES = {
    "projection-noise": {
        "analysis": {
            "sources": [
                "lab_workflows/experiment_modules/projection_noise/__init__.py",
                "lab_workflows/experiment_modules/projection_noise/analysis.py"
            ],
            "tests": [
                "test_complete_and_stepwise_preserve_original_welch",
                "test_free_lorentzian_fit_keeps_parameter_and_t2_definitions",
                "test_verified_historical_versions_and_raw_version_are_independent",
                "test_unknown_identity_format_and_writing_run_refuse_without_side_effects",
                "test_readonly_snapshot_and_independent_directory_overrides",
                "test_archive_second_stage_keeps_first_input_and_detects_stale_products",
                "test_nested_raw_content_change_with_same_size_mtime_invalidates",
                "test_bad_input_does_not_archive_existing_science",
                "test_failed_fit_exports_diagnostics_instead_of_theoretical_fallback",
                "test_legacy_hf2_uses_original_psd_branch_not_rf_lorentzian",
                "test_junction_results_cannot_archive_raw",
                "test_failed_fit_plot_uses_english_annotations_and_real_png_pdf",
                "test_actual_thin_cli_requires_directory_and_creates_png_pdf"
            ]
        }
    }
}


def synthetic_run(root, *, control=False, schema=6, marked=True):
    root.mkdir(parents=True, exist_ok=True)
    (root / "raw").mkdir(exist_ok=True)
    (root / "results").mkdir(exist_ok=True)
    params = replace(small_params(), measure_field_off_control=control)
    values = params.to_external()
    if schema < 6:
        values.pop("SYSTEM_SETTLE_S")
        values["TEMP_SWITCH_OFF_SETTLE_S"] = 0.001
    config = dict(experiment_id="projection-noise", experiment_type="Projection_noise", schema_version=schema,
        acquisition_backend="sds_pd_raw", completion_status="completed", run_tag="synthetic", timestamp="1024_test",
        parameters=values, main_field_calibration=dict(predicted_larmor_frequency_hz=1000.0))
    if marked:
        config["raw_format_version"] = 1
    (root / "experiment_config.yaml").write_text(yaml.safe_dump(config), encoding="utf8")
    for phase in (["field_off", "field_on"] if control else ["field_on"]):
        folder = root / "raw" / phase
        folder.mkdir(exist_ok=True)
        for index in range(params.acq_repeats):
            rng = np.random.default_rng(index + (100 if phase == "field_on" else 0))
            t = np.arange(params.scope_requested_points) / params.scope_sample_rate_sa_s
            v = 0.01 * rng.normal(size=t.size)
            if phase == "field_on":
                v += signal.lfilter([0.012], [1, -1.8 * np.cos(2 * np.pi * 1000 / params.scope_sample_rate_sa_s), 0.81], rng.normal(size=t.size))
            raw = np.rint(v / 1e-5).astype(np.int16)
            result = AcquisitionResult(channel=1, raw_data=raw, time=t, voltage=raw.astype(float) * 1e-5,
                config_snapshot={}, preamble_dict=dict(horiz_interval=1 / params.scope_sample_rate_sa_s,
                    horiz_offset=0.0, point_num=len(t), vertical_gain=1e-5, vertical_offset=0.0, code_per_div=1))
            save_to_npz(str(folder / f"waveform_{index:04d}.npz"), [result],
                dict(timebase_scale=params.scope_duration_s / 10, horizontal_divisions=10), save_mode="raw")
    return root


def legacy_run(root):
    root.mkdir(parents=True)
    (root / "raw").mkdir()
    (root / "results").mkdir()
    config = dict(experiment_type="Projection_noise", schema_version=1, completion_status="completed",
                  parameters={}, psd_params=dict(nperseg=64, integ_fmin=50, integ_fmax=2000))
    (root / "experiment_config.yaml").write_text(yaml.safe_dump(config), encoding="utf8")
    for name, seed in (("waveforms_phase1_light.npz", 10), ("waveforms_thermal.npz", 20)):
        data = np.random.default_rng(seed).normal(size=(2, 1024))
        np.savez(root / "raw" / name, x_waveforms=data, y_waveforms=data * 0.7, actual_rate=8192.0)
    return root


@pytest.fixture
def no_render(monkeypatch):
    monkeypatch.setattr(steps, "_plot_overview", lambda *a, **kw: "scope_psd_overview.png")
    monkeypatch.setattr(steps, "_plot_fit", lambda *a, **kw: "spin_noise_lorentzian_fit.png")


@pytest.mark.parametrize("control", [False, True])
def test_complete_and_stepwise_preserve_original_welch(tmp_path, no_render, control):
    run = synthetic_run(tmp_path / "full", control=control)
    original = (run / "experiment_config.yaml").read_bytes()
    full = analysis.analyze(run)
    with np.load(run / "results/psd_spectra.npz") as data:
        expected = {key: data[key] for key in data.files}
    ctx = analysis.AnalysisWorkflow(run)
    ctx.execute(["B01"]); ctx.execute(["B02"])
    with np.load(run / "results/psd_spectra.npz") as data:
        for key, value in expected.items():
            np.testing.assert_equal(data[key], value)
    psds = []
    for path in sorted((run / "raw/field_on").glob("waveform_*.npz")):
        results, _ = load_npz(str(path))
        result = results[0]
        frequency, psd = signal.welch(result.voltage, fs=1 / np.median(np.diff(result.time)), window="hann",
            nperseg=256, noverlap=128, detrend="constant", scaling="density", return_onesided=True)
        psds.append(psd)
    np.testing.assert_allclose(expected["frequency_hz"], frequency, rtol=0, atol=0)
    np.testing.assert_allclose(expected["field_on_mean_psd_v2_hz"], np.mean(psds, axis=0), rtol=0, atol=0)
    assert full["analysis_mode"] == ("field_on_minus_field_off" if control else "field_on_only")
    assert bool(expected["control_group_measured"]) is control
    assert full["pnl_metrics_emitted"] is False
    assert (run / "experiment_config.yaml").read_bytes() == original


def test_free_lorentzian_fit_keeps_parameter_and_t2_definitions():
    frequency = np.linspace(400, 1600, 601)
    expected = np.array([5e-9, 85, 1010, 2e-9])
    psd = analysis.lorentzian_psd(frequency, *expected)
    fit = analysis.fit_spin_noise_lorentzian(frequency, psd, np.full_like(psd, 1e-9),
        predicted_center_hz=1000, fit_half_width_hz=500)
    assert fit["success"]
    np.testing.assert_allclose(fit["parameters"], expected, rtol=1e-5, atol=1e-12)
    assert fit["fwhm_hz"] == 2 * fit["gamma_hz"]
    assert fit["t2_s"] == 1 / (2 * np.pi * fit["gamma_hz"])
    assert fit["lorentzian_area_v2"] == np.pi * fit["amplitude_v2_hz"] * fit["gamma_hz"]


@pytest.mark.parametrize("schema", [2, 3, 4, 5, 6])
def test_verified_historical_versions_and_raw_version_are_independent(tmp_path, no_render, schema):
    run = synthetic_run(tmp_path / "history", schema=schema, marked=False)
    config = (run / "experiment_config.yaml").read_bytes()
    ctx = analysis.AnalysisWorkflow(run)
    assert ctx.params.system_settle_s == 0.001
    assert ctx.execute(["B01", "B02"])["acquisition_backend"] == "sds_pd_raw"
    assert (run / "experiment_config.yaml").read_bytes() == config


def test_unknown_identity_format_and_writing_run_refuse_without_side_effects(tmp_path):
    run = synthetic_run(tmp_path / "run")
    path = run / "experiment_config.yaml"
    config = yaml.safe_load(path.read_text())
    for change in (dict(experiment_id="other"), dict(raw_format_version=99), dict(raw_format_version=True),
                   dict(completion_status="running"), dict(completion_status="acquired")):
        path.write_text(yaml.safe_dump(config | change))
        with pytest.raises(ValueError):
            analysis.AnalysisWorkflow(run)
        assert not (run / "analysis_state.json").exists()


def test_readonly_snapshot_and_independent_directory_overrides(tmp_path, no_render):
    first = synthetic_run(tmp_path / "one")
    other = synthetic_run(tmp_path / "two")
    configs = {p: (p / "experiment_config.yaml").read_bytes() for p in (first, other)}
    assert not (first / "analysis_state.json").exists()
    analysis.AnalysisWorkflow(first).snapshot()
    assert not (first / "analysis_state.json").exists()
    ctx = analysis.AnalysisWorkflow(first)
    ctx.execute(["B01", "B02"])
    changed = analysis.AnalysisWorkflow(first, {"WELCH_NPERSEG": 128, "FIT_HALF_WIDTH_HZ": 250})
    assert {key for key, value in changed.states.items() if value["status"] == "stale"} == {"B01", "B02"}
    with pytest.raises(ValueError, match="缺少前置"):
        changed.execute(["B02"])
    changed.execute(["B01", "B02"])
    assert analysis.AnalysisWorkflow(first).options == {"WELCH_NPERSEG": 128, "FIT_HALF_WIDTH_HZ": 250}
    assert analysis.AnalysisWorkflow(other).options["WELCH_NPERSEG"] == 256
    assert all((p / "experiment_config.yaml").read_bytes() == saved for p, saved in configs.items())
    for options in ({"unknown": 1}, {"WELCH_NPERSEG": 2}, {"FIT_HALF_WIDTH_HZ": float("nan")}):
        with pytest.raises(ValueError):
            analysis.AnalysisWorkflow(first, options)


def test_archive_second_stage_keeps_first_input_and_detects_stale_products(tmp_path, no_render):
    run = synthetic_run(tmp_path / "run")
    ctx = analysis.AnalysisWorkflow(run)
    ctx.execute(["B01", "B02"])
    inputs = (run / "results/projection_inputs.json").read_bytes()
    ctx.execute(["B02"])
    assert (run / "results/projection_inputs.json").read_bytes() == inputs
    assert not any((p / "projection_inputs.json").exists() for p in (run / "results").glob("before_*"))
    result = run / "results/lorentzian_fit.csv"
    result.write_bytes(result.read_bytes() + b"\n")
    refreshed = analysis.AnalysisWorkflow(run)
    assert refreshed.states["B01"]["status"] == "completed" and refreshed.states["B02"]["status"] == "stale"


def test_nested_raw_content_change_with_same_size_mtime_invalidates(tmp_path, no_render):
    run = synthetic_run(tmp_path / "run")
    frame = next((run / "raw/field_on").glob("*.npz"))
    with np.load(frame) as data:
        payload = {key: data[key] for key in data.files}
    np.savez(frame, **payload)
    ctx = analysis.AnalysisWorkflow(run)
    ctx.execute(["B01", "B02"])
    original = frame.stat()
    payload["ch1_raw"][0] += 1
    np.savez(frame, **payload)
    import os
    os.utime(frame, ns=(original.st_atime_ns, original.st_mtime_ns))
    assert frame.stat().st_size == original.st_size
    refreshed = analysis.AnalysisWorkflow(run)
    assert refreshed.states["B01"]["status"] == "stale" and refreshed.revision > ctx.revision


@pytest.mark.parametrize("damage", ["missing", "nonfinite", "uneven_time", "escape", "object_array"])
def test_bad_input_does_not_archive_existing_science(tmp_path, no_render, damage):
    run = synthetic_run(tmp_path / "run")
    analysis.analyze(run)
    product = run / "results/psd_spectra.npz"
    before = product.read_bytes()
    frame = next((run / "raw/field_on").glob("*.npz"))
    if damage == "missing":
        frame.unlink()
        # 原始索引明确引用缺失文件，不能通过目录扫描把缺帧忽略。
        np.savez(run / "raw/scope_acquisition_index.npz", phase=["field_on"], relative_file=[str(frame.relative_to(run))])
    elif damage == "escape":
        np.savez(run / "raw/scope_acquisition_index.npz", phase=["field_on"], relative_file=["../outside.npz"])
    elif damage == "object_array":
        np.savez(frame, metadata=np.array([{}], dtype=object))
    else:
        results, metadata = load_npz(str(frame))
        result = results[0]
        if damage == "nonfinite": result.voltage[0] = np.nan
        else: result.time[0] = result.time[1] + 1
        save_to_npz(str(frame), [result], metadata, save_mode="voltage_time")
    with pytest.raises((ValueError, FileNotFoundError)):
        analysis.analyze(run)
    assert product.read_bytes() == before


def test_failed_fit_exports_diagnostics_instead_of_theoretical_fallback(tmp_path, no_render, monkeypatch):
    run = synthetic_run(tmp_path / "run")
    monkeypatch.setattr(steps, "fit_spin_noise_lorentzian", lambda f, *a, **kw: dict(success=False,
        reason="拟合频带点数不足", fit_mask=np.zeros_like(f, dtype=bool)))
    ctx = analysis.AnalysisWorkflow(run)
    result = ctx.execute(["B01", "B02"])
    assert result["status"] == "quality_failed" and result["fit"]["reason"] == "拟合频带点数不足"
    assert ctx.states["B02"]["status"] == "quality_failed"
    assert "gamma_hz" not in result["fit"]
    assert (run / "results/analysis.json").exists()
    json.dumps(ctx.snapshot(), allow_nan=False)


def test_legacy_hf2_uses_original_psd_branch_not_rf_lorentzian(tmp_path):
    run = legacy_run(tmp_path / "legacy")
    ctx = analysis.AnalysisWorkflow(run)
    assert ctx.options == {}
    with pytest.raises(ValueError, match="不支持"):
        analysis.AnalysisWorkflow(run, {"WELCH_NPERSEG": 128})
    result = ctx.execute(["B01", "B02"])
    assert result["acquisition_backend"] == "legacy_hf2_demod_xy" and result["success"]
    assert (run / "results/legacy_psd_spectra.npz").is_file()
    assert (run / "results/legacy_projection_noise_psd.pdf").is_file()
    assert not (run / "results/lorentzian_fit.npz").exists()


def test_junction_results_cannot_archive_raw(tmp_path):
    run = synthetic_run(tmp_path / "run")
    (run / "results").rmdir()
    if sys.platform == "win32":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(run / "results"), str(run / "raw")], check=True, capture_output=True)
    else:
        (run / "results").symlink_to(run / "raw", target_is_directory=True)
    try:
        with pytest.raises(ValueError, match="目录联接"):
            analysis.AnalysisWorkflow(run)
    finally:
        if sys.platform == "win32": (run / "results").rmdir()
        else: (run / "results").unlink()
    assert list((run / "raw").rglob("waveform_*.npz"))


def test_failed_fit_plot_uses_english_annotations_and_real_png_pdf(tmp_path, monkeypatch):
    from . import analysis_plotting as plotting
    original = plotting.save_figure
    def save(fig, path):
        texts = [text.get_text() for axes in fig.axes for text in axes.texts]
        assert texts and all(not any(0x4E00 <= ord(c) <= 0x9FFF for c in text) for text in texts)
        return original(fig, path)
    monkeypatch.setattr(plotting, "save_figure", save)
    frequency = np.linspace(500, 1500, 30)
    plotting._plot_fit(tmp_path, frequency, None, np.ones(30), np.ones(30),
        dict(success=False, fit_mask=np.ones(30, dtype=bool), reason="模拟中文诊断"), fit_input_label="Field-on PSD (V²/Hz)")
    assert (tmp_path / "spin_noise_lorentzian_fit.png").stat().st_size > 0
    assert (tmp_path / "spin_noise_lorentzian_fit.pdf").stat().st_size > 0


def test_actual_thin_cli_requires_directory_and_creates_png_pdf(tmp_path):
    run = synthetic_run(tmp_path / "explicit")
    entry = Path(__file__).resolve().parents[3] / "experiments/Projection_noise_plot.py"
    missing = subprocess.run([sys.executable, str(entry)], capture_output=True, text=True, timeout=30)
    assert missing.returncode != 0 and "run_dir" in missing.stderr
    result = subprocess.run([sys.executable, str(entry), str(run)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    for name in ("analysis.json", "scope_psd_overview.png", "scope_psd_overview.pdf", "spin_noise_lorentzian_fit.png", "spin_noise_lorentzian_fit.pdf"):
        assert (run / "results" / name).stat().st_size > 0
