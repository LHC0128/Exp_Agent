"""Mx 主磁场标定 真实子进程会话、全局互斥、历史与独立分析接口。"""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
import time
import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from lab_workflows.experiment_modules.mx_main_field_calibration.definition import ADAPTER
from lab_workflows.experiment_modules.mx_main_field_calibration.test_workflow import copy_config, small_params
from .jobs import JobManager
from .mx_main_field_calibration import MxMainFieldCalibrationSessionService, create_router

BASE = "/api/experiments/mx-main-field-calibration"


def wait(predicate):
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.025)
    raise AssertionError("假仪器子进程超时")


@pytest.fixture
def client(tmp_path, monkeypatch):
    copy_config(tmp_path)
    diagram = tmp_path / "lab_workflows/experiment_modules/mx_main_field_calibration/workflow.excalidraw"
    diagram.parent.mkdir(parents=True)
    diagram.write_bytes((Path(__file__).resolve().parents[2] /
        "lab_workflows/experiment_modules/mx_main_field_calibration/workflow.excalidraw").read_bytes())
    manager = JobManager()
    service = MxMainFieldCalibrationSessionService(lambda: tmp_path, manager)
    original = ADAPTER._execute

    def execute(module, **kwargs):
        if module.endswith(".session"):
            module = "lab_workflows.experiment_modules.mx_main_field_calibration.test_workflow"
        return original(module, **kwargs)

    monkeypatch.setattr(ADAPTER, "_execute", execute)
    monkeypatch.setenv("MX_MAIN_FIELD_TEST_ROOT", str(tmp_path))
    app = FastAPI()
    app.include_router(create_router(service, include_history=True))
    with TestClient(app) as http:
        http.service, http.manager, http.root = service, manager, tmp_path
        yield http
    service.close_communication()
    for job in list(manager.jobs.values()):
        if job.status in ("queued", "running"):
            job.cancellation.cancel()
    wait(lambda: all(job.status not in ("queued", "running") for job in manager.jobs.values()))


def create(client):
    params = small_params()
    response = client.post(BASE + "/sessions", json={"parameters": params.to_external()})
    assert response.status_code == 200, response.text
    session_id = response.json()["session_id"]
    wait(lambda: client.service.get(session_id)["status"] in ("waiting", "closed"))
    value = client.service.get(session_id)
    assert value["status"] == "waiting", (value, client.manager.get(value["job_id"]).public())
    return session_id


def steps(client, session_id, ids):
    revision = client.service.get(session_id)["revision"]
    response = client.post(f"{BASE}/sessions/{session_id}/steps", json={"revision": revision, "step_ids": ids})
    assert response.status_code == 200, response.text
    wait(lambda: client.service.get(session_id)["pending_request"] is None)
    value = client.service.get(session_id)
    assert value["error"] is None, value
    return value


def test_real_child_dependency_revision_lock_refresh_and_finish(client):
    flow = client.get(BASE + "/workflow").json()
    assert len(flow["acquisition"]) == 10 and len(flow["analysis"]) == 4
    assert client.get(BASE + "/workflow/diagram").json()["type"] == "excalidraw"
    session_id = create(client)
    assert client.manager.hardware_lock.locked()
    current = client.get(BASE + "/sessions/active").json()
    assert current["session_id"] == session_id
    assert client.post(BASE + "/sessions", json={"parameters": {}}).status_code == 409
    response = client.post(f"{BASE}/sessions/{session_id}/steps", json={"revision": 0, "step_ids": ["A09"]})
    assert response.status_code == 422
    state = steps(client, session_id, [f"A{index:02d}" for index in range(1, 8)])
    assert client.post(f"{BASE}/sessions/{session_id}/finish", json={"revision": 0}).status_code == 409
    assert client.get(f"{BASE}/sessions/{session_id}").json()["revision"] == state["revision"]
    # 新服务不能从磁盘恢复仍带输出的硬件会话。
    assert MxMainFieldCalibrationSessionService(lambda: client.root, client.manager).active() is None
    state = steps(client, session_id, ["A08", "A09"])
    assert state["demod_mode"] == "response"
    response = client.post(f"{BASE}/sessions/{session_id}/finish", json={"revision": state["revision"]})
    assert response.status_code == 200
    wait(lambda: client.service.get(session_id)["status"] == "closed")
    wait(lambda: not client.manager.hardware_lock.locked())
    state = client.service.get(session_id)
    assert state["safety_shutdown"]["completed"]
    assert state["steps"]["A10"]["status"] == "completed"


@pytest.mark.parametrize("method", ["cancel", "eof", "shutdown"])
def test_cancel_eof_backend_shutdown_safely_close_real_child(client, method):
    session_id = create(client)
    state = steps(client, session_id, [f"A{index:02d}" for index in range(1, 8)])
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
    assert final["outputs"]["Y RF"] == "OFF"
    config = yaml.safe_load((client.root / "data/Mx_Main_Field_Calibration" / state["run_id"] /
        "experiment_config.yaml").read_text(encoding="utf-8"))
    assert config["completion_status"] in ("cancelled", "failed")


