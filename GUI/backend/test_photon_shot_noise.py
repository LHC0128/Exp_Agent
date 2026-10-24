"""真实假仪器子进程、全局互斥、历史只读与独立分析 API。"""
from pathlib import Path
from threading import Event
import time
import sys
import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from lab_workflows.experiment_modules.photon_shot_noise.definition import ADAPTER
from lab_workflows.experiment_modules.photon_shot_noise.test_workflow import copy_config, small_params
from lab_workflows.experiment_modules.photon_shot_noise.test_analysis import synthetic_run, frozen_inputs
from .jobs import JobManager
from .photon_shot_noise import PhotonShotNoiseSessionService, create_router

ID = "photon-shot-noise"
BASE = "/api/experiments/" + ID


def wait(predicate):
    deadline = time.monotonic()+90
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.025)
    raise AssertionError("假仪器子进程超时")


@pytest.fixture
def client(tmp_path, monkeypatch):
    copy_config(tmp_path)
    relative = "lab_workflows/experiment_modules/photon_shot_noise/workflow.excalidraw"
    diagram = tmp_path / relative
    diagram.parent.mkdir(parents=True)
    diagram.write_bytes((Path(__file__).resolve().parents[2] / relative).read_bytes())
    manager = JobManager()
    service = PhotonShotNoiseSessionService(lambda:tmp_path, manager)
    original = ADAPTER._execute
    def execute(module, **kwargs):
        if module in (ADAPTER.acquisition_module, service.session_module):
            full = module == ADAPTER.acquisition_module
            monkeypatch.setenv("PHOTON_TEST_FULL", "1" if full else "0")
            module = "lab_workflows.experiment_modules.photon_shot_noise.test_workflow"
        return original(module, **kwargs)
    monkeypatch.setattr(ADAPTER, "_execute", execute)
    # 真实子进程仍用本仓库解释器，输出/取消标记全部进入临时根目录。
    from lab_workflows.experiments import typed
    original_popen = typed.subprocess.Popen
    def popen(command, **kwargs):
        kwargs["cwd"] = Path(__file__).resolve().parents[2]
        return original_popen([sys.executable, *command[1:]], **kwargs)
    monkeypatch.setattr(typed.subprocess, "Popen", popen)
    monkeypatch.setattr(ADAPTER, "root", tmp_path)
    monkeypatch.setattr(ADAPTER, "defaults_path", tmp_path / "params/experiments/photon-shot-noise.yaml")
    monkeypatch.setenv("PHOTON_TEST_ROOT", str(tmp_path))
    app = FastAPI()
    app.include_router(create_router(service))
    with TestClient(app) as http:
        http.service, http.manager, http.root = service, manager, tmp_path
        yield http
    service.shutdown()
    wait(lambda:all(job.status not in ("queued","running") for job in manager.jobs.values()))


def create(client):
    response = client.post(BASE+"/sessions", json={"parameters":small_params().to_external()})
    assert response.status_code == 200, response.text
    sid = response.json()["session_id"]
    wait(lambda:client.service.get(sid)["status"] in ("waiting","closed"))
    assert client.service.get(sid)["status"] == "waiting", client.service.get(sid)
    return sid


def steps(client, sid, ids):
    response = client.post(f"{BASE}/sessions/{sid}/steps",
        json=dict(revision=client.service.get(sid)["revision"], step_ids=ids))
    assert response.status_code == 200, response.text
    wait(lambda:client.service.get(sid)["pending_request"] is None)
    state = client.service.get(sid)
    assert state["error"] is None, state
    return state


def test_child_revision_dependencies_lock_rerun_and_finish(client):
    flow = client.get(BASE+"/workflow").json()
    assert len(flow["acquisition"]) == 8 and len(flow["analysis"]) == 3
    assert client.get(BASE+"/workflow/diagram").json()["type"] == "excalidraw"
    sid = create(client)
    wait(lambda:client.manager.hardware_lock.locked())
    assert client.post(f"{BASE}/sessions/{sid}/steps", json=dict(revision=0, step_ids=["A07"])).status_code == 422
    state = steps(client, sid, [f"A{i:02d}" for i in range(1,7)])
    assert client.post(f"{BASE}/sessions/{sid}/finish", json={"revision":0}).status_code == 409
    assert client.get(f"{BASE}/sessions/{sid}").json()["revision"] == state["revision"]
    assert PhotonShotNoiseSessionService(lambda:client.root, client.manager).active() is None
    assert client.get(f"{BASE}/runs/{state['run_id']}/analysis").status_code == 409
    state = steps(client, sid, ["A07"])
    first = client.root / "data/Photon_shot_noise" / state["run_id"]
    before = frozen_inputs(first)
    state = steps(client, sid, ["A06","A07"])
    assert state["run_id"] != first.name and frozen_inputs(first) == before
    response = client.post(f"{BASE}/sessions/{sid}/finish", json={"revision":state["revision"]})
    assert response.status_code == 200, response.text
    wait(lambda:client.service.get(sid)["status"] == "closed")
    wait(lambda:not client.manager.hardware_lock.locked())
    final = client.service.get(sid)
    assert final["steps"]["A08"]["status"] == "completed"
    assert final["outputs"]["Pump"] == "OFF" and final["safety_shutdown"]["completed"]


