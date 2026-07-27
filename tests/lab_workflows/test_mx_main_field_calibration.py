from __future__ import annotations

import inspect
import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import numpy as np
import pytest
import yaml

from lab_workflows.experiment_modules.mx_main_field_calibration.analysis import (
    _calibration_fit_label,
    analyze,
)
from lab_workflows.experiment_modules.mx_main_field_calibration.definition import DEFINITION
from lab_workflows.experiment_modules.mx_main_field_calibration.models import (
    MxMainFieldCalibrationParams,
)
from lab_workflows.experiment_modules.mx_main_field_calibration.scan import (
    build_frequency_axis,
    build_main_field_axis,
    predicted_center_hz,
)
from lab_workflows.experiment_modules.mx_main_field_calibration.workflow import (
    _acquire_scan,
    _configure_outputs,
    _temperature_gated_acquire,
    run,
    safe_shutdown,
)
from lab_workflows.experiment_modules.mx_y_rf_sensitivity.analysis_core import (
    lorentzian_response,
)
from lab_workflows.experiments.registry import get_experiment
from lab_workflows.common import WorkflowCancelled
from lab_workflows.steps import (
    MainFieldState,
    restore_main_field_state,
    snapshot_main_field_state,
)
from lab_workflows.steps.state import StateGuard


@pytest.fixture
def local_tmp_path():
    root = Path(__file__).resolve().parents[2] / "data"
    path = root / f".test_mx_main_field_{uuid4().hex}"
    path.mkdir(mode=0o777)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def test_defaults_schema_and_registry_contract() -> None:
    params = MxMainFieldCalibrationParams()
    assert params.validate() == []
    assert get_experiment("mx-main-field-calibration") is DEFINITION
    assert DEFINITION.execution_mode == "typed_workflow"
    assert DEFINITION.family == "z-field-calibration"
    assert DEFINITION.variant == "mx-gs200"
    schema = DEFINITION.schema()
    fields = {item["name"]: item for item in schema["fields"]}
    assert schema["schema_version"] == 1
    assert fields["MAIN_FIELD_START_MA"]["default"] == 7.0
    assert fields["MAIN_FIELD_STOP_MA"]["default"] == 10.0
    assert fields["MAIN_FIELD_REFERENCE_CURRENT_MA"]["default"] == 9.333
    assert fields["MAIN_FIELD_REFERENCE_FREQUENCY_HZ"]["default"] == 90000.0
    assert fields["GYROMAGNETIC_RATIO_HZ_PER_NT"]["default"] == 7.0
    assert fields["SETTLE_TIME_S"]["default"] == 0.1
    assert "FIXED_PARAMS.main_magnetic_field" not in fields


def test_gui_api_discovers_schema_and_preflight() -> None:
    from GUI.backend import main as gui_main

    experiments = {item["id"]: item for item in gui_main.experiments()}
    assert experiments["mx-main-field-calibration"]["execution_mode"] == "typed_workflow"
    schema = gui_main.experiment_schema("mx-main-field-calibration")
    assert {field["name"] for field in schema["fields"]} == set(
        MxMainFieldCalibrationParams.external_names()
    )
    result = gui_main.experiment_preflight(
        "mx-main-field-calibration", gui_main.ExperimentBody(parameters={})
    )
    assert result == {"ok": True, "errors": []}


def test_scan_axes_and_reference_prediction() -> None:
    params = MxMainFieldCalibrationParams()
    assert build_main_field_axis(params).tolist() == [
        7.0,
        7.5,
        8.0,
        8.5,
        9.0,
        9.5,
        10.0,
    ]
    assert predicted_center_hz(params, 9.333) == pytest.approx(90000.0)
    frequency = build_frequency_axis(params, 9.333)
    assert frequency.size == 41
    assert frequency[0] == pytest.approx(80000.0)
    assert frequency[-1] == pytest.approx(100000.0)


def test_calibration_plot_label_contains_frequency_and_field_intercepts() -> None:
    label = _calibration_fit_label(
        {
            "slope_hz_per_ma": 9600.0,
            "intercept_hz": 2000.0,
            "r_squared": 0.99999,
        },
        7.0,
    )
    assert "f0=2000.000 Hz" in label
    assert "B0=285.714 nT" in label


