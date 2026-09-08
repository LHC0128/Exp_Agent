"""Mx Z 最优控制 XY 噪声谱实验测试。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import yaml

from lab_workflows.experiment_modules.mx_z_optimal_control_xy_noise_spectrum import (
    ADAPTER,
    DEFINITION,
)
from lab_workflows.experiment_modules.mx_z_optimal_control_xy_noise_spectrum.analysis import (
    analyze,
)
from lab_workflows.experiment_modules.mx_z_optimal_control_xy_noise_spectrum.models import (
    MxZOptimalControlXYNoiseSpectrumParams,
)
from lab_workflows.experiment_modules.mx_z_optimal_control_xy_noise_spectrum.workflow import (
    _acquire_point,
    _grid_points,
    _initial_manifest,
)
from lab_workflows.experiments import get_experiment


def _write_run(
    root: Path,
    *,
    rate: float = 4096.0,
    count: int = 3,
    band_max_hz: float = 200.0,
) -> Path:
    run_dir = root / "run"
    raw = run_dir / "raw"
    raw.mkdir(parents=True)
    params = MxZOptimalControlXYNoiseSpectrumParams(
        x_field_start_v=0.0,
        x_field_stop_v=0.01,
        x_field_points=2,
        y_field_start_v=-0.01,
        y_field_stop_v=0.0,
        y_field_points=2,
        noise_n_avg=count,
        noise_duration_s=1.0,
        noise_rate_sa_s=rate,
        noise_band_max_hz=band_max_hz,
    )
    points = _grid_points(params)
    manifest = _initial_manifest(params, points)
    rng = np.random.default_rng(42)
    sample_count = int(rate)
    time_s = np.arange(sample_count, dtype=float) / rate
    for item in points:
        key = item["key"]
        manifest["points"][key]["status"] = "completed"
        point_dir = raw / "points" / key
        point_dir.mkdir(parents=True)
        # 使一个点具有确定性窄带峰，验证主排序和峰值诊断。
        scale = 1e-3 if key == "x_000_y_000" else 2e-3
        for index in range(count):
            values = rng.normal(scale=scale, size=sample_count)
            if key == "x_000_y_000":
                values += 5e-3 * np.sin(2 * np.pi * 50.0 * time_s)
            np.savez(
                point_dir / f"noise_{index:03d}.npz",
                time_s=time_s,
                r_v=values,
                actual_rate_sa_s=np.float64(rate),
            )
    (raw / "point_manifest.yaml").write_text(
        yaml.safe_dump(manifest, allow_unicode=True), encoding="utf-8"
    )
    (run_dir / "experiment_config.yaml").write_text(
        yaml.safe_dump(
            {
                "experiment_id": DEFINITION.id,
                "data_type": DEFINITION.data_type,
                "schema_version": params.schema_version,
                "parameters": params.to_external(),
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    return run_dir


def test_defaults_and_registration() -> None:
    params = ADAPTER.defaults()
    assert params.validate() == []
    assert params.x_field_points == 11
    assert params.y_field_points == 11
    assert params.noise_band_max_hz == pytest.approx(200.0)
    assert get_experiment(DEFINITION.id) is DEFINITION
    assert DEFINITION.execution_mode == "typed_workflow"
    assert DEFINITION.data_type == "Mx_Z_Optimal_Control_XY_Noise_Spectrum"
    visible = {item["name"] for item in DEFINITION.schema()["fields"]}
    assert "X_FIELD_START_V" in visible
    assert "NOISE_BAND_MAX_HZ" in visible
    assert "Y_RF_AMP_START_VPP" not in visible


def test_axis_and_band_validation() -> None:
    params = MxZOptimalControlXYNoiseSpectrumParams(
        x_field_start_v=1.0,
        x_field_stop_v=0.0,
        x_field_points=2,
    )
    assert any("X 偏置" in error for error in params.validate())
    params = MxZOptimalControlXYNoiseSpectrumParams(
        low_freq_skip_hz=200.0,
        noise_band_max_hz=200.0,
    )
    assert any("LOW_FREQ_SKIP_HZ" in error for error in params.validate())
    params = MxZOptimalControlXYNoiseSpectrumParams(
        noise_rate_sa_s=300.0,
        noise_band_max_hz=200.0,
    )
    assert any("Nyquist" in error for error in params.validate())


def test_point_acquisition_keeps_y_dc_and_records_control_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace
    from lab_workflows.experiment_modules.mx_z_optimal_control_xy_noise_spectrum import (
        workflow,
    )

    captured: dict[str, object] = {}

    def fake_acquire_noise(
        _params: object,
        point_run_dir: object,
        _devices: dict[str, object],
        _channels: dict[str, int],
        _device_id: str,
        **kwargs: object,
    ) -> float:
        captured.update(kwargs)
        setter = kwargs["set_y_rf_off_state"]
        assert callable(setter)
        setter()
        captured["point_raw"] = point_run_dir.raw
        return 57565.8

    dc_calls: list[tuple[int, str, float]] = []
    monkeypatch.setattr(workflow, "_acquire_noise", fake_acquire_noise)
    monkeypatch.setattr(
        workflow,
        "configure_fixed_dc_field",
        lambda _device, channel, key, value: dc_calls.append(
            (channel, key, float(value))
        ),
    )
    params = MxZOptimalControlXYNoiseSpectrumParams(noise_n_avg=1)
    run_dir = SimpleNamespace(raw=tmp_path / "raw", results=tmp_path / "results")
    point = {
        "key": "x_003_y_003",
        "x_field_v": 0.006,
        "y_field_v": -0.004,
    }
    result = _acquire_point(
        params,
        point,
        run_dir,
        {"xy_field": object()},
        {"x_field": 1, "y_rf": 2},
        "dev18246",
    )
    assert result["actual_noise_rate_sa_s"] == pytest.approx(57565.8)
    assert dc_calls == [
        (1, "X_magnetic_field", 0.006),
        (2, "Y_magnetic_field", -0.004),
        (2, "Y_magnetic_field", -0.004),
    ]
    assert captured["y_rf_dc_v"] == pytest.approx(-0.004)
    assert captured["y_rf_output_on"] is True
    metadata = captured["noise_metadata"]
    assert metadata["y_rf_waveform_mode"] == "dc"
    assert bool(metadata["control_output_on"])
    assert metadata["temperature_gate_state"] == "off_during_acquire_then_on"


def test_analysis_averages_psd_and_ranks_points(tmp_path: Path) -> None:
    run_dir = _write_run(tmp_path, rate=4096.0, count=3)
    result = analyze(run_dir)
    assert result["point_count"] == 4
    assert result["best_point"]["key"] == "x_000_y_000"
    assert result["best_point"]["noise_peak_frequency_hz"] == pytest.approx(50.0, abs=2.0)
    for filename in (
        "noise_median_matrix.npz",
        "analysis.yaml",
        "analysis.json",
        "noise_median_heatmap.png",
        "noise_spectrum_best.png",
    ):
        assert (run_dir / "results" / filename).is_file()
    with np.load(run_dir / "results" / "noise_median_matrix.npz") as data:
        assert data["noise_median_r_asd_v_per_sqrt_hz"].shape == (2, 2)


def test_analysis_rejects_missing_record_and_mismatched_rate(tmp_path: Path) -> None:
    run_dir = _write_run(tmp_path, rate=4096.0, count=2)
    missing = run_dir / "raw" / "points" / "x_000_y_000" / "noise_001.npz"
    missing.unlink()
    with pytest.raises(FileNotFoundError, match="缺少噪声记录"):
        analyze(run_dir)

    run_dir = _write_run(tmp_path / "mismatch", rate=4096.0, count=2)
    path = run_dir / "raw" / "points" / "x_000_y_000" / "noise_001.npz"
    with np.load(path) as data:
        np.savez(path, r_v=data["r_v"], actual_rate_sa_s=np.float64(2048.0))
    with pytest.raises(ValueError, match="采样率不一致"):
        analyze(run_dir)


def test_analysis_rejects_band_above_actual_nyquist(tmp_path: Path) -> None:
    run_dir = _write_run(tmp_path, rate=4096.0, count=2, band_max_hz=3000.0)
    with pytest.raises(ValueError, match="Nyquist"):
        analyze(run_dir)
