"""Mx 主磁场控制噪声谱实验契约测试。"""

from __future__ import annotations

from pathlib import Path
import shutil
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import numpy as np
import pytest
import yaml

from lab_workflows.common import WorkflowCancelled
from lab_workflows.experiment_modules.mx_main_field_noise_spectrum.analysis import (
    analyze,
)
from lab_workflows.experiment_modules.mx_main_field_noise_spectrum.definition import (
    DEFINITION,
)
from lab_workflows.experiment_modules.mx_main_field_noise_spectrum.models import (
    MxMainFieldNoiseSpectrumParams,
)
from lab_workflows.experiment_modules.mx_main_field_noise_spectrum.scan import (
    build_scan_axes,
)
from lab_workflows.experiment_modules.mx_main_field_noise_spectrum.workflow import (
    _acquire_scan,
    _configure_outputs,
    _temperature_gated_acquire,
    _validate_calibrated_current,
    run,
    safe_shutdown,
)
from lab_workflows.experiments.registry import get_experiment
from lab_workflows.steps import restore_main_field_state, snapshot_main_field_state
from lab_workflows.steps.state import StateGuard


@pytest.fixture
def local_tmp_path():
    root = Path(__file__).resolve().parents[1] / "data"
    path = root / f".test_mx_main_field_noise_{uuid4().hex}"
    path.mkdir(mode=0o777)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def test_defaults_schema_registry_and_catalog_contract() -> None:
    params = MxMainFieldNoiseSpectrumParams()
    assert params.validate() == []
    assert get_experiment("mx-main-field-noise-spectrum") is DEFINITION
    assert DEFINITION.execution_mode == "typed_workflow"
    assert DEFINITION.data_type == "Mx_Main_Field_Noise_Spectrum"
    fields = {item["name"]: item for item in DEFINITION.schema()["fields"]}
    assert fields["CONTROL_FREQUENCY_POINTS"]["default"] == 500
    assert fields["HF2_REFERENCE_FREQUENCY_HZ"]["default"] == 90000.0
    assert fields["MAIN_FIELD_CALIBRATION_HZ_PER_MA"]["default"] == pytest.approx(
        9671.91741380711
    )
    assert fields["MAIN_FIELD_CALIBRATION_INTERCEPT_HZ"]["default"] == pytest.approx(
        196.65637261343872
    )

    root = Path(__file__).resolve().parents[1]
    with (root / "params" / "experiment_catalog.yaml").open(encoding="utf-8") as stream:
        catalog = yaml.safe_load(stream)
    layout = catalog["parameter_layouts"]["mx-main-field-noise-spectrum"]
    visible = set(fields)
    arranged = [*layout["basic"], *layout["advanced"]]
    assert len(arranged) == len(set(arranged))
    assert set(arranged) == visible


def test_scan_axis_uses_calibration_and_preserves_both_signs() -> None:
    params = MxMainFieldNoiseSpectrumParams()
    axes = build_scan_axes(params)
    control = axes["control_frequency_hz"]
    signed = axes["signed_detuning_hz"]
    resonance = axes["resonance_frequency_hz"]
    current = axes["main_field_current_ma"]
    assert control.size == 500
    assert control[[0, -1]].tolist() == [0.0, 50000.0]
    np.testing.assert_allclose(signed, -control, rtol=0.0, atol=0.0)
    np.testing.assert_allclose(
        resonance, params.hf2_reference_frequency_hz - control
    )
    assert current[0] == pytest.approx(9.28495765474467)
    assert current[-1] == pytest.approx(4.11535189191808)
    assert np.all(np.diff(control) > 0)
    assert np.all(np.diff(current) < 0)
    rebuilt = (
        params.main_field_calibration_hz_per_ma * current
        + params.main_field_calibration_intercept_hz
    )
    np.testing.assert_allclose(rebuilt, resonance, rtol=0.0, atol=1e-9)


