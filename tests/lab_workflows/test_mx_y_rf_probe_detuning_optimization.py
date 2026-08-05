"""Mx Y RF Probe 光功率与 PZT 失谐优化实验测试。"""

from __future__ import annotations

from pathlib import Path
import shutil
from types import SimpleNamespace
from uuid import uuid4

import numpy as np
import pytest
import yaml

from lab_workflows.common import WorkflowCancelled
from lab_workflows.experiment_modules.mx_y_rf_probe_detuning_optimization import (
    DEFINITION,
    MxYRFProbeDetuningOptimizationParams,
)
from lab_workflows.experiment_modules.mx_y_rf_probe_detuning_optimization.analysis import (
    analyze,
)
from lab_workflows.experiment_modules.mx_y_rf_probe_detuning_optimization.scan import (
    build_probe_detuning_axes,
    iter_probe_detuning_grid,
)
from lab_workflows.experiment_modules.mx_y_rf_probe_detuning_optimization.workflow import (
    ProbeLaserState,
)
from lab_workflows.experiment_modules.mx_y_rf_sensitivity.workflow import (
    RPointQualityError,
)
from lab_workflows.experiments import get_experiment


@pytest.fixture
def local_tmp_path() -> Path:
    root = Path(__file__).resolve().parents[2] / "data"
    path = root / f".test_mx_y_rf_probe_detuning_{uuid4().hex}"
    path.mkdir(mode=0o777)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def test_defaults_schema_registry_and_grid() -> None:
    params = MxYRFProbeDetuningOptimizationParams()
    assert params.validate() == []
    assert params.linewidth_mode == "amplitude_equivalent"
    assert params.pump_laser_power_v == 0.5
    assert params.probe_laser_power_v == 0.3
    assert params.y_rf_frequency_hz == 90000.0
    assert params.y_rf_amp_points == 21
    assert params.noise_n_avg == 5
    definition = get_experiment(
        "mx-y-rf-probe-detuning-optimization"
    )
    assert definition is DEFINITION
    assert definition.execution_mode == "typed_workflow"
    assert "TEC103" not in definition.required_devices
    assert definition.required_devices[-1] == "DLC_PRO"

    fields = {item["name"]: item for item in definition.schema()["fields"]}
    assert fields["PZT_VOLTAGE_START_V"]["default"] == 60.0
    assert fields["PZT_VOLTAGE_STOP_V"]["default"] == 100.0
    assert fields["PZT_VOLTAGE_POINTS"]["default"] == 9
    assert fields["PZT_SETTLE_TIME_S"]["default"] == 1.0
    assert fields["PROBE_POWER_START_V"]["default"] == 0.1
    assert fields["PROBE_POWER_STOP_V"]["default"] == 0.5
    assert fields["PROBE_POWER_POINTS"]["default"] == 7
    assert fields["PROBE_POWER_POINTS"]["minimum"] == 1
    assert "FIXED_PARAMS.temperature" not in fields
    assert "TEMPERATURE_TOLERANCE_C" not in fields
    assert "TEMPERATURE_STABLE_READS" not in fields
    assert "TEMPERATURE_POLL_INTERVAL_S" not in fields
    assert "TEMPERATURE_TIMEOUT_S" not in fields
    assert "TEMP_SWITCH_OFF_LEAD_S" in fields
    assert "TEMP_SWITCH_ON_LAG_S" in fields
    assert "LINEWIDTH_MODE" not in fields
    assert "FREQUENCY_START_HZ" not in fields

    pzt_axis, probe_axis = build_probe_detuning_axes(params)
    np.testing.assert_allclose(pzt_axis, np.linspace(60.0, 100.0, 9))
    np.testing.assert_allclose(probe_axis, np.linspace(0.1, 0.5, 7))
    points = iter_probe_detuning_grid(params)
    assert len(points) == 63
    assert [item.probe_index for item in points[:7]] == list(range(7))
    assert [item.probe_index for item in points[7:14]] == list(
        reversed(range(7))
    )
    assert points[0].key == "pzt_000_probe_000"
    assert points[-1].key == "pzt_008_probe_006"


