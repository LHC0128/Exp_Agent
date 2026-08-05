"""原子自旋投影噪声 SDS 工作流契约测试。"""

from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import numpy as np
import pytest
import yaml

from lab_workflows.experiment_modules.projection_noise.analysis import (
    analyze,
    fit_spin_noise_lorentzian,
    lorentzian_psd,
)
from lab_workflows.experiment_modules.projection_noise.calibration import (
    MainFieldCalibration,
    load_main_field_calibration,
)
from lab_workflows.experiment_modules.projection_noise.definition import DEFINITION
from lab_workflows.experiment_modules.projection_noise.models import ProjectionNoiseParams
from lab_workflows.experiment_modules.projection_noise.workflow import run
from lab_workflows.experiments.registry import get_experiment
from lab_workflows.steps import (
    SafetyShutdownReport,
    next_auto_offset,
    next_auto_range_scale,
)
from lab_workflows.steps.state import StateGuard
from sds_acquisition import AcquisitionResult, save_to_npz


@pytest.fixture
def local_tmp_path():
    root = Path(__file__).resolve().parents[1] / "data"
    path = root / f".test_projection_noise_scope_{uuid4().hex}"
    path.mkdir(mode=0o777)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def test_defaults_schema_registry_and_target_current() -> None:
    params = ProjectionNoiseParams()
    assert params.validate() == []
    assert params.target_current_ma() == pytest.approx(9.284957654744666)
    assert get_experiment("projection-noise") is DEFINITION
    assert DEFINITION.execution_mode == "typed_workflow"
    assert DEFINITION.data_type == "Projection_noise"
    assert "SDS" in DEFINITION.required_devices
    assert "HF2" not in DEFINITION.required_devices
    fields = {item["name"]: item for item in DEFINITION.schema()["fields"]}
    assert fields["SCOPE_SAMPLE_RATE"]["default"] == 500000.0
    assert fields["ACQ_REPEATS"]["default"] == 100
    assert fields["FIT_HALF_WIDTH_HZ"]["default"] == 5000.0
    assert fields["FIXED_PARAMS.Pump_laser_power"]["minimum"] == 0.0
    assert fields["FIXED_PARAMS.Pump_laser_power"]["maximum"] == 1.0
    assert fields["MEASURE_FIELD_OFF_CONTROL"] == {
        "name": "MEASURE_FIELD_OFF_CONTROL",
        "label": "测量关闭电流源对照组",
        "type": "boolean",
        "default": True,
        "unit": "",
        "group": "basic",
        "minimum": None,
        "maximum": None,
        "description": "开启时先采集 GS200 输出关闭背景；关闭时仅采集主场打开数据。",
    }
    assert "SCOPE_PD_CHANNEL" not in fields
    assert params.scope_vertical_divisions == 8
    assert params.scope_auto_offset_tolerance_fraction == pytest.approx(0.2)


def test_pump_power_accepts_nonzero_values_within_safety_limit() -> None:
    assert ProjectionNoiseParams(pump_laser_power_v=0.4).validate() == []
    errors = ProjectionNoiseParams(pump_laser_power_v=1.1).validate()
    assert any(
        "FIXED_PARAMS.Pump_laser_power=1.1 高于上限 1.0" in error
        for error in errors
    )


