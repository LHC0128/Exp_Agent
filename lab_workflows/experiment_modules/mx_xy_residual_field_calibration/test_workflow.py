"""真实准备、逐次门控、完整 SDS 读数和真实落盘的假仪器回归。"""
from dataclasses import replace
from pathlib import Path
import json
import time
import numpy as np
import pytest
import yaml
from ...common import WorkflowCancelled
from ..mx_main_field_scope_noise_spectrum.test_workflow import copy_config, install_rig as install_scope_rig, assert_safe
from . import workflow, preparation, acquisition, stages
from .models import MxXYResidualFieldCalibrationParams

ACQUISITION_CASES = {
    "mx-xy-residual-field-calibration": {"mode": "single", "cases": {
        "order": ["test_real_order_slow_disk_and_forward_grid"],
        "slow_disk": ["test_real_order_slow_disk_and_forward_grid"],
        "cancel": ["test_failure_preserves_previous_points", "test_later_range_failure_rescues_first_frame"],
        "acquire_failure": ["test_failure_preserves_previous_points"],
        "restore_failure": ["test_restore_failure_rescues_frame_and_preserves_primary"],
        "save_failure": ["test_save_failure_rescues_current_frame"],
        "partial_save": ["test_failure_preserves_previous_points", "test_restore_failure_rescues_frame_and_preserves_primary", "test_save_failure_rescues_current_frame", "test_later_range_failure_rescues_first_frame"],
        "entrypoints": ["test_full_and_stepwise_share_order_and_rerun_preserves_raw"],
    }, "sources": [
        "lab_workflows/experiment_modules/mx_xy_residual_field_calibration/preparation.py",
        "lab_workflows/experiment_modules/mx_xy_residual_field_calibration/acquisition.py",
        "lab_workflows/experiment_modules/mx_xy_residual_field_calibration/workflow.py",
        "lab_workflows/experiment_modules/mx_xy_residual_field_calibration/session.py",
    ]},
}


def small_params():
    return replace(MxXYResidualFieldCalibrationParams(), x_field_points=3, y_field_points=7,
        scope_sample_rate_sa_s=8192, scope_duration_s=0.125, scope_initial_scale_v_div=0.1,
        system_settle_s=0.001, temp_switch_on_settle_s=0)


def install_rig(root, monkeypatch, params=None, *, progress=False):
    rig = install_scope_rig(root, monkeypatch, params or small_params(), progress=progress)
    monkeypatch.setattr(workflow, "find_project_root", lambda: root)
    original_check = acquisition.check_cancelled
    def check():
        original_check()
        if rig.cancel:
            raise WorkflowCancelled("模拟取消")
    def wait(seconds):
        rig.events.append("settle")
        check()
    for module in (workflow, preparation, acquisition):
        monkeypatch.setattr(module, "check_cancelled", check)
    monkeypatch.setattr(acquisition, "_sleep", wait)
    monkeypatch.setattr(preparation, "_sleep", lambda seconds: check())
    if not progress:
        monkeypatch.setattr(workflow, "report_runtime_progress", lambda *a, **kw: None)
        monkeypatch.setattr(acquisition, "report_runtime_progress", lambda *a, **kw: None)
    return rig


@pytest.fixture
def rig(tmp_path, monkeypatch):
    copy_config(tmp_path)
    return install_rig(tmp_path, monkeypatch)


def through(last):
    ids = [s.id for s in stages.ACQUISITION if s.id != stages.FINISH_ID]
    return ids[:ids.index(last) + 1]


def test_real_order_slow_disk_and_forward_grid(rig, monkeypatch):
    rig.params = replace(rig.params, point_repeats=2)
    original, text = np.savez_compressed, Path.write_text
    def write(path, **values):
        assert rig.enabled, "波形、元数据与快照只能在温控恢复后保存"
        rig.events.append("save")
        time.sleep(0.001)
        return original(path, **values)
    def write_text(path, *args, **kwargs):
        assert rig.enabled, "配置和流程状态不能在关闭窗口写入"
        return text(path, *args, **kwargs)
    monkeypatch.setattr(np, "savez_compressed", write)
    monkeypatch.setattr(Path, "write_text", write_text)
    progress = []
    monkeypatch.setattr(acquisition, "report_runtime_progress", lambda *a, **kw: progress.append(kw["data"]))
    directory = workflow.run(rig.params)
    assert_safe(rig)
    for i, event in enumerate(rig.events):
        if event == "acquire":
            assert rig.events[i-3:i] == ["off", "settle", "settle"]
            assert rig.events[i+1:i+3] == ["on", "save"]
    paths = sorted((directory / "raw").glob("grid_*.npz"))
    assert len(paths) == 42 and not list((directory / "raw").glob("recovered*"))
    for sequence, path in enumerate(paths):
        with np.load(path) as data:
            assert int(data["sequence_index"]) == sequence
            assert data["x_output_on"] and data["y_output_on"]
            assert data["offset_used_v"] == 0
    assert progress[0]["estimated_remaining_seconds"] is None
    assert progress[-1]["estimated_remaining_seconds"] == 0
    config = yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf8"))
    assert config["raw_format_version"] == 1 and config["schema_version"] == 3
    assert config["completion_status"] == "completed" and config["safety_shutdown"]["completed"]
    assert config["temperature_control"]["actual_temperature_c"] is None


