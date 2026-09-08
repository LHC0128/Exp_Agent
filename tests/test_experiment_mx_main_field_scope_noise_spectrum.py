"""Mx 主磁场示波器噪声谱实验契约测试。"""

from __future__ import annotations

from pathlib import Path
import shutil
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import numpy as np
import pytest
import yaml

from lab_workflows.analysis.noise_spectrum_separation import (
    NoiseSeparationResult,
    fit_noise_separation,
    lorentzian_vs_control,
)
from lab_workflows.common import WorkflowCancelled
from lab_workflows.experiment_modules.mx_main_field_scope_noise_spectrum.analysis import (
    analyze,
)
from lab_workflows.experiment_modules.mx_main_field_scope_noise_spectrum.definition import (
    DEFINITION,
)
from lab_workflows.experiment_modules.mx_main_field_scope_noise_spectrum.global_analysis import (
    assess_global_noise_accuracy,
    fit_global_noise_separation,
    lorentzian_response_matrix,
    project_global_noise_separation,
)
from lab_workflows.experiment_modules.mx_main_field_scope_noise_spectrum.models import (
    MxMainFieldScopeNoiseSpectrumParams,
)
from lab_workflows.experiment_modules.mx_main_field_scope_noise_spectrum.scan import (
    build_scan_axes,
)
from lab_workflows.experiment_modules.mx_main_field_scope_noise_spectrum.workflow import (
    _AutoRangeState,
    _acquire_scan,
    _acquire_scope_point,
    _configure_outputs,
    _configure_scope,
    _next_auto_offset,
    _next_auto_range_scale,
    _read_scope_record,
    _temperature_gated_acquire,
    run,
    safe_shutdown,
)
from lab_workflows.experiments.registry import get_experiment
from sds_acquisition import AcquisitionConfig, SDSAcquisition, SDSInstrument


@pytest.fixture
def local_tmp_path():
    root = Path(__file__).resolve().parents[1] / "data"
    path = root / f".test_mx_main_field_scope_noise_{uuid4().hex}"
    path.mkdir(mode=0o777)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def test_defaults_schema_registry_and_catalog_contract() -> None:
    params = MxMainFieldScopeNoiseSpectrumParams()
    assert params.validate() == []
    assert get_experiment("mx-main-field-scope-noise-spectrum") is DEFINITION
    assert DEFINITION.execution_mode == "typed_workflow"
    assert DEFINITION.data_type == "Mx_Main_Field_Scope_Noise_Spectrum"
    assert "SDS" in DEFINITION.required_devices
    assert "HF2" not in DEFINITION.required_devices

    fields = {item["name"]: item for item in DEFINITION.schema()["fields"]}
    assert fields["CONTROL_FREQUENCY_POINTS"]["default"] == 201
    assert fields["SCOPE_SAMPLE_RATE"]["default"] == 500000.0
    assert fields["SCOPE_DURATION"]["default"] == 2.0
    assert fields["SCOPE_AC_COUPLING"]["type"] == "boolean"
    assert fields["SCOPE_AC_COUPLING"]["default"] is True
    assert fields["FIXED_PARAMS.X_magnetic_field"]["default"] == pytest.approx(-0.01)
    assert fields["FIXED_PARAMS.Y_magnetic_field"]["default"] == pytest.approx(0.008)
    assert fields["FIT_EXAMPLE_PLOT_COUNT"]["default"] == 10
    assert fields["FIT_HALF_WIDTH_HZ"]["default"] == 5000.0
    assert fields["GLOBAL_FIT_FREQUENCY_MIN_HZ"]["default"] == 500.0
    assert fields["GLOBAL_CORE_FIT_FREQUENCY_MIN_HZ"]["default"] == 2000.0
    assert "SCOPE_PD_CHANNEL" not in fields

    root = Path(__file__).resolve().parents[1]
    defaults = MxMainFieldScopeNoiseSpectrumParams.from_yaml(
        root / "params" / "experiments" / "mx-main-field-scope-noise-spectrum.yaml"
    )
    assert defaults.validate() == []
    assert defaults.scope_ac_coupling is True
    assert defaults.x_dc_field_v == pytest.approx(-0.01)
    assert defaults.y_dc_field_v == pytest.approx(0.008)
    assert defaults.scope_initial_scale_v_div == pytest.approx(0.1)
    assert defaults.welch_nperseg == 20000
    assert defaults.fit_half_width_hz == pytest.approx(5000.0)
    assert fields["SCOPE_INITIAL_SCALE"]["default"] == pytest.approx(0.1)
    assert fields["WELCH_NPERSEG"]["default"] == 20000
    with (root / "params" / "experiment_catalog.yaml").open(
        encoding="utf-8"
    ) as stream:
        catalog = yaml.safe_load(stream)
    layout = catalog["parameter_layouts"]["mx-main-field-scope-noise-spectrum"]
    arranged = [*layout["basic"], *layout["advanced"]]
    assert len(arranged) == len(set(arranged))
    assert set(arranged) == set(fields)


def test_scan_axis_uses_extrapolated_calibration_and_exact_endpoints() -> None:
    params = MxMainFieldScopeNoiseSpectrumParams()
    axes = build_scan_axes(params)
    control = axes["control_frequency_hz"]
    larmor = axes["larmor_frequency_hz"]
    current = axes["main_field_current_ma"]
    assert control.size == 500
    assert control[[0, -1]].tolist() == [0.0, 50000.0]
    np.testing.assert_allclose(larmor, control, rtol=0.0, atol=0.0)
    assert current[0] == pytest.approx(-0.020332718343180085)
    assert current[-1] == pytest.approx(5.149273044483401)
    assert np.all(np.diff(control) > 0.0)
    assert np.all(np.diff(current) > 0.0)
    rebuilt = (
        params.main_field_calibration_hz_per_ma * current
        + params.main_field_calibration_intercept_hz
    )
    np.testing.assert_allclose(rebuilt, control, rtol=0.0, atol=1e-9)


