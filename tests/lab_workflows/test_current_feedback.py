from __future__ import annotations

from pathlib import Path
import io
from unittest.mock import Mock

import numpy as np
import pytest
import lab_workflows.current_feedback as current_feedback

from lab_workflows.current_feedback import (
    CorrectedControlWaveform,
    load_corrected_control_waveform,
    load_current_coupling_calibration,
    relative_regularization_scale,
    regularized_inverse_update,
    sense_voltage_to_current,
    spectral_nrmse,
    validate_current_power,
    validate_sense_resistor,
)
from lab_workflows.experiment_modules.z_aw_closed_loop_waveform_correction.workflow import (
    _applied_from_voltage,
    _log_progress,
    _trigger_relative_time,
)
from lab_workflows.experiment_modules.z_aw_closed_loop_waveform_correction.models import (
    ZAWClosedLoopWaveformCorrectionParams,
)
from lab_workflows.experiment_modules.z_aw_closed_loop_waveform_correction import (
    DEFINITION as ZAW_CLOSED_LOOP_DEFINITION,
)
from lab_workflows.experiment_modules.z_aw_closed_loop_waveform_correction.legacy_analysis import (
    _comparison_metrics,
)
from lab_workflows.experiment_modules.mx_z_optimal_control_rf_sensitivity.workflow import (
    _corrected_control_contract,
)
from lab_workflows.experiment_modules.mx_z_optimal_control_rf_sensitivity.sources import (
    load_theory_control,
    resolve_control_results_root,
)


def test_sense_resistor_conversion_and_power_limits() -> None:
    validate_sense_resistor(2.0, 1.0, tolerance_percent=0.1)
    np.testing.assert_allclose(
        sense_voltage_to_current(np.asarray([-0.2, 0.0, 0.4]), 2.0),
        [-0.1, 0.0, 0.2],
    )
    report = validate_current_power(
        [-0.1, 0.1],
        2.0,
        1.0,
        derating_fraction=0.5,
        maximum_current_a=0.2,
    )
    assert report["sense_resistor_power_w"] == pytest.approx(0.02)
    with pytest.raises(ValueError, match="峰值电流"):
        validate_current_power(
            [0.3], 2.0, 1.0, derating_fraction=0.5, maximum_current_a=0.2
        )


def test_current_coupling_loader_rejects_unsuccessful_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "success": False,
        "experiment_id": "mx-z-current-coupling-calibration",
    }
    monkeypatch.setattr(Path, "is_file", lambda _path: True)
    monkeypatch.setattr(
        Path,
        "open",
        lambda _path, **_kwargs: io.StringIO(
            "success: false\nexperiment_id: mx-z-current-coupling-calibration\n"
        ),
    )
    with pytest.raises(ValueError, match="未通过质量检查"):
        load_current_coupling_calibration(Path("project"), "cal_run")


