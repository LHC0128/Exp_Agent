"""真实业务步骤配合假仪器，覆盖分步执行、重试、失效和目录隔离。"""
import json
import os
from pathlib import Path
import sys

import numpy as np
import pytest
import yaml

from . import workflow, acquisition
from .stages import ACQUISITION, ANALYSIS, FINISH_ID, definition
from .test_workflow import rig, control_source, assert_shutdown


def through(last):
    # A13B 由 LINEWIDTH_MODE 决定是否执行，默认色散等效时分步全透不包括它。
    ids = [stage.id for stage in ACQUISITION if stage.id != "A13B"]
    return ids[:ids.index(last) + 1]


def test_missing_prerequisites_and_preflight_preserve_state(rig):
    ctx = workflow.Workflow(rig.params)
    with pytest.raises(ValueError, match="缺少前置"):
        ctx.execute(["A13"])
    assert not rig.events and not ctx.devices
    ctx.execute(through("A10")[::-1])
    before = ctx.snapshot()["steps"]
    ctx.execute(["A01"])
    assert ctx.snapshot()["steps"] == before
    ctx.finish()


def test_quality_retry_keeps_connections_and_diagnostics(rig):
    rig.xy_r = 1e-20
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A10"))
    first = ctx.directory.root
    assert ctx.states["A09"]["status"] == "quality_failed"
    assert ctx.state["xy_phase_calibration"]["history"]
    with pytest.raises(ValueError, match="缺少前置"):
        ctx.execute(["A10"])
    connections = [event for event in rig.events if event[1] == "connect"]
    rig.xy_r = 1.0
    ctx.execute(["A09", "A10"])
    assert ctx.states["A10"]["status"] == "completed"
    assert ctx.directory.root != first
    assert (first / "raw/xy_aw_waveforms.npz").is_file()
    assert [event for event in rig.events if event[1] == "connect"] == connections
    ctx.finish()


def test_parameter_impacts_match_actual_invalidation(rig):
    ctx = workflow.Workflow(rig.params)
    for state in ctx.states.values():
        state["status"] = "completed"
    before = ctx.snapshot()["steps"]
    ctx.update_parameters({"NOISE_NPERSEG": 200})
    assert ctx.states == before
    ctx.update_parameters({"TEMP_SWITCH_ON_LAG_S": 0.8, "Z_V_TO_NT": 1800})
    expected = set(definition()["parameter_impacts"]["acquisition"]["TEMP_SWITCH_ON_LAG_S"])
    assert {key for key, state in ctx.states.items() if state["status"] == "stale"} == expected
    assert ctx.states[FINISH_ID]["status"] == "completed"
    assert not ctx.devices


def test_manual_rf_phase_step_applies_without_acquisition_and_mode_change_invalidates(rig):
    from dataclasses import replace
    ctx = workflow.Workflow(replace(rig.params, scan_rf_phase=False, z_burst_phase_init=181.5))
    try:
        ctx.execute(through("A10"))
        first = ctx.directory.root
        events_before, reads_before = len(rig.events), len(rig.captures)
        ctx.execute(["A11"])
        assert ctx.states["A11"] == {"status": "completed", "error": "", "artifacts": []}
        assert ctx.z_positive_phase == 181.5
        z, temp = rig.mapping["Z_magnetic_field"], rig.mapping["Temp_Switch"]
        assert (z["resource"], "set_burst_phase", z["channel"], 181.5) in rig.events[events_before:]
        assert not any(event[:3] == (temp["resource"], "setup_dc", temp["channel"])
                       for event in rig.events[events_before:])
        assert len(rig.captures) == reads_before
        original = (first / "experiment_config.yaml").read_bytes()
        ctx.update_parameters({"Z_PHASE_SCAN_ENABLED": True})
        assert ctx.states["A10"]["status"] == "completed"
        assert ctx.states["A11"]["status"] == "stale"
        assert definition()["parameter_impacts"]["acquisition"]["Z_PHASE_SCAN_ENABLED"] == [
            "A11", "A12", "A13", "A14", "A13B", "A15"]
        ctx.execute(["A11", "A12"])
        assert ctx.directory.root != first
        assert ctx.state["rf_phase_calibration"]["mode"] == "scan"
        assert ctx.state["rf_phase_calibration"]["scan_skipped"] is False
        assert ctx.z_positive_phase == pytest.approx(rig.z_phi0)
        # 重跑另建目录，旧手动相位记录及缺少扫描文件的事实保持不变。
        assert yaml.safe_load((first / "experiment_config.yaml").read_text(encoding="utf-8"))["parameters"] == yaml.safe_load(original)["parameters"]
        assert not (first / "raw/z_phase_scan.npz").exists()
        ctx.update_parameters({"Z_BURST_PHASE_INIT": 45.0})
        assert ctx.states["A10"]["status"] == ctx.states["A11"]["status"] == ctx.states["A12"]["status"] == "stale"
    finally:
        ctx.finish()
    assert_shutdown(rig)