def test_model_rejects_nyquist_and_calibration_extrapolation() -> None:
    nyquist = MxMainFieldNoiseSpectrumParams(requested_rate_sa_s=90000.0)
    assert any("Nyquist" in item for item in nyquist.validate_model())
    extrapolated = MxMainFieldNoiseSpectrumParams(
        control_frequency_stop_hz=65000.0,
        requested_rate_sa_s=150000.0,
    )
    assert any("标定有效范围" in item for item in extrapolated.validate_model())
    with pytest.raises(ValueError, match="标定有效范围"):
        _validate_calibrated_current(MxMainFieldNoiseSpectrumParams(), 2.99)


def test_temperature_gate_uses_combined_wait_and_restores_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import lab_workflows.experiment_modules.mx_main_field_noise_spectrum.workflow as workflow

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
        workflow, "_sleep", lambda seconds: events.append(("sleep", seconds))
    )

    with pytest.raises(RuntimeError, match="DAQ failed"):
        _temperature_gated_acquire(
            MxMainFieldNoiseSpectrumParams(),
            {"temp_switch": object()},
            {"temp_switch": 2},
            acquire=lambda: (_ for _ in ()).throw(RuntimeError("DAQ failed")),
        )

    assert events == [
        ("temperature", False, 2, 0.0),
        ("sleep", 0.3),
        ("temperature", True, 2, 1.0),
    ]


def test_configure_outputs_keeps_auxiliary_z_field_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import lab_workflows.experiment_modules.mx_main_field_noise_spectrum.workflow as workflow

    monkeypatch.setattr(workflow, "_configure_reference_clocks", lambda *a: {})
    monkeypatch.setattr(workflow, "load_safety_limits", lambda: {"main_magnetic_field": {"max": 10.0}})
    monkeypatch.setattr(workflow, "wait_for_temperature_stable", lambda *a, **k: 120.0)
    monkeypatch.setattr(workflow.demod, "configure_signal_input", lambda *a, **k: None)
    monkeypatch.setattr(workflow.demod, "configure_oscillator", lambda *a, **k: None)
    monkeypatch.setattr(workflow.demod, "configure_demodulator", lambda *a, **k: 100000.0)
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
        MxMainFieldNoiseSpectrumParams(),
        devices,
        channels,
        {"main_magnetic_field": {"source_function": "CURRent"}},
    )
    devices["z_field"].setup_dc.assert_called_once_with(0.0, channel=1)
    devices["z_field"].set_output.assert_called_once_with(False, channel=1)
    devices["gs200"].set_current.assert_called_once_with(
        pytest.approx(9.28495765474467e-3)
    )


def test_scan_validates_before_setting_current_and_has_no_dedicated_wait(
    monkeypatch: pytest.MonkeyPatch, local_tmp_path
) -> None:
    import lab_workflows.experiment_modules.mx_main_field_noise_spectrum.workflow as workflow

    events: list[tuple] = []

    class ScanGS:
        def set_current(self, value):
            events.append(("current", value))

        def set_output(self, value):
            events.append(("output", value))

    run_root = local_tmp_path / "scan_run"
    raw = run_root / "raw"
    raw.mkdir(parents=True)
    monkeypatch.setattr(workflow, "check_cancelled", lambda: None)
    monkeypatch.setattr(
        workflow,
        "build_scan_axes",
        lambda params: {
            "control_frequency_hz": np.asarray([0.0]),
            "signed_detuning_hz": np.asarray([0.0]),
            "resonance_frequency_hz": np.asarray([90000.0]),
            "main_field_current_ma": np.asarray([9.28495765474467]),
        },
    )
    monkeypatch.setattr(
        workflow,
        "_validate_calibrated_current",
        lambda params, value: events.append(("validate", value)) or value,
    )
    monkeypatch.setattr(
        workflow,
        "_temperature_gated_acquire",
        lambda *args, **kwargs: {
            "time_s": np.arange(10, dtype=float),
            "r": np.ones(10, dtype=float),
        },
    )
    monkeypatch.setattr(
        workflow,
        "_sleep",
        lambda seconds: (_ for _ in ()).throw(
            AssertionError("主场电流切换后不应直接等待")
        ),
    )
    _acquire_scan(
        MxMainFieldNoiseSpectrumParams(control_frequency_points=10),
        SimpleNamespace(root=run_root, raw=raw),
        {"gs200": ScanGS(), "hf2": object(), "temp_switch": object()},
        {"temp_switch": 2},
        100000.0,
        "dev",
    )
    assert events[:3] == [
        ("validate", 9.28495765474467),
        ("current", pytest.approx(9.28495765474467e-3)),
        ("output", True),
    ]