def test_model_rejects_nyquist_samples_ranges_and_global_safety() -> None:
    assert any(
        "Nyquist" in item
        for item in MxMainFieldScopeNoiseSpectrumParams(
            scope_sample_rate_sa_s=100000.0
        ).validate_model()
    )
    assert any(
        "WELCH_NPERSEG" in item
        for item in MxMainFieldScopeNoiseSpectrumParams(
            scope_sample_rate_sa_s=200000.0,
            scope_duration_s=0.01,
            welch_nperseg=10000,
        ).validate_model()
    )
    assert any(
        "LOW" in item
        for item in MxMainFieldScopeNoiseSpectrumParams(
            scope_auto_range_low_fraction=0.95
        ).validate_model()
    )
    assert any(
        "TOLERANCE" in item
        for item in MxMainFieldScopeNoiseSpectrumParams(
            scope_auto_offset_tolerance_fraction=1.0
        ).validate_model()
    )
    assert any(
        "FIT_HALF_WIDTH_HZ" in item
        for item in MxMainFieldScopeNoiseSpectrumParams(
            control_frequency_points=100,
            fit_half_width_hz=1000.0,
        ).validate_model()
    )
    assert any(
        "安全拦截" in item
        for item in MxMainFieldScopeNoiseSpectrumParams(
            control_frequency_stop_hz=100000.0,
            scope_sample_rate_sa_s=300000.0,
        ).validate_model()
    )
    assert any(
        "X_magnetic_field" in item
        for item in MxMainFieldScopeNoiseSpectrumParams(
            x_dc_field_v=-10.1
        ).validate()
    )
    assert any(
        "Y_magnetic_field" in item
        for item in MxMainFieldScopeNoiseSpectrumParams(
            y_dc_field_v=10.1
        ).validate()
    )


def test_centered_fit_window_excludes_low_control_frequency_noise() -> None:
    control_axis = np.linspace(500.0, 50000.0, 200)
    frequency_axis = np.asarray([11125.0])
    psd = lorentzian_vs_control(
        control_axis,
        300.0,
        1.0,
        1.0e-6,
        0.0,
        frequency_axis[0],
    )
    psd[control_axis < 5000.0] += 1.0e-8 * (5000.0 / control_axis[control_axis < 5000.0]) ** 2
    result = fit_noise_separation(
        psd[:, None],
        frequency_axis,
        control_axis,
        peak_margin_hz=10000.0,
        fit_half_width_hz=5000.0,
        gamma_guess_hz=300.0,
    )
    assert result.fit_mask.tolist() == [True]
    assert result.parameters[0, 0] == pytest.approx(300.0, rel=1e-4)
    assert result.parameters[0, 2] == pytest.approx(1.0e-6, rel=1e-4)


def test_global_2d_separation_recovers_rank_one_background_and_noise_spectra() -> None:
    control_hz = np.linspace(500.0, 20000.0, 60)
    frequency_hz = np.linspace(3000.0, 17000.0, 50)
    background = 1.0 + 5.0 / (1.0 + (control_hz / 3000.0) ** 2)
    background /= np.median(
        background[control_hz >= np.quantile(control_hz, 0.6)]
    )
    n_s1 = 2.0e-10 * (1.0 + 0.1 * np.sin(frequency_hz / 3000.0))
    c_s_beta = 1.0e-3 * (1.0 + 0.2 * np.cos(frequency_hz / 4000.0))
    response = lorentzian_response_matrix(
        control_hz,
        frequency_hz,
        gamma_hz=700.0,
        control_offset_hz=-120.0,
    )
    psd = background[:, None] * n_s1[None, :] + response * c_s_beta[None, :]
    rng = np.random.default_rng(20260721)
    psd *= rng.lognormal(mean=-0.5 * 0.03**2, sigma=0.03, size=psd.shape)

    result = fit_global_noise_separation(
        psd,
        frequency_hz,
        control_hz,
        gamma_guess_hz=600.0,
        background_margin_hz=3000.0,
        cv_folds=5,
    )

    assert result.gamma_hz == pytest.approx(700.0, rel=0.02)
    assert result.control_offset_hz == pytest.approx(-120.0, abs=20.0)
    assert np.median(np.abs(result.background_profile / background - 1.0)) < 0.03
    assert np.median(np.abs(result.n_s1 / n_s1 - 1.0)) < 0.05
    assert np.median(np.abs(result.c_s_beta / c_s_beta - 1.0)) < 0.06
    assert result.log_r_squared > 0.99
    assert np.count_nonzero(result.quantitative_valid_mask) >= 45


def test_global_2d_core_fit_projects_low_frequency_diagnostics() -> None:
    control_hz = np.linspace(500.0, 20000.0, 60)
    frequency_hz = np.arange(500.0, 17000.1, 250.0)
    background = 1.0 + 2.0 / (1.0 + (control_hz / 3000.0) ** 2)
    background /= np.median(
        background[control_hz >= np.quantile(control_hz, 0.6)]
    )
    n_s1 = 2.0e-10 * (1.0 + 0.1 * np.sin(frequency_hz / 3000.0))
    c_s_beta = 1.0e-3 * (1.0 + 0.2 * np.cos(frequency_hz / 4000.0))
    response = lorentzian_response_matrix(
        control_hz,
        frequency_hz,
        gamma_hz=700.0,
        control_offset_hz=-120.0,
    )
    psd = background[:, None] * n_s1[None, :] + response * c_s_beta[None, :]
    rng = np.random.default_rng(20260722)
    psd *= rng.lognormal(mean=-0.5 * 0.02**2, sigma=0.02, size=psd.shape)
    core = frequency_hz >= 2000.0

    core_result = fit_global_noise_separation(
        psd[:, core],
        frequency_hz[core],
        control_hz,
        gamma_guess_hz=600.0,
        background_margin_hz=3000.0,
        cv_folds=5,
    )
    projected = project_global_noise_separation(
        psd,
        frequency_hz,
        control_hz,
        core_result,
        cv_folds=5,
    )
    accuracy = assess_global_noise_accuracy(
        psd[:, core],
        frequency_hz[core],
        control_hz,
        core_result,
        background_margin_hz=3000.0,
        cv_folds=5,
        projection_psd_matrix=psd,
        projection_frequency_hz=frequency_hz,
        projection_result=projected,
    )

    assert core_result.gamma_hz == pytest.approx(700.0, rel=0.03)
    assert projected.frequency_hz[0] == pytest.approx(500.0)
    assert projected.gamma_hz == pytest.approx(core_result.gamma_hz)
    assert accuracy.c_s_beta_total_relative_uncertainty.shape == frequency_hz.shape
    for target_hz in (500.0, 1000.0):
        index = int(np.argmin(np.abs(frequency_hz - target_hz)))
        assert not accuracy.quantitative_valid_mask[index]
    index_2000 = int(np.argmin(np.abs(frequency_hz - 2000.0)))
    assert accuracy.quantitative_valid_mask[index_2000]


