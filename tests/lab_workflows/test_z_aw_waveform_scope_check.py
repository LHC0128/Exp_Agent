"""Z 任意波线圈波形一致性验证测试。"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
import yaml

from lab_workflows.common import load_mapping
from lab_workflows.experiment_modules.z_aw_waveform_scope_check.analysis import (
    _falling_edge_time,
    _periodic_alignment,
    _resample_for_plot,
    _zscore_rows,
    analyze,
)
from lab_workflows.experiment_modules.z_aw_waveform_scope_check.models import (
    ZAWWaveformScopeCheckParams,
)
from lab_workflows.experiment_modules.z_aw_waveform_scope_check.workflow import (
    SCOPE_TRIGGER_MODE,
    SCOPE_TRIGGER_SLOPE,
    _capture_frame,
    _scope_config,
    safe_shutdown,
)
from lab_workflows.experiments import get_experiment
from lab_workflows.steps import configure_optimal_control_trigger


def _periodic_reference(time_s: np.ndarray, frequency_hz: float) -> np.ndarray:
    return (
        np.sin(2.0 * np.pi * frequency_hz * time_s)
        + 0.25 * np.sin(4.0 * np.pi * frequency_hz * time_s + 0.3)
    )


def test_defaults_schema_and_scope_configuration() -> None:
    params = ZAWWaveformScopeCheckParams()
    fields = {item["name"]: item for item in params.schema()["fields"]}
    assert len(fields) == 14
    assert params.scope_cycles == 3
    assert params.scope_repeats == 5
    assert fields["SCOPE_CYCLES"]["default"] == 3
    assert fields["SCOPE_REPEATS"]["default"] == 5

    theory = type("Theory", (), {
        "time_s": np.arange(100, dtype=float) * 1e-4,
        "repeat_frequency_hz": 10.0,
    })()
    config, snapshot = _scope_config(
        params,
        theory,
        measured_scale_v_div=1.0,
        measured_offset_v=0.0,
    )
    assert config.trigger.source == "C4"
    assert config.trigger.mode == SCOPE_TRIGGER_MODE
    assert config.trigger.slope == SCOPE_TRIGGER_SLOPE
    assert [channel.number for channel in config.channels if channel.enabled] == [3, 4]
    assert all(
        channel.coupling == "DC"
        and channel.impedance == "ONEMeg"
        and channel.probe == 1.0
        for channel in config.channels
        if channel.enabled
    )
    assert snapshot["requested_duration_s"] == pytest.approx(0.3)
    assert snapshot["requested_sample_rate_sa_s"] == pytest.approx(1e4)


def test_preflight_rejects_wrong_z_trigger_colocation() -> None:
    definition = get_experiment("z-aw-waveform-scope-check")
    mapping = deepcopy(load_mapping())
    mapping["Time_sequence_2"]["resource"] = "USB0::OTHER::INSTR"
    with patch("lab_workflows.common.load_mapping", return_value=mapping):
        errors = definition.preflight({})
    assert any("同一台 DG4000" in error for error in errors)


def test_capture_frame_recovers_when_first_c3_c4_record_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    params = ZAWWaveformScopeCheckParams()
    theory = type(
        "Theory",
        (),
        {
            "time_s": np.arange(100, dtype=float) * 1e-4,
            "repeat_frequency_hz": 10.0,
        },
    )()
    config, _ = _scope_config(
        params,
        theory,
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
            return 10000.0

        def get_actual_points(self) -> float:
            return 3000.0

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
        "lab_workflows.experiment_modules.z_aw_waveform_scope_check.workflow.check_cancelled",
        lambda: None,
    )
    monkeypatch.setattr(
        "lab_workflows.experiment_modules.z_aw_waveform_scope_check.workflow._sleep_cancellable",
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
    assert sleeps[0] == pytest.approx(0.55)


def test_capture_frame_rejects_untriggered_frame_and_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    params = ZAWWaveformScopeCheckParams()
    theory = type(
        "Theory",
        (),
        {
            "time_s": np.arange(100, dtype=float) * 1e-4,
            "repeat_frequency_hz": 10.0,
        },
    )()
    config, _ = _scope_config(
        params,
        theory,
        measured_scale_v_div=1.0,
        measured_offset_v=0.0,
    )
    time_s = np.linspace(-0.15, 0.15, 32, endpoint=False)
    measured = SimpleNamespace(
        time=time_s,
        voltage=np.sin(2.0 * np.pi * 10.0 * time_s),
        preamble_dict={"point_num": time_s.size},
    )
    flat_trigger = SimpleNamespace(
        time=time_s,
        voltage=np.full(time_s.size, 6.75),
        preamble_dict={"point_num": time_s.size},
    )
    valid_trigger = SimpleNamespace(
        time=time_s,
        voltage=np.where(time_s < 0.0, 5.0, 0.0),
        preamble_dict={"point_num": time_s.size},
    )
    scope = MagicMock()
    scope.trigger_status.return_value = "Trig'd"
    scope.get_sampling_rate.return_value = 10000.0
    scope.get_actual_points.return_value = 3000.0
    scope.get_channel_scale.return_value = 1.0
    scope.get_channel_offset.return_value = 0.0
    acquirer = MagicMock()
    acquirer.acquire_channel.side_effect = (
        measured,
        flat_trigger,
        measured,
        valid_trigger,
    )
    monkeypatch.setattr(
        "lab_workflows.experiment_modules.z_aw_waveform_scope_check.workflow.check_cancelled",
        lambda: None,
    )
    monkeypatch.setattr(
        "lab_workflows.experiment_modules.z_aw_waveform_scope_check.workflow._sleep_cancellable",
        lambda _seconds: None,
    )
    monkeypatch.setattr(
        "lab_workflows.experiment_modules.z_aw_waveform_scope_check.workflow._wait_for_scope_stop",
        lambda _scope, _timeout: None,
    )

    frame = _capture_frame(
        {"scope": scope, "acquirer": acquirer},
        config,
        params,
    )

    assert np.any(
        (frame["trigger_voltage_v"][:-1] >= params.scope_trigger_level_v)
        & (frame["trigger_voltage_v"][1:] < params.scope_trigger_level_v)
    )
    assert scope.trigger_run.call_count == 2
    scope.reset.assert_called_once_with()
    acquirer.apply_config.assert_called_once_with(config)


def test_capture_frame_retries_trigger_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    params = ZAWWaveformScopeCheckParams()
    theory = type(
        "Theory",
        (),
        {
            "time_s": np.arange(100, dtype=float) * 1e-4,
            "repeat_frequency_hz": 10.0,
        },
    )()
    config, _ = _scope_config(
        params,
        theory,
        measured_scale_v_div=1.0,
        measured_offset_v=0.0,
    )
    scope = MagicMock()
    scope.get_channel_scale.return_value = 1.0
    scope.get_channel_offset.return_value = 0.0
    acquirer = MagicMock()
    monkeypatch.setattr(
        "lab_workflows.experiment_modules.z_aw_waveform_scope_check.workflow.check_cancelled",
        lambda: None,
    )
    monkeypatch.setattr(
        "lab_workflows.experiment_modules.z_aw_waveform_scope_check.workflow._sleep_cancellable",
        lambda _seconds: None,
    )
    monkeypatch.setattr(
        "lab_workflows.experiment_modules.z_aw_waveform_scope_check.workflow._wait_for_scope_trigger",
        lambda _scope, _timeout: (_ for _ in ()).throw(
            TimeoutError("SDS 等待 C4 下降沿触发超时")
        ),
    )

    with pytest.raises(RuntimeError, match="等待 C4 下降沿触发超时"):
        _capture_frame(
            {"scope": scope, "acquirer": acquirer},
            config,
            params,
        )

    assert scope.trigger_run.call_count == 3
    assert scope.reset.call_count == 1
    assert acquirer.apply_config.call_count == 1


def test_optimal_control_trigger_uses_high_impedance_load() -> None:
    device = MagicMock()
    params = ZAWWaveformScopeCheckParams()

    configure_optimal_control_trigger(params, device, 2, output=False)

    device.set_output_load.assert_called_once_with("INFinity", channel=2)
    device.setup_square.assert_called_once_with(
        freq=100.0,
        amplitude=5.0,
        offset=2.5,
        dcycle=50.0,
        phase=0.0,
        channel=2,
    )


def test_safe_shutdown_zeros_both_dg_channels_and_stops_scope() -> None:
    dg = MagicMock()
    scope = MagicMock()
    report = safe_shutdown(
        {"z_control": dg, "scope": scope},
        {"z_field": 1, "trigger": 2},
    )
    assert report.completed
    assert dg.setup_dc.call_count == 2
    assert dg.set_output.call_count == 2
    assert dg.set_burst_state.call_count == 2
    assert dg.set_mod_state.call_count == 2
    dg.setup_dc.assert_any_call(0.0, channel=1)
    dg.setup_dc.assert_any_call(0.0, channel=2)
    dg.set_output.assert_any_call(False, channel=1)
    dg.set_output.assert_any_call(False, channel=2)
    scope.trigger_stop.assert_called_once_with()
    dg.disconnect.assert_called_once_with()
    scope.disconnect.assert_called_once_with()


def _write_synthetic_run(root: Path, *, bad_trigger: bool = False) -> Path:
    run_dir = root / "run"
    raw = run_dir / "raw"
    raw.mkdir(parents=True)
    frequency_hz = 10.0
    points_per_period = 128
    period_s = 1.0 / frequency_hz
    expected_time = np.arange(points_per_period, dtype=float) * period_s / points_per_period
    expected_voltage = _periodic_reference(expected_time, frequency_hz)
    np.savez(
        raw / "applied_control_waveform.npz",
        time_s=expected_time,
        voltage_v=expected_voltage,
        repeat_frequency_hz=np.float64(frequency_hz),
    )

    time_s = (np.arange(448, dtype=float) - 32.0) * period_s / points_per_period
    trigger_voltage = np.where(time_s < 0.0, 5.0, 0.0)
    if bad_trigger:
        trigger_voltage[:] = 0.0
    for index, delay_samples in enumerate((5, 6, 4, 5, 7)):
        delay_s = delay_samples * period_s / points_per_period
        measured = 2.0 * _periodic_reference(time_s - delay_s, frequency_hz) + 0.35
        measured += 0.002 * np.sin(2.0 * np.pi * 31.0 * time_s)
        np.savez(
            raw / f"scope_capture_{index:03d}.npz",
            time_s=time_s,
            measured_voltage_v=measured,
            trigger_time_s=time_s,
            trigger_voltage_v=trigger_voltage,
            scale_used_v_div=np.float64(1.0),
            offset_used_v=np.float64(0.0),
        )
    (run_dir / "experiment_config.yaml").write_text(
        yaml.safe_dump(
            {
                "parameters": {
                    "SCOPE_TRIGGER_LEVEL_V": 2.5,
                    "SCOPE_CYCLES": 3,
                    "SCOPE_TRIGGER_CHANNEL": 4,
                    "SCOPE_VERTICAL_DIVISIONS": 8,
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return run_dir


def test_analysis_recovers_delay_gain_bias_and_summary(tmp_path: Path) -> None:
    result = analyze(_write_synthetic_run(tmp_path))
    summary = result["similarity_summary"]
    assert result["capture_count"] == 5
    assert summary["shape_correlation"]["mean"] > 0.999
    assert summary["shape_nrmse"]["mean"] < 0.05
    assert summary["affine_gain"]["mean"] == pytest.approx(2.0, rel=0.02)
    assert summary["affine_offset_v"]["mean"] == pytest.approx(0.35, abs=0.02)
    assert summary["best_delay_s"]["mean"] == pytest.approx(
        5.4 / (10.0 * 128.0), abs=2.0 / (10.0 * 128.0)
    )
    assert (tmp_path / "run" / "results" / "similarity_diagnostics.npz").is_file()
    assert result["threshold_evaluation"] is None


def test_analysis_rejects_missing_trigger_edge(tmp_path: Path) -> None:
    run_dir = _write_synthetic_run(tmp_path, bad_trigger=True)
    with pytest.raises(ValueError, match="下降沿"):
        analyze(run_dir)


def test_analysis_rejects_nonfinite_and_length_mismatch(tmp_path: Path) -> None:
    run_dir = _write_synthetic_run(tmp_path)
    capture = run_dir / "raw" / "scope_capture_000.npz"
    with np.load(capture) as data:
        payload = {key: data[key] for key in data.files}
    payload["measured_voltage_v"] = payload["measured_voltage_v"].copy()
    payload["measured_voltage_v"][0] = np.nan
    np.savez(capture, **payload)
    with pytest.raises(ValueError, match="无有效数据"):
        analyze(run_dir)


def test_falling_edge_interpolates_at_threshold() -> None:
    time_s = np.array([-1.0, -0.5, 0.5])
    voltage_v = np.array([5.0, 4.0, 0.0])
    assert _falling_edge_time(time_s, voltage_v, 2.5) == pytest.approx(-0.125)


def test_periodic_alignment_normalizes_delay_to_one_period() -> None:
    frequency_hz = 10.0
    points = 128
    dt = 1.0 / frequency_hz / points
    time_s = np.arange(3 * points, dtype=float) * dt
    expected_time = np.arange(points, dtype=float) * dt
    expected_voltage = _periodic_reference(expected_time, frequency_hz)
    measured = _periodic_reference(time_s - 7 * dt, frequency_hz)
    aligned = _periodic_alignment(
        time_s,
        measured,
        {
            "time_s": expected_time,
            "voltage_v": expected_voltage,
            "repeat_frequency_hz": np.asarray(frequency_hz),
        },
    )
    assert aligned["best_shift_samples"] == 7
    assert aligned["best_delay_s"] == pytest.approx(7 * dt)
    assert aligned["shape_correlation"] > 0.999999


def test_periodic_alignment_handles_high_rate_scope_capture() -> None:
    """高采样率示波器帧的延迟搜索应保持可计算。"""
    frequency_hz = 12_000.0
    dt = 1.0e-8
    expected_time = np.arange(1_000, dtype=float) / 1_000.0 / frequency_hz
    expected_voltage = _periodic_reference(expected_time, frequency_hz)
    time_s = (np.arange(50_000, dtype=float) - 25_000.0) * dt
    delay_samples = 37
    measured = _periodic_reference(time_s - delay_samples * dt, frequency_hz)
    aligned = _periodic_alignment(
        time_s,
        measured,
        {
            "time_s": expected_time,
            "voltage_v": expected_voltage,
            "repeat_frequency_hz": np.asarray(frequency_hz),
        },
    )
    assert aligned["best_shift_samples"] == delay_samples
    assert aligned["shape_correlation"] > 0.99999


def test_plot_resampling_stays_inside_actual_capture_window() -> None:
    frequency_hz = 1.0
    time_s = np.linspace(-0.5, 0.5, 101, endpoint=False)
    measured = np.sin(2.0 * np.pi * time_s)
    expected_time = np.arange(64, dtype=float) / 64.0
    expected = {
        "time_s": expected_time,
        "voltage_v": np.sin(2.0 * np.pi * expected_time),
        "repeat_frequency_hz": np.asarray(frequency_hz),
    }
    grid, measured_grid, _ = _resample_for_plot(
        {
            "time_s": time_s,
            "measured_voltage_v": measured,
            "best_delay_s": 0.0,
        },
        expected,
        period_s=1.0,
        cycles=1,
        points=256,
    )
    assert grid[0] == pytest.approx(-0.5)
    assert grid[-1] < 0.5
    assert np.ptp(measured_grid[-64:]) > 0.1


def test_zscore_rows_removes_gain_and_offset_for_shape_comparison() -> None:
    base = np.sin(np.linspace(0.0, 2.0 * np.pi, 128, endpoint=False))
    normalized = _zscore_rows(np.vstack((base, 4.0 * base + 7.5)))
    assert np.allclose(normalized[0], normalized[1])
    assert np.allclose(np.mean(normalized, axis=1), 0.0)
    assert np.allclose(np.std(normalized, axis=1), 1.0)