@pytest.mark.parametrize(
    ("pump_power_v", "expected_output"),
    [(0.0, False), (0.4, True)],
)
def test_configure_outputs_switches_pump_for_nonzero_power(
    monkeypatch: pytest.MonkeyPatch,
    pump_power_v: float,
    expected_output: bool,
) -> None:
    import lab_workflows.experiment_modules.projection_noise.workflow as workflow

    laser = MagicMock()
    tec = MagicMock()
    gs200 = MagicMock()
    z_field = MagicMock()
    xy_field = MagicMock()
    pump_rf = MagicMock()
    devices = {
        "laser": laser,
        "tec": tec,
        "gs200": gs200,
        "temp_switch": MagicMock(),
        "z_field": z_field,
        "xy_field": xy_field,
        "pump_rf": pump_rf,
    }
    channels = {
        "pump_laser": 1,
        "probe_laser": 2,
        "temp_switch": 2,
        "z_field": 1,
        "x_field": 1,
        "y_field": 2,
        "pump_carrier": 1,
        "pump_gate": 2,
    }
    mapping = {"main_magnetic_field": {"source_function": "CURRent"}}
    monkeypatch.setattr(workflow, "synchronize_connected_clocks", lambda *a: {})
    monkeypatch.setattr(workflow, "set_temperature_switch", lambda *a, **k: None)
    monkeypatch.setattr(workflow, "wait_for_temperature_stable", lambda *a, **k: 120.0)
    monkeypatch.setattr(
        workflow,
        "load_safety_limits",
        lambda: {"main_magnetic_field": {"max": 10.0}},
    )

    actual_temperature, _ = workflow._configure_outputs(
        ProjectionNoiseParams(pump_laser_power_v=pump_power_v),
        devices,
        channels,
        mapping,
    )

    assert actual_temperature == pytest.approx(120.0)
    laser.setup_dc.assert_any_call(pump_power_v, channel=1)
    laser.set_output.assert_any_call(expected_output, channel=1)
    for device, channel in (
        (z_field, 1),
        (xy_field, 1),
        (xy_field, 2),
        (pump_rf, 1),
        (pump_rf, 2),
    ):
        device.set_burst_state.assert_any_call(False, channel=channel)
        device.set_mod_state.assert_any_call(False, channel=channel)
        device.setup_dc.assert_any_call(0.0, channel=channel)
        device.set_output.assert_any_call(False, channel=channel)


def test_safe_shutdown_keeps_all_unrelated_projection_outputs_off() -> None:
    import lab_workflows.experiment_modules.projection_noise.workflow as workflow

    z_field = MagicMock()
    xy_field = MagicMock()
    pump_rf = MagicMock()
    report = workflow.safe_shutdown(
        {
            "z_field": z_field,
            "xy_field": xy_field,
            "pump_rf": pump_rf,
        },
        {
            "z_field": 1,
            "x_field": 1,
            "y_field": 2,
            "pump_carrier": 1,
            "pump_gate": 2,
        },
        StateGuard(),
    )

    assert report.completed
    assert "Pump_modulation" not in report.preserved_outputs
    for device, channel in (
        (z_field, 1),
        (xy_field, 1),
        (xy_field, 2),
        (pump_rf, 1),
        (pump_rf, 2),
    ):
        device.setup_dc.assert_any_call(0.0, channel=channel)
        device.set_output.assert_any_call(False, channel=channel)


def test_vertical_divisions_are_total_screen_divisions() -> None:
    from lab_workflows.experiment_modules.projection_noise.workflow import (
        _scope_settings,
    )

    settings = _scope_settings(ProjectionNoiseParams())
    assert settings.vertical_half_span_v(0.4) == pytest.approx(1.6)
    assert next_auto_range_scale(settings, 0.4, 0.63) == pytest.approx(0.2)
    assert next_auto_range_scale(settings, 0.4, 0.65) == pytest.approx(0.4)
    assert next_auto_offset(settings, 0.4, 0.0, 0.0, 0.64) == pytest.approx(0.0)
    assert next_auto_offset(settings, 0.4, 0.0, 0.0, 0.66) == pytest.approx(-0.33)


def test_v2_four_single_side_divisions_migrate_to_eight_total() -> None:
    params = ProjectionNoiseParams.from_external(
        {"SCOPE_VERTICAL_DIVISIONS": 4}, schema_version=2
    )
    assert params.scope_vertical_divisions == 8


