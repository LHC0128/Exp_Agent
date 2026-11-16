"""参数模型、外部键、迁移和过零几何派生的无硬件回归。"""
from __future__ import annotations

import math
from dataclasses import replace

import pytest

from ...common import find_project_root
from .models import CoilConstantCalibrationParams


def test_default_yaml_round_trip():
    path = find_project_root() / "params" / "experiments" / "coil-constant-calibration.yaml"
    params = CoilConstantCalibrationParams.from_yaml(path)
    assert params.main_field_enabled is True
    assert params.main_field_ma == pytest.approx(15.555)
    assert params.main_field_center_hz == pytest.approx(150_000.0)
    assert CoilConstantCalibrationParams.from_external(params.to_external()) == params


def test_missing_main_field_flag_is_inferred_from_current():
    external = CoilConstantCalibrationParams().to_external()
    external.pop("MAIN_FIELD_ENABLED")
    external["MAIN_FIELD_MA"] = 12.5
    assert CoilConstantCalibrationParams.from_external(external).main_field_enabled is True
    external["MAIN_FIELD_MA"] = 0.0
    assert CoilConstantCalibrationParams.from_external(external).main_field_enabled is False


def test_current_axis_is_bidirectional_without_repeating_endpoints():
    params = CoilConstantCalibrationParams()
    forward = params.forward_currents_ma()
    targets = params.current_targets_ma()
    directions = params.current_directions()
    assert forward == [-200.0, -150.0, -100.0, -50.0, 0.0, 50.0, 100.0, 150.0, 200.0]
    assert targets == [*forward, *forward[-2::-1]]
    assert directions.count("reverse") == len(forward) - 1
    assert replace(params, bidirectional_scan=False).current_targets_ma() == forward


def test_anchor_geometry():
    params = CoilConstantCalibrationParams()
    geometry = params.branch_counts()
    assert geometry["crossing_current_ma"] == pytest.approx(-150_000.0 / 602.0, rel=1e-9)
    assert geometry["crossing_inside"] is False and geometry["linear_case"] is True
    assert params.predicted_center_hz(0.0) == pytest.approx(150_000.0)
    assert params.predicted_center_hz(50.0) == pytest.approx(150_000.0 + 602.0 * 50.0)
    assert params.signed_center_hz(-200.0) == pytest.approx(150_000.0 - 602.0 * 200.0)


@pytest.mark.parametrize("changes, message", [
    ({"amplifier_gain_db": 7.3}, "0.5 dB"),
    ({"prediction_polarity": "unselected"}, "PREDICTION_POLARITY"),
    ({"current_step_ma": 0.0}, "CURRENT_STEP_MA"),
    ({"dc_check_currents_ma": [0.0, 10.0]}, "至少需要 3"),
    ({"sense_resistor_ohm": 0.0}, "SENSE_RESISTOR_OHM"),
    ({"main_field_center_hz": 1_000.0}, "MAIN_FIELD_CENTER_HZ"),
])
def test_validation_rejects_unsupported_plans(changes, message):
    errors = replace(CoilConstantCalibrationParams(), **changes).validate()
    assert any(message in error for error in errors), errors


def test_schema_keeps_stable_external_keys_and_units():
    schema = CoilConstantCalibrationParams().schema()
    fields = {field["name"]: field for field in schema["fields"]}
    assert fields["AMPLIFIER_GAIN_DB"]["unit"] == "dB"
    assert fields["CURRENT_STEP_MA"]["unit"] == "mA"
    assert fields["MAIN_FIELD_MA"]["unit"] == "mA"
    assert fields["MAIN_FIELD_CENTER_HZ"]["unit"] == "Hz"
    assert fields["FIXED_PARAMS.temperature"]["unit"] == "°C"
    assert set(CoilConstantCalibrationParams().to_external()) == set(fields)


def test_derive_preview_reports_geometry():
    derived = CoilConstantCalibrationParams.derive_external(CoilConstantCalibrationParams().to_external())
    assert derived["crossing_inside"] is False and derived["linear_case"] is True
    assert derived["anchor_center_hz"] == pytest.approx(150_000.0)
    assert len(derived["predicted_centers_hz"]) == 17
    assert derived["predicted_amps_per_volt"] == pytest.approx(10.0 ** (10.0 / 20.0) / 70.0)
