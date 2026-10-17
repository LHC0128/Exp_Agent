"""独立验收：末点故障、真实参数失效、来源和科学产物边界。"""
from copy import deepcopy
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import sys

import numpy as np
import pytest
import yaml

from ...common import WorkflowCancelled, find_project_root
from . import analysis, analysis_steps, workflow
from .models import MxYOptimalControlRFFrequencyResponseParams
from .stages import ACQUISITION, ANALYSIS, definition
from .test_analysis import build_run
from .test_workflow import assert_safe, copy_config, install_rig, through


@pytest.fixture
def rig(tmp_path, monkeypatch):
    copy_config(tmp_path)
    return install_rig(tmp_path, monkeypatch)


@pytest.mark.parametrize("index", [8, 11])
@pytest.mark.parametrize("cancel", [False, True])
def test_last_optimal_or_constant_failure_keeps_all_prior_real_files(rig, index, cancel):
    error = WorkflowCancelled("末点取消") if cancel else OSError("末点读数失败")
    rig.fault_index, rig.acquire_error = index, error
    with pytest.raises(type(error)) as caught:
        workflow.run(rig.params)
    assert caught.value is error
    assert_safe(rig)
    directory = next((rig.root / "data" / workflow.DATA_TYPE).iterdir())
    assert len(list((directory / "raw").glob("*attempt_*.npz"))) == index
    assert not (directory / workflow.CONSTANT_CONTROL_DATA_FILE).exists()
    assert (directory / workflow.OPTIMAL_CONTROL_DATA_FILE).exists() is (index == 11)
    config = yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf-8"))
    assert config["completion_status"] == ("cancelled" if cancel else "failed")
    assert config["safety_shutdown"]["completed"] is True


def test_last_constant_read_error_survives_temperature_restore_failure(rig):
    error = OSError("末个常数点的原始异常")
    rig.fault_index, rig.acquire_error = 11, error
    rig.restore_error, rig.restore_after = True, 12
    with pytest.raises(OSError) as caught:
        workflow.run(rig.params)
    assert caught.value is error
    directory = next((rig.root / "data" / workflow.DATA_TYPE).iterdir())
    assert len(list((directory / "raw").glob("*attempt_*.npz"))) == 11
    config = yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf-8"))
    assert config["completion_status"] == "failed"
    assert not config["safety_shutdown"]["completed"]
    assert "温控恢复失败" in config["failure_reason"]
    assert "恢复Z最优控制失败" in "；".join(config["safety_shutdown"]["errors"])


def test_every_visible_parameter_owns_real_invalidation_without_hardware_writes(rig):
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A05"))
    states, original = deepcopy(ctx.states), rig.params.to_external()
    frozen = rig.root / "data/Z_AW_Closed_Loop_Waveform_Correction"
    shutil.copytree(frozen / "frozen", frozen / "second")
    coupling = rig.root / "data/Mx_Z_Current_Coupling_Calibration"
    shutil.copytree(coupling / "coupling", coupling / "second")
    changes = {
        "CORRECTED_CONTROL_SOURCE_RUN": "second", "COMPARISON_ENABLED": False,
        "CONSTANT_CONTROL_LARMOR_FREQUENCY_HZ": 900., "CONSTANT_CONTROL_CALIBRATION_SOURCE_RUN": "second",
        "RUN_TAG": "独立验收", "FIXED_PARAMS.Pump_laser_power": .09,
        "FIXED_PARAMS.Probe_laser_power": .09, "FIXED_PARAMS.main_magnetic_field": .01,
        "FIXED_PARAMS.temperature": 129., "FIXED_PARAMS.X_magnetic_field": 8.,
        "FIXED_PARAMS.Y_magnetic_field": .001, "PUMP_CARRIER_FREQUENCY_HZ": 99000000.,
        "PUMP_CARRIER_AMPLITUDE_VPP": .17, "CONTROL_BURST_PHASE_DEG": 1.,
        "TRIGGER_FREQUENCY_HZ": 101., "TRIGGER_AMPLITUDE_VPP": 4.9,
        "TRIGGER_OFFSET_V": 2.4, "TRIGGER_DUTY_PERCENT": 49., "DEMOD_IDX": 1,
        "DEMOD_OSC_IDX": 1, "HF2_SIGNAL_RANGE_V": 1., "RESPONSE_RATE_SA_S": 2000.,
        "RESPONSE_TIME_CONSTANT_S": .002, "RESPONSE_DEMOD_ORDER": 3,
        "SYSTEM_SETTLE_S": .002, "TEMP_SWITCH_ON_LAG_S": .001,
        "R_BAD_POINT_STD_THRESHOLD_V": .09, "R_POINT_MAX_ATTEMPTS": 4,
        "FREQUENCY_START_HZ": 101., "FREQUENCY_STOP_HZ": 301., "FREQUENCY_POINTS": 4,
        "FREQUENCY_RF_AMPLITUDE_VPP": .011, "FREQUENCY_DURATION_S": .017,
        "PHASE_SCAN_START_DEG": 90., "PHASE_SCAN_STOP_DEG": 270., "PHASE_SCAN_STEP_DEG": 45.,
    }
    flow = definition()
    fields = {field["name"] for field in rig.params.schema()["fields"]}
    assert len(fields) == 37 and fields == changes.keys() | {"PUMP_GATE_VOLTAGE_V"}
    assert all([stage["id"] for stage in flow["acquisition"] if key in stage["display_parameters"]]
               for key in fields)
    before = list(rig.events)
    snapshot = (ctx.directory.root / "experiment_config.yaml").read_bytes()
    for key, value in changes.items():
        ctx.params, ctx.states = rig.params, deepcopy(states)
        ctx.update_parameters({key: value})
        expected = set(flow["parameter_impacts"]["acquisition"][key]) - {"A06"}
        assert {item for item, state in ctx.states.items() if state["status"] == "stale"} == expected, key
        assert rig.events == before, key
        assert (ctx.directory.root / "experiment_config.yaml").read_bytes() == snapshot, key
    ctx.params, ctx.states = rig.params, deepcopy(states)
    with pytest.raises(ValueError, match="PUMP_GATE_VOLTAGE_V"):
        ctx.update_parameters({"PUMP_GATE_VOLTAGE_V": 4.9})
    assert ctx.params.to_external() == original and ctx.states == states
    ctx.finish()
    assert_safe(rig)