def test_corrected_waveform_loader_and_frequency_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    time_s = np.arange(8, dtype=float) / 800.0
    normalized = np.sin(2.0 * np.pi * np.arange(8) / 8.0)
    payload = {
        "time_s": time_s,
        "omega_ctrl_hz": normalized,
        "target_current_a": normalized * 0.01,
        "normalized": normalized,
        "voltage_v": normalized,
        "repeat_frequency_hz": np.float64(100.0),
        "amplitude_vpp": np.float64(2.0),
        "offset_v": np.float64(0.0),
        "coupling_calibration_run": np.asarray("cal_run"),
        "coupling_calibration_sha256": np.asarray("a" * 64),
        "frequency_response_run": np.asarray("fr_run"),
        "frequency_response_sha256": np.asarray("b" * 64),
    }

    class FakeNpz:
        files = list(payload)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def __getitem__(self, key):
            return payload[key]

    monkeypatch.setattr(Path, "is_file", lambda _path: True)
    monkeypatch.setattr(current_feedback.np, "load", lambda *_args, **_kwargs: FakeNpz())
    monkeypatch.setattr(current_feedback, "sha256_file", lambda _path: "c" * 64)
    loaded = load_corrected_control_waveform(Path("project"), "corrected_run")
    assert loaded.repeat_frequency_hz == pytest.approx(100.0)
    assert loaded.coupling_calibration_run == "cal_run"
    transfer = np.ones(loaded.time_s.size // 2 + 1, dtype=complex)
    updated = regularized_inverse_update(
        np.zeros(8),
        normalized,
        np.zeros(8),
        transfer,
        damping=0.5,
        regularization=1e-6,
        reliable_bins=np.ones(transfer.size, dtype=bool),
    )
    assert spectral_nrmse(normalized, updated) < spectral_nrmse(
        normalized, np.zeros(8)
    )
    with pytest.raises(ValueError, match="超出 DG"):
        _applied_from_voltage(
            np.asarray([0.0, 1.1]), amplitude_vpp=2.0, offset_v=0.0
        )






def test_closed_loop_source_set_schema_and_fixed_roots() -> None:
    field = {
        item["name"]: item
        for item in ZAW_CLOSED_LOOP_DEFINITION.schema()["fields"]
    }["CONTROL_SOURCE_SET"]
    assert field["default"] == "oc_sens"
    assert [item["value"] for item in field["options"]] == [
        "oc_sens",
        "oc_broadband_v2",
    ]
    assert "CONTROL_RESULTS_ROOT" not in {
        item["name"] for item in ZAW_CLOSED_LOOP_DEFINITION.schema()["fields"]
    }
    assert resolve_control_results_root("oc_sens").name == "oc_sens"
    assert resolve_control_results_root("oc_broadband_v2").name == "oc_broadband_v2"
    with pytest.raises(ValueError, match="CONTROL_SOURCE_SET"):
        resolve_control_results_root("custom")


def test_closed_loop_source_set_migrates_legacy_root() -> None:
    migrated = ZAWClosedLoopWaveformCorrectionParams.migrate_external(
        {"CONTROL_RESULTS_ROOT": r"D:\Code\theory_agent\simulate\results\oc_broadband_v2"},
        2,
    )
    assert migrated["CONTROL_SOURCE_SET"] == "oc_broadband_v2"
    with pytest.raises(ValueError, match="固定结果集"):
        ZAWClosedLoopWaveformCorrectionParams.migrate_external(
            {"CONTROL_RESULTS_ROOT": r"D:\other\results"},
            2,
        )


def test_broadband_theory_control_loader_supports_time_ms_format() -> None:
    theory = load_theory_control(
        resolve_control_results_root("oc_broadband_v2"),
        "v2",
    )
    assert theory.version == "v2"
    assert theory.time_s[1] == pytest.approx(2e-7)
    assert theory.repeat_frequency_hz == pytest.approx(500.0)


def test_closed_loop_analysis_reports_absolute_amplitude_error() -> None:
    target = np.asarray([-1.0, 0.0, 1.0])
    measured = 0.5 * target + 0.1
    metrics = _comparison_metrics(target, measured)
    assert metrics["peak_ratio"] == pytest.approx(0.6)
    assert metrics["rms_ratio"] == pytest.approx(np.sqrt(0.53 / 2.0))
    assert metrics["mae_a"] == pytest.approx(0.3666666666666667)
    assert metrics["bias_a"] == pytest.approx(0.1)


def test_rf_sensitivity_corrected_contract_preserves_frozen_output() -> None:
    time_s = np.arange(8, dtype=float) / 800.0
    normalized = np.sin(2.0 * np.pi * np.arange(8) / 8.0)
    corrected = CorrectedControlWaveform(
        run_name="corrected_run",
        waveform_path=Path("corrected_control_waveform.npz"),
        time_s=time_s,
        omega_ctrl_hz=100.0 * normalized,
        target_current_a=0.01 * normalized,
        normalized=normalized,
        voltage_v=2.0 * normalized,
        repeat_frequency_hz=100.0,
        amplitude_vpp=4.0,
        offset_v=0.0,
        waveform_sha256="a" * 64,
        coupling_calibration_run="coupling_run",
        coupling_calibration_sha256="b" * 64,
        frequency_response_run="response_run",
        frequency_response_sha256="c" * 64,
    )
    theory, applied = _corrected_control_contract(corrected)
    np.testing.assert_array_equal(applied.normalized, normalized)
    np.testing.assert_array_equal(applied.voltage_v, corrected.voltage_v)
    assert applied.amplitude_vpp == pytest.approx(4.0)
    assert theory.repeat_frequency_hz == pytest.approx(100.0)




def test_closed_loop_uses_ch4_falling_edge_for_ch3_relative_time() -> None:
    frame = {
        "time_s": np.asarray([0.0, 0.5, 1.0]),
        "trigger_time_s": np.asarray([0.1, 0.3, 0.5]),
        "trigger_voltage_v": np.asarray([5.0, 4.0, 0.0]),
    }

    relative = _trigger_relative_time(frame, trigger_level_v=2.0)

    np.testing.assert_allclose(relative, [-0.4, 0.1, 0.6])


def test_closed_loop_progress_log_is_immediately_flushed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = Mock()
    monkeypatch.setattr("builtins.print", output)

    _log_progress("轮次 1/8，反馈采集 1/2: 等待 SDS CH4 下降沿")

    output.assert_called_once_with(
        "[Z 闭环] 轮次 1/8，反馈采集 1/2: 等待 SDS CH4 下降沿",
        flush=True,
    )
