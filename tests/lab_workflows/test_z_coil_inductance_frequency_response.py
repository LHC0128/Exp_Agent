"""Z 线圈电感效应频率响应实验测试。"""

from __future__ import annotations

from pathlib import Path
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import MagicMock
from unittest.mock import patch

import numpy as np
import pytest
import yaml

from lab_workflows.common import load_mapping
from lab_workflows.experiment_modules.z_coil_inductance_frequency_response.analysis import (
    analyze,
    fit_inductive_voltage_response,
    fit_sine,
    normalize_amplitude_to_reference,
)
from lab_workflows.experiment_modules.z_coil_inductance_frequency_response.models import (
    ZCoilInductanceFrequencyResponseParams,
)
from lab_workflows.experiment_modules.z_coil_inductance_frequency_response.workflow import (
    _capture_frame,
    _scope_config,
    build_frequency_axis,
    safe_shutdown,
    sine_reference,
)
from lab_workflows.experiments import get_experiment


def test_defaults_schema_and_frequency_axis() -> None:
    params = ZCoilInductanceFrequencyResponseParams()
    fields = {item["name"]: item for item in params.schema()["fields"]}
    assert params.frequency_start_hz == pytest.approx(10.0)
    assert params.frequency_stop_hz == pytest.approx(10000.0)
    assert params.frequency_points == 31
    assert fields["FREQUENCY_START_HZ"]["default"] == pytest.approx(10.0)
    assert fields["FREQUENCY_POINTS"]["default"] == 31
    assert fields["SCOPE_SAMPLE_RATE_SA_S"]["default"] == pytest.approx(500000.0)
    assert fields["SCOPE_SAMPLE_RATE_SA_S"]["group"] == "basic"
    axis = build_frequency_axis(params)
    assert axis.size == 31
    assert axis[0] == pytest.approx(10.0)
    assert axis[-1] == pytest.approx(10000.0)
    assert np.allclose(np.diff(axis), np.diff(axis)[0])


def test_scope_window_covers_two_trigger_periods_at_high_frequency() -> None:
    params = ZCoilInductanceFrequencyResponseParams()
    # 三个被测正弦周期已经短于 100 Hz 共同触发的一个周期；SDS 窗口
    # 必须扩展到两个触发周期，才能保证下载波形内存在 C4 下降沿。
    params.frequency_start_hz = 1000.0
    config, snapshot = _scope_config(
        params,
        1000.0,
        measured_scale_v_div=1.0,
        measured_offset_v=0.0,
    )
    assert config.sampling_time == pytest.approx(0.02)
    assert snapshot["requested_waveform_duration_s"] == pytest.approx(0.003)
    assert snapshot["requested_trigger_coverage_s"] == pytest.approx(0.02)


def test_model_rejects_invalid_frequency_and_sample_rate_lower_bound() -> None:
    params = ZCoilInductanceFrequencyResponseParams(
        frequency_start_hz=1000.0,
        frequency_stop_hz=100.0,
    )
    errors = params.validate()
    assert any("FREQUENCY_START_HZ" in error for error in errors)

    params = ZCoilInductanceFrequencyResponseParams(scope_sample_rate_sa_s=999.0)
    errors = params.validate()
    assert any("SCOPE_SAMPLE_RATE_SA_S=999.0 低于下限" in error for error in errors)


def test_model_does_not_tie_sample_rate_to_frequency_stop() -> None:
    params = ZCoilInductanceFrequencyResponseParams(
        frequency_stop_hz=100000.0,
        scope_sample_rate_sa_s=1000.0,
    )
    assert not any("SCOPE_SAMPLE_RATE_SA_S" in error for error in params.validate())


