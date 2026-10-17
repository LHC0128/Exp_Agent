"""独立验收：手算科学量、真实末点写盘故障与只读历史副本。"""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import shutil
import time

import numpy as np
import pytest
import yaml

from ...common import WorkflowCancelled
from . import workflow
from .analysis import AnalysisWorkflow, analyze
from .analysis_core import fit_bloch_response
from .analysis_steps import AnalysisQualityError
from .models import MxYRFFrequencyResponseParams
from .stages import definition
from .test_analysis_stages import build_run
from .test_workflow import assert_safe, copy_config, install_rig


ACQUISITION_CASES = {
    "mx-y-rf-frequency-response": {
        "mode": "single",
        "cases": {
            "partial_save": ["test_independent_nested_last_point_fault_preserves_completed_points"],
            "cancel": ["test_independent_nested_last_point_fault_preserves_completed_points"],
            "acquire_failure": ["test_independent_nested_last_point_fault_preserves_completed_points"],
            "restore_failure": ["test_independent_nested_last_point_fault_preserves_completed_points"],
            "save_failure": ["test_independent_nested_last_point_fault_preserves_completed_points"],
        },
        "sources": [
            "lab_workflows/experiment_modules/mx_y_rf_frequency_response/workflow.py",
            "lab_workflows/experiment_modules/mx_y_rf_frequency_response/acquisition_steps.py",
        ],
    },
}


def vector_magnitude(frequency, amplitude, gamma, center, offset, saturation):
    """由旋转坐标系的 u、v 分量独立构造 R，不调用被验收模型。"""
    detuning = (np.asarray(frequency) - center) / gamma
    denominator = 1 + detuning**2 + saturation
    return offset + np.hypot(amplitude * detuning / denominator, amplitude / denominator)


def scientific_checks(run_dir):
    result = json.loads((run_dir / "results/analysis.json").read_text(encoding="utf-8"))
    with np.load(run_dir / "results/bloch_fit.npz", allow_pickle=False) as saved:
        arrays = {key: saved[key] for key in saved.files}
    scores, unavailable_uncertainties = [], 0
    for index, curve in enumerate(result["per_amplitude"]):
        if not curve["success"]:
            continue
        amplitude, gamma, center, offset, saturation = arrays["fit_parameters"][index]
        mask = ~arrays["bad_point_mask"][index]
        observed = arrays["r_mean_v"][index, mask]
        predicted = vector_magnitude(arrays["frequency_hz"][mask], amplitude, gamma, center, offset, saturation)
        expected_r2 = 1 - np.sum((observed - predicted)**2) / np.sum((observed - observed.mean())**2)
        assert curve["r_squared"] == pytest.approx(expected_r2, abs=1e-12)
        assert arrays["r_squared"][index] == pytest.approx(expected_r2, abs=1e-12)
        assert curve["rmse_v"] == pytest.approx(np.sqrt(np.mean((observed - predicted)**2)), abs=1e-12)
        assert curve["hwhm_hz"] == gamma
        assert curve["effective_rabi_hz"] == pytest.approx(gamma * np.sqrt(saturation), abs=1e-12)
        relative = arrays["fit_uncertainties"][index, 1] / gamma
        if np.isfinite(relative):
            assert curve["relative_gamma_uncertainty"] == pytest.approx(relative, abs=1e-12)
        else:
            # 旧弱驱动四参数协方差带 NaN；保留科学缓存，JSON 明确表示未知。
            assert curve["relative_gamma_uncertainty"] is None
            assert curve["uncertainties"]["gamma_hz"] is None
            unavailable_uncertainties += 1
        if saturation > 1:
            expected_peaks = center + np.array([-1, 1]) * gamma * np.sqrt(saturation - 1)
            np.testing.assert_allclose(arrays["peak_positions_hz"][index], expected_peaks, rtol=1e-12)
            np.testing.assert_allclose(curve["peak_positions_hz"], expected_peaks, rtol=1e-12)
            peak_height = vector_magnitude(expected_peaks, amplitude, gamma, center, offset, saturation)
            np.testing.assert_allclose(peak_height, offset + amplitude / (2 * np.sqrt(saturation)), rtol=1e-12)
        else:
            assert curve["peak_positions_hz"] is None
            assert np.isnan(arrays["peak_positions_hz"][index]).all()
        scores.append(float(expected_r2))
    valid = arrays["fit_success"]
    if valid.sum() >= 2:
        squared = arrays["amplitude_vpp"][valid]**2
        saturation = arrays["saturation"][valid]
        matrix = np.column_stack((squared, np.ones_like(squared)))
        slope, intercept = np.linalg.lstsq(matrix, saturation, rcond=None)[0]
        scaling = result["saturation_scaling"]
        assert scaling["slope_per_vpp2"] == pytest.approx(slope, rel=1e-12)
        assert scaling["intercept"] == pytest.approx(intercept, abs=1e-12)
        predicted = matrix @ np.array([slope, intercept])
        expected_r2 = 1 - np.sum((saturation - predicted)**2) / np.sum((saturation - saturation.mean())**2)
        assert scaling["r_squared"] == pytest.approx(expected_r2, abs=1e-12)
    return {"array_count": len(arrays), "valid_curve_count": len(scores), "independent_r_squared": scores,
        "unavailable_uncertainty_count": unavailable_uncertainties}