@pytest.mark.parametrize("failure", [WorkflowCancelled("模拟取消"), OSError("模拟读取失败"), KeyboardInterrupt("中断")])
def test_failure_preserves_previous_points(rig, failure):
    rig.fault_index, rig.acquire_error = 1, failure
    ctx = workflow.Workflow(rig.params)
    with pytest.raises(type(failure), match=str(failure)):
        try:
            ctx.execute(through("A08"))
        finally:
            ctx.finish(failure)
    assert_safe(rig)
    assert len(list(ctx.directory.raw.glob("grid*"))) == 1
    assert ctx.state["failure_reason"] == str(failure)


@pytest.mark.parametrize("read_error", [False, True])
def test_restore_failure_rescues_frame_and_preserves_primary(rig, read_error):
    rig.restore_error = True
    if read_error:
        rig.fault_index, rig.acquire_error = 0, OSError("原始读取失败")
    ctx = workflow.Workflow(rig.params)
    message = "原始读取失败" if read_error else "温控恢复失败"
    with pytest.raises(OSError, match=message) as caught:
        try:
            ctx.execute(through("A08"))
        except BaseException as exc:
            ctx.finish(exc)
            raise
    assert len(rig.captures) == 1
    recovered = list(ctx.directory.raw.glob("recovered*"))
    assert len(recovered) == (0 if read_error else 1)
    if recovered:
        with np.load(recovered[0]) as values:
            assert values["voltage_v"].size == rig.params.scope_requested_points
            assert not values["accepted"]
    assert not ctx.state["safety_shutdown"]["completed"]
    assert ctx.state["completion_status"] != "completed"
    assert message in str(caught.value)


def test_save_failure_rescues_current_frame(rig, monkeypatch):
    original = acquisition._save_capture
    def fail(path, *args, **kwargs):
        if kwargs["sequence_index"] == 1:
            raise OSError("模拟磁盘写入失败")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(acquisition, "_save_capture", fail)
    ctx = workflow.Workflow(rig.params)
    with pytest.raises(OSError, match="磁盘写入失败"):
        try:
            ctx.execute(through("A08"))
        except BaseException as exc:
            ctx.finish(exc)
            raise
    assert_safe(rig)
    assert len(list(ctx.directory.raw.glob("grid*"))) == len(list(ctx.directory.raw.glob("recovered*"))) == 1


@pytest.mark.parametrize("failure", [OSError("后续量程采集失败"), WorkflowCancelled("后续量程取消")])
def test_later_range_failure_rescues_first_frame(rig, failure):
    rig.waveform_amplitude, rig.fault_index, rig.acquire_error = 0.01, 1, failure
    ctx = workflow.Workflow(rig.params)
    with pytest.raises(type(failure), match=str(failure)):
        try:
            ctx.execute(through("A08"))
        finally:
            ctx.finish(failure)
    assert_safe(rig)
    assert len(rig.captures) == 2
    assert len(list(ctx.directory.raw.glob("recovered*"))) == 1
    assert not list(ctx.directory.raw.glob("grid*"))


def test_full_and_stepwise_share_order_and_rerun_preserves_raw(rig):
    complete = workflow.run(rig.params)
    ctx = workflow.Workflow(rig.params)
    with pytest.raises(ValueError, match="缺少前置"):
        ctx.execute(["A08"])
    ctx.execute(through("A08"))
    first = ctx.directory.root
    frozen = {p.name: p.read_bytes() for p in ctx.directory.raw.glob("*")}
    full_paths = sorted((complete / "raw").glob("grid*"))
    for a, b in zip(full_paths, sorted(ctx.directory.raw.glob("grid*")), strict=True):
        with np.load(a) as da, np.load(b) as db:
            for key in ("x_field_v", "y_field_v", "sequence_index", "repeat_index", "voltage_v"):
                np.testing.assert_array_equal(da[key], db[key])
    ctx.execute(["A08"])
    assert ctx.directory.root != first
    assert {p.name: p.read_bytes() for p in (first / "raw").glob("*")} == frozen
    ctx.execute(["A09"])
    assert_safe(rig)
    sealed_directory = ctx.directory.root
    sealed = ctx.directory.config_path.read_bytes()
    assert json.loads((sealed_directory / "acquisition_state.json").read_text(encoding="utf8"))["steps"]["A09"]["status"] == "completed"
    with pytest.raises(ValueError, match="缺少前置"):
        ctx.execute(["A08"])
    ctx.execute([f"A{i:02d}" for i in range(4, 10)])
    assert_safe(rig)  # guard 在第二轮必须重新注册，不能只恢复第一轮。
    ctx.update_parameters({"PUMP_CARRIER_AMPLITUDE_VPP": 0.1})
    assert ctx.states["A06"]["status"] == "stale"
    ctx.finish()
    cfg = rig.mapping["Pump_modulation"]
    assert rig.generators[cfg["resource"]].amplitude[cfg["channel"]] == rig.params.pump_carrier_amplitude_vpp
    assert (sealed_directory / "experiment_config.yaml").read_bytes() == sealed


