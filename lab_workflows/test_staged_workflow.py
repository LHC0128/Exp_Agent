"""共享分步骨架的执行循环：配置写入失败须使节点失败，不先标完成；节点内声明的终态不被覆盖，
封存后是否回写运行配置可按类切换，取消分类可收窄。不连接硬件。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from . import staged_workflow
from .common import WorkflowCancelled
from .steps.run_directory import RunDirectory
from .workflow_stages import Stage

STAGES = (Stage("A01", "预检", "准备", ()), Stage("A02", "测量", "测量", ("A01",)), Stage("A03", "结束", "结束", ()))
LONG_STAGES = (*STAGES[:2], Stage("A02B", "复核", "测量", ("A02",)), STAGES[2])


class Params:
    run_tag, schema_version = "t", 1

    def to_external(self):
        return {"RUN_TAG": "t"}


class Directory:
    def __init__(self, root, fail=False):
        self.root, self.fail, self.writes = root, fail, []

    def update_config(self, **values):
        if self.fail:
            raise OSError("模拟配置写入失败")
        self.writes.append(dict(values))


class Flow(staged_workflow.StagedWorkflow):
    stages, finish_id, run_id = STAGES, "A03", "A02"
    run_format = SimpleNamespace(initial_metadata=lambda p: {"completion_status": "running"}, DATA_TYPE="t")
    seal_after_run = False

    def preflight(self, params):
        return params, {}

    def _configuration_revision(self):
        return "fixed"

    def run_step(self, item):
        self.ran.append(item)

    def shutdown(self, failure):
        return []

    def publish(self, message=""):
        pass


def flow(tmp_path, *, fail, cls=Flow):
    ctx = cls(Params(), project_root=tmp_path)
    ctx.ran = []
    ctx.directory = Directory(tmp_path, fail=fail)
    ctx.run_parameters = ctx.params.to_external()
    return ctx


def test_config_save_failure_marks_step_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(staged_workflow, "check_cancelled", lambda: None)
    ctx = flow(tmp_path, fail=True)
    with pytest.raises(OSError, match="模拟配置写入失败"):
        ctx.execute(["A01", "A02"])
    assert ctx.states["A01"]["status"] == "failed"  # 首个节点的配置写入即失败，不继续后继
    assert ctx.ran == [] and ctx.states["A02"]["status"] == "pending"
    assert ctx.state["completion_status"] == "failed" and "state_save_error" in ctx.state


def test_config_saved_before_completion_is_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(staged_workflow, "check_cancelled", lambda: None)
    ctx = flow(tmp_path, fail=False)
    ctx.execute(["A01", "A02"])
    assert ctx.ran == ["A02"] and ctx.states["A02"]["status"] == "completed"
    assert ctx.directory.writes[-1]["completion_status"] == "completed"


class TerminalFlow(Flow):
    """节点内自行声明终态（质量问题）后返回，不抛异常。"""

    stages = LONG_STAGES

    def run_step(self, item):
        super().run_step(item)
        self.states[item] = dict(status="quality_failed", error="XY 校相未收敛，禁止扫描")


def test_quality_failed_step_keeps_terminal_state_and_stops_loop(tmp_path, monkeypatch):
    monkeypatch.setattr(staged_workflow, "check_cancelled", lambda: None)
    ctx = flow(tmp_path, fail=False, cls=TerminalFlow)
    ctx.execute(["A01", "A02", "A02B"])
    assert ctx.states["A02"] == dict(status="quality_failed", error="XY 校相未收敛，禁止扫描")
    assert ctx.states["A01"]["status"] == "completed"
    # 后继节点保持 pending，且 execute 未再调用 run_step。
    assert ctx.states["A02B"]["status"] == "pending" and ctx.ran == ["A02"]


class FailingShutdownFlow(Flow):
    def shutdown(self, failure):
        return ["模拟安全收尾失败"]


def sealed_run_directory(tmp_path, monkeypatch):
    """真实 RunDirectory + 真实的 experiment_config.yaml，只对 update_config 计数。"""
    calls = []
    original = RunDirectory.update_config

    def counting(self, **values):
        calls.append(dict(values))
        return original(self, **values)

    monkeypatch.setattr(RunDirectory, "update_config", counting)
    root = tmp_path / "run"
    root.mkdir()
    config = root / "experiment_config.yaml"
    config.write_text("completion_status: running\nfailure_reason: null\n", encoding="utf-8")
    directory = RunDirectory(root=root, raw=root / "raw", results=root / "results", config_path=config)
    return directory, config, calls


def test_sealed_failure_does_not_write_back_when_disabled(tmp_path, monkeypatch):
    directory, config, calls = sealed_run_directory(tmp_path, monkeypatch)
    ctx = flow(tmp_path, fail=False, cls=FailingShutdownFlow)
    ctx.directory, ctx.round_sealed, ctx.sealed_failure_write_back = directory, True, False
    before = config.read_bytes()
    with pytest.raises(RuntimeError, match="安全收尾失败"):
        ctx.finish()
    assert [call for call in calls if call.get("completion_status") == "failed"] == []
    assert config.read_bytes() == before


def test_sealed_failure_keeps_write_back_by_default(tmp_path, monkeypatch):
    directory, config, calls = sealed_run_directory(tmp_path, monkeypatch)
    ctx = flow(tmp_path, fail=False, cls=FailingShutdownFlow)
    ctx.directory, ctx.round_sealed = directory, True
    with pytest.raises(RuntimeError, match="安全收尾失败"):
        ctx.finish()
    assert [call["completion_status"] for call in calls] == ["failed"]
    assert "completion_status: failed" in config.read_text(encoding="utf-8")


class RaisingFlow(Flow):
    error = None

    def run_step(self, item):
        super().run_step(item)
        raise self.error


@pytest.mark.parametrize("cancelled_errors, error, expected", [
    ((WorkflowCancelled,), KeyboardInterrupt(), "failed"),
    ((WorkflowCancelled,), WorkflowCancelled("用户取消"), "cancelled"),
    (None, KeyboardInterrupt(), "cancelled"),
    (None, WorkflowCancelled("用户取消"), "cancelled"),
])
def test_cancelled_errors_control_step_status(tmp_path, monkeypatch, cancelled_errors, error, expected):
    monkeypatch.setattr(staged_workflow, "check_cancelled", lambda: None)
    ctx = flow(tmp_path, fail=False, cls=RaisingFlow)
    ctx.error = error
    if cancelled_errors is not None:
        ctx.cancelled_errors = cancelled_errors
    with pytest.raises(type(error)):
        ctx.execute(["A01", "A02"])
    assert ctx.states["A01"]["status"] == "completed"
    assert ctx.states["A02"]["status"] == expected
    assert ctx.state["completion_status"] == expected


class SealedFlow(Flow):
    seal_after_run = True


@pytest.mark.parametrize("cancelled_errors, error, expected", [
    ((WorkflowCancelled,), KeyboardInterrupt(), "failed"),
    ((WorkflowCancelled,), WorkflowCancelled("用户取消"), "cancelled"),
    (None, KeyboardInterrupt(), "cancelled"),
    (None, WorkflowCancelled("用户取消"), "cancelled"),
])
def test_final_status_honours_cancelled_errors(tmp_path, cancelled_errors, error, expected):
    ctx = SealedFlow(Params(), project_root=tmp_path)
    if cancelled_errors is not None:
        ctx.cancelled_errors = cancelled_errors
    ctx.final_status(error, [])
    assert ctx.state["completion_status"] == expected


def patch_run_directory(monkeypatch, tmp_path, created):
    def create(experiment_type, run_tag, parameters, *, schema_version=1, project_root=None):
        directory = SimpleNamespace(root=tmp_path / f"run-{len(created)}", update_config=lambda **values: None)
        created.append((experiment_type, parameters, directory))
        return directory

    monkeypatch.setattr(staged_workflow, "create_run_directory", create)


class RecordingDirectoryFlow(Flow):
    def _new_directory(self, selected=None):
        self.saw_selected = selected
        super()._new_directory(selected)


def test_new_directory_receives_selected(tmp_path, monkeypatch):
    monkeypatch.setattr(staged_workflow, "check_cancelled", lambda: None)
    created = []
    patch_run_directory(monkeypatch, tmp_path, created)
    ctx = flow(tmp_path, fail=False, cls=RecordingDirectoryFlow)
    ctx.directory = None
    ctx.execute(["A01", "A02"])
    assert ctx.saw_selected == ["A01", "A02"]
    assert ctx.directory is created[0][2] and ctx.ran == ["A02"]


def test_default_new_directory_ignores_selected(tmp_path, monkeypatch):
    monkeypatch.setattr(staged_workflow, "check_cancelled", lambda: None)
    created = []
    patch_run_directory(monkeypatch, tmp_path, created)
    ctx = flow(tmp_path, fail=False)
    ctx.directory = None
    ctx.execute(["A01", "A02"])
    assert ctx.directory is created[0][2] and ctx.ran == ["A02"]
    assert created[0][0] == "t"

