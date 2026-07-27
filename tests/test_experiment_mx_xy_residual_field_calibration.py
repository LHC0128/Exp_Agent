"""Mx XY 剩磁二维校准实验测试。"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest
import yaml

from lab_workflows.experiment_modules.mx_xy_residual_field_calibration.analysis import (
    analyze,
    bloch_2d_surface,
    dispersive_line,
    fit_bloch_2d,
    fit_dispersive_line,
)
from lab_workflows.experiment_modules.mx_xy_residual_field_calibration.definition import DEFINITION
from lab_workflows.experiment_modules.mx_xy_residual_field_calibration.models import (
    MxXYResidualFieldCalibrationParams,
)
from lab_workflows.experiment_modules.mx_xy_residual_field_calibration.scan import (
    build_xy_axes,
    iter_grid,
)
from lab_workflows.experiment_modules.mx_xy_residual_field_calibration.workflow import (
    _acquire_all,
    _scope_settings,
    _set_xy_off_state,
    _set_xy_scan_state,
)
from lab_workflows.experiments.registry import get_experiment
from lab_workflows.steps import (
    ScopeAutoRangeState,
    ScopeCaptureSettings,
    acquire_autoranged_waveform,
)


def test_definition_defaults_axes_and_schema() -> None:
    params = MxXYResidualFieldCalibrationParams()
    assert params.validate() == []
    assert get_experiment("mx-xy-residual-field-calibration") is DEFINITION
    assert DEFINITION.execution_mode == "typed_workflow"
    assert "HF2" not in DEFINITION.required_devices
    x_axis, y_axis = build_xy_axes(params)
    assert x_axis.size == y_axis.size == 21
    assert x_axis[0] == pytest.approx(-0.05)
    assert x_axis[-1] == pytest.approx(0.05)
    assert y_axis[0] == pytest.approx(-0.05)
    assert y_axis[-1] == pytest.approx(0.05)
    grid = list(iter_grid(MxXYResidualFieldCalibrationParams(x_field_points=2, y_field_points=3)))
    assert [(item[0], item[1]) for item in grid] == [
        (0, 0),
        (0, 1),
        (0, 2),
        (1, 0),
        (1, 1),
        (1, 2),
    ]
    schema_names = {field["name"] for field in DEFINITION.schema()["fields"]}
    assert {"X_FIELD_START_V", "Y_FIELD_POINTS", "SCOPE_SAMPLE_RATE"} <= schema_names
    assert "REFERENCE_MAIN_FIELD_MA" not in schema_names
    assert "REFERENCE_REPEATS" not in schema_names
    assert MxXYResidualFieldCalibrationParams.schema_version == 2
    schema_fields = {field["name"]: field for field in DEFINITION.schema()["fields"]}
    assert schema_fields["X_FIELD_POINTS"]["minimum"] == 3
    assert schema_fields["Y_FIELD_POINTS"]["minimum"] == 7
    scope_settings = _scope_settings(params)
    assert scope_settings.offset_v == pytest.approx(0.0)
    assert scope_settings.auto_offset_enabled is False


def test_scope_offset_must_remain_zero() -> None:
    errors = MxXYResidualFieldCalibrationParams(scope_offset_v=0.1).validate()
    assert "SCOPE_OFFSET 必须固定为 0 V" in errors


def test_schema_v1_reference_parameters_are_migrated_away() -> None:
    params = MxXYResidualFieldCalibrationParams.from_external(
        {
            "REFERENCE_MAIN_FIELD_MA": 10.0,
            "REFERENCE_REPEATS": 3,
            "SCAN_MAIN_FIELD_MA": 0.0,
        },
        schema_version=1,
    )
    assert params.scan_main_field_ma == pytest.approx(0.0)


@pytest.mark.parametrize(
    "params, message",
    [
        (MxXYResidualFieldCalibrationParams(x_field_start_v=0.1), "X 场范围"),
        (MxXYResidualFieldCalibrationParams(x_field_points=2), "X_FIELD_POINTS"),
        (MxXYResidualFieldCalibrationParams(y_field_points=6), "Y_FIELD_POINTS"),
        (MxXYResidualFieldCalibrationParams(scope_sample_rate_sa_s=2.0, scope_duration_s=0.5), "请求点数"),
    ],
)
def test_preflight_rejects_invalid_contract(
    params: MxXYResidualFieldCalibrationParams, message: str
) -> None:
    assert any(message in error for error in params.validate())


class _XYDevice:
    def __init__(self) -> None:
        self.events: list[tuple] = []

    def set_burst_state(self, value, *, channel):
        self.events.append(("burst", channel, value))

    def set_mod_state(self, value, *, channel):
        self.events.append(("mod", channel, value))

    def setup_dc(self, value, *, channel):
        self.events.append(("dc", channel, value))

    def set_output(self, value, *, channel):
        self.events.append(("output", channel, value))


def test_initial_state_disables_xy_but_grid_zero_keeps_outputs_on() -> None:
    device = _XYDevice()
    devices = {"xy_field": device}
    channels = {"x_field": 1, "y_rf": 2}
    _set_xy_off_state(devices, channels)
    assert ("output", 1, False) in device.events
    assert ("output", 2, False) in device.events
    device.events.clear()
    _set_xy_scan_state(devices, channels, 0.0, 0.0)
    assert ("dc", 1, 0.0) in device.events
    assert ("dc", 2, 0.0) in device.events
    assert ("output", 1, True) in device.events
    assert ("output", 2, True) in device.events


class _ScopeRangeDevice:
    def __init__(self, scale: float = 0.5, offset: float = 0.0) -> None:
        self.scale = scale
        self.offset = offset
        self.events: list[tuple] = []

    def set_channel_scale(self, channel: int, value: float) -> None:
        self.events.append(("scale", channel, value))
        self.scale = float(value)

    def get_channel_scale(self, channel: int) -> float:
        return self.scale

    def set_channel_offset(self, channel: int, value: float) -> None:
        self.events.append(("offset", channel, value))
        self.offset = float(value)

    def get_channel_offset(self, channel: int) -> float:
        return self.offset


def _range_settings(*, auto_offset_enabled: bool = True) -> ScopeCaptureSettings:
    return ScopeCaptureSettings(
        sample_rate_sa_s=1000.0,
        duration_s=0.008,
        pd_channel=1,
        trigger_mode="AUTO",
        initial_scale_v_div=0.5,
        offset_v=0.0,
        vertical_divisions=8,
        scale_min_v_div=0.01,
        scale_max_v_div=10.0,
        auto_range_low_fraction=0.4,
        auto_range_high_fraction=0.9,
        auto_offset_tolerance_fraction=0.05,
        auto_range_max_attempts=3,
        welch_nperseg=8,
        maximum_frequency_hz=0.0,
        auto_offset_enabled=auto_offset_enabled,
    )


def _range_result(voltage: np.ndarray) -> SimpleNamespace:
    time_s = np.arange(voltage.size, dtype=float) / 1000.0
    return SimpleNamespace(time=time_s, voltage=voltage)


def test_fixed_zero_offset_does_not_move_offset_and_locked_scale_does_not_shrink() -> None:
    settings = _range_settings(auto_offset_enabled=False)
    scope = _ScopeRangeDevice()
    scope_config = SimpleNamespace(
        channels=[SimpleNamespace(number=1, scale=0.5, offset=0.0)]
    )
    first = acquire_autoranged_waveform(
        settings,
        scope_config,
        scope,
        ScopeAutoRangeState(0.5, 0.0, allow_shrink=True),
        capture=lambda: _range_result(np.linspace(-0.01, 0.01, 8)),
        check_cancelled=lambda: None,
    )
    assert first["attempt_count"] == 3
    assert not any(event[0] == "offset" for event in scope.events)

    scope.events.clear()
    locked = ScopeAutoRangeState(first["next_scale_v_div"], 0.0, allow_shrink=False)
    second = acquire_autoranged_waveform(
        settings,
        scope_config,
        scope,
        locked,
        capture=lambda: _range_result(np.linspace(-0.01, 0.01, 8)),
        check_cancelled=lambda: None,
    )
    assert second["attempt_count"] == 1
    assert locked.scale_v_div == pytest.approx(first["next_scale_v_div"])
    assert not scope.events


def test_locked_scale_still_expands_for_high_amplitude_waveform() -> None:
    settings = _range_settings(auto_offset_enabled=False)
    scope = _ScopeRangeDevice(scale=0.1)
    scope_config = SimpleNamespace(
        channels=[SimpleNamespace(number=1, scale=0.1, offset=0.0)]
    )
    state = ScopeAutoRangeState(0.1, 0.0, allow_shrink=False)
    payload = acquire_autoranged_waveform(
        settings,
        scope_config,
        scope,
        state,
        capture=lambda: _range_result(np.linspace(0.35, 0.39, 8)),
        check_cancelled=lambda: None,
    )
    assert payload["attempt_count"] == 2
    assert state.scale_v_div == pytest.approx(0.2)
    assert scope.events[0][0] == "scale"


def _payload(sequence: int) -> dict:
    time_s = np.arange(8, dtype=float) / 20.0
    voltage = np.full(time_s.size, float(sequence), dtype=float)
    return {
        "time_s": time_s,
        "voltage_v": voltage,
        "capture_started_unix_s": float(sequence),
        "capture_ended_unix_s": float(sequence) + 0.5,
        "capture_midpoint_unix_s": float(sequence) + 0.25,
        "pd_mean_v": float(sequence),
        "pd_std_v": 0.0,
        "actual_rate_sa_s": 20.0,
        "actual_duration_s": 0.35,
        "scale_used_v_div": 0.5,
        "offset_used_v": 0.0,
        "next_scale_v_div": 0.5,
        "next_offset_v": 0.0,
        "attempt_count": 1,
        "attempt_scales_v_div": np.asarray([0.5]),
        "attempt_offsets_v": np.asarray([0.0]),
        "attempt_abs_max_v": np.asarray([float(sequence)]),
        "attempt_min_v": np.asarray([float(sequence)]),
        "attempt_max_v": np.asarray([float(sequence)]),
        "attempt_center_v": np.asarray([float(sequence)]),
        "attempt_peak_deviation_v": np.asarray([0.0]),
        "attempt_display_edge_fraction": np.asarray([0.0]),
    }


def test_acquisition_contains_only_grid_points(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    import lab_workflows.experiment_modules.mx_xy_residual_field_calibration.workflow as workflow

    params = MxXYResidualFieldCalibrationParams(
        x_field_points=2,
        y_field_points=2,
        point_repeats=1,
    )
    raw = tmp_path / "raw"
    raw.mkdir()
    run_dir = SimpleNamespace(root=tmp_path, raw=raw)
    sequence = iter(range(4))
    allow_shrink_history: list[bool] = []
    monkeypatch.setattr(workflow, "check_cancelled", lambda: None)

    def fake_capture(*args):
        allow_shrink_history.append(args[-1].allow_shrink)
        return _payload(next(sequence))

    monkeypatch.setattr(workflow, "_capture_waveform", fake_capture)

    class GS:
        def __init__(self):
            self.currents: list[float] = []

        def set_current(self, value):
            self.currents.append(float(value))

        def set_output(self, value):
            pass

    gs200 = GS()
    auto_range = ScopeAutoRangeState(0.5)
    files = _acquire_all(
        params,
        run_dir,
        {"gs200": gs200, "xy_field": _XYDevice()},
        {"x_field": 1, "y_rf": 2},
        SimpleNamespace(),
        auto_range,
    )
    captures = []
    for relative in files[1:]:
        with np.load(tmp_path / relative, allow_pickle=False) as raw_file:
            captures.append(
                (
                    int(raw_file["sequence_index"]),
                    str(raw_file["phase"]),
                    int(raw_file["x_index"]),
                    int(raw_file["y_index"]),
                )
            )
    assert [item[1] for item in captures] == ["grid"] * 4
    assert [(item[2], item[3]) for item in captures] == [
        (0, 0),
        (0, 1),
        (1, 0),
        (1, 1),
    ]
    assert gs200.currents == [0.0]
    assert auto_range.allow_shrink is True
    assert allow_shrink_history == [True] * 4


def _write_summary_capture(
    path,
    *,
    sequence: int,
    time_s: float,
    mean_v: float,
    x_index: int = -1,
    y_index: int = -1,
    repeat_index: int = 0,
    x_v: float = 0.0,
    y_v: float = 0.0,
) -> None:
    np.savez(
        path,
        sequence_index=np.int64(sequence),
        repeat_index=np.int64(repeat_index),
        x_index=np.int64(x_index),
        y_index=np.int64(y_index),
        x_field_v=np.float64(x_v),
        y_field_v=np.float64(y_v),
        capture_midpoint_unix_s=np.float64(time_s),
        pd_mean_v=np.float64(mean_v),
        pd_std_v=np.float64(0.001),
    )


def test_dispersive_fit_recovers_center_amplitude_and_width() -> None:
    y_axis = np.linspace(-0.05, 0.05, 81)
    expected = {
        "offset_v": 0.012,
        "amplitude_v": -0.42,
        "center_y_v": 0.006,
        "gamma_v": 0.009,
    }
    signal = dispersive_line(y_axis, **expected)
    signal[5] += 0.12
    fit = fit_dispersive_line(y_axis, signal)
    assert fit.success is True
    assert fit.valid_for_selection is True
    assert fit.offset_v == pytest.approx(expected["offset_v"], abs=5.0e-4)
    assert fit.amplitude_v == pytest.approx(expected["amplitude_v"], rel=0.02)
    assert fit.center_y_v == pytest.approx(expected["center_y_v"], abs=2.0e-4)
    assert fit.gamma_v == pytest.approx(expected["gamma_v"], rel=0.03)
    assert fit.central_slope_v_per_v == pytest.approx(
        2.0 * abs(expected["amplitude_v"]) / expected["gamma_v"],
        rel=0.04,
    )


def test_bloch_2d_fit_recovers_balance_and_widths_with_outlier() -> None:
    x_axis = np.linspace(-0.05, 0.05, 21)
    y_axis = np.linspace(-0.05, 0.05, 41)
    x_grid, y_grid = np.meshgrid(x_axis, y_axis, indexing="ij")
    expected = {
        "offset_v": 0.004,
        "amplitude_v": -0.36,
        "balance_x_v": -0.013,
        "balance_y_v": 0.006,
        "width_x_v": 0.027,
        "width_y_v": 0.011,
    }
    signal = bloch_2d_surface(x_grid, y_grid, **expected)
    signal[2, 3] += 0.15
    fit = fit_bloch_2d(x_axis, y_axis, signal)
    assert fit.success is True
    assert fit.valid_for_selection is True
    assert fit.balance_x_v == pytest.approx(expected["balance_x_v"], abs=5.0e-4)
    assert fit.balance_y_v == pytest.approx(expected["balance_y_v"], abs=2.0e-4)
    assert fit.width_x_v == pytest.approx(expected["width_x_v"], rel=0.03)
    assert fit.width_y_v == pytest.approx(expected["width_y_v"], rel=0.03)
    assert fit.r_squared > 0.99


def test_analysis_uses_global_2d_bloch_fit_as_primary_result(tmp_path) -> None:
    run_dir = tmp_path / "run"
    raw_dir = run_dir / "raw"
    results_dir = run_dir / "results"
    raw_dir.mkdir(parents=True)
    results_dir.mkdir()
    params = MxXYResidualFieldCalibrationParams(
        x_field_start_v=-0.05,
        x_field_stop_v=0.05,
        x_field_points=11,
        y_field_start_v=-0.05,
        y_field_stop_v=0.05,
        y_field_points=41,
    )
    (run_dir / "experiment_config.yaml").write_text(
        yaml.safe_dump(
            {
                "experiment_id": "mx-xy-residual-field-calibration",
                "schema_version": 2,
                "parameters": params.to_external(),
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    x_axis, y_axis = build_xy_axes(params)
    np.savez(raw_dir / "xy_residual_scan_axes.npz", x_field_v=x_axis, y_field_v=y_axis)
    sequence = 0
    expected_x_balance_v = -0.013
    expected_y_balance_v = 0.006
    x_grid, y_grid = np.meshgrid(x_axis, y_axis, indexing="ij")
    expected_surface = bloch_2d_surface(
        x_grid,
        y_grid,
        offset_v=0.8,
        amplitude_v=-0.30,
        balance_x_v=expected_x_balance_v,
        balance_y_v=expected_y_balance_v,
        width_x_v=0.027,
        width_y_v=0.011,
    )
    expected_drift_v_per_s = 4.0e-4
    capture_times = 1000.0 + 1.7 * np.arange(params.grid_points, dtype=float)
    time_origin = float(np.mean(capture_times))
    for x_index, x_v in enumerate(x_axis):
        for y_index, y_v in enumerate(y_axis):
            point_time = float(capture_times[sequence])
            mean_v = float(
                expected_surface[x_index, y_index]
                + expected_drift_v_per_s * (point_time - time_origin)
            )
            _write_summary_capture(
                raw_dir / f"grid_X{x_index:03d}_Y{y_index:03d}_R000.npz",
                sequence=sequence,
                time_s=point_time,
                mean_v=mean_v,
                x_index=x_index,
                y_index=y_index,
                x_v=float(x_v),
                y_v=float(y_v),
            )
            sequence += 1

    result = analyze(run_dir)
    assert result["criterion"] == "global 2D steady-state Bloch fit of raw PD mean"
    assert result["reference_strategy"] == "none"
    assert result["bz_assumption"] == "approximately_zero"
    assert result["fitted_x_balance_v"] == pytest.approx(
        expected_x_balance_v, abs=2.0e-4
    )
    assert result["fitted_y_balance_v"] == pytest.approx(
        expected_y_balance_v, abs=2.0e-4
    )
    assert result["best_x_field_v"] == pytest.approx(-0.01)
    assert result["best_y_field_v"] == pytest.approx(0.005)
    assert result["bloch_width_x_v"] == pytest.approx(0.027, rel=0.02)
    assert result["bloch_width_y_v"] == pytest.approx(0.011, rel=0.02)
    assert result["bloch_linear_drift_v_per_s"] == pytest.approx(
        expected_drift_v_per_s, rel=0.01
    )
    assert result["bloch_r_squared"] > 0.999
    assert result["valid_dispersive_fit_count"] == params.x_field_points
    with np.load(results_dir / "calibration_results.npz", allow_pickle=False) as saved:
        assert float(saved["fitted_x_balance_v"]) == pytest.approx(
            expected_x_balance_v, abs=2.0e-4
        )
        assert float(saved["fitted_y_balance_v"]) == pytest.approx(
            expected_y_balance_v, abs=2.0e-4
        )
        assert saved["bloch_fit_surface_v"].shape == (11, 41)
        assert saved["fitted_linear_drift_v"].shape == (11, 41)
        assert float(saved["bloch_linear_drift_v_per_s"]) == pytest.approx(
            expected_drift_v_per_s, rel=0.01
        )
        assert saved["bloch_residual_v"].shape == (11, 41)
        assert int(np.count_nonzero(saved["dispersive_fit_valid"])) == params.x_field_points
    for filename in (
        "analysis.yaml",
        "analysis.json",
        "calibration_results.npz",
        "xy_residual_calibration.csv",
        "y_dispersive_fits.csv",
        "pd_mean_voltage_map.png",
        "pd_drift_corrected_map.png",
        "pd_mean_surface.png",
        "bloch_2d_fit.png",
        "bloch_2d_residual_map.png",
        "dispersive_fit_metrics.png",
        "best_y_dispersive_fit.png",
        "fitted_time_drift.png",
    ):
        assert (results_dir / filename).is_file()
