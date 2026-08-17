"""Mx Keithley 6221 最优控制 XYZ 平衡场实验测试。"""

from __future__ import annotations

from pathlib import Path
import shutil
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import numpy as np
import pytest
import yaml

from lab_workflows.experiment_modules.mx_keithley_6221_optimal_control_xyz_balance import (
    analysis as analysis_module,
)
from lab_workflows.experiment_modules.mx_keithley_6221_optimal_control_xyz_balance import (
    ADAPTER,
    DEFINITION,
)
from lab_workflows.experiment_modules.mx_keithley_6221_optimal_control_xyz_balance.analysis import (
    analyze,
)
from lab_workflows.experiment_modules.mx_keithley_6221_optimal_control_xyz_balance.models import (
    MxKeithley6221OptimalControlXYZBalanceParams,
)
from lab_workflows.experiment_modules.mx_keithley_6221_optimal_control_xyz_balance.scan import (
    build_xyz_axes,
    first_acquired_minimum,
    iter_serpentine_grid,
)
from lab_workflows.experiment_modules.mx_keithley_6221_optimal_control_xyz_balance.workflow import (
    _XYZFieldController,
    _restore_dg_channel,
    _snapshot_dg_channel,
    safe_shutdown,
)
from lab_workflows.experiments import get_experiment


@pytest.fixture
def local_tmp_path() -> Path:
    root = Path(__file__).resolve().parents[2] / "data"
    path = root / f".test_mx_6221_xyz_balance_{uuid4().hex}"
    path.mkdir(mode=0o777)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _params(**overrides) -> MxKeithley6221OptimalControlXYZBalanceParams:
    values = {
        "x_field_start_v": -0.01,
        "x_field_stop_v": 0.01,
        "x_field_points": 2,
        "y_field_start_v": -0.02,
        "y_field_stop_v": 0.02,
        "y_field_points": 2,
        "z_field_start_ma": -0.03,
        "z_field_stop_ma": 0.03,
        "z_field_points": 2,
    }
    values.update(overrides)
    return MxKeithley6221OptimalControlXYZBalanceParams(**values)


class _FakeDG:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.shape = {1: "SINusoid", 2: "DC"}
        self.frequency = {1: 1234.0, 2: 0.0}
        self.amplitude = {1: 0.4, 2: 0.0}
        self.offset = {1: 0.05, 2: -0.02}
        self.phase = {1: 20.0, 2: 0.0}
        self.burst = {1: True, 2: False}
        self.burst_mode = {1: "INFinity", 2: "TRIGgered"}
        self.burst_ncycles = {1: 8.0, 2: 2.0}
        self.burst_phase = {1: 30.0, 2: 0.0}
        self.burst_period = {1: 0.02, 2: 0.03}
        self.burst_delay = {1: 0.004, 2: 0.0}
        self.trigger_source = {1: "EXTernal", 2: "INTernal"}
        self.trigger_slope = {1: "POSitive", 2: "POSitive"}
        self.mod = {1: False, 2: True}
        self.output = {1: True, 2: False}

    def _record(self, name: str, channel: int, value) -> None:
        self.calls.append((name, channel, value))

    def get_shape(self, *, channel: int) -> str:
        return self.shape[channel]

    def set_shape(self, value: str, *, channel: int) -> None:
        self.shape[channel] = value
        self._record("shape", channel, value)

    def get_frequency(self, *, channel: int) -> float:
        return self.frequency[channel]

    def set_frequency(self, value: float, *, channel: int) -> None:
        self.frequency[channel] = value

    def get_amplitude(self, *, channel: int) -> float:
        return self.amplitude[channel]

    def set_amplitude(self, value: float, *, channel: int) -> None:
        self.amplitude[channel] = value

    def get_offset(self, *, channel: int) -> float:
        return self.offset[channel]

    def set_offset(self, value: float, *, channel: int) -> None:
        self.offset[channel] = value

    def get_phase_adjust(self, *, channel: int) -> float:
        return self.phase[channel]

    def set_phase_adjust(self, value: float, *, channel: int) -> None:
        self.phase[channel] = value

    def get_burst_state(self, *, channel: int) -> bool:
        return self.burst[channel]

    def set_burst_state(self, value: bool, *, channel: int) -> None:
        self.burst[channel] = value

    def get_burst_mode(self, *, channel: int) -> str:
        return self.burst_mode[channel]

    def set_burst_mode(self, value: str, *, channel: int) -> None:
        self.burst_mode[channel] = value

    def get_burst_ncycles(self, *, channel: int) -> float:
        return self.burst_ncycles[channel]

    def set_burst_ncycles(self, value: float, *, channel: int) -> None:
        self.burst_ncycles[channel] = value

    def get_burst_phase(self, *, channel: int) -> float:
        return self.burst_phase[channel]

    def set_burst_phase(self, value: float, *, channel: int) -> None:
        self.burst_phase[channel] = value

    def get_burst_period(self, *, channel: int) -> float:
        return self.burst_period[channel]

    def set_burst_period(self, value: float, *, channel: int) -> None:
        self.burst_period[channel] = value

    def get_burst_delay(self, *, channel: int) -> float:
        return self.burst_delay[channel]

    def set_burst_delay(self, value: float, *, channel: int) -> None:
        self.burst_delay[channel] = value

    def get_burst_trigger_source(self, *, channel: int) -> str:
        return self.trigger_source[channel]

    def set_burst_trigger_source(self, value: str, *, channel: int) -> None:
        self.trigger_source[channel] = value

    def get_burst_trigger_slope(self, *, channel: int) -> str:
        return self.trigger_slope[channel]

    def set_burst_trigger_slope(self, value: str, *, channel: int) -> None:
        self.trigger_slope[channel] = value

    def get_mod_state(self, *, channel: int) -> bool:
        return self.mod[channel]

    def set_mod_state(self, value: bool, *, channel: int) -> None:
        self.mod[channel] = value

    def get_output(self, *, channel: int) -> bool:
        return self.output[channel]

    def set_output(self, value: bool, *, channel: int) -> None:
        self.output[channel] = value
        self._record("output", channel, value)

    def setup_dc(self, value: float, *, channel: int) -> None:
        self.shape[channel] = "DC"
        self.offset[channel] = value
        self.output[channel] = True
        self._record("dc", channel, value)


