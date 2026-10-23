"""Probe/PZT API 的假仪器真实子进程、互斥、历史及独立分析。"""
from pathlib import Path
from threading import Event
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lab_workflows.experiment_modules.mx_y_rf_probe_detuning_optimization.definition import ADAPTER
from lab_workflows.experiment_modules.mx_y_rf_probe_detuning_optimization.test_workflow import parameters
from lab_workflows.experiment_modules.mx_y_rf_probe_detuning_optimization.test_analysis import synthetic_run
from lab_workflows.experiment_modules.mx_y_rf_sensitivity.test_workflow import copy_config
from .jobs import JobManager
from .mx_y_rf_probe_detuning_optimization import MxYRFProbeDetuningSessionService
from .workflow_sessions import create_router

ID = "mx-y-rf-probe-detuning-optimization"
BASE = "/api/experiments/" + ID


def wait(predicate):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.025)
    raise AssertionError("假仪器子进程超时")


@pytest.fixture
def client(tmp_path, monkeypatch):
    copy_config(tmp_path)
    relative = "lab_workflows/experiment_modules/mx_y_rf_probe_detuning_optimization/workflow.excalidraw"
    diagram = tmp_path / relative
    diagram.parent.mkdir(parents=True)
    diagram.write_bytes((Path(__file__).resolve().parents[2] / relative).read_bytes())
    manager = JobManager()
    service = MxYRFProbeDetuningSessionService(lambda: tmp_path, manager)
    original = ADAPTER._execute

    def execute(module, **kwargs):
        if module.endswith(".session"):
            module = "lab_workflows.experiment_modules.mx_y_rf_probe_detuning_optimization.test_workflow"
        return original(module, **kwargs)

    monkeypatch.setattr(ADAPTER, "_execute", execute)
    monkeypatch.setenv("PROBE_DETUNING_TEST_ROOT", str(tmp_path))
    app = FastAPI()
    app.include_router(create_router(service, include_history=True))
    with TestClient(app) as http:
        http.service, http.manager, http.root = service, manager, tmp_path
        yield http
    service.shutdown()
    wait(lambda: all(job.status not in ("queued", "running") for job in manager.jobs.values()))


def create(client):
    response = client.post(BASE + "/sessions", json={"parameters": parameters().to_external()})
    assert response.status_code == 200, response.text
    session_id = response.json()["session_id"]
    wait(lambda: client.service.get(session_id)["status"] in ("waiting", "closed"))
    assert client.service.get(session_id)["status"] == "waiting", client.service.get(session_id)
    return session_id


def steps(client, session_id, ids):
    revision = client.service.get(session_id)["revision"]
    response = client.post(f"{BASE}/sessions/{session_id}/steps", json={"revision": revision, "step_ids": ids})
    assert response.status_code == 200, response.text
    wait(lambda: client.service.get(session_id)["pending_request"] is None)
    state = client.service.get(session_id)
    assert state["error"] is None, state
    return state


def test_real_child_dependency_revision_refresh_and_global_lock(client):
    flow = client.get(BASE + "/workflow")
    assert flow.status_code == 200, flow.text
    assert len(flow.json()["acquisition"]) == 9 and len(flow.json()["analysis"]) == 2
    assert client.get(BASE + "/workflow/diagram").json()["type"] == "excalidraw"
    session_id = create(client)
    assert client.manager.hardware_lock.locked()
    assert client.get(BASE + "/sessions/active").json()["session_id"] == session_id
    assert client.post(BASE + "/sessions", json={"parameters": {}}).status_code == 409
    assert client.post(f"{BASE}/sessions/{session_id}/steps", json={"revision": 0, "step_ids": ["A07"]}).status_code == 422
    state = steps(client, session_id, [f"A{i:02d}" for i in range(1, 7)])
    assert client.post(f"{BASE}/sessions/{session_id}/finish", json={"revision": 0}).status_code == 409
    assert client.get(f"{BASE}/sessions/{session_id}").json()["revision"] == state["revision"]
    assert MxYRFProbeDetuningSessionService(lambda: client.root, client.manager).active() is None
    assert client.get(f"{BASE}/runs/{state['run_id']}/analysis").status_code == 409
    state = steps(client, session_id, ["A07", "A08"])
    assert state["safety_shutdown"]["completed"]
    response = client.post(f"{BASE}/sessions/{session_id}/finish", json={"revision": state["revision"]})
    assert response.status_code == 200
    wait(lambda: client.service.get(session_id)["status"] == "closed")
    wait(lambda: not client.manager.hardware_lock.locked())
    assert client.service.get(session_id)["steps"]["A09"]["status"] == "completed"