def test_parameter_preflight_rejects_invalid_scan_and_prediction() -> None:
    errors = MxMainFieldCalibrationParams(
        main_field_start_ma=9.0,
        main_field_stop_ma=8.0,
    ).validate()
    assert any("MAIN_FIELD_START_MA" in error for error in errors)
    errors = MxMainFieldCalibrationParams(main_field_stop_ma=10.5).validate()
    assert any("MAIN_FIELD_STOP_MA" in error and "安全" in error for error in errors)
    errors = MxMainFieldCalibrationParams(
        main_field_reference_frequency_hz=1000.0,
        frequency_half_width_hz=2000.0,
    ).validate()
    assert any("预测扫频下限" in error for error in errors)


def test_acquisition_module_does_not_import_fitting() -> None:
    import lab_workflows.experiment_modules.mx_main_field_calibration.workflow as workflow

    source = inspect.getsource(workflow)
    assert "fit_lorentzian" not in source
    assert "lorentzian_response" not in source
    assert "analysis_core" not in source


def test_temperature_gate_uses_one_combined_wait_and_restores_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import lab_workflows.experiment_modules.mx_main_field_calibration.workflow as workflow

    events: list[tuple] = []
    monkeypatch.setattr(workflow, "check_cancelled", lambda: None)
    monkeypatch.setattr(
        workflow,
        "set_temperature_switch",
        lambda device, enabled, *, channel: events.append(
            ("temperature", enabled, channel)
        ),
    )
    monkeypatch.setattr(
        workflow, "_sleep", lambda seconds: events.append(("sleep", seconds))
    )
    monkeypatch.setattr(
        workflow,
        "_uncancellable_sleep",
        lambda seconds: events.append(("recovery", seconds)),
    )

    with pytest.raises(RuntimeError, match="DAQ failed"):
        _temperature_gated_acquire(
            MxMainFieldCalibrationParams(),
            {"temp_switch": object()},
            {"temp_switch": 2},
            acquire=lambda: (_ for _ in ()).throw(RuntimeError("DAQ failed")),
        )

    assert events == [
        ("temperature", False, 2),
        ("sleep", 0.1),
        ("temperature", True, 2),
        ("recovery", 1.0),
    ]


class _ScanGS:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def set_current(self, current_a: float) -> None:
        self.calls.append(("current", current_a))

    def set_output(self, state: bool) -> None:
        self.calls.append(("output", state))


class _ScanRF:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def set_frequency(self, value: float, *, channel: int) -> None:
        self.calls.append(("frequency", channel, value))

    def set_phase_adjust(self, value: float, *, channel: int) -> None:
        self.calls.append(("phase", channel, value))

    def set_amplitude(self, value: float, *, channel: int) -> None:
        self.calls.append(("amplitude", channel, value))

    def set_output(self, state: bool, *, channel: int) -> None:
        self.calls.append(("output", channel, state))


def test_setting_main_field_has_no_dedicated_wait(
    monkeypatch: pytest.MonkeyPatch, local_tmp_path
) -> None:
    import lab_workflows.experiment_modules.mx_main_field_calibration.workflow as workflow

    gs200 = _ScanGS()
    rf = _ScanRF()
    run_root = local_tmp_path / "run"
    raw = run_root / "raw"
    raw.mkdir(parents=True)
    monkeypatch.setattr(workflow, "check_cancelled", lambda: None)
    monkeypatch.setattr(
        workflow, "build_main_field_axis", lambda params: np.asarray([7.0])
    )
    monkeypatch.setattr(
        workflow,
        "build_frequency_axis",
        lambda params, current: np.asarray([70000.0]),
    )
    monkeypatch.setattr(workflow.demod, "configure_oscillator", lambda *a, **k: None)
    monkeypatch.setattr(
        workflow,
        "_acquire_valid_r_point",
        lambda *args, **kwargs: (
            {
                "r_mean_v": 0.2,
                "r_scalar_mean_v": 0.2,
                "r_std_v": 0.001,
                "n_samples": 10,
            },
            0,
            "frequency_0000_attempt_00.npz",
        ),
    )
    monkeypatch.setattr(
        workflow,
        "_sleep",
        lambda seconds: (_ for _ in ()).throw(
            AssertionError("主场电流切换后不应直接等待")
        ),
    )
    _acquire_scan(
        MxMainFieldCalibrationParams(),
        SimpleNamespace(root=run_root, raw=raw),
        {"gs200": gs200, "xy_field": rf, "hf2": object()},
        {"y_rf": 2},
        1000.0,
        "dev",
    )
    assert gs200.calls[:2] == [("current", 0.007), ("output", True)]


