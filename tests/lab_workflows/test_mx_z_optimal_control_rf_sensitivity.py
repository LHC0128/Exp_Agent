from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import shutil
from types import SimpleNamespace
from uuid import uuid4

import numpy as np
import pytest
import yaml

from lab_workflows.experiment_modules.mx_y_rf_sensitivity.analysis_core import (
    absolute_dispersive_response,
)
from lab_workflows.experiment_modules.mx_y_rf_sensitivity.acquisition import (
    normalize_rxy_daq_results,
)
from lab_workflows.experiment_modules.mx_z_optimal_control_rf_sensitivity.analysis import (
    analyze,
)
from lab_workflows.experiment_modules.mx_z_optimal_control_rf_sensitivity.definition import (
    DEFINITION,
)
from lab_workflows.experiment_modules.mx_z_optimal_control_rf_sensitivity.models import (
    MxZOptimalControlRFParams,
)
from lab_workflows.experiment_modules.mx_z_optimal_control_rf_sensitivity.phase import (
    detect_complex_phase_outliers,
    fit_phase_scan,
    fit_quadrature_phase_scan,
    inside_absolute_sine,
    paired_phase_order,
)
from lab_workflows.experiment_modules.mx_z_optimal_control_rf_sensitivity.phase_plot import (
    plot_phase_calibration,
)
from lab_workflows.experiment_modules.mx_z_optimal_control_rf_sensitivity.sources import (
    MAX_DG4000_ARB_POINTS,
    build_applied_control,
    load_theory_control,
    load_z_calibration,
)
from lab_workflows.experiment_modules.mx_z_optimal_control_rf_sensitivity.workflow import (
    _acquire_phase_scan,
    _configure_control_output,
    _configure_main_field,
    _configure_y_rf_output,
    _rearm_y_rf,
    _save_source_snapshot,
    safe_shutdown,
)
from lab_workflows.experiments.registry import get_experiment


@pytest.fixture
def local_tmp_path() -> Path:
    root = Path(__file__).resolve().parents[2] / "data"
    path = root / f".test_mx_z_optimal_control_{uuid4().hex}"
    path.mkdir(mode=0o777)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def test_defaults_registry_and_real_sources() -> None:
    root = Path(__file__).resolve().parents[2]
    params = MxZOptimalControlRFParams.from_yaml(
        root
        / "params"
        / "experiments"
        / "mx-z-optimal-control-rf-sensitivity.yaml"
    )
    assert params.validate(root) == []
    assert params.y_rf_frequency_hz == pytest.approx(30000.0)
    assert params.main_magnetic_field_ma == pytest.approx(0.01)
    assert params.x_dc_field_v == pytest.approx(0.01)
    assert params.y_rf_offset_v == pytest.approx(0.01)
    assert params.linewidth_mode == "amplitude_equivalent"
    definition = get_experiment("mx-z-optimal-control-rf-sensitivity")
    assert definition is DEFINITION
    assert definition.execution_mode == "typed_workflow"
    assert definition.preflight_runner({}) == []
    main_field_schema = next(
        field
        for field in definition.schema_provider()["fields"]
        if field["name"] == "FIXED_PARAMS.main_magnetic_field"
    )
    assert main_field_schema["default"] == pytest.approx(0.01)
    assert main_field_schema["unit"] == "mA"
    assert main_field_schema["group"] == "basic"
    schema_fields = {
        field["name"]: field for field in definition.schema_provider()["fields"]
    }
    assert schema_fields["FIXED_PARAMS.X_magnetic_field"]["default"] == pytest.approx(
        0.01
    )
    assert schema_fields["FIXED_PARAMS.Y_magnetic_field"]["default"] == pytest.approx(
        0.01
    )

    theory = load_theory_control(
        Path(params.control_results_root),
        params.control_version,
    )
    calibration = load_z_calibration(
        root,
        params.z_calibration_source_run,
    )
    applied = build_applied_control(
        theory,
        calibration,
        params.control_scale,
        output_vpp=params.z_aw_output_vpp,
        output_offset_v=params.z_aw_output_offset_v,
    )
    assert theory.time_s.size == 10000
    assert theory.time_s.size <= MAX_DG4000_ARB_POINTS
    assert theory.repeat_frequency_hz == pytest.approx(30000.0)
    assert calibration.slope_hz_per_v == pytest.approx(
        25157.23790615295
    )
    assert len(calibration.analysis_sha256) == 64
    assert applied.minimum_v == pytest.approx(-2.2597704241192624)
    assert applied.maximum_v == pytest.approx(2.2598362031060795)
    assert applied.amplitude_vpp == pytest.approx(6.0)
    assert applied.offset_v == pytest.approx(0.0)
    assert applied.output_minimum_v == pytest.approx(-3.0)
    assert applied.output_maximum_v == pytest.approx(3.0)
    assert applied.max_abs_normalized == pytest.approx(
        applied.maximum_v / 3.0
    )
    assert np.max(np.abs(applied.normalized)) <= 1.0


def test_invalid_control_version_is_rejected() -> None:
    params = MxZOptimalControlRFParams(control_version="../v1")
    errors = params.validate()
    assert any("vN 格式" in error for error in errors)


