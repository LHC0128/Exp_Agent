"""合成 X 数据、真实分析子进程和无副作用导入验证。"""

from dataclasses import replace
from pathlib import Path
import json
import os
import subprocess
import sys

import matplotlib
matplotlib.use("Agg")
import numpy as np
import pytest
import yaml
from scipy.signal import welch

from sensitivity_analysis.fitting import LARMOR_PER_NT

from .analysis import analyze, dispersive, fit_response_curve
from .definition import ADAPTER, DEFINITION
from .models import RFSensitivityDirectAWParams
from .run_format import DATA_TYPE, EXPERIMENT_ID


REGRESSION_CASES = {
    "rf-sensitivity-aw": {
        "analysis": {
            "sources": [
                "lab_workflows/experiment_modules/rf_sensitivity_direct_aw/analysis.py"
            ],
            "tests": [
                "test_analysis_uses_recorded_index_and_legacy_default",
                "test_non_three_analysis_keeps_acquired_signal_chain",
                "test_response_index_must_match_saved_configuration",
                "test_x_slope_psd_sensitivity_and_artifacts",
                "test_bandwidth_fit_overrides_dispersive_hwhm",
                "test_bandwidth_requires_complete_finite_r_data",
                "test_bandwidth_analysis_and_plot_do_not_require_x_diagnostic",
                "test_old_x_bandwidth_analysis_version_invalidates_cached_stages",
                "test_bandwidth_mode_requires_sweep_data",
                "test_bandwidth_fit_quality_failure_is_not_silently_ignored",
                "test_dispersive_mode_ignores_bandwidth_data",
                "test_numeric_slope_fallback",
                "test_old_or_failed_run_rejected_before_writing_results",
                "test_incomplete_raw_data_rejected_before_results",
                "test_registered_analysis_preserves_old_config",
                "test_analysis_adapter_starts_real_subprocess",
                "test_imports_do_not_read_runtime_or_create_files"
            ]
        }
    }
}