def test_psd_map_limits_and_colors_match_control_frequency_range(
    monkeypatch: pytest.MonkeyPatch,
    local_tmp_path,
) -> None:
    import lab_workflows.experiment_modules.mx_main_field_scope_noise_spectrum.analysis as analysis

    captured: dict[str, tuple[float, float]] = {}

    def capture_figure(figure, path) -> None:
        captured["xlim"] = figure.axes[0].get_xlim()
        captured["ylim"] = figure.axes[0].get_ylim()
        captured["clim"] = figure.axes[0].collections[0].get_clim()

    monkeypatch.setattr(analysis, "save_figure", capture_figure)
    monkeypatch.setattr(analysis, "style_legend", lambda axis: None)
    frequency_hz = np.linspace(0.0, 100000.0, 101)
    control_hz = np.linspace(10000.0, 50000.0, 100)
    psd = np.logspace(-11.0, -9.0, control_hz.size * frequency_hz.size).reshape(
        control_hz.size, frequency_hz.size
    )
    psd[0, 1] = 1.0e-3
    analysis._plot_psd_matrix(
        local_tmp_path,
        frequency_hz,
        control_hz,
        psd,
        fit_max_hz=45000.0,
    )

    assert captured["xlim"] == pytest.approx((10.0, 50.0))
    assert captured["ylim"] == pytest.approx((10.0, 50.0))
    displayed_log_psd = np.log10(
        psd[
            :,
            (frequency_hz >= control_hz[0])
            & (frequency_hz <= control_hz[-1]),
        ]
    )
    expected_clim = np.percentile(displayed_log_psd, [1.0, 99.5])
    assert captured["clim"] == pytest.approx(tuple(expected_clim))
    assert captured["clim"][1] < np.log10(psd[0, 1])


def test_fit_examples_only_select_frequencies_inside_control_range(
    monkeypatch: pytest.MonkeyPatch,
    local_tmp_path,
) -> None:
    import lab_workflows.experiment_modules.mx_main_field_scope_noise_spectrum.analysis as analysis

    selected_frequency_hz: list[float] = []

    def capture_figure(figure, path) -> None:
        return None

    def capture_legend(axis, *, title: str, **kwargs) -> None:
        selected_frequency_hz.append(float(title.split(": ", 1)[1].split()[0]) * 1000.0)

    monkeypatch.setattr(analysis, "save_figure", capture_figure)
    monkeypatch.setattr(analysis, "style_legend", capture_legend)
    control_hz = np.linspace(10000.0, 20000.0, 101)
    frequency_hz = np.linspace(1000.0, 25000.0, 49)
    psd = np.full((control_hz.size, frequency_hz.size), 1.0e-9)
    parameters = np.column_stack(
        [
            np.full(frequency_hz.size, 300.0),
            np.full(frequency_hz.size, 1.0e-3),
            np.full(frequency_hz.size, 1.0e-9),
            np.zeros(frequency_hz.size),
        ]
    )
    fit = NoiseSeparationResult(
        parameters=parameters,
        uncertainties=np.ones_like(parameters),
        fit_mask=np.ones(frequency_hz.size, dtype=bool),
        interpolated_mask=np.zeros(frequency_hz.size, dtype=bool),
        s_beta=parameters[:, 1],
        n_s1=parameters[:, 2],
    )

    files = analysis._plot_fit_examples(
        local_tmp_path,
        control_hz,
        frequency_hz,
        psd,
        fit,
        count=10,
        fit_half_width_hz=5000.0,
    )

    assert len(files) == 10
    assert min(selected_frequency_hz) >= control_hz[0]
    assert max(selected_frequency_hz) <= control_hz[-1]


def test_global_fit_examples_select_frequencies_inside_control_range(
    monkeypatch: pytest.MonkeyPatch,
    local_tmp_path,
) -> None:
    import lab_workflows.experiment_modules.mx_main_field_scope_noise_spectrum.analysis as analysis

    captured: dict[str, list[str]] = {}

    def capture_figure(figure, path) -> None:
        captured["titles"] = [axis.get_title() for axis in figure.axes]

    monkeypatch.setattr(analysis, "save_figure", capture_figure)
    monkeypatch.setattr(analysis, "style_legend", lambda axis: None)
    control_hz = np.linspace(10000.0, 20000.0, 21)
    frequency_hz = np.linspace(500.0, 25000.0, 50)
    matrix_shape = (control_hz.size, frequency_hz.size)
    fit = SimpleNamespace(
        frequency_hz=frequency_hz,
        control_frequency_hz=control_hz,
        background_profile=np.ones(control_hz.size),
        n_s1=np.full(frequency_hz.size, 1.0e-9),
        c_s_beta=np.full(frequency_hz.size, 1.0e-3),
        response_matrix=np.ones(matrix_shape),
        model_matrix=np.full(matrix_shape, 2.0e-9),
    )

    analysis._plot_global_2d_examples(
        local_tmp_path,
        np.full(matrix_shape, 2.0e-9),
        fit,
    )

    selected_frequency_hz = [
        float(title.split(": ", 1)[1].split()[0]) * 1000.0
        for title in captured["titles"]
    ]
    assert len(selected_frequency_hz) == 4
    assert min(selected_frequency_hz) >= control_hz[0]
    assert max(selected_frequency_hz) <= control_hz[-1]


