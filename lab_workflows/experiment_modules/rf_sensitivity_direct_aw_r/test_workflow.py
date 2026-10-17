"""复用假仪器验证 R 工作流、独立改频、目录与安全边界。"""
from dataclasses import replace
from pathlib import Path
import io
import json
import os
import time
import numpy as np
import pytest
import yaml
from ..rf_sensitivity_direct_aw import workflow as shared
from ..rf_sensitivity_direct_aw.test_workflow import rig, control_source, assert_shutdown
from ...common import WorkflowCancelled
from ...lockin_compatibility import lockin_preflight
from . import workflow, session
from .models import RFSensitivityDirectAWRParams
from .run_format import EXPERIMENT_ID, DATA_TYPE
from .stages import ACQUISITION, definition


@pytest.fixture
def rrig(rig):
    rig.params = RFSensitivityDirectAWRParams.from_external(rig.params.to_external() | {
        "Z_RF_FREQ": 3020.0, "Z_RF_NOISE_FREQ": 3000.0, "RUN_TAG": "rf_sens_aw_r"})
    return rig


def through(last):
    ids = [stage.id for stage in ACQUISITION]
    return ids[:ids.index(last) + 1]


def test_complete_order_signed_phase_frequencies_and_zero_rf(rrig, monkeypatch):
    steps, records = [], []
    original, acquire = workflow.Workflow._execute_step, rrig.acquire
    def execute(ctx, step):
        steps.append(step)
        original(ctx, step)
    def capture(hf2, config, **kwargs):
        z = rrig.mapping["Z_magnetic_field"]
        source = rrig.generators[z["resource"]]
        records.append((config.signal_paths, source.get_frequency(channel=z["channel"]),
                        hf2.get_double(f"{hf2.osc_path(1)}/freq"), source.get_output(channel=z["channel"])))
        return acquire(hf2, config, **kwargs)
    monkeypatch.setattr(workflow.Workflow, "_execute_step", execute)
    monkeypatch.setattr(shared.acquisition.daq, "acquire_data", capture)
    directory = workflow.run(rrig.params)
    assert steps == through("A15") and "A11" not in steps
    assert directory.parent.name == DATA_TYPE
    config = yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf-8"))
    assert config["experiment_id"] == EXPERIMENT_ID and config["phase_calibration_status"] == "completed"
    assert config["stage_frequencies"] == {
        stage: {"rf_frequency_Hz": frequency, "demod3_frequency_Hz": frequency}
        for stage, frequency in (("response", 3020.0), ("calibration", 3000.0), ("noise", 3000.0))}
    assert config["hf2_response_snapshot"]["/dev18246/oscs/1/freq"] == 3020.0
    for snapshot in ("hf2_response_snapshot", "hf2_calibration_snapshot", "hf2_noise_snapshot", "hf2_final_snapshot"):
        for index in (0, 1):
            for mode in ("ac", "imp50"):
                assert config[snapshot][f"/dev18246/sigins/{index}/{mode}"] == 0
    input_events = [event for event in rrig.events if event[1] == "input_mode"]
    assert len(input_events) >= 4 and all(event[3] == 0 for event in input_events)
    assert config["hf2_calibration_snapshot"]["/dev18246/oscs/1/freq"] == 3000.0
    assert records[-1] == (["sample.x"], 3000.0, 3000.0, False)
    assert all(item[1:3] == (3020.0, 3020.0) for item in records[:-1])
    with np.load(directory / "raw/response_data.npz") as data:
        assert data["z_burst_phase_deg"].tolist() == [197.0, 17.0, 17.0]
        assert data["z_rf_freq_Hz"] == 3020.0
        assert data["demod3_r_series_V"].shape[0] == 3
    with np.load(directory / "raw/noise_D3_0000.npz") as data:
        assert data["z_rf_freq_Hz"] == data["demod3_freq_Hz"] == 3000.0
    assert not (directory / "raw/z_phase_scan.npz").exists()
    assert_shutdown(rrig)


