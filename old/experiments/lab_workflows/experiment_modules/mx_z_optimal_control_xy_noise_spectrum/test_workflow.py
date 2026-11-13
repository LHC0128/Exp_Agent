"""真实噪声业务配假仪器，保留文件写入与故障恢复。"""
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
import hashlib
import json
import os
import sys
import time
import numpy as np
import pytest
import yaml
from ...common import WorkflowCancelled
from ..mx_z_optimal_control_rf_sensitivity.test_workflow import install_rig as install_control
from ..mx_z_optimal_control_rf_sensitivity import workflow as control_workflow
from ..mx_y_optimal_control_rf_frequency_response.test_workflow import copy_config, assert_safe
from .. import mx_z_control_params
from . import workflow, acquisition, preparation
from .models import MxZOptimalControlXYNoiseSpectrumParams
from .stages import ACQUISITION, FINISH_ID, definition

ACQUISITION_CASES = {
    "mx-z-optimal-control-xy-noise-spectrum": {"mode": "single", "cases": {
        "order": ["test_real_grid_order_slow_disk_and_offsets"],
        "slow_disk": ["test_real_grid_order_slow_disk_and_offsets"],
        "cancel": ["test_failure_preserves_original_and_partial_data"],
        "acquire_failure": ["test_failure_preserves_original_and_partial_data"],
        "restore_failure": ["test_restore_failure_salvages_current_record"],
        "save_failure": ["test_save_failure_after_restore_stops_next_record"],
        "partial_save": ["test_failure_preserves_original_and_partial_data", "test_restore_failure_salvages_current_record"],
        "entrypoints": ["test_full_stepwise_and_rerun_preserve_old_directory"],
    }, "sources": [
        "lab_workflows/experiment_modules/mx_z_optimal_control_xy_noise_spectrum/workflow.py",
        "lab_workflows/experiment_modules/mx_z_optimal_control_xy_noise_spectrum/acquisition.py",
        "lab_workflows/experiment_modules/mx_z_optimal_control_xy_noise_spectrum/preparation.py",
    ]},
}


def parameters(**changes):
    values = dict(corrected_control_source_run="frozen", noise_n_avg=2, noise_rate_sa_s=1000.,
        noise_duration_s=.256, system_settle_s=.001, temp_switch_on_lag_s=0.,
        x_field_start_v=-.01, x_field_stop_v=.01, x_field_points=2,
        y_field_start_v=-.02, y_field_stop_v=.02, y_field_points=2)
    return replace(MxZOptimalControlXYNoiseSpectrumParams(), **(values | changes))


def install_rig(root, monkeypatch, params=None):
    rig = install_control(root, monkeypatch, params or parameters())
    monkeypatch.setattr(workflow, "find_project_root", lambda: root)
    monkeypatch.setattr(mx_z_control_params, "find_project_root", lambda: root)
    return rig


@pytest.fixture
def rig(tmp_path, monkeypatch):
    copy_config(tmp_path)
    return install_rig(tmp_path, monkeypatch)


def through(last):
    ids = [stage.id for stage in ACQUISITION if stage.id != FINISH_ID]
    return ids[:ids.index(last) + 1]


def read_config(directory):
    return yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf-8"))


def hashes(directory):
    return {p.relative_to(directory).as_posix(): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
        for p in directory.rglob("*") if p.is_file()}


