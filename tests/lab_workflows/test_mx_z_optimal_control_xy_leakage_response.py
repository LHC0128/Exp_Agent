"""Mx Z 最优控制 XY 泄露响应实验测试。"""

from __future__ import annotations

from pathlib import Path
import shutil
from types import SimpleNamespace
from uuid import uuid4

import numpy as np
import pytest
import yaml

from lab_workflows.experiment_modules.mx_z_optimal_control_xy_leakage_response import (
    ADAPTER,
    DEFINITION,
)
from lab_workflows.experiment_modules.mx_z_optimal_control_xy_leakage_response.analysis import (
    analyze,
)
from lab_workflows.experiment_modules.mx_z_optimal_control_xy_leakage_response.models import (
    MxZOptimalControlXYLeakageParams,
)
from lab_workflows.experiment_modules.mx_z_optimal_control_xy_leakage_response.scan import (
    first_acquired_minimum,
    iter_serpentine_grid,
    signed_control_hardware,
)
from lab_workflows.experiment_modules.mx_z_optimal_control_xy_leakage_response.workflow import (
    _XYWaveformController,
    _acquire_grid,
    safe_shutdown,
)
from lab_workflows.experiments import get_experiment


@pytest.fixture
def local_tmp_path() -> Path:
    root = Path(__file__).resolve().parents[2] / "data"
    path = root / f".test_mx_z_xy_leakage_{uuid4().hex}"
    path.mkdir(mode=0o777)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


class _FakeDG:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def _record(self, name: str, *args, **kwargs) -> None:
        self.calls.append((name, *args, kwargs))

    def set_output(self, state: bool, *, channel: int) -> None:
        self._record("output", channel, bool(state))

    def set_burst_state(self, state: bool, *, channel: int) -> None:
        self._record("burst", channel, bool(state))

    def set_mod_state(self, state: bool, *, channel: int) -> None:
        self._record("mod", channel, bool(state))

    def setup_dc(self, value: float, *, channel: int) -> None:
        self._record("dc", channel, float(value))

    def setup_arbitrary(
        self,
        values,
        *,
        freq: float,
        amplitude: float,
        offset: float,
        phase: float,
        channel: int,
        output: bool,
    ) -> None:
        self._record(
            "arbitrary",
            channel,
            np.asarray(values).copy(),
            float(freq),
            float(amplitude),
            float(offset),
            float(phase),
            bool(output),
        )

    def set_frequency(self, value: float, *, channel: int) -> None:
        self._record("frequency", channel, float(value))

    def set_amplitude(self, value: float, *, channel: int) -> None:
        self._record("amplitude", channel, float(value))

    def set_offset(self, value: float, *, channel: int) -> None:
        self._record("offset", channel, float(value))

    def set_burst_mode(self, mode: str, *, channel: int) -> None:
        self._record("burst_mode", channel, mode)

    def set_burst_trigger_source(
        self,
        source: str,
        *,
        channel: int,
    ) -> None:
        self._record("trigger_source", channel, source)

    def set_burst_trigger_slope(
        self,
        slope: str,
        *,
        channel: int,
    ) -> None:
        self._record("trigger_slope", channel, slope)

    def set_burst_phase(self, phase: float, *, channel: int) -> None:
        self._record("burst_phase", channel, float(phase))


def _params(**overrides) -> MxZOptimalControlXYLeakageParams:
    values = {
        "x_control_amp_start_vpp": -0.1,
        "x_control_amp_stop_vpp": 0.1,
        "x_control_amp_points": 3,
        "y_control_amp_start_vpp": -0.1,
        "y_control_amp_stop_vpp": 0.1,
        "y_control_amp_points": 3,
    }
    values.update(overrides)
    return MxZOptimalControlXYLeakageParams(**values)


def _controller(params=None):
    xy = _FakeDG()
    trigger = _FakeDG()
    controller = _XYWaveformController(
        xy,
        trigger,
        {"x_field": 1, "y_rf": 2, "trigger": 2},
        SimpleNamespace(repeat_frequency_hz=30000.0),
        SimpleNamespace(normalized=np.array([-0.5, 0.25, 0.5])),
        params or _params(),
    )
    return controller, xy, trigger


