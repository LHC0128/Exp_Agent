from __future__ import annotations

from pathlib import Path
import shutil
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import numpy as np
import pytest

from lab_workflows.experiment_modules.mx_keithley_6221_optimal_control_rf_sensitivity.definition import DEFINITION
from lab_workflows.experiment_modules.mx_keithley_6221_optimal_control_rf_sensitivity import analysis as analysis_module
from lab_workflows.experiment_modules.mx_keithley_6221_optimal_control_rf_sensitivity.models import MxKeithley6221OptimalControlRFParams
from lab_workflows.experiment_modules.mx_keithley_6221_optimal_control_rf_sensitivity.sources import build_applied_current, load_keithley_calibration, load_theory_control
from lab_workflows.experiment_modules.mx_keithley_6221_optimal_control_rf_sensitivity.workflow import _configure_6221, _configure_trigger, _check_6221_compliance, safe_shutdown
from lab_workflows.experiments.registry import get_experiment
from lab_workflows.steps.keithley_6221 import assess_keithley_6221_current_range


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def local_tmp_path() -> Path:
    path = ROOT / "data" / f".test_mx_6221_optimal_control_{uuid4().hex}"
    path.mkdir(mode=0o777)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def test_registry_schema_and_calibration_conversion() -> None:
    params = MxKeithley6221OptimalControlRFParams.from_yaml(
        ROOT / "params" / "experiments" / f"{DEFINITION.id}.yaml"
    )
    params.confirm_gs200_disconnected = True
    assert params.validate(ROOT) == []
    assert get_experiment(DEFINITION.id) is DEFINITION
    assert DEFINITION.execution_mode == "typed_workflow"
    assert "keithley_6221_main_field" in DEFINITION.required_mapping_keys
    fields = {item["name"]: item for item in DEFINITION.schema()["fields"]}
    assert fields["TRIGGER_FREQUENCY_HZ"]["read_only"] is True
    assert fields["KEITHLEY_OUTPUT_RESPONSE"]["read_only"] is True
    assert fields["KEITHLEY_COMPLIANCE_V"]["read_only"] is True
    assert fields["KEITHLEY_COMPLIANCE_V"]["default"] == pytest.approx(15.0)
    assert fields["KEITHLEY_CURRENT_RANGE_MA"]["default"] == pytest.approx(100.0)
    assert fields["KEITHLEY_CURRENT_RANGE_MA"]["options"][-3:] == [
        {"value": 2.0, "label": "2 mA"},
        {"value": 20.0, "label": "20 mA"},
        {"value": 100.0, "label": "100 mA"},
    ]
    assert "Z_AW_OUTPUT_VPP" not in fields
    theory = load_theory_control(Path(r"D:\Code\theory_agent\simulate\results\oc_sens"), "v2")
    calibration = load_keithley_calibration(ROOT, "0813_114104_mx_6221_main_field_cal")
    applied = build_applied_current(theory, calibration, 1.0)
    assert applied.current_ma[-1] == pytest.approx(0.0)
    assert applied.minimum_ma >= -20.0
    assert applied.maximum_ma <= 20.0
    assert calibration.intercept_hz == pytest.approx(342.3008502697368)


def test_default_control_requires_100ma_range_and_reports_margin() -> None:
    params = MxKeithley6221OptimalControlRFParams.from_yaml(
        ROOT / "params" / "experiments" / f"{DEFINITION.id}.yaml"
    )
    theory = load_theory_control(Path(params.control_results_root), params.control_version)
    calibration = load_keithley_calibration(ROOT, params.keithley_calibration_source_run)
    applied = build_applied_current(theory, calibration, params.control_scale)
    result = assess_keithley_6221_current_range(
        params.keithley_current_range_ma,
        applied.minimum_ma,
        applied.maximum_ma,
    )
    assert result.satisfies
    assert result.required_peak_ma == pytest.approx(89.9360265007)
    assert result.margin_ma == pytest.approx(10.0639734993)
    assert result.recommended_range_ma == pytest.approx(100.0)

    params.keithley_current_range_ma = 20.0
    errors = params.validate(ROOT)
    assert any("不满足控制电流包络要求" in error for error in errors)
    assert any("请选择至少 100 mA 档" in error for error in errors)


class _Source:
    def __init__(self, compliance: bool = False) -> None:
        self.calls: list[tuple] = []
        self.compliance = compliance

    def __getattr__(self, name):
        def method(*args, **kwargs):
            self.calls.append((name, *args, *kwargs.values()))
            if name == "is_in_compliance":
                return self.compliance
            if name.startswith("get_"):
                return 0.0
            return None
        return method


