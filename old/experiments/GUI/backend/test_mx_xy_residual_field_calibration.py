"""假仪器真实子进程、硬件互斥、历史只读与独立分析接口回归。"""
from pathlib import Path
from threading import Event
import time
import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from lab_workflows.experiment_modules.mx_xy_residual_field_calibration.definition import ADAPTER
from lab_workflows.experiment_modules.mx_xy_residual_field_calibration.test_workflow import copy_config, small_params
from lab_workflows.experiment_modules.mx_xy_residual_field_calibration.test_analysis import synthetic_run
from .jobs import JobManager
from .mx_xy_residual_field_calibration import MxXYResidualFieldCalibrationSessionService, create_router

ID = "mx-xy-residual-field-calibration"
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
    relative = "lab_workflows/experiment_modules/mx_xy_residual_field_calibration/workflow.excalidraw"
    diagram = tmp_path / relative
    diagram.parent.mkdir(parents=True)
    diagram.write_bytes((Path(__file__).resolve().parents[2] / relative).read_bytes())
    manager = JobManager()
    service = MxXYResidualFieldCalibrationSessionService(lambda: tmp_path, manager)
    original = ADAPTER._execute
    def execute(module, **kwargs):
        if module.endswith(".session"):
            module = "lab_workflows.experiment_modules.mx_xy_residual_field_calibration.test_workflow"
        return original(module, **kwargs)
    monkeypatch.setattr(ADAPTER, "_execute", execute)
    monkeypatch.setenv("MX_XY_TEST_ROOT", str(tmp_path))
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


def test_child_revision_dependency_refresh_and_hardware_lock(client):
    flow = client.get(BASE + "/workflow")
    assert flow.status_code == 200
    assert len(flow.json()["acquisition"]) == 10 and len(flow.json()["analysis"]) == 3
    assert client.get(BASE + "/workflow/diagram").json()["type"] == "excalidraw"
    session_id = create(client)
    assert client.manager.hardware_lock.locked()
    assert client.get(BASE + "/sessions/active").json()["session_id"] == session_id
    assert client.post(BASE + "/sessions", json={"parameters": {}}).status_code == 409
    assert client.post(f"{BASE}/sessions/{session_id}/steps", json={"revision": 0, "step_ids": ["A07"]}).status_code == 422
    state = steps(client, session_id, [f"A{i:02d}" for i in range(1, 8)])
    assert client.post(f"{BASE}/sessions/{session_id}/finish", json={"revision": 0}).status_code == 409
    assert client.get(f"{BASE}/sessions/{session_id}").json()["revision"] == state["revision"]
    assert MxXYResidualFieldCalibrationSessionService(lambda: client.root, client.manager).active() is None
    assert client.get(f"{BASE}/runs/{state['run_id']}/analysis").status_code == 409
    state = steps(client, session_id, ["A08", "A09"])
    assert state["safety_shutdown"]["completed"]
    response = client.post(f"{BASE}/sessions/{session_id}/finish", json={"revision": state["revision"]})
    assert response.status_code == 200
    wait(lambda: client.service.get(session_id)["status"] == "closed")
    wait(lambda: not client.manager.hardware_lock.locked())
    assert client.service.get(session_id)["steps"]["A10"]["status"] == "completed"


@pytest.mark.parametrize("method", ["cancel", "eof", "shutdown"])
def test_cancel_eof_shutdown_restore_before_unlock(client, method):
    session_id = create(client)
    state = steps(client, session_id, [f"A{i:02d}" for i in range(1, 8)])
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
    assert final["outputs"]["GS200"] == "运行前状态" and final["outputs"]["Y"] == "OFF"