def test_fixed_probe_single_point_pzt_scan() -> None:
    params = MxYRFProbeDetuningOptimizationParams(
        probe_power_start_v=0.3,
        probe_power_stop_v=0.3,
        probe_power_points=1,
        pzt_voltage_start_v=10.0,
        pzt_voltage_stop_v=30.0,
        pzt_voltage_points=5,
    )
    assert params.validate() == []
    pzt_axis, probe_axis = build_probe_detuning_axes(params)
    np.testing.assert_allclose(pzt_axis, np.linspace(10.0, 30.0, 5))
    np.testing.assert_allclose(probe_axis, [0.3])
    points = iter_probe_detuning_grid(params)
    assert len(points) == 5
    assert [point.probe_index for point in points] == [0] * 5
    assert [point.probe_power_v for point in points] == [0.3] * 5
    assert points[0].key == "pzt_000_probe_000"
    assert points[-1].key == "pzt_004_probe_000"


@pytest.mark.parametrize(
    ("params", "message"),
    [
        (
            MxYRFProbeDetuningOptimizationParams(
                pzt_voltage_start_v=-1.0
            ),
            "PZT_VOLTAGE_START_V",
        ),
        (
            MxYRFProbeDetuningOptimizationParams(
                pzt_voltage_stop_v=141.0
            ),
            "PZT_VOLTAGE_STOP_V",
        ),
        (
            MxYRFProbeDetuningOptimizationParams(
                probe_power_stop_v=1.1
            ),
            "PROBE_POWER_STOP_V",
        ),
        (
            MxYRFProbeDetuningOptimizationParams(
                pzt_voltage_start_v=100.0,
                pzt_voltage_stop_v=60.0,
            ),
            "PZT 电压扫描起点",
        ),
        (
            MxYRFProbeDetuningOptimizationParams(
                probe_power_start_v=0.2,
                probe_power_stop_v=0.3,
                probe_power_points=1,
            ),
            "扫描点数为 1",
        ),
        (
            MxYRFProbeDetuningOptimizationParams(
                probe_power_start_v=0.3,
                probe_power_stop_v=0.3,
                probe_power_points=2,
            ),
            "扫描点数至少为 2",
        ),
        (
            MxYRFProbeDetuningOptimizationParams(
                probe_power_points=0,
            ),
            "PROBE_POWER_POINTS",
        ),
    ],
)
def test_preflight_rejects_invalid_scan_params(
    params: MxYRFProbeDetuningOptimizationParams,
    message: str,
) -> None:
    assert any(message in error for error in params.validate())


class _FakePowerGenerator:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def setup_dc(self, value: float, *, channel: int) -> None:
        self.calls.append(("dc", channel, value))

    def set_output(self, value: bool, *, channel: int) -> None:
        self.calls.append(("output", channel, value))


class _FakeProbeLaserController:
    def __init__(self) -> None:
        self.pzt = 80.0
        self.actual = 79.8
        self.amplitude = 34.0
        self.scan_enabled = True
        self.calls: list[tuple] = []

    def get_controller_serial(self) -> str:
        return "DLC PRO_53043"

    def get_laser_head_serial(self) -> str:
        return "24105"

    def get_system_health_code(self) -> int:
        return 0

    def get_system_health(self) -> str:
        return "OK"

    def get_laser_health_code(self) -> int:
        return 0

    def get_laser_health(self) -> str:
        return "OK"

    def get_interlock_open(self) -> bool:
        return False

    def get_laser_enabled(self) -> bool:
        return True

    def get_emission(self) -> bool:
        return True

    def get_laser_emission(self) -> bool:
        return True

    def get_pzt_voltage_v(self) -> float:
        return self.pzt

    def get_pzt_voltage_actual_v(self) -> float:
        return self.actual

    def get_scan_amplitude_vpp(self) -> float:
        return self.amplitude

    def get_scan_enabled(self) -> bool:
        return self.scan_enabled

    def set_scan_enabled(self, value: bool) -> bool:
        self.calls.append(("scan_enabled", value))
        self.scan_enabled = value
        return value

    def set_scan_amplitude_vpp(self, value: float) -> float:
        self.calls.append(("scan_amplitude", value))
        self.amplitude = value
        return value

    def set_pzt_voltage_v(self, value: float) -> float:
        self.calls.append(("pzt", value))
        self.pzt = value
        self.actual = value - 0.2
        return value