def test_6221_external_trigger_configuration_uses_line1_ignore_off_and_zero_inactive() -> None:
    source = _Source()
    theory = SimpleNamespace(repeat_frequency_hz=30000.0)
    applied = SimpleNamespace(
        minimum_ma=-5.0,
        maximum_ma=5.0,
        amplitude_peak_ma=5.0,
        offset_ma=0.0,
        normalized=np.asarray([-1.0, 0.0, 1.0]),
    )
    assert _configure_6221(
        source,
        theory,
        applied,
        current_range_ma=20.0,
        compliance_v=15.0,
    ) == pytest.approx(0.0)
    names = [item[0] for item in source.calls]
    assert names.index("abort_waveform") < names.index("set_waveform_function")
    assert names.index("set_external_trigger_line") < names.index("arm_waveform")
    assert ("set_external_trigger_line", 1) in source.calls
    assert ("set_external_trigger_ignore", False) in source.calls
    assert ("set_external_trigger_inactive_value", 0.0) in source.calls
    assert ("set_compliance", 15.0) in source.calls
    assert ("set_current_range", 0.02) in source.calls
    assert ("set_output", True) not in source.calls


def test_6221_configuration_rejects_range_smaller_than_control_envelope() -> None:
    source = _Source()
    theory = SimpleNamespace(repeat_frequency_hz=30000.0)
    applied = SimpleNamespace(
        minimum_ma=-21.0,
        maximum_ma=19.0,
        amplitude_peak_ma=21.0,
        offset_ma=0.0,
        normalized=np.asarray([-1.0, 0.0, 19.0 / 21.0]),
    )
    with pytest.raises(ValueError, match="请选择至少 100 mA 档"):
        _configure_6221(
            source,
            theory,
            applied,
            current_range_ma=20.0,
            compliance_v=15.0,
        )
    assert source.calls == []


def test_compliance_failure_keeps_original_and_shutdown_error() -> None:
    source = _Source(compliance=True)
    with pytest.raises(RuntimeError, match="Compliance"):
        _check_6221_compliance(source, "测试点")
    assert [item[0] for item in source.calls[:3]] == ["is_in_compliance", "abort_waveform", "set_output"]


def test_trigger_source_is_5v_50_percent_and_frequency_locked() -> None:
    params = MxKeithley6221OptimalControlRFParams(confirm_gs200_disconnected=True)
    device = MagicMock()
    _configure_trigger(params, device, 2, 30000.0, output=False)
    device.setup_square.assert_called_once_with(
        freq=30000.0, amplitude=5.0, offset=2.5, dcycle=50.0,
        phase=0.0, channel=2,
    )


def test_safe_shutdown_zeros_6221_and_gs200() -> None:
    source = MagicMock()
    gs200 = MagicMock()
    source.get_current.return_value = 0.0
    source.get_output.return_value = False
    gs200.get_current.return_value = 0.0
    gs200.get_output.return_value = False
    report = safe_shutdown(
        {"keithley": source, "gs200": gs200}, {},
        MxKeithley6221OptimalControlRFParams(confirm_gs200_disconnected=True),
    )
    assert report.completed
    source.abort_waveform.assert_called_once()
    source.set_output.assert_called_with(False)
    source.set_current.assert_called_with(0.0)
    gs200.set_output.assert_called_with(False)
    gs200.set_current.assert_called_with(0.0)


def test_analysis_reports_keithley_calibration_key(monkeypatch, local_tmp_path: Path) -> None:
    run_dir = local_tmp_path / "run"
    (run_dir / "raw").mkdir(parents=True)
    (run_dir / "results").mkdir()
    (run_dir / "experiment_config.yaml").write_text(
        "experiment_id: mx-keithley-6221-optimal-control-rf-sensitivity\n"
        "geometry:\n  gs200_main_field_current_ma: 0\n"
        "keithley_calibration:\n  source_run: calibration-run\n",
        encoding="utf-8",
    )
    (run_dir / "results" / "phase_calibration.yaml").write_text(
        "success: true\nselected_y_rf_phase_deg: 12.0\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(analysis_module, "plot_phase_calibration", lambda *a: "phase_calibration.png")
    monkeypatch.setattr(
        analysis_module,
        "analyze_rf_sensitivity",
        lambda *a, **k: {"run_dir": str(run_dir), "files": []},
    )
    result = analysis_module.analyze(run_dir)
    assert result["keithley_calibration"] == {"source_run": "calibration-run"}
    assert "z_calibration" not in result