def test_staged_directory_rerun_and_parameter_invalidation(rrig):
    ctx = workflow.Workflow(rrig.params)
    with pytest.raises(ValueError, match="缺少前置"):
        ctx.execute(["A12"])
    assert not ctx.devices
    ctx.execute(through("A13")[::-1])
    first = ctx.directory.root
    raw = (first / "raw/response_data.npz").read_bytes()
    ctx.execute(["A12"])
    response_snapshot = dict(ctx.state["hf2_response_snapshot"])
    ctx.execute(["A14"])
    assert ctx.directory.root == first
    ctx.execute(["A13"])
    assert ctx.directory.root != first
    assert (first / "raw/response_data.npz").read_bytes() == raw
    assert ctx.state["hf2_response_snapshot"] == response_snapshot
    assert ctx.state["stage_frequencies"]["response"]["rf_frequency_Hz"] == 3020.0
    assert ctx.states["A12"]["status"] == ctx.states["A14"]["status"] == "stale"
    with pytest.raises(ValueError, match="缺少前置"):
        ctx.execute(["A14"])
    ctx.execute(["A12", "A14"])
    for record in ctx.states.values():
        record["status"] = "completed"
    ctx.update_parameters({"Z_RF_NOISE_FREQ": 2999.0})
    assert ctx.states["A13"]["status"] == "completed"
    assert {key for key, record in ctx.states.items() if record["status"] == "stale"} == {"A12", "A14", "A15"}
    assert definition()["parameter_impacts"]["acquisition"]["Z_RF_NOISE_FREQ"] == ["A12", "A14", "A15"]
    ctx.finish()


def test_aw_constraint_hidden_phase_and_mfli_routes(rrig):
    assert not rrig.params.validate_model()
    assert any("AW_REPEAT_FREQ" in error for error in replace(rrig.params, aw_repeat_freq=3020).validate_model())
    assert not any("Z_RF_FREQ" in error for error in rrig.params.validate_model())
    fields = rrig.params.schema(rrig.tmp_path)["fields"]
    assert not any(field["name"].startswith("Z_PHASE_SCAN") for field in fields)
    flow = definition()
    editable = {name for stage in (*flow["acquisition"], *flow["analysis"]) for name in stage["display_parameters"]}
    assert {field["name"] for field in fields} <= editable
    mapping = dict(rrig.mapping) | {"lockin_r": {"model": "MFLI", "capabilities": {"options": ["MD", "DIG"]}}}
    assert not lockin_preflight(EXPERIMENT_ID, rrig.params.to_external(), mapping=mapping)
    assert lockin_preflight(EXPERIMENT_ID, replace(rrig.params, rf_physical_input_source="voltage1").to_external(), mapping=mapping)


def test_aux_safety_missing_or_exceeding_and_actual_readback(rrig):
    limits_path = rrig.tmp_path / "params/safety_limits.yaml"
    limits = yaml.safe_load(limits_path.read_text(encoding="utf-8"))
    rule = limits["safety_limits"].pop("hf2_dev18246_aux_output_1")
    limits_path.write_text(yaml.safe_dump(limits), encoding="utf-8")
    with pytest.raises(PermissionError, match="安全电压范围"):
        workflow.Workflow(rrig.params)
    assert not rrig.events
    limits["safety_limits"]["hf2_dev18246_aux_output_1"] = rule
    limits_path.write_text(yaml.safe_dump(limits), encoding="utf-8")
    with pytest.raises(ValueError):
        workflow.Workflow(replace(rrig.params, loopback_aux_scale=100))
    ctx = workflow.Workflow(rrig.params)
    ctx.execute(through("A09"))
    hf2 = ctx.devices["hf2"]
    hf2.nodes[f"{hf2.sig_in_path(0)}/range"] = 100
    with pytest.raises(ValueError):
        ctx.execute(["A10"])
    assert f"{hf2.aux_out_path(1)}/scale" not in hf2.nodes
    ctx.finish()
    assert_shutdown(rrig)


@pytest.mark.parametrize("cancel", [False, True])
def test_failure_and_cancel_restore_safe_outputs(rrig, cancel):
    rrig.daq_error = WorkflowCancelled("模拟取消") if cancel else RuntimeError("模拟噪声异常")
    with pytest.raises(type(rrig.daq_error), match=str(rrig.daq_error)):
        workflow.run(rrig.params)
    directory = next((rrig.tmp_path / "data" / DATA_TYPE).iterdir())
    config = yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf-8"))
    assert config["completion_status"] == ("cancelled" if cancel else "failed")
    assert (directory / "raw/response_data.npz").is_file()
    assert config["safety_shutdown"]["completed"]
    assert_shutdown(rrig)