def test_history_snapshots_missing_results_and_analysis_isolation(client):
    session_id = create(client)
    state = steps(client, session_id, [f"A{index:02d}" for index in range(1, 10)])
    run_id = state["run_id"]
    history = client.get(BASE + "/runs?limit=50&offset=0").json()
    assert history["runs"][0]["run_id"] == run_id
    summary = client.get(f"{BASE}/runs/{run_id}/summary").json()
    assert summary["parameters"]["MAIN_FIELD_STEP_MA"] == 0.5
    assert summary["analysis_status"] == "not_started"
    assert client.get(BASE + "/runs/not-a-run/summary").status_code == 404
    assert client.get(BASE + "/runs/..%2Fescape/summary").status_code == 404
    analysis_state = client.get(f"{BASE}/runs/{run_id}/analysis")
    assert analysis_state.status_code == 200, analysis_state.text
    revision = analysis_state.json()["revision"]
    job = client.post(f"{BASE}/runs/{run_id}/analysis", json={"revision": revision,
        "step_ids": ["B01", "B02", "B03", "B04"], "analysis_parameters": {"GYROMAGNETIC_RATIO_HZ_PER_NT": 5}})
    assert job.status_code == 200, job.text
    job_id = job.json()["id"]
    wait(lambda: client.manager.get(job_id).status in ("completed", "failed"))
    assert client.manager.get(job_id).status == "completed", client.manager.get(job_id).error
    refreshed = client.get(f"{BASE}/runs/{run_id}/summary").json()
    assert refreshed["analysis_status"] == "completed"
    assert refreshed["parameters"]["GYROMAGNETIC_RATIO_HZ_PER_NT"] == 7
    assert any(name.endswith("main_field_frequency_calibration.png") for name in refreshed["artifacts"])
    assert client.get(f"{BASE}/runs/{run_id}/analysis").json()["options"]["GYROMAGNETIC_RATIO_HZ_PER_NT"] == 5


def test_concurrent_commands_and_global_hardware_queue(client):
    session_id = create(client)
    revision = client.service.get(session_id)["revision"]
    assert not client.manager.try_acquire_short_hardware()
    assert client.post(f"{BASE}/sessions/{session_id}/steps", json={"revision": revision,
        "step_ids": ["A01", "A01"]}).status_code == 422
    barrier = Barrier(2)

    def submit():
        barrier.wait(timeout=5)
        return client.post(f"{BASE}/sessions/{session_id}/steps", json={"revision": revision, "step_ids": ["A01"]})

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: submit(), range(2)))
    assert sorted(response.status_code for response in responses) == [200, 409]
    wait(lambda: client.service.get(session_id)["pending_request"] is None)
    observed = Event()
    queued = client.manager.create("验收队列探针", lambda job: observed.set())
    assert queued.status == "queued" and not observed.is_set()
    state = client.service.get(session_id)
    assert client.post(f"{BASE}/sessions/{session_id}/finish", json={"revision": state["revision"]}).status_code == 200
    wait(lambda: queued.status == "completed")
    assert observed.is_set()


@pytest.mark.parametrize("first", ["stepwise", "legacy"])
def test_stepwise_and_legacy_analysis_share_directory_lock(client, monkeypatch, first):
    from . import main
    monkeypatch.setattr(main, "ROOT", client.root)
    monkeypatch.setattr(main, "manager", client.manager)
    monkeypatch.setattr(main, "_workflow_session_services", lambda: (client.service,))
    client.app.add_api_route("/api/runs/{run_id}/analyze", main.analyze_run, methods=["POST"])
    session_id = create(client)
    state = steps(client, session_id, [f"A{index:02d}" for index in range(1, 8)])
    run_id = state["run_id"]
    staged_url, legacy_url = f"{BASE}/runs/{run_id}/analysis", f"/api/runs/{run_id}/analyze"
    assert client.get(staged_url).status_code == 409
    assert client.post(legacy_url, json={"experiment_id": "mx-main-field-calibration"}).status_code == 409
    steps(client, session_id, ["A08", "A09"])
    revision = client.get(staged_url).json()["revision"]
    body = {"revision": revision, "step_ids": ["B01", "B02", "B03", "B04"], "analysis_parameters": {}}
    assert client.post(staged_url, json={**body, "revision": revision + 1}).status_code == 409
    assert client.post(staged_url, json={**body, "analysis_parameters": {"RUN_TAG": "illegal"}}).status_code == 422
    entered, release = Event(), Event()
    execute = ADAPTER._execute

    def held(module, **kwargs):
        if module == ADAPTER.analysis_module:
            entered.set()
            assert release.wait(timeout=20)
        return execute(module, **kwargs)

    monkeypatch.setattr(ADAPTER, "_execute", held)
    start_url, start_body = (staged_url, body) if first == "stepwise" else (legacy_url, {"experiment_id": "mx-main-field-calibration"})
    other_url, other_body = (legacy_url, {"experiment_id": "mx-main-field-calibration"}) if first == "stepwise" else (staged_url, body)
    try:
        response = client.post(start_url, json=start_body)
        assert response.status_code == 200, response.text
        assert entered.wait(timeout=5)
        blocked = client.post(other_url, json=other_body)
        assert blocked.status_code == 409 and blocked.headers["X-Error-Code"] == "analysis_running"
    finally:
        release.set()
    job = client.manager.get(response.json()["id"])
    wait(lambda: job.status in ("completed", "failed"))
    assert job.status == "completed", job.error
    assert (client.root / "data/Mx_Main_Field_Calibration" / run_id / "results/calibration_results.npz").is_file()