def test_real_grid_order_slow_disk_and_offsets(rig, monkeypatch):
    save, write = np.savez, Path.write_text
    def slow(path, **data):
        assert rig.enabled
        if "r_v" in data:
            rig.events.append("save")
            time.sleep(.002)
        save(path, **data)
    def text(path, *args, **kwargs):
        assert rig.enabled
        return write(path, *args, **kwargs)
    monkeypatch.setattr(np, "savez", slow)
    monkeypatch.setattr(Path, "write_text", text)
    directory = workflow.run(rig.params)
    assert_safe(rig)
    for i, event in enumerate(rig.events):
        if event == "acquire":
            assert rig.events[i - 2:i] == ["off", "settle"]
            assert rig.events[i + 1:i + 3] == ["on", "save"]
    config = read_config(directory)
    assert config["raw_format_version"] == 1 and config["completion_status"] == "completed"
    assert config["experiment_id"] == workflow.EXPERIMENT_ID
    assert config["main_field_restore"]["success"] and config["safety_shutdown"]["completed"]
    assert config["temperature_control"]["actual_temperature_c"] is None
    assert config["temperature_control"]["target_temperature_c"] == rig.params.temperature_c
    manifest = yaml.safe_load((directory / "raw/point_manifest.yaml").read_text(encoding="utf-8"))
    assert manifest["acquisition_order"] == ["x_000_y_000", "x_000_y_001", "x_001_y_001", "x_001_y_000"]
    for key, record in manifest["points"].items():
        assert record["status"] == "completed"
        for path in (directory / "raw/points" / key).glob("noise_*.npz"):
            with np.load(path) as data:
                assert data["x_dc_field_v"] == record["x_field_v"]
                assert data["y_rf_dc_v"] == record["y_field_v"]
                assert bool(data["y_rf_output_on"]) == bool(record["y_field_v"])
                assert str(data["y_rf_waveform_mode"]) == "dc"
                assert data["actual_rate_sa_s"] > 0
    assert len(rig.captures) == 8 and all(c == ["sample.r"] for c in rig.captures)
    assert not list((directory / "raw").glob("phase*.npz"))


@pytest.mark.parametrize("failure", [WorkflowCancelled("模拟取消"), OSError("模拟读取失败")])
def test_failure_preserves_original_and_partial_data(rig, failure):
    rig.fault_index, rig.acquire_error = 3, failure
    with pytest.raises(type(failure)) as caught:
        workflow.run(rig.params)
    assert caught.value is failure
    assert_safe(rig)
    directory = next((rig.root / "data" / workflow.DATA_TYPE).iterdir())
    assert len(list((directory / "raw/points").rglob("noise_*.npz"))) == 3
    manifest = yaml.safe_load((directory / "raw/point_manifest.yaml").read_text(encoding="utf-8"))
    assert manifest["points"]["x_000_y_001"]["reason"] == str(failure)
    assert manifest["points"]["x_001_y_001"]["status"] == "pending"
    assert read_config(directory)["completion_status"] != "completed"


@pytest.mark.parametrize("read_fails", [False, True])
def test_restore_failure_salvages_current_record(rig, read_fails):
    rig.restore_error, rig.restore_after = True, 2
    if read_fails:
        rig.fault_index, rig.acquire_error = 1, ValueError("原始读取错误")
    with pytest.raises(ValueError if read_fails else OSError) as caught:
        workflow.run(rig.params)
    if read_fails:
        assert caught.value is rig.acquire_error
    directory = next((rig.root / "data" / workflow.DATA_TYPE).iterdir())
    assert (directory / "raw/points/x_000_y_000/noise_000.npz").is_file()
    if not read_fails:
        with np.load(directory / "raw/points/x_000_y_000/noise_001.npz") as data:
            assert data["r_v"].size >= 8
    assert len(rig.captures) == 2
    assert read_config(directory)["completion_status"] == "failed"
    assert read_config(directory)["safety_shutdown"]["completed"] is False


def test_save_failure_after_restore_stops_next_record(rig, monkeypatch):
    save = np.savez
    def fail(path, **data):
        assert rig.enabled
        if "r_v" in data:
            raise OSError("模拟磁盘写入失败")
        save(path, **data)
    monkeypatch.setattr(np, "savez", fail)
    with pytest.raises(OSError, match="磁盘写入失败"):
        workflow.run(rig.params)
    assert_safe(rig)
    assert len(rig.captures) == 1


def test_zero_bias_keeps_rf_off_even_with_legacy_hidden_rf_values(rig, monkeypatch):
    from ..mx_y_rf_sensitivity import acquisition as r_acquisition
    rig.params = parameters(x_field_start_v=0., x_field_stop_v=0., x_field_points=1,
        y_field_start_v=0., y_field_stop_v=0., y_field_points=1, noise_n_avg=1,
        phase_cal_rf_amplitude_vpp=.05, noise_rf_enabled=True)
    reader = r_acquisition.daq.acquire_data
    def acquire(hf2, config, **kwargs):
        source = rig.mapping["rf_coil"]
        rf = rig.generators[source["resource"]]
        assert rf.shape[source["channel"]] == "DC" and not rf.output[source["channel"]]
        return reader(hf2, config, **kwargs)
    monkeypatch.setattr(r_acquisition.daq, "acquire_data", acquire)
    directory = workflow.run(rig.params)
    with np.load(directory / "raw/points/x_000_y_000/noise_000.npz") as raw:
        assert not raw["y_rf_output_on"] and raw["y_rf_dc_v"] == 0.
    assert_safe(rig)