def test_eof_safe_shutdown(rrig):
    from ..noise_spectrum_xy.session import serve
    ctx = workflow.Workflow(rrig.params)
    ctx.execute(through("A13"))
    serve(ctx, io.StringIO(""), stages=ACQUISITION, validate=workflow.shared.validate_selection, finish_id="A16")
    assert ctx.closed and ctx.state["completion_status"] == "cancelled"
    assert_shutdown(rrig)


@pytest.mark.parametrize("kind", ["low_r", "nonfinite", "nonconverged"])
def test_xy_quality_failure_keeps_diagnostics_blocks_successors_and_can_retry(rrig, kind):
    rrig.params = replace(rrig.params, xy_ctrl_phase_max_iter=1)
    ctx = workflow.Workflow(rrig.params)
    ctx.execute(through("A08"))
    rrig.xy_r = 1e-20 if kind == "low_r" else float("nan") if kind == "nonfinite" else 1.0
    rrig.xy_angle = 0.2 if kind == "nonconverged" else 0.0
    try:
        ctx.execute(["A09"])
        assert ctx.states["A09"]["status"] == "quality_failed"
        assert ctx.state["xy_phase_calibration"]["history"]
        first = ctx.directory.root
        raw = (first / "raw/xy_aw_waveforms.npz").read_bytes()
        with pytest.raises(ValueError, match="缺少前置"):
            ctx.execute(["A10"])
        assert not (first / "raw/response_data.npz").exists()
        rrig.xy_r, rrig.xy_angle = 1.0, 0.0
        ctx.execute(["A09"])
        assert ctx.states["A09"]["status"] == "completed"
        assert ctx.directory.root != first
        assert (first / "raw/xy_aw_waveforms.npz").read_bytes() == raw
    finally:
        ctx.finish()


def test_applied_unrun_parameters_do_not_reconfigure_finish_or_old_snapshot(rrig):
    ctx = workflow.Workflow(rrig.params)
    ctx.execute(through("A14"))
    path = ctx.directory.root / "experiment_config.yaml"
    old_config = yaml.safe_load(path.read_text(encoding="utf-8"))
    previous_frequency = ctx.devices["hf2"].get_double("/dev18246/oscs/1/freq")
    events = list(rrig.events)
    updates = {"DEMOD3_RATE": 5000.0, "DEMOD3_TC": 0.003, "Z_RF_NOISE_FREQ": 2999.0,
               "SYSTEM_SETTLE_S": 0.8, "TEMP_SWITCH_ON_LAG_S": 6.0}
    completed = {key for key, record in ctx.states.items() if record["status"] == "completed"}
    expected = {step for key in updates for step in definition()["parameter_impacts"]["acquisition"][key]} & completed
    ctx.update_parameters(updates)
    assert {key for key, record in ctx.states.items() if record["status"] == "stale"} == expected
    assert rrig.events == events, "应用参数不执行仪器写入"
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["parameters"] == old_config["parameters"]
    ctx.execute(["A16"])
    hf2 = ctx.devices["hf2"]
    assert hf2.get_double("/dev18246/demods/3/rate") == 0.9 * rrig.params.demod3_rate
    assert hf2.get_double("/dev18246/demods/3/timeconstant") == rrig.params.demod3_tc
    assert hf2.get_double("/dev18246/oscs/1/freq") == previous_frequency
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["parameters"] == old_config["parameters"]
    assert_shutdown(rrig)


def test_complete_and_individual_steps_preserve_identical_r_raw_and_frequencies(rrig):
    first = workflow.run(rrig.params)
    original = {path.name: path.read_bytes() for path in (first / "raw").glob("*.npz")}
    ctx = workflow.Workflow(rrig.params)
    try:
        for step in through("A15"):
            ctx.execute([step])
        assert ctx.directory.root != first
        for name in original:
            with np.load(first / "raw" / name) as complete, np.load(ctx.directory.raw / name) as staged:
                assert complete.files == staged.files
                for key in complete.files:
                    np.testing.assert_equal(complete[key], staged[key])
            assert (first / "raw" / name).read_bytes() == original[name]
        assert ctx.state["stage_frequencies"] == yaml.safe_load((first / "experiment_config.yaml").read_text(encoding="utf-8"))["stage_frequencies"]
        assert ctx.states["A09"]["status"] == "stale", "收尾关闭 XY 后必须重新准备"
        from .analysis import AnalysisWorkflow, analyze
        whole = analyze(first)
        for stage in ("B01", "B02", "B03", "B04"):
            separate = AnalysisWorkflow(ctx.directory.root).execute([stage])
        assert separate["response_fit"] == whole["response_fit"]
        assert whole["response_fit"]["method"] == "numeric_gradient"
        with np.load(first / "raw/response_data.npz") as saved:
            expected = np.max(np.abs(np.gradient(saved["demod3_r_V"], saved["amplitudes_nT"])))
        assert whole["response_fit"]["slope_V_per_nT"] == pytest.approx(expected)
        for name in ("response_fit.npz", "psd_avg.npz", "sensitivity.npz"):
            with np.load(first / "results" / name) as complete, np.load(ctx.directory.results / name) as staged:
                assert complete.files == staged.files
                for key in complete.files:
                    np.testing.assert_equal(complete[key], staged[key])
    finally:
        ctx.finish()