def test_probe_laser_prepare_and_restore_order() -> None:
    import lab_workflows.experiment_modules.mx_y_rf_probe_detuning_optimization.workflow as workflow

    controller = _FakeProbeLaserController()
    state, snapshot = workflow._snapshot_and_validate_probe_laser(
        controller,
        {
            "controller_serial": "DLC PRO_53043",
            "laser_head_serial": "24105",
        },
    )
    assert state == ProbeLaserState(80.0, 79.8, 34.0, True)
    assert snapshot["emission"] is True
    workflow._prepare_probe_laser_scan(controller)
    controller.set_pzt_voltage_v(95.0)
    report = workflow._restore_probe_laser_state(controller, state)
    assert report["success"]
    assert controller.calls == [
        ("scan_enabled", False),
        ("scan_amplitude", 0.0),
        ("pzt", 95.0),
        ("pzt", 80.0),
        ("scan_amplitude", 34.0),
        ("scan_enabled", True),
    ]


def test_probe_laser_preflight_rejects_emission_off() -> None:
    import lab_workflows.experiment_modules.mx_y_rf_probe_detuning_optimization.workflow as workflow

    controller = _FakeProbeLaserController()
    controller.get_emission = lambda: False
    with pytest.raises(RuntimeError, match="Emission 未开启"):
        workflow._snapshot_and_validate_probe_laser(
            controller,
            {
                "controller_serial": "DLC PRO_53043",
                "laser_head_serial": "24105",
            },
        )
    assert controller.calls == []


@pytest.mark.parametrize(
    (
        "probe_power_start_v",
        "probe_power_stop_v",
        "probe_power_points",
        "expected_point_count",
    ),
    [
        (0.1, 0.5, 2, 4),
        (0.3, 0.3, 1, 2),
    ],
)
def test_grid_sets_pzt_once_per_row_and_checkpoints_readback(
    local_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    probe_power_start_v: float,
    probe_power_stop_v: float,
    probe_power_points: int,
    expected_point_count: int,
) -> None:
    import lab_workflows.experiment_modules.mx_y_rf_probe_detuning_optimization.workflow as workflow

    params = MxYRFProbeDetuningOptimizationParams(
        pzt_voltage_points=2,
        probe_power_start_v=probe_power_start_v,
        probe_power_stop_v=probe_power_stop_v,
        probe_power_points=probe_power_points,
        pzt_settle_time_s=1.0,
    )
    controller = _FakeProbeLaserController()
    power = _FakePowerGenerator()
    waits: list[float] = []
    monkeypatch.setattr(workflow, "check_cancelled", lambda: None)
    monkeypatch.setattr(
        workflow,
        "_sleep_cancellable",
        lambda seconds: waits.append(seconds),
    )
    monkeypatch.setattr(
        workflow,
        "acquire_mx_y_rf_point",
        lambda *args, **kwargs: {
            "actual_response_rate_sa_s": 1000.0,
            "actual_noise_rate_sa_s": 50000.0,
        },
    )
    summary = workflow._acquire_grid(
        params,
        SimpleNamespace(raw=local_tmp_path / "raw"),
        {
            "laser": power,
            "probe_laser_controller": controller,
        },
        {"probe_laser": 2},
        1000.0,
        "dev",
    )
    assert summary["completed_point_count"] == expected_point_count
    assert waits == [1.0, 1.0]
    assert [call for call in controller.calls if call[0] == "pzt"] == [
        ("pzt", 60.0),
        ("pzt", 100.0),
    ]
    manifest = yaml.safe_load(
        (
            local_tmp_path / "raw" / "point_manifest.yaml"
        ).read_text(encoding="utf-8")
    )
    assert manifest["scan_order"] == "pzt_outer_probe_inner_serpentine"
    assert len(manifest["pzt_rows"]) == 2
    assert len(manifest["points"]) == expected_point_count
    assert all(
        record["status"] == "completed"
        and "pzt_voltage_readback_v" in record
        and "pzt_actual_v" in record
        for record in manifest["points"].values()
    )


