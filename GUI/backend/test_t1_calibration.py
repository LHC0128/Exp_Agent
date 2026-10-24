"""真实假仪器子进程覆盖 T1 会话、全局锁、只读历史与分析互斥。"""
from pathlib import Path
from threading import Event
import sys
import time
import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from lab_workflows.experiment_modules.t1_calibration.definition import ADAPTER
from lab_workflows.experiment_modules.t1_calibration.test_workflow import copy_config, small_params
from lab_workflows.experiment_modules.t1_calibration.test_analysis import synthetic_run, frozen_inputs
from .jobs import JobManager
from .t1_calibration import T1SessionService, create_router

ID, BASE = "t1-calibration", "/api/experiments/t1-calibration"
ROOT = Path(__file__).resolve().parents[2]


def wait(predicate):
    deadline = time.monotonic()+90
    while time.monotonic() < deadline:
        if predicate(): return
        time.sleep(.025)
    raise AssertionError("假仪器子进程超时")


@pytest.fixture
def client(tmp_path, monkeypatch):
    copy_config(tmp_path)
    diagram = "lab_workflows/experiment_modules/t1_calibration/workflow.excalidraw"
    path = tmp_path/diagram; path.parent.mkdir(parents=True); path.write_bytes((ROOT/diagram).read_bytes())
    manager = JobManager(); service = T1SessionService(lambda: tmp_path, manager)
    execute = ADAPTER._execute
    def simulated(module, **kwargs):
        if module in (ADAPTER.acquisition_module, service.session_module):
            monkeypatch.setenv("T1_TEST_FULL", "1" if module == ADAPTER.acquisition_module else "0")
            module = "lab_workflows.experiment_modules.t1_calibration.test_workflow"
        return execute(module, **kwargs)
    monkeypatch.setattr(ADAPTER, "_execute", simulated)
    from lab_workflows.experiments import typed
    popen = typed.subprocess.Popen
    def start(command, **kwargs):
        kwargs["cwd"] = ROOT
        return popen([sys.executable, *command[1:]], **kwargs)
    monkeypatch.setattr(typed.subprocess, "Popen", start)
    monkeypatch.setattr(ADAPTER, "root", tmp_path)
    monkeypatch.setattr(ADAPTER, "defaults_path", tmp_path/"params/experiments/t1-calibration.yaml")
    small_params().save_yaml(ADAPTER.defaults_path)
    monkeypatch.setenv("T1_TEST_ROOT", str(tmp_path))
    app = FastAPI(); app.include_router(create_router(service))
    with TestClient(app) as http:
        http.service, http.manager, http.root = service, manager, tmp_path
        yield http
    service.shutdown()
    wait(lambda: all(j.status not in ("queued", "running") for j in manager.jobs.values()))


def create(client):
    result = client.post(BASE+"/sessions", json={"parameters": small_params().to_external()})
    assert result.status_code == 200, result.text
    sid = result.json()["session_id"]
    wait(lambda: client.service.get(sid)["status"] in ("waiting", "closed"))
    assert client.service.get(sid)["status"] == "waiting", client.service.get(sid)
    return sid


def steps(client, sid, ids):
    result = client.post(f"{BASE}/sessions/{sid}/steps", json={"revision": client.service.get(sid)["revision"], "step_ids": ids})
    assert result.status_code == 200, result.text
    wait(lambda: client.service.get(sid)["pending_request"] is None)
    state = client.service.get(sid); assert state["error"] is None, state
    return state


def test_dependencies_revision_global_lock_refresh_rerun_finish(client):
    definition = client.get(BASE+"/workflow").json()
    assert len(definition["acquisition"]) == 8 and len(definition["analysis"]) == 3
    assert client.get(BASE+"/workflow/diagram").json()["type"] == "excalidraw"
    sid = create(client); wait(lambda: client.manager.hardware_lock.locked())
    assert client.post(f"{BASE}/sessions/{sid}/steps", json={"revision": 0, "step_ids": ["A07"]}).status_code == 422
    assert client.post(f"{BASE}/sessions/{sid}/steps", json={"revision": 0, "step_ids": ["A01", "A01"]}).status_code == 422
    state = steps(client, sid, [f"A{i:02d}" for i in range(1, 7)])
    assert client.get(BASE+"/sessions/active").json()["session_id"] == sid
    assert T1SessionService(lambda: client.root, client.manager).active() is None
    assert client.post(f"{BASE}/sessions/{sid}/finish", json={"revision": 0}).status_code == 409
    assert client.get(f"{BASE}/runs/{state['run_id']}/analysis").status_code == 409
    state = steps(client, sid, ["A07"])
    first = client.root/"data/T1_Calibration"/state["run_id"]; before = frozen_inputs(first)
    assert state["outputs"]["X"] == "0 V + OFF"
    assert client.post(f"{BASE}/sessions/{sid}/steps", json={"revision": state["revision"], "step_ids": ["A06", "A07"]}).status_code == 422
    state = steps(client, sid, ["A04", "A05", "A06", "A07"])
    assert state["run_id"] != first.name and frozen_inputs(first) == before
    result = client.post(f"{BASE}/sessions/{sid}/finish", json={"revision": state["revision"]})
    assert result.status_code == 200
    wait(lambda: client.service.get(sid)["status"] == "closed")
    wait(lambda: not client.manager.hardware_lock.locked())
    assert client.service.get(sid)["safety_shutdown"]["completed"]


