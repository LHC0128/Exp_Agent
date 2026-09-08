"""Mx Z 最优控制 XY 补偿偏置 RF 灵敏度实验测试。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import yaml

from lab_workflows.experiment_modules.mx_z_optimal_control_xy_rf_sensitivity import (
    ADAPTER,
    DEFINITION,
    analysis as xy_analysis,
)
from lab_workflows.experiment_modules.mx_z_optimal_control_xy_rf_sensitivity.analysis import (
    analyze,
)
from lab_workflows.experiment_modules.mx_z_optimal_control_xy_rf_sensitivity.models import (
    MxZOptimalControlXYRFSensitivityParams,
)
from lab_workflows.experiment_modules.mx_z_optimal_control_xy_rf_sensitivity.workflow import (
    _grid_points,
    _initial_manifest,
)
from lab_workflows.experiments import get_experiment


def test_definition_defaults_and_schema() -> None:
    assert get_experiment(DEFINITION.id) is DEFINITION
    assert DEFINITION.execution_mode == "typed_workflow"
    assert "X_magnetic_field" in DEFINITION.required_mapping_keys
    assert "rf_coil" in DEFINITION.required_mapping_keys
    params = ADAPTER.defaults()
    assert params.x_field_points == 11
    assert params.y_field_points == 11
    assert params.phase_calibration_point() == pytest.approx((0.0, 0.0))
    fields = {item["name"] for item in DEFINITION.schema()["fields"]}
    assert "X_FIELD_START_V" in fields
    assert "Y_FIELD_STOP_V" in fields
    assert "FIXED_PARAMS.X_magnetic_field" not in fields


def test_axis_validation_and_y_rf_envelope() -> None:
    params = MxZOptimalControlXYRFSensitivityParams(
        x_field_points=1,
        x_field_start_v=0.1,
        x_field_stop_v=0.2,
    )
    assert any("X 补偿偏置" in error for error in params.validate())
    params = MxZOptimalControlXYRFSensitivityParams(
        y_field_start_v=9.95,
        y_field_stop_v=9.95,
        y_field_points=1,
        y_rf_amp_start_vpp=-0.2,
        y_rf_amp_stop_vpp=0.2,
    )
    assert any("Y RF 输出包络" in error for error in params.validate())


def test_grid_is_x_outer_y_serpentine() -> None:
    params = MxZOptimalControlXYRFSensitivityParams(
        x_field_start_v=-1.0,
        x_field_stop_v=1.0,
        x_field_points=2,
        y_field_start_v=-2.0,
        y_field_stop_v=2.0,
        y_field_points=3,
    )
    points = _grid_points(params)
    assert [(item["x_index"], item["y_index"]) for item in points] == [
        (0, 0),
        (0, 1),
        (0, 2),
        (1, 2),
        (1, 1),
        (1, 0),
    ]
    manifest = _initial_manifest(params, points)
    assert manifest["acquisition_order"] == [item["key"] for item in points]


def test_xy_point_analysis_uses_mx_z_fit_convention(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def capture(
        raw_dir: Path,
        params: object,
        *,
        include_rejected_fit_diagnostics: bool,
        ignore_relative_gamma_uncertainty: bool,
        initial_center: float,
    ) -> dict[str, object]:
        captured.update(
            {
                "raw_dir": raw_dir,
                "params": params,
                "include_rejected_fit_diagnostics": (
                    include_rejected_fit_diagnostics
                ),
                "ignore_relative_gamma_uncertainty": (
                    ignore_relative_gamma_uncertainty
                ),
                "initial_center": initial_center,
            }
        )
        return {"ok": True}

    monkeypatch.setattr(xy_analysis, "evaluate_mx_y_rf_point", capture)
    params = MxZOptimalControlXYRFSensitivityParams()

    result = xy_analysis._evaluate_xy_point(
        Path("raw"),
        params,
        include_rejected_fit_diagnostics=True,
    )

    assert result == {"ok": True}
    assert captured["include_rejected_fit_diagnostics"] is True
    assert captured["ignore_relative_gamma_uncertainty"] is True
    assert captured["initial_center"] == pytest.approx(0.0)


def test_xy_response_restore_rearms_burst_with_point_offset_and_phase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lab_workflows.experiment_modules.mx_z_optimal_control_xy_rf_sensitivity import (
        workflow as xy_workflow,
    )

    class RecordingRF:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

        def _record(self, name: str, *args: object, **kwargs: object) -> None:
            self.calls.append((name, args, kwargs))

        def set_output(self, value: bool, *, channel: int) -> None:
            self._record("set_output", value, channel=channel)

        def set_burst_state(self, value: bool, *, channel: int) -> None:
            self._record("set_burst_state", value, channel=channel)

        def set_mod_state(self, value: bool, *, channel: int) -> None:
            self._record("set_mod_state", value, channel=channel)

        def setup_sine(
            self,
            frequency_hz: float,
            amplitude_vpp: float,
            *,
            offset: float,
            phase: float,
            channel: int,
        ) -> None:
            self._record(
                "setup_sine",
                frequency_hz,
                amplitude_vpp,
                offset=offset,
                phase=phase,
                channel=channel,
            )

        def set_burst_mode(self, value: str, *, channel: int) -> None:
            self._record("set_burst_mode", value, channel=channel)

        def set_burst_trigger_source(self, value: str, *, channel: int) -> None:
            self._record("set_burst_trigger_source", value, channel=channel)

        def set_burst_trigger_slope(self, value: str, *, channel: int) -> None:
            self._record("set_burst_trigger_slope", value, channel=channel)

        def set_burst_phase(self, value: float, *, channel: int) -> None:
            self._record("set_burst_phase", value, channel=channel)

    rf = RecordingRF()
    acquired: list[str] = []

    def gated_acquire(
        _params: object,
        _devices: dict[str, object],
        _channels: dict[str, int],
        *,
        set_y_rf: object,
        settle_time_s: float,
        acquire: object,
    ) -> float:
        assert settle_time_s == 0.0
        assert callable(set_y_rf)
        assert callable(acquire)
        set_y_rf()
        acquired.append("hf2")
        return float(acquire())

    monkeypatch.setattr(xy_workflow, "_temperature_gated_acquire", gated_acquire)
    monkeypatch.setattr(
        xy_workflow,
        "_configure_response_demodulator",
        lambda _params, _hf2: 899.5,
    )

    params = MxZOptimalControlXYRFSensitivityParams(
        y_rf_amp_start_vpp=-0.1,
        y_rf_amp_stop_vpp=0.1,
        y_rf_frequency_hz=12000.0,
        y_rf_offset_v=0.008,
    )
    result = xy_workflow._restore_xy_response_acquisition_state(
        params,
        {"xy_field": rf, "hf2": object()},
        {"y_rf": 2},
        selected_phase_deg=146.969,
    )

    assert result == pytest.approx(899.5)
    assert acquired == ["hf2"]
    assert ("set_burst_state", (True,), {"channel": 2}) in rf.calls
    assert ("set_burst_mode", ("INFinity",), {"channel": 2}) in rf.calls
    assert (
        "set_burst_trigger_source",
        ("EXTernal",),
        {"channel": 2},
    ) in rf.calls
    assert (
        "set_burst_trigger_slope",
        ("NEGative",),
        {"channel": 2},
    ) in rf.calls
    assert ("set_burst_phase", (pytest.approx(146.969),), {"channel": 2}) in rf.calls
    setup = next(call for call in rf.calls if call[0] == "setup_sine")
    assert setup[1][0] == pytest.approx(12000.0)
    assert setup[2]["offset"] == pytest.approx(0.008)


def _write_point(
    point_dir: Path,
    *,
    center: float,
    noise_scale: float = 1e-5,
    count: int = 2,
) -> None:
    point_dir.mkdir(parents=True, exist_ok=True)
    amplitude = np.linspace(-0.1, 0.1, 41)
    gamma = 0.02
    response = 0.3 * gamma * np.abs(amplitude - center) / (
        (amplitude - center) ** 2 + gamma**2
    ) + 0.003
    np.savez(
        point_dir / "amplitude_scan.npz",
        signed_amplitude_vpp=amplitude,
        r_mean_v=response,
        r_scalar_mean_v=response,
        r_std_v=np.full_like(response, 1e-4),
    )
    rng = np.random.default_rng(7)
    for index in range(count):
        np.savez(
            point_dir / f"noise_{index:03d}.npz",
            r_v=rng.normal(scale=noise_scale, size=8192),
            actual_rate_sa_s=np.float64(8192.0),
        )


def test_analysis_writes_xy_sensitivity_maps_and_best_point(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    raw = run_dir / "raw"
    raw.mkdir(parents=True)
    (run_dir / "results").mkdir()
    params = MxZOptimalControlXYRFSensitivityParams(
        x_field_start_v=-0.05,
        x_field_stop_v=0.05,
        x_field_points=2,
        y_field_start_v=-0.05,
        y_field_stop_v=0.05,
        y_field_points=2,
        noise_n_avg=2,
    )
    points = _grid_points(params)
    manifest = _initial_manifest(params, points)
    for item in points:
        manifest["points"][item["key"]]["status"] = "completed"
        _write_point(raw / "points" / item["key"], center=0.0)
    (raw / "point_manifest.yaml").write_text(
        yaml.safe_dump(manifest, allow_unicode=True), encoding="utf-8"
    )
    config = {
        "experiment_id": DEFINITION.id,
        "schema_version": params.schema_version,
        "parameters": params.to_external(),
        "phase_calibration_reference": {"x_field_v": 0.0, "y_field_v": 0.0},
        "selected_y_rf_phase_deg": 90.0,
    }
    (run_dir / "experiment_config.yaml").write_text(
        yaml.safe_dump(config, allow_unicode=True), encoding="utf-8"
    )
    try:
        result = analyze(run_dir)
    except RuntimeError:
        # 合成白噪声不一定通过真实平坦频段门槛；输出文件仍应完整生成。
        result = yaml.safe_load(
            (run_dir / "results" / "optimization.yaml").read_text(
                encoding="utf-8"
            )
        )
    assert result["experiment_id"] == DEFINITION.id
    assert result["total_point_count"] == 4
    for filename in (
        "optimization.npz",
        "optimization.yaml",
        "optimization.json",
        "analysis.yaml",
        "analysis.json",
        "sensitivity_heatmap.png",
        "slope_heatmap.png",
        "zero_point_sensitivity_heatmap.png",
        "zero_point_slope_heatmap.png",
        "hwhm_heatmap.png",
    ):
        assert (run_dir / "results" / filename).is_file(), filename
