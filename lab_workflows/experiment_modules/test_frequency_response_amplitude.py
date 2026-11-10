"""以独立复数传递函数验收幅度频响，避免被测模型自行生成真值。"""

import numpy as np
import pytest

from lab_workflows.analysis.noise_spectrum_separation import lorentzian_vs_control
from .mx_y_rf_sensitivity.analysis_core import fit_lorentzian_response, lorentzian_response
from .rf_sensitivity_constxy_frequency.analysis_algorithms import fit_lorentzian
from .noise_spectrum_xy_demod3_r.analysis_core import _fit_profiles, lorentzian_r_response
from .xy_direct_aw_dc_calibration import analysis_steps as dc_steps


@pytest.mark.parametrize("offset,span", [(0.0, 1200.0), (0.03, 1200.0), (0.2, 720.0)])
def test_shared_amplitude_fit_recovers_physical_gamma_and_width_definitions(offset, span):
    gamma, center, amplitude = 120.0, 10000.0, 1.7
    frequency = np.linspace(center - span, center + span, 41)
    response = offset + amplitude * np.abs(1.0 / (1.0 + 1j * (frequency - center) / gamma))
    fit = fit_lorentzian_response(frequency, response)
    assert fit.success, fit.rejection_reasons
    np.testing.assert_allclose(fit.parameters, [amplitude, gamma, center, offset], rtol=1e-5, atol=1e-8)
    levels = lorentzian_response(center + np.array([gamma, np.sqrt(3.0) * gamma]), amplitude, gamma, center, offset)
    np.testing.assert_allclose(levels - offset, amplitude * np.array([1 / np.sqrt(2.0), 0.5]), rtol=1e-12)
    metadata = fit.to_dict()
    assert metadata["model_type"] == "sqrt_lorentzian_amplitude"
    assert metadata["gamma_definition"] == "power_hwhm_and_amplitude_3db_half_width"
    assert metadata["amplitude_hwhm_hz"] == pytest.approx(np.sqrt(3.0) * gamma, rel=1e-5)
    assert metadata["amplitude_fwhm_hz"] == pytest.approx(2 * np.sqrt(3.0) * gamma, rel=1e-5)


def test_constxy_single_fit_uses_amplitude_not_power_and_keeps_legacy_width_keys():
    gamma, center, amplitude = 75.0, 1000.0, 0.8
    frequency = np.linspace(400.0, 1600.0, 41)
    response = amplitude * np.abs(1 / (1 + 1j * (frequency - center) / gamma))
    fit = fit_lorentzian(frequency, response)
    assert fit["success"], fit["message"]
    assert fit["hwhm_Hz"] == pytest.approx(gamma, rel=1e-5)
    assert fit["fwhm_Hz"] == pytest.approx(2 * gamma, rel=1e-5)
    assert fit["amplitude_fwhm_Hz"] == pytest.approx(2 * np.sqrt(3) * gamma, rel=1e-5)
    expected = amplitude * np.abs(1 / (1 + 1j * (fit["fit_curve_freqs"] - center) / gamma))
    np.testing.assert_allclose(fit["fit_curve_values"], expected, rtol=1e-5)
    assert fit["residual_max"] < 1e-7


def test_demod3_r_uses_complex_modulus_while_psd_keeps_modulus_squared():
    control = np.linspace(200.0, 1800.0, 41)
    gamma, amplitude, baseline, offset, frequency = 100.0, 40.0, 0.03, 12.0, 1000.0
    transfer = (gamma + 1j * frequency) / ((gamma + 1j * frequency) ** 2 + (control + offset) ** 2)
    expected = baseline + amplitude * np.abs(transfer)
    np.testing.assert_allclose(lorentzian_r_response(control, gamma, amplitude, baseline, offset, frequency), expected, rtol=1e-12)
    # PSD 是功率谱密度，不应随平均 R 模型一起开平方。
    np.testing.assert_allclose(lorentzian_vs_control(control, gamma, amplitude, baseline, offset, frequency),
                               baseline + amplitude * np.abs(transfer) ** 2, rtol=1e-12)
    parameters, _, mask = _fit_profiles(expected[:, None], control, np.array([frequency]))
    assert mask[0]
    np.testing.assert_allclose(parameters[0], [gamma, amplitude, baseline, offset], rtol=1e-4, atol=1e-6)


def test_direct_aw_dc_sqrt_fallback_recovers_gamma_without_changing_bloch(monkeypatch):
    gamma, center, amplitude, baseline = 90.0, 3000.0, 0.4, 0.02
    frequency = np.linspace(2100.0, 3900.0, 41)
    response = baseline + amplitude * np.abs(1 / (1 + 1j * (frequency - center) / gamma))
    fit = dc_steps.fit_lorentzian(frequency, response)
    assert fit["success"], fit["message"]
    assert fit["center_Hz"] == pytest.approx(center, abs=1e-4)
    assert fit["fwhm_Hz"] == pytest.approx(2 * gamma, rel=1e-5)
    assert fit["amplitude_fwhm_Hz"] == pytest.approx(2 * np.sqrt(3) * gamma, rel=1e-5)
    monkeypatch.setattr(dc_steps, "fit_bloch_response", lambda *args: {"success": False, "rmse_V": np.nan})
    selected = dc_steps.fit_response_peak(frequency, response)
    assert selected["model"] == "single_sqrt_lorentzian"
    assert selected["fwhm_Hz"] == pytest.approx(2 * gamma, rel=1e-5)