def test_legacy_parameter_aliases_are_migrated() -> None:
    params = ProjectionNoiseParams.from_external(
        {
            "RUN_TAG": "legacy",
            "PROBE_POWER": 0.2,
            "ACQ_DURATION": 2.0,
            "ACQ_REPEATS": 5,
            "NPERSEG": 1000,
            "MAIN_FIELD_NORMAL_mA": 9.0,
            "HF2_DEMOD_IDX": 0,
        },
        schema_version=1,
    )
    assert params.probe_laser_power_v == pytest.approx(0.2)
    assert params.scope_duration_s == pytest.approx(2.0)
    assert params.welch_nperseg == 1000
    assert params.target_larmor_frequency_hz == pytest.approx(
        9671.91741380711 * 9.0 + 196.65637261343872
    )
    assert params.measure_field_off_control is True


def test_calibration_loader_rejects_failed_run(local_tmp_path) -> None:
    result_dir = (
        local_tmp_path
        / "data"
        / "Mx_Main_Field_Calibration"
        / "failed"
        / "results"
    )
    result_dir.mkdir(parents=True)
    (result_dir / "analysis.yaml").write_text("success: false\n", encoding="utf-8")
    with pytest.raises(ValueError, match="未成功"):
        load_main_field_calibration(local_tmp_path, "failed")


def test_robust_lorentzian_fit_recovers_free_center_and_width() -> None:
    rng = np.random.default_rng(20260720)
    frequency = np.linspace(85000.0, 95000.0, 2001)
    expected = np.asarray([4.0e-10, 420.0, 90125.0, -2.0e-12])
    clean = lorentzian_psd(frequency, *expected)
    sigma = np.full_like(frequency, 2.0e-12)
    observed = clean + rng.normal(scale=sigma)
    observed[::157] += 2.0e-10
    fit = fit_spin_noise_lorentzian(
        frequency,
        observed,
        sigma,
        predicted_center_hz=90000.0,
        fit_half_width_hz=5000.0,
    )
    assert fit["success"] is True
    assert fit["center_hz"] == pytest.approx(expected[2], abs=15.0)
    assert fit["gamma_hz"] == pytest.approx(expected[1], rel=0.08)
    assert fit["fwhm_hz"] == pytest.approx(840.0, rel=0.08)
    assert fit["t2_s"] == pytest.approx(1.0 / (2.0 * np.pi * 420.0), rel=0.08)


def _write_compact_frame(path: Path, voltage: np.ndarray, sample_rate: float) -> None:
    raw = np.clip(np.rint(voltage * 1.0e6), -32768, 32767).astype(np.int16)
    time_s = np.arange(raw.size, dtype=float) / sample_rate
    preamble = {
        "point_num": int(raw.size),
        "vertical_gain": 1.0,
        "vertical_offset": 0.0,
        "code_per_div": 1.0e6,
        "horiz_interval": 1.0 / sample_rate,
        "horiz_offset": 0.0,
        "adc_bit": 16,
    }
    result = AcquisitionResult(
        channel=1,
        raw_data=raw,
        voltage=raw.astype(float) / 1.0e6,
        time=time_s,
        preamble_dict=preamble,
        config_snapshot={},
    )
    save_to_npz(
        str(path),
        [result],
        config_dict={"timebase_scale": raw.size / sample_rate / 10.0, "horizontal_divisions": 10},
        save_mode="raw",
    )


def test_compact_frame_reconstructs_scope_time_axis(local_tmp_path) -> None:
    from sds_acquisition import load_npz

    path = local_tmp_path / "compact.npz"
    voltage = np.linspace(-1.0e-3, 1.0e-3, 1000)
    _write_compact_frame(path, voltage, 10000.0)
    results, _ = load_npz(str(path))
    assert len(results) == 1
    assert np.median(np.diff(results[0].time)) == pytest.approx(1.0e-4)
    assert results[0].time[0] == pytest.approx(-0.05)


