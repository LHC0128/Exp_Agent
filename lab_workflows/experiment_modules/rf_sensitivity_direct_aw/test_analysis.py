"""合成 X 数据、真实分析子进程和无副作用导入验证。"""

from dataclasses import replace
from pathlib import Path
import json
import os
import subprocess
import sys

import numpy as np
import pytest
import yaml
from scipy.signal import welch

from sensitivity_analysis.fitting import LARMOR_PER_NT

from .analysis import analyze, dispersive, fit_response_curve
from .definition import ADAPTER, DEFINITION
from .models import RFSensitivityDirectAWParams
from .run_format import DATA_TYPE, EXPERIMENT_ID


def synthetic_run(directory: Path) -> tuple[dict, float]:
    (directory / "raw").mkdir(parents=True)
    params = replace(RFSensitivityDirectAWParams(), rf_amp_points=41, noise_n_avg=2, noise_nperseg=400)
    config = {"experiment_id": EXPERIMENT_ID, "experiment_type": DATA_TYPE, "execution_mode": "typed_workflow", "schema_version": 2, "acquisition_method": "direct_aw_x", "parameters": params.to_external(), "completion_status": "completed", "phase_calibration_status": "completed", "xy_phase_calibration": {"converged": True}, "analysis_status": "not_started"}
    (directory / "experiment_config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    field = np.linspace(-400, 400, 41)
    x = dispersive(field, 8.0, 80.0, 0.0, 0.01)
    np.savez(directory / "raw/response_data.npz", amplitudes_V=field / 1758.5, amplitudes_nT=field, demod3_x_V=x, demod3_y_V=x * 100, demod3_r_V=np.abs(x) * 100, z_v_to_nT=1758.5)
    for index, rate in enumerate((4000, 4100)):
        values = np.random.default_rng(index).normal(0, 1e-4, 16000)
        np.savez(directory / f"raw/noise_D3_{index:04d}.npz", noise_x_V=values, actual_rate_Sa_s=rate, demod_idx=3, signal_path="sample.x")
    return config, 8 / 80 ** 2


def test_x_slope_psd_sensitivity_and_artifacts(tmp_path):
    _, expected_slope = synthetic_run(tmp_path)
    result = analyze(tmp_path)
    assert result["response_component"] == result["noise_component"] == "X"
    assert result["response_fit"]["success"]
    assert result["response_fit"]["r_squared"] > 0.99
    assert result["response_fit"]["slope_V_per_nT"] == pytest.approx(expected_slope, rel=1e-5)
    spectra = []
    axis = None
    for index in range(2):
        with np.load(tmp_path / f"raw/noise_D3_{index:04d}.npz") as data:
            values = data["noise_x_V"]
            frequency, psd = welch(values - values.mean(), fs=float(data["actual_rate_Sa_s"]), nperseg=400, noverlap=200)
        if axis is None:
            axis = frequency
        else:
            psd = np.interp(axis, frequency, psd)
        spectra.append(psd)
    expected_psd = np.mean(spectra, axis=0)
    with np.load(tmp_path / "results/psd_avg.npz") as data:
        assert np.allclose(data["psd_avg_V2_per_Hz"], expected_psd)
        assert data["actual_rate_Sa_s"] == 4050
    with np.load(tmp_path / "results/sensitivity.npz") as data:
        expected_raw = np.sqrt(expected_psd) / (expected_slope / 1e6)
        hwhm_hz = 80.0 * LARMOR_PER_NT
        correction = np.sqrt(1.0 + (axis / hwhm_hz) ** 2)
        expected = expected_raw * correction
        flat_mask = (axis >= 3) & (axis <= hwhm_hz)
        assert np.allclose(data["sensitivity_fT_per_sqrtHz"], expected, rtol=1e-5)
        assert np.allclose(data["sensitivity_raw_fT_per_sqrtHz"], expected_raw, rtol=1e-5)
        assert data["f_larmor_Hz"] == pytest.approx(hwhm_hz, rel=1e-5)
        assert data["flat_median_fT_per_sqrtHz"] == pytest.approx(np.median(expected[flat_mask]), rel=1e-5)
        assert data["raw_flat_median_fT_per_sqrtHz"] == pytest.approx(np.median(expected_raw[flat_mask]), rel=1e-5)
    assert result["frequency_response_correction"]["applied"] is True
    assert result["frequency_response_correction"]["hwhm_Hz"] == pytest.approx(hwhm_hz, rel=1e-5)
    for name in ("response_curve.png", "noise_psd.png", "sensitivity.png", "full_analysis.png", "analysis.yaml", "analysis.json"):
        assert (tmp_path / "results" / name).stat().st_size > 100


def test_numeric_slope_fallback():
    result = fit_response_curve(np.array([-2.0, 0, 2]), np.array([-1.0, 0, 1.0]))
    assert result["method"] == "numeric_gradient"
    assert result["success"] is False
    assert result["slope_V_per_nT"] == 0.5
    assert result["failure_reason"]


@pytest.mark.parametrize("change", [{"schema_version": 1}, {"completion_status": "failed"}, {"xy_phase_calibration": {"converged": False}}])
def test_old_or_failed_run_rejected_before_writing_results(tmp_path, change):
    config, _ = synthetic_run(tmp_path)
    config.update(change)
    (tmp_path / "experiment_config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    with pytest.raises(ValueError):
        analyze(tmp_path)
    assert not (tmp_path / "results").exists()


def test_incomplete_raw_data_rejected_before_results(tmp_path):
    synthetic_run(tmp_path)
    (tmp_path / "raw/noise_D3_0001.npz").unlink()
    with pytest.raises(ValueError, match="不完整"):
        analyze(tmp_path)
    assert not (tmp_path / "results").exists()


def test_registered_analysis_preserves_old_config(tmp_path):
    config, _ = synthetic_run(tmp_path)
    config["schema_version"] = 1
    path = tmp_path / "experiment_config.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="旧数据"):
        DEFINITION.analyze(tmp_path)
    assert path.read_bytes() == before
    assert not (tmp_path / "results").exists()


def test_analysis_adapter_starts_real_subprocess(tmp_path):
    synthetic_run(tmp_path)
    response = ADAPTER.analyze(tmp_path, None)
    assert response["artifacts"]
    assert (tmp_path / "results/full_analysis.png").is_file()
    result = json.loads((tmp_path / "results/analysis.json").read_text(encoding="utf-8"))
    assert result["response_fit"]["slope_V_per_nT"] == pytest.approx(0.00125, rel=1e-5)
    assert yaml.safe_load((tmp_path / "experiment_config.yaml").read_text(encoding="utf-8"))["analysis_status"] == "completed"


def test_imports_do_not_read_runtime_or_create_files(tmp_path):
    code = '''
import os
os.environ["LAB_TYPED_PARAMETERS"] = "invalid JSON"
os.environ["LAB_RUN_DIR"] = "missing"
from lab_workflows.experiment_modules.rf_sensitivity_direct_aw import models, workflow, analysis
import experiments.RF_Field_Sensitivity_AW
import experiments.RF_Field_Sensitivity_AW_plot
'''
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[3])
    result = subprocess.run([sys.executable, "-B", "-c", code], cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert not list(tmp_path.iterdir())