class _StateGS:
    def __init__(self) -> None:
        self.source = "CURR"
        self.current_a = 0.0091
        self.output = True
        self.current_range_a = 0.01
        self.current_limit_a = 0.0098
        self.calls: list[tuple] = []

    def get_source_function(self):
        return self.source

    def get_current(self):
        return self.current_a

    def get_output(self):
        return self.output

    def get_current_range(self):
        return self.current_range_a

    def get_current_limit(self):
        return self.current_limit_a

    def set_output(self, value):
        self.output = value
        self.calls.append(("output", value))

    def set_source_function(self, value):
        self.source = value
        self.calls.append(("source", value))

    def set_current_limit(self, value):
        self.current_limit_a = value
        self.calls.append(("limit", value))

    def set_current(self, value):
        self.current_a = value
        self.calls.append(("current", value))

    def set_current_range(self, value):
        self.current_range_a = value
        self.calls.append(("range", value))


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


class _TEC:
    def __init__(self) -> None:
        self.disconnected = False

    def disconnect(self) -> None:
        self.disconnected = True


def test_safe_shutdown_zeros_fields_restores_temperature_and_main_field() -> None:
    gs200 = _StateGS()
    state = snapshot_main_field_state(gs200)
    guard = StateGuard()
    guard.add("恢复 GS200", lambda: restore_main_field_state(gs200, state))
    z_field = _ShutdownDG()
    xy_field = _ShutdownDG()
    temperature = _ShutdownDG()
    tec = _TEC()
    gs200.set_current(0.0042)
    report = safe_shutdown(
        {
            "gs200": gs200,
            "z_field": z_field,
            "xy_field": xy_field,
            "temp_switch": temperature,
            "tec": tec,
        },
        {"z_field": 1, "x_field": 1, "y_rf": 2, "temp_switch": 2},
        MxMainFieldNoiseSpectrumParams(),
        guard,
    )
    assert report.completed
    assert ("dc", 1, 0.0) in z_field.calls
    assert ("output", 1, False) in z_field.calls
    assert ("dc", 1, 0.0) in xy_field.calls
    assert ("dc", 2, 0.0) in xy_field.calls
    assert ("dc", 2, 5.0) in temperature.calls
    assert ("output", 2, True) in temperature.calls
    assert gs200.calls[-1] == ("output", True)
    assert ("current", 0.0091) in gs200.calls
    assert tec.disconnected