class _FakeGS200:
    def __init__(self) -> None:
        self.current_a = 0.0
        self.output_on = False
        self.calls: list[tuple] = []

    def set_current(self, value: float) -> None:
        self.current_a = value
        self.calls.append(("current", value))

    def set_output(self, value: bool) -> None:
        self.output_on = value
        self.calls.append(("output", value))


def test_definition_and_defaults_are_registered() -> None:
    definition = get_experiment(DEFINITION.id)
    assert definition.execution_mode == "typed_workflow"
    assert definition.data_type == "Mx_Keithley_6221_Optimal_Control_XYZ_Balance"
    assert "keithley_6221_main_field" in definition.required_mapping_keys
    assert "Y_magnetic_field" in definition.required_mapping_keys
    defaults = ADAPTER.defaults()
    assert defaults.x_field_points == 11
    assert defaults.y_field_points == 11
    assert defaults.z_field_points == 5
    assert defaults.control_version == "v4"
    assert defaults.demod_frequency_hz == pytest.approx(12000.0)
    assert defaults.trigger_frequency_hz == pytest.approx(12000.0)


def test_schema_fields_and_derived_values() -> None:
    fields = {item["name"]: item for item in DEFINITION.schema()["fields"]}
    assert fields["TRIGGER_FREQUENCY_HZ"]["read_only"] is True
    assert fields["DEMOD_FREQUENCY_HZ"]["read_only"] is True
    assert fields["KEITHLEY_CURRENT_RANGE_MA"]["default"] == pytest.approx(100.0)
    assert "Y_RF_FREQUENCY_HZ" not in fields
    derived = ADAPTER.derive({})
    assert derived["TRIGGER_FREQUENCY_HZ"] == pytest.approx(12000.0)
    assert derived["DEMOD_FREQUENCY_HZ"] == pytest.approx(12000.0)


def test_single_point_axis_requires_equal_start_and_stop() -> None:
    params = _params(
        x_field_start_v=0.004,
        x_field_stop_v=0.004,
        x_field_points=1,
    )
    x_axis, _, _ = build_xyz_axes(params)
    assert x_axis.tolist() == [0.004]
    with pytest.raises(ValueError, match="START=STOP"):
        build_xyz_axes(
            _params(
                x_field_start_v=0.004,
                x_field_stop_v=0.005,
                x_field_points=1,
            )
        )