def test_global_noise_spectra_use_linear_frequency_axis_from_zero(
    monkeypatch: pytest.MonkeyPatch,
    local_tmp_path,
) -> None:
    import lab_workflows.experiment_modules.mx_main_field_scope_noise_spectrum.analysis as analysis

    captured: dict[str, object] = {}

    def capture_figure(figure, path) -> None:
        captured["xscales"] = [axis.get_xscale() for axis in figure.axes]
        captured["xlims"] = [axis.get_xlim() for axis in figure.axes]

    monkeypatch.setattr(analysis, "save_figure", capture_figure)
    monkeypatch.setattr(analysis, "style_legend", lambda axis: None)
    frequency_hz = np.asarray([5000.0, 10000.0, 45000.0])
    fit = SimpleNamespace(
        frequency_hz=frequency_hz,
        c_s_beta=np.asarray([1.0e-3, 1.1e-3, 1.2e-3]),
        n_s1=np.asarray([1.0e-10, 1.1e-10, 1.2e-10]),
    )
    accuracy = SimpleNamespace(
        quantitative_valid_mask=np.asarray([True, False, True]),
        c_s_beta_total_relative_uncertainty=np.full(3, 0.1),
        n_s1_total_relative_uncertainty=np.full(3, 0.02),
    )

    analysis._plot_global_2d_spectra(local_tmp_path, fit, accuracy)

    assert captured["xscales"] == ["linear", "linear"]
    for xlim in captured["xlims"]:
        assert xlim == pytest.approx((0.0, 45000.0))


def test_auto_range_rule_matches_t2_thresholds() -> None:
    params = MxMainFieldScopeNoiseSpectrumParams()
    assert _next_auto_range_scale(params, 0.5, 0.79) == 0.25
    assert _next_auto_range_scale(params, 0.5, 1.81) == 1.0
    assert _next_auto_range_scale(params, 0.5, 1.0) == 0.5
    assert _next_auto_range_scale(params, 0.02, 0.0) == 0.02
    assert _next_auto_range_scale(params, 10.0, 100.0) == 10.0


def test_auto_offset_uses_negative_midpoint_and_deadband() -> None:
    params = MxMainFieldScopeNoiseSpectrumParams(
        scope_auto_offset_tolerance_fraction=0.05
    )
    assert _next_auto_offset(params, 0.5, 0.0, 0.8, 1.2) == pytest.approx(-1.0)
    assert _next_auto_offset(params, 0.5, -1.0, 0.85, 1.05) == pytest.approx(
        -1.0
    )


def test_configure_outputs_applies_fixed_xy_dc_zero_rule_and_negative_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import lab_workflows.experiment_modules.mx_main_field_scope_noise_spectrum.workflow as workflow

    monkeypatch.setattr(workflow, "synchronize_connected_clocks", lambda *a, **k: {})
    monkeypatch.setattr(workflow, "wait_for_temperature_stable", lambda *a, **k: 120.0)
    monkeypatch.setattr(
        workflow,
        "load_safety_limits",
        lambda: {"main_magnetic_field": {"min": -10.0, "max": 10.0}},
    )
    devices = {
        name: MagicMock()
        for name in (
            "z_field",
            "xy_field",
            "laser",
            "gs200",
            "pump_rf",
            "temp_switch",
            "tec",
        )
    }
    channels = {
        "z_field": 1,
        "x_field": 1,
        "y_rf": 2,
        "pump_laser": 1,
        "probe_laser": 2,
        "pump_carrier": 1,
        "pump_gate": 2,
        "temp_switch": 2,
    }
    params = MxMainFieldScopeNoiseSpectrumParams(
        x_dc_field_v=-0.015,
        y_dc_field_v=0.0,
    )
    _configure_outputs(
        params,
        devices,
        channels,
        {"main_magnetic_field": {"source_function": "CURRent"}},
    )
    devices["z_field"].setup_dc.assert_called_once_with(0.0, channel=1)
    devices["z_field"].set_output.assert_called_once_with(False, channel=1)
    devices["xy_field"].setup_dc.assert_any_call(-0.015, channel=1)
    devices["xy_field"].setup_dc.assert_any_call(0.0, channel=2)
    devices["xy_field"].set_output.assert_any_call(True, channel=1)
    devices["xy_field"].set_output.assert_any_call(False, channel=2)
    devices["gs200"].set_current.assert_called_once_with(
        pytest.approx(params.current_for_control_frequency(0.0) / 1000.0)
    )


def test_configure_scope_uses_ch1_auto_and_checks_real_rate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import lab_workflows.experiment_modules.mx_main_field_scope_noise_spectrum.workflow as workflow

    class Acquirer:
        def __init__(self) -> None:
            self.config = None

        def apply_config(self, config) -> None:
            self.config = config

    scope = MagicMock()
    scope.get_sampling_rate.return_value = 199996.0
    scope.get_actual_points.return_value = 200000
    scope.get_memory_management.return_value = "FSRate"
    scope.get_memory_depth.return_value = "200K"
    scope.get_channel_scale.return_value = 0.5
    scope.get_channel_offset.return_value = 0.0
    acquirer = Acquirer()
    monkeypatch.setattr(workflow, "_sleep", lambda seconds: None)
    config, snapshot = _configure_scope(
        MxMainFieldScopeNoiseSpectrumParams(),
        {"scope": scope, "acquirer": acquirer},
    )
    assert acquirer.config is config
    assert config.memory_management == "FSRate"
    assert config.trigger.mode == "AUTO"
    assert config.trigger.source == "C1"
    assert [item.enabled for item in config.channels] == [True, False, False, False]
    assert config.channels[0].coupling == "DC"
    assert snapshot["coupling"] == "DC"
    assert snapshot["actual_sample_rate_sa_s"] == 199996.0
    assert snapshot["actual_points"] == 200000
    assert snapshot["actual_memory_management"] == "FSRate"
    assert snapshot["actual_initial_scale_v_div"] == pytest.approx(0.5)
    assert snapshot["actual_initial_offset_v"] == pytest.approx(0.0)

    ac_config, ac_snapshot = _configure_scope(
        MxMainFieldScopeNoiseSpectrumParams(scope_ac_coupling=True),
        {"scope": scope, "acquirer": acquirer},
    )
    assert ac_config.channels[0].coupling == "AC"
    assert ac_snapshot["coupling"] == "AC"


