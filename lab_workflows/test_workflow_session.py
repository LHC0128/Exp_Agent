"""公共串行会话的微型协议、旧入口和无硬件子进程契约。"""

import importlib
import inspect
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from . import workflow_session
from .common import WorkflowCancelled
from .workflow_state import read_state, write_state


class SessionContext:
    """只记录编排动作；不构造仪器或执行测量。"""

    def __init__(self):
        self.closed = False
        self.revision = 0
        self.states = {key: {"status": "pending"} for key in ("S01", "END")}
        self.executions, self.failures = [], []
        self.parameters = {}
        self.rechecks = 0

    def publish(self):
        pass

    def snapshot(self):
        return {"revision": self.revision, "closed": self.closed}

    def recheck(self):
        self.rechecks += 1

    def execute(self, selected):
        self.executions.append(selected)
        self.revision += 1
        if selected == ["END"]:
            self.finish()

    def update_parameters(self, values):
        self.parameters.update(values)
        self.revision += 1

    def finish(self, failure=None):
        self.failures.append(failure)
        self.closed = True


def validate(stages, states, selected):
    if any(key not in stages for key in selected):
        raise ValueError("缺少前置或未知步骤")
    return selected


@pytest.fixture
def protocol(monkeypatch):
    monkeypatch.delenv("LAB_CANCEL_FILE", raising=False)
    progress = []
    monkeypatch.setattr(workflow_session, "report_runtime_progress",
                        lambda *args, **kwargs: progress.append(kwargs["data"]))
    return SessionContext(), progress


def serve(ctx, stream):
    return workflow_session.serve(ctx, stream, stages=("S01", "END"), validate=validate, finish_id="END")


@pytest.mark.parametrize("ending", ["finish", "steps"])
def test_serial_commands_validation_revisions_and_finish(protocol, ending):
    ctx, progress = protocol
    commands = [
        "{",
        {"request_id": 123, "revision": 0, "action": "finish"},
        {"request_id": "missing", "revision": 0},
        {"request_id": "unknown", "revision": 0, "action": "unknown"},
        {"request_id": "blocked", "revision": 0, "action": "steps", "step_ids": ["blocked"]},
        {"request_id": "first", "revision": 0, "action": "steps", "step_ids": ["S01"]},
        {"request_id": "first", "revision": 1, "action": "steps", "step_ids": ["S01"]},
        {"request_id": "stale", "revision": 0, "action": "steps", "step_ids": ["S01"]},
        {"request_id": "params", "revision": 1, "action": "parameters", "parameters": {"gain": 2}},
        {"request_id": "end", "revision": 2, "action": ending, "step_ids": ["END"]},
    ]
    stream = io.StringIO("\n".join(item if isinstance(item, str) else json.dumps(item) for item in commands))
    assert serve(ctx, stream) is None
    results = [item["command_result"] for item in progress]
    assert [item["request_id"] for item in results] == [
        None, 123, "missing", "unknown", "blocked", "first", "first", "stale", "params", "end"]
    assert [item["error"] is None for item in results] == [
        False, False, False, False, False, True, False, False, True, True]
    assert "重复或无效" in results[6]["error"]
    assert "修订已改变" in results[7]["error"]
    assert ctx.executions == [["S01"]] + ([["END"]] if ending == "steps" else [])
    assert ctx.rechecks == 1 and ctx.parameters == {"gain": 2}
    assert ctx.revision == (3 if ending == "steps" else 2)
    assert ctx.closed and len(ctx.failures) == 2
    assert progress[-1]["workflow_session"] == ctx.snapshot()


@pytest.mark.parametrize("where", ["publish", "execute", "progress"])
def test_original_failure_always_reaches_finish(protocol, monkeypatch, where):
    ctx, _ = protocol
    error = KeyboardInterrupt("模拟执行中断") if where == "execute" else OSError("模拟会话失败")

    def fail(*args, **kwargs):
        raise error

    if where == "progress":
        monkeypatch.setattr(workflow_session, "report_runtime_progress", fail)
    else:
        monkeypatch.setattr(ctx, where, fail)
    stream = io.StringIO(json.dumps(dict(request_id="first", revision=0, action="steps", step_ids=["S01"])))
    with pytest.raises(type(error)) as caught:
        serve(ctx, stream)
    assert caught.value is error and ctx.failures == [error] and ctx.closed


def test_eof_marks_cancel_file_and_still_finishes(protocol, tmp_path, monkeypatch):
    ctx, _ = protocol
    cancel = tmp_path / "cancel"
    monkeypatch.setenv("LAB_CANCEL_FILE", str(cancel))
    # 同步 reader 仅用于消除 EOF 标记和主线程首个检查之间的竞态。
    monkeypatch.setattr(workflow_session, "Thread",
                        lambda *, target, daemon: SimpleNamespace(start=target))
    serve(ctx, io.StringIO(""))
    assert cancel.is_file() and ctx.closed
    assert len(ctx.failures) == 1 and isinstance(ctx.failures[0], WorkflowCancelled)


