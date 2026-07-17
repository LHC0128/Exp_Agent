from __future__ import annotations

import inspect
from types import SimpleNamespace

import numpy as np
import pytest
import yaml

from lab_workflows.experiment_modules.mx_z_field_calibration.analysis import analyze
from lab_workflows.experiment_modules.mx_z_field_calibration.definition import DEFINITION
from lab_workflows.experiment_modules.mx_z_field_calibration.models import (
    MxZFieldCalibrationParams,
)
from lab_workflows.experiment_modules.mx_z_field_calibration.scan import (
    build_frequency_axis,
    build_z_axis,
    predicted_center_hz,
)
from lab_workflows.experiment_modules.mx_z_field_calibration.workflow import (
    _acquire_scan,
    _temperature_gated_acquire,
    safe_shutdown,
)
from lab_workflows.experiment_modules.mx_y_rf_sensitivity.analysis_core import (
    lorentzian_response,
)
from lab_workflows.experiments.registry import get_experiment


def configured_params(**overrides) -> MxZFieldCalibrationParams:
    return MxZFieldCalibrationParams(
        z_prediction_polarity="positive_increases_frequency",
        **overrides,
    )


def test_defaults_schema_and_registry_contract() -> None:
    params = MxZFieldCalibrationParams()
    assert any("Z_PREDICTION_POLARITY" in item for item in params.validate_model())
    params = configured_params()
    assert params.validate() == []
    assert get_experiment("mx-z-field-calibration") is DEFINITION
    assert DEFINITION.execution_mode == "typed_workflow"
    fields = {item["name"]: item for item in DEFINITION.schema()["fields"]}
    assert fields["FIXED_PARAMS.main_magnetic_field"]["default"] == 9.3
    assert fields["ZERO_BIAS_CENTER_FREQUENCY_HZ"]["default"] == 90000.0
    assert DEFINITION.schema()["schema_version"] == 2
    assert {
        "FIT_R_SQUARED_MIN",
        "FIT_CENTER_UNCERTAINTY_MAX_HZ",
        "FIT_RELATIVE_GAMMA_UNCERTAINTY_MAX",
        "MIN_VALID_Z_POINTS",
        "MIN_VALID_Z_SPAN_V",
    }.isdisjoint(fields)


def test_schema_v1_quality_thresholds_are_migrated_away() -> None:
    legacy = configured_params().to_external()
    legacy.update(
        {
            "FIT_R_SQUARED_MIN": 0.95,
            "FIT_CENTER_UNCERTAINTY_MAX_HZ": 100.0,
            "FIT_RELATIVE_GAMMA_UNCERTAINTY_MAX": 0.5,
            "MIN_VALID_Z_POINTS": 5,
            "MIN_VALID_Z_SPAN_V": 4.0,
        }
    )
    migrated = MxZFieldCalibrationParams.from_external(legacy, schema_version=1)
    assert migrated == configured_params()


def test_single_curve_diagnostics_do_not_reject_fit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    import lab_workflows.experiment_modules.mx_z_field_calibration.analysis as analysis
    from lab_workflows.experiment_modules.mx_y_rf_sensitivity.analysis_core import (
        FitResult,
    )

    run_dir = tmp_path / "run"
    summary_path = run_dir / "raw" / "z_000" / "frequency_scan.npz"
    summary_path.parent.mkdir(parents=True)
    frequency = np.linspace(0.0, 10.0, 11)
    np.savez(
        summary_path,
        frequency_hz=frequency,
        r_mean_v=np.ones_like(frequency),
        r_std_v=np.full_like(frequency, 1e-4),
    )

    def fake_fit(
        frequency_hz,
        response_v,
        *,
        r_squared_min,
        relative_gamma_uncertainty_max,
    ) -> FitResult:
        assert r_squared_min is None
        assert relative_gamma_uncertainty_max is None
        return FitResult(
            method="lorentzian",
            success=True,
            parameters=(1.0, 1.0, 5.0, 0.0),
            uncertainties=(1.0, 1000.0, 1e9, 1.0),
            r_squared=-10.0,
            rmse=1.0,
            relative_gamma_uncertainty=1000.0,
            rejection_reasons=(),
        )

    monkeypatch.setattr(analysis, "fit_lorentzian_response", fake_fit)
    curve = analysis._analyze_curve(
        configured_params(),
        run_dir,
        {
            "z_bias_v": 0.0,
            "predicted_center_hz": 5.0,
            "summary_file": "raw/z_000/frequency_scan.npz",
        },
    )
    assert curve["fit"]["success"]
    assert curve["fit"]["r_squared"] == -10.0
    assert curve["fit"]["center_uncertainty_hz"] == 1e9
    assert curve["fit"]["relative_gamma_uncertainty"] == 1000.0


