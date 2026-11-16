"""合成原始数据的离线分析回归：主磁场锚点、过零几何、零场剔除与结果格式。"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import yaml

from . import analysis, analysis_steps as steps, run_format
from .models import CoilConstantCalibrationParams

GAMMA_HZ = 800.0
HALF_WIDTH_HZ = 2400.0
STEP_HZ = 100.0


def _lorentzian(frequency, center):
    return 1.0 + 2.0 * GAMMA_HZ / np.hypot(frequency - center, GAMMA_HZ)


def _write_curve(directory: Path, index: int, current_ma: float, center_hz: float, anchor_hz: float, *,
                 flat=False, direction="forward"):
    point_dir = directory / "raw" / f"current_{index:03d}"
    point_dir.mkdir(parents=True, exist_ok=True)
    near_zero = center_hz < 4000.0
    if near_zero:
        frequencies = np.asarray([], dtype=float)
        response = np.asarray([], dtype=float)
        std = np.asarray([], dtype=float)
    else:
        frequencies = center_hz - HALF_WIDTH_HZ + STEP_HZ * np.arange(
            int(round(2 * HALF_WIDTH_HZ / STEP_HZ)) + 1)
        response = np.full(frequencies.shape, 2.0) if flat else _lorentzian(frequencies, center_hz)
        noise = np.random.default_rng(index).normal(0, 2e-4, frequencies.size)
        response = response + noise
        std = np.full(frequencies.shape, 2e-4)
    np.savez(
        point_dir / "frequency_scan.npz",
        frequency_hz=frequencies, r_mean_v=response, r_std_v=std,
        current_a=np.float64(current_ma * 1e-3), current_ma=np.float64(current_ma),
        sense_voltage_mean_v=np.float64(current_ma * 1e-3 * 10.0),
        sense_voltage_std_v=np.float64(1e-5), drive_v=np.float64(current_ma * 1e-3 / 0.045),
        target_current_ma=np.float64(current_ma), scan_direction=np.asarray(direction),
        predicted_center_hz=np.float64(center_hz), window_half_width_hz=np.float64(HALF_WIDTH_HZ),
        anchor_center_hz=np.float64(anchor_hz), near_zero_skipped=np.uint8(near_zero),
        acquisition_note=np.asarray(""),
        accepted_attempt_index=np.zeros(1, dtype=np.int64), accepted_file=np.asarray(["x"]),
        actual_rate_sa_s=np.float64(1000.0))
    return (point_dir / "frequency_scan.npz").relative_to(directory).as_posix()


def make_run(tmp_path, *, k_hz_per_ma=600.0, i0_ma=-50.0, currents=None, flat_index=None):
    """anchor = -K·I0，使绝对模型 f=|K(I−I0)| 与 f=|anchor + K·I| 等价。"""
    directory = tmp_path / "data" / run_format.DATA_TYPE / "0116_0800_synthetic"
    (directory / "raw").mkdir(parents=True)
    if currents is None:
        currents = [-100.0, -75.0, -50.0, -25.0, 0.0, 25.0, 50.0, 75.0, 100.0]
    anchor_hz = -k_hz_per_ma * i0_ma
    params = replace(
        CoilConstantCalibrationParams(),
        main_field_enabled=True, main_field_ma=15.555, main_field_center_hz=anchor_hz,
        initial_hz_per_ma=k_hz_per_ma, prediction_polarity="positive_increases_frequency",
        current_start_ma=min(currents), current_stop_ma=max(currents), current_step_ma=25.0,
        bidirectional_scan=False, near_zero_scan_floor_hz=4000.0, near_zero_fit_floor_hz=2000.0,
        frequency_half_width_hz=HALF_WIDTH_HZ, frequency_step_hz=STEP_HZ)
    files, targets = [], []
    for index, current_ma in enumerate(currents):
        center = abs(anchor_hz + k_hz_per_ma * current_ma)
        files.append(_write_curve(directory, index, current_ma, center, anchor_hz, flat=index == flat_index))
        targets.append(current_ma)
    np.savez(directory / "raw/current_scan_index.npz",
             target_current_ma=np.asarray(targets, dtype=float),
             current_ma=np.asarray(targets, dtype=float),
             current_a=np.asarray(targets, dtype=float) * 1e-3,
             drive_v=np.asarray(targets, dtype=float) * 1e-3 / 0.045,
             scan_direction=np.asarray(["forward"] * len(targets)),
             predicted_center_hz=np.asarray([abs(anchor_hz + k_hz_per_ma * value) for value in targets],
                                            dtype=float),
             near_zero_skipped=np.asarray([abs(anchor_hz + k_hz_per_ma * value) < 4000.0 for value in targets],
                                          dtype=bool),
             scan_failed=np.zeros(len(targets), dtype=bool),
             summary_file=np.asarray(files),
             anchor_center_hz=np.float64(anchor_hz), polarity_sign=np.float64(1.0),
             initial_hz_per_ma=np.float64(k_hz_per_ma))
    config = {
        "experiment_id": run_format.EXPERIMENT_ID, "data_type": run_format.DATA_TYPE,
        "raw_format_version": run_format.RAW_FORMAT_VERSION, "schema_version": 1,
        "completion_status": "completed", "parameters": params.to_external(),
        "dc_check": {"k_a_per_v": 0.045, "offset_fit_a": 0.0, "r_squared": 0.999,
                     "points": [{"drive_v": value * 1e-3 / 0.045, "current_a": value * 1e-3}
                                for value in (-20.0, 0.0, 20.0)]},
        "geometry": {}, "main_field": {"enabled": True, "current_ma": 15.555, "center_hz": anchor_hz},
    }
    (directory / "experiment_config.yaml").write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return directory


def payload_of(directory):
    return json.loads((directory / "results" / "calibration_evaluation.json").read_text(encoding="utf-8"))


def test_recovers_known_constant_and_crossing(tmp_path):
    directory = make_run(tmp_path)
    summary = analysis.analyze(directory)
    assert summary["status"] == "completed"
    payload = payload_of(directory)
    assert payload["success"], payload["rejection_reasons"]
    assert payload["K_Hz_per_mA"] == pytest.approx(600.0, rel=0.01)
    assert payload["K_B_nT_per_mA"] == pytest.approx(600.0 / 7.0, rel=0.01)
    assert payload["K_signed_Hz_per_mA"] == pytest.approx(600.0, rel=0.01)
    assert payload["I0_ma"] == pytest.approx(-50.0, abs=0.5)
    assert payload["anchor_center_hz"] == pytest.approx(30000.0)
    assert payload["geometry"]["crossing_inside"] is True
    assert payload["geometry"]["branch_counts"]["below"] >= 2
    assert payload["branch_fits"]["slope_relative_difference"] < 0.05
    assert payload["closure"]["ratio"] == pytest.approx(1.0, rel=0.02)
    for name in ("analysis.yaml", "analysis.json", "calibration_results.npz",
                 "frequency_response_fits.png", "coil_constant_calibration.png",
                 "coil_voltage_response.png", "coil_constant_residuals.png", "dc_iv_check.png"):
        assert (directory / "results" / name).is_file(), name


def test_linear_case_without_crossing_is_accepted(tmp_path):
    directory = make_run(tmp_path, i0_ma=-200.0, currents=[0.0, 25.0, 50.0, 75.0, 100.0])
    analysis.analyze(directory)
    payload = payload_of(directory)
    assert payload["success"], payload["rejection_reasons"]
    assert payload["geometry"]["linear_case"] is True
    assert payload["K_Hz_per_mA"] == pytest.approx(600.0, rel=0.01)
    assert payload["K_signed_Hz_per_mA"] == pytest.approx(600.0, rel=0.01)


def test_near_zero_curve_is_excluded_and_flagged(tmp_path):
    directory = make_run(tmp_path)
    analysis.analyze(directory)
    payload = payload_of(directory)
    skipped = [curve for curve in payload["curves"] if curve["near_zero_skipped"]]
    assert skipped and all(not curve["fit"]["success"] for curve in skipped)


def test_flat_curve_is_excluded_without_breaking_calibration(tmp_path):
    directory = make_run(tmp_path, flat_index=3)  # 电流 0 mA，中心 30 kHz 的曲线退化为常量
    analysis.analyze(directory)
    payload = payload_of(directory)
    excluded = [curve for curve in payload["curves"] if not curve["fit"]["success"] and not curve["near_zero_skipped"]]
    assert excluded
    assert payload["accepted_curve_count"] >= 4


def test_resolve_options_rejects_unknown_analysis_key():
    params = CoilConstantCalibrationParams()
    with pytest.raises(ValueError, match="不支持的分析覆盖值"):
        steps.resolve_options(params, {"UNKNOWN_KEY": 1})
    resolved = steps.resolve_options(params, {"NEAR_ZERO_FIT_FLOOR_HZ": 5500.0})
    assert resolved.near_zero_fit_floor_hz == 5500.0


def test_plain_maps_non_finite_to_null():
    assert steps.plain(float("nan")) is None
    assert steps.plain({"a": np.float64(np.inf), "b": np.asarray([1.0, np.nan])}) == {
        "a": None, "b": [1.0, None]}


def test_missing_index_is_rejected(tmp_path):
    directory = make_run(tmp_path)
    (directory / "raw/current_scan_index.npz").unlink()
    with pytest.raises(ValueError, match="current_scan_index"):
        analysis.analyze(directory)
