"""Mx Y 向 RF 频率响应实验契约测试。"""

from __future__ import annotations

from pathlib import Path
import shutil
from types import SimpleNamespace
from uuid import uuid4

import numpy as np
import pytest
import yaml

from lab_workflows.experiment_modules.mx_y_rf_frequency_response.analysis import (
    analyze,
)
from lab_workflows.experiment_modules.mx_y_rf_frequency_response.analysis_core import (
    BlochFitResult,
    bloch_response,
    fit_bloch_response,
    fit_saturation_scaling,
)
from lab_workflows.experiment_modules.mx_y_rf_frequency_response.definition import (
    DEFINITION,
)
from lab_workflows.experiment_modules.mx_y_rf_frequency_response.models import (
    MxYRFFrequencyResponseParams,
)
from lab_workflows.experiment_modules.mx_y_rf_frequency_response.workflow import (
    build_frequency_axis,
    build_rf_amplitude_axis,
)
from lab_workflows.experiments.registry import get_experiment


@pytest.fixture
def local_tmp_path() -> Path:
    root = Path(__file__).resolve().parents[2] / "data"
    path = root / f".test_mx_y_rf_frequency_response_{uuid4().hex}"
    path.mkdir(mode=0o777)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def test_default_params_and_registry_contract() -> None:
    params = MxYRFFrequencyResponseParams()
    assert params.validate() == []
    assert params.linewidth_mode == "frequency_sweep"
    assert params.rf_amplitude_points == 1
    assert params.rf_amplitude_start_vpp == pytest.approx(0.01)
    assert params.rf_amplitude_stop_vpp == pytest.approx(0.01)
    definition = get_experiment("mx-y-rf-frequency-response")
    assert definition is DEFINITION
    assert definition.execution_mode == "typed_workflow"
    assert definition.required_devices == ("GS200", "DG900", "DG4000", "HF2")
    assert definition.preflight_runner({}) == []


def test_hidden_parent_parameters_are_not_exposed_in_schema() -> None:
    params = MxYRFFrequencyResponseParams()
    names = [field["name"] for field in params.schema()["fields"]]
    for hidden in (
        "LINEWIDTH_MODE",
        "Y_RF_AMP_START_VPP",
        "NOISE_N_AVG",
        "FREQUENCY_RF_AMPLITUDE_VPP",
        "RESPONSE_SETTLE_TIME_S",
        "Y_RF_NT_PER_VPP",
        "LINEAR_CHECK_GAMMA_FRACTION",
    ):
        assert hidden not in names
    for visible in (
        "RF_AMPLITUDE_START_VPP",
        "RF_AMPLITUDE_STOP_VPP",
        "RF_AMPLITUDE_POINTS",
        "FREQUENCY_START_HZ",
        "FREQUENCY_STOP_HZ",
        "FREQUENCY_POINTS",
    ):
        assert visible in names


def test_amplitude_axis_single_and_multi() -> None:
    single = build_rf_amplitude_axis(MxYRFFrequencyResponseParams())
    assert single.size == 1
    assert single[0] == pytest.approx(0.01)

    multi = build_rf_amplitude_axis(
        MxYRFFrequencyResponseParams(
            rf_amplitude_start_vpp=0.01,
            rf_amplitude_stop_vpp=0.2,
            rf_amplitude_points=5,
        )
    )
    assert multi.size == 5
    assert multi[0] == pytest.approx(0.01)
    assert multi[-1] == pytest.approx(0.2)

    frequency = build_frequency_axis(MxYRFFrequencyResponseParams())
    assert frequency.size == 201
    assert frequency[0] == pytest.approx(8000.0)
    assert frequency[-1] == pytest.approx(12000.0)


def test_amplitude_axis_validation_errors() -> None:
    mismatch = MxYRFFrequencyResponseParams(
        rf_amplitude_start_vpp=0.01,
        rf_amplitude_stop_vpp=0.02,
        rf_amplitude_points=1,
    )
    assert any("等于 1 时" in item for item in mismatch.validate_model())

    flat = MxYRFFrequencyResponseParams(
        rf_amplitude_start_vpp=0.2,
        rf_amplitude_stop_vpp=0.01,
        rf_amplitude_points=5,
    )
    assert any("大于 1 时" in item for item in flat.validate_model())


