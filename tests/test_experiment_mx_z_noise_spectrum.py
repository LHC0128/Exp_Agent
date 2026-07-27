"""Mx Z 直流控制噪声谱实验契约测试。"""

from __future__ import annotations

import numpy as np
import pytest
import yaml

from lab_workflows.analysis.noise_spectrum_separation import (
    fit_noise_separation,
    lorentzian_vs_control,
)
from lab_workflows.experiment_modules.mx_z_noise_spectrum.definition import (
    DEFINITION,
)
from lab_workflows.experiment_modules.mx_z_noise_spectrum.analysis import analyze
from lab_workflows.experiment_modules.mx_z_noise_spectrum.models import (
    MxZNoiseSpectrumParams,
)
from lab_workflows.experiment_modules.mx_z_noise_spectrum.scan import (
    build_scan_axes,
)
from lab_workflows.experiment_modules.mx_z_noise_spectrum.workflow import (
    _temperature_gated_acquire,
    safe_shutdown,
)
from lab_workflows.experiments.registry import get_experiment


def test_defaults_schema_and_registry_contract() -> None:
    params = MxZNoiseSpectrumParams()
    assert params.validate() == []
    assert get_experiment("mx-z-noise-spectrum") is DEFINITION
    assert DEFINITION.execution_mode == "typed_workflow"
    assert DEFINITION.data_type == "Mx_Z_Noise_Spectrum"
    fields = {item["name"]: item for item in DEFINITION.schema()["fields"]}
    assert fields["TARGET_DETUNING_POINTS"]["default"] == 500
    assert fields["ZERO_BIAS_REFERENCE_FREQUENCY_HZ"]["default"] == 90000.0
    assert fields["Z_CALIBRATION_HZ_PER_V"]["default"] == pytest.approx(
        24224.007001623544
    )
    assert fields["Z_CALIBRATION_INTERCEPT_HZ"]["default"] == pytest.approx(
        90302.74646653689
    )
    assert fields["TEMP_SWITCH_ON_SETTLE_S"]["default"] == 1.0


def test_scan_axis_strictly_uses_calibration_equation() -> None:
    params = MxZNoiseSpectrumParams()
    axes = build_scan_axes(params)
    detuning = axes["target_detuning_hz"]
    voltage = axes["z_voltage_v"]
    assert detuning.size == 500
    assert detuning[[0, -1]].tolist() == [0.0, 50000.0]
    assert voltage[0] == pytest.approx(
        (90000.0 - 90302.74646653689) / 24224.007001623544
    )
    assert voltage[-1] == pytest.approx(
        (140000.0 - 90302.74646653689) / 24224.007001623544
    )
    rebuilt = (
        params.z_calibration_hz_per_v * voltage
        + params.z_calibration_intercept_hz
        - params.zero_bias_reference_frequency_hz
    )
    np.testing.assert_allclose(rebuilt, detuning, rtol=0.0, atol=1e-9)


def test_requested_rate_must_cover_target_detuning() -> None:
    params = MxZNoiseSpectrumParams(requested_rate_sa_s=90000.0)
    assert any("Nyquist" in item for item in params.validate_model())


def test_temperature_gate_waits_after_restore_even_when_acquisition_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import lab_workflows.experiment_modules.mx_z_noise_spectrum.workflow as workflow

    events: list[tuple] = []
    monkeypatch.setattr(workflow, "check_cancelled", lambda: None)
    monkeypatch.setattr(
        workflow,
        "set_temperature_switch",
        lambda device, enabled, *, channel, settle_time=0.0: events.append(
            ("temperature", enabled, channel, settle_time)
        ),
    )
    monkeypatch.setattr(
        workflow,
        "_sleep",
        lambda seconds: events.append(("sleep", seconds)),
    )

    with pytest.raises(RuntimeError, match="DAQ failed"):
        _temperature_gated_acquire(
            MxZNoiseSpectrumParams(),
            {"temp_switch": object()},
            {"temp_switch": 2},
            acquire=lambda: (_ for _ in ()).throw(RuntimeError("DAQ failed")),
        )

    assert events == [
        ("temperature", False, 2, 0.0),
        ("sleep", 0.3),
        ("temperature", True, 2, 1.0),
    ]


class _ShutdownDG:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def set_burst_state(self, state: bool, *, channel: int) -> None:
        self.calls.append(("burst", channel, state))

    def set_mod_state(self, state: bool, *, channel: int) -> None:
        self.calls.append(("mod", channel, state))

    def setup_dc(self, value: float, *, channel: int) -> None:
        self.calls.append(("dc", channel, value))

    def set_output(self, state: bool, *, channel: int) -> None:
        self.calls.append(("output", channel, state))

    def setup_sine(self, frequency, amplitude, *, offset, phase, channel) -> None:
        self.calls.append(("sine", channel, frequency, amplitude, offset, phase))