def test_preflight_parameter_impacts_and_executed_shutdown(rig):
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A07"))
    before = dict(ctx.states)
    ctx.execute(["A01"])
    assert {k:v for k,v in ctx.states.items() if k != "A01"} == {k:v for k,v in before.items() if k != "A01"}
    ctx.update_parameters({"PUMP_CARRIER_AMPLITUDE_VPP": 0.1, "SCOPE_DURATION": 0.25})
    impacts = stages.definition()["parameter_impacts"]["acquisition"]
    expected = set(impacts["PUMP_CARRIER_AMPLITUDE_VPP"]) | set(impacts["SCOPE_DURATION"])
    assert {k for k,v in ctx.states.items() if v["status"] == "stale"} == expected.intersection(through("A07"))
    ctx.finish()
    cfg = rig.mapping["Pump_modulation"]
    assert rig.generators[cfg["resource"]].amplitude[cfg["channel"]] == rig.params.pump_carrier_amplitude_vpp
    assert_safe(rig)


def test_clock_failure_and_configuration_change_stop_before_capture(rig):
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A07"))
    rig.clock_error = True
    with pytest.raises(RuntimeError, match="时钟"):
        try:
            ctx.execute(["A03"])
        except BaseException as exc:
            ctx.finish(exc)
            raise
    assert not rig.captures and rig.enabled
    ctx = workflow.Workflow(rig.params)
    path = rig.root / "params/safety_limits.yaml"
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(RuntimeError, match="已改变"):
        ctx.execute(["A01"])
    ctx.finish()


def test_legacy_keys_yaml_and_scope_only_preflight(rig, monkeypatch):
    from .definition import ADAPTER, DEFINITION
    old = MxXYResidualFieldCalibrationParams.from_external({"TEMP_SWITCH_OFF_SETTLE_S": 0.8,
        "REFERENCE_MAIN_FIELD_MA": 10, "REFERENCE_REPEATS": 3}, schema_version=1)
    assert old.system_settle_s == 0.8
    explicit = MxXYResidualFieldCalibrationParams.from_external({"SYSTEM_SETTLE_S": 0.2, "TEMP_SWITCH_OFF_SETTLE_S": 0.8})
    assert explicit.system_settle_s == 0.2
    assert ADAPTER.params_type.from_external(ADAPTER.defaults().to_external()).to_external() == ADAPTER.defaults().to_external()
    assert "scope_waveform" in DEFINITION.required_mapping_keys
    assert not any(key.startswith("lockin_") for key in DEFINITION.required_mapping_keys)
    monkeypatch.setattr(ADAPTER, "root", rig.root)
    monkeypatch.setattr(ADAPTER, "defaults_path", rig.root / "params/experiments/missing.yaml")
    assert ADAPTER.preflight(rig.params.to_external()) == []
    assert "X_FIELD_POINTS" in ADAPTER.preflight({"X_FIELD_POINTS": 1})[0]


@pytest.mark.parametrize("key,value", [("SCOPE_DURATION", float("nan")), ("PUMP_CARRIER_FREQUENCY_HZ", float("inf"))])
def test_nonfinite_parameters_fail_without_connecting(rig, key, value):
    field = type(rig.params).external_names()[key]
    params = replace(rig.params, **{field: value})
    with pytest.raises(ValueError, match="有限"):
        workflow.Workflow(params)
    assert not rig.events


def test_final_disconnect_failure_is_not_safe_success(rig):
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A09"))
    def fail():
        raise OSError("TEC 断开失败")
    rig.tec.disconnect = fail
    with pytest.raises(RuntimeError, match="TEC"):
        ctx.finish()
    assert ctx.closed and not ctx.snapshot()["safety_shutdown"]["completed"]
    assert ctx.states[stages.FINISH_ID]["status"] == "failed"


def fake_session_main():
    """正式 API 测试显式选择的假仪器入口；复用生产 stdin 协议。"""
    import os
    import sys
    from pytest import MonkeyPatch
    from .session import serve
    payload = json.loads(os.environ["LAB_TYPED_PARAMETERS"])
    session_id = payload.pop("session_id")
    root = Path(os.environ["MX_XY_TEST_ROOT"])
    params = MxXYResidualFieldCalibrationParams.from_external(payload)
    with MonkeyPatch.context() as patch:
        install_rig(root, patch, params, progress=True)
        serve(workflow.Workflow(params, project_root=root, session_id=session_id), sys.stdin)


if __name__ == "__main__":
    fake_session_main()