def test_definition_and_defaults_are_registered() -> None:
    definition = get_experiment(DEFINITION.id)
    assert definition.execution_mode == "typed_workflow"
    assert definition.data_type == "Mx_Z_Optimal_Control_XY_Leakage_Response"
    defaults = ADAPTER.defaults()
    assert defaults.control_version == "v2"
    assert defaults.demod_frequency_hz == pytest.approx(30000.0)
    assert defaults.x_control_amp_points == 21
    assert defaults.y_control_amp_points == 21
    assert ADAPTER.preflight({}) == []


def test_serpentine_grid_keeps_canonical_indices() -> None:
    points = list(iter_serpentine_grid(_params()))
    assert [(item[1], item[2]) for item in points] == [
        (0, 0),
        (0, 1),
        (0, 2),
        (1, 2),
        (1, 1),
        (1, 0),
        (2, 0),
        (2, 1),
        (2, 2),
    ]


@pytest.mark.parametrize(
    ("value", "expected_amplitude", "expected_phase", "output_on"),
    [
        (0.1, 0.1, 30.0, True),
        (-0.1, 0.1, 210.0, True),
        (0.0, 0.0, 30.0, False),
    ],
)
def test_signed_control_hardware(
    value: float,
    expected_amplitude: float,
    expected_phase: float,
    output_on: bool,
) -> None:
    amplitude, phase, state = signed_control_hardware(value, 30.0)
    assert amplitude == pytest.approx(expected_amplitude)
    assert phase == pytest.approx(expected_phase)
    assert state is output_on


def test_xy_controller_arms_both_channels_on_same_trigger() -> None:
    controller, xy, trigger = _controller(
        _params(control_burst_phase_deg=15.0)
    )
    hardware = controller.apply(-0.1, 0.1)
    assert hardware["x_hardware_phase_deg"] == pytest.approx(195.0)
    assert hardware["y_hardware_phase_deg"] == pytest.approx(15.0)
    assert trigger.calls[0] == ("output", 2, False, {})
    assert trigger.calls[-1] == ("output", 2, True, {})
    assert ("output", 1, True, {}) in xy.calls
    assert ("output", 2, True, {}) in xy.calls
    assert ("trigger_slope", 1, "NEGative", {}) in xy.calls
    assert ("trigger_slope", 2, "NEGative", {}) in xy.calls


def test_xy_controller_uses_dc_off_at_zero_and_reloads_aw() -> None:
    controller, xy, _ = _controller()
    controller.apply(0.1, 0.1)
    controller.apply(0.0, 0.1)
    controller.apply(-0.1, 0.1)
    x_arbitrary_calls = [
        call for call in xy.calls if call[0] == "arbitrary" and call[1] == 1
    ]
    assert len(x_arbitrary_calls) == 2
    assert ("dc", 1, 0.0, {}) in xy.calls
    assert xy.calls.count(("output", 1, False, {})) >= 3


def test_first_acquired_minimum_breaks_ties_by_acquisition_order() -> None:
    best = first_acquired_minimum(
        [
            {"r_mean_v": 1.0, "acquisition_index": 0},
            {"r_mean_v": 0.5, "acquisition_index": 4},
            {"r_mean_v": 0.5, "acquisition_index": 2},
        ]
    )
    assert best["acquisition_index"] == 2