def test_external_cancel_keeps_original_exception(protocol, monkeypatch):
    ctx, _ = protocol
    error = WorkflowCancelled("模拟外部取消")
    monkeypatch.setattr(workflow_session, "Thread",
                        lambda **kwargs: SimpleNamespace(start=lambda: None))

    def cancel():
        raise error

    monkeypatch.setattr(workflow_session, "check_cancelled", cancel)
    with pytest.raises(WorkflowCancelled) as caught:
        serve(ctx, io.StringIO(""))
    assert caught.value is error and ctx.failures == [error] and ctx.closed


def test_finish_failure_is_not_silenced(protocol, monkeypatch):
    ctx, _ = protocol
    error = OSError("模拟收尾失败")

    def finish(failure=None):
        raise error

    monkeypatch.setattr(ctx, "finish", finish)
    with pytest.raises(OSError) as caught:
        serve(ctx, io.StringIO(""))
    assert caught.value is error


SESSION_ENTRIES = [
    ("noise_spectrum_xy", "A14"),
    ("detection_chain_frequency_response", "A08"),
    ("t2_calibration", "A07"),
    ("rf_sensitivity_direct_aw_frequency", "A07"),
    ("xy_direct_aw_dc_calibration", "A07"),
]


@pytest.mark.parametrize("name,finish_id", SESSION_ENTRIES)
def test_original_main_preserves_loading_and_public_wiring(protocol, monkeypatch, name, finish_id):
    ctx, _ = protocol
    module = importlib.import_module(f"lab_workflows.experiment_modules.{name}.session")
    parameters = object()
    monkeypatch.setenv("LAB_TYPED_PARAMETERS", json.dumps({"session_id": "contract", "RUN_TAG": "micro"}))

    def load(params_type):
        assert params_type.__module__ == f"lab_workflows.experiment_modules.{name}.models"
        assert json.loads(os.environ["LAB_TYPED_PARAMETERS"]) == {"RUN_TAG": "micro"}
        return parameters

    def construct(params, *, session_id):
        assert params is parameters and session_id == "contract"
        return ctx

    original = module.serve_commands
    assert original is workflow_session.serve

    def commands(context, stream, **kwargs):
        assert context is ctx
        assert kwargs == dict(stages=module.ACQUISITION, validate=module.validate_selection, finish_id=finish_id)
        return original(context, stream, **kwargs)

    monkeypatch.setattr(module, "load_runtime_params", load)
    monkeypatch.setattr(module, "Workflow", construct)
    monkeypatch.setattr(module, "serve_commands", commands)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(dict(request_id="end", revision=0, action="finish"))))
    module.main()
    assert ctx.closed and len(ctx.failures) == 2


@pytest.mark.parametrize("name,finish_id", SESSION_ENTRIES[:2])
def test_old_import_defaults_and_explicit_overrides(protocol, name, finish_id):
    ctx, _ = protocol
    module = importlib.import_module(f"lab_workflows.experiment_modules.{name}.session")
    defaults = inspect.signature(module.serve).parameters
    assert defaults["stages"].default is module.ACQUISITION
    assert defaults["validate"].default is module.validate_selection
    assert defaults["finish_id"].default == finish_id
    stream = io.StringIO(json.dumps(dict(request_id="end", revision=0, action="steps", step_ids=["END"])))
    module.serve(ctx, stream, stages=("S01", "END"), validate=validate, finish_id="END")
    assert ctx.executions == [["END"]] and ctx.rechecks == 0 and ctx.closed


def test_real_child_cancel_finishes_before_exit(tmp_path):
    cancel = tmp_path / "cancel"
    env = dict(os.environ, LAB_CANCEL_FILE=str(cancel))
    process = subprocess.Popen([sys.executable, "-X", "utf8", "-m", "lab_workflows.test_workflow_session", str(tmp_path)],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, encoding="utf-8", env=env)
    try:
        deadline = time.monotonic() + 10
        while not (tmp_path / "ready").exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert (tmp_path / "ready").is_file()
        cancel.touch()
        # 不先关闭 stdin，确保这是外部取消而不是 EOF 路径。
        process.wait(timeout=10)
        output, error = process.communicate()
        assert process.returncode != 0 and "WorkflowCancelled" in error, output + error
        state = read_state(tmp_path / "closed.json")
        assert state == {"closed": True, "failure_type": "WorkflowCancelled", "failure": "收到 GUI 安全停止请求"}
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate()


def cancel_child(root):
    """真实管道和取消文件，只替换与会话协议无关的仪器工作流。"""
    ctx = SessionContext()
    ctx.publish = lambda: (root / "ready").touch()
    finish = ctx.finish

    def close(failure=None):
        finish(failure)
        write_state(root / "closed.json", dict(closed=ctx.closed,
                    failure_type=type(failure).__name__, failure=str(failure)))

    ctx.finish = close
    serve(ctx, sys.stdin)


if __name__ == "__main__":
    cancel_child(Path(sys.argv[1]))