def test_bloch_model_weak_drive_hwhm_and_strong_drive_split() -> None:
    frequency = np.linspace(8000.0, 12000.0, 1001)
    center, gamma, amplitude, offset = 10048.0, 350.0, 0.2, 0.01

    weak = bloch_response(frequency, amplitude, gamma, center, offset, 0.0)
    assert weak[np.argmin(np.abs(frequency - center))] == pytest.approx(
        amplitude + offset
    )
    half_height = amplitude / np.sqrt(2.0) + offset
    index = int(np.argmin(np.abs(weak - half_height)))
    assert abs(frequency[index] - center) == pytest.approx(gamma, abs=20.0)

    strong = bloch_response(frequency, amplitude, gamma, center, offset, 4.0)
    peaks = (
        center - gamma * np.sqrt(3.0),
        center + gamma * np.sqrt(3.0),
    )
    peak_value = amplitude / (2.0 * np.sqrt(4.0)) + offset
    for peak in peaks:
        local = strong[np.argmin(np.abs(frequency - peak))]
        assert local == pytest.approx(peak_value, rel=0.02)
    valley = strong[np.argmin(np.abs(frequency - center))]
    assert valley == pytest.approx(amplitude / 5.0 + offset)
    assert local > valley


def test_fit_bloch_response_recovers_weak_drive_parameters() -> None:
    rng = np.random.default_rng(7)
    frequency = np.linspace(8000.0, 12000.0, 201)
    response = bloch_response(
        frequency,
        0.2,
        350.0,
        10050.0,
        0.01,
        0.0,
    )
    response += rng.normal(scale=2e-3, size=response.size)
    fit = fit_bloch_response(frequency, response)
    assert fit.success
    assert not fit.split
    assert fit.gamma == pytest.approx(350.0, rel=0.08)
    assert fit.center == pytest.approx(10050.0, abs=30.0)
    assert fit.saturation == pytest.approx(0.0, abs=0.35)


def test_fit_bloch_response_recovers_split_double_peak() -> None:
    rng = np.random.default_rng(11)
    frequency = np.linspace(6000.0, 14000.0, 401)
    response = bloch_response(
        frequency,
        0.9,
        400.0,
        10000.0,
        0.005,
        9.0,
    )
    response += rng.normal(scale=4e-3, size=response.size)
    fit = fit_bloch_response(frequency, response)
    assert fit.success
    assert fit.split
    assert fit.saturation == pytest.approx(9.0, rel=0.25)
    assert fit.center == pytest.approx(10000.0, abs=40.0)
    assert fit.gamma == pytest.approx(400.0, rel=0.25)
    peaks = fit.peak_positions_hz
    assert peaks is not None
    expected = 400.0 * np.sqrt(8.0)
    assert abs(peaks[1] - peaks[0]) == pytest.approx(2.0 * expected, rel=0.3)
    assert fit.effective_rabi_hz == pytest.approx(400.0 * 3.0, rel=0.25)


def test_saturation_scaling_linear_in_amplitude_squared() -> None:
    amplitude = np.asarray([0.05, 0.1, 0.2])
    saturation = 500.0 * amplitude**2
    result = fit_saturation_scaling(amplitude, saturation)
    assert result["success"]
    assert result["slope_per_vpp2"] == pytest.approx(500.0, rel=0.01)
    assert result["intercept"] == pytest.approx(0.0, abs=1e-9)
    assert result["r_squared"] == pytest.approx(1.0, abs=1e-6)

    single = fit_saturation_scaling(np.asarray([0.1]), np.asarray([5.0]))
    assert not single["success"]
    assert single["n_points"] == 1