def test_full_stepwise_and_rerun_preserve_old_directory(rig, monkeypatch):
    initialize = workflow.ControlWorkflow.__init__
    preflight = preparation.preflight
    initializations, preflight_calls = [], []

    def checked(params, root):
        preflight_calls.append((params, root))
        return preflight(params, root)

    def shared_init(ctx, *args, **kwargs):
        initializations.append(kwargs)
        initialize(ctx, *args, **kwargs)

    def wrong_preflight(*args):
        raise AssertionError("噪声子实验必须显式使用自己的预检")

    monkeypatch.setattr(preparation, "preflight", checked)
    monkeypatch.setattr(control_workflow.preparation, "preflight", wrong_preflight)
    monkeypatch.setattr(workflow.ControlWorkflow, "__init__", shared_init)
    full = workflow.run(rig.params)
    events, check_count = list(rig.events), len(preflight_calls)
    ctx = workflow.Workflow(rig.params, session_id="noise-initialization-contract")
    assert len(initializations) == 2 and initializations[-1]["preflight"] is checked
    assert preflight_calls[check_count:] == [(rig.params, rig.root)]
    assert rig.events == events and ctx.directory is None
    assert ctx.session_id == "noise-initialization-contract" and ctx.revision == 0
    assert ctx.shutdown_parameters is ctx.response_parameters is ctx.params
    assert ctx.corrected is ctx.applied is ctx.selected_phase is ctx.sources is None
    metadata = dict(experiment_id=workflow.EXPERIMENT_ID, data_type=workflow.DATA_TYPE,
        execution_mode="typed_workflow", raw_format_version=1, measurement_mode="xy_bias_noise_spectrum")
    assert ctx.state == metadata | {"completion_status": "running"}
    assert ctx.mode_policy is workflow.NOISE_STAGE_MODE_POLICY
    assert ctx.mode_policy.response_prepare_id == "A03"
    assert ctx.mode_policy.response_required_ids == frozenset(("A04", "A05"))
    assert ctx.mode_policy.new_round_on_repeat_ids == ctx.mode_policy.uncertain_y_output_ids == frozenset(("A04", "A05", "A06"))
    with pytest.raises(FrozenInstanceError):
        ctx.mode_policy.response_prepare_id = "A02"
    with pytest.raises(TypeError):
        ctx.initial_metadata["measurement_mode"] = "被修改的模板"
    initializations[-1]["initial_metadata"]["measurement_mode"] = "被修改的调用方字典"
    assert ctx.initial_metadata == metadata
    legacy = object.__new__(workflow.ControlWorkflow)
    assert legacy.mode_policy is workflow.ControlWorkflow.mode_policy and legacy.initial_metadata is None
    stage_ids = {"A04"}
    immutable = replace(ctx.mode_policy, response_required_ids=stage_ids)
    stage_ids.add("A05")
    assert immutable.response_required_ids == frozenset(("A04",))
    for invalid in (metadata | {"devices": object()}, metadata | {"raw_format_version": True},
                    metadata | {"experiment_id": {"state": "不能作为元数据"}}):
        with pytest.raises(ValueError, match="初始元数据"):
            initialize(object.__new__(workflow.ControlWorkflow), rig.params, project_root=rig.root,
                preflight=checked, initial_metadata=invalid)
    for step in ACQUISITION:
        if step.id != FINISH_ID:
            ctx.execute([step.id])
            if step.id == "A05":
                events, revision = list(rig.events), ctx.revision
                with monkeypatch.context() as patch:
                    patch.setattr(ctx, "mode_policy", replace(ctx.mode_policy,
                        response_prepare_id="A02", response_required_ids=frozenset(("A06",))))
                    with pytest.raises(ValueError, match="显式选择 A02"):
                        ctx.execute(["A06"])
                assert ctx.revision == revision and rig.events == events
    for point in ("x_000_y_000", "x_001_y_000"):
        relative = f"points/{point}/noise_000.npz"
        with np.load(full / "raw" / relative) as left, np.load(ctx.directory.raw / relative) as right:
            for key in left.files:
                np.testing.assert_array_equal(left[key], right[key])
    previous, before, sealed_state = ctx.directory.root, hashes(ctx.directory.root), ctx.state
    ctx.execute(["A03", "A04", "A05", "A06"])
    assert ctx.directory.root != previous and hashes(previous) == before
    assert ctx.state is not sealed_state and sealed_state["completion_status"] == "completed"
    assert ctx.state["source_run_id"] == previous.name and ctx.initial_metadata == metadata
    for directory in (full, previous, ctx.directory.root):
        config = read_config(directory)
        assert all(config[key] == value for key, value in metadata.items())
        assert config["completion_status"] == "completed"
    ctx.finish()
    assert_safe(rig)


