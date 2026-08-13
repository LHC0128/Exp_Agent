from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import numpy as np
import pytest
import yaml

from lab_workflows.common import WorkflowCancelled
from lab_workflows.experiment_modules.mx_keithley_6221_main_field_calibration.analysis import (
    analyze,
)
from lab_workflows.experiment_modules.mx_keithley_6221_main_field_calibration.definition import (
    DEFINITION,
)
from lab_workflows.experiment_modules.mx_keithley_6221_main_field_calibration.models import (
    MxKeithley6221MainFieldCalibrationParams,
)
from lab_workflows.experiment_modules.mx_keithley_6221_main_field_calibration.scan import (
    build_current_axis,
    build_frequency_axis,
    predicted_center_hz,
)
from lab_workflows.experiment_modules.mx_keithley_6221_main_field_calibration.workflow import (
    _acquire_scan,
    _assert_not_in_compliance,
    _configure_outputs,
    run,
    safe_shutdown,
)
from lab_workflows.experiment_modules.mx_y_rf_sensitivity.analysis_core import (
    lorentzian_response,
)
from lab_workflows.experiments.registry import get_experiment


@pytest.fixture
def local_tmp_path():
    root = Path(__file__).resolve().parents[2] / "data"
    path = root / f".test_mx_6221_main_field_{uuid4().hex}"
    path.mkdir(mode=0o777)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def test_defaults_schema_registry_and_preflight_contract() -> None:
    params = MxKeithley6221MainFieldCalibrationParams()
    assert params.validate() == ["必须确认 GS200 已从 Z 主线圈物理断开"]
    confirmed = MxKeithley6221MainFieldCalibrationParams(
        confirm_gs200_disconnected=True
    )
    assert confirmed.validate() == []
    assert get_experiment("mx-keithley-6221-main-field-calibration") is DEFINITION
    assert DEFINITION.execution_mode == "typed_workflow"
    assert DEFINITION.variant == "mx-keithley-6221"
    assert "keithley_6221_main_field" in DEFINITION.required_mapping_keys
    fields = {field["name"]: field for field in DEFINITION.schema()["fields"]}
    assert fields["KEITHLEY_CURRENT_START_MA"]["default"] == 20.0
    assert fields["KEITHLEY_CURRENT_STOP_MA"]["default"] == 50.0
    assert fields["KEITHLEY_CURRENT_RANGE_MA"]["default"] == 100.0
    assert fields["KEITHLEY_CURRENT_RANGE_MA"]["options"] == [
        {"value": 0.000002, "label": "2 nA"},
        {"value": 0.00002, "label": "20 nA"},
        {"value": 0.0002, "label": "200 nA"},
        {"value": 0.002, "label": "2 uA"},
        {"value": 0.02, "label": "20 uA"},
        {"value": 0.2, "label": "200 uA"},
        {"value": 2.0, "label": "2 mA"},
        {"value": 20.0, "label": "20 mA"},
        {"value": 100.0, "label": "100 mA"},
    ]
    assert fields["KEITHLEY_COMPLIANCE_V"]["default"] == 1.0
    assert fields["FIXED_PARAMS.main_magnetic_field"]["maximum"] == 0.0


def test_gui_discovers_schema_and_confirmation_gate() -> None:
    from GUI.backend import main as gui_main

    experiments = {item["id"]: item for item in gui_main.experiments()}
    experiment_id = "mx-keithley-6221-main-field-calibration"
    assert experiments[experiment_id]["execution_mode"] == "typed_workflow"
    assert gui_main.experiment_preflight(
        experiment_id,
        gui_main.ExperimentBody(
            parameters={"CONFIRM_GS200_PHYSICALLY_DISCONNECTED": False}
        ),
    )["ok"] is False
    result = gui_main.experiment_preflight(
        experiment_id,
        gui_main.ExperimentBody(
            parameters={"CONFIRM_GS200_PHYSICALLY_DISCONNECTED": True}
        ),
    )
    assert result == {"ok": True, "errors": []}