def test_noise_rf_defaults_and_gui_schema_preserve_legacy_behavior() -> None:
    schema = {field["name"]: field for field in DEFINITION.schema_provider()["fields"]}
    assert schema["NOISE_RF_ENABLED"]["default"] is False
    assert schema["NOISE_RF_AMPLITUDE_VPP"]["default"] == pytest.approx(0.002)
    assert schema["NOISE_RF_AMPLITUDE_VPP"]["unit"] == "Vpp"
    params = MxZOptimalControlRFParams.from_external({}, schema_version=4)
    assert params.noise_rf_enabled is False
    assert params.noise_rf_amplitude_vpp == pytest.approx(0.002)
    configured = MxZOptimalControlRFParams.from_external(
        {"NOISE_RF_ENABLED": True, "NOISE_RF_AMPLITUDE_VPP": 0.013},
        schema_version=4,
    )
    assert configured.noise_rf_enabled is True
    assert configured.noise_rf_amplitude_vpp == pytest.approx(0.013)
    for experiment_id in (
        "mx-z-optimal-control-xy-rf-sensitivity",
        "mx-z-optimal-control-xy-noise-spectrum",
        "mx-y-optimal-control-rf-frequency-response",
    ):
        names = {field["name"] for field in get_experiment(experiment_id).schema()["fields"]}
        assert "NOISE_RF_ENABLED" not in names
        assert "NOISE_RF_AMPLITUDE_VPP" not in names


def test_zero_phase_cal_rf_amplitude_enables_residual_mode() -> None:
    params = MxZOptimalControlRFParams(phase_cal_rf_amplitude_vpp=0.0)
    assert params.validate() == []


def test_schema_v2_migration_preserves_xy_zero_output() -> None:
    params = MxZOptimalControlRFParams.from_external({}, schema_version=2)

    assert params.x_dc_field_v == pytest.approx(0.0)
    assert params.y_rf_offset_v == pytest.approx(0.0)


def test_quadrature_phase_scan_requires_complete_opposite_pairs() -> None:
    params = MxZOptimalControlRFParams(
        phase_scan_start_deg=0.0,
        phase_scan_stop_deg=170.0,
        phase_scan_step_deg=10.0,
    )
    errors = params.validate()
    assert any("360° 周期" in error for error in errors)


def test_source_snapshot_contains_checksums(
    local_tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]
    params = MxZOptimalControlRFParams()
    theory = load_theory_control(
        Path(params.control_results_root),
        params.control_version,
    )
    calibration = load_z_calibration(
        root,
        params.z_calibration_source_run,
    )
    applied = build_applied_control(
        theory,
        calibration,
        1.0,
        output_vpp=4.0,
        output_offset_v=0.0,
    )

    files = _save_source_snapshot(
        local_tmp_path,
        theory,
        calibration,
        applied,
    )
    manifest = yaml.safe_load(
        (local_tmp_path / "source_manifest.yaml").read_text(encoding="utf-8")
    )

    assert "raw/source_manifest.yaml" in files
    assert (
        manifest["files"]["source_optimal_control_waveform.csv"]["sha256"]
        == theory.waveform_sha256
    )
    assert (
        manifest["files"]["source_z_calibration_analysis.yaml"]["sha256"]
        == calibration.analysis_sha256
    )
    assert manifest["control_conversion"]["minimum_v"] == pytest.approx(
        applied.minimum_v
    )


def test_fixed_aw_range_rejects_unrepresentable_control() -> None:
    root = Path(__file__).resolve().parents[2]
    params = MxZOptimalControlRFParams()
    theory = load_theory_control(
        Path(params.control_results_root),
        params.control_version,
    )
    calibration = load_z_calibration(
        root,
        params.z_calibration_source_run,
    )
    with pytest.raises(ValueError, match="超出固定输出范围"):
        build_applied_control(
            theory,
            calibration,
            1.0,
            output_vpp=1.0,
            output_offset_v=0.0,
        )


def test_phase_fit_selects_primary_peak_and_rejects_flat_data() -> None:
    phase = np.arange(0.0, 360.0, 10.0)
    rng = np.random.default_rng(17)
    response = inside_absolute_sine(phase, 0.01, 0.2, 25.0)
    response += rng.normal(scale=2e-4, size=phase.size)
    primary, diagnostic = fit_phase_scan(
        phase,
        response,
        r_squared_min=0.85,
        amplitude_sigma_min=3.0,
    )
    assert primary.success
    assert primary.r_squared > 0.99
    assert primary.selected_phase_deg == pytest.approx(115.0, abs=1.0)
    assert diagnostic.model == "C + A*abs(sin(phi-phi0))"

    flat_primary, _ = fit_phase_scan(
        phase,
        np.full_like(phase, 0.1),
        r_squared_min=0.85,
        amplitude_sigma_min=3.0,
    )
    assert not flat_primary.success
    assert flat_primary.rejection_reasons


def test_quadrature_fit_recovers_phase_when_response_exceeds_baseline() -> None:
    phase = np.arange(0.0, 360.0, 10.0)
    ordered_phase = paired_phase_order(phase)
    baseline = 0.6 + 0.8j
    baseline_direction = baseline / abs(baseline)
    response = (
        baseline
        + 1.5
        * np.cos(np.deg2rad(ordered_phase - 27.0))
        * baseline_direction
    )

    fit, arrays = fit_quadrature_phase_scan(
        ordered_phase,
        response.real,
        response.imag,
        r_squared_min=0.85,
        amplitude_sigma_min=3.0,
    )

    assert fit.success
    assert fit.selected_phase_deg == pytest.approx(27.0, abs=1e-8)
    assert fit.baseline_r_v == pytest.approx(1.0)
    assert fit.in_phase_amplitude_v == pytest.approx(1.5)
    assert fit.r_squared == pytest.approx(1.0)
    assert fit.quadrature_at_selected_v == pytest.approx(0.0, abs=1e-12)
    assert arrays["paired_phase_deg"].size == 18


def test_complex_phase_outlier_detector_finds_stable_trigger_failures() -> None:
    phase = paired_phase_order(np.arange(0.0, 360.0, 10.0))
    baseline = 0.6 + 0.8j
    response = baseline + 0.4 * np.exp(
        1j * np.deg2rad(phase - 35.0)
    )
    response[phase == 110.0] += 1.2 - 0.5j
    response[phase == 240.0] += -0.9 + 0.7j

    detection = detect_complex_phase_outliers(
        phase,
        response.real,
        response.imag,
        np.full(phase.size, 2e-4),
        sigma_threshold=6.0,
    )

    assert set(detection.outlier_phase_deg) == {110.0, 240.0}
    assert detection.threshold_v > 0.0