def test_configure_outputs_keeps_auxiliary_z_field_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import lab_workflows.experiment_modules.mx_main_field_calibration.workflow as workflow

    monkeypatch.setattr(workflow, "_configure_reference_clocks", lambda *a: {})
    monkeypatch.setattr(workflow, "wait_for_temperature_stable", lambda *a, **k: 120.0)
    monkeypatch.setattr(workflow.demod, "configure_signal_input", lambda *a, **k: None)
    monkeypatch.setattr(workflow.demod, "configure_oscillator", lambda *a, **k: None)
    monkeypatch.setattr(workflow.demod, "configure_demodulator", lambda *a, **k: 1000.0)
    devices = {
        name: MagicMock()
        for name in (
            "z_field",
            "xy_field",
            "laser",
            "gs200",
            "pump_rf",
            "temp_switch",
            "tec",
            "hf2",
        )
    }
    devices["hf2"].get_double.return_value = 0.0
    devices["hf2"].demod_path.return_value = "/dev/demods/0"
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
    _configure_outputs(
        MxMainFieldCalibrationParams(),
        devices,
        channels,
        {"main_magnetic_field": {"source_function": "CURRent"}},
    )
    devices["z_field"].setup_dc.assert_called_once_with(0.0, channel=1)
    devices["z_field"].set_output.assert_called_once_with(False, channel=1)
    devices["gs200"].set_current.assert_not_called()


class _StateGS:
    def __init__(self) -> None:
        self.source_function = "CURR"
        self.current_a = 0.0091
        self.output_on = True
        self.current_range_a = 0.01
        self.current_limit_a = 0.0098
        self.calls: list[tuple] = []

    def get_source_function(self):
        return self.source_function

    def get_current(self):
        return self.current_a

    def get_output(self):
        return self.output_on

    def get_current_range(self):
        return self.current_range_a

    def get_current_limit(self):
        return self.current_limit_a

    def set_output(self, value):
        self.calls.append(("output", value))

    def set_source_function(self, value):
        self.calls.append(("source", value))

    def set_current_limit(self, value):
        self.calls.append(("limit", value))

    def set_current(self, value):
        self.calls.append(("current", value))

    def set_current_range(self, value):
        self.calls.append(("range", value))


def test_main_field_snapshot_and_restore_all_state() -> None:
    gs200 = _StateGS()
    state = snapshot_main_field_state(gs200)
    assert state == MainFieldState("CURR", 0.0091, True, 0.01, 0.0098)
    restore_main_field_state(gs200, state)
    assert gs200.calls == [
        ("output", False),
        ("source", "CURR"),
        ("limit", 0.0098),
        ("current", 0.0091),
        ("range", 0.01),
        ("output", True),
    ]


def test_safe_shutdown_runs_main_field_guard() -> None:
    gs200 = _StateGS()
    state = snapshot_main_field_state(gs200)
    guard = StateGuard()
    guard.add("恢复 GS200", lambda: restore_main_field_state(gs200, state))
    report = safe_shutdown({}, {}, MxMainFieldCalibrationParams(), guard)
    assert report.completed
    assert ("current", 0.0091) in gs200.calls
    assert "main_magnetic_field" in report.preserved_outputs