def test_single_pass_z_axis_and_predicted_frequency() -> None:
    params = configured_params()
    assert build_z_axis(params).tolist() == [-3.0, -2.0, -1.0, 0.0, 1.0, 2.0, 3.0]
    assert predicted_center_hz(params, -3.0) == pytest.approx(
        90000.0 - 3.0 * 10621.690594509037
    )
    frequency = build_frequency_axis(params, -3.0)
    assert frequency.size == 81
    assert frequency[0] == pytest.approx(predicted_center_hz(params, -3.0) - 4000.0)
    assert frequency[-1] == pytest.approx(predicted_center_hz(params, -3.0) + 4000.0)


def test_decreasing_prediction_polarity() -> None:
    params = MxZFieldCalibrationParams(
        z_prediction_polarity="positive_decreases_frequency"
    )
    assert predicted_center_hz(params, 2.0) == pytest.approx(
        90000.0 - 2.0 * 10621.690594509037
    )


def test_acquisition_module_does_not_import_fitting() -> None:
    import lab_workflows.experiment_modules.mx_z_field_calibration.workflow as workflow

    source = inspect.getsource(workflow)
    assert "fit_lorentzian" not in source
    assert "lorentzian_response" not in source
    assert "analysis_core" not in source


def test_temperature_gate_restores_and_waits_after_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import lab_workflows.experiment_modules.mx_z_field_calibration.workflow as workflow

    events: list[tuple] = []
    monkeypatch.setattr(workflow, "check_cancelled", lambda: None)
    monkeypatch.setattr(
        workflow,
        "set_temperature_switch",
        lambda device, enabled, *, channel: events.append(("temperature", enabled, channel)),
    )
    monkeypatch.setattr(workflow, "_sleep", lambda seconds: events.append(("sleep", seconds)))
    monkeypatch.setattr(
        workflow,
        "_uncancellable_sleep",
        lambda seconds: events.append(("recovery", seconds)),
    )

    with pytest.raises(RuntimeError, match="DAQ failed"):
        _temperature_gated_acquire(
            configured_params(),
            {"temp_switch": object()},
            {"temp_switch": 2},
            acquire=lambda: (_ for _ in ()).throw(RuntimeError("DAQ failed")),
        )

    assert events == [
        ("temperature", False, 2),
        ("sleep", 0.1),
        ("sleep", 0.05),
        ("temperature", True, 2),
        ("recovery", 1.0),
    ]


class _ScanDG:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def setup_dc(self, value: float, *, channel: int) -> None:
        self.calls.append(("dc", channel, value))

    def set_output(self, state: bool, *, channel: int) -> None:
        self.calls.append(("output", channel, state))

    def set_frequency(self, value: float, *, channel: int) -> None:
        self.calls.append(("frequency", channel, value))

    def set_phase_adjust(self, value: float, *, channel: int) -> None:
        self.calls.append(("phase", channel, value))

    def set_amplitude(self, value: float, *, channel: int) -> None:
        self.calls.append(("amplitude", channel, value))


