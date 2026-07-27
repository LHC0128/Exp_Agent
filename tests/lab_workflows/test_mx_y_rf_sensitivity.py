from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest
import yaml

from lab_workflows.experiment_modules.mx_y_rf_sensitivity.acquisition import (
    normalize_r_daq_results,
    summarize_r,
)
from lab_workflows.experiment_modules.mx_y_rf_sensitivity.analysis_core import (
    absolute_dispersive_response,
    amplitude_gamma_to_hz,
    average_welch_psd,
    detect_flat_sensitivity_band,
    dispersive_response,
    fit_absolute_dispersive_response,
    fit_dispersive_response,
    fit_lorentzian_response,
    lorentzian_response,
    sensitivity_spectrum,
)
from lab_workflows.experiment_modules.mx_y_rf_sensitivity.analysis import (
    SENSITIVITY_REFERENCE_FT_PER_SQRT_HZ,
    analyze,
)
from lab_workflows.experiment_modules.mx_y_rf_sensitivity.definition import (
    DEFINITION,
)
from lab_workflows.experiment_modules.mx_y_rf_sensitivity.models import (
    MxYRFParams,
)
from lab_workflows.experiment_modules.mx_y_rf_sensitivity.scan import (
    build_amplitude_axis,
    signed_amplitude_hardware,
)
from lab_workflows.experiment_modules.mx_y_rf_sensitivity.workflow import (
    _acquire_valid_r_point,
    _configure_reference_clocks,
    _set_y_rf_zero_off,
    safe_shutdown,
)
from lab_workflows.experiments.registry import get_experiment
from sensitivity_analysis.fitting import LARMOR_PER_NT


def test_default_params_and_registry_contract() -> None:
    params = MxYRFParams()
    assert params.validate() == []
    assert params.linewidth_mode == "amplitude_equivalent"
    assert params.pump_gate_voltage_v == 5.0
    assert params.pump_carrier_frequency_hz == 100e6
    assert params.r_bad_point_std_threshold_v == 0.01
    assert params.r_point_max_attempts == 3
    definition = get_experiment("mx-y-rf-sensitivity")
    assert definition is DEFINITION
    assert definition.execution_mode == "typed_workflow"
    assert any("Pump 光沿 Z" in note for note in definition.wiring_notes)
    assert definition.preflight_runner({}) == []


def test_schema_v1_phase_parameters_are_migrated_away() -> None:
    params = MxYRFParams.from_external(
        {
            "PHASE_CAL_AMPLITUDE_VPP": 0.1,
            "PHASE_CAL_SETTLE_TIME_S": 0.3,
            "PHASE_CAL_DURATION_S": 0.2,
            "PHASE_CAL_TOLERANCE_DEG": 1.0,
            "PHASE_CAL_MAX_ATTEMPTS": 5,
            "PHASE_CAL_MIN_DELTA_V": 1e-12,
        },
        schema_version=1,
    )
    assert params.r_bad_point_std_threshold_v == 0.01


def test_signed_amplitude_axis_and_mapping() -> None:
    axis = build_amplitude_axis(MxYRFParams())
    assert axis.size == 81
    assert axis[40] == pytest.approx(0.0, abs=1e-15)
    assert signed_amplitude_hardware(0.1) == (0.1, 0.0, True)
    assert signed_amplitude_hardware(-0.1) == (0.1, 180.0, True)
    assert signed_amplitude_hardware(0.0) == (0.0, 0.0, False)


def test_r_only_daq_normalization() -> None:
    result = SimpleNamespace(
        signal_name="sample.r",
        values=np.linspace(0.0, 1.0, 16),
    )
    payload = normalize_r_daq_results([result], 1000.0)
    assert set(payload) == {"time_s", "r"}
    assert payload["r"].size == 16
    summary = summarize_r(payload)
    assert set(summary) == {
        "r_mean_v",
        "r_scalar_mean_v",
        "r_std_v",
        "n_samples",
    }