def test_sds_memory_management_scpi_and_apply_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instrument = SDSInstrument("TEST::SCOPE")
    instrument.write = MagicMock()
    instrument.query = MagicMock(return_value="FSRate")
    instrument.set_memory_management("fsrate")
    instrument.write.assert_called_once_with(":ACQuire:MMANagement FSRate")
    assert instrument.get_memory_management() == "FSRate"
    instrument.query.assert_called_once_with(":ACQuire:MMANagement?")
    with pytest.raises(ValueError, match="存储管理模式"):
        instrument.set_memory_management("invalid")

    scope = MagicMock()
    monkeypatch.setattr("sds_acquisition.acquire.time.sleep", lambda seconds: None)
    SDSAcquisition(scope).apply_config(
        AcquisitionConfig(memory_management="FSRate")
    )
    calls = [call[0] for call in scope.method_calls]
    assert calls.index("set_memory_management") < calls.index("set_sampling_rate")


def test_sds_binary_block_parser_handles_prefixed_zero_length_response() -> None:
    assert SDSInstrument._strip_binary_header(b"#14data\n") == b"data"
    assert (
        SDSInstrument._strip_binary_header(b"C1:WF DAT2,#9000000000\n\n")
        == b""
    )


def test_temperature_gate_restores_and_waits_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import lab_workflows.experiment_modules.mx_main_field_scope_noise_spectrum.workflow as workflow

    events: list[tuple] = []
    monkeypatch.setattr(workflow, "check_cancelled", lambda: None)
    monkeypatch.setattr(
        workflow,
        "set_temperature_switch",
        lambda device, enabled, **kwargs: events.append(
            (
                "temperature",
                enabled,
                kwargs["channel"],
                kwargs.get("settle_time", 0.0),
                "cancellation" in kwargs,
            )
        ),
    )
    monkeypatch.setattr(
        workflow, "_sleep", lambda seconds: events.append(("sleep", seconds))
    )
    with pytest.raises(RuntimeError, match="scope failed"):
        _temperature_gated_acquire(
            MxMainFieldScopeNoiseSpectrumParams(),
            {"temp_switch": object()},
            {"temp_switch": 2},
            acquire=lambda: (_ for _ in ()).throw(RuntimeError("scope failed")),
        )
    assert events == [
        ("temperature", False, 2, 0.0, False),
        ("sleep", 0.3),
        ("temperature", True, 2, 1.0, True),
    ]


def test_safe_shutdown_restores_dc_even_when_scope_stop_fails() -> None:
    scope = MagicMock()
    scope.trigger_stop.side_effect = RuntimeError("stop failed")

    report = safe_shutdown(
        {"scope": scope},
        {},
        MxMainFieldScopeNoiseSpectrumParams(scope_pd_channel=3),
    )

    assert any("停止示波器触发失败" in error for error in report.action_errors)
    scope.set_channel_coupling.assert_called_once_with(3, "DC")


def test_scope_record_uses_force_trigger_and_retries_short_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import lab_workflows.experiment_modules.mx_main_field_scope_noise_spectrum.workflow as workflow

    events: list[tuple] = []
    params = MxMainFieldScopeNoiseSpectrumParams(welch_nperseg=20000)
    config = AcquisitionConfig(
        sampling_rate=200000.0,
        sampling_time=1.0,
        acquire_delay=1.0,
    )

    class Scope:
        def __init__(self) -> None:
            self.stopped = True
            self.status_index = 0
            self.force_mode_reads = 0

        def trigger_run(self) -> None:
            self.stopped = False
            self.status_index = 0
            events.append(("run",))

        def set_trigger_mode(self, mode: str) -> None:
            events.append(("mode", mode))

        def get_trigger_mode(self) -> str:
            events.append(("get_mode",))
            self.force_mode_reads += 1
            return "FTRIG" if self.force_mode_reads % 2 == 1 else "AUTO"

        def trigger_stop(self) -> None:
            self.stopped = True
            events.append(("stop",))

        def trigger_status(self) -> str:
            events.append(("status",))
            if self.stopped:
                return "Stop"
            self.status_index += 1
            return "Auto" if self.status_index == 1 else "Arm"

    short_time = np.arange(12, dtype=float) / 200000.0
    full_time = np.arange(200001, dtype=float) / 200000.0
    records = iter(
        [
            SimpleNamespace(
                time=short_time,
                voltage=np.zeros(short_time.size),
                preamble_dict={"point_num": 200000},
            ),
            SimpleNamespace(
                time=full_time,
                voltage=np.zeros(full_time.size),
                preamble_dict={"point_num": 200000},
            ),
        ]
    )

    class Acquirer:
        def acquire_channel(self, *args, **kwargs):
            events.append(("read",))
            return next(records)

    monkeypatch.setattr(workflow, "check_cancelled", lambda: None)
    monkeypatch.setattr(
        workflow, "_sleep", lambda seconds: events.append(("wait", seconds))
    )
    result = _read_scope_record(
        params,
        {"scope": Scope(), "acquirer": Acquirer()},
        config,
    )
    assert result.voltage.size == 200001
    assert events == [
        ("run",),
        ("mode", "AUTO"),
        ("mode", "FTRIG"),
        ("get_mode",),
        ("wait", 0.05),
        ("get_mode",),
        ("wait", 0.2),
        ("stop",),
        ("status",),
        ("read",),
        ("run",),
        ("mode", "AUTO"),
        ("mode", "FTRIG"),
        ("get_mode",),
        ("wait", 0.05),
        ("get_mode",),
        ("wait", 0.2),
        ("stop",),
        ("status",),
        ("read",),
    ]