def test_setting_z_bias_has_no_dedicated_wait(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    import lab_workflows.experiment_modules.mx_z_field_calibration.workflow as workflow

    z_device = _ScanDG()
    rf_device = _ScanDG()
    run_root = tmp_path / "run"
    raw = run_root / "raw"
    raw.mkdir(parents=True)
    monkeypatch.setattr(workflow, "check_cancelled", lambda: None)
    monkeypatch.setattr(workflow, "build_z_axis", lambda params: np.asarray([-3.0]))
    monkeypatch.setattr(
        workflow,
        "build_frequency_axis",
        lambda params, z_bias: np.asarray([58000.0]),
    )
    monkeypatch.setattr(
        workflow.demod,
        "configure_oscillator",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        workflow,
        "_acquire_valid_r_point",
        lambda *args, **kwargs: (
            {"r_mean_v": 0.2, "r_scalar_mean_v": 0.2, "r_std_v": 0.001, "n_samples": 10},
            0,
            "frequency_0000_attempt_00.npz",
        ),
    )
    monkeypatch.setattr(
        workflow,
        "_sleep",
        lambda seconds: (_ for _ in ()).throw(AssertionError("不应等待 Z 稳定")),
    )
    _acquire_scan(
        configured_params(),
        SimpleNamespace(root=run_root, raw=raw),
        {"z_field": z_device, "xy_field": rf_device, "hf2": object()},
        {"z_field": 1, "y_rf": 2},
        1000.0,
        "dev",
    )
    assert z_device.calls[:2] == [("dc", 1, -3.0), ("output", 1, True)]


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


def test_safe_shutdown_zeros_z_and_y_rf() -> None:
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
        configured_params(),
    )
    assert report.completed
    assert ("dc", 1, 0.0) in z_field.calls
    assert ("output", 1, False) in z_field.calls
    assert ("dc", 2, 0.0) in xy_field.calls
    assert ("output", 2, False) in xy_field.calls
    assert ("dc", 2, 5.0) in temperature.calls
    assert ("output", 2, True) in temperature.calls
    assert tec.disconnected


def _write_synthetic_run(tmp_path, *, invalid_indices: set[int] | None = None):
    invalid_indices = invalid_indices or set()
    run_dir = tmp_path / "run"
    raw_dir = run_dir / "raw"
    raw_dir.mkdir(parents=True)
    params = configured_params()
    (run_dir / "experiment_config.yaml").write_text(
        yaml.safe_dump(
            {
                "experiment_id": "mx-z-field-calibration",
                "schema_version": params.schema_version,
                "parameters": params.to_external(),
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    z_axis = build_z_axis(params)
    summary_files: list[str] = []
    predicted: list[float] = []
    for index, z_bias in enumerate(z_axis):
        point_dir = raw_dir / f"z_{index:03d}"
        point_dir.mkdir()
        center = 90500.0 + 10450.0 * z_bias
        frequency = np.linspace(center - 4000.0, center + 4000.0, 81)
        response = lorentzian_response(frequency, 0.6, 1200.0, center, 0.1)
        if index in invalid_indices:
            response[:] = np.nan
        filename = f"raw/z_{index:03d}/frequency_scan.npz"
        np.savez(
            run_dir / filename,
            frequency_hz=frequency,
            r_mean_v=response,
            r_std_v=np.full(frequency.size, 1e-4),
        )
        summary_files.append(filename)
        predicted.append(predicted_center_hz(params, z_bias))
    np.savez(
        raw_dir / "z_scan_index.npz",
        z_bias_v=z_axis,
        predicted_center_hz=np.asarray(predicted),
        summary_file=np.asarray(summary_files, dtype=str),
    )
    return run_dir


def test_offline_analysis_recovers_linear_calibration(tmp_path) -> None:
    run_dir = _write_synthetic_run(tmp_path)
    result = analyze(run_dir)
    assert result["success"]
    assert result["K_Z_Hz_per_V"] == pytest.approx(10450.0, rel=1e-6)
    assert result["f_0V_Hz"] == pytest.approx(90500.0, rel=1e-6)
    assert (run_dir / "results" / "calibration_results.npz").is_file()
    assert (run_dir / "results" / "frequency_response_fits.png").is_file()


def test_analysis_accepts_two_valid_centers_without_span_gate(tmp_path) -> None:
    run_dir = _write_synthetic_run(tmp_path, invalid_indices={0, 1, 2, 3, 4})
    result = analyze(run_dir)
    assert result["success"]
    assert "valid_point_count" not in result
    assert "valid_z_span_v" not in result
    assert result["rejection_reasons"] == []