def _write_run(
    run_dir: Path,
    *,
    amplitude_vpp: np.ndarray,
    saturation: np.ndarray,
    noise_scale: float = 2e-3,
) -> None:
    raw_dir = run_dir / "raw"
    raw_dir.mkdir(parents=True)
    params = MxYRFFrequencyResponseParams(
        rf_amplitude_points=int(amplitude_vpp.size),
        rf_amplitude_start_vpp=float(amplitude_vpp[0]),
        rf_amplitude_stop_vpp=float(amplitude_vpp[-1]),
    ).to_external()
    (run_dir / "experiment_config.yaml").write_text(
        yaml.safe_dump(
            {
                "experiment_id": "mx-y-rf-frequency-response",
                "schema_version": MxYRFFrequencyResponseParams.schema_version,
                "parameters": params,
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    rng = np.random.default_rng(23)
    frequency = np.linspace(8000.0, 12000.0, 201)
    r_mean = np.vstack(
        [
            bloch_response(frequency, 0.5, 350.0, 10050.0, 0.01, s)
            + rng.normal(scale=noise_scale, size=frequency.size)
            for s in saturation
        ]
    )
    r_std = np.full_like(r_mean, 1e-4)
    np.savez(
        raw_dir / "frequency_response.npz",
        frequency_hz=frequency,
        amplitude_vpp=amplitude_vpp,
        r_mean_v=r_mean,
        r_std_v=r_std,
    )


def test_analyze_single_amplitude_run(local_tmp_path: Path) -> None:
    run_dir = local_tmp_path / "run"
    _write_run(
        run_dir,
        amplitude_vpp=np.asarray([0.01]),
        saturation=np.asarray([0.0]),
    )

    result = analyze(run_dir)

    assert result["success"]
    results_dir = run_dir / "results"
    assert (results_dir / "frequency_response.png").is_file()
    assert (results_dir / "bloch_fit.npz").is_file()
    assert (results_dir / "analysis.yaml").is_file()
    assert not (results_dir / "s_vs_amplitude.png").exists()
    assert result["per_amplitude"][0]["success"]
    assert not result["per_amplitude"][0]["split"]
    assert "frequency_response.png" in result["files"]


def test_analyze_multi_amplitude_run_reports_saturation_scaling(
    local_tmp_path: Path,
) -> None:
    run_dir = local_tmp_path / "run"
    amplitude = np.asarray([0.05, 0.1, 0.2])
    _write_run(
        run_dir,
        amplitude_vpp=amplitude,
        saturation=500.0 * amplitude**2,
        noise_scale=1e-4,
    )

    result = analyze(run_dir)

    assert result["success"]
    results_dir = run_dir / "results"
    assert (results_dir / "s_vs_amplitude.png").is_file()
    scaling = result["saturation_scaling"]
    assert scaling["success"]
    assert scaling["slope_per_vpp2"] == pytest.approx(500.0, rel=0.1)
    split_flags = [item["split"] for item in result["per_amplitude"]]
    assert split_flags == [True, True, True]


def test_acquire_valid_r_point_gating_and_file_naming(
    monkeypatch: pytest.MonkeyPatch,
    local_tmp_path: Path,
) -> None:
    import lab_workflows.experiment_modules.mx_y_rf_frequency_response.workflow as workflow

    payloads = iter(
        [
            {
                "time_s": np.arange(16) / 1000.0,
                "r": np.r_[np.zeros(8), np.ones(8)],
            },
            {
                "time_s": np.arange(16) / 1000.0,
                "r": np.full(16, 0.5),
            },
        ]
    )
    events: list[str] = []
    monkeypatch.setattr(
        workflow,
        "acquire_r",
        lambda *args, **kwargs: next(payloads),
    )
    monkeypatch.setattr(
        workflow,
        "set_temperature_switch",
        lambda _device, enabled, *, channel: events.append(
            f"temp_{'on' if enabled else 'off'}"
        ),
    )
    monkeypatch.setattr(workflow, "_sleep", lambda _duration: None)
    monkeypatch.setattr(
        workflow,
        "_uncancellable_sleep",
        lambda _duration: None,
    )

    params = MxYRFFrequencyResponseParams(
        r_bad_point_std_threshold_v=0.01,
        r_point_max_attempts=3,
    )
    summary, attempt, filename = workflow._acquire_valid_r_point(
        params,
        local_tmp_path,
        {"hf2": object(), "temp_switch": object()},
        {"temp_switch": 2},
        file_stem="response_a000_f0001",
        metadata={
            "amplitude_vpp": np.float64(0.01),
            "frequency_hz": np.float64(9000.0),
        },
        set_y_rf=lambda: events.append("set_y_rf"),
        settle_time_s=0.0,
        duration_s=0.1,
        actual_rate=1000.0,
        device_id="dev",
    )

    assert attempt == 1
    assert filename == "response_a000_f0001_attempt_01.npz"
    assert summary["r_std_v"] == pytest.approx(0.0)
    assert events == [
        "set_y_rf",
        "temp_off",
        "temp_on",
        "set_y_rf",
        "temp_off",
        "temp_on",
    ]
    with np.load(
        local_tmp_path / "response_a000_f0001_attempt_00.npz"
    ) as first:
        assert int(first["quality_accepted"]) == 0
        assert first["amplitude_vpp"] == pytest.approx(0.01)
        assert first["frequency_hz"] == pytest.approx(9000.0)
    with np.load(
        local_tmp_path / "response_a000_f0001_attempt_01.npz"
    ) as second:
        assert int(second["quality_accepted"]) == 1


def test_frequency_response_scan_writes_summary_matrix(
    monkeypatch: pytest.MonkeyPatch,
    local_tmp_path: Path,
) -> None:
    import lab_workflows.experiment_modules.mx_y_rf_frequency_response.workflow as workflow

    monkeypatch.setattr(
        workflow,
        "acquire_r",
        lambda *args, **kwargs: {
            "time_s": np.arange(16) / 1000.0,
            "r": np.full(16, 0.5),
        },
    )
    monkeypatch.setattr(
        workflow,
        "set_temperature_switch",
        lambda _device, enabled, *, channel: None,
    )
    monkeypatch.setattr(workflow, "_sleep", lambda _duration: None)
    monkeypatch.setattr(
        workflow,
        "_uncancellable_sleep",
        lambda _duration: None,
    )
    monkeypatch.setattr(workflow, "check_cancelled", lambda: None)

    class _FakeHF2:
        pass

    class _FakeRF:
        def __init__(self) -> None:
            self.calls: list[tuple] = []

        def set_frequency(self, frequency, *, channel: int) -> None:
            self.calls.append(("frequency", channel, frequency))

        def set_phase_adjust(self, phase, *, channel: int) -> None:
            self.calls.append(("phase", channel, phase))

        def set_amplitude(self, amplitude, *, channel: int) -> None:
            self.calls.append(("amplitude", channel, amplitude))

        def set_output(self, state, *, channel: int) -> None:
            self.calls.append(("output", channel, state))

    params = MxYRFFrequencyResponseParams(
        frequency_points=5,
        rf_amplitude_points=2,
        rf_amplitude_start_vpp=0.01,
        rf_amplitude_stop_vpp=0.02,
    )
    rf = _FakeRF()
    hf2 = _FakeHF2()
    monkeypatch.setattr(
        workflow.demod,
        "configure_oscillator",
        lambda hf2_inst, config: None,
    )
    monkeypatch.setattr(
        workflow,
        "validate_safety_limit",
        lambda *args: None,
    )

    workflow._acquire_frequency_response(
        params,
        local_tmp_path,
        {"xy_field": rf, "hf2": hf2, "temp_switch": object()},
        {"y_rf": 2, "temp_switch": 1},
        1000.0,
        "dev",
    )

    with np.load(local_tmp_path / "frequency_response.npz") as data:
        assert data["frequency_hz"].shape == (5,)
        assert data["amplitude_vpp"].shape == (2,)
        assert data["r_mean_v"].shape == (2, 5)
        assert np.all(data["r_mean_v"] == pytest.approx(0.5))
        assert data["accepted_attempt_index"].shape == (2, 5)
        assert int(np.min(data["accepted_attempt_index"])) == 0
    amplitudes = [call[2] for call in rf.calls if call[0] == "amplitude"]
    assert amplitudes == [0.01] * 5 + [0.02] * 5
    frequencies = [call[2] for call in rf.calls if call[0] == "frequency"]
    assert frequencies[0] == pytest.approx(8000.0)
    assert frequencies[4] == pytest.approx(12000.0)
    assert len(frequencies) == 10