def test_completed_scan_safely_seals_and_rerun_keeps_old_directory(rig):
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A15"))
    first = ctx.directory.root
    original = (first / "raw/response_data.npz").read_bytes()
    config = (first / "experiment_config.yaml").read_bytes()
    assert yaml.safe_load(config)["completion_status"] == "completed"
    assert not ctx.closed
    assert_shutdown(rig)
    with pytest.raises(ValueError, match="缺少前置"):
        ctx.execute(["A13"])
    ctx.execute([f"A{i:02d}" for i in range(9, 16)])
    assert ctx.directory.root != first
    assert (first / "raw/response_data.npz").read_bytes() == original
    assert (first / "experiment_config.yaml").read_bytes() == config
    ctx.finish()


def test_applied_parameters_are_saved_before_first_scan_and_noise(rig):
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A12"))
    calibration_dir = ctx.directory.root
    ctx.update_parameters({"RF_AMP_POINTS": 4, "Z_V_TO_NT": 1800})
    ctx.execute(["A13"])
    response_dir = ctx.directory.root
    assert response_dir != calibration_dir
    with np.load(response_dir / "raw/response_data.npz") as data:
        assert len(data["amplitudes_V"]) == 4
        assert data["z_v_to_nT"] == 1800
    response_config = (response_dir / "experiment_config.yaml").read_bytes()
    ctx.update_parameters({"NOISE_N_AVG": 2})
    ctx.execute(["A14", "A15"])
    config = yaml.safe_load(ctx.directory.config_path.read_text(encoding="utf-8"))
    assert ctx.directory.root != response_dir
    assert config["parameters"]["RF_AMP_POINTS"] == 4
    assert config["parameters"]["NOISE_N_AVG"] == 2
    assert config["calibration"]["z_v_to_nT"] == 1800
    assert config["response_acquisition"]["mode"] == "daq_duration"
    # 旧响应轮次被标记替代，原始参数快照和数据仍保留。
    old = yaml.safe_load(response_config)
    saved = yaml.safe_load((response_dir / "experiment_config.yaml").read_text(encoding="utf-8"))
    assert saved["parameters"] == old["parameters"]
    from .analysis import AnalysisWorkflow
    AnalysisWorkflow(ctx.directory.root).execute(["B01"])
    ctx.finish()


def test_external_demod_changes_are_recorded_before_response(rig):
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A12"))
    hf2 = ctx.devices["hf2"]
    hf2.set_int(hf2.demod_path(3) + "/adcselect", 0)
    ctx.execute(["A13"])
    config = yaml.safe_load(ctx.directory.config_path.read_text(encoding="utf-8"))
    assert config["hf2_response_snapshot"][hf2.demod_path(3) + "/adcselect"] == 0
    assert ctx.states["A13"]["status"] == "completed"
    ctx.finish()
    assert_shutdown(rig)


@pytest.mark.parametrize("axis,setting,value", [("x", "shape", "SIN"), ("y", "output", False),
    ("x", "burst_mode", "TRIGgered"), ("z", "trigger_source", "INTernal"),
    ("trigger", "frequency", 1.0)])