def test_capture_frame_waits_for_complete_record_and_recovers_empty_first_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    params = ZCoilInductanceFrequencyResponseParams()
    config, _ = _scope_config(
        params,
        10.0,
        measured_scale_v_div=1.0,
        measured_offset_v=0.0,
    )
    time_s = np.linspace(-0.15, 0.15, 32, endpoint=False)
    valid_records = {
        3: SimpleNamespace(
            time=time_s,
            voltage=np.sin(2.0 * np.pi * 10.0 * time_s),
            preamble_dict={"point_num": time_s.size},
        ),
        4: SimpleNamespace(
            time=time_s,
            voltage=np.where(time_s < 0.0, 5.0, 0.0),
            preamble_dict={"point_num": time_s.size},
        ),
    }
    empty_record = SimpleNamespace(
        time=np.asarray([], dtype=float),
        voltage=np.asarray([], dtype=float),
        preamble_dict={"point_num": 0},
    )

    class Scope:
        def __init__(self) -> None:
            self.stopped = True
            self.run_count = 0
            self.reset_count = 0

        def trigger_run(self) -> None:
            self.stopped = False
            self.run_count += 1

        def set_trigger_mode(self, mode: str) -> None:
            assert mode == "SINGle"

        def trigger_stop(self) -> None:
            self.stopped = True

        def trigger_status(self) -> str:
            return "Stop" if self.stopped else "Trig'd"

        def get_sampling_rate(self) -> float:
            return 500000.0

        def get_actual_points(self) -> float:
            return 250000.0

        def get_channel_scale(self, channel: int) -> float:
            assert channel == 3
            return 1.0

        def get_channel_offset(self, channel: int) -> float:
            assert channel == 3
            return 0.0

        def set_channel_scale(self, channel: int, scale: float) -> None:
            assert (channel, scale) == (3, 1.0)

        def set_channel_offset(self, channel: int, offset: float) -> None:
            assert (channel, offset) == (3, 0.0)

        def reset(self) -> None:
            self.reset_count += 1

    class Acquirer:
        def __init__(self) -> None:
            self.read_count = 0
            self.applied_configs = []

        def acquire_channel(self, channel: int, *_args, **_kwargs):
            self.read_count += 1
            if self.read_count <= 2:
                return empty_record
            return valid_records[channel]

        def apply_config(self, applied_config) -> None:
            self.applied_configs.append(applied_config)

    scope = Scope()
    acquirer = Acquirer()
    sleeps = []
    monkeypatch.setattr(
        "lab_workflows.experiment_modules.z_coil_inductance_frequency_response.workflow.check_cancelled",
        lambda: None,
    )
    monkeypatch.setattr(
        "lab_workflows.experiment_modules.z_coil_inductance_frequency_response.workflow._sleep_cancellable",
        sleeps.append,
    )

    frame = _capture_frame(
        {"scope": scope, "acquirer": acquirer},
        config,
        params,
    )

    assert frame["time_s"].size == time_s.size
    assert frame["trigger_time_s"].size == time_s.size
    assert scope.run_count == 2
    assert scope.reset_count == 1
    assert acquirer.applied_configs == [config]
    assert sleeps[0] == pytest.approx(0.75)


def test_fit_sine_recovers_gain_phase_offset_and_residual() -> None:
    frequency_hz = 1000.0
    time_s = np.arange(6000, dtype=float) / 500000.0
    expected = sine_reference(time_s, frequency_hz, 1.0, 0.0)
    measured = 0.72 * np.sin(2.0 * np.pi * frequency_hz * time_s + np.deg2rad(27.0)) + 0.18
    result = fit_sine(time_s, measured, frequency_hz, 1.0, 0.0)
    assert result["amplitude_v"] == pytest.approx(0.72, rel=1e-6)
    assert result["gain"] == pytest.approx(1.44, rel=1e-6)
    assert result["phase_deg"] == pytest.approx(27.0, abs=1e-6)
    assert result["offset_v"] == pytest.approx(0.18, abs=1e-6)
    assert result["residual_rms_v"] < 1e-10
    assert result["correlation"] > 0.999999
    assert np.ptp(expected) == pytest.approx(1.0, rel=1e-3)


def test_amplitude_normalization_reports_change_and_drop_from_lowest_frequency() -> None:
    result = normalize_amplitude_to_reference(
        np.asarray([10.0, 100.0, 1000.0]),
        np.asarray([1.0, 0.8, 1.2]),
        np.asarray([0.01, 0.02, 0.03]),
    )
    assert result["reference_frequency_hz"] == pytest.approx(10.0)
    assert result["normalized_amplitude"] == pytest.approx([1.0, 0.8, 1.2])
    assert result["amplitude_change_percent"] == pytest.approx([0.0, -20.0, 20.0])
    assert result["amplitude_drop_percent"] == pytest.approx([0.0, 20.0, -20.0])
    assert result["amplitude_change_db"][1] == pytest.approx(20.0 * np.log10(0.8))


def test_inductive_response_fit_reports_zero_frequency_reference() -> None:
    frequency_hz = np.asarray([10.0, 100.0, 1000.0, 10000.0, 100000.0])
    amplitude_v = np.asarray(
        [
            0.8,
            0.8008,
            0.87,
            0.98,
            1.0,
        ]
    )
    result = fit_inductive_voltage_response(frequency_hz, amplitude_v)
    assert result["available"]
    assert result["amplitude_zero_frequency_v"] == pytest.approx(0.8, rel=0.03)
    assert result["amplitude_high_frequency_v"] == pytest.approx(1.0, rel=0.03)
    assert result["corner_frequency_hz"] > 0.0
    assert result["r_squared"] > 0.99