def test_preconditions_ownership_and_parameter_impacts(rig):
    ctx = workflow.Workflow(rig.params)
    with pytest.raises(ValueError, match="缺少前置"):
        ctx.execute(["A05"])
    assert not rig.events
    visible = {f["name"] for f in rig.params.schema()["fields"]}
    owned = {k for col in ("acquisition", "analysis") for s in definition()[col] for k in s["display_parameters"]}
    assert visible == owned
    ctx.execute(through("A05"))
    ctx.update_parameters({"LOW_FREQ_SKIP_HZ": 5.})
    assert ctx.states["A05"]["status"] == "completed"
    previous = ctx.directory.root
    raw = hashes(previous / "raw")
    ctx.execute(["A06"])
    assert ctx.directory.root != previous and hashes(previous / "raw") == raw
    assert {k: v[0] for k, v in hashes(ctx.directory.raw).items()} == {k: v[0] for k, v in raw.items()}
    ctx.finish()
    empty = workflow.Workflow(rig.params)
    empty.execute([FINISH_ID])
    assert empty.closed and empty.directory is None


def test_unexecuted_draft_and_failed_workpoint_do_not_change_shutdown(rig):
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A05"))
    baseline = ctx.shutdown_parameters
    ctx.update_parameters({"PUMP_CARRIER_FREQUENCY_HZ": 99e6, "RESPONSE_RATE_SA_S": 2000.})
    rig.clock_error = True
    with pytest.raises(RuntimeError):
        ctx.execute(["A03"])
    assert ctx.shutdown_parameters is baseline
    ctx.finish()
    source = rig.mapping["Pump_modulation"]
    assert rig.generators[source["resource"]].frequency[source["channel"]] == baseline.pump_carrier_frequency_hz
    assert ctx.response_parameters.response_rate_sa_s == baseline.response_rate_sa_s
    assert_safe(rig)


def test_source_drift_and_copy_hash_fail_before_output(rig):
    ctx = workflow.Workflow(rig.params)
    ctx.execute(["A01"])
    frozen = ctx.sources
    path = ctx.sources[0].waveform_path
    path.write_bytes(path.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="来源文件已改变"):
        ctx.execute(["A02"])
    assert not rig.events
    raw = rig.root / "copy"
    raw.mkdir()
    with pytest.raises(ValueError, match="冻结内容不一致"):
        preparation.save_source(raw, frozen)
    ctx.finish()


def test_manifest_failure_retains_previous_and_original_error(rig, monkeypatch):
    path = rig.root / "manifest.yaml"
    acquisition._write_manifest(path, {"previous": True})
    before = path.read_bytes()
    def fail(*args):
        raise PermissionError("模拟原子替换失败")
    with monkeypatch.context() as patch:
        patch.setattr(Path, "replace", fail)
        with pytest.raises(PermissionError):
            acquisition._write_manifest(path, {"next": True})
    assert path.read_bytes() == before
    failure = OSError("原始读数失败")
    rig.fault_index, rig.acquire_error = 1, failure
    write = acquisition._write_manifest
    def fail_manifest(path, value):
        if any(p["status"] == "failed" for p in value["points"].values()):
            raise PermissionError("故障状态无法保存")
        write(path, value)
    monkeypatch.setattr(acquisition, "_write_manifest", fail_manifest)
    with pytest.raises(OSError) as caught:
        workflow.run(rig.params)
    assert caught.value is failure
    assert "故障点清单" in str(caught.value.acquisition_cleanup_notes)