def test_scan_axes_prediction_and_global_point_count() -> None:
    params = MxKeithley6221MainFieldCalibrationParams(
        confirm_gs200_disconnected=True
    )
    assert build_current_axis(params).tolist() == [5.0, 6.0, 7.0, 8.0, 9.0]
    total = 0
    for current_ma, expected_center in zip(
        build_current_axis(params), [50000, 60000, 70000, 80000, 90000], strict=True
    ):
        assert predicted_center_hz(params, current_ma) == expected_center
        frequency = build_frequency_axis(params, current_ma)
        assert frequency.size == 41
        assert frequency[0] == expected_center - 10000
        assert frequency[-1] == expected_center + 10000
        total += frequency.size
    assert total == 205


def test_parameter_safety_and_fixed_gs200_zero() -> None:
    errors = MxKeithley6221MainFieldCalibrationParams(
        confirm_gs200_disconnected=True,
        keithley_current_stop_ma=101.0,
        keithley_current_range_ma=100.0,
    ).validate()
    assert any("KEITHLEY_CURRENT_STOP_MA" in error and "安全" in error for error in errors)
    errors = MxKeithley6221MainFieldCalibrationParams(
        confirm_gs200_disconnected=True,
        main_magnetic_field_ma=0.1,
    ).validate()
    assert any("必须严格为 0 mA" in error for error in errors)
    errors = MxKeithley6221MainFieldCalibrationParams(
        confirm_gs200_disconnected=True,
        keithley_current_stop_ma=100.0,
        keithley_current_step_ma=5.0,
        keithley_current_range_ma=20.0,
        keithley_initial_hz_per_ma=500.0,
    ).validate()
    assert any("不满足控制电流包络要求" in error for error in errors)


class _Source:
    def __init__(self, compliance: list[bool] | None = None) -> None:
        self.calls: list[tuple] = []
        self.compliance = iter(compliance or [])

    def abort_waveform(self):
        self.calls.append(("abort",))

    def set_output(self, value):
        self.calls.append(("output", value))

    def set_current(self, value):
        self.calls.append(("current", value))

    def get_current(self):
        return 0.0

    def get_output(self):
        return False

    def set_autorange(self, value):
        self.calls.append(("autorange", value))

    def set_current_range(self, value):
        self.calls.append(("range", value))

    def set_output_response(self, value):
        self.calls.append(("response", value))

    def set_analog_filter(self, value):
        self.calls.append(("filter", value))

    def set_compliance(self, value):
        self.calls.append(("compliance", value))

    def set_compliance_test(self, value):
        self.calls.append(("compliance_test", value))

    def raise_for_errors(self):
        self.calls.append(("errors",))

    def is_in_compliance(self):
        self.calls.append(("compliance_query",))
        return next(self.compliance)


class _GS:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def set_output(self, value):
        self.calls.append(("output", value))

    def set_current(self, value):
        self.calls.append(("current", value))

    def set_source_function(self, value):
        self.calls.append(("source", value))

    def get_current(self):
        return 0.0

    def get_output(self):
        return False


class _RF:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def set_frequency(self, value, *, channel):
        self.calls.append(("frequency", channel, value))

    def set_phase_adjust(self, value, *, channel):
        self.calls.append(("phase", channel, value))

    def set_amplitude(self, value, *, channel):
        self.calls.append(("amplitude", channel, value))

    def set_output(self, value, *, channel):
        self.calls.append(("output", channel, value))


def test_compliance_hit_immediately_shuts_down() -> None:
    source = _Source([True])
    with pytest.raises(RuntimeError, match="Compliance"):
        _assert_not_in_compliance(source, "测试点")
    assert source.calls == [
        ("compliance_query",),
        ("abort",),
        ("output", False),
        ("current", 0.0),
    ]