def synthetic_run(directory: Path, *, demod_idx=3) -> tuple[dict, float]:
    (directory / "raw").mkdir(parents=True)
    params = replace(RFSensitivityDirectAWParams(), rf_amp_points=41, noise_n_avg=2, noise_nperseg=400, demod3_idx=demod_idx)
    config = {"experiment_id": EXPERIMENT_ID, "experiment_type": DATA_TYPE, "execution_mode": "typed_workflow", "schema_version": 2, "acquisition_method": "direct_aw_x", "parameters": params.to_external(), "completion_status": "completed", "phase_calibration_status": "completed", "xy_phase_calibration": {"converged": True}, "analysis_status": "not_started"}
    (directory / "experiment_config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    field = np.linspace(-400, 400, 41)
    x = dispersive(field, 8.0, 80.0, 0.0, 0.01)
    np.savez(directory / "raw/response_data.npz", amplitudes_V=field / 1758.5, amplitudes_nT=field, demod3_x_V=x, demod3_y_V=x * 100, demod3_r_V=np.abs(x) * 100, z_v_to_nT=1758.5, demod_idx=demod_idx)
    for index, rate in enumerate((4000, 4100)):
        values = np.random.default_rng(index).normal(0, 1e-4, 16000)
        np.savez(directory / f"raw/noise_D3_{index:04d}.npz", noise_x_V=values, actual_rate_Sa_s=rate, demod_idx=demod_idx, signal_path="sample.x")
    return config, 8 / 80 ** 2


@pytest.mark.parametrize("legacy", [False, True])
def test_analysis_uses_recorded_index_and_legacy_default(tmp_path, legacy):
    from .analysis_steps import noise_psd
    config, _ = synthetic_run(tmp_path, demod_idx=3 if legacy else 2)
    if legacy:
        config["parameters"].pop("DEMOD3_IDX")
        path = tmp_path / "raw/response_data.npz"
        with np.load(path) as data:
            old_response = {key: data[key] for key in data.files if key != "demod_idx"}
        np.savez(path, **old_response)
    (tmp_path / "results").mkdir()
    assert noise_psd(tmp_path, config, 400)["n_avg"] == 2
    path = tmp_path / "raw/noise_D3_0000.npz"
    with np.load(path) as data:
        noise = dict(data)
    np.savez(path, **(noise | {"demod_idx": 1}))
    with pytest.raises(ValueError, match="运行配置指定"):
        noise_psd(tmp_path, config, 400)


def test_non_three_analysis_keeps_acquired_signal_chain(tmp_path):
    config, _ = synthetic_run(tmp_path, demod_idx=2)
    config["signal_chain"] = "Demod0 Y -> AuxOut4 -> internal -> RF demodulator (index 2) X"
    (tmp_path / "experiment_config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    result = analyze(tmp_path)
    assert result["rf_demod_index"] == 2
    assert result["signal_chain"] == config["signal_chain"]


def test_response_index_must_match_saved_configuration(tmp_path):
    from .analysis_steps import load_inputs
    config, _ = synthetic_run(tmp_path, demod_idx=2)
    config["parameters"]["DEMOD3_IDX"] = 1
    with pytest.raises(ValueError, match="响应记录的 RF 解调器索引"):
        load_inputs(tmp_path, config)


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


def write_bandwidth(directory, *, gamma=300.0, center=3000.0, demod_idx=3, mode="frequency_sweep"):
    """在合成运行上叠加 A13B 带宽扫频数据并切换线宽模式。"""
    config = yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf-8"))
    config["parameters"]["LINEWIDTH_MODE"] = mode
    (directory / "experiment_config.yaml").write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
    frequency = np.linspace(2000.0, 4000.0, 21)
    r = 0.02 + 0.8 * gamma ** 2 / ((frequency - center) ** 2 + gamma ** 2)
    x = 0.01 + 0.3 * 100.0 ** 2 / ((frequency - 3100.0) ** 2 + 100.0 ** 2)
    np.savez(directory / "raw/bandwidth_data.npz", frequency_Hz=frequency,
             demod3_x_V=x, demod3_y_V=np.sqrt(np.maximum(r ** 2 - x ** 2, 0)), demod3_r_V=r,
             demod3_x_std_V=np.zeros_like(x), demod3_y_std_V=np.zeros_like(x), demod3_r_std_V=np.zeros_like(x),
             z_rf_amplitude_vpp=0.1, z_burst_phase_deg=37.0, z_rf_freq_Hz=3000.0,
             duration_s=0.2, actual_rate_Sa_s=1000.0, demod_idx=demod_idx)
    return config


def test_bandwidth_fit_overrides_dispersive_hwhm(tmp_path):
    synthetic_run(tmp_path)
    write_bandwidth(tmp_path)
    result = analyze(tmp_path)
    correction = result["frequency_response_correction"]
    assert correction["source"] == "bandwidth_lorentzian_fit"
    assert correction["hwhm_Hz"] == pytest.approx(300.0, rel=1e-3)
    assert result["bandwidth_fit"]["enabled"] is True
    assert result["bandwidth_fit"]["response_component"] == "R"
    assert "R 均值" in correction["source_detail"]
    assert result["bandwidth_fit"]["center_Hz"] == pytest.approx(3000.0, rel=1e-3)
    assert result["bandwidth_fit"]["n_points"] == 21
    with np.load(tmp_path / "results/sensitivity.npz") as data:
        # 扫频线宽取代色散等效 HWHM（80 nT × LARMOR_PER_NT）。
        assert data["f_larmor_Hz"] == pytest.approx(300.0, rel=1e-3)
        assert data["bandwidth_hwhm_Hz"] == pytest.approx(300.0, rel=1e-3)
        assert np.isnan(data["gamma_nT"])
    for name in ("bandwidth_fit.png", "bandwidth_fit.pdf"):
        assert (tmp_path / "results" / name).stat().st_size > 100


@pytest.mark.parametrize("change", ["missing_r", "partial", "shape", "non_finite"])
def test_bandwidth_requires_complete_finite_r_data(tmp_path, change):
    from .analysis_steps import fit_bandwidth
    synthetic_run(tmp_path)
    config = write_bandwidth(tmp_path)
    path = tmp_path / "raw/bandwidth_data.npz"
    with np.load(path) as data:
        fields = dict(data)
    if change == "missing_r":
        fields.pop("demod3_r_V")
    elif change == "partial":
        for key in ("frequency_Hz", "demod3_r_V"):
            fields[key] = fields[key][:-1]
    elif change == "shape":
        fields["demod3_r_V"] = fields["demod3_r_V"][:, None]
    else:
        fields["demod3_r_V"][0] = np.nan
    np.savez(path, **fields)
    with pytest.raises(ValueError, match="缺少 R|不完整"):
        fit_bandwidth(tmp_path, config)


def test_bandwidth_analysis_and_plot_do_not_require_x_diagnostic(tmp_path):
    synthetic_run(tmp_path)
    write_bandwidth(tmp_path)
    path = tmp_path / "raw/bandwidth_data.npz"
    with np.load(path) as data:
        fields = {key: data[key] for key in data.files if key != "demod3_x_V"}
    np.savez(path, **fields)
    result = analyze(tmp_path)
    assert result["bandwidth_fit"]["response_component"] == "R"
    assert result["bandwidth_fit"]["hwhm_Hz"] == pytest.approx(300.0, rel=1e-3)
    assert (tmp_path / "results/bandwidth_fit.png").is_file()


def test_old_x_bandwidth_analysis_version_invalidates_cached_stages(tmp_path):
    from .analysis import AnalysisWorkflow
    synthetic_run(tmp_path)
    write_bandwidth(tmp_path)
    analyze(tmp_path)
    path = tmp_path / "analysis_state.json"
    saved = json.loads(path.read_text(encoding="utf-8"))
    saved["fingerprint"]["analysis_version"] = "direct-aw-x-stages-v1"
    path.write_text(json.dumps(saved), encoding="utf-8")
    ctx = AnalysisWorkflow(tmp_path)
    assert all(state["status"] == "stale" for state in ctx.states.values())


def test_bandwidth_mode_requires_sweep_data(tmp_path):
    synthetic_run(tmp_path)
    config = yaml.safe_load((tmp_path / "experiment_config.yaml").read_text(encoding="utf-8"))
    config["parameters"]["LINEWIDTH_MODE"] = "frequency_sweep"
    (tmp_path / "experiment_config.yaml").write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError, match="带宽扫频"):
        analyze(tmp_path)


def test_bandwidth_fit_quality_failure_is_not_silently_ignored(tmp_path):
    synthetic_run(tmp_path)
    # HWHM 不小于扫描跨度一半，拟合质量门槛拒绝且不回退色散结果。
    write_bandwidth(tmp_path, gamma=1500.0)
    with pytest.raises(ValueError, match="洛伦兹拟合质量不合格"):
        analyze(tmp_path)


def test_dispersive_mode_ignores_bandwidth_data(tmp_path):
    synthetic_run(tmp_path)
    write_bandwidth(tmp_path, mode="dispersive_equivalent")
    result = analyze(tmp_path)
    correction = result["frequency_response_correction"]
    assert correction["source"] == "dispersive_gamma"
    assert correction["hwhm_Hz"] == pytest.approx(80.0 * LARMOR_PER_NT, rel=1e-5)
    assert result["bandwidth_fit"]["enabled"] is False
    with np.load(tmp_path / "results/sensitivity.npz") as data:
        assert data["f_larmor_Hz"] == pytest.approx(80.0 * LARMOR_PER_NT, rel=1e-5)
        assert np.isnan(data["bandwidth_hwhm_Hz"])
    assert not (tmp_path / "results/bandwidth_fit.png").exists()


def test_numeric_slope_fallback():
    result = fit_response_curve(np.array([-2.0, 0, 2]), np.array([-1.0, 0, 1.0]))
    assert result["method"] == "numeric_gradient"
    assert result["success"] is False
    assert result["slope_V_per_nT"] == 0.5
    assert result["failure_reason"]


@pytest.mark.parametrize("change", [{"schema_version": 1}, {"schema_version": 6}, {"schema_version": 5}, {"completion_status": "failed"}, {"xy_phase_calibration": {"converged": False}}])
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