def test_scope_record_soft_resets_once_after_zero_length_waveform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import lab_workflows.experiment_modules.mx_main_field_scope_noise_spectrum.workflow as workflow

    params = MxMainFieldScopeNoiseSpectrumParams(welch_nperseg=20000)
    config = AcquisitionConfig(
        sampling_rate=200000.0,
        sampling_time=1.0,
        acquire_delay=1.0,
    )

    class Scope:
        def __init__(self) -> None:
            self.stopped = True
            self.reset_count = 0
            self.scale = 0.4
            self.offset = -0.1

        def trigger_run(self) -> None:
            self.stopped = False

        def set_trigger_mode(self, mode: str) -> None:
            assert mode in {"AUTO", "FTRIG"}

        def get_trigger_mode(self) -> str:
            return "AUTO"

        def trigger_stop(self) -> None:
            self.stopped = True

        def trigger_status(self) -> str:
            return "Stop" if self.stopped else "Auto"

        def get_channel_scale(self, channel: int) -> float:
            assert channel == 1
            return self.scale

        def get_channel_offset(self, channel: int) -> float:
            assert channel == 1
            return self.offset

        def set_channel_scale(self, channel: int, scale: float) -> None:
            assert channel == 1
            self.scale = scale

        def set_channel_offset(self, channel: int, offset: float) -> None:
            assert channel == 1
            self.offset = offset

        def reset(self) -> None:
            self.reset_count += 1

    zero_record = SimpleNamespace(
        time=np.asarray([], dtype=float),
        voltage=np.asarray([], dtype=float),
        preamble_dict={"point_num": 200000},
    )
    full_time = np.arange(200001, dtype=float) / 200000.0
    full_record = SimpleNamespace(
        time=full_time,
        voltage=np.zeros(full_time.size),
        preamble_dict={"point_num": 200000},
    )

    class Acquirer:
        def __init__(self) -> None:
            self.records = iter([zero_record, full_record])
            self.applied_configs: list[AcquisitionConfig] = []

        def acquire_channel(self, *args, **kwargs):
            return next(self.records)

        def apply_config(self, applied_config: AcquisitionConfig) -> None:
            self.applied_configs.append(applied_config)

    scope = Scope()
    acquirer = Acquirer()
    sleeps: list[float] = []
    monkeypatch.setattr(workflow, "check_cancelled", lambda: None)
    monkeypatch.setattr(workflow, "_sleep", sleeps.append)

    result = _read_scope_record(
        params,
        {"scope": scope, "acquirer": acquirer},
        config,
    )

    assert result is full_record
    assert scope.reset_count == 1
    assert acquirer.applied_configs == [config]
    assert scope.scale == pytest.approx(0.4)
    assert scope.offset == pytest.approx(-0.1)
    assert 4.0 in sleeps


def test_sds_channel_read_resets_transfer_start_before_preamble() -> None:
    preamble = bytearray(346)
    preamble[0x3C:0x40] = (4).to_bytes(4, byteorder="little", signed=True)
    preamble[0x74:0x78] = (4).to_bytes(4, byteorder="little", signed=True)
    preamble[0x9C:0xA0] = np.float32(1.0).tobytes()
    preamble[0xA0:0xA4] = np.float32(0.0).tobytes()
    preamble[0xA4:0xA8] = np.float32(1.0).tobytes()
    preamble[0xAC:0xAE] = (8).to_bytes(2, byteorder="little", signed=True)
    preamble[0xB0:0xB4] = np.float32(1e-6).tobytes()
    preamble[0xB4:0xBC] = np.float64(0.0).tobytes()
    preamble[0x144:0x146] = (0).to_bytes(2, byteorder="little", signed=True)
    preamble[0x148:0x14C] = np.float32(1.0).tobytes()

    instrument = MagicMock()
    instrument.get_waveform_preamble.return_value = bytes(preamble)
    instrument.get_waveform_max_points.return_value = 4
    instrument.get_waveform_data.return_value = bytes([0, 1, 2, 3])

    result = SDSAcquisition(instrument).acquire_channel(
        1,
        timebase_scale=4e-6,
    )

    assert result.voltage.size == 4
    calls = [call[0] for call in instrument.method_calls]
    assert calls.index("set_waveform_start") < calls.index("set_waveform_source")
    assert calls.index("set_waveform_source") < calls.index(
        "get_waveform_preamble"
    )


def test_scope_point_retries_three_times_and_uses_real_time_axis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import lab_workflows.experiment_modules.mx_main_field_scope_noise_spectrum.workflow as workflow

    params = MxMainFieldScopeNoiseSpectrumParams(
        control_frequency_stop_hz=40000.0,
        welch_nperseg=10000,
    )
    time_s = np.arange(12000, dtype=float) / 200000.0
    waveforms = iter(
        [
            np.resize(np.asarray([0.20, 0.40]), time_s.size),
            np.resize(np.asarray([0.20, 0.40]), time_s.size),
            np.resize(np.asarray([0.0, 0.50]), time_s.size),
        ]
    )

    class Scope:
        def __init__(self) -> None:
            self.scales: list[float] = []
            self.offsets: list[float] = []
            self.offset = 0.0

        def set_channel_scale(self, channel: int, scale: float) -> None:
            assert channel == 1
            self.scales.append(scale)

        def get_channel_scale(self, channel: int) -> float:
            assert channel == 1
            return self.scales[-1]

        def set_channel_offset(self, channel: int, offset: float) -> None:
            assert channel == 1
            self.offset = offset
            self.offsets.append(offset)

        def get_channel_offset(self, channel: int) -> float:
            assert channel == 1
            return self.offset

    scope = Scope()
    monkeypatch.setattr(workflow, "check_cancelled", lambda: None)
    monkeypatch.setattr(
        workflow,
        "_temperature_gated_acquire",
        lambda params, devices, channels, *, acquire: acquire(),
    )
    monkeypatch.setattr(
        workflow,
        "_read_scope_record",
        lambda *args: SimpleNamespace(time=time_s, voltage=next(waveforms)),
    )
    auto_range = _AutoRangeState(scale_v_div=0.5)
    payload = _acquire_scope_point(
        params,
        {"scope": scope, "acquirer": object(), "temp_switch": object()},
        {"temp_switch": 2},
        AcquisitionConfig(),
        auto_range,
    )
    np.testing.assert_allclose(payload["attempt_scales_v_div"], [0.5, 0.25, 0.125])
    assert payload["scale_used_v_div"] == pytest.approx(0.125)
    assert payload["actual_rate_sa_s"] == pytest.approx(200000.0)
    assert auto_range.scale_v_div == pytest.approx(0.125)
    assert scope.scales == [0.25, 0.125]
    np.testing.assert_allclose(payload["attempt_offsets_v"], [0.0, -0.3, -0.3])
    assert payload["attempt_count"] == 3
    assert payload["offset_used_v"] == pytest.approx(-0.3)
    assert auto_range.offset_v == pytest.approx(-0.25)
    np.testing.assert_allclose(scope.offsets, [-0.3, -0.3, -0.25])