@pytest.mark.parametrize("method", ["cancel", "eof", "shutdown"])
def test_cancel_eof_and_shutdown_restore_pzt_before_unlock(client, method):
    session_id = create(client)
    state = steps(client, session_id, [f"A{i:02d}" for i in range(1, 7)])
    if method == "cancel":
        client.manager.get(state["job_id"]).cancellation.cancel()
    elif method == "eof":
        client.service.close_communication()
    else:
        client.service.shutdown()
    wait(lambda: client.service.get(session_id)["status"] == "closed")
    wait(lambda: not client.manager.hardware_lock.locked())
    final = client.service.get(session_id)
    assert final["safety_shutdown"]["completed"]
    assert final["outputs"]["PZT/扫描"] == "已恢复进入会话前状态"
    assert final["outputs"]["Y RF"] == "OFF"


def test_history_readonly_snapshots_and_real_analysis_directory_isolation(client, monkeypatch):
    base = client.root / "data" / ADAPTER.data_type
    run = synthetic_run(base / "1018_test")
    other = synthetic_run(base / "1018_other")
    raw = {path: path.read_bytes() for path in run.rglob("*") if path.is_file()}
    listing = client.get(BASE + "/runs?limit=50&offset=0")
    assert listing.status_code == 200 and listing.json()["total"] == 2
    summary = client.get(BASE + f"/runs/{run.name}/summary").json()
    assert summary["parameters"]["PROBE_POWER_POINTS"] == 1
    assert summary["analysis_status"] == "not_started" and not summary["artifacts"]
    assert client.get(BASE + "/runs/missing/summary").status_code == 404
    assert client.get(BASE + "/runs/..%2Fescape/summary").status_code == 404
    state = client.get(BASE + f"/runs/{run.name}/analysis").json()
    assert {path: path.read_bytes() for path in raw} == raw
    body = dict(revision=state["revision"], step_ids=["B01", "B02"], analysis_parameters={"LOW_FREQ_SKIP_HZ": 5})
    execute, entered, release = ADAPTER._execute, Event(), Event()

    def blocked(module, **kwargs):
        if module == ADAPTER.analysis_module:
            entered.set()
            assert release.wait(30)
        return execute(module, **kwargs)

    monkeypatch.setattr(ADAPTER, "_execute", blocked)
    try:
        response = client.post(BASE + f"/runs/{run.name}/analysis", json=body)
        assert response.status_code == 200, response.text
        job_id = response.json()["id"]
        assert entered.wait(5)
        assert client.post(BASE + f"/runs/{run.name}/analysis", json=body).status_code == 409
    finally:
        release.set()
    wait(lambda: client.manager.get(job_id).status in ("completed", "failed"))
    assert client.manager.get(job_id).status == "completed", client.manager.get(job_id).error
    assert client.get(BASE + f"/runs/{run.name}/analysis").json()["options"]["LOW_FREQ_SKIP_HZ"] == 5
    assert client.get(BASE + f"/runs/{other.name}/analysis").json()["options"]["LOW_FREQ_SKIP_HZ"] == 3
    assert client.post(BASE + f"/runs/{run.name}/analysis", json=body).status_code == 409
    refreshed = client.get(BASE + f"/runs/{run.name}/summary").json()
    assert refreshed["analysis_status"] == "completed"
    assert refreshed["parameters"]["LOW_FREQ_SKIP_HZ"] == 3
    assert any(name.endswith("sensitivity_heatmap.png") for name in refreshed["artifacts"])
    first_file = next(iter(refreshed["artifacts"].values()))
    assert client.get(first_file).status_code == 200
    assert {path: path.read_bytes() for path in raw} == raw


def test_main_routes_discovery_and_lifespan_registration():
    from . import main
    paths = main.app.openapi()["paths"]
    assert BASE + "/workflow" in paths and BASE + "/runs/{run_id}/analysis" in paths
    assert main.mx_y_rf_probe_detuning_sessions in main._workflow_session_services()
    from lab_workflows.experiment_modules.mx_y_rf_probe_detuning_optimization.definition import DEFINITION
    assert main._experiment_public(DEFINITION)["id"] == ID