@pytest.mark.parametrize("outcome", ["success", "error", "cancel"])
def test_run_restores_main_field_on_every_exit_path(
    monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    import lab_workflows.experiment_modules.mx_main_field_noise_spectrum.workflow as workflow

    gs200 = _StateGS()
    config_path = MagicMock()
    config_path.exists.return_value = True
    run_dir = SimpleNamespace(
        root=Path("D:/synthetic/main-field-noise-run"),
        config_path=config_path,
        update_config=MagicMock(),
    )
    monkeypatch.setattr(workflow, "find_project_root", lambda: Path("D:/synthetic"))
    monkeypatch.setattr(
        workflow, "load_mapping", lambda root: {"lockin_r": {"device_id": "dev"}}
    )
    monkeypatch.setattr(workflow, "create_run_directory", lambda *a, **k: run_dir)
    monkeypatch.setattr(workflow, "check_cancelled", lambda: None)
    monkeypatch.setattr(
        workflow,
        "_connect_devices",
        lambda mapping, session: ({"gs200": gs200}, {}),
    )
    monkeypatch.setattr(workflow, "_initial_state_snapshot", lambda *a: {})
    monkeypatch.setattr(
        workflow, "_configure_outputs", lambda *a, **k: (1000.0, 120.0, {}, {})
    )

    def acquire(*args, **kwargs):
        gs200.set_current(0.0042)
        gs200.set_output(True)
        if outcome == "error":
            raise RuntimeError("synthetic failure")
        if outcome == "cancel":
            raise WorkflowCancelled("synthetic cancellation")
        return []

    monkeypatch.setattr(workflow, "_acquire_scan", acquire)

    if outcome == "success":
        assert run(MxMainFieldNoiseSpectrumParams()) == run_dir.root
    else:
        expected = RuntimeError if outcome == "error" else WorkflowCancelled
        with pytest.raises(expected):
            run(MxMainFieldNoiseSpectrumParams())
    current_calls = [call for call in gs200.calls if call[0] == "current"]
    output_calls = [call for call in gs200.calls if call[0] == "output"]
    assert current_calls[-1] == ("current", 0.0091)
    assert output_calls[-1] == ("output", True)


def test_offline_analysis_uses_actual_rate_and_rebuilds_both_axes(
    local_tmp_path,
) -> None:
    run_dir = local_tmp_path / "mx_main_field_noise_run"
    raw_dir = run_dir / "raw"
    (run_dir / "results").mkdir(parents=True)
    raw_dir.mkdir()
    params = MxMainFieldNoiseSpectrumParams(
        control_frequency_stop_hz=500.0,
        control_frequency_points=10,
        requested_rate_sa_s=1000.0,
        welch_nperseg=100,
    )
    (run_dir / "experiment_config.yaml").write_text(
        yaml.safe_dump(
            {
                "experiment_id": "mx-main-field-noise-spectrum",
                "schema_version": 1,
                "parameters": params.to_external(),
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    rng = np.random.default_rng(20260720)
    axes = build_scan_axes(params)
    for index in range(params.control_frequency_points):
        time_s = np.arange(1000, dtype=float) / 1000.0
        r_v = (
            1.0
            + 1.0e-3
            * np.sin(
                2.0
                * np.pi
                * (50.0 + axes["control_frequency_hz"][index] / 20.0)
                * time_s
            )
            + rng.normal(scale=1.0e-4, size=time_s.size)
        )
        np.savez(
            raw_dir / f"waveform_I{index:04d}.npz",
            time_s=time_s,
            r_v=r_v,
            point_index=np.int64(index),
            control_frequency_hz=np.float64(
                axes["control_frequency_hz"][index]
            ),
            signed_detuning_hz=np.float64(
                axes["signed_detuning_hz"][index]
            ),
            resonance_frequency_hz=np.float64(
                axes["resonance_frequency_hz"][index]
            ),
            main_field_current_ma=np.float64(
                axes["main_field_current_ma"][index]
            ),
            actual_rate_sa_s=np.float64(1000.0),
        )

    result = analyze(run_dir)
    assert result["scan"]["completed_points"] == 10
    assert result["welch"]["actual_rate_sa_s"] == 1000.0
    psd = np.load(run_dir / "results" / "psd_matrix.npz")
    np.testing.assert_allclose(
        psd["control_frequency_hz"], axes["control_frequency_hz"], atol=1e-6
    )
    np.testing.assert_allclose(
        psd["signed_detuning_hz"], -axes["control_frequency_hz"], atol=1e-6
    )
    for filename in (
        "noise_spectra.npz",
        "main_field_control_axis.png",
        "noise_spectrum_2d.png",
        "analysis.yaml",
    ):
        assert (run_dir / "results" / filename).is_file()