def test_v1_four_single_side_divisions_migrate_to_eight_total() -> None:
    params = MxMainFieldScopeNoiseSpectrumParams.from_external(
        {"SCOPE_VERTICAL_DIVISIONS": 4}, schema_version=1
    )
    assert params.scope_vertical_divisions == 8
    assert params.scope_ac_coupling is False
    assert params.x_dc_field_v == 0.0
    assert params.y_dc_field_v == 0.0


def test_v5_migration_preserves_disabled_xy_dc_outputs() -> None:
    params = MxMainFieldScopeNoiseSpectrumParams.from_external(
        {}, schema_version=5
    )
    assert params.x_dc_field_v == 0.0
    assert params.y_dc_field_v == 0.0


def test_v6_migration_enables_two_stage_low_frequency_projection() -> None:
    params = MxMainFieldScopeNoiseSpectrumParams.from_external(
        {"GLOBAL_FIT_FREQUENCY_MIN_HZ": 5000.0}, schema_version=6
    )
    assert params.global_fit_frequency_min_hz == pytest.approx(500.0)
    assert params.global_core_fit_frequency_min_hz == pytest.approx(2000.0)


def test_scope_point_recenters_before_shrinking_edge_waveform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import lab_workflows.experiment_modules.mx_main_field_scope_noise_spectrum.workflow as workflow

    params = MxMainFieldScopeNoiseSpectrumParams(
        control_frequency_stop_hz=40000.0,
        welch_nperseg=10000,
        scope_auto_range_max_attempts=1,
    )
    time_s = np.arange(12000, dtype=float) / 200000.0
    voltage = np.resize(np.asarray([1.9, 2.0]), time_s.size)

    class Scope:
        def __init__(self) -> None:
            self.scales: list[float] = []
            self.offset = 0.0

        def set_channel_scale(self, channel: int, scale: float) -> None:
            self.scales.append(scale)

        def get_channel_scale(self, channel: int) -> float:
            return self.scales[-1]

        def set_channel_offset(self, channel: int, offset: float) -> None:
            self.offset = offset

        def get_channel_offset(self, channel: int) -> float:
            return self.offset

    scope = Scope()
    monkeypatch.setattr(workflow, "check_cancelled", lambda: None)
    monkeypatch.setattr(
        workflow,
        "_temperature_gated_acquire",
        lambda params, devices, channels, *, acquire: acquire(),
    )
    monkeypatch.setattr(
        workflow,
        "_read_scope_record",
        lambda *args: SimpleNamespace(time=time_s, voltage=voltage),
    )
    auto_range = _AutoRangeState(scale_v_div=0.5)
    payload = _acquire_scope_point(
        params,
        {"scope": scope, "acquirer": object(), "temp_switch": object()},
        {"temp_switch": 2},
        AcquisitionConfig(),
        auto_range,
    )

    assert scope.scales == []
    assert auto_range.scale_v_div == pytest.approx(0.5)
    assert auto_range.offset_v == pytest.approx(-1.95)
    assert payload["attempt_display_edge_fraction"][0] == pytest.approx(1.0)


def test_scan_validates_each_current_before_writing(
    monkeypatch: pytest.MonkeyPatch,
    local_tmp_path,
) -> None:
    import lab_workflows.experiment_modules.mx_main_field_scope_noise_spectrum.workflow as workflow

    events: list[tuple] = []

    class GS:
        def set_current(self, value):
            events.append(("current", value))

        def set_output(self, value):
            events.append(("output", value))

    monkeypatch.setattr(workflow, "check_cancelled", lambda: None)
    monkeypatch.setattr(
        workflow,
        "build_scan_axes",
        lambda params: {
            "control_frequency_hz": np.asarray([0.0]),
            "larmor_frequency_hz": np.asarray([0.0]),
            "main_field_current_ma": np.asarray([-0.020332718343180085]),
        },
    )
    monkeypatch.setattr(
        workflow,
        "_validate_main_field_current",
        lambda value: events.append(("validate", value)) or value,
    )
    monkeypatch.setattr(
        workflow,
        "_acquire_scope_point",
        lambda *a, **k: events.append(("acquire",)) or {},
    )
    monkeypatch.setattr(workflow, "_save_point", lambda *a, **k: None)
    run_dir = SimpleNamespace(root=local_tmp_path, raw=local_tmp_path)
    _acquire_scan(
        MxMainFieldScopeNoiseSpectrumParams(),
        run_dir,
        {"gs200": GS()},
        {},
        AcquisitionConfig(),
        _AutoRangeState(scale_v_div=0.5),
    )
    assert events[:4] == [
        ("validate", pytest.approx(-0.020332718343180085)),
        ("current", pytest.approx(-0.020332718343180086e-3)),
        ("output", True),
        ("acquire",),
    ]


class _StateGS:
    def __init__(self) -> None:
        self.source = "CURR"
        self.current_a = 0.0091
        self.output = True
        self.current_range_a = 0.01
        self.current_limit_a = 0.0098
        self.calls: list[tuple] = []

    def get_source_function(self):
        return self.source

    def get_current(self):
        return self.current_a

    def get_output(self):
        return self.output

    def get_current_range(self):
        return self.current_range_a

    def get_current_limit(self):
        return self.current_limit_a

    def set_output(self, value):
        self.output = value
        self.calls.append(("output", value))

    def set_source_function(self, value):
        self.source = value
        self.calls.append(("source", value))

    def set_current_limit(self, value):
        self.current_limit_a = value
        self.calls.append(("limit", value))

    def set_current_range(self, value):
        self.current_range_a = value
        self.calls.append(("range", value))

    def set_current(self, value):
        self.current_a = value
        self.calls.append(("current", value))