def test_normalize_rxy_daq_results_keeps_synchronized_finite_samples() -> None:
    results = [
        SimpleNamespace(
            signal_name="sample.r",
            values=np.asarray([1.0, 2.0, np.nan, 4.0] * 3),
        ),
        SimpleNamespace(
            signal_name="sample.x",
            values=np.asarray([0.1, 0.2, 0.3, 0.4] * 3),
        ),
        SimpleNamespace(
            signal_name="sample.y",
            values=np.asarray([0.5, 0.6, 0.7, 0.8] * 3),
        ),
    ]

    payload = normalize_rxy_daq_results(results, 1000.0)

    assert payload["r"].size == 9
    assert payload["r"].shape == payload["x"].shape == payload["y"].shape
    assert np.all(np.isfinite(payload["r"]))
    assert payload["time_s"][-1] == pytest.approx(0.008)


def test_rejected_online_phase_fit_still_saves_diagnostic_plot(
    local_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_dir = local_tmp_path / "raw"
    results_dir = local_tmp_path / "results"
    raw_dir.mkdir()
    results_dir.mkdir()
    run_dir = SimpleNamespace(raw=raw_dir, results=results_dir)
    params = MxZOptimalControlRFParams()

    def fake_acquire(*args, file_stem: str, **kwargs):
        return (
            {
                "r_scalar_mean_v": 0.1,
                "r_std_v": 1e-4,
                "x_mean_v": 0.1,
                "x_std_v": 1e-4,
                "y_mean_v": 0.0,
                "y_std_v": 1e-4,
                "complex_std_v": 2e-4,
            },
            1,
            f"{file_stem}_attempt_01.npz",
    )

    monkeypatch.setattr(
        "lab_workflows.experiment_modules."
        "mx_z_optimal_control_rf_sensitivity.workflow."
        "_acquire_valid_rxy_point",
        fake_acquire,
    )

    with pytest.raises(RuntimeError, match="Y RF 正交相位拟合不合格"):
        _acquire_phase_scan(
            params,
            run_dir,
            {"xy_field": _FakeDG()},
            {"y_rf": 2},
            actual_rate=1000.0,
            device_id="dev-test",
        )

    payload = yaml.safe_load(
        (results_dir / "phase_calibration.yaml").read_text(encoding="utf-8")
    )
    assert payload["success"] is False
    assert (raw_dir / "phase_scan.npz").is_file()
    assert (results_dir / "phase_calibration.png").is_file()


def test_quadrature_phase_plot_includes_demod_r(
    local_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_dir = local_tmp_path / "raw"
    results_dir = local_tmp_path / "results"
    raw_dir.mkdir()
    results_dir.mkdir()
    phase = np.arange(0.0, 360.0, 10.0)
    radians = np.deg2rad(phase)
    x_mean = 0.6 + 0.1 * np.cos(radians)
    y_mean = 0.8 + 0.1 * np.sin(radians)
    r_mean = np.hypot(x_mean, y_mean)
    pair_phase = phase[: phase.size // 2]
    np.savez(
        raw_dir / "phase_scan.npz",
        scanned_phase_deg=phase,
        r_mean_v=r_mean,
        r_std_v=np.full_like(phase, 1e-4),
        x_mean_v=x_mean,
        x_std_v=np.full_like(phase, 1e-4),
        y_mean_v=y_mean,
        y_std_v=np.full_like(phase, 1e-4),
        paired_phase_deg=pair_phase,
        pair_in_phase_v=np.cos(np.deg2rad(pair_phase)),
        pair_quadrature_v=np.sin(np.deg2rad(pair_phase)),
        cross_phase_reacquired=np.zeros(phase.size, dtype=bool),
    )
    payload = {
        "mode": "y_rf_quadrature_phase_calibration",
        "fit_accepted": True,
        "quadrature_fit": {
            "fit_cos_coefficient_v": 1.0,
            "fit_sin_coefficient_v": 0.0,
            "quadrature_cos_coefficient_v": 0.0,
            "quadrature_sin_coefficient_v": 1.0,
            "selected_phase_deg": 0.0,
            "r_squared": 1.0,
        },
    }
    captured_labels: list[str] = []

    def capture_figure(fig, path):
        captured_labels.extend(
            text.get_text() for text in fig.axes[0].get_legend().get_texts()
        )
        return Path(path)

    monkeypatch.setattr(
        "lab_workflows.experiment_modules."
        "mx_z_optimal_control_rf_sensitivity.phase_plot.save_figure",
        capture_figure,
    )

    plot_phase_calibration(raw_dir, results_dir, payload)

    assert {"Demod R", "Demod X", "Demod Y"} <= set(captured_labels)


def test_online_quadrature_phase_scan_uses_paired_order_and_dummy(
    local_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_dir = local_tmp_path / "raw"
    results_dir = local_tmp_path / "results"
    raw_dir.mkdir()
    results_dir.mkdir()
    run_dir = SimpleNamespace(raw=raw_dir, results=results_dir)
    params = MxZOptimalControlRFParams()
    baseline = 0.6 + 0.8j
    baseline_direction = baseline / abs(baseline)
    acquired_phases: list[float] = []

    def fake_acquire(*args, file_stem: str, metadata, **kwargs):
        phase = float(metadata["scanned_phase_deg"])
        acquired_phases.append(phase)
        response = (
            baseline
            + 0.4
            * np.cos(np.deg2rad(phase - 35.0))
            * baseline_direction
        )
        return (
            {
                "r_scalar_mean_v": float(abs(response)),
                "r_std_v": 1e-4,
                "x_mean_v": float(response.real),
                "x_std_v": 1e-4,
                "y_mean_v": float(response.imag),
                "y_std_v": 1e-4,
                "complex_std_v": 2e-4,
            },
            0,
            f"{file_stem}_attempt_00.npz",
    )

    monkeypatch.setattr(
        "lab_workflows.experiment_modules."
        "mx_z_optimal_control_rf_sensitivity.workflow."
        "_acquire_valid_rxy_point",
        fake_acquire,
    )

    selected, payload = _acquire_phase_scan(
        params,
        run_dir,
        {"xy_field": _FakeDG()},
        {"y_rf": 2},
        actual_rate=1000.0,
        device_id="dev-test",
    )

    assert selected == pytest.approx(35.0)
    assert payload["mode"] == "y_rf_quadrature_phase_calibration"
    assert payload["fit_accepted"] is True
    assert payload["phase_scan"]["pair_count"] == 18
    assert acquired_phases[:5] == [0.0, 0.0, 180.0, 10.0, 190.0]
    with np.load(raw_dir / "phase_scan.npz") as data:
        assert "x_mean_v" in data
        assert "pair_in_phase_v" in data
        assert data["paired_phase_deg"].size == 18
    assert (results_dir / "phase_calibration.png").is_file()


def test_online_phase_scan_reacquires_cross_phase_outliers_once(
    local_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_dir = local_tmp_path / "raw"
    results_dir = local_tmp_path / "results"
    raw_dir.mkdir()
    results_dir.mkdir()
    run_dir = SimpleNamespace(raw=raw_dir, results=results_dir)
    params = MxZOptimalControlRFParams()
    baseline = 0.6 + 0.8j
    baseline_direction = baseline / abs(baseline)
    calls: list[tuple[float, str]] = []

    def fake_acquire(*args, file_stem: str, metadata, **kwargs):
        phase = float(metadata["scanned_phase_deg"])
        calls.append((phase, file_stem))
        response = (
            baseline
            + 0.4
            * np.cos(np.deg2rad(phase - 35.0))
            * baseline_direction
        )
        if (
            phase in {110.0, 240.0}
            and "cross_retry" not in file_stem
        ):
            response += 1.2 - 0.8j
        return (
            {
                "r_scalar_mean_v": float(abs(response)),
                "r_std_v": 1e-4,
                "x_mean_v": float(response.real),
                "x_std_v": 1e-4,
                "y_mean_v": float(response.imag),
                "y_std_v": 1e-4,
                "complex_std_v": 2e-4,
            },
            0,
            f"{file_stem}_attempt_00.npz",
        )

    monkeypatch.setattr(
        "lab_workflows.experiment_modules."
        "mx_z_optimal_control_rf_sensitivity.workflow."
        "_acquire_valid_rxy_point",
        fake_acquire,
    )

    selected, payload = _acquire_phase_scan(
        params,
        run_dir,
        {"xy_field": _FakeDG()},
        {"y_rf": 2},
        actual_rate=1000.0,
        device_id="dev-test",
    )

    report = payload["cross_phase_reacquisition"]
    assert selected == pytest.approx(35.0)
    assert payload["fit_accepted"] is True
    assert set(
        report["initial_detection"]["outlier_phase_deg"]
    ) == {110.0, 240.0}
    assert report["final_detection"]["outlier_phase_deg"] == []
    assert set(report["reacquired_phase_deg"]) == {110.0, 240.0}
    assert sum("cross_retry" in stem for _, stem in calls) == 2
    with np.load(raw_dir / "phase_scan.npz") as data:
        retried_phase = data["scanned_phase_deg"][
            data["cross_phase_reacquired"].astype(bool)
        ]
        assert set(retried_phase.tolist()) == {110.0, 240.0}
        assert all(
            "cross_retry" in str(value)
            for value in data["accepted_file"][
                data["cross_phase_reacquired"].astype(bool)
            ]
        )


class _FakeDG:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def set_burst_state(self, state: bool, *, channel: int) -> None:
        self.calls.append(("burst_state", channel, state))

    def set_burst_mode(self, mode: str, *, channel: int) -> None:
        self.calls.append(("burst_mode", channel, mode))

    def set_burst_trigger_source(
        self,
        source: str,
        *,
        channel: int,
    ) -> None:
        self.calls.append(("trigger_source", channel, source))

    def set_burst_trigger_slope(
        self,
        slope: str,
        *,
        channel: int,
    ) -> None:
        self.calls.append(("trigger_slope", channel, slope))

    def set_burst_phase(self, phase: float, *, channel: int) -> None:
        self.calls.append(("burst_phase", channel, phase))

    def set_mod_state(self, state: bool, *, channel: int) -> None:
        self.calls.append(("mod_state", channel, state))

    def setup_arbitrary(self, values, **kwargs) -> None:
        self.calls.append(
            (
                "arbitrary",
                kwargs["channel"],
                len(values),
                kwargs["freq"],
                kwargs["amplitude"],
                kwargs["offset"],
            )
        )

    def set_output(self, state: bool, *, channel: int) -> None:
        self.calls.append(("output", channel, state))

    def set_amplitude(self, amplitude: float, *, channel: int) -> None:
        self.calls.append(("amplitude", channel, amplitude))

    def set_frequency(self, frequency: float, *, channel: int) -> None:
        self.calls.append(("frequency", channel, frequency))

    def set_offset(self, offset: float, *, channel: int) -> None:
        self.calls.append(("offset", channel, offset))

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


def test_zero_phase_cal_rf_keeps_y_compensation_as_dc() -> None:
    rf = _FakeDG()
    params = MxZOptimalControlRFParams(phase_cal_rf_amplitude_vpp=0.0)

    _configure_y_rf_output(params, rf, 2)

    assert rf.calls == [
        ("burst_state", 2, False),
        ("mod_state", 2, False),
        ("dc", 2, 0.008),
        ("output", 2, True),
    ]
    assert not any(call[0] == "amplitude" for call in rf.calls)


def test_nonzero_phase_cal_rf_uses_y_compensation_as_sine_offset() -> None:
    rf = _FakeDG()
    params = MxZOptimalControlRFParams(
        phase_cal_rf_amplitude_vpp=0.01,
        y_rf_offset_v=0.008,
    )

    _configure_y_rf_output(params, rf, 2)

    assert ("sine", 2, 12000.0, 0.01, 0.008, 0.0) in rf.calls
    assert ("burst_state", 2, True) in rf.calls
    assert rf.calls[-1] == ("output", 2, False)


def test_y_rf_offset_envelope_is_validated() -> None:
    params = MxZOptimalControlRFParams(
        y_rf_offset_v=9.99,
        y_rf_amp_start_vpp=-0.1,
        y_rf_amp_stop_vpp=0.1,
    )

    errors = params.validate()

    assert any("Y RF 输出上限" in error for error in errors)


@pytest.mark.parametrize(
    ("enabled", "amplitude_vpp", "offset_v"),
    [(False, 0.013, 0.008), (False, 0.013, 0.0),
     (True, 0.013, 0.008), (True, 0.0, 0.008)],
)
def test_control_noise_sets_rf_and_saves_actual_state(
    local_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    enabled: bool,
    amplitude_vpp: float,
    offset_v: float,
) -> None:
    """真实噪声循环配合模拟仪器验证输出、触发相位、温控时序和落盘。"""
    from lab_workflows.experiment_modules.mx_y_rf_sensitivity import workflow as shared
    from lab_workflows.experiment_modules.mx_z_optimal_control_rf_sensitivity import workflow

    params = MxZOptimalControlRFParams(
        noise_rf_enabled=enabled,
        noise_rf_amplitude_vpp=amplitude_vpp,
        noise_n_avg=2,
        y_rf_offset_v=offset_v,
    )
    rf = _FakeDG()
    hf2 = SimpleNamespace(
        get_double=lambda path: 17.0,
        demod_path=lambda index: f"/dev/demods/{index}",
    )
    config: dict[str, object] = {}
    run_dir = SimpleNamespace(raw=local_tmp_path, update_config=lambda **kw: config.update(kw))
    monkeypatch.setattr(shared.demod, "configure_demodulator", lambda *args: 4096.0)
    monkeypatch.setattr(shared, "set_temperature_switch", lambda *args, **kw: None)
    waits: list[float] = []
    monkeypatch.setattr(shared, "_sleep", waits.append)
    monkeypatch.setattr(shared, "_uncancellable_sleep", lambda duration: None)
    actual_amplitude = amplitude_vpp if enabled else 0.0
    rf_on = actual_amplitude > 0.0

    def acquire(*args, **kwargs):
        assert rf.calls[-1] == ("output", 2, bool(rf_on or offset_v != 0.0))
        if rf_on:
            assert ("amplitude", 2, actual_amplitude) in rf.calls
            assert ("burst_phase", 2, 37.0) in rf.calls
            assert not any(call[0] in {"sine", "dc", "burst_state"} for call in rf.calls)
        else:
            assert ("dc", 2, offset_v) in rf.calls
            assert not any(call[0] in {"amplitude", "sine"} for call in rf.calls)
        return {"time_s": np.arange(16) / 4096.0, "r": np.ones(16)}

    monkeypatch.setattr(shared, "acquire_r", acquire)
    rate = workflow._acquire_control_noise(
        params, run_dir, {"xy_field": rf, "hf2": hf2, "temp_switch": object()},
        {"y_rf": 2, "temp_switch": 1}, "dev", 397.0,
    )
    assert rate == pytest.approx(4096.0)
    assert config["noise_rf"]["y_rf_enabled"] is rf_on
    assert config["noise_rf"]["y_rf_amplitude_vpp"] == pytest.approx(actual_amplitude)
    for index in range(2):
        with np.load(local_tmp_path / f"noise_{index:03d}.npz") as data:
            assert bool(data["y_rf_enabled"]) is rf_on
            assert data["y_rf_amplitude_vpp"] == pytest.approx(actual_amplitude)
            assert data["y_rf_dc_v"] == pytest.approx(offset_v)
            assert data["y_rf_burst_phase_deg"] == pytest.approx(37.0)
            assert data["y_rf_frequency_hz"] == pytest.approx(params.y_rf_frequency_hz)
            assert bool(data["y_rf_output_on"]) is bool(rf_on or offset_v != 0.0)
            assert data["actual_rate_sa_s"] == pytest.approx(4096.0)
    output = capsys.readouterr().out
    assert ("Y RF 开启噪声" if rf_on else "零 Y RF 噪声") in output
    if rf_on:
        assert "零 Y RF 噪声" not in output
        assert waits.count(params.response_settle_time_s) >= 2


@pytest.mark.parametrize("amplitude", [-0.001, 2.1, float("nan"), float("inf")])
def test_noise_rf_rejects_invalid_amplitude(amplitude: float) -> None:
    params = MxZOptimalControlRFParams(
        noise_rf_enabled=True, noise_rf_amplitude_vpp=amplitude,
    )
    assert any("NOISE_RF_AMPLITUDE_VPP" in error for error in params.validate())


def test_noise_rf_envelope_is_checked_before_instrument_commands(
    local_tmp_path: Path,
) -> None:
    from lab_workflows.experiment_modules.mx_z_optimal_control_rf_sensitivity import workflow

    params = MxZOptimalControlRFParams(
        noise_rf_enabled=True, noise_rf_amplitude_vpp=0.4, y_rf_offset_v=9.9,
    )
    assert any("Y RF 输出上限" in error for error in params.validate())
    rf = _FakeDG()
    with pytest.raises(ValueError, match="Y_magnetic_field"):
        workflow._acquire_control_noise(
            params, SimpleNamespace(raw=local_tmp_path), {"xy_field": rf},
            {"y_rf": 2}, "dev", 37.0,
        )
    assert rf.calls == []


class _FakeGS200:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def set_output(self, state: bool) -> None:
        self.calls.append(("output", state))

    def set_source_function(self, source_function: str) -> None:
        self.calls.append(("source_function", source_function))

    def set_current_limit(self, current_a: float) -> None:
        self.calls.append(("current_limit", current_a))

    def set_current(self, current_a: float) -> None:
        self.calls.append(("current", current_a))


@pytest.mark.parametrize(
    ("current_ma", "expected_output"),
    [(0.0, False), (1.25, True), (-1.25, True)],
)
def test_main_field_zero_closes_and_nonzero_opens(
    current_ma: float,
    expected_output: bool,
) -> None:
    gs200 = _FakeGS200()
    params = MxZOptimalControlRFParams(main_magnetic_field_ma=current_ma)

    _configure_main_field(
        params,
        gs200,
        {"source_function": "CURRent"},
    )

    assert gs200.calls == [
        ("output", False),
        ("source_function", "CURRent"),
        ("current_limit", 0.01),
        ("current", current_ma / 1000.0),
        ("output", expected_output),
    ]


def test_control_starts_once_and_y_rf_rearms_independently() -> None:
    root = Path(__file__).resolve().parents[2]
    params = MxZOptimalControlRFParams()
    theory = load_theory_control(
        Path(params.control_results_root),
        params.control_version,
    )
    calibration = load_z_calibration(
        root,
        params.z_calibration_source_run,
    )
    applied = build_applied_control(
        theory,
        calibration,
        1.0,
        output_vpp=4.0,
        output_offset_v=0.0,
    )
    control = _FakeDG()
    _configure_control_output(
        params,
        control,
        1,
        theory,
        applied,
    )
    assert control.calls.count(("output", 1, True)) == 1
    assert ("trigger_source", 1, "EXTernal") in control.calls
    assert ("trigger_slope", 1, "NEGative") in control.calls
    assert ("burst_mode", 1, "INFinity") in control.calls
    assert (
        "frequency",
        1,
        theory.repeat_frequency_hz,
    ) in control.calls
    assert ("amplitude", 1, applied.amplitude_vpp) in control.calls
    assert ("offset", 1, applied.offset_v) in control.calls

    rf = _FakeDG()
    hardware = _rearm_y_rf(
        rf,
        2,
        amplitude_vpp=0.05,
        phase_deg=125.0,
    )
    assert rf.calls == [
        ("output", 2, False),
        ("burst_phase", 2, 125.0),
        ("amplitude", 2, 0.05),
        ("output", 2, True),
    ]
    assert hardware["output_on"]
    assert control.calls.count(("output", 1, True)) == 1


def test_y_rf_zero_uses_dc_compensation_then_restores_burst_sine() -> None:
    rf = _FakeDG()
    state = {"mode": "burst_sine"}

    zero = _rearm_y_rf(
        rf,
        2,
        amplitude_vpp=0.0,
        phase_deg=35.0,
        frequency_hz=12000.0,
        offset_v=0.008,
        waveform_state=state,
    )

    assert zero["waveform_mode"] == "dc"
    assert zero["output_on"] is True
    assert ("dc", 2, 0.008) in rf.calls
    assert not any(
        call[0] == "amplitude" and call[2] == 0.0 for call in rf.calls
    )

    rf.calls.clear()
    restored = _rearm_y_rf(
        rf,
        2,
        amplitude_vpp=0.05,
        phase_deg=125.0,
        frequency_hz=12000.0,
        offset_v=0.008,
        waveform_state=state,
    )

    assert restored["waveform_mode"] == "burst_sine"
    assert ("sine", 2, 12000.0, 0.05, 0.008, 0.0) in rf.calls
    assert ("burst_state", 2, True) in rf.calls
    assert ("burst_mode", 2, "INFinity") in rf.calls
    assert ("trigger_source", 2, "EXTernal") in rf.calls
    assert ("trigger_slope", 2, "NEGative") in rf.calls
    assert rf.calls[-1] == ("output", 2, True)


def test_residual_phase_scan_accepts_flat_response_and_restores_control(
    local_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_dir = local_tmp_path / "raw"
    results_dir = local_tmp_path / "results"
    raw_dir.mkdir()
    results_dir.mkdir()
    run_dir = SimpleNamespace(raw=raw_dir, results=results_dir)
    params = MxZOptimalControlRFParams(
        phase_cal_rf_amplitude_vpp=0.0,
        control_burst_phase_deg=37.0,
        phase_scan_start_deg=0.0,
        phase_scan_stop_deg=50.0,
        phase_scan_step_deg=10.0,
    )
    control = _FakeDG()
    rf = _FakeDG()

    def fake_acquire(*args, set_y_rf, file_stem: str, **kwargs):
        set_y_rf()
        return (
            {"r_scalar_mean_v": 0.1, "r_std_v": 1e-4},
            0,
            f"{file_stem}_attempt_00.npz",
        )

    monkeypatch.setattr(
        "lab_workflows.experiment_modules.mx_z_optimal_control_rf_sensitivity.workflow._acquire_valid_r_point",
        fake_acquire,
    )

    selected, payload = _acquire_phase_scan(
        params,
        run_dir,
        {"z_field": control, "xy_field": rf},
        {"z_field": 1, "y_rf": 2},
        actual_rate=1000.0,
        device_id="dev-test",
    )

    assert np.isnan(selected)
    assert payload["mode"] == "residual_control_phase_scan"
    assert payload["scan_completed"] is True
    assert payload["fit_accepted"] is False
    assert control.calls[-3:] == [
        ("output", 1, False),
        ("burst_phase", 1, 37.0),
        ("output", 1, True),
    ]
    assert ("dc", 2, params.y_rf_offset_v) in rf.calls
    assert not any(call[0] == "amplitude" for call in rf.calls)
    assert rf.calls[-1] == ("output", 2, True)
    with np.load(raw_dir / "phase_scan.npz") as data:
        assert "control_burst_phase_deg" in data
        assert "y_rf_burst_phase_deg" not in data
    assert (results_dir / "phase_calibration.png").is_file()


def test_residual_phase_scan_restores_control_after_acquisition_error(
    local_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_dir = local_tmp_path / "raw"
    results_dir = local_tmp_path / "results"
    raw_dir.mkdir()
    results_dir.mkdir()
    run_dir = SimpleNamespace(raw=raw_dir, results=results_dir)
    params = MxZOptimalControlRFParams(
        phase_cal_rf_amplitude_vpp=0.0,
        control_burst_phase_deg=23.0,
        phase_scan_start_deg=0.0,
        phase_scan_stop_deg=50.0,
        phase_scan_step_deg=10.0,
    )
    control = _FakeDG()
    rf = _FakeDG()

    def fail_acquire(*args, set_y_rf, **kwargs):
        set_y_rf()
        raise RuntimeError("采集失败")

    monkeypatch.setattr(
        "lab_workflows.experiment_modules.mx_z_optimal_control_rf_sensitivity.workflow._acquire_valid_r_point",
        fail_acquire,
    )

    with pytest.raises(RuntimeError, match="采集失败"):
        _acquire_phase_scan(
            params,
            run_dir,
            {"z_field": control, "xy_field": rf},
            {"z_field": 1, "y_rf": 2},
            actual_rate=1000.0,
            device_id="dev-test",
        )

    assert control.calls[-3:] == [
        ("output", 1, False),
        ("burst_phase", 1, 23.0),
        ("output", 1, True),
    ]


def test_safe_shutdown_preserves_z_control_and_closes_trigger_and_xy() -> None:
    z = _FakeDG()
    xy = _FakeDG()
    report = safe_shutdown(
        {"z_field": z, "xy_field": xy},
        {"z_field": 1, "trigger": 2, "x_field": 1, "y_rf": 2},
        MxZOptimalControlRFParams(),
    )
    assert report.completed
    assert "Z_magnetic_field" in report.preserved_outputs
    assert not any(call[1] == 1 for call in z.calls)
    assert ("dc", 2, 0.0) in z.calls
    assert ("output", 2, False) in z.calls
    for channel in (1, 2):
        assert ("dc", channel, 0.0) in xy.calls
        assert ("output", channel, False) in xy.calls


def test_residual_phase_analysis_does_not_require_rf_scan_files(
    local_tmp_path: Path,
) -> None:
    run_dir = local_tmp_path / "residual_run"
    raw_dir = run_dir / "raw"
    results_dir = run_dir / "results"
    raw_dir.mkdir(parents=True)
    results_dir.mkdir()
    params = MxZOptimalControlRFParams(
        phase_cal_rf_amplitude_vpp=0.0,
        control_burst_phase_deg=17.0,
    )
    config = {
        "experiment_id": "mx-z-optimal-control-rf-sensitivity",
        "schema_version": params.schema_version,
        "measurement_mode": "residual_control_phase_scan",
        "parameters": params.to_external(),
        "control_source": {"version": "v1"},
        "z_calibration": {"source_run": "0728_161259_mx_z_cal"},
        "applied_control": {"points": 10000},
        "trigger": {"source": "Time_sequence_2"},
    }
    (run_dir / "experiment_config.yaml").write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    phase = np.arange(0.0, 360.0, 10.0)
    response = np.full_like(phase, 0.1)
    primary, diagnostic = fit_phase_scan(
        phase,
        response,
        r_squared_min=0.85,
        amplitude_sigma_min=3.0,
    )
    np.savez(
        raw_dir / "phase_scan.npz",
        scanned_phase_deg=phase,
        control_burst_phase_deg=phase,
        r_mean_v=response,
        r_std_v=np.full_like(phase, 1e-4),
        actual_rate_sa_s=np.float64(1000.0),
    )
    phase_payload = {
        "mode": "residual_control_phase_scan",
        "phase_target": "control_burst",
        "success": primary.success,
        "scan_completed": True,
        "fit_accepted": primary.success,
        "selected_phase_deg": primary.selected_phase_deg,
        "candidate_control_phase_deg": primary.selected_phase_deg,
        "selected_control_phase_deg": None,
        "primary_fit": primary.to_dict(),
        "diagnostic_fit": diagnostic.to_dict(),
        "phase_scan": {
            "rf_amplitude_vpp": 0.0,
            "rf_output_on": False,
            "control_restored_phase_deg": 17.0,
        },
    }
    (results_dir / "phase_calibration.yaml").write_text(
        yaml.safe_dump(phase_payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    result = analyze(run_dir)

    assert result["measurement_mode"] == "residual_control_phase_scan"
    assert result["scan_completed"] is True
    assert result["fit_accepted"] is False
    assert result["y_rf_output_during_scan"] == "DC_BIAS_ON"
    assert result["x_dc_field_v"] == pytest.approx(params.x_dc_field_v)
    assert result["y_rf_dc_offset_v"] == pytest.approx(params.y_rf_offset_v)
    assert result["gs200_main_field_during_measurement"] == (
        "0 mA, output OFF"
    )
    assert result["restored_control_phase_deg"] == pytest.approx(17.0)
    assert result["response_summary"]["points"] == phase.size
    assert result["response_summary"]["peak_to_peak_r_v"] == pytest.approx(0.0)
    assert (results_dir / "phase_calibration.png").is_file()
    assert (results_dir / "analysis.yaml").is_file()
    assert not (raw_dir / "amplitude_scan.npz").exists()


def test_offline_analysis_adds_phase_and_control_results(
    local_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = local_tmp_path / "run"
    raw_dir = run_dir / "raw"
    results_dir = run_dir / "results"
    raw_dir.mkdir(parents=True)
    results_dir.mkdir()
    params = MxZOptimalControlRFParams(
        noise_n_avg=2,
        main_magnetic_field_ma=1.25,
    )
    config = {
        "experiment_id": "mx-z-optimal-control-rf-sensitivity",
        "schema_version": params.schema_version,
        "parameters": params.to_external(),
        "control_source": {"version": "v1"},
        "z_calibration": {"source_run": "0728_161259_mx_z_cal"},
        "applied_control": {"points": 10000},
        "trigger": {"source": "Time_sequence_2"},
    }
    (run_dir / "experiment_config.yaml").write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    amplitude = np.linspace(-0.1, 0.1, 21)
    response = absolute_dispersive_response(
        amplitude,
        0.1,
        0.04,
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

    phase = np.arange(0.0, 360.0, 10.0)
    phase_r = inside_absolute_sine(phase, 0.01, 0.2, 25.0)
    primary, diagnostic = fit_phase_scan(
        phase,
        phase_r,
        r_squared_min=0.85,
        amplitude_sigma_min=3.0,
    )
    np.savez(
        raw_dir / "phase_scan.npz",
        y_rf_burst_phase_deg=phase,
        r_mean_v=phase_r,
        r_std_v=np.full_like(phase, 1e-4),
    )
    phase_payload = {
        "success": True,
        "selected_y_rf_phase_deg": primary.selected_phase_deg,
        "primary_fit": primary.to_dict(),
        "diagnostic_fit": diagnostic.to_dict(),
    }
    (results_dir / "phase_calibration.yaml").write_text(
        yaml.safe_dump(phase_payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    from lab_workflows.experiment_modules.mx_y_rf_sensitivity import (
        point_analysis as shared_point_analysis,
    )

    original_fit = shared_point_analysis.fit_absolute_dispersive_response
    captured: dict[str, object] = {}

    def capture_fit(*args, **kwargs):
        captured["relative_gamma_uncertainty_max"] = kwargs.get(
            "relative_gamma_uncertainty_max"
        )
        captured["initial_center"] = kwargs.get("initial_center")
        fitted = original_fit(*args, **kwargs)
        return replace(fitted, relative_gamma_uncertainty=0.75)

    monkeypatch.setattr(
        shared_point_analysis,
        "fit_absolute_dispersive_response",
        capture_fit,
    )

    result = analyze(run_dir)

    assert result["experiment_id"] == (
        "mx-z-optimal-control-rf-sensitivity"
    )
    assert result["control_enabled"]
    assert result["gs200_main_field_during_measurement"] == (
        "1.25 mA, output ON"
    )
    assert result["applied_control"]["points"] == 10000
    assert result["selected_y_rf_phase_deg"] == pytest.approx(
        primary.selected_phase_deg
    )
    assert (results_dir / "phase_calibration.png").is_file()
    assert (results_dir / "full_analysis.png").is_file()
    assert (results_dir / "full_analysis_zero_point.png").is_file()
    assert "phase_calibration.png" in result["files"]
    assert captured["relative_gamma_uncertainty_max"] is None
    assert captured["initial_center"] == pytest.approx(0.0)
    assert result["response_fit"]["relative_gamma_uncertainty"] == pytest.approx(
        0.75
    )
    with np.load(results_dir / "response_fit.npz") as response_fit:
        assert response_fit["fit_uncertainties"].shape == (4,)


def test_offline_analysis_saves_measured_response_when_fit_fails(
    local_tmp_path: Path,
) -> None:
    run_dir = local_tmp_path / "fit_failed_run"
    raw_dir = run_dir / "raw"
    results_dir = run_dir / "results"
    raw_dir.mkdir(parents=True)
    results_dir.mkdir()
    params = MxZOptimalControlRFParams(noise_n_avg=1)
    (run_dir / "experiment_config.yaml").write_text(
        yaml.safe_dump(
            {
                "experiment_id": "mx-z-optimal-control-rf-sensitivity",
                "schema_version": params.schema_version,
                "parameters": params.to_external(),
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    phase = np.arange(0.0, 360.0, 10.0)
    np.savez(
        raw_dir / "phase_scan.npz",
        y_rf_burst_phase_deg=phase,
        r_mean_v=np.full_like(phase, 0.1),
        r_std_v=np.full_like(phase, 1e-4),
    )
    (results_dir / "phase_calibration.yaml").write_text(
        yaml.safe_dump(
            {
                "success": True,
                "selected_y_rf_phase_deg": 0.0,
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    amplitude = np.linspace(-0.1, 0.1, 21)
    np.savez(
        raw_dir / "amplitude_scan.npz",
        signed_amplitude_vpp=amplitude,
        r_mean_v=np.full_like(amplitude, 0.0034),
        r_std_v=np.full_like(amplitude, 1e-4),
    )

    with pytest.raises(RuntimeError, match="幅度色散拟合质量不合格"):
        analyze(run_dir)

    measured_plot = results_dir / "amplitude_response.png"
    assert measured_plot.is_file()
    assert measured_plot.stat().st_size > 0
    payload = yaml.safe_load(
        (results_dir / "analysis.yaml").read_text(encoding="utf-8")
    )
    assert payload["success"] is False
    assert payload["files"] == ["amplitude_response.png"]
    assert payload["plot_profile"] == "paper"
