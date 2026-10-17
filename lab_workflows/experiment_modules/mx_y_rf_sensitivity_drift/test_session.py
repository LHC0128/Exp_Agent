"""真子进程的会话协议；仅在显式临时根安装假仪器，保留真实 writer。"""
import json
import os
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from queue import Queue
from threading import Thread

import numpy as np
import pytest

from ...common import CancellationToken
from ... import experiment_params
from ..mx_y_rf_sensitivity.test_workflow import copy_config, install_rig
from ..mx_y_rf_sensitivity import acquisition as child_acquisition
from . import workflow
from .definition import ADAPTER
from .models import MxYRFDriftParams

FAKE_SESSION_MODULE = "lab_workflows.experiment_modules.mx_y_rf_sensitivity_drift.test_session"


def wait_for(predicate, timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if value := predicate():
            return value
        time.sleep(.025)
    raise AssertionError("长飘假仪器子进程超时")


def test_real_child_rejects_duplicate_revision_and_dependencies_then_eof(tmp_path, monkeypatch):
    copy_config(tmp_path)
    monkeypatch.setenv("MX_Y_RF_DRIFT_TEST_ROOT", str(tmp_path))
    params = replace(MxYRFDriftParams(), y_rf_nt_per_vpp=10.)
    commands, events, failures = Queue(), [], []
    def run():
        try:
            ADAPTER._execute(FAKE_SESSION_MODULE, parameters=params.to_external() | {"session_id": "protocol"},
                commands=commands, progress=events.append, cancellation=CancellationToken())
        except BaseException as exc:
            failures.append(exc)
    thread = Thread(target=run)
    thread.start()
    try:
        wait_for(lambda: failures or any(event.data.get("workflow_session", {}).get("status") == "waiting" for event in events))
        assert not failures, "\n".join(event.message for event in events)
        def command(request, revision, selected):
            commands.put(dict(request_id=request, revision=revision, action="steps", step_ids=selected))
            return wait_for(lambda: next((event.data["command_result"] for event in reversed(events)
                if event.data.get("command_result", {}).get("request_id") == request), None))
        assert "前置" in command("missing", 0, ["A09"])["error"]
        assert command("first", 0, ["A01"])["error"] is None
        commands.put(dict(request_id="first", revision=1, action="steps", step_ids=["A01"]))
        wait_for(lambda: any("重复或无效" in event.message for event in events))
        assert "修订" in command("old", 0, ["A01"])["error"]
        assert command("connect", 1, ["A01", "A02", "A03", "A04", "A05", "A06", "A07"])["error"] is None
    finally:
        commands.put(None)
        thread.join(45)
    assert not thread.is_alive() and not failures
    final = [event.data["workflow_session"] for event in events if "workflow_session" in event.data][-1]
    assert final["status"] == "closed" and final["steps"]["A11"]["status"] == "completed"
    assert final["safety_shutdown"]["completed"]
    assert json.loads((tmp_path / "test-instrument-events.json").read_text(encoding="utf-8"))["safe"]


def fake_session_main():
    """必须先证明根目录是测试临时树，再替换全部仪器工厂。"""
    from .session import main
    root = Path(os.environ["MX_Y_RF_DRIFT_TEST_ROOT"]).resolve()
    assert root.is_relative_to(Path(tempfile.gettempdir()).resolve())
    assert root != ADAPTER.root.resolve() and (root / "params/devices.yaml").is_file()
    payload = json.loads(os.environ["LAB_TYPED_PARAMETERS"])
    payload.pop("session_id")
    params = MxYRFDriftParams.from_external(payload)
    with pytest.MonkeyPatch.context() as patch:
        rig = install_rig(root, patch, params)
        patch.setattr(workflow, "find_project_root", lambda: root)
        patch.setattr(experiment_params, "find_project_root", lambda: root)
        if os.environ.get("MX_Y_RF_DRIFT_INVALID_FIRST_CYCLE"):
            acquire = child_acquisition.daq.acquire_data
            def bad_first_cycle(*args, **kwargs):
                data = acquire(*args, **kwargs)
                if len(rig.captures) <= params.r_point_max_attempts:
                    for result in data:
                        result.values += np.linspace(-1, 1, result.values.size)
                return data
            patch.setattr(child_acquisition.daq, "acquire_data", bad_first_cycle)
        writer = np.savez
        def save(path, **data):
            assert rig.enabled, "子进程真实 writer 必须在恢复温控后调用"
            assert Path(path).resolve().is_relative_to(root)
            rig.events.append("save")
            if name := os.environ.get("MX_Y_RF_DRIFT_WRITER_GATE"):
                gate = Path(name)
                assert gate.resolve().is_relative_to(root)
                if not gate.with_suffix(".started").exists():
                    gate.with_suffix(".started").write_text("温控已恢复，等待真实写盘", encoding="utf-8")
                    wait_for(gate.exists, timeout=30)
            return writer(path, **data)
        patch.setattr(np, "savez", save)
        shutdown = workflow.safe_shutdown
        def cleanup(devices, channels, executed, **kwargs):
            gate_name = os.environ.get("MX_Y_RF_DRIFT_SHUTDOWN_GATE")
            if gate_name:
                gate = Path(gate_name)
                assert gate.resolve().is_relative_to(root)
                gate.with_suffix(".started").write_text("收尾开始", encoding="utf-8")
                wait_for(gate.exists, timeout=30)
            report = shutdown(devices, channels, executed, **kwargs)
            pump = rig.mapping["Pump_modulation"]
            trace = dict(events=rig.events, safe=report.completed,
                pump_frequency_hz=rig.generators[pump["resource"]].frequency.get(pump["channel"]),
                parameters=executed.to_external())
            (root / "test-instrument-events.json").write_text(json.dumps(trace), encoding="utf-8")
            if gate_name:
                gate.with_suffix(".finished").write_text(json.dumps(trace), encoding="utf-8")
            return report
        patch.setattr(workflow, "safe_shutdown", cleanup)
        main()


if __name__ == "__main__":
    fake_session_main()
