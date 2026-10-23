"""父子目录封存提交失败时，不得把运行标成完整，且保留最初异常。"""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from ...common import WorkflowCancelled
from . import control_runs


@pytest.mark.parametrize("failure,rollback_fails", [
    ("none", False), ("child", False), ("parent", False), ("cancel", False),
    ("child", True), ("parent", True), ("cancel", True),
])
def test_seal_commits_children_first_and_never_hides_failure(monkeypatch, failure, rollback_fails):
    events, committed = [], []
    original = WorkflowCancelled("封存前取消") if failure == "cancel" else OSError("首次封存写入失败")
    parent = SimpleNamespace(state={"completion_status": "completed", "controls_sealed": False,
        "safety_shutdown": {"completed": True}}, controls={})
    checks = 0

    def check_cancelled():
        nonlocal checks
        checks += 1
        if failure == "cancel" and checks == 2:
            raise original

    monkeypatch.setattr(control_runs, "check_cancelled", check_cancelled)
    for version in ("v1", "v2"):
        child = SimpleNamespace(state={})

        def save_child(version=version):
            events.append(version)
            if failure == "child" and version == "v2":
                raise original

        child.save = save_child
        parent.controls[version] = child

    def save_parent():
        sealed = parent.state["controls_sealed"]
        events.append(("parent", sealed))
        if failure == "parent" and sealed:
            raise original
        if rollback_fails and not sealed:
            raise OSError("失败状态也无法写入")
        committed.append(deepcopy(parent.state))

    parent.save = save_parent
    if failure == "none":
        control_runs.seal_controls(parent)
        assert events == ["v1", "v2", ("parent", True)]
        assert committed[-1]["controls_sealed"] is True
        assert all(child.state["completion_status"] == "completed" for child in parent.controls.values())
    else:
        with pytest.raises(type(original)) as caught:
            control_runs.seal_controls(parent)
        assert caught.value is original
        assert parent.state["controls_sealed"] is False
        assert parent.state["completion_status"] == ("cancelled" if failure == "cancel" else "failed")
        assert parent.state["failure_reason"] == str(original)
        assert not any(state["controls_sealed"] for state in committed)
        assert ("state_save_error" in parent.state) is rollback_fails
