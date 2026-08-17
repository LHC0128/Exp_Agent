"""Mx Keithley 6221 最优控制 平衡-灵敏度一体化实验测试。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest
import yaml

from lab_workflows.experiment_modules.mx_keithley_6221_optimal_control_balanced_sensitivity import (
    ADAPTER,
    DEFINITION,
)
from lab_workflows.experiment_modules.mx_keithley_6221_optimal_control_balanced_sensitivity.analysis import (
    analyze,
)
from lab_workflows.experiment_modules.mx_keithley_6221_optimal_control_balanced_sensitivity.models import (
    MxKeithley6221OptimalControlBalancedSensitivityParams,
)
from lab_workflows.experiment_modules.mx_keithley_6221_optimal_control_balanced_sensitivity.workflow import (
    _best_workpoint,
    _fine_axes,
)
from lab_workflows.experiments import get_experiment


@pytest.fixture
def local_tmp_path() -> Path:
    root = Path(__file__).resolve().parents[2] / "data"
    path = root / f".test_mx_6221_balanced_sensitivity_{uuid4().hex}"
    path.mkdir(mode=0o777)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _params(**overrides) -> MxKeithley6221OptimalControlBalancedSensitivityParams:
    values = {
        "confirm_gs200_connected": True,
        "control_version": "v4",
        "keithley_calibration_source_run": "0813_183404_mx_6221_main_field_cal",
        "y_rf_frequency_hz": 12000.0,
        "trigger_frequency_hz": 12000.000000000522,
    }
    values.update(overrides)
    return MxKeithley6221OptimalControlBalancedSensitivityParams(**values)


def test_definition_and_defaults_are_registered() -> None:
    definition = get_experiment(DEFINITION.id)
    assert definition.execution_mode == "typed_workflow"
    assert (
        definition.data_type
        == "Mx_Keithley_6221_Optimal_Control_Balanced_Sensitivity"
    )
    assert "keithley_6221_main_field" in definition.required_mapping_keys
    defaults = ADAPTER.defaults()
    assert defaults.balance_coarse_x_points == 9
    assert defaults.balance_coarse_y_stop_v == pytest.approx(0.25)
    assert defaults.balance_fine_enabled is True
    assert defaults.balance_fine_points == 11


def test_validate_requires_gs200_confirmation_and_axis_rules() -> None:
    params = _params(confirm_gs200_connected=False)
    errors = params.validate_model()
    assert any("GS200" in error for error in errors)
    params = _params(
        confirm_gs200_connected=True,
        balance_coarse_y_stop_v=0.0,
        balance_coarse_y_start_v=0.05,
    )
    errors = params.validate_model()
    assert any("起点 < 终点" in error for error in errors)


def test_fine_axes_follows_coarse_workpoint() -> None:
    params = _params()
    fit = {
        "fitted_workpoint": {
            "x_field_v": 0.02,
            "y_field_v": 0.12,
            "z_field_ma": 0.01,
        }
    }
    axes = _fine_axes(params, fit)
    assert axes is not None
    x_axis, y_axis, z_axis = axes
    assert x_axis[0] == pytest.approx(0.0)
    assert x_axis[-1] == pytest.approx(0.04)
    assert y_axis[0] == pytest.approx(0.10)
    assert z_axis[0] == pytest.approx(-0.02)
    assert z_axis[-1] == pytest.approx(0.04)
    # 细扫关闭时返回 None
    params_off = _params(balance_fine_enabled=False)
    assert _fine_axes(params_off, fit) is None


def test_best_workpoint_falls_back_to_fixed_params() -> None:
    params = _params(x_dc_field_v=-0.03, y_rf_offset_v=0.02, main_magnetic_field_ma=0.04)
    fit = {
        "fitted_workpoint": {
            "x_field_v": 0.01,
            "y_field_v": None,
            "z_field_ma": 0.005,
        }
    }
    x0, y0, z0 = _best_workpoint(fit, params)
    assert x0 == pytest.approx(0.01)
    assert y0 == pytest.approx(0.02)
    assert z0 == pytest.approx(0.005)


def _write_pipeline_run(run_dir: Path) -> None:
    """构造一体化运行目录：平衡 V 形数据 + RF 色散幅度/噪声数据。"""
    raw_dir = run_dir / "raw"
    results_dir = run_dir / "results"
    raw_dir.mkdir(parents=True)
    results_dir.mkdir(parents=True)
    with open(
        r"D:\Code\exp_agent\params\experiments\mx-keithley-6221-optimal-control-balanced-sensitivity.yaml",
        encoding="utf-8",
    ) as stream:
        defaults = yaml.safe_load(stream)
    config = {
        "experiment_id": DEFINITION.id,
        "data_type": DEFINITION.data_type,
        "schema_version": 1,
        "completion_status": "completed",
        "parameters": defaults["parameters"],
    }
    (run_dir / "experiment_config.yaml").write_text(
        yaml.safe_dump(config, allow_unicode=True), encoding="utf-8"
    )
    # 平衡数据：V 形响应，谷在 (0.01, 0.05, 0)
    x_axis = np.linspace(-0.02, 0.04, 13)
    y_axis = np.linspace(0.02, 0.08, 13)
    z_axis = np.array([-0.02, 0.0, 0.02])
    z_grid, x_grid, y_grid = np.meshgrid(z_axis, x_axis, y_axis, indexing="ij")
    r_mean = (
        np.abs(2.0 * (x_grid - 0.01))
        + np.abs(1.5 * (y_grid - 0.05))
        + 0.02 * z_grid**2
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
    # 相位校准结果
    (results_dir / "phase_calibration.yaml").write_text(
        yaml.safe_dump(
            {
                "mode": "y_rf_r_phase_calibration",
                "success": True,
                "selected_phase_deg": 179.0,
                "selected_y_rf_phase_deg": 179.0,
                "scan_completed": True,
                "fit_accepted": True,
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    # 相位扫描原始数据（36 点，R 呈色散折叠形）
    phase_deg = np.arange(0.0, 360.0, 10.0)
    r_phase = 0.5 + 0.3 * np.abs(
        np.sin(np.deg2rad(phase_deg - 179.0))
    )
    np.savez(
        raw_dir / "phase_scan.npz",
        scanned_phase_deg=phase_deg,
        y_rf_burst_phase_deg=phase_deg,
        r_mean_v=r_phase,
        r_std_v=np.full_like(phase_deg, 1e-4),
        x_mean_v=np.zeros_like(phase_deg),
        x_std_v=np.full_like(phase_deg, 1e-4),
        y_mean_v=np.zeros_like(phase_deg),
        y_std_v=np.full_like(phase_deg, 1e-4),
        complex_std_v=np.full_like(phase_deg, 1e-4),
        actual_rate_sa_s=np.float64(1000.0),
    )
    # 幅度扫描：干净色散形（零点偏移 -0.01 Vpp）
    amplitude = np.linspace(-0.1, 0.1, 41)
    gamma = 0.02
    r_amp = (
        0.3 * gamma * np.abs(amplitude + 0.01)
        / ((amplitude + 0.01) ** 2 + gamma**2)
        + 0.003
    )
    np.savez(
        raw_dir / "amplitude_scan.npz",
        signed_amplitude_vpp=amplitude,
        hardware_amplitude_vpp=np.abs(amplitude),
        hardware_phase_deg=np.where(amplitude >= 0, 179.0, 359.0),
        r_mean_v=r_amp,
        r_std_v=np.full_like(amplitude, 1e-4),
        accepted_attempt_index=np.zeros(amplitude.size, dtype=int),
        actual_rate_sa_s=np.float64(1000.0),
    )
    # 噪声数据：白噪声（平坦 PSD）
    rng = np.random.default_rng(7)
    for index in range(4):
        np.savez(
            raw_dir / f"noise_{index:03d}.npz",
            r_v=rng.normal(scale=1e-5, size=8192),
            actual_rate_sa_s=np.float64(8192.0),
        )


def test_pipeline_analysis_combines_balance_and_rf(local_tmp_path: Path) -> None:
    run_dir = local_tmp_path / "run"
    _write_pipeline_run(run_dir)
    result = analyze(run_dir)
    assert result["success"] is True
    assert result["experiment_id"] == DEFINITION.id
    # 平衡部分
    balance = result["balance"]
    assert balance is not None
    assert balance["fitted_workpoint"]["x_field_v"] == pytest.approx(0.01, abs=0.003)
    assert balance["fitted_workpoint"]["y_field_v"] == pytest.approx(0.05, abs=0.003)
    # RF 部分
    rf = result["rf_sensitivity"]
    assert rf is not None
    assert rf["primary_slope_v_per_vpp"] > 0
    assert (run_dir / "results" / "balance_analysis.yaml").is_file()
    assert (run_dir / "results" / "rf_analysis.yaml").is_file()
    assert (run_dir / "results" / "analysis.yaml").is_file()


def test_pipeline_analysis_tolerates_missing_rf(local_tmp_path: Path) -> None:
    run_dir = local_tmp_path / "balance_only"
    _write_pipeline_run(run_dir)
    (run_dir / "results" / "phase_calibration.yaml").unlink()
    result = analyze(run_dir)
    assert result["success"] is True
    assert result["balance"] is not None
    assert result["rf_sensitivity"] is None
    assert any("phase_calibration" in warning for warning in result["warnings"])