@pytest.mark.parametrize("measure_control", [True, False])
def test_offline_analysis_supports_optional_control_group(
    local_tmp_path, measure_control: bool
) -> None:
    run_dir = local_tmp_path / "scope_run"
    raw_dir = run_dir / "raw"
    if measure_control:
        (raw_dir / "field_off").mkdir(parents=True)
    else:
        raw_dir.mkdir(parents=True)
    (raw_dir / "field_on").mkdir()
    (run_dir / "results").mkdir()
    params = ProjectionNoiseParams(
        target_larmor_frequency_hz=4000.0,
        scope_sample_rate_sa_s=20000.0,
        scope_duration_s=1.0,
        acq_repeats=12,
        welch_nperseg=2000,
        fit_half_width_hz=1000.0,
        measure_field_off_control=measure_control,
    )
    (run_dir / "experiment_config.yaml").write_text(
        yaml.safe_dump(
            {
                "experiment_id": "projection-noise",
                "schema_version": params.schema_version,
                "parameters": params.to_external(),
                "main_field_calibration": {
                    "predicted_larmor_frequency_hz": 4000.0
                },
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    rng = np.random.default_rng(1234)
    sample_rate = 20000.0
    count = int(sample_rate)
    frequency = np.fft.rfftfreq(count, 1.0 / sample_rate)
    shape = 250.0**2 / ((frequency - 4050.0) ** 2 + 250.0**2)
    phase_values = []
    file_values = []
    phases = ("field_off", "field_on") if measure_control else ("field_on",)
    for phase in phases:
        for index in range(params.acq_repeats):
            base = rng.normal(scale=8.0e-4, size=count)
            if phase == "field_on":
                spectrum = (
                    rng.normal(size=frequency.size)
                    + 1j * rng.normal(size=frequency.size)
                ) * np.sqrt(shape)
                spin = np.fft.irfft(spectrum, n=count)
                spin *= 3.0e-3 / np.std(spin)
                voltage = base + spin
            else:
                voltage = base
            relative = f"raw/{phase}/waveform_{index:04d}.npz"
            _write_compact_frame(run_dir / relative, voltage, sample_rate)
            phase_values.append(phase)
            file_values.append(relative)
    np.savez(
        raw_dir / "scope_acquisition_index.npz",
        phase=np.asarray(phase_values, dtype=str),
        relative_file=np.asarray(file_values, dtype=str),
    )
    result = analyze(run_dir)
    assert result["success"] is True
    assert result["fit"]["center_hz"] == pytest.approx(4050.0, abs=80.0)
    assert result["control_group_measured"] is measure_control
    assert result["analysis_mode"] == (
        "field_on_minus_field_off" if measure_control else "field_on_only"
    )
    assert result["phase_frame_counts"]["field_off"] == (
        params.acq_repeats if measure_control else 0
    )
    assert result["pnl_metrics_emitted"] is False
    for filename in (
        "psd_spectra.npz",
        "lorentzian_fit.npz",
        "lorentzian_fit.csv",
        "scope_psd_overview.png",
        "spin_noise_lorentzian_fit.png",
        "analysis.yaml",
        "analysis.json",
    ):
        assert (run_dir / "results" / filename).is_file()


def test_legacy_hf2_layout_uses_compatibility_analysis(local_tmp_path) -> None:
    run_dir = local_tmp_path / "legacy_run"
    raw_dir = run_dir / "raw"
    raw_dir.mkdir(parents=True)
    (run_dir / "results").mkdir()
    (run_dir / "experiment_config.yaml").write_text(
        yaml.safe_dump(
            {
                "psd_params": {
                    "nperseg": 256,
                    "integ_fmin": 1.0,
                    "integ_fmax": 1000.0,
                }
            }
        ),
        encoding="utf-8",
    )
    rng = np.random.default_rng(9)
    off = rng.normal(size=(3, 2048)) * 1e-3
    on = off + rng.normal(size=(3, 2048)) * 2e-4
    np.savez(
        raw_dir / "waveforms_phase1_light.npz",
        x_waveforms=off,
        y_waveforms=off,
        actual_rate=10000.0,
    )
    np.savez(
        raw_dir / "waveforms_thermal.npz",
        x_waveforms=on,
        y_waveforms=on,
        actual_rate=10000.0,
    )
    result = analyze(run_dir)
    assert result["acquisition_backend"] == "legacy_hf2_demod_xy"
    assert (run_dir / "results" / "legacy_psd_spectra.npz").is_file()


@pytest.mark.parametrize("measure_control", [True, False])
def test_run_supports_optional_field_off_before_validated_field_on_and_restores(
    monkeypatch: pytest.MonkeyPatch, local_tmp_path, measure_control: bool
) -> None:
    import lab_workflows.experiment_modules.projection_noise.workflow as workflow

    root = local_tmp_path / "run"
    raw = root / "raw"
    results = root / "results"
    raw.mkdir(parents=True)
    results.mkdir()
    config_path = root / "experiment_config.yaml"
    config_path.write_text("{}", encoding="utf-8")

    class RunDir:
        def __init__(self):
            self.root = root
            self.raw = raw
            self.results = results
            self.config_path = config_path

        def update_config(self, **values):
            config_path.write_text(yaml.safe_dump(values), encoding="utf-8")

    events: list[tuple] = []

    class GS:
        output = False
        current = 0.0

        def set_output(self, value):
            self.output = bool(value)
            events.append(("output", bool(value)))

        def set_current(self, value):
            self.current = float(value)
            events.append(("current", float(value)))

    gs = GS()
    calibration = MainFieldCalibration(
        "cal", 10000.0, 0.0, 1.0, local_tmp_path / "analysis.yaml"
    )
    guard_state = SimpleNamespace()
    monkeypatch.setattr(workflow, "find_project_root", lambda: local_tmp_path)
    monkeypatch.setattr(workflow, "load_mapping", lambda root: {})
    monkeypatch.setattr(workflow, "load_main_field_calibration", lambda *a: calibration)
    monkeypatch.setattr(workflow, "create_run_directory", lambda *a, **k: RunDir())
    monkeypatch.setattr(workflow, "check_cancelled", lambda: None)
    monkeypatch.setattr(workflow, "_connect_devices", lambda *a: ({"gs200": gs}, {}))
    monkeypatch.setattr(workflow, "snapshot_main_field_state", lambda gs: guard_state)
    monkeypatch.setattr(workflow, "restore_main_field_state", lambda *a: events.append(("restore",)))
    monkeypatch.setattr(workflow, "_device_snapshot", lambda *a: {})
    monkeypatch.setattr(workflow, "_configure_outputs", lambda *a: (120.0, {}))
    monkeypatch.setattr(
        workflow,
        "configure_fixed_rate_scope",
        lambda *a, **k: (
            SimpleNamespace(),
            {"actual_sample_rate_sa_s": 500000.0, "actual_initial_scale_v_div": 0.4, "actual_initial_offset_v": 0.0},
        ),
    )
    monkeypatch.setattr(workflow, "_sleep", lambda seconds: events.append(("sleep", seconds)))

    def acquire_phase(*args, phase, main_field_output_on, main_field_current_ma, **kwargs):
        events.append(("phase", phase, gs.output, gs.current, main_field_current_ma))
        return []

    monkeypatch.setattr(workflow, "_acquire_phase", acquire_phase)
    monkeypatch.setattr(workflow, "_save_acquisition_index", lambda *a: "raw/index.npz")

    def shutdown(devices, channels, guard):
        guard.restore()
        return SafetyShutdownReport((), (), ())

    monkeypatch.setattr(workflow, "safe_shutdown", shutdown)
    result = run(
        ProjectionNoiseParams(
            target_larmor_frequency_hz=90000.0,
            measure_field_off_control=measure_control,
        )
    )
    assert result == root
    assert (("phase", "field_off", False, 0.0, 0.0) in events) is measure_control
    assert ("current", pytest.approx(0.009)) in events
    assert ("phase", "field_on", True, pytest.approx(0.009), pytest.approx(9.0)) in events
    assert ("sleep", 1.0) in events
    assert events[-1] == ("restore",)
