"""从真实两态时域波形跑 Welch、脊线、拟合、增益与真值链。"""
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import shutil

import numpy as np
import pytest
from scipy.signal import welch
import yaml

from ...analysis.noise_spectrum_separation import lorentzian_vs_control
from . import analysis
from .generation import generate_known_noise_waveform
from .stages import ANALYSIS
from .test_analysis import static_source
from .test_workflow import copy_known_config


REGRESSION_CASES = {
    "noise-spectrum-xy-known-noise": {
        "analysis": {
            "sources": [
                "lab_workflows/experiment_modules/noise_spectrum_xy_known_noise/__init__.py",
                "lab_workflows/experiment_modules/noise_spectrum_xy_known_noise/analysis.py"
            ],
            "tests": [
                "test_real_waveforms_full_and_staged_science_match",
                "test_real_quality_failure_keeps_diagnostics_and_blocks_summary",
                "test_actual_rates_and_missing_pairs_are_rejected_without_fabricating_rows",
                "test_history_options_and_recompute_archive_are_isolated",
                "test_real_module_outside_repository_uses_explicit_run"
            ]
        }
    }
}


def write_run(root, name):
    copy_known_config(root)
    static_source(root, schema_version=4)
    directory = root / "data/Noise_Spectrum_XY_Ctrl_Known_Noise" / name
    raw = directory / "raw"
    raw.mkdir(parents=True)
    rate, samples = 8192.0, 32768
    frequency = np.fft.rfftfreq(samples, 1 / rate)
    voltage = np.linspace(.05, 3.95, 101)
    matrix = 1e-9 + np.column_stack([
        lorentzian_vs_control(1000 * voltage, 80.0, .005, 0, 0, fixed) for fixed in frequency])
    rng = np.random.default_rng(20261015)
    for index, psd in enumerate(matrix):
        for state, factor in (("off", 1.0), ("on", 1.4)):
            spectrum = np.sqrt(psd * factor * rate * samples / 2) * np.exp(1j * rng.uniform(0, 2 * np.pi, frequency.size))
            spectrum[0] = spectrum[-1] = 0
            np.save(raw / f"waveform_C{index:04d}_{state}.npy", np.fft.irfft(spectrum, samples))
    wave = generate_known_noise_waveform(repeat_freq_hz=4, bands=[(300, 3000, 1e-6)], seed=20260922)
    np.save(raw / "known_noise_waveform.npy", wave.normalized)
    config = dict(experiment_id="noise-spectrum-xy-known-noise", experiment_type="Noise_Spectrum_XY_Ctrl_Known_Noise",
        schema_version=5, completion_status="completed", parameters={"RUN_TAG": name,
            "ANALYSIS_BIN_WIDTH_HZ": 32.0, "ANALYSIS_FREQUENCY_MIN_HZ": 32.0,
            "ANALYSIS_FREQUENCY_MAX_HZ": 3800.0, "ANALYSIS_FIT_HALF_WIDTH_HZ": 320.0,
            "TARGET_NOISE_FREQ_STOP_HZ": 3800.0, "ANALYSIS_DISPERSION_SOURCE_RUN": "source"},
        scan_params={"xy_envelope_voltage_V": voltage.tolist()},
        hf2_daq={"actual_rate_Sa_s": rate, "DAQ_duration_s": samples / rate},
        known_noise=dict(waveform_file="raw/known_noise_waveform.npy", sample_rate_sa_s=wave.sample_rate_sa_s,
            amplitude_vpp=.01, repeat_frequency_hz=4, design_peak_v=wave.design_peak_v, rms_v=wave.rms_v,
            seed=wave.seed, bands=[dict(start_hz=300, stop_hz=3000, design_psd_v2_per_hz=1e-6)]),
        truth_chain=dict(z_calibration_source="manual", z_calibration_run="", K_Z_Hz_per_V=5373.638,
                         K_Z_uncertainty_Hz_per_V=float("nan")))
    (directory / "experiment_config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    return directory


def test_real_waveforms_full_and_staged_science_match(tmp_path):
    full = write_run(tmp_path, "full")
    staged = full.parent / "staged"
    shutil.copytree(full, staged)
    with redirect_stdout(StringIO()):
        expected = analysis.analyze(full)
        for stage in ANALYSIS:
            ctx = analysis.AnalysisWorkflow(staged)
            ctx.execute([stage.id])
    assert expected["status"] == ctx.summary["status"] == "completed"
    assert expected["calibration"]["k"] == pytest.approx(1000, rel=.05)
    assert expected["calibration"]["support_points"] >= 45
    assert expected["fit_quality"]["both_accepted"] > 20
    assert expected["fit_quality"]["background_both_accepted"] > 20
    assert expected["truth_chain"]["dispersion_slope_v_per_hz"] == pytest.approx(.001 / .025**2 / 5373.638)
    assert expected["comparison"] == ctx.summary["comparison"]
    for name in ("psd_matrices.npz", "calibration.npz", "popt_fit.npz", "separated_spectra.npz", "noise_spectra.npz", "fit_diagnostics.npz"):
        with np.load(full / "results" / name, allow_pickle=False) as first, np.load(staged / "results" / name, allow_pickle=False) as second:
            assert first.files == second.files
            for key in first.files:
                np.testing.assert_array_equal(first[key], second[key])
    with np.load(full / "results/psd_matrices.npz") as data:
        wave = np.load(full / "raw/waveform_C0000_on.npy")
        f, reference = welch(wave, fs=8192.0, nperseg=256, noverlap=128,
            window="hann", detrend="constant", scaling="density")
        np.testing.assert_allclose(data["psd_on"][0], reference)
        np.testing.assert_array_equal(data["freq_axis"], f)
    with np.load(full / "results/noise_spectra.npz") as data:
        assert data["fit_mask"].any()
        measured = (data["A_on"] - data["A_off"]) / (data["gamma_on"]**2 * expected["truth_chain"]["dispersion_slope_v_per_hz"]**2)
        np.testing.assert_allclose(data["S_measured_hz2_per_hz"], measured, equal_nan=True)
    for name in ("known_noise_preview", "noise_spectrum_2d", "differential_psd", "lorentzian_line_shapes",
                 "noise_spectra_extracted", "dispersion_scan", "controlled_uncontrolled_comparison", "measured_vs_truth"):
        for suffix in ("png", "pdf"):
            assert (full / f"results/{name}.{suffix}").stat().st_size > 1000


def test_real_quality_failure_keeps_diagnostics_and_blocks_summary(tmp_path):
    directory = write_run(tmp_path, "quality")
    with redirect_stdout(StringIO()):
        ctx = analysis.AnalysisWorkflow(directory)
        ctx.execute(["B01", "B02", "B03"])
        ctx = analysis.AnalysisWorkflow(directory, {"ANALYSIS_FIT_HALF_WIDTH_HZ": 160.0})
        with pytest.raises(ValueError, match="拟合质量"):
            ctx.execute(["B04"])
    assert ctx.states["B04"]["status"] == "quality_failed"
    with np.load(directory / "results/popt_fit.npz") as diagnostic:
        assert np.all(diagnostic["rejection_on"] != "accepted")
    assert (directory / "results/fit_diagnostics.npz").is_file()
    with pytest.raises(ValueError, match="B04"):
        analysis.AnalysisWorkflow(directory).execute(["B06"])
    assert not (directory / "results/noise_spectra.npz").exists()


def test_actual_rates_and_missing_pairs_are_rejected_without_fabricating_rows(tmp_path):
    directory = write_run(tmp_path, "rates")
    ctx = analysis.AnalysisWorkflow(directory)
    with redirect_stdout(StringIO()):
        ctx.execute(["B01"])
        np.savez(directory / "raw/waveform_C0000_on_acquisition.npz", actual_rate_Sa_s=16384.0)
        with pytest.raises(ValueError, match="实际采样率"):
            analysis.AnalysisWorkflow(directory).execute(["B01", "B02"])
    (directory / "raw/waveform_C0000_on_acquisition.npz").unlink()
    for file in sorted((directory / "raw").glob("waveform_C*_off.npy"))[19:]:
        file.unlink()
    with pytest.raises(ValueError, match="不足 20"), redirect_stdout(StringIO()):
        analysis.AnalysisWorkflow(directory).execute(["B01", "B02"])
    assert not (directory / "results/psd_matrices.npz").exists()


def test_history_options_and_recompute_archive_are_isolated(tmp_path):
    directory = write_run(tmp_path, "one")
    other = directory.parent / "two"
    shutil.copytree(directory, other)
    before = yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf-8"))["parameters"]
    with redirect_stdout(StringIO()):
        first = analysis.AnalysisWorkflow(directory)
        first.execute(["B01", "B02", "B03"])
        second = analysis.AnalysisWorkflow(other)
        second.execute(["B01", "B02"])
    other_state = (other / "analysis_state.json").read_bytes()
    psd = (directory / "results/psd_matrices.npz").read_bytes()
    with redirect_stdout(StringIO()):
        ctx = analysis.AnalysisWorkflow(directory, {"ANALYSIS_FREQUENCY_MAX_HZ": 3500.0})
        ctx.execute(["B02", "B03"])
    assert list((directory / "results").glob("before_*/known_noise_preview.npz"))
    assert list((directory / "results").glob("before_*/psd_matrices.npz"))
    assert (other / "analysis_state.json").read_bytes() == other_state
    assert yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf-8"))["parameters"] == before
    assert analysis.AnalysisWorkflow(directory).options["frequency_max_hz"] == 3500.0
    assert analysis.AnalysisWorkflow(other).options["frequency_max_hz"] == 3800.0
    with redirect_stdout(StringIO()):
        ctx.execute(["B03"])
    assert (directory / "results/psd_matrices.npz").read_bytes() == psd


@pytest.mark.parametrize("mode", ["explicit", "missing", "nonexistent"])
def test_real_module_outside_repository_uses_explicit_run(tmp_path, mode):
    from lab_workflows.experiments.test_analysis_imports import check_real_analysis_module

    run = write_run(tmp_path, "module")
    check_real_analysis_module(run, __package__ + ".analysis", "noise_spectra.npz", mode)