def test_generator_readbacks_are_recorded_without_blocking_scan(rig, axis, setting, value):
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A12"))
    binding = rig.mapping[{"x": "X_magnetic_field", "y": "Y_magnetic_field",
        "z": "Z_magnetic_field", "trigger": "Time_sequence_2"}[axis]]
    source = rig.generators[binding["resource"]]
    getattr(source, setting)[binding["channel"]] = value
    ctx.execute(["A13"])
    config = yaml.safe_load(ctx.directory.config_path.read_text(encoding="utf-8"))
    key = {"frequency": "frequency_Hz", "trigger_source": "burst_trigger_source"}.get(setting, setting)
    assert config["response_output_snapshot"][axis][key] == value
    assert ctx.state["response_points_completed"] == rig.params.rf_amp_points
    ctx.finish()
    assert_shutdown(rig)


def test_discrete_demod_readbacks_do_not_block_phase_response_or_noise(rig, monkeypatch):
    from dataclasses import replace
    from .test_workflow import FakeHF2
    requested_tc, actual_tc = 0.009576194757015027, 0.009486844759316142
    original = FakeHF2.set_double

    def set_double(hf2, path, value):
        if path.endswith("/demods/3/timeconstant") and value == requested_tc:
            value = actual_tc
        original(hf2, path, value)

    monkeypatch.setattr(FakeHF2, "set_double", set_double)
    ctx = workflow.Workflow(replace(rig.params, demod3_tc=requested_tc, demod3_order=8))
    ctx.execute(through("A10"))
    config = yaml.safe_load(ctx.directory.config_path.read_text(encoding="utf-8"))
    base = ctx.devices["hf2"].demod_path(3)
    assert config["parameters"]["DEMOD3_TC"] == requested_tc
    assert config["hf2_response_snapshot"][base + "/timeconstant"] == actual_tc
    assert config["hf2_response_snapshot"][base + "/rate"] == 900
    ctx.execute(["A11", "A12", "A13", "A14"])
    assert all(ctx.states[item]["status"] == "completed" for item in ("A11", "A13", "A14"))
    config = yaml.safe_load(ctx.directory.config_path.read_text(encoding="utf-8"))
    assert config["hf2_response_snapshot"][base + "/timeconstant"] == actual_tc
    with np.load(ctx.directory.raw / "response_data.npz") as data:
        assert data["actual_rate_Sa_s"] == 900
    ctx.execute(["A15"])
    ctx.finish()
    assert_shutdown(rig)


def test_visible_parameters_and_source_diagram_match_manifest():
    from dataclasses import fields
    from .models import RFSensitivityDirectAWParams
    visible = {field.metadata["external_name"] for field in fields(RFSensitivityDirectAWParams)
               if field.metadata.get("visible", True)}
    flow = definition()
    displayed = {name for stage in flow["acquisition"] + flow["analysis"] for name in stage["display_parameters"]}
    assert displayed == visible
    source = Path(__file__).with_name("RF 灵敏度（DirectAW）.excalidraw")
    text = source.read_text(encoding="utf-8")
    for stage in ACQUISITION + ANALYSIS:
        assert stage.id in text and stage.title in text
    elements = {item["id"]: item for item in json.loads(text)["elements"]}
    assert elements["A14"]["y"] < elements["A13B"]["y"] < elements["A15"]["y"]
    assert elements["A13Btext"]["containerId"] == "A13B"
    assert "前置：A14" in elements["A13Btext"]["text"]
    arrow = elements["A14toA13B"]
    assert arrow["startBinding"]["elementId"] == "A14" and arrow["endBinding"]["elementId"] == "A13B"