@pytest.mark.parametrize("saturation", [0.0, 4.0])
def test_independent_bloch_weak_and_split_formula(saturation):
    frequency = np.linspace(6500, 13500, 141)
    expected = (0.04, 240.0, 10000.0, 0.03, saturation)
    observed = vector_magnitude(frequency, *expected)
    fitted = fit_bloch_response(frequency, observed, r_squared_min=0.999,
        relative_gamma_uncertainty_max=0.1)
    assert fitted.success, fitted.rejection_reasons
    np.testing.assert_allclose(fitted.parameters, expected, rtol=2e-4, atol=2e-5)
    if saturation == 0:
        center = fitted.center
        half_height = vector_magnitude(center + np.sqrt(3) * fitted.gamma, *fitted.parameters)
        assert half_height - fitted.offset == pytest.approx(fitted.amplitude / 2, rel=1e-12)
        assert fitted.to_dict()["hwhm_hz"] == fitted.gamma
    else:
        assert fitted.peak_positions_hz == pytest.approx((10000 - 240 * np.sqrt(3), 10000 + 240 * np.sqrt(3)), abs=0.01)


@pytest.mark.parametrize("fault", ["cancel", "read", "restore", "save"])
def test_independent_nested_last_point_fault_preserves_completed_points(tmp_path, monkeypatch, fault):
    copy_config(tmp_path)
    rig = install_rig(tmp_path, monkeypatch)
    rig.params = replace(rig.params, rf_amplitude_points=3, frequency_points=7)
    point_count = rig.params.rf_amplitude_points * rig.params.frequency_points
    error = WorkflowCancelled("独立验收末点取消") if fault == "cancel" else OSError(f"独立验收末点{fault}失败")
    if fault in ("cancel", "read"):
        rig.fault_index, rig.acquire_error = point_count - 1, error
    from ..mx_y_rf_sensitivity import acquisition
    reader = acquisition.daq.acquire_data

    def acquire(*args, **kwargs):
        if fault == "restore" and len(rig.captures) == point_count - 1:
            rig.restore_error = True
        return reader(*args, **kwargs)

    original = np.savez

    def save(path, **payload):
        if not rig.enabled:
            assert fault == "restore" and rig.events[-1] == "on", "恢复失败也必须先尝试恢复再抢救数据"
        if Path(path).name.startswith("response_"):
            time.sleep(0.001)
            if fault == "save" and len(rig.captures) == point_count:
                raise error
        return original(path, **payload)

    monkeypatch.setattr(acquisition.daq, "acquire_data", acquire)
    monkeypatch.setattr(np, "savez", save)
    with pytest.raises(type(error)) as caught:
        workflow.run(rig.params)
    if fault != "restore":
        assert caught.value is error
        assert_safe(rig)
    assert len(rig.captures) == point_count
    directory = next((tmp_path / "data/Mx_Y_RF_Frequency_Response").iterdir())
    raw = directory / "raw"
    expected_count = point_count if fault == "restore" else point_count - 1
    assert len(list(raw.glob("response_*_attempt_00.npz"))) == expected_count
    assert not (raw / "frequency_response.npz").exists()
    for a in range(3):
        for f in range(7):
            if a == 2 and f == 6 and fault != "restore":
                continue
            with np.load(raw / f"response_a{a:03d}_f{f:04d}_attempt_00.npz") as data:
                assert data["quality_accepted"]
                assert np.isfinite(data["r_v"]).all()
    config = yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf-8"))
    assert config["completion_status"] == ("cancelled" if fault == "cancel" else "failed")


