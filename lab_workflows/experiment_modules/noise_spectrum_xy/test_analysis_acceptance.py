"""由真实时域波形验证完整科学链、格式资格和历史目录隔离。"""

from contextlib import redirect_stdout
from copy import deepcopy
from io import StringIO
import json
import os
from pathlib import Path
import shutil

import numpy as np
import pytest
from scipy.signal import welch
import yaml

from ...analysis.noise_spectrum_separation import lorentzian_vs_control
from . import analysis
from .run_format import require_supported_format
from .stages import ANALYSIS


def write_run(directory, *, schema=4):
    """周期平稳信号的已知单边谱；Welch、脊线和拟合均不替换。"""
    raw = directory / "raw"
    raw.mkdir(parents=True)
    rate, samples = 8192.0, 32768
    frequency = np.fft.rfftfreq(samples, 1 / rate)
    voltage = np.linspace(.05, 3.95, 101)
    model = 1e-9 + np.column_stack([
        lorentzian_vs_control(1000 * voltage, 80.0, .005, 0, 0, fixed) for fixed in frequency])
    rng = np.random.default_rng(20261015)
    for index, psd in enumerate(model):
        spectrum = np.sqrt(psd * rate * samples / 2) * np.exp(1j * rng.uniform(0, 2 * np.pi, frequency.size))
        spectrum[0] = spectrum[-1] = 0
        np.save(raw / f"waveform_C{index:04d}.npy", np.fft.irfft(spectrum, samples))
    config = dict(experiment_id="noise-spectrum-xy", experiment_type="Noise_Spectrum_XY_Ctrl",
        raw_format_version=1, schema_version=schema, completion_status="completed",
        parameters={"RUN_TAG": directory.name, "ANALYSIS_BIN_WIDTH_HZ": 32.0,
            "ANALYSIS_FREQUENCY_MIN_HZ": 0.0, "ANALYSIS_FREQUENCY_MAX_HZ": 3800.0,
            "ANALYSIS_FIT_HALF_WIDTH_HZ": 320.0},
        scan_params={"xy_envelope_voltage_V": voltage.tolist()},
        hf2_daq={"actual_rate_Sa_s": rate, "DAQ_duration_s": samples / rate})
    (directory / "experiment_config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    return config


def test_real_waveforms_full_and_staged_science_match(tmp_path):
    full, staged = tmp_path / "full", tmp_path / "staged"
    write_run(full)
    shutil.copytree(full, staged)
    before = (full / "experiment_config.yaml").read_bytes()
    with redirect_stdout(StringIO()):
        expected = analysis.analyze(full)
        for index, stage in enumerate(ANALYSIS):
            actual = analysis.analyze(staged, step_ids=[stage.id], expected_revision=index)
    assert expected == actual
    assert expected["status"] == "completed"
    assert expected["calibration"]["support_points"] >= 45
    assert expected["calibration"]["k"] == pytest.approx(1000, rel=.05)
    assert abs(expected["calibration"]["b"]) < 100
    assert expected["fit"]["valid_points"] > 20
    assert expected["fit"]["background_valid_points"] > 20
    assert (full / "experiment_config.yaml").read_bytes() == before
    for name in ("psd_matrix.npz", "calibration.npz", "popt_fit.npz", "noise_spectra.npz", "fit_diagnostics.npz"):
        with np.load(full / "results" / name, allow_pickle=False) as first, np.load(staged / "results" / name, allow_pickle=False) as second:
            assert first.files == second.files
            for key in first.files:
                np.testing.assert_array_equal(first[key], second[key])
    with np.load(full / "results/psd_matrix.npz") as psd:
        wave = np.load(full / "raw/waveform_C0000.npy")
        frequency, reference = welch(wave, fs=8192.0, nperseg=256, noverlap=128,
            window="hann", detrend="constant", scaling="density")
        np.testing.assert_allclose(psd["psd_matrix"][0], reference)
        np.testing.assert_array_equal(psd["freq_axis"], frequency)
    with np.load(full / "results/noise_spectra.npz") as separated:
        assert np.all(separated["freq_axis"] > 0)
        assert not separated["interpolated_mask"].any()
        assert np.isnan(separated["S_beta"][~separated["fit_mask"]]).all()
        assert np.isnan(separated["N_S1"][~separated["background_fit_mask"]]).all()
    for name in ("noise_spectrum_2d", "calibration", "noise_spectra_extracted", "background_identifiability"):
        for suffix in ("png", "pdf"):
            assert (full / f"results/{name}.{suffix}").stat().st_size > 1000


@pytest.mark.parametrize("identity,version", [("noise-spectrum-xy-known-noise", 1),
                                               ("noise-spectrum-xy", 99), ("noise-spectrum-xy", True)])
def test_foreign_identity_or_unknown_format_never_touches_results(tmp_path, identity, version):
    directory = tmp_path / "invalid"
    config = write_run(directory)
    config.update(experiment_id=identity, raw_format_version=version)
    path = directory / "experiment_config.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    results = directory / "results"
    results.mkdir()
    (results / "existing.txt").write_text("历史诊断", encoding="utf-8")
    before = {file.name: file.read_bytes() for file in results.iterdir()}
    with pytest.raises(ValueError):
        analysis.analyze(directory)
    assert {file.name: file.read_bytes() for file in results.iterdir()} == before
    assert not (directory / "analysis_state.json").exists()


def test_historical_amp_axis_and_schema_versions_keep_original_segment_rule(tmp_path):
    directory = tmp_path / "legacy"
    config = write_run(directory)
    config.pop("raw_format_version")
    config["hf2_daq"]["nperseg"] = 512
    # 已核实的 0709 无 ID/schema 格式：type + AMP 轴 + 实际采样率。
    config.pop("schema_version")
    config.pop("experiment_id")
    config["scan_params"] = {"XY_AMP_START_V": .05, "XY_AMP_STOP_V": 3.95, "XY_AMP_POINTS": 101}
    for schema in (None, 1, 2, 3, 4):
        current = deepcopy(config)
        if schema is not None:
            current.update(schema_version=schema, experiment_id="noise-spectrum-xy")
        require_supported_format(current)
        matrix, frequency, voltage, rows, info = analysis.compute_psd(directory, current, {"bin_width_hz": 32.0})
        assert info["nperseg"] == (512 if schema is None or schema < 3 else 256)
        assert matrix.shape == (101, len(frequency)) and len(rows) == 101
        np.testing.assert_allclose(voltage, np.linspace(.05, 3.95, 101))
    current = dict(config, experiment_id="noise-spectrum-xy", raw_format_version=1, schema_version=999)
    require_supported_format(current)
    path = directory / "experiment_config.yaml"
    path.write_text(yaml.safe_dump(current), encoding="utf-8")
    with redirect_stdout(StringIO()):
        analysis.analyze(directory, step_ids=["B01", "B02"])
    assert json.loads((directory / "analysis_state.json").read_text(encoding="utf-8"))["steps"]["B02"]["status"] == "completed"


def test_invalid_actual_rate_is_rejected_before_archiving_and_running_directory_is_blocked(tmp_path):
    directory = tmp_path / "missing-rate"
    config = write_run(directory)
    config["hf2_daq"].pop("actual_rate_Sa_s")
    path = directory / "experiment_config.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    results = directory / "results"
    results.mkdir()
    prior = results / "existing.txt"
    prior.write_text("保留已有结果", encoding="utf-8")
    with pytest.raises(ValueError, match="实际采样率"), redirect_stdout(StringIO()):
        analysis.analyze(directory, step_ids=["B01"])
    assert prior.read_text(encoding="utf-8") == "保留已有结果"
    assert not list(results.glob("before_*"))
    config["completion_status"] = "running"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    with pytest.raises(ValueError, match="仍在写入"):
        analysis.AnalysisWorkflow(directory)


@pytest.mark.parametrize("value", [True, "32"])
def test_analysis_overrides_reject_non_numeric_types(tmp_path, value):
    directory = tmp_path / "invalid-option"
    write_run(directory)
    with pytest.raises(ValueError, match="必须为数值"):
        analysis.AnalysisWorkflow(directory, {"ANALYSIS_BIN_WIDTH_HZ": value})
    assert not (directory / "analysis_state.json").exists()


@pytest.mark.parametrize("invalid", ["missing-rate", "bad-axis"])
def test_b01_ineligible_inputs_preserve_existing_summary_and_products(tmp_path, invalid):
    """资格失败前不得覆盖尚未归档的旧分析摘要。"""
    config = dict(experiment_id="noise-spectrum-xy", experiment_type="Noise_Spectrum_XY_Ctrl",
        raw_format_version=1, schema_version=4, completion_status="completed", parameters={},
        scan_params={"xy_envelope_voltage_V": [0.0, 1.0]},
        hf2_daq={"actual_rate_Sa_s": 8192.0, "DAQ_duration_s": 1.0})
    if invalid == "missing-rate":
        config["hf2_daq"].pop("actual_rate_Sa_s")
    else:
        config["scan_params"]["xy_envelope_voltage_V"] = [1.0, 1.0]
    (tmp_path / "experiment_config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    results = tmp_path / "results"
    results.mkdir()
    (results / "analysis_summary.json").write_text('{"previous_scientific_summary": 42}', encoding="utf-8")
    (results / "psd_matrix.npz").write_bytes(b"previous PSD")
    before = {path.name: path.read_bytes() for path in results.iterdir()}
    with pytest.raises(ValueError), redirect_stdout(StringIO()):
        analysis.analyze(tmp_path, step_ids=["B01"])
    assert {path.name: path.read_bytes() for path in results.iterdir()} == before


def test_real_inputs_and_products_invalidate_only_their_own_directory(tmp_path):
    directory, sibling = tmp_path / "one", tmp_path / "two"
    write_run(directory)
    shutil.copytree(directory, sibling)
    with redirect_stdout(StringIO()):
        analysis.analyze(directory, step_ids=["B01", "B02", "B03"])
        analysis.analyze(sibling, step_ids=["B01", "B02"])
    sibling_state = (sibling / "analysis_state.json").read_bytes()
    psd = directory / "results/psd_matrix.npz"
    original = psd.read_bytes()
    with redirect_stdout(StringIO()):
        analysis.analyze(directory, step_ids=["B03"])
    assert psd.read_bytes() == original
    assert list((directory / "results").glob("before_*/calibration.npz"))
    assert not list((directory / "results").glob("before_*/psd_matrix.npz"))
    product = directory / "results/calibration.npz"
    stat, content = product.stat(), bytearray(product.read_bytes())
    content[-1] ^= 1
    product.write_bytes(content)
    os.utime(product, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    with pytest.raises(ValueError, match="B03"):
        analysis.analyze(directory, step_ids=["B04"])
    wave = directory / "raw/waveform_C0000.npy"
    stat = wave.stat()
    np.save(wave, np.load(wave) * 2)
    os.utime(wave, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    with pytest.raises(ValueError, match="B01"):
        analysis.analyze(directory, step_ids=["B02"])
    assert (sibling / "analysis_state.json").read_bytes() == sibling_state
