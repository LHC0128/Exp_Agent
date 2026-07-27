"""Mx Y RF 光功率灵敏度优化实验测试。"""

from __future__ import annotations

from pathlib import Path
import shutil
from types import SimpleNamespace
from uuid import uuid4

import numpy as np
import pytest
import yaml

from lab_workflows.common import WorkflowCancelled
from lab_workflows.experiment_modules.mx_y_rf_power_optimization import (
    DEFINITION,
    MxYRFPowerOptimizationParams,
)
from lab_workflows.experiment_modules.mx_y_rf_power_optimization.analysis import (
    analyze,
)
from lab_workflows.experiment_modules.mx_y_rf_power_optimization.scan import (
    build_power_axes,
    iter_serpentine_grid,
)
from lab_workflows.experiment_modules.mx_y_rf_sensitivity.workflow import (
    RPointQualityError,
)
from lab_workflows.experiments import get_experiment


@pytest.fixture
def local_tmp_path() -> Path:
    root = Path(__file__).resolve().parents[2] / "data"
    path = root / f".test_mx_y_rf_power_optimization_{uuid4().hex}"
    path.mkdir(mode=0o777)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def test_defaults_schema_registry_and_grid() -> None:
    params = MxYRFPowerOptimizationParams()
    assert params.validate() == []
    assert params.linewidth_mode == "amplitude_equivalent"
    assert params.y_rf_frequency_hz == 90000.0
    assert params.y_rf_amp_points == 21
    assert params.noise_n_avg == 5
    assert params.main_magnetic_field_ma == 9.28
    assert params.temperature_c == 120.0
    assert params.y_rf_nt_per_vpp == 1517.79147
    definition = get_experiment("mx-y-rf-power-optimization")
    assert definition is DEFINITION
    assert definition.category == "optimization"
    assert definition.execution_mode == "typed_workflow"
    assert definition.data_type == "Mx_Y_RF_Power_Optimization"

    fields = {item["name"]: item for item in definition.schema()["fields"]}
    assert fields["PUMP_POWER_START_V"]["default"] == 0.2
    assert fields["PUMP_POWER_STOP_V"]["default"] == 0.8
    assert fields["PUMP_POWER_POINTS"]["default"] == 7
    assert fields["PROBE_POWER_START_V"]["default"] == 0.1
    assert fields["PROBE_POWER_STOP_V"]["default"] == 0.5
    assert fields["PROBE_POWER_POINTS"]["default"] == 7
    assert "LINEWIDTH_MODE" not in fields
    assert "FREQUENCY_START_HZ" not in fields

    pump_axis, probe_axis = build_power_axes(params)
    np.testing.assert_allclose(pump_axis, np.linspace(0.2, 0.8, 7))
    np.testing.assert_allclose(probe_axis, np.linspace(0.1, 0.5, 7))
    points = iter_serpentine_grid(params)
    assert len(points) == 49
    assert [item.probe_index for item in points[:7]] == list(range(7))
    assert [item.probe_index for item in points[7:14]] == list(reversed(range(7)))
    assert points[0].key == "pump_000_probe_000"
    assert points[-1].key == "pump_006_probe_006"


@pytest.mark.parametrize(
    ("params", "message"),
    [
        (
            MxYRFPowerOptimizationParams(pump_power_start_v=-0.1),
            "PUMP_POWER_START_V",
        ),
        (
            MxYRFPowerOptimizationParams(probe_power_stop_v=1.1),
            "PROBE_POWER_STOP_V",
        ),
        (
            MxYRFPowerOptimizationParams(y_rf_nt_per_vpp=0.0),
            "Y_RF_NT_PER_VPP",
        ),
        (
            MxYRFPowerOptimizationParams(linewidth_mode="frequency_sweep"),
            "LINEWIDTH_MODE",
        ),
    ],
)
def test_preflight_rejects_invalid_optimization_params(
    params: MxYRFPowerOptimizationParams,
    message: str,
) -> None:
    assert any(message in error for error in params.validate())


class _FakeLaser:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def setup_dc(self, value: float, *, channel: int) -> None:
        self.calls.append(("dc", channel, value))

    def set_output(self, value: bool, *, channel: int) -> None:
        self.calls.append(("output", channel, value))