def test_changed_frozen_input_blocks_next_stage_but_finish_uses_executed_source(rig):
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A03"))
    path = rig.root / "data/Z_AW_Closed_Loop_Waveform_Correction/frozen/results/corrected_control_waveform.npz"
    with np.load(path) as data:
        values = {key: data[key] for key in data.files}
    values["amplitude_vpp"] *= .9
    values["voltage_v"] *= .9
    np.savez(path, **values)
    events = list(rig.events)
    with pytest.raises(ValueError, match="冻结文件已改变"):
        ctx.execute(["A04"])
    assert ctx.states["A01"]["status"] == "stale" and not rig.captures
    assert rig.events == events
    ctx.finish()
    assert_safe(rig)


def test_constant_calibration_plan_is_frozen_with_exact_source_and_inverse_metadata(rig):
    ctx = workflow.Workflow(rig.params)
    ctx.execute(["A01"])
    frozen = ctx.plan.to_metadata()
    path = rig.root / "data/Mx_Z_Current_Coupling_Calibration/coupling/results/analysis.yaml"
    assert frozen["calibration_analysis_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    calibration = yaml.safe_load(path.read_text(encoding="utf-8"))
    calibration["K_Z_Hz_per_A"] *= 2
    path.write_text(yaml.safe_dump(calibration), encoding="utf-8")
    ctx.execute(["A02", "A03", "A04", "A05", "A06"])
    config = yaml.safe_load((ctx.directory.root / "experiment_config.yaml").read_text(encoding="utf-8"))
    assert config["comparison"] == frozen
    with np.load(ctx.directory.root / workflow.CONSTANT_CONTROL_DATA_FILE, allow_pickle=True) as data:
        assert float(data["constant_z_voltage_v"]) == frozen["target_voltage_v"]
    assert frozen["calibration_slope_hz_per_a"] == 100000.
    assert frozen["target_larmor_frequency_hz"] == 1000.
    assert frozen["target_current_a"] == .01
    assert frozen["voltage_to_current_fit"]["point_count"] == 3
    ctx.finish()
    assert_safe(rig)


@pytest.mark.parametrize("kind,stage", [("median", "B02"), ("png", "B03")])
def test_product_sha_detects_same_size_same_timestamp_content_change(tmp_path, kind, stage):
    directory = build_run(tmp_path)
    analysis.analyze(directory)
    path = directory / "results" / ("optimal_control_median_response.npz" if kind == "median" else "r_phase_frequency.png")
    before = path.read_bytes()
    stat = path.stat()
    changed = bytearray(before)
    changed[-1] ^= 1
    path.write_bytes(changed)
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    ctx = analysis.AnalysisWorkflow(directory)
    assert ctx.states["B01"]["status"] == "completed"
    assert ctx.states[stage]["status"] == "stale"
    if stage == "B02":
        with pytest.raises(ValueError, match="缺少前置"):
            ctx.execute(["B03"])
        ctx.execute(["B02", "B03"])
    else:
        ctx.execute(["B03"])
    assert ctx.states["B03"]["status"] == "completed"
    assert any(path.read_bytes() == changed for path in (directory / "results").glob("before_*/" + path.name))


def test_finite_phase_median_matches_independent_sorted_formula_in_both_modes(tmp_path):
    import statistics
    matrix = np.array([[-4., np.inf, 2.], [np.nan, 3., -7.], [8., 5., np.nan]])
    expected = [statistics.median(sorted(float(value) for value in row if np.isfinite(value))) for row in matrix]
    for name, split in (("full", False), ("split", True)):
        directory = build_run(tmp_path / name)
        path = directory / "raw" / analysis_steps.OPTIMAL_CONTROL_NPZ
        with np.load(path, allow_pickle=False) as data:
            values = {key: data[key] for key in data.files}
        values["r_mean_v"] = matrix
        np.savez(path, **values)
        if split:
            ctx = analysis.AnalysisWorkflow(directory)
            for stage in ("B01", "B02", "B03"):
                ctx.execute([stage])
        else:
            analysis.analyze(directory)
        with np.load(directory / "results/optimal_control_median_response.npz") as data:
            np.testing.assert_array_equal(data["response_median_v"], expected)
            np.testing.assert_array_equal(data["valid_phase_count"], [2, 2, 2])
        report = json.loads((directory / "results/analysis.json").read_text(encoding="utf-8"))
        assert report["constant_control"]["response_v"] == [.1, .2, .3]


def test_offline_thin_entry_runs_real_child_and_keeps_raw_and_acquisition_parameters(tmp_path):
    directory = build_run(tmp_path)
    raw = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in (directory / "raw").iterdir()}
    params = yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf-8"))["parameters"]
    env = dict(os.environ, LAB_RUN_DIR=str(directory), LAB_TYPED_PARAMETERS="{}", MPLBACKEND="Agg", PYTHONUTF8="1")
    # 薄入口的旧CLI默认选择最新目录；通过其正式run_dir参数绑定临时副本。
    script = ("import os,runpy; from pathlib import Path; "
        "from lab_workflows.experiment_modules.mx_y_optimal_control_rf_frequency_response.definition import ADAPTER; "
        "analyze=ADAPTER.analyze_cli; ADAPTER.analyze_cli=lambda:analyze(Path(os.environ['LAB_RUN_DIR'])); "
        "runpy.run_path('experiments/Mx_Y_Optimal_Control_RF_Frequency_Response_plot.py',run_name='__main__')")
    result = subprocess.run([sys.executable, "-c", script],
        cwd=find_project_root(), env=env, capture_output=True, text=True, encoding="utf-8", timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
    with np.load(directory / "results/optimal_control_median_response.npz") as data:
        np.testing.assert_array_equal(data["response_median_v"], [2., 5., 8.])
    assert all((directory / "results" / name).is_file() for name in ("r_phase_frequency.png", "r_phase_frequency.pdf",
        "rf_frequency_response_comparison.png", "rf_frequency_response_comparison.pdf", "analysis.json"))
    assert {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in (directory / "raw").iterdir()} == raw
    assert yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf-8"))["parameters"] == params


def test_legacy_settle_migration_and_explicit_new_key_and_hidden_compatibility():
    values = {"RESPONSE_SETTLE_TIME_S": .3, "FREQUENCY_SETTLE_TIME_S": .4, "TEMP_SWITCH_OFF_LEAD_S": .2,
        "Z_AW_OUTPUT_VPP": 7., "Z_AW_OUTPUT_OFFSET": 1.}
    old = MxYOptimalControlRFFrequencyResponseParams.from_external(values)
    new = MxYOptimalControlRFFrequencyResponseParams.from_external(values | {"SYSTEM_SETTLE_S": .15})
    assert old.system_settle_s == .4 and new.system_settle_s == .15
    assert MxYOptimalControlRFFrequencyResponseParams.from_external(new.to_external()).to_external() == new.to_external()
    assert new.to_external()["Z_AW_OUTPUT_VPP"] == 7. and new.to_external()["Z_AW_OUTPUT_OFFSET"] == 1.
    assert not {"Z_AW_OUTPUT_VPP", "Z_AW_OUTPUT_OFFSET"}.intersection(field["name"] for field in new.schema()["fields"])


def test_original_diagram_retains_every_stage_and_actual_quality_gate():
    diagram = json.loads(Path(__file__).with_name("workflow.excalidraw").read_text(encoding="utf-8"))
    texts = {item["id"]: item.get("originalText", "") for item in diagram["elements"] if item["type"] == "text"}
    for stage in (*ACQUISITION, *ANALYSIS):
        assert stage.id in texts
        for gate in stage.quality_gates:
            assert gate in texts[stage.id]