@pytest.mark.parametrize("outcome", ["success", "error", "cancel"])
def test_run_restores_complete_gs200_state(
    monkeypatch: pytest.MonkeyPatch,
    local_tmp_path,
    outcome: str,
) -> None:
    import lab_workflows.experiment_modules.mx_main_field_scope_noise_spectrum.workflow as workflow

    root = local_tmp_path / outcome
    raw = root / "raw"
    raw.mkdir(parents=True)
    config_path = root / "experiment_config.yaml"
    config_path.write_text("{}", encoding="utf-8")

    class RunDir:
        def __init__(self) -> None:
            self.root = root
            self.raw = raw
            self.config_path = config_path

        def update_config(self, **values):
            self.config_path.write_text(
                yaml.safe_dump(values, allow_unicode=True), encoding="utf-8"
            )

    gs200 = _StateGS()
    scope = MagicMock()
    monkeypatch.setattr(workflow, "find_project_root", lambda: local_tmp_path)
    monkeypatch.setattr(workflow, "load_mapping", lambda root: {})
    monkeypatch.setattr(workflow, "create_run_directory", lambda *a, **k: RunDir())
    monkeypatch.setattr(workflow, "check_cancelled", lambda: None)
    monkeypatch.setattr(
        workflow,
        "_connect_devices",
        lambda mapping, session: ({"gs200": gs200, "scope": scope}, {}),
    )
    monkeypatch.setattr(workflow, "_device_snapshot", lambda *a: {})
    monkeypatch.setattr(workflow, "_configure_outputs", lambda *a: (120.0, {}))
    monkeypatch.setattr(
        workflow,
        "_configure_scope",
        lambda *a: (
            AcquisitionConfig(),
            {
                "actual_sample_rate_sa_s": 200000.0,
                "actual_initial_scale_v_div": 0.5,
                "actual_initial_offset_v": 0.0,
            },
        ),
    )

    def acquire(*args, **kwargs):
        gs200.set_current(-0.00002)
        gs200.set_output(True)
        if outcome == "error":
            raise RuntimeError("synthetic failure")
        if outcome == "cancel":
            raise WorkflowCancelled("synthetic cancellation")
        return []

    monkeypatch.setattr(workflow, "_acquire_scan", acquire)
    if outcome == "success":
        assert run(MxMainFieldScopeNoiseSpectrumParams()).name == outcome
    else:
        expected = RuntimeError if outcome == "error" else WorkflowCancelled
        with pytest.raises(expected):
            run(MxMainFieldScopeNoiseSpectrumParams())
    current_calls = [call for call in gs200.calls if call[0] == "current"]
    output_calls = [call for call in gs200.calls if call[0] == "output"]
    assert current_calls[-1] == ("current", 0.0091)
    assert output_calls[-1] == ("output", True)
    assert gs200.current_range_a == pytest.approx(0.01)
    assert gs200.current_limit_a == pytest.approx(0.0098)
    scope.trigger_stop.assert_called_once_with()
    scope.set_channel_coupling.assert_called_once_with(1, "DC")


def test_offline_analysis_uses_actual_rate_limits_fit_and_writes_ten_examples(
    monkeypatch: pytest.MonkeyPatch,
    local_tmp_path,
) -> None:
    import lab_workflows.experiment_modules.mx_main_field_scope_noise_spectrum.analysis as analysis

    run_dir = local_tmp_path / "scope_noise_run"
    raw_dir = run_dir / "raw"
    (run_dir / "results").mkdir(parents=True)
    raw_dir.mkdir()
    params = MxMainFieldScopeNoiseSpectrumParams(
        control_frequency_stop_hz=500.0,
        control_frequency_points=10,
        scope_sample_rate_sa_s=2000.0,
        scope_duration_s=1.0,
        welch_nperseg=100,
    )
    (run_dir / "experiment_config.yaml").write_text(
        yaml.safe_dump(
            {
                "experiment_id": "mx-main-field-scope-noise-spectrum",
                "schema_version": 1,
                "parameters": params.to_external(),
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    rng = np.random.default_rng(20260720)
    axes = build_scan_axes(params)
    time_s = np.arange(2000, dtype=float) / 2000.0
    for index in range(params.control_frequency_points):
        voltage_v = (
            1.0e-3
            * np.sin(2.0 * np.pi * (40.0 + index * 2.0) * time_s)
            + rng.normal(scale=1.0e-4, size=time_s.size)
        )
        np.savez(
            raw_dir / f"waveform_I{index:04d}.npz",
            time_s=time_s,
            voltage_v=voltage_v,
            point_index=np.int64(index),
            control_frequency_hz=np.float64(axes["control_frequency_hz"][index]),
            larmor_frequency_hz=np.float64(axes["larmor_frequency_hz"][index]),
            main_field_current_ma=np.float64(axes["main_field_current_ma"][index]),
            actual_rate_sa_s=np.float64(2000.0),
            scale_used_v_div=np.float64(0.125),
        )

    def fake_fit(matrix, frequency, control, **kwargs):
        assert kwargs["fit_half_width_hz"] == pytest.approx(5000.0)
        size = frequency.size
        parameters = np.column_stack(
            [
                np.full(size, 30.0),
                np.full(size, 1.0e-3),
                np.full(size, 1.0e-8),
                np.zeros(size),
            ]
        )
        return NoiseSeparationResult(
            parameters=parameters,
            uncertainties=np.full_like(parameters, 1.0e-9),
            fit_mask=np.ones(size, dtype=bool),
            interpolated_mask=np.zeros(size, dtype=bool),
            s_beta=parameters[:, 1].copy(),
            n_s1=parameters[:, 2].copy(),
        )

    monkeypatch.setattr(analysis, "fit_noise_separation", fake_fit)
    stale_example = run_dir / "results" / "fit_example_99_stale.png"
    stale_example.write_bytes(b"stale")
    result = analyze(run_dir)
    assert result["welch"]["actual_rate_min_sa_s"] == pytest.approx(2000.0)
    assert result["fit"]["dc_excluded"] is True
    assert result["fit"]["frequency_min_hz"] == pytest.approx(20.0)
    assert result["fit"]["frequency_max_hz"] == pytest.approx(500.0)
    assert result["fit"]["example_plot_created_count"] == 10
    psd = np.load(run_dir / "results" / "psd_matrix.npz")
    assert float(psd["frequency_axis_hz"][-1]) == pytest.approx(1000.0)
    fit = np.load(run_dir / "results" / "popt_fit.npz")
    assert float(fit["frequency_axis_hz"][0]) > 0.0
    assert float(fit["frequency_axis_hz"][-1]) <= 500.0
    assert len(list((run_dir / "results").glob("fit_example_*.png"))) == 10
    assert not stale_example.exists()
    for filename in (
        "noise_spectrum_2d.png",
        "noise_spectra_extracted.png",
        "lorentzian_fit_parameters.png",
        "analysis.yaml",
        "analysis.json",
    ):
        assert (run_dir / "results" / filename).is_file()