def test_bad_r_point_is_saved_and_reacquired(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    import lab_workflows.experiment_modules.mx_y_rf_sensitivity.workflow as workflow

    payloads = iter(
        [
            {
                "time_s": np.arange(16) / 1000.0,
                "r": np.r_[np.zeros(8), np.ones(8)],
            },
            {
                "time_s": np.arange(16) / 1000.0,
                "r": np.full(16, 0.5),
            },
        ]
    )
    monkeypatch.setattr(
        workflow,
        "acquire_r",
        lambda *args, **kwargs: next(payloads),
    )
    monkeypatch.setattr(
        workflow,
        "_temperature_gated_acquire",
        lambda *args, acquire, **kwargs: acquire(),
    )
    params = MxYRFParams(
        r_bad_point_std_threshold_v=0.01,
        r_point_max_attempts=3,
    )
    summary, attempt, filename = _acquire_valid_r_point(
        params,
        SimpleNamespace(raw=tmp_path),
        {"hf2": object()},
        {},
        file_stem="amplitude_0001",
        metadata={"signed_amplitude_vpp": 0.1},
        settle_time_s=0.0,
        duration_s=0.1,
        actual_rate=1000.0,
        device_id="dev",
    )
    assert attempt == 1
    assert filename == "amplitude_0001_attempt_01.npz"
    assert summary["r_std_v"] == pytest.approx(0.0)
    with np.load(tmp_path / "amplitude_0001_attempt_00.npz") as first:
        assert int(first["quality_accepted"]) == 0
        assert set(first.files).isdisjoint({"x_v", "y_v"})
    with np.load(tmp_path / "amplitude_0001_attempt_01.npz") as second:
        assert int(second["quality_accepted"]) == 1


class _FakeClockDG:
    def __init__(self, *, ignore_setting: bool = False) -> None:
        self.source = "INT"
        self.ignore_setting = ignore_setting

    def set_ref_clock_source(self, source: str) -> None:
        if not self.ignore_setting:
            self.source = "EXT" if source.upper().startswith("EXT") else "INT"

    def get_ref_clock_source(self) -> str:
        return self.source


class _FakeClockHF2:
    def __init__(self) -> None:
        self.external = False

    def set_extclk(self, enabled: bool) -> None:
        self.external = enabled

    def get_extclk(self) -> bool:
        return self.external


def test_reference_clocks_follow_repository_profile() -> None:
    xy = _FakeClockDG()
    pump = _FakeClockDG()
    hf2 = _FakeClockHF2()
    mapping = {
        "rf_coil": {
            "instrument": "signal_generator",
            "model": "DG4000",
            "resource": "USB0::VENDOR::MODEL::XY_SERIAL::INSTR",
        },
        "Pump_laser_power": {
            "instrument": "signal_generator",
            "model": "DG900",
            "resource": "USB0::VENDOR::MODEL::LASER_SERIAL::INSTR",
        },
        "Pump_modulation": {
            "instrument": "signal_generator",
            "model": "DG4000",
            "resource": "USB0::VENDOR::MODEL::PUMP_SERIAL::INSTR",
        },
        "Temp_Switch": {
            "instrument": "signal_generator",
            "model": "DG900",
            "resource": "USB0::VENDOR::MODEL::TEMP_SERIAL::INSTR",
        },
        "lockin_r": {
            "instrument": "lockin_amplifier",
            "device_id": "HF2_SERIAL",
        },
    }
    result = _configure_reference_clocks(
        {
            "xy_field": xy,
            "laser": _FakeClockDG(),
            "pump_rf": pump,
            "temp_switch": _FakeClockDG(),
            "hf2": hf2,
        },
        mapping,
        profile={
            "__default__": "EXT",
            "XY_SERIAL": "EXT",
            "LASER_SERIAL": "EXT",
            "PUMP_SERIAL": "INT",
            "TEMP_SERIAL": "EXT",
            "HF2_SERIAL": "EXT",
        },
    )
    assert result == {
        "xy_field": {"target": "EXT", "actual": "EXT"},
        "laser": {"target": "EXT", "actual": "EXT"},
        "pump_rf": {"target": "INT", "actual": "INT"},
        "temp_switch": {"target": "EXT", "actual": "EXT"},
        "hf2": {"target": "EXT", "actual": "EXT"},
    }


def test_reference_clock_readback_mismatch_stops_configuration() -> None:
    mapping = {
        "rf_coil": {
            "instrument": "signal_generator",
            "model": "DG4000",
            "resource": "USB0::VENDOR::MODEL::XY_SERIAL::INSTR",
        },
        "Pump_modulation": {
            "instrument": "signal_generator",
            "model": "DG4000",
            "resource": "USB0::VENDOR::MODEL::PUMP_SERIAL::INSTR",
        },
        "lockin_r": {
            "instrument": "lockin_amplifier",
            "device_id": "HF2_SERIAL",
        },
    }
    with pytest.raises(RuntimeError, match="xy_field 时钟源设置失败"):
        _configure_reference_clocks(
            {
                "xy_field": _FakeClockDG(ignore_setting=True),
                "pump_rf": _FakeClockDG(),
                "hf2": _FakeClockHF2(),
            },
            mapping,
            profile={
                "__default__": "EXT",
                "XY_SERIAL": "EXT",
                "PUMP_SERIAL": "INT",
                "HF2_SERIAL": "EXT",
            },
        )


def test_dispersive_and_lorentzian_fits() -> None:
    rng = np.random.default_rng(7)
    amplitude = np.linspace(-0.2, 0.2, 81)
    x = dispersive_response(amplitude, 0.003, 0.055, 0.004, 0.02)
    x += rng.normal(scale=2e-4, size=x.size)
    dispersive_fit = fit_dispersive_response(amplitude, x)
    assert dispersive_fit.success
    assert dispersive_fit.gamma == pytest.approx(0.055, rel=0.05)
    assert dispersive_fit.center == pytest.approx(0.004, abs=0.003)

    frequency = np.linspace(8000.0, 12000.0, 201)
    response = lorentzian_response(frequency, 0.2, 350.0, 10050.0, 0.01)
    response += rng.normal(scale=1e-3, size=response.size)
    linewidth_fit = fit_lorentzian_response(frequency, response)
    assert linewidth_fit.success
    assert linewidth_fit.gamma == pytest.approx(350.0, rel=0.05)
    assert linewidth_fit.center == pytest.approx(10050.0, abs=20.0)


def test_absolute_dispersive_r_fit_after_bad_point_exclusion() -> None:
    rng = np.random.default_rng(11)
    amplitude = np.linspace(-0.2, 0.2, 81)
    response = absolute_dispersive_response(
        amplitude,
        0.198,
        0.096,
        0.0,
        -0.003,
    )
    response += rng.normal(scale=0.002, size=response.size)
    r_std = np.full(response.size, 0.001)
    r_std[[22, 47, 75]] = 0.2
    response[[22, 47, 75]] -= 0.3
    fit = fit_absolute_dispersive_response(
        amplitude[r_std <= 0.01],
        response[r_std <= 0.01],
    )
    assert fit.success
    assert fit.gamma == pytest.approx(0.096, rel=0.05)
    assert fit.center == pytest.approx(0.0, abs=0.003)
    assert abs(fit.amplitude) / fit.gamma**2 == pytest.approx(
        0.198 / 0.096**2,
        rel=0.08,
    )


def test_psd_uses_actual_rate_and_unit_conversion() -> None:
    sample_rate = 4096.0
    time_s = np.arange(4096) / sample_rate
    waveforms = [
        np.sin(2 * np.pi * 128.0 * time_s),
        np.sin(2 * np.pi * 128.0 * time_s + 0.3),
    ]
    frequency, psd = average_welch_psd(
        waveforms,
        [sample_rate, sample_rate],
    )
    assert frequency[np.argmax(psd)] == pytest.approx(128.0)

    result = sensitivity_spectrum(
        np.full_like(frequency, 4e-12),
        frequency,
        slope_signal_v_per_vpp=2.0,
        hwhm_hz=100.0,
        low_freq_skip_hz=3.0,
        y_rf_nt_per_vpp=5.0,
    )
    assert result["raw_vpp_per_sqrt_hz"][10] == pytest.approx(1e-6)
    assert result["raw_ft_per_sqrt_hz"][10] == pytest.approx(5.0)
    assert amplitude_gamma_to_hz(0.02, 5.0) == pytest.approx(
        0.1 * LARMOR_PER_NT
    )
    assert amplitude_gamma_to_hz(0.02, 0.0) is None


def test_flat_sensitivity_band_is_detected_from_spectrum_shape() -> None:
    rng = np.random.default_rng(31)
    frequency = np.arange(1.0, 2501.0)
    sensitivity = 300.0 * np.exp(
        rng.normal(scale=0.04, size=frequency.size)
    )
    low = frequency < 80.0
    high = frequency > 500.0
    sensitivity[low] *= np.exp((80.0 - frequency[low]) / 35.0)
    sensitivity[high] *= np.exp((frequency[high] - 500.0) / 700.0)
    sensitivity[[120, 275, 420, 880]] *= 8.0

    result = detect_flat_sensitivity_band(
        frequency,
        sensitivity,
        hwhm_hz=1000.0,
        minimum_frequency_hz=3.0,
    )

    assert result["success"]
    assert 20.0 <= result["band_hz"][0] <= 180.0
    assert 350.0 <= result["band_hz"][1] <= 700.0
    assert result["median"] == pytest.approx(300.0, rel=0.08)
    assert result["median_cv"] <= 0.03
    assert result["rise_sigma"] >= 3.0


def test_flat_sensitivity_band_rejects_monotonic_spectrum() -> None:
    frequency = np.arange(1.0, 2501.0)
    sensitivity = 100.0 * np.exp(frequency / 700.0)

    result = detect_flat_sensitivity_band(
        frequency,
        sensitivity,
        hwhm_hz=1000.0,
        minimum_frequency_hz=3.0,
    )

    assert not result["success"]
    assert not np.any(result["mask"])
    assert np.isnan(result["median"])
    assert result["reason"]


def test_analysis_generates_field_full_analysis_without_voltage_plot(tmp_path) -> None:
    run_dir = tmp_path / "run"
    raw_dir = run_dir / "raw"
    raw_dir.mkdir(parents=True)
    parameters = MxYRFParams(
        y_rf_nt_per_vpp=1517.79147,
        noise_n_avg=2,
    ).to_external()
    (run_dir / "experiment_config.yaml").write_text(
        yaml.safe_dump(
            {
                "experiment_id": "mx-y-rf-sensitivity",
                "schema_version": MxYRFParams.schema_version,
                "parameters": parameters,
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    amplitude = np.linspace(-0.2, 0.2, 81)
    response = absolute_dispersive_response(
        amplitude,
        0.1,
        0.05,
        0.0,
        0.01,
    )
    np.savez(
        raw_dir / "amplitude_scan.npz",
        signed_amplitude_vpp=amplitude,
        r_mean_v=response,
        r_std_v=np.full_like(amplitude, 1e-4),
    )
    rng = np.random.default_rng(23)
    for index in range(2):
        np.savez(
            raw_dir / f"noise_{index:03d}.npz",
            r_v=rng.normal(scale=1e-3, size=4096),
            actual_rate_sa_s=np.float64(4096.0),
        )

    result = analyze(run_dir)

    assert (run_dir / "results" / "full_analysis.png").is_file()
    assert not (run_dir / "results" / "sensitivity_voltage.png").exists()
    assert "full_analysis.png" in result["files"]
    assert "sensitivity_voltage.png" not in result["files"]
    assert result["plot_profile"] == "paper"
    assert "flat_detection" in result
    assert "candidate_count" in result["flat_detection"]
    assert result["sensitivity_reference_ft_per_sqrt_hz"] == pytest.approx(
        150.0
    )
    assert SENSITIVITY_REFERENCE_FT_PER_SQRT_HZ == pytest.approx(150.0)


class _FakeDG:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def set_burst_state(self, state: bool, *, channel: int) -> None:
        self.calls.append(("burst", channel, state))

    def set_mod_state(self, state: bool, *, channel: int) -> None:
        self.calls.append(("mod", channel, state))

    def setup_dc(self, value: float, *, channel: int) -> None:
        self.calls.append(("dc", channel, value))

    def setup_sine(
        self,
        frequency: float,
        amplitude: float,
        *,
        offset: float,
        phase: float,
        channel: int,
    ) -> None:
        self.calls.append(
            ("sine", channel, frequency, amplitude, offset, phase)
        )

    def set_output(self, state: bool, *, channel: int) -> None:
        self.calls.append(("output", channel, state))

    def write(self, command: str) -> None:
        self.calls.append(("write", command))


class _FakeTEC:
    def __init__(self) -> None:
        self.disconnected = False

    def disconnect(self) -> None:
        self.disconnected = True


def test_noise_acquisition_sets_y_rf_to_zero_dc_and_output_off() -> None:
    rf = _FakeDG()

    _set_y_rf_zero_off(rf, 2)

    assert rf.calls == [
        ("output", 2, False),
        ("dc", 2, 0.0),
        ("output", 2, False),
    ]


def test_safe_shutdown_preserves_pump_and_zeros_xy() -> None:
    xy = _FakeDG()
    pump = _FakeDG()
    temperature = _FakeDG()
    tec = _FakeTEC()
    report = safe_shutdown(
        {
            "xy_field": xy,
            "pump_rf": pump,
            "temp_switch": temperature,
            "tec": tec,
        },
        {
            "x_field": 1,
            "y_rf": 2,
            "pump_carrier": 1,
            "pump_gate": 2,
            "temp_switch": 2,
        },
        MxYRFParams(),
    )
    assert report.completed
    for channel in (1, 2):
        assert ("dc", channel, 0.0) in xy.calls
        assert ("output", channel, False) in xy.calls
    assert ("dc", 2, 5.0) in pump.calls
    assert ("output", 2, True) in pump.calls
    assert ("sine", 1, 100e6, 0.18, 0.0, 0.0) in pump.calls
    assert ("output", 1, True) in pump.calls
    assert ("dc", 2, 5.0) in temperature.calls
    assert ("output", 2, True) in temperature.calls
    assert tec.disconnected
    assert "Time_sequence" in report.preserved_outputs