@pytest.mark.parametrize("outcome", ["success", "error", "cancel"])
def test_run_restores_main_field_on_every_exit_path(
    monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    import lab_workflows.experiment_modules.mx_main_field_calibration.workflow as workflow

    gs200 = _StateGS()
    config_path = MagicMock()
    config_path.exists.return_value = True
    run_dir = SimpleNamespace(
        root=Path("D:/synthetic/main-field-run"),
        config_path=config_path,
        update_config=MagicMock(),
    )
    monkeypatch.setattr(workflow, "find_project_root", lambda: Path("D:/synthetic"))
    monkeypatch.setattr(
        workflow,
        "load_mapping",
        lambda root: {"lockin_r": {"device_id": "dev"}},
    )
    monkeypatch.setattr(workflow, "create_run_directory", lambda *a, **k: run_dir)
    monkeypatch.setattr(workflow, "check_cancelled", lambda: None)
    monkeypatch.setattr(
        workflow,
        "_connect_devices",
        lambda mapping, session: ({"gs200": gs200}, {}),
    )
    monkeypatch.setattr(
        workflow, "_configure_outputs", lambda *a, **k: (1000.0, 120.0, {})
    )

    def acquire(*args, **kwargs):
        gs200.set_current(0.007)
        gs200.set_output(True)
        if outcome == "error":
            raise RuntimeError("synthetic failure")
        if outcome == "cancel":
            raise WorkflowCancelled("synthetic cancellation")
        return []

    monkeypatch.setattr(workflow, "_acquire_scan", acquire)

    if outcome == "success":
        assert run(MxMainFieldCalibrationParams()) == run_dir.root
    else:
        expected = RuntimeError if outcome == "error" else WorkflowCancelled
        with pytest.raises(expected):
            run(MxMainFieldCalibrationParams())
    current_calls = [call for call in gs200.calls if call[0] == "current"]
    output_calls = [call for call in gs200.calls if call[0] == "output"]
    assert current_calls[-1] == ("current", 0.0091)
    assert output_calls[-1] == ("output", True)


def _write_synthetic_run(tmp_path):
    run_dir = tmp_path / "run"
    raw_dir = run_dir / "raw"
    raw_dir.mkdir(parents=True)
    params = MxMainFieldCalibrationParams()
    (run_dir / "experiment_config.yaml").write_text(
        yaml.safe_dump(
            {
                "experiment_id": "mx-main-field-calibration",
                "schema_version": params.schema_version,
                "parameters": params.to_external(),
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    current_axis = build_main_field_axis(params)
    summary_files: list[str] = []
    predicted: list[float] = []
    for index, current_ma in enumerate(current_axis):
        point_dir = raw_dir / f"current_{index:03d}"
        point_dir.mkdir()
        center = 2000.0 + 9600.0 * current_ma
        frequency = np.linspace(center - 10000.0, center + 10000.0, 41)
        response = lorentzian_response(frequency, 0.6, 1800.0, center, 0.1)
        filename = f"raw/current_{index:03d}/frequency_scan.npz"
        np.savez(
            run_dir / filename,
            frequency_hz=frequency,
            r_mean_v=response,
            r_std_v=np.full(frequency.size, 1e-4),
        )
        summary_files.append(filename)
        predicted.append(predicted_center_hz(params, current_ma))
    np.savez(
        raw_dir / "main_field_scan_index.npz",
        main_field_current_ma=current_axis,
        predicted_center_hz=np.asarray(predicted),
        summary_file=np.asarray(summary_files, dtype=str),
    )
    return run_dir


def test_offline_analysis_recovers_frequency_and_field_calibration(local_tmp_path) -> None:
    run_dir = _write_synthetic_run(local_tmp_path)
    result = analyze(run_dir)
    assert result["success"]
    assert result["K_f_Hz_per_mA"] == pytest.approx(9600.0, rel=1e-6)
    assert result["f_0mA_Hz"] == pytest.approx(2000.0, rel=1e-6)
    assert result["K_B_nT_per_mA"] == pytest.approx(9600.0 / 7.0, rel=1e-6)
    assert result["B_0mA_nT"] == pytest.approx(2000.0 / 7.0, rel=1e-6)
    for filename in (
        "analysis.yaml",
        "analysis.json",
        "calibration_results.npz",
        "frequency_response_fits.png",
        "main_field_frequency_calibration.png",
        "main_field_frequency_residuals.png",
    ):
        assert (run_dir / "results" / filename).is_file()