@pytest.mark.parametrize("method", ["cancel","eof","shutdown"])
def test_cancel_eof_shutdown_restore_before_unlock(client, method):
    sid = create(client)
    state = steps(client, sid, [f"A{i:02d}" for i in range(1,7)])
    if method == "cancel":
        client.manager.get(state["job_id"]).cancellation.cancel()
    elif method == "eof":
        client.service.close_communication()
    else:
        client.service.shutdown()
    wait(lambda:client.service.get(sid)["status"] == "closed")
    wait(lambda:not client.manager.hardware_lock.locked())
    final = client.service.get(sid)
    assert final["safety_shutdown"]["completed"]
    assert final["outputs"]["温控"] == "5 V DC + ON" and final["outputs"]["Pump"] == "OFF"


def test_history_parameters_readonly_real_analysis_and_directory_lock(client, monkeypatch):
    base = client.root / "data/Photon_shot_noise"
    run, other = synthetic_run(base / "legacy",legacy=True), synthetic_run(base / "other")
    before = frozen_inputs(run)
    assert client.get(BASE+"/runs?limit=50&offset=0").json()["total"] == 2
    value = client.get(f"{BASE}/runs/{run.name}/summary").json()
    assert value["parameters"]["PROBE_POWER_POINTS"] == 5 and not value["artifacts"]
    assert value["parameters"]["FIT_START_IDX"] == 2
    assert client.get(BASE+"/runs/missing/summary").status_code == 404
    assert client.get(BASE+"/runs/..%2Fescape/summary").status_code == 404
    state = client.get(f"{BASE}/runs/{run.name}/analysis").json()
    assert state["options"]["PSD_NPERSEG"] == 64 and frozen_inputs(run) == before
    execute, entered, release = ADAPTER._execute, Event(), Event()
    def blocked(module, **kwargs):
        if module == ADAPTER.analysis_module:
            entered.set()
            assert release.wait(30)
        return execute(module, **kwargs)
    monkeypatch.setattr(ADAPTER, "_execute", blocked)
    body = dict(revision=state["revision"],step_ids=["B01","B02","B03"],analysis_parameters={"FIT_START_IDX":1})
    try:
        response = client.post(f"{BASE}/runs/{run.name}/analysis",json=body)
        assert response.status_code == 200, response.text
        jid = response.json()["id"]
        assert entered.wait(5)
        assert client.post(f"{BASE}/runs/{run.name}/analysis",json=body).status_code == 409
    finally:
        release.set()
    wait(lambda:client.manager.get(jid).status in ("completed","failed"))
    assert client.manager.get(jid).status == "completed",client.manager.get(jid).error
    assert client.get(f"{BASE}/runs/{other.name}/analysis").json()["options"]["FIT_START_IDX"] == 2
    assert client.post(f"{BASE}/runs/{run.name}/analysis",json=body).status_code == 409
    summary = client.get(f"{BASE}/runs/{run.name}/summary").json()
    assert summary["analysis_status"] == "completed" and "shot_noise_x.png" in str(summary["artifacts"])
    assert client.get(next(iter(summary["artifacts"].values()))).status_code == 200
    assert frozen_inputs(run) == before


def test_identity_invalid_legacy_config_and_strict_request_rejected(client):
    run = synthetic_run(client.root / "data/Photon_shot_noise/wrong", legacy=True)
    path = run / "experiment_config.yaml"
    config = yaml.safe_load(path.read_text())
    config["experiment_type"] = "Wrong"
    path.write_text(yaml.safe_dump(config))
    assert client.get(f"{BASE}/runs/{run.name}/summary").status_code == 422
    assert client.get(BASE+"/runs").json()["total"] == 0
    assert client.post(BASE+"/sessions",json={"parameters":{"unknown":1}}).status_code == 422


def test_main_discovery_lifespan_generic_analysis_lock_and_full_auto(client, monkeypatch):
    from . import main
    monkeypatch.setattr(main,"ROOT",client.root)
    monkeypatch.setattr(main,"manager",client.manager)
    monkeypatch.setattr(main,"photon_sessions",client.service)
    http = TestClient(main.app)
    definition = http.get(BASE).json()
    assert definition["id"] == ID and definition["execution_mode"] == "typed_workflow"
    assert "节点" in definition["description"] and client.service in main._workflow_session_services()
    assert BASE+"/sessions/{session_id}/finish" in main.app.openapi()["paths"]
    run = synthetic_run(client.root / "data/Photon_shot_noise/generic")
    before = frozen_inputs(run)
    execute, entered, release = ADAPTER._execute, Event(), Event()
    def blocked(module, **kwargs):
        entered.set()
        assert release.wait(30)
        return execute(module, **kwargs)
    monkeypatch.setattr(ADAPTER,"_execute",blocked)
    try:
        response = http.post(f"/api/runs/{run.name}/analyze",json={"experiment_id":ID})
        assert response.status_code == 200,response.text
        jid = response.json()["id"]
        assert entered.wait(5)
        state = client.get(f"{BASE}/runs/{run.name}/analysis").json()
        assert client.post(f"{BASE}/runs/{run.name}/analysis",json={"revision":state["revision"],"step_ids":["B01"]}).status_code == 409
    finally:
        release.set()
    wait(lambda:client.manager.get(jid).status in ("completed","failed"))
    assert client.manager.get(jid).status == "completed",client.manager.get(jid).error
    assert frozen_inputs(run) == before
    monkeypatch.setattr(ADAPTER,"_execute",execute)
    response = http.post(BASE+"/runs",json={"parameters":small_params().to_external()})
    assert response.status_code == 200,response.text
    jid = response.json()["id"]
    wait(lambda:client.manager.get(jid).status in ("completed","failed"))
    job = client.manager.get(jid)
    assert job.status == "completed",job.error
    directory = Path(job.result["run_dir"])
    assert directory.parent == client.root / "data/Photon_shot_noise"
    assert (directory / "results/shot_noise_x.png").is_file()
