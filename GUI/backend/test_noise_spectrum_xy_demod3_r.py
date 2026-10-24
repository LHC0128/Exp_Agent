"""假仪器真实子进程会话、硬件互斥及显式目录历史分析回归。"""
from pathlib import Path
from threading import Event
import time
import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from lab_workflows.experiment_modules.noise_spectrum_xy_demod3_r.definition import ADAPTER
from lab_workflows.experiment_modules.noise_spectrum_xy_demod3_r.test_workflow import copy_config, small_params
from lab_workflows.experiment_modules.noise_spectrum_xy_demod3_r.test_analysis import synthetic_run
from .jobs import JobManager
from .noise_spectrum_xy_demod3_r import NoiseSpectrumXYDemod3RSessionService, create_router

ID = "noise-spectrum-xy-demod3-r"
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
    relative = "lab_workflows/experiment_modules/noise_spectrum_xy_demod3_r/workflow.excalidraw"
    diagram = tmp_path / relative
    diagram.parent.mkdir(parents=True)
    diagram.write_bytes((Path(__file__).resolve().parents[2] / relative).read_bytes())
    manager = JobManager()
    service = NoiseSpectrumXYDemod3RSessionService(lambda: tmp_path, manager)
    original = ADAPTER._execute
    def execute(module, **kwargs):
        if module.endswith(".session"):
            module = "lab_workflows.experiment_modules.noise_spectrum_xy_demod3_r.test_workflow"
        return original(module, **kwargs)
    monkeypatch.setattr(ADAPTER, "_execute", execute)
    monkeypatch.setenv("DEMOD3_TEST_ROOT", str(tmp_path))
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
    response = client.post(f"{BASE}/sessions/{session_id}/steps", json={
        "revision": client.service.get(session_id)["revision"], "step_ids": ids})
    assert response.status_code == 200, response.text
    wait(lambda: client.service.get(session_id)["pending_request"] is None)
    state = client.service.get(session_id)
    assert state["error"] is None, state
    return state


def test_child_revision_dependencies_lock_rerun_and_finish(client):
    flow = client.get(BASE + "/workflow").json()
    assert len(flow["acquisition"]) == 12 and len(flow["analysis"]) == 3
    assert client.get(BASE + "/workflow/diagram").json()["type"] == "excalidraw"
    sid = create(client)
    assert client.manager.hardware_lock.locked()
    assert client.get(BASE + "/sessions/active").json()["session_id"] == sid
    assert client.post(BASE + "/sessions", json={"parameters": small_params().to_external()}).status_code == 409
    assert client.post(f"{BASE}/sessions/{sid}/steps", json={"revision": 0, "step_ids": ["A11"]}).status_code == 422
    state = steps(client, sid, [f"A{i:02d}" for i in range(1, 11)])
    assert client.post(f"{BASE}/sessions/{sid}/finish", json={"revision": 0}).status_code == 409
    assert client.get(f"{BASE}/sessions/{sid}").json()["revision"] == state["revision"]
    assert NoiseSpectrumXYDemod3RSessionService(lambda: client.root, client.manager).active() is None
    assert client.get(f"{BASE}/runs/{state['run_id']}/analysis").status_code == 409
    state = steps(client, sid, ["A11"])
    first = client.root / "data" / ADAPTER.data_type / state["run_id"]
    frozen = (first / "raw/demod3_r_mean_matrix.npz").read_bytes()
    state = steps(client, sid, [f"A{i:02d}" for i in range(4, 12)])
    assert state["run_id"] != first.name and (first / "raw/demod3_r_mean_matrix.npz").read_bytes() == frozen
    assert state["safety_shutdown"]["completed"]
    assert client.post(f"{BASE}/sessions/{sid}/finish", json={"revision": state["revision"]}).status_code == 200
    wait(lambda: client.service.get(sid)["status"] == "closed")
    wait(lambda: not client.manager.hardware_lock.locked())
    assert client.service.get(sid)["steps"]["A12"]["status"] == "completed"


@pytest.mark.parametrize("method", ["cancel", "eof", "shutdown"])
def test_cancel_eof_shutdown_restores_before_unlock(client, method):
    sid = create(client)
    state = steps(client, sid, [f"A{i:02d}" for i in range(1, 11)])
    if method == "cancel":
        client.manager.get(state["job_id"]).cancellation.cancel()
    elif method == "eof":
        client.service.close_communication()
    else:
        client.service.shutdown()
    wait(lambda: client.service.get(sid)["status"] == "closed")
    wait(lambda: not client.manager.hardware_lock.locked())
    final = client.service.get(sid)
    assert final["safety_shutdown"]["completed"]
    assert final["outputs"]["双路外触发"] == "OFF" and final["outputs"]["温控"] == "ON"


def test_history_readonly_analysis_child_directory_isolation_and_lock(client, monkeypatch):
    base = client.root / "data" / ADAPTER.data_type
    run, other = synthetic_run(base / "old"), synthetic_run(base / "other")
    frozen = {p:p.read_bytes() for p in run.rglob("*") if p.is_file()}
    assert client.get(BASE + "/runs?limit=50&offset=0").json()["total"] == 2
    summary = client.get(f"{BASE}/runs/{run.name}/summary").json()
    assert summary["parameters"]["CONTROL_FREQ_POINTS"] == 61 and not summary["artifacts"]
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
    assert summary["analysis_status"] == "completed" and any(name.endswith("r_spectra.png") for name in summary["artifacts"])
    assert client.get(next(iter(summary["artifacts"].values()))).status_code == 200
    assert {p:p.read_bytes() for p in frozen} == frozen


def test_legacy_snapshot_migration_and_identity_rejection(client):
    run = synthetic_run(client.root / "data" / ADAPTER.data_type / "legacy", schema=1, raw_version=None)
    path = run / "experiment_config.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config["parameters"].pop("SYSTEM_SETTLE_S")
    config["parameters"].update(DEMOD3_FREQ_SETTLE_TIME_S=0.8, XY_AW_OUTPUT_VPP=5)
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    before = path.read_bytes()
    response = client.get(f"{BASE}/runs/{run.name}/summary")
    assert response.status_code == 200, response.text
    assert response.json()["parameters"]["SYSTEM_SETTLE_S"] == 0.8
    assert "XY_AW_OUTPUT_VPP" not in response.json()["parameters"]
    assert path.read_bytes() == before
    config["experiment_id"] = "other"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    assert client.get(f"{BASE}/runs/{run.name}/summary").status_code == 422
    assert client.get(BASE + "/runs").json()["total"] == 0


def test_main_discovery_lifespan_preflight_and_legacy_analysis_lock(client, monkeypatch):
    from . import main
    monkeypatch.setattr(main, "ROOT", client.root)
    monkeypatch.setattr(main, "manager", client.manager)
    monkeypatch.setattr(main, "demod3_r_sessions", client.service)
    http = TestClient(main.app)
    definition = http.get(BASE).json()
    assert definition["id"] == ID and "节点" in definition["description"]
    assert http.get(BASE + "/workflow").status_code == 200
    assert client.service in main._workflow_session_services()
    paths = main.app.openapi()["paths"]
    assert BASE + "/sessions/{session_id}/finish" in paths and BASE + "/runs/{run_id}/summary" in paths
    run = synthetic_run(client.root / "data" / ADAPTER.data_type / "generic")
    entered, release, execute = Event(), Event(), ADAPTER._execute
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
