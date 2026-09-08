"""Mx Z 最优控制 XY 平衡场 RF 相位响应实验测试。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import yaml

from lab_workflows.experiment_modules.mx_z_optimal_control_xy_rf_phase_response import (
    ADAPTER,
    DEFINITION,
)
from lab_workflows.experiment_modules.mx_z_optimal_control_xy_rf_phase_response.analysis import (
    _nonlinear_fit_grid,
    analyze,
)
from lab_workflows.experiment_modules.mx_z_optimal_control_xy_rf_phase_response.workflow import (
    _acquire_grid,
    safe_shutdown,
)
from lab_workflows.experiment_modules.mx_z_optimal_control_xy_rf_phase_response import (
    workflow as xy_workflow,
)
from lab_workflows.experiment_modules.mx_z_optimal_control_xy_rf_phase_response.models import (
    MxZOptimalControlXYRFPhaseResponseParams,
)
from lab_workflows.experiment_modules.mx_z_optimal_control_xy_rf_phase_response.scan import (
    build_axes,
    iter_serpentine_grid,
)
from lab_workflows.experiments.registry import get_experiment
from lab_workflows.experiment_modules.mx_z_optimal_control_rf_sensitivity.phase import (
    detect_r_phase_outliers,
    fit_phase_scan,
    inside_absolute_sine,
)
from lab_workflows.experiment_modules.mx_keithley_6221_optimal_control_rf_sensitivity.phase import (
    dispersion_phase_response,
)


def test_definition_defaults_and_phase_axis() -> None:
    assert get_experiment(DEFINITION.id) is DEFINITION
    assert DEFINITION.execution_mode == "typed_workflow"
    assert "Time_sequence_2" in DEFINITION.required_mapping_keys
    assert "rf_coil" in DEFINITION.required_mapping_keys
    params = ADAPTER.defaults()
    assert params.x_field_points == 11
    assert params.y_field_points == 11
    _, _, configured, acquired = build_axes(params)
    assert configured.size == 18
    assert acquired[:4].tolist() == [0.0, 180.0, 20.0, 200.0]
    assert ADAPTER.preflight({}) == []


def test_serpentine_grid() -> None:
    params = MxZOptimalControlXYRFPhaseResponseParams(
        x_field_points=2,
        y_field_points=3,
    )
    assert [(item[1], item[2]) for item in iter_serpentine_grid(params)] == [
        (0, 0), (0, 1), (0, 2), (1, 2), (1, 1), (1, 0)
    ]


def test_y_offset_envelope_is_validated() -> None:
    params = MxZOptimalControlXYRFPhaseResponseParams(
        y_field_start_v=10.0,
        y_field_stop_v=10.0,
        y_field_points=1,
    )
    assert any("Y RF 输出包络" in error for error in params.validate())


def test_analysis_writes_maps_and_masks_rejected_points(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    raw_dir = run_dir / "raw"
    raw_dir.mkdir(parents=True)
    (run_dir / "results").mkdir()
    params = MxZOptimalControlXYRFPhaseResponseParams(
        x_field_start_v=-0.1,
        x_field_stop_v=0.1,
        x_field_points=2,
        y_field_start_v=-0.1,
        y_field_stop_v=0.1,
        y_field_points=2,
    )
    _, _, configured, phase = build_axes(params)
    x_axis = np.array([-0.1, 0.1])
    y_axis = np.array([-0.1, 0.1])
    response = inside_absolute_sine(phase, 0.1, 0.2, 25.0)
    r_mean = np.tile(response, (2, 2, 1))
    accepted = np.array([[True, False], [True, True]])
    np.savez(
        raw_dir / "xy_rf_phase_response_scan.npz",
        x_field_v=x_axis,
        y_field_v=y_axis,
        configured_phase_deg=configured,
        acquisition_phase_deg=phase,
        r_mean_v=r_mean,
        r_std_v=np.full_like(r_mean, 1e-4),
        acquisition_order=np.array([[0, 1], [3, 2]]),
        fit_accepted=accepted,
        fit_rejection_reason=np.array([["", "flat"], ["", ""]]),
        baseline_v=np.full((2, 2), 0.1),
        amplitude_v=np.full((2, 2), 0.2),
        phase_zero_deg=np.full((2, 2), 25.0),
        selected_phase_deg=np.full((2, 2), 115.0),
        r_squared=np.full((2, 2), 0.99),
        diagnostic_fit_accepted=accepted,
        diagnostic_fit_rejection_reason=np.array([["", "flat"], ["", ""]]),
        diagnostic_model=np.full((2, 2), "C + A*abs(sin(phi-phi0))"),
        diagnostic_baseline_v=np.full((2, 2), 0.1),
        diagnostic_amplitude_v=np.full((2, 2), 0.2),
        diagnostic_phase_zero_deg=np.full((2, 2), 25.0),
        diagnostic_baseline_uncertainty_v=np.full((2, 2), 1e-4),
        diagnostic_amplitude_uncertainty_v=np.full((2, 2), 1e-4),
        diagnostic_phase_zero_uncertainty_deg=np.full((2, 2), 0.1),
        diagnostic_selected_phase_deg=np.full((2, 2), 115.0),
        diagnostic_observed_max_phase_deg=np.full((2, 2), 120.0),
        diagnostic_r_squared=np.full((2, 2), 0.99),
        actual_rate_sa_s=np.float64(1000.0),
    )
    (run_dir / "experiment_config.yaml").write_text(
        yaml.safe_dump({"completion_status": "completed"}, allow_unicode=True),
        encoding="utf-8",
    )
    result = analyze(run_dir)
    assert result["fit_rejected_count"] == 1
    assert len(result["invalid_points"]) == 1
    assert (run_dir / "results" / "xy_rf_phase_response_maps.png").is_file()
    assert (run_dir / "results" / "xy_rf_phase_response_curves.png").is_file()
    assert (run_dir / "results" / "analysis.json").is_file()


def test_phase_fit_is_reusable() -> None:
    phase = np.arange(0.0, 360.0, 20.0)
    primary, diagnostic = fit_phase_scan(
        phase,
        inside_absolute_sine(phase, 0.1, 0.2, 25.0),
        r_squared_min=0.1,
        amplitude_sigma_min=0.0,
    )
    assert primary.success
    assert diagnostic.model == "C + A*abs(sin(phi-phi0))"


def test_nonlinear_r_only_fit_does_not_require_xy_calibration() -> None:
    phase = np.arange(0.0, 360.0, 20.0)
    response = dispersion_phase_response(
        phase,
        0.02,
        0.008,
        0.001,
        0.004,
        37.0,
        0.01,
    )
    data = {
        "x_field_v": np.asarray([0.0]),
        "y_field_v": np.asarray([0.0]),
        "acquisition_phase_deg": phase,
        "r_mean_v": response.reshape(1, 1, -1),
        "r_std_v": np.full((1, 1, phase.size), 1e-5),
    }
    result = _nonlinear_fit_grid(
        data,
        {
            "parameters": {
                "PHASE_CAL_RF_AMPLITUDE_VPP": 0.01,
                "PHASE_FIT_R_SQUARED_MIN": 0.8,
                "PHASE_FIT_AMPLITUDE_SIGMA_MIN": 0.0,
            }
        },
    )
    assert result["input_signal"] == "Demod0 R"
    assert result["fit_accepted"].tolist() == [[True]]
    assert result["r_squared"][0, 0] > 0.8


def test_online_primary_fit_uses_nonlinear_dispersion_model() -> None:
    params = MxZOptimalControlXYRFPhaseResponseParams(
        phase_fit_amplitude_sigma_min=0.0,
    )
    phase = params.phase_axis_deg()
    response = dispersion_phase_response(
        phase,
        0.02,
        0.008,
        0.001,
        0.004,
        37.0,
        params.phase_cal_rf_amplitude_vpp,
    )
    payload, summary = xy_workflow._fit_payload(
        phase,
        response,
        params,
        r_std_v=np.full(phase.size, 1e-6),
    )
    assert payload["primary_fit"]["model"] == "abs(dispersion(B_eff(phi)))"
    assert payload["fit_accepted"]
    assert summary["nonlinear_r_squared"] > 0.8


def test_final_cross_phase_outlier_rejects_xy_point() -> None:
    phase = np.arange(0.0, 360.0, 20.0)
    params = MxZOptimalControlXYRFPhaseResponseParams(
        phase_fit_amplitude_sigma_min=0.0,
    )
    payload, summary = xy_workflow._fit_payload(
        phase,
        inside_absolute_sine(phase, 0.1, 0.2, 25.0),
        params,
        final_cross_phase_outliers=(140.0,),
    )
    assert not payload["fit_accepted"]
    assert not payload["primary_fit"]["success"]
    assert any(
        "140" in reason
        for reason in payload["primary_fit"]["rejection_reasons"]
    )
    assert not summary["fit_accepted"]


def test_r_phase_outlier_detector_finds_stable_trigger_failure() -> None:
    phase = np.arange(0.0, 360.0, 20.0)
    response = 0.6 + 0.2 * np.sin(np.deg2rad(phase - 25.0))
    response[phase == 140.0] = 0.05
    detection = detect_r_phase_outliers(
        phase,
        response,
        np.full(phase.size, 1e-3),
        sigma_threshold=6.0,
    )
    assert detection.outlier_phase_deg == (140.0,)
    assert detection.residual_v[7] > detection.threshold_v


def test_cross_phase_reacquisition_skips_when_outlier_limit_is_exceeded() -> None:
    params = MxZOptimalControlXYRFPhaseResponseParams(
        phase_outlier_max_reacquire_points=1,
    )
    phase = params.phase_axis_deg()
    response = 0.6 + 0.2 * np.sin(np.deg2rad(phase - 25.0))
    response[[2, 9, 14]] = (-4.0, 4.0, -4.0)
    summaries = [
        {"r_mean_v": float(value), "r_std_v": 1e-3}
        for value in response
    ]
    from lab_workflows.experiment_modules.mx_z_optimal_control_xy_rf_phase_response.workflow import (
        _reacquire_phase_outliers_once,
    )

    report, reacquired, _, final_residual, _, _ = _reacquire_phase_outliers_once(
        params,
        SimpleNamespace(raw=Path(".")),
        {"xy_field": object(), "hf2": object()},
        {"y_rf": 2},
        x_index=0,
        y_index=0,
        acquisition_index=0,
        x_v=0.0,
        y_v=0.0,
        phase_axis=phase,
        summaries=summaries,
        attempts=[0] * phase.size,
        files=[f"phase_{index:03d}.npz" for index in range(phase.size)],
        actual_rate=1000.0,
        device_id="dev-test",
    )
    assert report["too_many_initial_outliers"]
    assert report["reacquired_phase_deg"] == []
    assert not np.any(reacquired)
    assert np.count_nonzero(final_residual > report["final_detection"]["threshold_v"]) >= 2


def test_grid_reacquires_r_phase_outlier_and_replaces_file(
    tmp_path: Path,
    monkeypatch,
) -> None:
    params = MxZOptimalControlXYRFPhaseResponseParams(
        phase_outlier_sigma_threshold=6.0,
        phase_outlier_max_reacquire_points=2,
    )
    phase = params.phase_axis_deg()
    class FakeXY:
        def set_burst_state(self, state: bool, *, channel: int) -> None:
            pass

        def set_mod_state(self, state: bool, *, channel: int) -> None:
            pass

        def setup_dc(self, value: float, *, channel: int) -> None:
            pass

        def set_output(self, state: bool, *, channel: int) -> None:
            pass

    xy = FakeXY()
    calls: list[str] = []
    bad_phase = 140.0

    def fake_rearm(*args, **kwargs):
        calls.append("rearm")

    monkeypatch.setattr(
        "lab_workflows.experiment_modules.mx_z_optimal_control_xy_rf_phase_response.workflow._rearm_y_rf_at_offset",
        fake_rearm,
    )

    def fake_acquire(*args, file_stem: str, metadata, **kwargs):
        is_retry = "cross_retry" in file_stem
        scanned = float(metadata["scanned_phase_deg"])
        kwargs["set_y_rf"]()
        values = 0.6 + 0.2 * np.sin(np.deg2rad(scanned - 25.0))
        if scanned == bad_phase and not is_retry:
            values = 0.05
        return (
            {"r_mean_v": values, "r_std_v": 1e-3},
            1 if is_retry else 0,
            f"{file_stem}_attempt_{1 if is_retry else 0:02d}.npz",
        )

    monkeypatch.setattr(
        "lab_workflows.experiment_modules.mx_z_optimal_control_xy_rf_phase_response.workflow._acquire_valid_r_point",
        fake_acquire,
    )
    from lab_workflows.experiment_modules.mx_z_optimal_control_xy_rf_phase_response.workflow import (
        _acquire_phase_curve,
    )

    record = _acquire_phase_curve(
        params,
        SimpleNamespace(raw=tmp_path),
        {"xy_field": xy, "hf2": object()},
        {"x_field": 1, "y_rf": 2},
        x_index=0,
        y_index=0,
        acquisition_index=0,
        x_v=0.0,
        y_v=0.0,
        phase_axis=phase,
        actual_rate=1000.0,
        device_id="dev-test",
    )
    report = record["fit_payload"]["cross_phase_reacquisition"]
    assert report["reacquired_phase_deg"] == [bad_phase]
    assert report["original_files"][0].endswith("phase_007_attempt_00.npz")
    assert report["replacement_files"][0].endswith("cross_retry_attempt_01.npz")
    assert record["accepted_attempt_index"][7] == 1
    assert record["initial_accepted_attempt_index"][7] == 0
    assert record["cross_phase_reacquired"][7]
    assert len(calls) == 19


def test_phase_axis_rejects_duplicate_wrapped_endpoint() -> None:
    params = MxZOptimalControlXYRFPhaseResponseParams(
        phase_scan_start_deg=0.0,
        phase_scan_stop_deg=360.0,
        phase_scan_step_deg=20.0,
    )
    assert any("不能重复" in error for error in params.validate())


def test_invalid_phase_step_reports_validation_error() -> None:
    params = MxZOptimalControlXYRFPhaseResponseParams(phase_scan_step_deg=0.0)
    assert any("步进必须大于 0" in error for error in params.validate())


def test_grid_workflow_keeps_canonical_shape_and_continues_after_bad_fit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class FakeDG:
        def __init__(self) -> None:
            self.calls: list[tuple] = []

        def _call(self, name: str, *args, **kwargs) -> None:
            self.calls.append((name, args, kwargs))

        def set_output(self, state: bool, *, channel: int) -> None:
            self._call("output", state, channel=channel)

        def set_burst_state(self, state: bool, *, channel: int) -> None:
            self._call("burst", state, channel=channel)

        def set_mod_state(self, state: bool, *, channel: int) -> None:
            self._call("mod", state, channel=channel)

        def setup_dc(self, value: float, *, channel: int) -> None:
            self._call("dc", value, channel=channel)

        def set_burst_phase(self, value: float, *, channel: int) -> None:
            self._call("phase", value, channel=channel)

        def set_amplitude(self, value: float, *, channel: int) -> None:
            self._call("amplitude", value, channel=channel)

        def set_offset(self, value: float, *, channel: int) -> None:
            self._call("offset", value, channel=channel)

    params = MxZOptimalControlXYRFPhaseResponseParams(
        x_field_points=2,
        y_field_points=2,
        phase_fit_amplitude_sigma_min=0.0,
    )
    phase = params.phase_axis_deg()
    xy = FakeDG()

    def fake_acquire(*args, file_stem: str, metadata, **kwargs):
        # 真实采集器会在温控门控前调用该回调，测试中也要保留每相位点重新装载。
        kwargs["set_y_rf"]()
        current = float(metadata["scanned_phase_deg"])
        response = inside_absolute_sine(phase, 0.1, 0.2, 25.0)
        value = float(response[np.argmin(np.abs(phase - current))])
        return (
            {"r_mean_v": value, "r_std_v": 1e-4, "r_scalar_mean_v": value},
            0,
            f"{file_stem}_attempt_00.npz",
        )

    monkeypatch.setattr(
        "lab_workflows.experiment_modules.mx_z_optimal_control_xy_rf_phase_response.workflow._acquire_valid_r_point",
        fake_acquire,
    )
    real_fit_payload = xy_workflow._fit_payload
    fit_calls = 0

    def fake_fit_payload(*args, **kwargs):
        nonlocal fit_calls
        fit_calls += 1
        payload, summary = real_fit_payload(*args, **kwargs)
        if fit_calls == 2:
            payload["primary_fit"]["success"] = False
            payload["primary_fit"]["rejection_reasons"] = ["synthetic rejection"]
            summary["fit_accepted"] = False
        return payload, summary

    monkeypatch.setattr(xy_workflow, "_fit_payload", fake_fit_payload)
    run_dir = SimpleNamespace(raw=tmp_path)
    records = _acquire_grid(
        params,
        run_dir,
        {"xy_field": xy},
        {"x_field": 1, "y_rf": 2},
        1000.0,
        "dev-test",
    )
    assert len(records) == 4
    with np.load(tmp_path / "xy_rf_phase_response_scan.npz") as data:
        assert data["r_mean_v"].shape == (2, 2, 18)
        assert data["acquisition_order"].tolist() == [[0, 1], [3, 2]]
        assert data["fit_accepted"].shape == (2, 2)
        assert int(np.count_nonzero(~data["fit_accepted"])) == 1
        assert data["fit_rejection_reason"][0, 1] == "synthetic rejection"
        assert data["diagnostic_amplitude_v"].shape == (2, 2)
        assert data["diagnostic_fit_accepted"].shape == (2, 2)
    assert sum(call[0] == "phase" for call in xy.calls) == 4 * 18


def test_safe_shutdown_preserves_z_waveform_and_zeros_xy_outputs() -> None:
    class ShutdownDG:
        def __init__(self) -> None:
            self.calls: list[tuple] = []

        def set_burst_state(self, state: bool, *, channel: int) -> None:
            self.calls.append(("burst", (state,), {"channel": channel}))

        def set_mod_state(self, state: bool, *, channel: int) -> None:
            self.calls.append(("mod", (state,), {"channel": channel}))

        def setup_dc(self, value: float, *, channel: int) -> None:
            self.calls.append(("dc", (value,), {"channel": channel}))

        def set_output(self, state: bool, *, channel: int) -> None:
            self.calls.append(("output", (state,), {"channel": channel}))

    z = ShutdownDG()
    xy = ShutdownDG()
    report = safe_shutdown(
        {"z_field": z, "xy_field": xy},
        {"trigger": 2, "x_field": 1, "y_rf": 2},
        MxZOptimalControlXYRFPhaseResponseParams(),
    )
    assert report.completed
    assert "Z_magnetic_field" in report.preserved_outputs
    assert ("dc", (0.0,), {"channel": 2}) in z.calls
    assert ("output", (False,), {"channel": 2}) in z.calls
    for channel in (1, 2):
        assert ("dc", (0.0,), {"channel": channel}) in xy.calls
        assert ("output", (False,), {"channel": channel}) in xy.calls
