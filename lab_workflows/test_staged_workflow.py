"""共享分步骨架的执行循环：配置写入失败须使节点失败，不先标完成。不连接硬件。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from . import staged_workflow
from .workflow_stages import Stage

STAGES = (Stage("A01", "预检", "准备", ()), Stage("A02", "测量", "测量", ("A01",)), Stage("A03", "结束", "结束", ()))


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


def flow(tmp_path, *, fail):
    ctx = Flow(Params(), project_root=tmp_path)
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