def test_history_readonly_directory_isolation_and_analysis_lock(client, monkeypatch):
    base = client.root / "data" / ADAPTER.data_type
    run, other = synthetic_run(base / "old"), synthetic_run(base / "other")
    frozen = {p: p.read_bytes() for p in run.rglob("*") if p.is_file()}
    listing = client.get(BASE + "/runs?limit=50&offset=0")
    assert listing.status_code == 200 and listing.json()["total"] == 2
    summary = client.get(f"{BASE}/runs/{run.name}/summary").json()
    assert summary["parameters"]["X_FIELD_POINTS"] == 5
    assert summary["analysis_status"] == "not_started" and not summary["artifacts"]
    assert client.get(BASE + "/runs/missing/summary").status_code == 404
    assert client.get(BASE + "/runs/..%2Fescape/summary").status_code == 404
    state = client.get(f"{BASE}/runs/{run.name}/analysis").json()
    assert state["options"] == {}
    assert {p:p.read_bytes() for p in frozen} == frozen
    body = dict(revision=state["revision"], step_ids=["B01", "B02", "B03"], analysis_parameters={})
    execute, entered, release = ADAPTER._execute, Event(), Event()
    def blocked(module, **kwargs):
        if module == ADAPTER.analysis_module:
            entered.set()
            assert release.wait(30)
        return execute(module, **kwargs)
    monkeypatch.setattr(ADAPTER, "_execute", blocked)
    try:
        response = client.post(f"{BASE}/runs/{run.name}/analysis", json=body)
        assert response.status_code == 200, response.text
        job_id = response.json()["id"]
        assert entered.wait(5)
        assert client.post(f"{BASE}/runs/{run.name}/analysis", json=body).status_code == 409
    finally:
        release.set()
    wait(lambda: client.manager.get(job_id).status in ("completed", "failed"))
    assert client.manager.get(job_id).status == "completed", client.manager.get(job_id).error
    assert client.get(f"{BASE}/runs/{other.name}/analysis").json()["summary"]["status"] == "not_started"
    assert client.post(f"{BASE}/runs/{run.name}/analysis", json=body).status_code == 409
    summary = client.get(f"{BASE}/runs/{run.name}/summary").json()
    assert summary["analysis_status"] == "completed"
    assert any(name.endswith("bloch_2d_fit.png") for name in summary["artifacts"])
    assert client.get(next(iter(summary["artifacts"].values()))).status_code == 200
    assert {p:p.read_bytes() for p in frozen} == frozen


def test_historical_parameter_projection_identity_and_missing_results(client):
    run = synthetic_run(client.root / "data" / ADAPTER.data_type / "legacy", schema=1)
    path = run / "experiment_config.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf8"))
    config["parameters"].pop("SYSTEM_SETTLE_S")
    config["parameters"].update(TEMP_SWITCH_OFF_SETTLE_S=0.8, REFERENCE_MAIN_FIELD_MA=10, REFERENCE_REPEATS=2)
    path.write_text(yaml.safe_dump(config), encoding="utf8")
    before = path.read_bytes()
    response = client.get(f"{BASE}/runs/{run.name}/summary")
    assert response.status_code == 200, response.text
    assert response.json()["parameters"]["SYSTEM_SETTLE_S"] == 0.8
    assert "REFERENCE_REPEATS" not in response.json()["parameters"]
    assert path.read_bytes() == before
    config["experiment_id"] = "other"
    path.write_text(yaml.safe_dump(config), encoding="utf8")
    assert client.get(f"{BASE}/runs/{run.name}/summary").status_code == 422
    assert client.get(BASE + "/runs").json()["total"] == 0


def test_main_routes_register_schema_and_lifespan(monkeypatch, client):
    from . import main
    monkeypatch.setattr(main, "ROOT", client.root)
    monkeypatch.setattr(main, "manager", client.manager)
    monkeypatch.setattr(main, "mx_xy_residual_sessions", client.service)
    http = TestClient(main.app)
    definition = http.get(BASE).json()
    assert definition["id"] == ID and "流程图" in definition["description"]
    assert http.get(BASE + "/workflow").status_code == 200
    assert client.service in main._workflow_session_services()
    paths = main.app.openapi()["paths"]
    assert BASE + "/sessions/{session_id}/finish" in paths
    assert BASE + "/runs/{run_id}/summary" in paths
    run = synthetic_run(client.root / "data" / ADAPTER.data_type / "generic")
    entered, release = Event(), Event()
    execute = ADAPTER._execute
    def blocked(module, **kwargs):
        entered.set()
        assert release.wait(30)
        return execute(module, **kwargs)
    monkeypatch.setattr(ADAPTER, "_execute", blocked)
    try:
        response = http.post(f"/api/runs/{run.name}/analyze", json={"experiment_id": ID})
        assert response.status_code == 200, response.text
        job_id = response.json()["id"]
        assert entered.wait(5)
        state = client.get(f"{BASE}/runs/{run.name}/analysis").json()
        assert client.post(f"{BASE}/runs/{run.name}/analysis", json={"revision":state["revision"],"step_ids":["B01"]}).status_code == 409
    finally:
        release.set()
    wait(lambda: client.manager.get(job_id).status in ("completed", "failed"))
    assert client.manager.get(job_id).status == "completed", client.manager.get(job_id).error