def test_quality_failure_continues_but_hardware_error_aborts(
    local_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import lab_workflows.experiment_modules.mx_y_rf_probe_detuning_optimization.workflow as workflow

    params = MxYRFProbeDetuningOptimizationParams(
        pzt_voltage_points=2,
        probe_power_points=2,
        pzt_settle_time_s=0.0,
    )
    devices = {
        "laser": _FakePowerGenerator(),
        "probe_laser_controller": _FakeProbeLaserController(),
    }
    monkeypatch.setattr(workflow, "check_cancelled", lambda: None)
    outcomes = iter(
        [
            RPointQualityError("连续坏点"),
            {
                "actual_response_rate_sa_s": 1000.0,
                "actual_noise_rate_sa_s": 50000.0,
            },
            RuntimeError("PZT 后设备通信失败"),
        ]
    )

    def fake_acquire(*args, **kwargs):
        result = next(outcomes)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(workflow, "acquire_mx_y_rf_point", fake_acquire)
    with pytest.raises(RuntimeError, match="设备通信失败"):
        workflow._acquire_grid(
            params,
            SimpleNamespace(raw=local_tmp_path / "raw"),
            devices,
            {"probe_laser": 2},
            1000.0,
            "dev",
        )
    manifest = yaml.safe_load(
        (
            local_tmp_path / "raw" / "point_manifest.yaml"
        ).read_text(encoding="utf-8")
    )
    assert manifest["points"]["pzt_000_probe_000"]["status"] == (
        "invalid_quality"
    )
    assert manifest["points"]["pzt_000_probe_001"]["status"] == "completed"
    assert manifest["points"]["pzt_001_probe_001"]["status"] == "running"


def test_cancelled_grid_keeps_completed_point_checkpoint(
    local_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import lab_workflows.experiment_modules.mx_y_rf_probe_detuning_optimization.workflow as workflow

    params = MxYRFProbeDetuningOptimizationParams(
        pzt_voltage_points=2,
        probe_power_points=2,
        pzt_settle_time_s=0.0,
    )
    outcomes = iter(
        [
            {
                "actual_response_rate_sa_s": 1000.0,
                "actual_noise_rate_sa_s": 50000.0,
            },
            WorkflowCancelled("取消"),
        ]
    )
    monkeypatch.setattr(workflow, "check_cancelled", lambda: None)

    def fake_acquire(*args, **kwargs):
        result = next(outcomes)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(workflow, "acquire_mx_y_rf_point", fake_acquire)
    with pytest.raises(WorkflowCancelled):
        workflow._acquire_grid(
            params,
            SimpleNamespace(raw=local_tmp_path / "raw"),
            {
                "laser": _FakePowerGenerator(),
                "probe_laser_controller": _FakeProbeLaserController(),
            },
            {"probe_laser": 2},
            1000.0,
            "dev",
        )
    manifest = yaml.safe_load(
        (
            local_tmp_path / "raw" / "point_manifest.yaml"
        ).read_text(encoding="utf-8")
    )
    assert manifest["points"]["pzt_000_probe_000"]["status"] == "completed"
    assert manifest["points"]["pzt_000_probe_001"]["status"] == "running"


class _FakeRunDir:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.raw = root / "raw"
        self.results = root / "results"
        self.raw.mkdir(parents=True)
        self.results.mkdir()
        self.config_path = root / "experiment_config.yaml"
        self.config_path.write_text("{}\n", encoding="utf-8")
        self.updates: list[dict] = []

    def update_config(self, **kwargs) -> None:
        self.updates.append(kwargs)


@pytest.mark.parametrize("outcome", ["success", "error", "cancel"])
def test_run_restores_probe_laser_on_all_exit_paths(
    local_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    import lab_workflows.experiment_modules.mx_y_rf_probe_detuning_optimization.workflow as workflow

    run_dir = _FakeRunDir(local_tmp_path / outcome)
    controller = _FakeProbeLaserController()
    restored_states: list[ProbeLaserState | None] = []
    power_restores: list[dict] = []
    connection_options: list[dict] = []
    configuration_options: list[dict] = []
    monkeypatch.setattr(workflow, "find_project_root", lambda: local_tmp_path)
    monkeypatch.setattr(
        workflow,
        "load_mapping",
        lambda root: {
            "lockin_r": {"device_id": "dev"},
            "probe_laser": {},
        },
    )
    monkeypatch.setattr(
        workflow,
        "create_run_directory",
        lambda *args, **kwargs: run_dir,
    )
    monkeypatch.setattr(workflow, "check_cancelled", lambda: None)
    monkeypatch.setattr(
        workflow,
        "connect_mx_y_rf_devices",
        lambda *args, **kwargs: (
            connection_options.append(kwargs)
            or (
                {"laser": object()},
                {"pump_laser": 1, "probe_laser": 2},
            )
        ),
    )
    monkeypatch.setattr(
        workflow,
        "_connect_probe_laser_controller",
        lambda *args: (controller, {}),
    )
    monkeypatch.setattr(
        workflow,
        "_snapshot_and_validate_probe_laser",
        lambda *args: (
            ProbeLaserState(80.0, 79.8, 34.0, True),
            {},
        ),
    )
    monkeypatch.setattr(workflow, "snapshot_mx_y_rf_state", lambda *args: {})
    monkeypatch.setattr(
        workflow,
        "configure_mx_y_rf_outputs",
        lambda *args, **kwargs: (
            configuration_options.append(kwargs) or (1000.0, {})
        ),
    )
    monkeypatch.setattr(
        workflow,
        "_prepare_probe_laser_scan",
        lambda *args: None,
    )

    def acquire(*args):
        if outcome == "error":
            raise RuntimeError("synthetic failure")
        if outcome == "cancel":
            raise WorkflowCancelled("synthetic cancellation")
        return {"completed_point_count": 1}

    monkeypatch.setattr(workflow, "_acquire_grid", acquire)
    monkeypatch.setattr(
        workflow,
        "safe_shutdown",
        lambda *args: SimpleNamespace(
            errors=[],
            disconnect_errors=[],
            to_dict=lambda: {},
        ),
    )
    monkeypatch.setattr(
        workflow,
        "_restore_probe_laser_state",
        lambda instrument, state: (
            restored_states.append(state)
            or {"attempted": True, "success": True, "error": None}
        ),
    )
    monkeypatch.setattr(
        workflow,
        "restore_mx_y_rf_laser_powers",
        lambda *args, **kwargs: power_restores.append(kwargs),
    )
    params = MxYRFProbeDetuningOptimizationParams()
    if outcome == "success":
        assert workflow.run(params) == run_dir.root
    else:
        expected = RuntimeError if outcome == "error" else WorkflowCancelled
        with pytest.raises(expected):
            workflow.run(params)
    assert restored_states == [
        ProbeLaserState(80.0, 79.8, 34.0, True)
    ]
    assert power_restores == [
        {"pump_power_v": 0.5, "probe_power_v": 0.3}
    ]
    assert connection_options == [{"connect_tec": False}]
    assert configuration_options == [{"control_tec": False}]
    assert any(
        update.get("temperature_control")
        == {
            "tec_connected": False,
            "tec_target_temperature_configured": False,
            "temperature_stability_waited": False,
            "temperature_switch_controlled": True,
            "temperature_switch_restored_on_exit": True,
        }
        for update in run_dir.updates
    )
    assert run_dir.updates[-1]["device_disconnect"] == {
        "tec_connected": False,
        "tec_disconnect_attempted": False,
        "other_devices_preserved": True,
        "errors": [],
    }


def _synthetic_evaluation(value: float | None) -> dict:
    valid = value is not None
    zero_value = float(value) * 1.5 if valid else None
    return {
        "valid": valid,
        "invalid_reasons": [] if valid else ["自动平坦段识别失败"],
        "warnings": [],
        "response_fit": SimpleNamespace(
            success=True,
            parameters=(0.01, 0.05, 0.0, 0.0),
            center=0.0,
            r_squared=0.99,
        ),
        "response_result": {"success": True},
        "primary_slope": 10.0,
        "zero_point_linear": {
            "method": "adaptive_zero_point_absolute_linear",
            "success": True,
            "slope": 8.0,
            "intercept": 0.0,
            "r_squared": 0.95,
            "n_points": 5,
            "minimum_points": 5,
            "minimum_points_per_side": 2,
            "left_point_count": 2,
            "right_point_count": 3,
            "window_vpp": [-0.1, 0.1],
            "max_abs_distance_vpp": 0.1,
            "rejection_reasons": [],
            "mask": np.asarray([True, True, True]),
        },
        "zero_point_slope": 8.0,
        "zero_point_valid": valid,
        "zero_point_invalid_reasons": [] if valid else ["无平坦段"],
        "hwhm_hz": 100.0,
        "sensitivity": {
            "flat_detection_success": valid,
            "flat_detection_reason": "ok" if valid else "no flat band",
            "flat_band_hz": np.asarray([10.0, 100.0]),
            "corrected_ft_per_sqrt_hz": np.asarray([100.0, 100.0]),
            "flat_median_ft_per_sqrt_hz": (
                float(value) if valid else np.nan
            ),
        },
        "zero_point_sensitivity": {
            "flat_detection_success": valid,
            "flat_detection_reason": "ok" if valid else "no flat band",
            "flat_band_hz": np.asarray([10.0, 100.0]),
            "corrected_ft_per_sqrt_hz": np.asarray([150.0, 150.0]),
            "flat_median_ft_per_sqrt_hz": (
                zero_value if valid else np.nan
            ),
        },
        "amplitude_vpp": np.asarray([-0.1, 0.0, 0.1]),
        "r_mean_v": np.asarray([0.1, 0.0, 0.1]),
        "fit_mask": np.asarray([True, True, True]),
        "bad_point_mask": np.asarray([False, False, False]),
        "frequency_hz": np.asarray([1.0, 10.0]),
    }


def _write_analysis_run(
    root: Path,
    *,
    probe_values: tuple[float, ...] = (0.1, 0.5),
) -> Path:
    raw = root / "raw"
    raw.mkdir(parents=True)
    (root / "results").mkdir()
    params = MxYRFProbeDetuningOptimizationParams(
        pzt_voltage_points=2,
        probe_power_start_v=probe_values[0],
        probe_power_stop_v=probe_values[-1],
        probe_power_points=len(probe_values),
    )
    (root / "experiment_config.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "experiment_id": (
                    "mx-y-rf-probe-detuning-optimization"
                ),
                "parameters": params.to_external(),
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    points = {}
    for pzt_index in range(2):
        for probe_index, probe_power in enumerate(probe_values):
            key = f"pzt_{pzt_index:03d}_probe_{probe_index:03d}"
            pzt = [60.0, 100.0][pzt_index]
            points[key] = {
                "pzt_index": pzt_index,
                "probe_index": probe_index,
                "pzt_voltage_v": pzt,
                "probe_power_v": probe_power,
                "pzt_voltage_readback_v": pzt,
                "pzt_actual_v": pzt - 0.2,
                "pzt_setpoint_error_v": 0.0,
                "status": "completed",
                "reason": None,
            }
    (raw / "point_manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "pzt_voltage_v": [60.0, 100.0],
                "probe_power_v": list(probe_values),
                "points": points,
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return root


def test_analysis_selects_optimum_and_saves_pzt_readback_matrices(
    local_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import lab_workflows.experiment_modules.mx_y_rf_probe_detuning_optimization.analysis as module

    run_dir = _write_analysis_run(local_tmp_path / "run")
    values = {
        "pzt_000_probe_000": 200.0,
        "pzt_000_probe_001": 100.0,
        "pzt_001_probe_000": 150.0,
        "pzt_001_probe_001": None,
    }
    monkeypatch.setattr(
        module,
        "evaluate_mx_y_rf_point",
        lambda path, params, **kwargs: _synthetic_evaluation(
            values[path.name]
        ),
    )

    def fake_full_plot(**kwargs):
        path = kwargs["results_dir"] / kwargs["filename"]
        path.write_bytes(b"plot")
        return kwargs["filename"]

    monkeypatch.setattr(module, "_plot_full_analysis", fake_full_plot)
    result = module.analyze(run_dir)
    assert result["success"]
    assert result["valid_point_count"] == 3
    assert result["best_point"]["key"] == "pzt_000_probe_001"
    assert result["best_point"]["pzt_voltage_v"] == 60.0
    assert result["zero_point_method"]["valid_point_count"] == 3
    assert (run_dir / "results" / "sensitivity_heatmap.png").exists()
    assert (
        run_dir / "results" / "best_zero_point_full_analysis.png"
    ).exists()
    with np.load(run_dir / "results" / "optimization.npz") as data:
        assert data["valid_mask"].sum() == 3
        assert data["pzt_voltage_v"].tolist() == [60.0, 100.0]
        assert data["probe_power_v"].tolist() == [0.1, 0.5]
        assert data["pzt_voltage_readback_v"][0, 1] == 60.0
        assert data["pzt_actual_v"][1, 0] == 99.8


def test_analysis_supports_single_probe_axis(
    local_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import lab_workflows.experiment_modules.mx_y_rf_probe_detuning_optimization.analysis as module

    run_dir = _write_analysis_run(
        local_tmp_path / "single_probe",
        probe_values=(0.3,),
    )
    values = {
        "pzt_000_probe_000": 200.0,
        "pzt_001_probe_000": 100.0,
    }
    monkeypatch.setattr(
        module,
        "evaluate_mx_y_rf_point",
        lambda path, params, **kwargs: _synthetic_evaluation(
            values[path.name]
        ),
    )

    def fake_full_plot(**kwargs):
        path = kwargs["results_dir"] / kwargs["filename"]
        path.write_bytes(b"plot")
        return kwargs["filename"]

    monkeypatch.setattr(module, "_plot_full_analysis", fake_full_plot)
    result = module.analyze(run_dir)
    assert result["success"]
    assert result["total_point_count"] == 2
    assert result["best_point"]["key"] == "pzt_001_probe_000"
    assert result["best_point"]["probe_power_v"] == 0.3
    assert (run_dir / "results" / "sensitivity_heatmap.png").exists()
    with np.load(run_dir / "results" / "optimization.npz") as data:
        assert data["flat_median_ft_per_sqrt_hz"].shape == (2, 1)
        assert data["probe_power_v"].tolist() == [0.3]