def test_configure_outputs_applies_documented_6221_and_gs200_sequence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import lab_workflows.experiment_modules.mx_keithley_6221_main_field_calibration.workflow as workflow

    monkeypatch.setattr(workflow, "_configure_reference_clocks", lambda *args: {})
    monkeypatch.setattr(workflow, "configure_temperature_control", lambda *a, **k: SimpleNamespace(actual_temperature_c=120.0))
    monkeypatch.setattr(workflow.demod, "configure_signal_input", lambda *a, **k: None)
    monkeypatch.setattr(workflow.demod, "configure_oscillator", lambda *a, **k: None)
    monkeypatch.setattr(workflow.demod, "configure_demodulator", lambda *a, **k: 1000.0)
    source = _Source()
    gs200 = _GS()
    devices = {name: MagicMock() for name in (
        "z_field", "xy_field", "laser", "pump_rf", "temp_switch", "tec", "hf2"
    )}
    devices.update({"keithley": source, "gs200": gs200})
    devices["hf2"].get_double.return_value = 0.0
    devices["hf2"].demod_path.return_value = "/dev/demods/0"
    channels = {
        "z_field": 1, "x_field": 1, "y_rf": 2, "pump_laser": 1,
        "probe_laser": 2, "pump_carrier": 1, "pump_gate": 2,
        "temp_switch": 2,
    }
    _configure_outputs(
        MxKeithley6221MainFieldCalibrationParams(
            confirm_gs200_disconnected=True,
            keithley_current_range_ma=100.0,
        ),
        devices,
        channels,
        {"main_magnetic_field": {"source_function": "CURRent"}},
    )
    assert gs200.calls[:3] == [("output", False), ("source", "CURRent"), ("current", 0.0)]
    assert source.calls == [
        ("abort",), ("output", False), ("current", 0.0),
        ("autorange", False), ("range", 0.1), ("response", "SLOW"),
        ("filter", False), ("compliance", 1.0), ("compliance_test", True),
        ("errors",),
    ]


def test_scan_jumps_directly_from_zero_to_5ma_and_checks_compliance(
    monkeypatch: pytest.MonkeyPatch, local_tmp_path: Path
) -> None:
    import lab_workflows.experiment_modules.mx_keithley_6221_main_field_calibration.workflow as workflow

    source = _Source([False, False, False])
    rf = _RF()
    root = local_tmp_path / "run"
    raw = root / "raw"
    raw.mkdir(parents=True)
    monkeypatch.setattr(workflow, "check_cancelled", lambda: None)
    monkeypatch.setattr(workflow, "build_current_axis", lambda params: np.asarray([5.0]))
    monkeypatch.setattr(workflow, "build_frequency_axis", lambda params, current: np.asarray([50000.0]))
    sleeps: list[float] = []
    monkeypatch.setattr(workflow, "_sleep", sleeps.append)
    monkeypatch.setattr(workflow.demod, "configure_oscillator", lambda *a, **k: None)
    monkeypatch.setattr(workflow, "_acquire_valid_r_point", lambda *a, **k: (
        {"r_mean_v": 0.2, "r_scalar_mean_v": 0.2, "r_std_v": 0.001},
        0,
        "frequency_0000_attempt_00.npz",
    ))
    _acquire_scan(
        MxKeithley6221MainFieldCalibrationParams(confirm_gs200_disconnected=True),
        SimpleNamespace(root=root, raw=raw),
        {"keithley": source, "xy_field": rf, "hf2": object()},
        {"y_rf": 2},
        1000.0,
        "dev",
    )
    assert source.calls[:2] == [("current", 0.005), ("output", True)]
    assert sleeps == [0.5]
    assert source.calls.count(("compliance_query",)) == 3


def test_safe_shutdown_starts_with_both_current_sources() -> None:
    events: list[tuple] = []
    source = _Source()
    gs200 = _GS()
    source.calls = events
    gs200.calls = events
    report = safe_shutdown(
        {"keithley": source, "gs200": gs200},
        {},
        MxKeithley6221MainFieldCalibrationParams(confirm_gs200_disconnected=True),
    )
    assert report.completed
    assert events == [
        ("abort",), ("output", False), ("current", 0.0),
        ("output", False), ("current", 0.0),
    ]


