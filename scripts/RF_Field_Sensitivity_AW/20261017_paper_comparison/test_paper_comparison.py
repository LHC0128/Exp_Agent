"""论文对比的数值口径、原始记录只读与命令行产物回归。"""

from pathlib import Path
import hashlib
import json
import subprocess
import sys

import numpy as np
import pytest
from scipy.signal import welch
import yaml

from paper_comparison_core import dispersive
from paper_comparison import band_statistics, compare_for_paper, slope_at_field


def _run(directory, *, optimized=False):
    raw = directory / "raw"
    raw.mkdir(parents=True)
    field = np.linspace(-54, 54, 26)
    gain = 2.0 if optimized else 1.0
    config = dict(experiment_id="rf-sensitivity-aw", execution_mode="typed_workflow", schema_version=2,
                  acquisition_method="direct_aw_x", completion_status="completed", phase_calibration_status="completed",
                  xy_phase_calibration={"converged": True}, response_component="X", noise_component="X",
                  noise_output_snapshot={"z": {"output": False}},
                  parameters=dict(RF_AMP_POINTS=26, NOISE_N_AVG=4, NOISE_NPERSEG=1000, DEMOD3_IDX=1,
                                  Z_RF_FREQ=3000.0, LINEWIDTH_MODE="frequency_sweep", BW_FREQ_POINTS=40,
                                  ARB_WAVEFORM_FILE="optimized.csv" if optimized else "constant.csv"))
    (directory / "experiment_config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    x = dispersive(field, 40 * gain, 18, 1.0, 0.01)
    np.savez(raw / "response_data.npz", amplitudes_V=field / 542.89, amplitudes_nT=field,
             demod3_x_V=x, demod3_y_V=np.zeros(26), demod3_r_V=abs(x),
             demod3_x_std_V=np.full(26, 1e-4), z_v_to_nT=542.89, demod_idx=1)
    frequency = np.linspace(2500, 3500, 40)
    np.savez(raw / "bandwidth_data.npz", frequency_Hz=frequency,
             demod3_r_V=0.8 / (1 + ((frequency - 3000) / 160)**2) + 0.02,
             z_rf_amplitude_vpp=0.01, demod_idx=1)
    np.savez(raw / "xy_aw_waveforms.npz", omega_ctrl_Hz=np.linspace(-3000, 3000, 100) if optimized else np.full(100, 3000))
    for index in range(4):
        values = np.random.default_rng(index).normal(0, 1e-4, 2000)
        np.savez(raw / f"noise_D3_{index:04d}.npz", noise_x_V=values, actual_rate_Sa_s=2000.0,
                 signal_path="sample.x", demod_idx=1)


def _hashes(directory):
    return {str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in directory.rglob("*") if path.is_file()}


def test_local_slope_and_full_covariance_gradient():
    parameters = np.array([42.0, 19.0, -1.3, 0.01])
    slope, gradient = slope_at_field(parameters)
    assert slope == pytest.approx((dispersive(1e-5, *parameters) - dispersive(-1e-5, *parameters)) / 2e-5)
    for index in range(4):
        shifted = parameters.copy()
        shifted[index] += 1e-5
        assert gradient[index] == pytest.approx((slope_at_field(shifted)[0] - slope) / 1e-5, rel=1e-4, abs=1e-9)
    assert slope != pytest.approx(parameters[0] / parameters[1]**2)


def test_band_bootstrap_averages_psd_before_sqrt():
    run = dict(frequency=np.arange(5.0), spectra=np.array([[1, 1, 1, 1, 1], [9, 9, 9, 9, 9]]),
               bandwidth={"hwhm_Hz": 10.0}, slope=1e6, correction=np.ones(5),
               sensitivity=np.full(5, np.sqrt(5)), raw_sensitivity=np.full(5, np.sqrt(5)), mean_psd=np.full(5, 5))
    summary, samples = band_statistics(run, (1, 4), np.random.default_rng(1))
    assert summary["corrected_median_fT_per_sqrtHz"] == pytest.approx(np.sqrt(5))
    assert set(np.round(samples, 8)) == {1.0, round(np.sqrt(5), 8), 3.0}
    with pytest.raises(ValueError, match="HWHM"):
        band_statistics(run, (1, 11), np.random.default_rng(1))


def test_cli_recomputes_raw_and_keeps_sources_read_only(tmp_path):
    constant, optimized = tmp_path / "constant", tmp_path / "optimized"
    _run(constant)
    _run(optimized, optimized=True)
    before = [_hashes(directory) for directory in (constant, optimized)]
    script = Path(__file__).with_name("RF_Field_Sensitivity_AW_compare.py")
    output = tmp_path / "comparison"
    completed = subprocess.run([sys.executable, str(script), str(constant), str(optimized), "--output", str(output)],
                               cwd=tmp_path, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = json.loads((output / "comparison.json").read_text(encoding="utf-8"))
    assert result["comparison"]["slope_ratio"] == pytest.approx(2.0)
    assert result["comparison"]["sensitivity_ratio"] == pytest.approx(0.5)
    assert result["comparison"]["sensitivity_reduction_percent"] == pytest.approx(50.0)
    assert result["field_amplitude_convention"].startswith("RMS")
    assert before == [_hashes(directory) for directory in (constant, optimized)]
    spectra = []
    for path in sorted((constant / "raw").glob("noise_D3_*.npz")):
        with np.load(path) as data:
            _, psd = welch(data["noise_x_V"] - data["noise_x_V"].mean(), fs=2000, window="hann", nperseg=1000, noverlap=500)
            spectra.append(psd)
    with np.load(output / "comparison_source_data.npz") as data:
        np.testing.assert_allclose(data["constant_mean_psd"], np.mean(spectra, axis=0))
        np.testing.assert_allclose(data["constant_sensitivity"] / data["constant_raw_sensitivity"], data["constant_correction"])
    for stem in ("rf_control_response", "rf_control_sensitivity", "rf_control_comparison", "rf_control_diagnostics"):
        for extension in ("pdf", "png"):
            assert (output / f"{stem}.{extension}").stat().st_size > 1000
    with pytest.raises(ValueError, match="已存在"):
        compare_for_paper(constant, optimized, output)
    with pytest.raises(ValueError, match="重叠"):
        compare_for_paper(constant, optimized, constant / "results/paper")


@pytest.mark.parametrize("change, message", [("component", "X 标定"), ("noise_on", "零 RF"), ("grid", "频率网格")])
def test_invalid_inputs_do_not_create_output(tmp_path, change, message):
    constant, optimized = tmp_path / "constant", tmp_path / "optimized"
    _run(constant)
    _run(optimized, optimized=True)
    path = constant / "experiment_config.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    if change == "component":
        config["noise_component"] = "R"
    elif change == "noise_on":
        config["noise_output_snapshot"]["z"]["output"] = True
    else:
        source = constant / "raw/noise_D3_0001.npz"
        with np.load(source) as data:
            fields = dict(data)
        fields["actual_rate_Sa_s"] = 2100.0
        np.savez(source, **fields)
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        compare_for_paper(constant, optimized, tmp_path / "output")
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize("change, message", [
    ("schema", "新格式"), ("phase", "校相"), ("calibration", "磁场标定"),
    ("missing_noise", "噪声原始记录不完整"), ("response_index", "解调器索引"),
    ("missing_r", "缺少 R"), ("bandwidth_index", "解调器索引"), ("bandwidth_quality", "质量不合格"),
])
def test_standalone_validation_keeps_incomplete_data_out(tmp_path, change, message):
    constant, optimized = tmp_path / "constant", tmp_path / "optimized"
    _run(constant)
    _run(optimized, optimized=True)
    config_path = constant / "experiment_config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if change == "schema":
        config["schema_version"] = 1
    elif change == "phase":
        config["xy_phase_calibration"]["converged"] = False
    elif change == "missing_noise":
        (constant / "raw/noise_D3_0000.npz").unlink()
    else:
        name = "response_data.npz" if change in ("calibration", "response_index") else "bandwidth_data.npz"
        path = constant / "raw" / name
        with np.load(path) as data:
            fields = dict(data)
        if change == "calibration":
            fields["z_v_to_nT"] = 1.0
        elif change.endswith("index"):
            fields["demod_idx"] = 2
        elif change == "missing_r":
            fields.pop("demod3_r_V")
        else:
            fields["demod3_r_V"] = np.zeros_like(fields["demod3_r_V"])
        np.savez(path, **fields)
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        compare_for_paper(constant, optimized, tmp_path / "output")
    assert not (tmp_path / "output").exists()