def test_xyz_serpentine_grid_keeps_zyx_indices() -> None:
    points = list(iter_serpentine_grid(_params()))
    assert [(item[1], item[2], item[3]) for item in points] == [
        (0, 0, 0),
        (0, 0, 1),
        (0, 1, 1),
        (0, 1, 0),
        (1, 1, 0),
        (1, 1, 1),
        (1, 0, 1),
        (1, 0, 0),
    ]


def test_first_acquired_minimum_breaks_ties_by_acquisition_order() -> None:
    best = first_acquired_minimum(
        [
            {"r_mean_v": 1.0, "acquisition_index": 0},
            {"r_mean_v": 0.5, "acquisition_index": 4},
            {"r_mean_v": 0.5, "acquisition_index": 2},
        ]
    )
    assert best["acquisition_index"] == 2


def test_field_controller_keeps_zero_outputs_on_and_changes_z_by_plane() -> None:
    dg = _FakeDG()
    gs200 = _FakeGS200()
    controller = _XYZFieldController(
        dg,
        gs200,
        {"x_field": 1, "y_rf": 2},
    )
    controller.apply(0.0, 0.0, -0.01)
    controller.apply(0.001, -0.002, -0.01)
    assert dg.output[1] is True
    assert dg.output[2] is True
    assert ("dc", 1, 0.0) in dg.calls
    assert ("dc", 2, 0.0) in dg.calls
    assert gs200.calls.count(("current", -0.01 / 1000.0)) == 1
    assert gs200.output_on is True


def test_dg_channel_state_is_restored_after_dc_scan() -> None:
    dg = _FakeDG()
    state = _snapshot_dg_channel(dg, 1)
    controller = _XYZFieldController(
        dg,
        _FakeGS200(),
        {"x_field": 1, "y_rf": 2},
    )
    controller.apply(0.001, 0.002, 0.0)
    _restore_dg_channel(dg, 1, state)
    assert dg.shape[1] == "SINusoid"
    assert dg.frequency[1] == pytest.approx(1234.0)
    assert dg.amplitude[1] == pytest.approx(0.4)
    assert dg.offset[1] == pytest.approx(0.05)
    assert dg.phase[1] == pytest.approx(20.0)
    assert dg.burst[1] is True
    assert dg.output[1] is True


def test_safe_shutdown_preserves_6221_and_trigger() -> None:
    keithley = MagicMock()
    trigger = MagicMock()
    report = safe_shutdown(
        {"keithley": keithley, "trigger": trigger},
        {"trigger": 2},
        MxKeithley6221OptimalControlXYZBalanceParams(confirm_gs200_connected=True),
    )
    assert report.completed
    keithley.abort_waveform.assert_not_called()
    keithley.set_output.assert_not_called()
    keithley.set_current.assert_not_called()
    trigger.setup_dc.assert_not_called()
    trigger.set_output.assert_not_called()


def test_preflight_requires_gs200_connection_confirmation() -> None:
    params = MxKeithley6221OptimalControlXYZBalanceParams(
        confirm_gs200_connected=False,
    )
    errors = params.validate(Path(__file__).resolve().parents[2])
    assert any("GS200" in error for error in errors)