def test_acquire_grid_rearms_each_point_and_saves_canonical_matrix(
    local_tmp_path,
    monkeypatch,
) -> None:
    params = _params()
    controller, _, trigger = _controller(params)

    def fake_acquire(
        params,
        run_dir,
        devices,
        channels,
        *,
        file_stem,
        metadata,
        set_y_rf,
        settle_time_s,
        duration_s,
        actual_rate,
        device_id,
    ):
        set_y_rf()
        x = float(metadata["x_signed_amplitude_vpp"])
        y = float(metadata["y_signed_amplitude_vpp"])
        return (
            {
                "r_mean_v": x * x + y * y,
                "r_scalar_mean_v": x * x + y * y,
                "r_std_v": 0.001,
                "n_samples": 10,
            },
            0,
            f"{file_stem}_attempt_00.npz",
        )

    monkeypatch.setattr(
        "lab_workflows.experiment_modules."
        "mx_z_optimal_control_xy_leakage_response.workflow."
        "_acquire_valid_r_point",
        fake_acquire,
    )
    run_dir = SimpleNamespace(raw=local_tmp_path)
    records, best = _acquire_grid(
        params,
        run_dir,
        {"hf2": object()},
        {"temp_switch": 2},
        controller,
        1000.0,
        "dev",
    )
    assert len(records) == 9
    assert best["x_signed_vpp"] == pytest.approx(0.0)
    assert best["y_signed_vpp"] == pytest.approx(0.0)
    trigger_off = [
        call for call in trigger.calls if call == ("output", 2, False, {})
    ]
    trigger_on = [
        call for call in trigger.calls if call == ("output", 2, True, {})
    ]
    assert len(trigger_off) == len(trigger_on) == 9
    with np.load(local_tmp_path / "xy_leakage_scan.npz") as data:
        assert data["r_mean_v"].shape == (3, 3)
        assert data["r_mean_v"][1, 1] == pytest.approx(0.0)
        assert data["acquisition_order"].tolist() == [
            [0, 1, 2],
            [5, 4, 3],
            [6, 7, 8],
        ]


def test_analysis_reports_measured_minimum_and_writes_plot(
    local_tmp_path,
) -> None:
    run_dir = local_tmp_path / "run"
    raw_dir = run_dir / "raw"
    raw_dir.mkdir(parents=True)
    (run_dir / "experiment_config.yaml").write_text(
        yaml.safe_dump(
            {
                "completion_status": "completed",
                "control_source": {"version": "v2"},
                "trigger": {"source": "Time_sequence_2"},
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    x_axis = np.array([-0.1, 0.0, 0.1])
    y_axis = np.array([-0.1, 0.0, 0.1])
    r_mean = np.array(
        [
            [3.0, 2.0, 1.0],
            [2.0, 0.5, 1.0],
            [1.0, 0.5, 2.0],
        ]
    )
    np.savez(
        raw_dir / "xy_leakage_scan.npz",
        x_signed_amplitude_vpp=x_axis,
        y_signed_amplitude_vpp=y_axis,
        r_mean_v=r_mean,
        r_std_v=np.full((3, 3), 0.01),
        acquisition_order=np.array(
            [[0, 1, 2], [5, 4, 3], [6, 7, 8]]
        ),
        actual_rate_sa_s=np.float64(1000.0),
    )
    result = analyze(run_dir)
    best = result["measured_grid_minimum"]
    assert best["x_signed_amplitude_vpp"] == pytest.approx(0.0)
    assert best["y_signed_amplitude_vpp"] == pytest.approx(0.0)
    assert best["combined_state_remeasured"] is False
    assert (run_dir / "results" / "analysis.yaml").is_file()
    assert (run_dir / "results" / "analysis.json").is_file()
    assert (run_dir / "results" / "xy_leakage_response.png").is_file()


def test_safe_shutdown_only_schedules_trigger_channel(
    monkeypatch,
) -> None:
    captured = {}

    def fake_shutdown(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            errors=[],
            disconnect_errors=[],
            preserved_outputs=kwargs["preserved_outputs"],
        )

    monkeypatch.setattr(
        "lab_workflows.experiment_modules."
        "mx_z_optimal_control_xy_leakage_response.workflow."
        "run_safety_shutdown",
        fake_shutdown,
    )
    report = safe_shutdown(
        {"z_field": object()},
        {"trigger": 2},
        _params(),
    )
    assert len(captured["dg_channels"]) == 1
    assert "Z_magnetic_field" in report.preserved_outputs
    assert "X_magnetic_field" in report.preserved_outputs
    assert "Y_magnetic_field" in report.preserved_outputs