def test_independent_quality_failure_fingerprints_are_real(tmp_path):
    run_dir = build_run(tmp_path, amplitudes=(0.01,), noise=0.02, seed=19)
    ctx = AnalysisWorkflow(run_dir, {"FIT_R_SQUARED_MIN": 1.0})
    with pytest.raises(AnalysisQualityError):
        ctx.execute(["B01", "B02", "B03"])
    state = json.loads((run_dir / "analysis_state.json").read_text(encoding="utf-8"))
    failed = state["steps"]["B02"]
    assert failed["status"] == "quality_failed" and state["steps"]["B03"]["status"] == "pending"
    assert set(failed["artifacts"]) == {"bloch_fit.npz", "fit_diagnostics.yaml"}
    for name, signature in failed["files"].items():
        content = (run_dir / "results" / name).read_bytes()
        assert signature == [len(content), hashlib.sha256(content).hexdigest()]
    assert failed["parameter_fingerprint"] == hashlib.sha256(json.dumps(ctx.options, sort_keys=True).encode()).hexdigest()
    assert failed["parameters"] == ctx.options and failed["input_fingerprint"] == ctx.input_fingerprint()
    assert failed["revision"] == ctx.revision
    assert state["summary"]["per_amplitude"][0]["rejection_reasons"]
    with pytest.raises(ValueError, match="缺少前置"):
        ctx.execute(["B03"])
    assert not (run_dir / "results/frequency_response.png").exists()


def test_independent_every_visible_parameter_has_its_real_invalidation_start(tmp_path, monkeypatch):
    copy_config(tmp_path)
    rig = install_rig(tmp_path, monkeypatch)
    groups = {
        "A02": {"RUN_TAG": "independent"},
        "A04": {"FIXED_PARAMS.main_magnetic_field": 0.8, "FIXED_PARAMS.Pump_laser_power": 0.4,
            "FIXED_PARAMS.Probe_laser_power": 0.1},
        "A05": {"FIXED_PARAMS.temperature": 101.0},
        "A06": {"PUMP_CARRIER_FREQUENCY_HZ": 99e6, "PUMP_CARRIER_AMPLITUDE_VPP": 0.17},
        "A07": {"Y_RF_FREQUENCY_HZ": 10001.0, "DEMOD_IDX": 1, "DEMOD_OSC_IDX": 1,
            "HF2_SIGNAL_RANGE_V": 1.0, "RESPONSE_RATE_SA_S": 1500.0,
            "RESPONSE_TIME_CONSTANT_S": 0.002, "RESPONSE_DEMOD_ORDER": 3},
        "A08": {"RF_AMPLITUDE_START_VPP": 0.009, "RF_AMPLITUDE_STOP_VPP": 0.031,
            "RF_AMPLITUDE_POINTS": 4, "FREQUENCY_START_HZ": 7900.0, "FREQUENCY_STOP_HZ": 12100.0,
            "FREQUENCY_POINTS": 27, "FREQUENCY_DURATION_S": 0.032, "SYSTEM_SETTLE_S": 0.002,
            "TEMP_SWITCH_ON_LAG_S": 0.1, "R_BAD_POINT_STD_THRESHOLD_V": 0.02, "R_POINT_MAX_ATTEMPTS": 4},
        None: {"FIT_R_SQUARED_MIN": 0.7, "FIT_RELATIVE_GAMMA_UNCERTAINTY_MAX": 0.6},
    }
    keys = {key for values in groups.values() for key in values} | {"PUMP_GATE_VOLTAGE_V"}
    assert keys == {field["name"] for field in rig.params.schema(rig.root)["fields"]}
    assert len(keys) == 28
    for start, values in groups.items():
        for key, value in values.items():
            ctx = workflow.Workflow(rig.params, project_root=tmp_path)
            for state in ctx.states.values():
                state["status"] = "completed"
            ctx.update_parameters({key: value})
            expected = {f"A{i:02d}" for i in range(int(start[1:]), 10)} if start else set()
            assert {key for key, state in ctx.states.items() if state["status"] == "stale"} == expected
            assert ctx.states["A10"]["status"] == "completed"
            ctx.finish()
    ctx = workflow.Workflow(rig.params, project_root=tmp_path)
    original = ctx.snapshot()
    with pytest.raises(ValueError, match="PUMP_GATE_VOLTAGE_V"):
        ctx.update_parameters({"PUMP_GATE_VOLTAGE_V": 4.5})
    assert ctx.snapshot() == original
    assert not rig.events
    assert definition()["parameter_impacts"]["acquisition"]["PUMP_GATE_VOLTAGE_V"] == ["A06", "A07", "A08", "A09"]