def test_power_point_writes_pump_then_probe_without_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import lab_workflows.experiment_modules.mx_y_rf_power_optimization.workflow as workflow

    laser = _FakeLaser()
    point = iter_serpentine_grid(MxYRFPowerOptimizationParams())[0]
    workflow._set_power_point(
        point,
        {"laser": laser},
        {"pump_laser": 1, "probe_laser": 2},
    )
    assert laser.calls == [
        ("dc", 1, 0.2),
        ("output", 1, True),
        ("dc", 2, 0.1),
        ("output", 2, True),
    ]
    assert not hasattr(workflow, "_power_settle_sleep")


def test_quality_failure_is_checkpointed_and_grid_continues(
    local_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import lab_workflows.experiment_modules.mx_y_rf_power_optimization.workflow as workflow

    params = MxYRFPowerOptimizationParams(
        pump_power_points=2,
        probe_power_points=2,
    )
    laser = _FakeLaser()
    run_dir = SimpleNamespace(raw=local_tmp_path / "raw")
    outcomes = iter(
        [
            RPointQualityError("连续坏点"),
            {
                "actual_response_rate_sa_s": 1000.0,
                "actual_noise_rate_sa_s": 50000.0,
            },
            {
                "actual_response_rate_sa_s": 1000.0,
                "actual_noise_rate_sa_s": 50000.0,
            },
            {
                "actual_response_rate_sa_s": 1000.0,
                "actual_noise_rate_sa_s": 50000.0,
            },
        ]
    )
    monkeypatch.setattr(workflow, "check_cancelled", lambda: None)

    def fake_acquire(*args, **kwargs):
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(workflow, "acquire_mx_y_rf_point", fake_acquire)
    summary = workflow._acquire_grid(
        params,
        run_dir,
        {"laser": laser},
        {"pump_laser": 1, "probe_laser": 2},
        1000.0,
        "dev",
    )
    assert summary["completed_point_count"] == 3
    assert summary["invalid_quality_point_count"] == 1
    manifest = yaml.safe_load(
        (run_dir.raw / "point_manifest.yaml").read_text(encoding="utf-8")
    )
    assert manifest["points"]["pump_000_probe_000"]["status"] == "invalid_quality"
    assert manifest["points"]["pump_000_probe_001"]["status"] == "completed"


def test_hardware_error_aborts_grid(
    local_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import lab_workflows.experiment_modules.mx_y_rf_power_optimization.workflow as workflow

    params = MxYRFPowerOptimizationParams(
        pump_power_points=2,
        probe_power_points=2,
    )
    run_dir = SimpleNamespace(raw=local_tmp_path / "raw")
    monkeypatch.setattr(workflow, "check_cancelled", lambda: None)
    monkeypatch.setattr(
        workflow,
        "acquire_mx_y_rf_point",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("设备通信失败")
        ),
    )
    with pytest.raises(RuntimeError, match="设备通信失败"):
        workflow._acquire_grid(
            params,
            run_dir,
            {"laser": _FakeLaser()},
            {"pump_laser": 1, "probe_laser": 2},
            1000.0,
            "dev",
        )


def test_cancelled_grid_keeps_completed_point_checkpoint(
    local_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import lab_workflows.experiment_modules.mx_y_rf_power_optimization.workflow as workflow

    params = MxYRFPowerOptimizationParams(
        pump_power_points=2,
        probe_power_points=2,
    )
    run_dir = SimpleNamespace(raw=local_tmp_path / "raw")
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
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(workflow, "acquire_mx_y_rf_point", fake_acquire)
    with pytest.raises(WorkflowCancelled):
        workflow._acquire_grid(
            params,
            run_dir,
            {"laser": _FakeLaser()},
            {"pump_laser": 1, "probe_laser": 2},
            1000.0,
            "dev",
        )
    manifest = yaml.safe_load(
        (run_dir.raw / "point_manifest.yaml").read_text(encoding="utf-8")
    )
    assert manifest["points"]["pump_000_probe_000"]["status"] == "completed"
    assert manifest["points"]["pump_000_probe_001"]["status"] == "running"


def test_grid_tqdm_tracks_all_points_and_writes_eta_snapshots(
    local_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import lab_workflows.experiment_modules.mx_y_rf_power_optimization.workflow as workflow

    instances: list[SimpleNamespace] = []

    class FakeTqdm:
        def __init__(self, *, total, **kwargs) -> None:
            self.total = total
            self.n = 0
            self.closed = False
            self.postfixes: list[str] = []
            instances.append(self)

        def set_postfix_str(self, value: str) -> None:
            self.postfixes.append(value)

        def update(self, count: int) -> None:
            self.n += count

        def close(self) -> None:
            self.closed = True

        def __str__(self) -> str:
            return f"{self.n}/{self.total} [ETA 00:01]"

    monkeypatch.setattr(workflow, "tqdm", FakeTqdm)
    monkeypatch.setattr(workflow, "check_cancelled", lambda: None)
    monkeypatch.setattr(
        workflow,
        "acquire_mx_y_rf_point",
        lambda *args, **kwargs: {
            "actual_response_rate_sa_s": 1000.0,
            "actual_noise_rate_sa_s": 50000.0,
        },
    )
    params = MxYRFPowerOptimizationParams(
        pump_power_points=2,
        probe_power_points=2,
    )

    summary = workflow._acquire_grid(
        params,
        SimpleNamespace(raw=local_tmp_path / "raw"),
        {"laser": _FakeLaser()},
        {"pump_laser": 1, "probe_laser": 2},
        1000.0,
        "dev",
    )

    assert summary["completed_point_count"] == 4
    assert summary["actual_response_rates_sa_s"] == [1000.0] * 4
    assert len(instances) == 1
    assert instances[0].total == 4
    assert instances[0].n == 4
    assert instances[0].closed
    output = capsys.readouterr().out
    assert output.count("[ETA 00:01]") == 4
    assert "4/4 [ETA 00:01]" in output


def test_shared_point_helper_zeros_y_rf_when_quality_fails(
    local_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import lab_workflows.experiment_modules.mx_y_rf_sensitivity.workflow as workflow

    zeroed: list[tuple] = []
    monkeypatch.setattr(
        workflow,
        "_acquire_amplitude_scan",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RPointQualityError("连续坏点")
        ),
    )
    monkeypatch.setattr(
        workflow,
        "_set_y_rf_zero_off",
        lambda device, channel: zeroed.append((device, channel)),
    )
    xy = object()
    with pytest.raises(RPointQualityError):
        workflow.acquire_mx_y_rf_point(
            MxYRFPowerOptimizationParams(),
            local_tmp_path / "point",
            {"xy_field": xy},
            {"y_rf": 2},
            1000.0,
            "dev",
        )
    assert zeroed == [(xy, 2)]


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
def test_run_restores_baseline_on_all_exit_paths(
    local_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    import lab_workflows.experiment_modules.mx_y_rf_power_optimization.workflow as workflow

    run_dir = _FakeRunDir(local_tmp_path / outcome)
    restored: list[dict] = []
    monkeypatch.setattr(workflow, "find_project_root", lambda: local_tmp_path)
    monkeypatch.setattr(
        workflow,
        "load_mapping",
        lambda root: {"lockin_r": {"device_id": "dev"}},
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
        lambda *args: ({"laser": object()}, {"pump_laser": 1, "probe_laser": 2}),
    )
    monkeypatch.setattr(workflow, "snapshot_mx_y_rf_state", lambda *args: {})
    monkeypatch.setattr(
        workflow,
        "configure_mx_y_rf_outputs",
        lambda *args: (1000.0, {}),
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
        "restore_mx_y_rf_laser_powers",
        lambda *args, **kwargs: restored.append(kwargs),
    )
    params = MxYRFPowerOptimizationParams()
    if outcome == "success":
        assert workflow.run(params) == run_dir.root
    else:
        expected = RuntimeError if outcome == "error" else WorkflowCancelled
        with pytest.raises(expected):
            workflow.run(params)
    assert restored == [{"pump_power_v": 0.5, "probe_power_v": 0.3}]


def _synthetic_evaluation(value: float | None) -> dict:
    valid = value is not None
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
        "hwhm_hz": 100.0,
        "sensitivity": {
            "flat_detection_success": valid,
            "flat_detection_reason": "ok" if valid else "no flat band",
            "flat_band_hz": np.asarray([10.0, 100.0]),
            "flat_mask": np.asarray([True, True]),
            "raw_ft_per_sqrt_hz": np.asarray([100.0, 100.0]),
            "corrected_ft_per_sqrt_hz": np.asarray([100.0, 100.0]),
            "flat_median_ft_per_sqrt_hz": (
                float(value) if value is not None else np.nan
            ),
        },
        "amplitude_vpp": np.asarray([-0.1, 0.0, 0.1]),
        "r_mean_v": np.asarray([0.1, 0.0, 0.1]),
        "fit_mask": np.asarray([True, True, True]),
        "bad_point_mask": np.asarray([False, False, False]),
        "frequency_hz": np.asarray([1.0, 10.0]),
    }


def _write_analysis_run(root: Path) -> Path:
    raw = root / "raw"
    raw.mkdir(parents=True)
    (root / "results").mkdir()
    params = MxYRFPowerOptimizationParams(
        pump_power_points=2,
        probe_power_points=2,
    )
    (root / "experiment_config.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "experiment_id": "mx-y-rf-power-optimization",
                "parameters": params.to_external(),
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    points = {}
    for pump_index in range(2):
        for probe_index in range(2):
            key = f"pump_{pump_index:03d}_probe_{probe_index:03d}"
            points[key] = {
                "pump_index": pump_index,
                "probe_index": probe_index,
                "pump_power_v": [0.2, 0.8][pump_index],
                "probe_power_v": [0.1, 0.5][probe_index],
                "status": "completed",
                "reason": None,
            }
    (raw / "point_manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "pump_power_v": [0.2, 0.8],
                "probe_power_v": [0.1, 0.5],
                "points": points,
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return root


def test_analysis_selects_known_optimum_and_excludes_invalid_point(
    local_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import lab_workflows.experiment_modules.mx_y_rf_power_optimization.analysis as module

    run_dir = _write_analysis_run(local_tmp_path / "run")
    values = {
        "pump_000_probe_000": 200.0,
        "pump_000_probe_001": 100.0,
        "pump_001_probe_000": 150.0,
        "pump_001_probe_001": None,
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
    result = analyze(run_dir)
    assert result["success"]
    assert result["valid_point_count"] == 3
    assert result["best_point"]["key"] == "pump_000_probe_001"
    assert result["best_point"]["flat_median_ft_per_sqrt_hz"] == 100.0
    assert (run_dir / "results" / "sensitivity_heatmap.png").exists()
    assert (run_dir / "results" / "slope_heatmap.png").exists()
    assert (run_dir / "results" / "hwhm_heatmap.png").exists()
    assert (run_dir / "results" / "best_point_full_analysis.png").exists()
    assert len(result["point_full_analysis_files"]) == 4
    for key in values:
        assert (
            run_dir
            / "results"
            / "points"
            / key
            / "full_analysis.png"
        ).exists()
    with np.load(run_dir / "results" / "optimization.npz") as data:
        assert data["valid_mask"].sum() == 3
        assert data["flat_median_ft_per_sqrt_hz"][0, 1] == 100.0


def test_analysis_reports_zero_valid_points_after_writing_diagnostics(
    local_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import lab_workflows.experiment_modules.mx_y_rf_power_optimization.analysis as module

    run_dir = _write_analysis_run(local_tmp_path / "run")
    monkeypatch.setattr(
        module,
        "evaluate_mx_y_rf_point",
        lambda path, params, **kwargs: _synthetic_evaluation(None),
    )
    with pytest.raises(RuntimeError, match="未找到可用最优点"):
        analyze(run_dir)
    result = yaml.safe_load(
        (run_dir / "results" / "optimization.yaml").read_text(encoding="utf-8")
    )
    assert result["success"] is False
    assert result["valid_point_count"] == 0
    assert result["best_point"] is None