def test_direct_aw_dc_persists_new_width_definitions(tmp_path, monkeypatch):
    """用最小合成单电压频扫验证最终 YAML/JSON 不丢失宽度解释。"""
    import json
    import yaml
    import matplotlib.pyplot as plt

    gamma, center = 90.0, 3000.0
    raw = tmp_path / "raw"
    raw.mkdir()
    (tmp_path / "experiment_config.yaml").write_text("{}", encoding="utf-8")
    records = []
    for index, frequency in enumerate(np.linspace(2100.0, 3900.0, 21)):
        name = f"point_{index:02d}.npz"
        response = 0.02 + 0.4 * abs(1 / (1 + 1j * (frequency - center) / gamma))
        np.savez(raw / name, xy_envelope_voltage_V=0.5, z_freq_Hz=frequency,
                 demod3_r_vector_mean_V=response)
        records.append(dict(xy_envelope_voltage_V=0.5, z_freq_Hz=frequency, subdir=".", file=name))
    (raw / "frequency_index.json").write_text(json.dumps(records), encoding="utf-8")
    monkeypatch.setattr(dc_steps, "fit_bloch_response", lambda *args: {"success": False, "rmse_V": np.nan})
    monkeypatch.setattr(dc_steps, "save_figure", lambda figure, *args, **kwargs: plt.close(figure))
    dc_steps.evaluate(tmp_path)
    for name, loader in (("analysis.json", json.loads), ("analysis.yaml", yaml.safe_load)):
        row = loader((tmp_path / "results" / name).read_text(encoding="utf-8"))["per_voltage"][0]
        assert row["gamma_Hz"] == pytest.approx(gamma, rel=1e-5)
        assert row["width_definition"] == "power_fwhm_and_amplitude_3db_full_width"
        assert row["amplitude_fwhm_Hz"] == pytest.approx(2 * np.sqrt(3) * gamma, rel=1e-5)
        assert row["fit_model"] == "single_sqrt_lorentzian"
        assert row["model_formula"] == "C + A*gamma/sqrt((f-f0)^2+gamma^2)"


def test_constxy_npz_persists_amplitude_model_and_widths(tmp_path):
    """以单路最小合成频扫检查真实 NPZ 与 JSON/YAML 的拟合定义一致。"""
    import json
    import yaml
    from .rf_sensitivity_constxy_frequency.models import RFConstXYFrequencyParams
    from .rf_sensitivity_constxy_frequency import analysis_steps as steps, run_format

    params = RFConstXYFrequencyParams(acquisition_mode="demod_r", fit_model="single_lorentzian")
    gamma, center = 75.0, 1000.0
    points = []
    for index, frequency in enumerate(np.linspace(400.0, 1600.0, 21)):
        response = 0.8 * abs(1 / (1 + 1j * (frequency - center) / gamma))
        points.append(dict(idx=index, freq_Hz=frequency, is_baseline=False, data={
            "demod_r_r_vector_mean_V": response, "demod_r_r_mean_scalar_V": response,
            "demod_r_r_std_scalar_V": 0.0, "demod_r_x_mean_V": response, "demod_r_y_mean_V": 0.0,
            "demod_r_phase_vector_rad": 0.0, "demod_r_phase_vector_deg": 0.0}))
    (tmp_path / "results").mkdir()
    config = run_format.initial_metadata(params) | {"experiment_type": run_format.DATA_TYPE}
    steps.write_responses(tmp_path, points, params)
    steps.write_fits(tmp_path, config, params, points)
    with np.load(tmp_path / "results/frequency_response.npz", allow_pickle=False) as saved:
        assert saved["fit_model"].item() == "single_lorentzian"  # 已发布的选择键保持兼容。
        for name, loader in (("analysis.json", json.loads), ("analysis.yaml", yaml.safe_load)):
            fit = loader((tmp_path / "results" / name).read_text(encoding="utf-8"))["lorentzian_hardware_demod_r"]
            assert fit["success"]
            for key in ("model_type", "model", "width_definition"):
                assert saved[f"lorentzian_hardware_demod_r_{key}"].item() == fit[key]
            for key in ("amplitude_hwhm_Hz", "amplitude_fwhm_Hz", "amplitude_hwhm_unc_Hz", "amplitude_fwhm_unc_Hz"):
                assert saved[f"lorentzian_hardware_demod_r_{key}"].item() == pytest.approx(fit[key])
        assert saved["lorentzian_hardware_demod_r_hwhm_Hz"].item() == pytest.approx(gamma, rel=1e-5)
        assert saved["lorentzian_hardware_demod_r_amplitude_fwhm_Hz"].item() == pytest.approx(2 * np.sqrt(3) * gamma, rel=1e-5)
