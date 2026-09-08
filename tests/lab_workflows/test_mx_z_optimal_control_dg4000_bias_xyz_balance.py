"""DG4000 Z 偏置 Mx XYZ 平衡场实验测试。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import matplotlib
import numpy as np
import pytest
import yaml

matplotlib.use("Agg")

from lab_workflows.experiment_modules.mx_z_optimal_control_dg4000_bias_xyz_balance import (
    ADAPTER,
    DEFINITION,
)
from lab_workflows.experiment_modules.mx_z_optimal_control_dg4000_bias_xyz_balance.analysis import (
    _complex_surface,
    analyze,
)
from lab_workflows.experiment_modules.mx_z_optimal_control_dg4000_bias_xyz_balance.models import (
    MxZOptimalControlDG4000BiasXYZBalanceParams,
)
from lab_workflows.experiment_modules.mx_z_optimal_control_dg4000_bias_xyz_balance.scan import (
    build_xyz_axes,
    first_acquired_minimum,
)
from lab_workflows.experiment_modules.mx_z_optimal_control_dg4000_bias_xyz_balance.workflow import (
    _XYZBiasController,
    _validate_bias_envelope,
)
from lab_workflows.current_feedback import load_corrected_control_waveform
from lab_workflows.experiment_modules.mx_z_optimal_control_rf_sensitivity.sources import (
    corrected_control_contract,
)
from lab_workflows.experiments import get_experiment


def test_definition_schema_and_mapping_contract() -> None:
    definition = get_experiment(DEFINITION.id)
    assert definition.execution_mode == "typed_workflow"
    assert definition.data_type == "Mx_Z_Optimal_Control_DG4000_Bias_XYZ_Balance"
    assert "GS200" not in definition.required_devices
    fields = {field["name"] for field in ADAPTER.schema()["fields"]}
    assert {"Z_BIAS_START_V", "Z_BIAS_STOP_V", "Z_BIAS_POINTS"} <= fields
    assert "CONFIRM_GS200_DISCONNECTED" in fields


def test_control_waveform_source_defaults_and_v1_migration() -> None:
    fields = {field["name"]: field for field in ADAPTER.schema()["fields"]}
    assert fields["CONTROL_WAVEFORM_SOURCE"]["default"] == "corrected_run"
    assert (
        fields["CORRECTED_CONTROL_SOURCE_RUN"]["default"]
        == "0824_094157_z_aw_closed_loop_waveform_correction"
    )
    legacy = MxZOptimalControlDG4000BiasXYZBalanceParams.from_external(
        {}, schema_version=1
    )
    assert legacy.control_waveform_source == "theory"
    assert legacy.corrected_control_source_run == ""


def test_corrected_control_contract_uses_frozen_voltage() -> None:
    root = Path(__file__).resolve().parents[2]
    corrected = load_corrected_control_waveform(
        root,
        "0824_094157_z_aw_closed_loop_waveform_correction",
    )
    theory, applied = corrected_control_contract(corrected)
    assert theory.version.startswith("corrected:")
    assert np.array_equal(applied.voltage_v, corrected.voltage_v)
    assert np.array_equal(applied.normalized, corrected.normalized)
    assert applied.amplitude_vpp == pytest.approx(corrected.amplitude_vpp)
    assert applied.offset_v == pytest.approx(corrected.offset_v)


def test_model_requires_gs200_disconnect_confirmation() -> None:
    params = MxZOptimalControlDG4000BiasXYZBalanceParams()
    assert any("CONFIRM_GS200_DISCONNECTED" in error for error in params.validate_model())
    params.confirm_gs200_disconnected = True
    assert not any(
        "CONFIRM_GS200_DISCONNECTED" in error
        for error in params.validate_model()
    )


def test_bias_axis_and_minimum_selection() -> None:
    params = MxZOptimalControlDG4000BiasXYZBalanceParams(
        confirm_gs200_disconnected=True,
        x_field_start_v=-0.01,
        x_field_stop_v=0.01,
        x_field_points=2,
        y_field_start_v=-0.02,
        y_field_stop_v=0.02,
        y_field_points=2,
        z_field_start_ma=-0.03,
        z_field_stop_ma=0.03,
        z_field_points=3,
    )
    _, _, z_axis = build_xyz_axes(params)
    assert z_axis.tolist() == [-0.03, 0.0, 0.03]
    assert first_acquired_minimum(
        [
            {"r_mean_v": 0.2, "acquisition_index": 0},
            {"r_mean_v": 0.1, "acquisition_index": 2},
            {"r_mean_v": 0.1, "acquisition_index": 1},
        ]
    )["acquisition_index"] == 1


class _FakeDG:
    def __init__(self) -> None:
        self.offset = {1: 0.0, 2: 0.0}
        self.output = {1: False, 2: False}
        self.calls: list[tuple[str, int, float | bool]] = []

    def set_offset(self, value: float, *, channel: int) -> None:
        self.offset[channel] = float(value)
        self.calls.append(("offset", channel, float(value)))

    def set_burst_state(self, value: bool, *, channel: int) -> None:
        self.calls.append(("burst", channel, value))

    def set_mod_state(self, value: bool, *, channel: int) -> None:
        self.calls.append(("mod", channel, value))

    def setup_dc(self, value: float, *, channel: int) -> None:
        self.calls.append(("dc", channel, float(value)))

    def set_output(self, value: bool, *, channel: int) -> None:
        self.output[channel] = bool(value)
        self.calls.append(("output", channel, bool(value)))


def test_controller_changes_z_offset_without_overwriting_aw() -> None:
    xy = _FakeDG()
    z = _FakeDG()
    applied = SimpleNamespace(
        offset_v=0.2,
        minimum_v=-0.5,
        maximum_v=0.5,
    )
    controller = _XYZBiasController(
        xy,
        z,
        {"x_field": 1, "y_rf": 2, "z_field": 1},
        applied,
        2.0,
    )
    controller.apply(0.01, -0.02, 0.1)
    controller.apply(0.01, -0.02, 0.1)
    assert z.offset[1] == pytest.approx(0.3)
    assert [call for call in z.calls if call[0] == "offset"] == [
        ("offset", 1, pytest.approx(0.3)),
    ]
    assert not any(call[0] == "dc" for call in z.calls)
    assert xy.output[1] is True
    assert xy.output[2] is True


def test_bias_envelope_checks_waveform_and_output_limits() -> None:
    applied = SimpleNamespace(offset_v=0.0, minimum_v=-0.5, maximum_v=0.5)
    assert _validate_bias_envelope(applied, 0.25, 2.0) == pytest.approx(0.25)
    with pytest.raises(ValueError):
        _validate_bias_envelope(applied, 10.0, 2.0)


def _write_synthetic_run(run_dir: Path) -> None:
    raw_dir = run_dir / "raw"
    raw_dir.mkdir(parents=True)
    (run_dir / "experiment_config.yaml").write_text(
        yaml.safe_dump(
            {
                "completion_status": "completed",
                "z_control_calibration": {
                    "slope_hz_per_v": 100.0,
                    "intercept_hz": 1000.0,
                    "gamma_hz_per_nt": 7.0,
                },
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    x_axis = np.linspace(-0.03, 0.03, 7)
    y_axis = np.linspace(-0.03, 0.03, 7)
    z_axis = np.array([-0.02, 0.0, 0.02])
    z_grid, x_grid, y_grid = np.meshgrid(
        z_axis,
        x_axis,
        y_axis,
        indexing="ij",
    )
    x_response = np.empty_like(x_grid)
    y_response = np.empty_like(y_grid)
    for z_index in range(z_axis.size):
        parameters = np.asarray(
            [
                0.004 + 0.0002 * z_index,
                -0.003 + 0.0001 * z_index,
                -0.006,
                0.004,
                0.012,
                0.015,
                0.035,
                0.008,
                -0.006,
                0.028,
            ]
        )
        sx, sy = _complex_surface(parameters, x_grid[z_index], y_grid[z_index])
        x_response[z_index] = sx
        y_response[z_index] = sy
    r_mean = 0.01 * np.hypot(x_response, y_response) + (z_grid + 0.02) ** 2
    shape = r_mean.shape
    np.savez(
        raw_dir / "xyz_balance_scan.npz",
        x_field_v=x_axis,
        y_field_v=y_axis,
        z_bias_v=z_axis,
        z_bias_frequency_hz=100.0 * z_axis,
        z_bias_absolute_bz_nt=(1000.0 + 100.0 * z_axis) / 7.0,
        r_mean_v=r_mean,
        r_std_v=np.full(shape, 0.001),
        x_mean_v=x_response,
        y_mean_v=y_response,
        x_std_v=np.full(shape, 0.001),
        y_std_v=np.full(shape, 0.001),
        complex_std_v=np.full(shape, 0.0014),
        acquisition_order=np.arange(r_mean.size).reshape(shape),
        actual_rate_sa_s=np.float64(1000.0),
    )


def test_analysis_reports_bias_hz_nt_and_components(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _write_synthetic_run(run_dir)
    result = analyze(run_dir)
    best = result["measured_grid_minimum"]
    assert result["criterion"] == "robust complex Demod X/Y Bloch fit"
    assert result["interpretation"]["bloch_fit_available"] is True
    assert best["z_bias_v"] == pytest.approx(-0.02)
    assert best["z_bias_frequency_hz"] == pytest.approx(-2.0)
    assert best["z_bias_absolute_bz_nt"] == pytest.approx(998.0 / 7.0)
    fitted = result["fitted_balance_point"]
    assert fitted["x_field_v"] == pytest.approx(-0.006, abs=5.0e-4)
    assert fitted["y_field_v"] == pytest.approx(0.004, abs=5.0e-4)
    assert result["bloch_fit"]["plane_count"] == 3
    assert result["bloch_fit"]["median_r_squared"] > 0.99
    assert (run_dir / "results" / "xyz_balance_components.png").is_file()
    assert (run_dir / "results" / "xyz_balance_grid.csv").is_file()
    assert (run_dir / "results" / "bloch_fit_analysis.yaml").is_file()
    assert (run_dir / "results" / "bloch_fit_analysis.json").is_file()
    assert (run_dir / "results" / "bloch_fit_analysis.npz").is_file()
    assert (run_dir / "results" / "bloch_fit_summary.png").is_file()