@pytest.mark.parametrize("action", ["cancel", "eof", "shutdown"])
def test_cancel_eof_shutdown_restore_before_unlock(client, action):
    sid = create(client); state = steps(client, sid, [f"A{i:02d}" for i in range(1, 7)])
    if action == "cancel": client.manager.get(state["job_id"]).cancellation.cancel()
    elif action == "eof": client.service.close_communication()
    else: client.service.shutdown()
    wait(lambda: client.service.get(sid)["status"] == "closed")
    wait(lambda: not client.manager.hardware_lock.locked())
    state = client.service.get(sid)
    assert state["safety_shutdown"]["completed"] and state["outputs"]["温控"] == "5 V DC + ON"
    assert state["outputs"]["X"] == "0 V + OFF" and "SYNC ON" in state["outputs"]["门控"]


def test_history_snapshot_missing_results_analysis_lock_and_immutability(client, monkeypatch):
    base = client.root/"data/T1_Calibration"
    run = synthetic_run(base/"legacy", legacy=True); synthetic_run(base/"other")
    before = frozen_inputs(run)
    assert client.get(BASE+"/runs").json()["total"] == 2
    summary = client.get(f"{BASE}/runs/legacy/summary").json()
    assert summary["parameters"]["PROBE_POWER_LIST"] == [.03, .06, .09] and summary["parameters"]["ACQ_REPEATS"] == 2
    assert not summary["artifacts"] and frozen_inputs(run) == before
    assert client.get(f"{BASE}/runs/missing/summary").status_code == 404
    assert client.get(f"{BASE}/runs/..%2Fescape/summary").status_code == 404
    state = client.get(f"{BASE}/runs/legacy/analysis").json()
    execute, entered, release = ADAPTER._execute, Event(), Event()
    def blocked(module, **kwargs):
        if module == ADAPTER.analysis_module:
            entered.set(); assert release.wait(30)
        return execute(module, **kwargs)
    monkeypatch.setattr(ADAPTER, "_execute", blocked)
    body = dict(revision=state["revision"], step_ids=["B01", "B02", "B03"], analysis_parameters={"T1_MAX_S": .4})
    try:
        response = client.post(f"{BASE}/runs/legacy/analysis", json=body)
        assert response.status_code == 200, response.text
        jid = response.json()["id"]; assert entered.wait(5)
        assert client.post(f"{BASE}/runs/legacy/analysis", json=body).status_code == 409
    finally: release.set()
    wait(lambda: client.manager.get(jid).status in ("completed", "failed"))
    assert client.manager.get(jid).status == "completed", client.manager.get(jid).error
    assert client.get(f"{BASE}/runs/other/analysis").json()["options"]["T1_MAX_S"] == .5
    assert client.post(f"{BASE}/runs/legacy/analysis", json=body).status_code == 409
    summary = client.get(f"{BASE}/runs/legacy/summary").json()
    assert summary["analysis_status"] == "completed" and "T1_all_decay_curves.png" in str(summary["artifacts"])
    assert client.get(next(iter(summary["artifacts"].values()))).status_code == 200
    assert frozen_inputs(run) == before


def test_strict_requests_identity_and_format(client):
    run = synthetic_run(client.root/"data/T1_Calibration/wrong")
    path = run/"experiment_config.yaml"; config = yaml.safe_load(path.read_text(encoding="utf-8")); config["raw_format_version"] = 99
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    assert client.get(f"{BASE}/runs/wrong/summary").status_code == 422
    assert client.get(BASE+"/runs").json()["total"] == 0
    assert client.post(BASE+"/sessions", json={"parameters": {"unknown": 1}}).status_code == 422
    assert client.post(BASE+"/sessions", json={"parameters": {}, "unknown": 1}).status_code == 422


def test_main_complete_autoanalysis_and_shared_reanalysis_lock(client, monkeypatch):
    from . import main
    from lab_workflows import common
    monkeypatch.setattr(main, "ROOT", client.root); monkeypatch.setattr(main, "manager", client.manager)
    monkeypatch.setattr(main, "t1_sessions", client.service)
    monkeypatch.setattr(common, "find_project_root", lambda: client.root)
    http = TestClient(main.app)
    assert ID in main.WORKFLOW_RUN_FORMATS and client.service in main._workflow_session_services()
    assert http.get(BASE).json()["execution_mode"] == "typed_workflow"
    response = http.post(BASE+"/runs", json={"parameters": small_params().to_external()})
    assert response.status_code == 200, response.text
    jid = response.json()["id"]
    wait(lambda: client.manager.get(jid).status in ("completed", "failed"))
    job = client.manager.get(jid)
    assert job.status == "completed", job.error
    assert (Path(job.result["run_dir"])/"results/T1_all_decay_curves.png").is_file()
    run = synthetic_run(client.root/"data/T1_Calibration/reanalyze"); before = frozen_inputs(run)
    execute, entered, release = ADAPTER._execute, Event(), Event()
    def blocked(module, **kwargs):
        entered.set(); assert release.wait(30); return execute(module, **kwargs)
    monkeypatch.setattr(ADAPTER, "_execute", blocked)
    try:
        response = http.post("/api/runs/reanalyze/analyze", json={"experiment_id": ID})
        assert response.status_code == 200, response.text
        jid = response.json()["id"]; assert entered.wait(5)
        assert client.post(f"{BASE}/runs/reanalyze/analysis", json={"revision": 0, "step_ids": ["B01"]}).status_code == 409
    finally: release.set()
    wait(lambda: client.manager.get(jid).status in ("completed", "failed"))
    assert client.manager.get(jid).status == "completed", client.manager.get(jid).error
    assert frozen_inputs(run) == before