class _TEC:
    def __init__(self) -> None:
        self.disconnected = False

    def disconnect(self) -> None:
        self.disconnected = True


def test_safe_shutdown_zeros_fields_and_preserves_pump() -> None:
    z_field = _ShutdownDG()
    xy_field = _ShutdownDG()
    pump = _ShutdownDG()
    temperature = _ShutdownDG()
    tec = _TEC()
    report = safe_shutdown(
        {
            "z_field": z_field,
            "xy_field": xy_field,
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
        MxZNoiseSpectrumParams(),
    )
    assert report.completed
    assert ("dc", 1, 0.0) in z_field.calls
    assert ("output", 1, False) in z_field.calls
    assert ("dc", 1, 0.0) in xy_field.calls
    assert ("dc", 2, 0.0) in xy_field.calls
    assert ("output", 2, False) in xy_field.calls
    assert ("dc", 2, 5.0) in temperature.calls
    assert ("output", 2, True) in temperature.calls
    assert ("dc", 2, 5.0) in pump.calls
    assert ("output", 2, True) in pump.calls
    assert any(call[0] == "sine" and call[2] == 100e6 for call in pump.calls)
    assert tec.disconnected


def test_shared_noise_separation_recovers_synthetic_parameters() -> None:
    control_axis = np.linspace(0.0, 2000.0, 101)
    frequency_axis = np.asarray([1000.0])
    psd = lorentzian_vs_control(
        control_axis,
        100.0,
        1.0,
        1.0e-6,
        0.0,
        frequency_axis[0],
    )[:, None]
    result = fit_noise_separation(
        psd,
        frequency_axis,
        control_axis,
        peak_margin_hz=500.0,
        gamma_guess_hz=100.0,
    )
    assert result.fit_mask.tolist() == [True]
    assert result.parameters[0, 0] == pytest.approx(100.0, rel=1e-4)
    assert result.s_beta[0] == pytest.approx(1.0, rel=1e-4)
    assert result.n_s1[0] == pytest.approx(1.0e-6, rel=1e-4)


def test_offline_analysis_uses_saved_actual_rate_and_strict_axis(tmp_path) -> None:
    run_dir = tmp_path / "mx_z_noise_run"
    raw_dir = run_dir / "raw"
    (run_dir / "results").mkdir(parents=True)
    raw_dir.mkdir()
    params = MxZNoiseSpectrumParams(
        target_detuning_stop_hz=500.0,
        target_detuning_points=10,
        requested_rate_sa_s=1000.0,
        welch_nperseg=100,
    )
    (run_dir / "experiment_config.yaml").write_text(
        yaml.safe_dump(
            {
                "experiment_id": "mx-z-noise-spectrum",
                "schema_version": 1,
                "parameters": params.to_external(),
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    rng = np.random.default_rng(20260720)
    detuning_axis = np.linspace(0.0, 500.0, 10)
    for index, detuning in enumerate(detuning_axis):
        z_voltage = params.z_voltage_for_detuning(float(detuning))
        time_s = np.arange(1000, dtype=float) / 1000.0
        r_v = (
            1.0
            + 1.0e-3 * np.sin(2.0 * np.pi * (50.0 + detuning / 20.0) * time_s)
            + rng.normal(scale=1.0e-4, size=time_s.size)
        )
        np.savez(
            raw_dir / f"waveform_Z{index:04d}.npz",
            time_s=time_s,
            r_v=r_v,
            point_index=np.int64(index),
            target_detuning_hz=np.float64(detuning),
            absolute_resonance_frequency_hz=np.float64(90000.0 + detuning),
            z_voltage_v=np.float64(z_voltage),
            actual_rate_sa_s=np.float64(1000.0),
        )

    result = analyze(run_dir)
    assert result["scan"]["completed_points"] == 10
    assert result["welch"]["actual_rate_sa_s"] == 1000.0
    psd = np.load(run_dir / "results" / "psd_matrix.npz")
    np.testing.assert_allclose(
        psd["calibrated_detuning_hz"],
        detuning_axis,
        rtol=0.0,
        atol=1e-6,
    )
    for filename in (
        "noise_spectra.npz",
        "noise_spectrum_2d.png",
        "analysis.yaml",
    ):
        assert (run_dir / "results" / filename).is_file()
