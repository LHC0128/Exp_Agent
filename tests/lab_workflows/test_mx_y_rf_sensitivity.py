from __future__ import annotations

from pathlib import Path
import shutil
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import numpy as np
import pytest
import yaml

from lab_workflows.experiment_modules.mx_y_rf_sensitivity.acquisition import (
    normalize_r_daq_results,
    summarize_r,
)
from lab_workflows.experiment_modules.mx_y_rf_sensitivity.analysis_core import (
    adaptive_zero_point_absolute_linear_fit,
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
    _plot_full_analysis,
    analyze,
)
from lab_workflows.experiment_modules.mx_y_rf_sensitivity.definition import (
    DEFINITION,
)
from lab_workflows.experiment_modules.mx_y_rf_sensitivity.models import (
    MxYRFParams,
)
from lab_workflows.experiment_modules.mx_y_rf_sensitivity.point_analysis import (
    evaluate_mx_y_rf_point,
)
from lab_workflows.experiment_modules.mx_y_rf_sensitivity.scan import (
    build_amplitude_axis,
    signed_amplitude_hardware,
)
from lab_workflows.experiment_modules.mx_y_rf_sensitivity.workflow import (
    _acquire_valid_r_point,
    _configure_reference_clocks,
    _configure_temperature_control,
    _restore_response_acquisition_state,
    _set_y_rf_zero_off,
    safe_shutdown,
)
from lab_workflows.experiments.registry import get_experiment
from sensitivity_analysis.fitting import LARMOR_PER_NT


@pytest.fixture
def local_tmp_path() -> Path:
    root = Path(__file__).resolve().parents[2] / "data"
    path = root / f".test_mx_y_rf_sensitivity_{uuid4().hex}"
    path.mkdir(mode=0o777)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


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
    local_tmp_path: Path,
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
        lambda *args, set_y_rf, acquire, **kwargs: (
            set_y_rf(),
            acquire(),
        )[1],
    )
    params = MxYRFParams(
        r_bad_point_std_threshold_v=0.01,
        r_point_max_attempts=3,
    )
    summary, attempt, filename = _acquire_valid_r_point(
        params,
        SimpleNamespace(raw=local_tmp_path),
        {"hf2": object()},
        {},
        file_stem="amplitude_0001",
        metadata={"signed_amplitude_vpp": 0.1},
        set_y_rf=lambda: None,
        settle_time_s=0.0,
        duration_s=0.1,
        actual_rate=1000.0,
        device_id="dev",
    )
    assert attempt == 1
    assert filename == "amplitude_0001_attempt_01.npz"
    assert summary["r_std_v"] == pytest.approx(0.0)
    with np.load(
        local_tmp_path / "amplitude_0001_attempt_00.npz"
    ) as first:
        assert int(first["quality_accepted"]) == 0
        assert set(first.files).isdisjoint({"x_v", "y_v"})
    with np.load(
        local_tmp_path / "amplitude_0001_attempt_01.npz"
    ) as second:
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
        "Z_magnetic_field": {
            "instrument": "signal_generator",
            "model": "DG4000",
            "resource": "USB0::VENDOR::MODEL::Z_SERIAL::INSTR",
        },
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
            "z_field": _FakeClockDG(),
            "xy_field": xy,
            "laser": _FakeClockDG(),
            "pump_rf": pump,
            "temp_switch": _FakeClockDG(),
            "hf2": hf2,
        },
        mapping,
        profile={
            "__default__": "EXT",
            "Z_SERIAL": "EXT",
            "XY_SERIAL": "EXT",
            "LASER_SERIAL": "EXT",
            "PUMP_SERIAL": "INT",
            "TEMP_SERIAL": "EXT",
            "HF2_SERIAL": "EXT",
        },
    )
    assert result == {
        "z_field": {"target": "EXT", "actual": "EXT"},
        "xy_field": {"target": "EXT", "actual": "EXT"},
        "laser": {"target": "EXT", "actual": "EXT"},
        "pump_rf": {"target": "INT", "actual": "INT"},
        "temp_switch": {"target": "EXT", "actual": "EXT"},
        "hf2": {"target": "EXT", "actual": "EXT"},
    }