def test_bandwidth_dependencies_and_parameter_impacts_follow_noise(rig):
    from .stages import validate_selection
    flow = definition()
    ids = [stage["id"] for stage in flow["acquisition"]]
    assert ids[ids.index("A13"):ids.index("A15") + 1] == ["A13", "A14", "A13B", "A15"]
    assert flow["parameter_impacts"]["acquisition"]["NOISE_RATE"] == ["A14", "A13B", "A15"]
    ctx = workflow.Workflow(rig.params)
    for state in ctx.states.values():
        state["status"] = "completed"
    assert validate_selection(ACQUISITION, ctx.states, ["A13B", "A14"]) == ["A14", "A13B"]
    ctx.update_parameters({"BW_FREQ_POINTS": 11})
    assert ctx.states["A13B"]["status"] == "stale"
    assert ctx.states["A14"]["status"] == "completed"
    ctx.update_parameters({"NOISE_DURATION": 2.0})
    assert ctx.states["A14"]["status"] == ctx.states["A13B"]["status"] == "stale"


def test_session_finish_keeps_completed_run_and_analysis_cache(rig):
    from .analysis import AnalysisWorkflow
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A15"))
    analysis = AnalysisWorkflow(ctx.directory.root)
    analysis.execute(["B01"])
    config = (ctx.directory.root / "experiment_config.yaml").read_bytes()
    ctx.execute(["A01"])
    ctx.finish(workflow.WorkflowCancelled("本轮完成后取消等待"))
    assert (ctx.directory.root / "experiment_config.yaml").read_bytes() == config
    assert AnalysisWorkflow(ctx.directory.root).states["B01"]["status"] == "completed"


def test_progress_failure_does_not_prevent_shutdown_or_replace_primary(rig, monkeypatch):
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A12"))
    def fail(*args, **kwargs):
        raise OSError("模拟进度通道断开")
    monkeypatch.setattr(workflow, "report_runtime_progress", fail)
    primary = RuntimeError("原始采集错误")
    ctx.finish(primary)
    assert ctx.state["failure_reason"] == str(primary)
    assert ctx.state["safety_shutdown"]["completed"]
    assert_shutdown(rig)


def test_clock_failure_closes_outputs_before_disconnect(rig, monkeypatch):
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A10"))
    def fail(*args, **kwargs):
        raise RuntimeError("模拟时钟回读失败")
    monkeypatch.setattr(workflow, "synchronize_connected_clocks", fail)
    with pytest.raises(RuntimeError, match="时钟回读失败") as error:
        ctx.execute(["A03"])
    ctx.finish(error.value)
    assert_shutdown(rig)
    disconnect = next(index for index, event in enumerate(rig.events) if event[1] == "disconnect")
    assert any(event[1] == "set_output" and event[3] is False for event in rig.events[:disconnect])


def test_atomic_snapshot_retries_windows_lock(tmp_path, monkeypatch):
    from ..noise_spectrum_xy.state_io import write_state, read_state
    original = Path.replace
    attempts = []
    def replace(path, target):
        attempts.append(path)
        if len(attempts) == 1:
            raise PermissionError("模拟短暂占用")
        return original(path, target)
    monkeypatch.setattr(Path, "replace", replace)
    write_state(tmp_path / "state.json", {"revision": 2})
    assert read_state(tmp_path / "state.json") == {"revision": 2}
    assert len(attempts) == 2


def fake_session_main():
    """真实隔离子进程的假仪器入口，生产入口不会引用本测试。"""
    from .models import RFSensitivityDirectAWParams
    from .session import main
    root = Path(os.environ["RF_TEST_ROOT"])
    report = workflow.report_runtime_progress
    with pytest.MonkeyPatch.context() as patch:
        params = control_source.__wrapped__(root, patch)
        fake = rig.__wrapped__(root, patch, params)
        values = json.loads(os.environ["LAB_TYPED_PARAMETERS"])
        values.pop("session_id")
        fake.params = RFSensitivityDirectAWParams.from_external(values)
        def progress(stage, *args, **kwargs):
            if stage == "shutdown":
                fake.in_shutdown = True
            report(stage, *args, **kwargs)
        patch.setattr(workflow, "report_runtime_progress", progress)
        main()


if __name__ == "__main__":
    fake_session_main()
