from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from lab_workflows.experiment_modules.mx_z_current_coupling_calibration.definition import (
    ADAPTER,
    DEFINITION,
)
from lab_workflows.experiment_modules.mx_z_current_coupling_calibration.models import (
    MxZCurrentCouplingCalibrationParams,
)
import lab_workflows.experiment_modules.mx_z_field_calibration.workflow as scope_workflow
from lab_workflows.experiments.catalog import apply_parameter_layout
from lab_workflows.experiments.registry import list_experiments


def test_scope_defaults_are_based_on_verified_frequency_response() -> None:
    params = ADAPTER.defaults()
    assert params.sense_scope_sample_rate_sa_s == pytest.approx(500000.0)
    assert params.sense_scope_duration_s == pytest.approx(0.02)
    assert params.sense_scope_initial_scale_v_div == pytest.approx(1.0)
    assert params.sense_scope_auto_range_max_attempts == 3

    fields = {item["name"]: item for item in DEFINITION.schema()["fields"]}
    for name in (
        "SENSE_SCOPE_SAMPLE_RATE_SA_S",
        "SENSE_SCOPE_DURATION_S",
        "SENSE_SCOPE_INITIAL_SCALE_V_DIV",
        "SENSE_SCOPE_SCALE_MIN_V_DIV",
        "SENSE_SCOPE_SCALE_MAX_V_DIV",
        "SENSE_SCOPE_AUTO_RANGE_MAX_ATTEMPTS",
    ):
        assert name in fields


def test_scope_parameter_validation_rejects_invalid_range() -> None:
    params = MxZCurrentCouplingCalibrationParams(
        z_prediction_polarity="positive_increases_frequency",
        sense_scope_initial_scale_v_div=0.001,
        sense_scope_scale_min_v_div=0.01,
    )
    errors = params.validate_model()
    assert any("SENSE_SCOPE_INITIAL_SCALE_V_DIV" in item for item in errors)

    params = MxZCurrentCouplingCalibrationParams(
        z_prediction_polarity="positive_increases_frequency",
        sense_scope_auto_range_low_fraction=0.9,
        sense_scope_auto_range_high_fraction=0.4,
    )
    errors = params.validate_model()
    assert any("SENSE_SCOPE_AUTO_RANGE_LOW_FRACTION" in item for item in errors)


def test_scope_configuration_uses_gui_values(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_configure(settings, devices, *, sleep):
        return object(), {
            "actual_initial_scale_v_div": settings.initial_scale_v_div,
            "actual_initial_offset_v": settings.offset_v,
        }

    monkeypatch.setattr(scope_workflow, "configure_fixed_rate_scope", fake_configure)
    params = MxZCurrentCouplingCalibrationParams(
        sense_scope_sample_rate_sa_s=250000.0,
        sense_scope_duration_s=0.05,
        sense_scope_initial_scale_v_div=0.5,
        sense_scope_scale_min_v_div=0.02,
        sense_scope_scale_max_v_div=5.0,
        sense_scope_vertical_divisions=10,
        sense_scope_auto_range_low_fraction=0.3,
        sense_scope_auto_range_high_fraction=0.8,
        sense_scope_auto_offset_tolerance_fraction=0.07,
        sense_scope_auto_range_max_attempts=4,
    )

    _config, settings, state, _snapshot = scope_workflow._configure_sense_scope(
        params, {"scope": object()}
    )

    assert settings.sample_rate_sa_s == pytest.approx(250000.0)
    assert settings.duration_s == pytest.approx(0.05)
    assert settings.initial_scale_v_div == pytest.approx(0.5)
    assert settings.scale_min_v_div == pytest.approx(0.02)
    assert settings.scale_max_v_div == pytest.approx(5.0)
    assert settings.vertical_divisions == 10
    assert settings.auto_range_low_fraction == pytest.approx(0.3)
    assert settings.auto_range_high_fraction == pytest.approx(0.8)
    assert settings.auto_offset_tolerance_fraction == pytest.approx(0.07)
    assert settings.auto_range_max_attempts == 4
    assert state.scale_v_div == pytest.approx(0.5)
    assert state.allow_shrink is False


def test_catalog_layout_covers_all_visible_scope_fields() -> None:
    categories = {definition.id: definition.category for definition in list_experiments()}
    schema = apply_parameter_layout(
        DEFINITION.id,
        DEFINITION.schema(),
        categories,
    )
    names = {item["name"] for item in schema["fields"]}
    layout = schema["parameter_layout"]
    arranged = set(layout["basic"]) | set(layout["advanced"])
    assert names == arranged


def test_calibration_plot_uses_weighted_fit_field_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lab_workflows.experiment_modules.mx_z_current_coupling_calibration.analysis import (
        _plot_calibration,
    )

    saved: list[str] = []
    monkeypatch.setattr(
        "lab_workflows.experiment_modules.mx_z_current_coupling_calibration.analysis.save_figure",
        lambda figure, path: saved.append(str(path)),
    )
    curves = [
        {
            "current_a": 0.001,
            "fit": {
                "success": True,
                "parameters": (0.0, 1.0, 100.0, 0.0),
                "center_uncertainty_hz": 1.0,
            },
        },
        {
            "current_a": 0.002,
            "fit": {
                "success": True,
                "parameters": (0.0, 1.0, 110.0, 0.0),
                "center_uncertainty_hz": 1.0,
            },
        },
    ]
    filename = _plot_calibration(
        Path("results"),
        curves,
        {
            "success": True,
            "slope_hz_per_v": 10000.0,
            "intercept_hz": 90.0,
            "r_squared": 0.99,
        },
    )
    assert filename == "current_coupling_calibration.png"
    assert len(saved) == 1
    assert Path(saved[0]).name == "current_coupling_calibration.png"


def test_plot_entry_passes_explicit_run_dir_to_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    import experiments.Mx_Z_Current_Coupling_Calibration_plot as plot_entry

    run_dir = Path("data/Mx_Z_Current_Coupling_Calibration/example")
    monkeypatch.setattr(plot_entry, "ADAPTER", type("Adapter", (), {
        "analyze_cli": staticmethod(lambda selected: selected),
    })())
    monkeypatch.setattr(
        "sys.argv",
        ["Mx_Z_Current_Coupling_Calibration_plot.py", str(run_dir)],
    )

    assert plot_entry.main() == run_dir