def test_shutdown_failure_cannot_seal_or_finish_as_completed(rig, monkeypatch):
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A05"))
    signal, channel = ctx.devices["xy_field"], ctx.channels["y_rf"]
    original = signal.set_output
    def fail(value, *, channel=1):
        if not value and channel == ctx.channels["y_rf"]:
            raise OSError("模拟 Y 关闭失败")
        return original(value, channel=channel)
    monkeypatch.setattr(signal, "set_output", fail)
    with pytest.raises(RuntimeError, match="安全收尾失败"):
        ctx.execute(["A06"])
    with pytest.raises(RuntimeError, match="安全收尾失败"):
        ctx.finish()
    config = read_config(ctx.directory.root)
    assert config["completion_status"] == "failed" and not config["safety_shutdown"]["completed"]


def main():
    """只在测试子进程显式注入假仪器，生产入口不导入此模块。"""
    from ...experiment_runtime import load_runtime_params
    from ..noise_spectrum_xy.session import serve
    root = Path(os.environ["MX_Z_XY_NOISE_TEST_ROOT"])
    payload = json.loads(os.environ["LAB_TYPED_PARAMETERS"])
    session_id = payload.pop("session_id")
    os.environ["LAB_TYPED_PARAMETERS"] = json.dumps(payload)
    monkeypatch = pytest.MonkeyPatch()
    install_rig(root, monkeypatch)
    ctx = workflow.Workflow(load_runtime_params(MxZOptimalControlXYNoiseSpectrumParams), session_id=session_id)
    if gate_path := os.environ.get("MX_Z_XY_NOISE_TEST_SHUTDOWN_GATE"):
        gate = Path(gate_path)
        shutdown = ctx._shutdown
        def blocked_shutdown(**kwargs):
            gate.with_suffix(".started").touch()
            deadline = time.monotonic() + 25
            while not gate.exists():
                if time.monotonic() > deadline:
                    raise TimeoutError("测试未释放收尾")
                time.sleep(.02)
            result = shutdown(**kwargs)
            gate.with_suffix(".finished").write_text(json.dumps(dict(
                pump_frequency_hz=ctx.shutdown_parameters.pump_carrier_frequency_hz,
                response_rate_sa_s=ctx.response_parameters.response_rate_sa_s,
                errors=result)), encoding="utf-8")
            return result
        ctx._shutdown = blocked_shutdown
    try:
        serve(ctx, sys.stdin, stages=ACQUISITION, validate=workflow.validate_selection, finish_id=FINISH_ID)
    finally:
        monkeypatch.undo()


if __name__ == "__main__":
    main()


def test_theory_source_freeze_and_calibration_drift_use_real_contract(rig):
    source = rig.root / "theory/v1"
    (source / "waveforms").mkdir(parents=True)
    (source / "parameters").mkdir()
    time_axis = np.arange(16) / 1600.
    np.savetxt(source / "waveforms/optimal_control_waveform.csv",
        np.column_stack((time_axis, 50 * np.sin(2 * np.pi * 100 * time_axis))), delimiter=",")
    (source / "parameters/optimal_control_params.csv").write_text("# Output version,v1\nf_rf_Hz,100\n", encoding="utf-8")
    calibration = rig.root / "data/Mx_Z_Field_Calibration/calibration/results/analysis.yaml"
    calibration.parent.mkdir(parents=True)
    payload = dict(experiment_id="mx-z-field-calibration", success=True, K_Z_Hz_per_V=1000.,
        f_0V_Hz=12000., linear_fit={"r_squared": .999})
    calibration.write_text(yaml.safe_dump(payload), encoding="utf-8")
    params = replace(rig.params, control_waveform_source="theory", control_results_root=str(source.parent),
        control_version="v1", z_calibration_source_run="calibration", z_aw_output_vpp=.2)
    ctx = workflow.Workflow(params)
    ctx.execute(through("A03"))
    assert ctx.sources[0] is None
    assert (ctx.directory.raw / "source_optimal_control_waveform.csv").is_file()
    assert ctx.state["control_source"]["mode"] == "theory"
    assert rig.aw_settings[-1] == (.2, 0.)
    payload["f_0V_Hz"] += 1
    calibration.write_text(yaml.safe_dump(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="来源文件已改变"):
        ctx.execute(["A04"])
    assert not rig.captures
    ctx.finish()
    assert_safe(rig)
