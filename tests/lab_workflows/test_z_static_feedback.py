"""静态标定、误差对齐、带限修正和理论量程的无硬件验证。"""
from types import SimpleNamespace

import numpy as np
import pytest

from lab_workflows.experiment_modules.z_aw_closed_loop_waveform_correction.static_feedback import (
    align_cycle, average_complete_cycles, fit_static_current, periodic_lowpass,
    prepare_feedback, rms, scope_range,
)
from lab_workflows.experiment_modules.z_aw_closed_loop_waveform_correction.models import ZAWClosedLoopWaveformCorrectionParams as Params
from lab_workflows.common import find_project_root


@pytest.mark.parametrize("gain", [0.004, -0.004])
def test_static_fit_preserves_signed_slope_and_intercept(gain):
    calibration = SimpleNamespace(payload={"curves": [
        {"z_bias_v": u, "current_a": gain * u + 0.00003} for u in [0.5, 1, 2, 1, 0.5]
    ]})
    fitted = fit_static_current(calibration)
    assert fitted.gain_a_per_v == pytest.approx(gain)
    assert fitted.intercept_a == pytest.approx(0.00003)
    assert fitted.r_squared == pytest.approx(1)


def test_real_static_source_is_sufficient_without_frequency_response():
    prepared = prepare_feedback(find_project_root(), Params())
    assert prepared.fit.gain_a_per_v == pytest.approx(0.005751042748629649)
    assert prepared.fit.voltage_v.size == 7
    np.testing.assert_allclose(prepared.fit.gain_a_per_v * prepared.initial.voltage_v +
                               prepared.fit.intercept_a, prepared.target_current_a, atol=1e-17)
    voltage = prepared.target_current_a * prepared.calibration.sense_resistor_ohm
    scale, offset = scope_range(voltage, 8, 1.5)
    assert scale == 0.5
    assert np.ptp(voltage) * 1.5 <= scale * 8
    assert offset == pytest.approx(-(voltage.min() + voltage.max()) / 2)


def test_alignment_recovers_fractional_delay_without_normalizing_amplitude():
    n = 1024
    x = np.arange(n)
    target = 0.001 * np.sin(2 * np.pi * x / n) + 0.0002 * np.cos(6 * np.pi * x / n)
    delay = 27.3
    measured = 0.8 * (0.001 * np.sin(2 * np.pi * (x - delay) / n) +
                      0.0002 * np.cos(6 * np.pi * (x - delay) / n)) + 0.0001
    aligned, shift = align_cycle(measured, target)
    assert shift == pytest.approx(delay, abs=0.06)
    assert aligned.mean() == pytest.approx(0.0001)
    assert rms(aligned - 0.0001) / rms(target) == pytest.approx(0.8, abs=2e-5)
    assert rms(target - aligned) / rms(target) > 0.2


def test_equal_alignment_prefers_zero_for_constant_waveform():
    aligned, shift = align_cycle(np.ones(64) * 2, np.ones(64))
    assert shift == 0
    np.testing.assert_array_equal(aligned, np.ones(64) * 2)


def test_average_uses_all_complete_cycles_and_discards_partial_edges():
    period = 0.01
    time = np.arange(-0.25, 3.5, 0.001) * period
    current = np.sin(2 * np.pi * time / period)
    current += np.floor(time / period) * 0.1
    grid = np.arange(1000) * period / 1000
    averaged = average_complete_cycles(time, current, grid, period)
    np.testing.assert_allclose(averaged, np.sin(2 * np.pi * grid / period) + 0.1, atol=7e-4)
    with pytest.raises(ValueError, match="完整"):
        average_complete_cycles(time[:100], current[:100], grid, period)


def test_lowpass_preserves_dc_phase_and_cutoff_bin():
    n = 512
    phase = 2 * np.pi * np.arange(n) / n
    dt = (1 / 20000) / n * (1 - 1e-14)
    wanted = 0.03 + np.cos(phase + 0.4) + 0.2 * np.sin(2 * phase)
    error = wanted + 0.4 * np.cos(5 * phase)
    np.testing.assert_allclose(periodic_lowpass(error, dt, 40000), wanted, atol=2e-14)


def test_scope_range_rounds_up_and_centers_asymmetric_target():
    assert scope_range(np.array([-0.4, 1.2]), 8, 1.5) == pytest.approx((0.5, -0.4))
    assert scope_range(np.array([-0.8, 0.8]), 8, 1) == pytest.approx((0.2, 0))
    with pytest.raises(ValueError, match="超过"):
        scope_range(np.array([-100., 100.]), 8, 1.5)


def test_old_parameters_migrate_to_static_only_schema():
    params = Params.from_external({"CURRENT_FREQUENCY_RESPONSE_SOURCE_RUN": "missing",
                                  "TIME_DOMAIN_CUTOFF_HZ": 0, "TARGET_SHAPE_NRMSE": 0.8,
                                  "SENSE_RESISTOR_OHM": 0, "CORRECTION_METHOD": "frequency_domain",
                                  "SCOPE_INITIAL_SCALE": 0.25}, schema_version=9)
    assert params.error_cutoff_hz == 40000
    assert params.target_relative_rms == 0.03
    assert params.validate() == []
    names = set(params.to_external())
    assert "CURRENT_FREQUENCY_RESPONSE_SOURCE_RUN" not in names
    assert "Z_CALIBRATION_SOURCE_RUN" not in names
    assert "SCOPE_INITIAL_SCALE" not in names
    assert params.scope_headroom_factor == 1.5