def test_reference_clock_readback_mismatch_stops_configuration() -> None:
    mapping = {
        "Z_magnetic_field": {
            "instrument": "signal_generator",
            "model": "DG4000",
            "resource": "USB0::VENDOR::MODEL::Z_SERIAL::INSTR",
        },
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
                "z_field": _FakeClockDG(),
                "xy_field": _FakeClockDG(ignore_setting=True),
                "pump_rf": _FakeClockDG(),
                "hf2": _FakeClockHF2(),
            },
            mapping,
            profile={
                "__default__": "EXT",
                "Z_SERIAL": "EXT",
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


def test_adaptive_zero_point_slope_uses_balanced_nearest_measured_points() -> None:
    amplitude = np.linspace(-0.2, 0.2, 21)
    response = 4.5 * np.abs(amplitude - 0.003) + 0.01
    response[[0, -1]] += 10.0

    result = adaptive_zero_point_absolute_linear_fit(
        amplitude,
        response,
        center=0.003,
    )

    assert result["success"]
    assert result["n_points"] == 5
    assert result["left_point_count"] >= 2
    assert result["right_point_count"] >= 2
    assert result["slope"] == pytest.approx(4.5)
    assert result["r_squared"] == pytest.approx(1.0)
    selected = amplitude[result["mask"]]
    assert selected.min() >= -0.041
    assert selected.max() <= 0.041


def test_adaptive_zero_point_slope_rejects_unbalanced_scan() -> None:
    amplitude = np.linspace(0.0, 0.2, 11)
    response = amplitude.copy()

    result = adaptive_zero_point_absolute_linear_fit(
        amplitude,
        response,
        center=0.0,
    )

    assert not result["success"]
    assert any(
        "零点左侧有效点数" in reason
        for reason in result["rejection_reasons"]
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


def test_rejected_response_fit_can_continue_for_diagnostics(
    local_tmp_path: Path,
) -> None:
    raw_dir = local_tmp_path / "raw"
    raw_dir.mkdir()
    params = MxYRFParams(noise_n_avg=1)
    amplitude = np.linspace(-0.2, 0.2, 21)
    np.savez(
        raw_dir / "amplitude_scan.npz",
        signed_amplitude_vpp=amplitude,
        r_mean_v=np.full_like(amplitude, 0.0034),
        r_std_v=np.full_like(amplitude, 1e-4),
    )
    rng = np.random.default_rng(42)
    np.savez(
        raw_dir / "noise_000.npz",
        r_v=rng.normal(scale=1e-4, size=4096),
        actual_rate_sa_s=np.float64(4096.0),
    )

    strict = evaluate_mx_y_rf_point(raw_dir, params)
    diagnostic = evaluate_mx_y_rf_point(
        raw_dir,
        params,
        include_rejected_fit_diagnostics=True,
    )

    assert not strict["valid"]
    assert "sensitivity" not in strict
    assert not diagnostic["valid"]
    assert "幅度色散拟合质量不合格" in diagnostic["invalid_reasons"][0]
    assert "sensitivity" in diagnostic


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


def test_full_analysis_marks_detected_flat_median_as_sensitivity(
    monkeypatch: pytest.MonkeyPatch,
    local_tmp_path: Path,
) -> None:
    from matplotlib.axes import Axes

    horizontal_lines: list[tuple[float, str | None]] = []
    original_axhline = Axes.axhline

    def record_axhline(
        self,
        y=0,
        *args,
        **kwargs,
    ):
        horizontal_lines.append((float(y), kwargs.get("label")))
        return original_axhline(self, y, *args, **kwargs)

    monkeypatch.setattr(Axes, "axhline", record_axhline)
    amplitude = np.linspace(-0.2, 0.2, 5)
    frequency = np.asarray([0.0, 10.0, 20.0, 30.0])
    filename = _plot_full_analysis(
        results_dir=local_tmp_path,
        params=MxYRFParams(y_rf_nt_per_vpp=1000.0),
        amplitude_vpp=amplitude,
        r_mean_v=absolute_dispersive_response(
            amplitude,
            0.1,
            0.05,
            0.0,
            0.01,
        ),
        fit_mask=np.ones(amplitude.size, dtype=bool),
        bad_point_mask=np.zeros(amplitude.size, dtype=bool),
        response_fit=SimpleNamespace(
            parameters=(0.1, 0.05, 0.0, 0.01),
            r_squared=0.99,
            center=0.0,
        ),
        primary_slope=40.0,
        frequency_hz=frequency,
        sensitivity={
            "corrected_ft_per_sqrt_hz": np.asarray(
                [500.0, 321.0, 322.0, 450.0]
            ),
            "raw_ft_per_sqrt_hz": np.asarray(
                [480.0, 300.0, 301.0, 400.0]
            ),
            "flat_mask": np.asarray([False, True, True, False]),
            "flat_median_ft_per_sqrt_hz": np.float64(321.5),
        },
        hwhm_hz=None,
    )

    assert filename == "full_analysis.png"
    assert (local_tmp_path / filename).is_file()
    assert horizontal_lines == [
        (
            pytest.approx(321.5),
            "Sensitivity (flat median): 321.5 fT/√Hz",
        )
    ]
    assert all(
        label is None or "Reference" not in label
        for _, label in horizontal_lines
    )


def test_analysis_generates_field_full_analysis_without_voltage_plot(
    local_tmp_path: Path,
) -> None:
    run_dir = local_tmp_path / "run"
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
    assert (
        run_dir / "results" / "full_analysis_zero_point.png"
    ).is_file()
    assert (
        run_dir / "results" / "sensitivity_zero_point.npz"
    ).is_file()
    assert not (run_dir / "results" / "sensitivity_voltage.png").exists()
    assert "full_analysis.png" in result["files"]
    assert "full_analysis_zero_point.png" in result["files"]
    assert "sensitivity_zero_point.npz" in result["files"]
    assert "sensitivity_voltage.png" not in result["files"]
    assert result["plot_profile"] == "paper"
    assert "flat_detection" in result
    assert result["zero_point_method"]["valid"]
    assert result["zero_point_method"]["linear_fit"]["n_points"] == 5
    assert "candidate_count" in result["flat_detection"]
    assert "sensitivity_reference_ft_per_sqrt_hz" not in result


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


class _FakeHF2:
    def demod_path(self, index: int) -> str:
        return f"/dev/demods/{index}"

    def get_double(self, path: str) -> float:
        assert path == "/dev/demods/0/phaseshift"
        return 12.5


def test_response_state_is_restored_after_noise_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import lab_workflows.experiment_modules.mx_y_rf_sensitivity.workflow as workflow

    rf = _FakeDG()
    temperature = _FakeDG()
    oscillator_configs: list[object] = []
    demodulator_configs: list[object] = []
    monkeypatch.setattr(workflow, "_sleep", lambda _duration: None)
    monkeypatch.setattr(
        workflow,
        "_uncancellable_sleep",
        lambda _duration: None,
    )
    monkeypatch.setattr(
        workflow.demod,
        "configure_oscillator",
        lambda hf2, config: oscillator_configs.append(config),
    )

    def fake_configure_demodulator(hf2, config):
        demodulator_configs.append(config)
        return 899.465

    monkeypatch.setattr(
        workflow.demod,
        "configure_demodulator",
        fake_configure_demodulator,
    )
    params = MxYRFParams(
        y_rf_frequency_hz=90000.0,
        frequency_rf_amplitude_vpp=0.05,
        response_rate_sa_s=1000.0,
        response_time_constant_s=0.001,
        response_demod_order=4,
    )

    actual_rate = _restore_response_acquisition_state(
        params,
        {
            "xy_field": rf,
            "hf2": _FakeHF2(),
            "temp_switch": temperature,
        },
        {"y_rf": 2, "temp_switch": 1},
    )

    assert actual_rate == pytest.approx(899.465)
    assert rf.calls == [
        ("burst", 2, False),
        ("mod", 2, False),
        ("sine", 2, 90000.0, 0.05, 0.0, 0.0),
        ("output", 2, False),
    ]
    assert temperature.calls == [
        ("dc", 1, 0.0),
        ("output", 1, True),
        ("dc", 1, 5.0),
        ("output", 1, True),
    ]
    assert len(oscillator_configs) == 1
    assert oscillator_configs[0].frequency == pytest.approx(90000.0)
    assert len(demodulator_configs) == 1
    config = demodulator_configs[0]
    assert config.rate == pytest.approx(1000.0)
    assert config.time_constant == pytest.approx(0.001)
    assert config.order == 4
    assert config.phase == pytest.approx(12.5)


def test_noise_acquisition_sets_y_rf_to_zero_dc_and_output_off() -> None:
    rf = _FakeDG()

    _set_y_rf_zero_off(rf, 2)

    assert rf.calls == [
        ("output", 2, False),
        ("dc", 2, 0.0),
        ("output", 2, False),
    ]


def test_y_rf_setting_precedes_temperature_gated_wait_and_acquisition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import lab_workflows.experiment_modules.mx_y_rf_sensitivity.workflow as workflow

    events: list[str] = []
    monkeypatch.setattr(
        workflow,
        "set_temperature_switch",
        lambda _device, enabled, *, channel: events.append(
            f"temp_{'on' if enabled else 'off'}"
        ),
    )
    monkeypatch.setattr(
        workflow,
        "_sleep",
        lambda duration: events.append(f"sleep_{duration}"),
    )
    monkeypatch.setattr(
        workflow,
        "_uncancellable_sleep",
        lambda duration: events.append(f"sleep_{duration}"),
    )

    payload = workflow._temperature_gated_acquire(
        MxYRFParams(
            temp_switch_off_lead_s=0.2,
            temp_switch_on_lag_s=0.3,
        ),
        {"temp_switch": object()},
        {"temp_switch": 2},
        set_y_rf=lambda: events.append("set_y_rf"),
        settle_time_s=0.4,
        acquire=lambda: events.append("acquire") or {"r": np.ones(1)},
    )

    assert payload["r"].tolist() == [1.0]
    assert events == [
        "set_y_rf",
        "temp_off",
        "sleep_0.2",
        "sleep_0.4",
        "acquire",
        "temp_on",
        "sleep_0.3",
    ]


def test_temperature_switch_is_not_toggled_when_y_rf_setting_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import lab_workflows.experiment_modules.mx_y_rf_sensitivity.workflow as workflow

    events: list[str] = []
    monkeypatch.setattr(
        workflow,
        "set_temperature_switch",
        lambda _device, enabled, *, channel: events.append(
            f"temp_{'on' if enabled else 'off'}"
        ),
    )
    monkeypatch.setattr(workflow, "_sleep", lambda _duration: None)
    monkeypatch.setattr(
        workflow,
        "_uncancellable_sleep",
        lambda _duration: events.append("on_lag"),
    )

    def fail_to_set_y_rf() -> None:
        events.append("set_y_rf")
        raise RuntimeError("Y RF 设置失败")

    with pytest.raises(RuntimeError, match="Y RF 设置失败"):
        workflow._temperature_gated_acquire(
            MxYRFParams(),
            {"temp_switch": object()},
            {"temp_switch": 2},
            set_y_rf=fail_to_set_y_rf,
            settle_time_s=0.0,
            acquire=lambda: {"r": np.ones(1)},
        )

    assert events == ["set_y_rf"]


def test_temperature_switch_is_controlled_without_tec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import lab_workflows.experiment_modules.mx_y_rf_sensitivity.workflow as workflow

    temperature = _FakeDG()
    wait_calls: list[tuple] = []
    monkeypatch.setattr(
        workflow,
        "wait_for_temperature_stable",
        lambda *args, **kwargs: wait_calls.append((args, kwargs)),
    )

    actual_temperature = _configure_temperature_control(
        MxYRFParams(),
        {"temp_switch": temperature},
        {"temp_switch": 2},
        control_tec=False,
    )

    assert actual_temperature is None
    assert temperature.calls == [
        ("dc", 2, 5.0),
        ("output", 2, True),
    ]
    assert wait_calls == []


def test_safe_shutdown_preserves_pump_and_zeros_xy() -> None:
    z = _FakeDG()
    xy = _FakeDG()
    pump = _FakeDG()
    temperature = _FakeDG()
    tec = _FakeTEC()
    report = safe_shutdown(
        {
            "z_field": z,
            "xy_field": xy,
            "pump_rf": pump,
            "temp_switch": temperature,
            "tec": tec,
        },
        {
            "z_field": 1,
            "x_field": 1,
            "y_rf": 2,
            "pump_carrier": 1,
            "pump_gate": 2,
            "temp_switch": 2,
        },
        MxYRFParams(),
    )
    assert report.completed
    assert ("dc", 1, 0.0) in z.calls
    assert ("output", 1, False) in z.calls
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


def test_configure_outputs_zeros_z_field_before_mx_working_point(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import lab_workflows.experiment_modules.mx_y_rf_sensitivity.workflow as workflow

    z_field = MagicMock()
    devices = {
        "z_field": z_field,
        "xy_field": MagicMock(),
        "laser": MagicMock(),
        "gs200": MagicMock(),
        "pump_rf": MagicMock(),
        "temp_switch": MagicMock(),
        "hf2": MagicMock(),
    }
    channels = {
        "z_field": 1,
        "x_field": 1,
        "y_rf": 2,
        "pump_laser": 1,
        "probe_laser": 2,
        "pump_carrier": 1,
        "pump_gate": 2,
        "temp_switch": 2,
    }
    monkeypatch.setattr(workflow, "_configure_reference_clocks", lambda *a: {})
    monkeypatch.setattr(
        workflow,
        "_temperature_gated_acquire",
        lambda *args, set_y_rf, acquire, **kwargs: (
            set_y_rf(),
            acquire(),
        )[1],
    )
    monkeypatch.setattr(
        workflow, "_configure_temperature_control", lambda *a, **k: None
    )
    monkeypatch.setattr(
        workflow, "_configure_response_demodulator", lambda *a: 1000.0
    )
    monkeypatch.setattr(
        workflow.demod, "configure_signal_input", lambda *a, **k: None
    )

    actual_rate, clock_sources = workflow._configure_outputs(
        MxYRFParams(),
        devices,
        channels,
        {"main_magnetic_field": {"source_function": "CURRent"}},
    )

    assert actual_rate == pytest.approx(1000.0)
    assert clock_sources == {}
    z_field.set_burst_state.assert_called_once_with(False, channel=1)
    z_field.set_mod_state.assert_called_once_with(False, channel=1)
    z_field.setup_dc.assert_called_once_with(0.0, channel=1)
    z_field.set_output.assert_called_once_with(False, channel=1)


def test_safe_shutdown_without_tec_still_restores_temperature_switch() -> None:
    temperature = _FakeDG()

    report = safe_shutdown(
        {"temp_switch": temperature},
        {"temp_switch": 2},
        MxYRFParams(),
    )

    assert report.completed
    assert report.disconnect_errors == ()
    assert temperature.calls == [
        ("dc", 2, 5.0),
        ("output", 2, True),
    ]