def test_analysis_reports_measured_minimum_and_writes_outputs(
    local_tmp_path: Path,
) -> None:
    run_dir = local_tmp_path / "run"
    raw_dir = run_dir / "raw"
    raw_dir.mkdir(parents=True)
    (run_dir / "experiment_config.yaml").write_text(
        yaml.safe_dump(
            {
                "completion_status": "completed",
                "control_source": {"version": "v4"},
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    x_axis = np.array([-0.01, 0.0, 0.01])
    y_axis = np.array([-0.01, 0.0, 0.01])
    z_axis = np.array([-0.01, 0.0, 0.01])
    z_grid, x_grid, y_grid = np.meshgrid(
        z_axis,
        x_axis,
        y_axis,
        indexing="ij",
    )
    r_mean = (
        (x_grid - 0.0) ** 2
        + (y_grid - 0.01) ** 2
        + (z_grid + 0.01) ** 2
    )
    np.savez(
        raw_dir / "xyz_balance_scan.npz",
        x_field_v=x_axis,
        y_field_v=y_axis,
        z_field_ma=z_axis,
        r_mean_v=r_mean,
        r_std_v=np.full(r_mean.shape, 0.001),
        acquisition_order=np.arange(r_mean.size).reshape(r_mean.shape),
        actual_rate_sa_s=np.float64(1000.0),
    )
    result = analyze(run_dir)
    best = result["measured_grid_minimum"]
    assert best["x_field_v"] == pytest.approx(0.0)
    assert best["y_field_v"] == pytest.approx(0.01)
    assert best["z_field_ma"] == pytest.approx(-0.01)
    assert best["remeasured"] is False
    assert result["experiment_id"] == "mx-keithley-6221-optimal-control-xyz-balance"
    assert (run_dir / "results" / "analysis.yaml").is_file()
    assert (run_dir / "results" / "balance_results.npz").is_file()
    assert (run_dir / "results" / "xyz_balance_grid.csv").is_file()
    assert (run_dir / "results" / "xyz_balance_slices.png").is_file()


def test_analysis_supports_all_axes_fixed(local_tmp_path: Path) -> None:
    run_dir = local_tmp_path / "fixed"
    raw_dir = run_dir / "raw"
    raw_dir.mkdir(parents=True)
    (run_dir / "experiment_config.yaml").write_text(
        "completion_status: completed\n",
        encoding="utf-8",
    )
    np.savez(
        raw_dir / "xyz_balance_scan.npz",
        x_field_v=np.array([0.001]),
        y_field_v=np.array([-0.002]),
        z_field_ma=np.array([0.003]),
        r_mean_v=np.array([[[0.4]]]),
        r_std_v=np.array([[[0.01]]]),
        acquisition_order=np.array([[[0]]]),
        actual_rate_sa_s=np.float64(1000.0),
    )
    result = analyze(run_dir)
    assert result["scan_shape_zyx"] == [1, 1, 1]
    assert result["measured_grid_minimum"]["r_mean_v"] == pytest.approx(0.4)


def test_analysis_linear_fit_recovers_workpoint_on_vshape_data(
    local_tmp_path: Path,
) -> None:
    run_dir = local_tmp_path / "vshape"
    raw_dir = run_dir / "raw"
    raw_dir.mkdir(parents=True)
    (run_dir / "experiment_config.yaml").write_text(
        yaml.safe_dump(
            {
                "completion_status": "completed",
                "control_source": {"version": "v4"},
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    x_axis = np.linspace(-0.03, 0.03, 13)
    y_axis = np.linspace(-0.03, 0.03, 13)
    z_axis = np.array([-0.02, -0.01, 0.0, 0.01, 0.02])
    z_grid, x_grid, y_grid = np.meshgrid(z_axis, x_axis, y_axis, indexing="ij")
    x0 = -z_grid
    y0 = 0.5 * z_grid + 0.01
    r_mean = (
        np.abs(2.0 * (x_grid - x0))
        + np.abs(1.5 * (y_grid - y0))
        + 0.02 * np.abs(z_grid - 0.01)
        + 0.002
    )
    np.savez(
        raw_dir / "xyz_balance_scan.npz",
        x_field_v=x_axis,
        y_field_v=y_axis,
        z_field_ma=z_axis,
        r_mean_v=r_mean,
        r_std_v=np.full(r_mean.shape, 0.0005),
        acquisition_order=np.arange(r_mean.size).reshape(r_mean.shape),
        actual_rate_sa_s=np.float64(1000.0),
    )
    result = analyze(run_dir)
    workpoint = result["linear_fit"]["fitted_workpoint"]
    assert workpoint["z_field_ma"] == pytest.approx(0.01, abs=0.001)
    assert workpoint["x_field_v"] == pytest.approx(-0.01, abs=0.002)
    assert workpoint["y_field_v"] == pytest.approx(0.015, abs=0.002)
    assert workpoint["extrapolated"] is False
    assert result["linear_fit"]["coupling"]["x0_slope_v_per_ma"] == pytest.approx(
        -1.0, abs=0.05
    )
    assert result["interpretation"]["continuous_fit_performed"] is True
    assert (run_dir / "results" / "balance_linear_fit.npz").is_file()
    assert (run_dir / "results" / "xyz_balance_linear_fit.png").is_file()
