"""真实假仪器子进程、全局互斥、历史只读与独立分析 API。"""
from pathlib import Path
import sys
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lab_workflows.experiment_modules.coil_drive_characterization.definition import ADAPTER
from lab_workflows.experiment_modules.coil_drive_characterization.test_workflow import copy_config, small_params

from . import main
from .coil_drive_characterization import CoilDriveSessionService, create_router
from .jobs import JobManager

ID = "coil-drive-characterization"
BASE = "/api/experiments/" + ID


def wait(predicate):
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.025)
    raise AssertionError("假仪器子进程超时")


@pytest.fixture
def client(tmp_path, monkeypatch):
    copy_config(tmp_path)
    relative = "lab_workflows/experiment_modules/coil_drive_characterization/workflow.excalidraw"
    diagram = tmp_path / relative
    diagram.parent.mkdir(parents=True)
    diagram.write_bytes((Path(__file__).resolve().parents[2] / relative).read_bytes())
    manager = JobManager()
    service = CoilDriveSessionService(lambda: tmp_path, manager)
    original = ADAPTER._execute

    def execute(module, **kwargs):
        if module in (ADAPTER.acquisition_module, service.session_module):
            monkeypatch.setenv("COIL_DRIVE_TEST_FULL", "1" if module == ADAPTER.acquisition_module else "0")
            module = "lab_workflows.experiment_modules.coil_drive_characterization.test_workflow"
        return original(module, **kwargs)
    monkeypatch.setattr(ADAPTER, "_execute", execute)
    from lab_workflows.experiments import typed
    original_popen = typed.subprocess.Popen

    def popen(command, **kwargs):
        kwargs["cwd"] = Path(__file__).resolve().parents[2]
        return original_popen([sys.executable, *command[1:]], **kwargs)
    monkeypatch.setattr(typed.subprocess, "Popen", popen)
    monkeypatch.setattr(ADAPTER, "root", tmp_path)
    monkeypatch.setattr(ADAPTER, "defaults_path", tmp_path / f"params/experiments/{ID}.yaml")
    monkeypatch.setenv("COIL_DRIVE_TEST_ROOT", str(tmp_path))
    app = FastAPI()
    app.include_router(create_router(service))
    with TestClient(app) as http:
        http.service, http.manager, http.root = service, manager, tmp_path
        yield http
    service.shutdown()
    wait(lambda: all(job.status not in ("queued", "running") for job in manager.jobs.values()))


def create(client):
    response = client.post(BASE + "/sessions", json={"parameters": small_params().to_external()})
    assert response.status_code == 200, response.text
    sid = response.json()["session_id"]
    wait(lambda: client.service.get(sid)["status"] in ("waiting", "closed"))
    assert client.service.get(sid)["status"] == "waiting", client.service.get(sid)
    return sid


def steps(client, sid, ids):
    response = client.post(f"{BASE}/sessions/{sid}/steps",
                           json=dict(revision=client.service.get(sid)["revision"], step_ids=ids))
    assert response.status_code == 200, response.text
    wait(lambda: client.service.get(sid)["pending_request"] is None)
    state = client.service.get(sid)
    assert state["error"] is None, state
    return state


def test_registered_in_main_app():
    assert ID in main.WORKFLOW_RUN_FORMATS and main.coil_drive_sessions in main._workflow_session_services()


def test_session_steps_analysis_history_and_finish(client):
    flow = client.get(BASE + "/workflow").json()
    assert [s["id"] for s in flow["acquisition"]] == ["A01", "A02", "A03", "A04", "A05", "A06"]
    assert flow["run_step_id"] == "A05" and [s["id"] for s in flow["analysis"]] == ["B01", "B02"]
    assert client.get(BASE + "/workflow/diagram").json()["type"] == "excalidraw"
    sid = create(client)
    wait(lambda: client.manager.hardware_lock.locked())
    assert client.post(f"{BASE}/sessions/{sid}/steps", json=dict(revision=0, step_ids=["A04"])).status_code == 422
    state = steps(client, sid, ["A01", "A02", "A03", "A05"])
    assert state["steps"]["A05"]["status"] == "completed" and state["steps"]["A04"]["status"] == "pending"
    run_id = state["run_id"]
    # 模块完成即停止写入：会话等待时可以离线分析
    response = client.post(f"{BASE}/runs/{run_id}/analysis", json=dict(revision=0, step_ids=["B01", "B02"]))
    assert response.status_code == 200, response.text
    wait(lambda: client.get(f"{BASE}/runs/{run_id}/analysis").json()["summary"].get("status") in ("completed", "failed", "quality_failed"))
    analysis = client.get(f"{BASE}/runs/{run_id}/analysis").json()
    assert analysis["summary"]["status"] == "completed", analysis
    assert analysis["summary"]["shortest_usable_pulse_width_s"] == pytest.approx(20e-6)
    response = client.post(f"{BASE}/sessions/{sid}/finish", json={"revision": client.service.get(sid)["revision"]})
    assert response.status_code == 200, response.text
    wait(lambda: client.service.get(sid)["status"] == "closed")
    wait(lambda: not client.manager.hardware_lock.locked())
    final = client.service.get(sid)
    assert final["steps"]["A06"]["status"] == "completed" and final["safety_shutdown"]["completed"]
    assert final["outputs"]["功放"].startswith("OFF")
    runs = client.get(BASE + "/runs?limit=50&offset=0").json()
    assert runs["total"] == 1
    summary = client.get(f"{BASE}/runs/{run_id}/summary").json()
    assert summary["parameters"]["PULSE_WIDTHS_US"] == [5.0, 20.0, 100.0]
    assert "results/coil_drive.png" in summary["artifacts"]
    assert client.get(BASE + "/runs/..%2Fescape/summary").status_code == 404


@pytest.mark.parametrize("method", ["cancel", "eof"])
def test_cancel_or_eof_shuts_down_before_unlock(client, method):
    sid = create(client)
    state = steps(client, sid, ["A01", "A02", "A03"])
    if method == "cancel":
        client.manager.get(state["job_id"]).cancellation.cancel()
    else:
        client.service.close_communication()
    wait(lambda: client.service.get(sid)["status"] == "closed")
    wait(lambda: not client.manager.hardware_lock.locked())
    final = client.service.get(sid)
    assert final["safety_shutdown"]["completed"] and final["outputs"]["Z 通道"].startswith("DC 0 V + OFF")