def test_independent_wait_migration_and_external_roundtrip():
    legacy = {"RESPONSE_SETTLE_TIME_S": 0.2, "FREQUENCY_SETTLE_TIME_S": 0.9,
        "TEMP_SWITCH_OFF_LEAD_S": 0.4, "TEMPERATURE_TOLERANCE_C": 3.0}
    migrated = MxYRFFrequencyResponseParams.from_external(legacy)
    assert migrated.system_settle_s == 0.9
    explicit = MxYRFFrequencyResponseParams.from_external(legacy | {"SYSTEM_SETTLE_S": 0.1})
    assert explicit.system_settle_s == 0.1
    external = explicit.to_external()
    assert not legacy.keys() & external.keys()
    assert MxYRFFrequencyResponseParams.from_external(external).to_external() == external


@pytest.mark.parametrize("run_id", ["0817_105712_mx_y_rf_freq_resp", "0817_115725_mx_y_rf_freq_resp"])
def test_historical_copy_full_staged_and_existing_arrays_match(tmp_path, record_property, run_id):
    source = Path(__file__).resolve().parents[3] / "data/Mx_Y_RF_Frequency_Response" / run_id
    if not source.is_dir():
        pytest.skip("本机未提供此只读历史样本")
    before = {p.relative_to(source).as_posix(): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
        for p in source.rglob("*") if p.is_file()}
    config = yaml.safe_load((source / "experiment_config.yaml").read_text(encoding="utf-8"))
    assert config["schema_version"] == 1 and "raw_format_version" not in config
    full, staged = tmp_path / "full", tmp_path / "staged"
    for target in (full, staged):
        (target / "raw").mkdir(parents=True)
        shutil.copy2(source / "experiment_config.yaml", target / "experiment_config.yaml")
        shutil.copy2(source / "raw/frequency_response.npz", target / "raw/frequency_response.npz")
    analyze(full)
    ctx = AnalysisWorkflow(staged)
    for step in ("B01", "B02", "B03"):
        ctx.execute([step])
    with np.load(source / "results/bloch_fit.npz") as historical, np.load(full / "results/bloch_fit.npz") as complete, np.load(staged / "results/bloch_fit.npz") as separate:
        assert set(historical.files) == set(complete.files) == set(separate.files)
        for key in complete.files:
            np.testing.assert_allclose(complete[key], separate[key], rtol=1e-12, atol=1e-12, equal_nan=True)
            np.testing.assert_allclose(complete[key], historical[key], rtol=1e-12, atol=1e-12, equal_nan=True)
    metrics = scientific_checks(full)
    assert metrics["valid_curve_count"] == 5
    for target in (full, staged):
        assert yaml.safe_load((target / "experiment_config.yaml").read_text(encoding="utf-8"))["parameters"] == config["parameters"]
        assert (target / "results/frequency_response.png").is_file()
        assert (target / "results/s_vs_amplitude.png").is_file()
    after = {p.relative_to(source).as_posix(): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
        for p in source.rglob("*") if p.is_file()}
    assert after == before
    record_property("history_verification", json.dumps({"run_id": run_id, "source_files": len(before), **metrics}))