@pytest.mark.parametrize(
    "outcome", ["success", "error", "cancel", "configure_error"]
)
def test_run_leaves_both_current_sources_zero_and_off(
    monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    import lab_workflows.experiment_modules.mx_keithley_6221_main_field_calibration.workflow as workflow

    source = _Source()
    gs200 = _GS()
    run_dir = SimpleNamespace(
        root=Path("D:/synthetic/mx-6221-run"),
        config_path=MagicMock(),
        update_config=MagicMock(),
    )
    run_dir.config_path.exists.return_value = True
    monkeypatch.setattr(workflow, "find_project_root", lambda: Path("D:/synthetic"))
    monkeypatch.setattr(workflow, "load_mapping", lambda root: {"lockin_r": {"device_id": "dev"}})
    monkeypatch.setattr(workflow, "create_run_directory", lambda *a, **k: run_dir)
    monkeypatch.setattr(workflow, "check_cancelled", lambda: None)

    def connect(mapping, session, devices, channels):
        devices.update({"keithley": source, "gs200": gs200})

    monkeypatch.setattr(workflow, "_connect_devices", connect)
    def configure(*args, **kwargs):
        if outcome == "configure_error":
            source.set_current(0.005)
            source.set_output(True)
            raise RuntimeError("synthetic configuration failure")
        return 1000.0, 120.0, {}

    monkeypatch.setattr(workflow, "_configure_outputs", configure)

    def acquire(*args, **kwargs):
        source.set_current(0.005)
        source.set_output(True)
        if outcome == "error":
            raise RuntimeError("synthetic failure")
        if outcome == "cancel":
            raise WorkflowCancelled("synthetic cancellation")
        return []

    monkeypatch.setattr(workflow, "_acquire_scan", acquire)
    params = MxKeithley6221MainFieldCalibrationParams(confirm_gs200_disconnected=True)
    if outcome == "success":
        assert run(params) == run_dir.root
    else:
        expected = WorkflowCancelled if outcome == "cancel" else RuntimeError
        with pytest.raises(expected):
            run(params)
    assert source.calls[-3:] == [("abort",), ("output", False), ("current", 0.0)]
    assert gs200.calls[-2:] == [("output", False), ("current", 0.0)]


def _write_synthetic_run(
    root: Path, nonlinear_center_offsets_hz: list[float] | None = None
) -> Path:
    run_dir = root / "run"
    raw_dir = run_dir / "raw"
    raw_dir.mkdir(parents=True)
    params = MxKeithley6221MainFieldCalibrationParams(confirm_gs200_disconnected=True)
    (run_dir / "experiment_config.yaml").write_text(
        yaml.safe_dump(
            {
                "experiment_id": "mx-keithley-6221-main-field-calibration",
                "schema_version": params.schema_version,
                "parameters": params.to_external(),
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    files: list[str] = []
    predictions: list[float] = []
    for index, current_ma in enumerate(build_current_axis(params)):
        point_dir = raw_dir / f"current_{index:03d}"
        point_dir.mkdir()
        center = 1750.0 + 10050.0 * current_ma
        if nonlinear_center_offsets_hz is not None:
            center += nonlinear_center_offsets_hz[index]
        frequency = np.linspace(center - 10000.0, center + 10000.0, 41)
        response = lorentzian_response(frequency, 0.6, 1800.0, center, 0.1)
        filename = f"raw/current_{index:03d}/frequency_scan.npz"
        np.savez(
            run_dir / filename,
            frequency_hz=frequency,
            r_mean_v=response,
            r_std_v=np.full(frequency.size, 1e-4),
        )
        files.append(filename)
        predictions.append(predicted_center_hz(params, current_ma))
    np.savez(
        raw_dir / "keithley_main_field_scan_index.npz",
        keithley_current_ma=build_current_axis(params),
        predicted_center_hz=np.asarray(predictions),
        summary_file=np.asarray(files, dtype=str),
    )
    return run_dir


def test_offline_analysis_recovers_free_slope_and_intercept(local_tmp_path: Path) -> None:
    run_dir = _write_synthetic_run(local_tmp_path)
    result = analyze(run_dir)
    assert result["success"]
    assert result["K_f_Hz_per_mA"] == pytest.approx(10050.0, rel=1e-6)
    assert result["f_0mA_Hz"] == pytest.approx(1750.0, rel=1e-6)
    assert np.asarray(result["frequency_linear_fit"]["covariance"]).shape == (2, 2)
    for filename in (
        "analysis.yaml",
        "calibration_results.npz",
        "frequency_response_fits.png",
        "keithley_main_field_frequency_calibration.png",
        "keithley_main_field_frequency_residuals.png",
    ):
        assert (run_dir / "results" / filename).is_file()


def test_offline_analysis_rejects_low_linear_quality(local_tmp_path: Path) -> None:
    run_dir = _write_synthetic_run(
        local_tmp_path,
        nonlinear_center_offsets_hz=[0.0, 5000.0, -5000.0, 5000.0, 0.0],
    )
    result = analyze(run_dir)
    assert not result["success"]
    assert result["frequency_linear_fit"]["r_squared"] < 0.99
    assert any("线性 R^2" in reason for reason in result["rejection_reasons"])