def _write_synthetic_run(root: Path) -> Path:
    run_dir = root / "run"
    raw_dir = run_dir / "raw"
    raw_dir.mkdir(parents=True)
    frequencies = (10.0, 1000.0)
    records = []
    for frequency_index, frequency_hz in enumerate(frequencies):
        period_s = 1.0 / frequency_hz
        time_s = (np.arange(6000, dtype=float) - 120.0) / 500000.0
        trigger_voltage = np.where(time_s < 0.0, 5.0, 0.0)
        for repeat_index, phase_deg in enumerate((10.0, 12.0, 8.0)):
            measured = 0.8 * np.sin(
                2.0 * np.pi * frequency_hz * time_s + np.deg2rad(phase_deg)
            ) + 0.05
            path = raw_dir / f"scope_f{frequency_index:03d}_r{repeat_index:02d}.npz"
            np.savez(
                path,
                time_s=time_s,
                measured_voltage_v=measured,
                trigger_time_s=time_s,
                trigger_voltage_v=trigger_voltage,
                frequency_hz=np.float64(frequency_hz),
                drive_amplitude_vpp=np.float64(1.0),
                drive_offset_v=np.float64(0.0),
                actual_rate_sa_s=np.float64(500000.0),
            )
            records.append(str(path.relative_to(run_dir)))
    (run_dir / "experiment_config.yaml").write_text(
        yaml.safe_dump(
            {
                "experiment_id": "z-coil-inductance-frequency-response",
                "parameters": {
                    "SCOPE_TRIGGER_LEVEL_V": 2.5,
                    "DRIVE_AMPLITUDE_VPP": 1.0,
                    "DRIVE_OFFSET_V": 0.0,
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return run_dir


def test_analysis_writes_frequency_response_report(tmp_path: Path) -> None:
    run_dir = _write_synthetic_run(tmp_path)
    result = analyze(run_dir)
    assert result["capture_count"] == 6
    assert result["frequency_count"] == 2
    assert result["summary"]["gain_mean"][0] == pytest.approx(1.6, rel=0.01)
    assert result["summary"]["phase_deg_mean"][0] == pytest.approx(10.0, abs=2.0)
    assert result["summary"]["normalization_reference_frequency_hz"] == pytest.approx(10.0)
    assert result["summary"]["amplitude_normalized_to_reference_mean"] == pytest.approx(
        [1.0, 1.0]
    )
    assert not result["theory_fit"]["available"]
    assert (run_dir / "results" / "frequency_response.npz").is_file()
    assert (run_dir / "results" / "frequency_response.yaml").is_file()
    assert (run_dir / "results" / "frequency_response.json").is_file()
    assert (run_dir / "results" / "waveform_overview.png").is_file()
    assert (run_dir / "results" / "frequency_response.png").is_file()


def test_safe_shutdown_zeros_both_dg_channels_and_stops_scope() -> None:
    dg = MagicMock()
    scope = MagicMock()
    report = safe_shutdown(
        {"z_control": dg, "scope": scope},
        {"z_field": 1, "trigger": 2},
    )
    assert report.completed
    dg.setup_dc.assert_any_call(0.0, channel=1)
    dg.setup_dc.assert_any_call(0.0, channel=2)
    dg.set_output.assert_any_call(False, channel=1)
    dg.set_output.assert_any_call(False, channel=2)
    scope.trigger_stop.assert_called_once_with()
    dg.disconnect.assert_called_once_with()
    scope.disconnect.assert_called_once_with()


def test_definition_is_registered_as_typed_workflow() -> None:
    definition = get_experiment("z-coil-inductance-frequency-response")
    assert definition.execution_mode == "typed_workflow"
    assert definition.required_mapping_keys == (
        "Z_magnetic_field",
        "Time_sequence_2",
        "scope_waveform",
    )
    assert definition.analysis_program.endswith("Z_Coil_Inductance_Frequency_Response_plot.py")


def test_preflight_rejects_wrong_z_trigger_colocation() -> None:
    definition = get_experiment("z-coil-inductance-frequency-response")
    mapping = deepcopy(load_mapping())
    mapping["Time_sequence_2"]["resource"] = "USB0::OTHER::INSTR"
    with patch("lab_workflows.common.load_mapping", return_value=mapping):
        errors = definition.preflight({})
    assert any("同一台 DG4000" in error for error in errors)