def test_preflight_preserves_valid_steps_and_finish_requires_no_predecessor(rrig):
    empty = workflow.Workflow(rrig.params)
    empty.execute(["A16"])
    assert empty.closed and empty.directory is None and not rrig.events
    ctx = workflow.Workflow(rrig.params)
    ctx.execute(through("A10"))
    before = ctx.snapshot()["steps"]
    ctx.execute(["A01"])
    assert ctx.snapshot()["steps"] == before
    ctx.finish()


def test_visible_keys_manifest_quality_and_original_diagram_agree(rrig):
    from ...common import find_project_root
    flow = definition()
    assert len(flow["acquisition"]) == 15 and len(flow["analysis"]) == 4
    fields = rrig.params.schema(rrig.tmp_path)["fields"]
    assert len(fields) == 63
    assert not {field["name"] for field in fields} & {"LINEWIDTH_MODE", "BW_FREQ_START", "BW_FREQ_STOP", "BW_FREQ_POINTS"}
    source = next((Path(__file__).parent).glob("*.excalidraw"))
    texts = [item.get("text", "") for item in json.loads(source.read_text(encoding="utf-8"))["elements"]]
    for stage in (*flow["acquisition"], *flow["analysis"]):
        for gate in stage["quality_gates"]:
            assert any(gate in text for text in texts), stage["id"]
    fixture = json.loads((find_project_root() / "GUI/frontend/src/pages/rfDirectAWRWorkflow.fixture.json").read_text(encoding="utf-8"))
    assert fixture == json.loads(json.dumps(flow))
    schema_fixture = json.loads((find_project_root() / "GUI/frontend/src/pages/rfDirectAWRSchema.fixture.json").read_text(encoding="utf-8"))
    metadata = lambda values: [{key: value for key, value in field.items() if key != "default"} for field in values]
    assert metadata(schema_fixture["fields"]) == metadata(fields)


def fake_session_main():
    """真实子进程使用假仪器；生产入口不会导入本函数。"""
    root = Path(os.environ["RF_TEST_ROOT"])
    report = shared.report_runtime_progress
    with pytest.MonkeyPatch.context() as patch:
        params = control_source.__wrapped__(root, patch)
        fake = rig.__wrapped__(root, patch, params)
        values = json.loads(os.environ["LAB_TYPED_PARAMETERS"])
        values.pop("session_id")
        fake.params = RFSensitivityDirectAWRParams.from_external(values)
        acquire = fake.acquire
        def blocked_capture(*args, **kwargs):
            if os.environ.get("RF_R_CAPTURE_BARRIER"):
                (root / "capture-entered").touch()
                deadline = time.monotonic() + 20
                while not (root / "capture-release").exists():
                    kwargs["cancellation_check"]()
                    if time.monotonic() > deadline:
                        raise TimeoutError("假仪器采集屏障未释放")
                    time.sleep(0.01)
            return acquire(*args, **kwargs)
        patch.setattr(shared.acquisition.daq, "acquire_data", blocked_capture)
        def progress(stage, *args, **kwargs):
            if stage == "shutdown":
                fake.in_shutdown = True
                if os.environ.get("RF_R_SHUTDOWN_BARRIER"):
                    (root / "shutdown-entered").touch()
                    deadline = time.monotonic() + 20
                    while not (root / "shutdown-release").exists():
                        if time.monotonic() > deadline:
                            raise TimeoutError("假仪器收尾屏障未释放")
                        time.sleep(0.01)
            report(stage, *args, **kwargs)
        patch.setattr(shared, "report_runtime_progress", progress)
        session.main()


if __name__ == "__main__":
    fake_session_main()
