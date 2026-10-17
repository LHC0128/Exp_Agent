"""Mx Z 电压频率标定 真实子进程会话、全局互斥、历史与独立分析接口。"""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
import time
import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from lab_workflows.experiment_modules.mx_z_field_calibration.definition import ADAPTER
from lab_workflows.experiment_modules.mx_z_field_calibration.test_workflow import copy_config, small_params
from lab_workflows.experiment_modules.mx_z_field_calibration import stages
from .jobs import JobManager
from .mx_z_field_calibration import MxZFieldCalibrationSessionService, create_router

BASE = "/api/experiments/mx-z-field-calibration"


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
    diagram = tmp_path / "lab_workflows/experiment_modules/mx_z_field_calibration/workflow.excalidraw"
    diagram.parent.mkdir(parents=True)
    diagram.write_bytes((Path(__file__).resolve().parents[2] /
        "lab_workflows/experiment_modules/mx_z_field_calibration/workflow.excalidraw").read_bytes())
    manager = JobManager()
    service = MxZFieldCalibrationSessionService(lambda: tmp_path, manager)
    original = ADAPTER._execute

    def execute(module, **kwargs):
        if module.endswith(".session"):
            module = "lab_workflows.experiment_modules.mx_z_field_calibration.test_workflow"
        return original(module, **kwargs)

    monkeypatch.setattr(ADAPTER, "_execute", execute)
    monkeypatch.setenv("MX_Z_FIELD_TEST_ROOT", str(tmp_path))
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
    expected = {stage.id: list(stage.quality_gates) for stage in (*stages.ACQUISITION, *stages.ANALYSIS)}
    assert {stage["id"]: stage["quality_gates"] for column in ("acquisition", "analysis") for stage in flow[column]} == expected
    diagram = client.get(BASE + "/workflow/diagram").json()
    assert diagram["type"] == "excalidraw"
    notes = {item["customData"]["workflow_stage"]: item for item in diagram["elements"]
        if item.get("customData", {}).get("role") == "quality_gates"}
    assert set(notes) == {key for key, value in expected.items() if value}
    for key, item in notes.items():
        assert item["customData"]["quality_gates"] == expected[key]
        assert "".join(expected[key]).replace(" ", "") in item["text"].replace("\n", "").replace(" ", "")
    session_id = create(client)
    assert client.manager.hardware_lock.locked()
    current = client.get(BASE + "/sessions/active").json()
    assert current["session_id"] == session_id
    assert client.post(BASE + "/sessions", json={"parameters": {}}).status_code == 409
    response = client.post(f"{BASE}/sessions/{session_id}/steps", json={"revision": 0, "step_ids": ["A09"]})
    assert response.status_code == 422
    state = steps(client, session_id, [f"A{index:02d}" for index in range(1, 8)])
    assert client.get(f"{BASE}/runs/{state['run_id']}/analysis").status_code == 409
    assert client.post(f"{BASE}/sessions/{session_id}/finish", json={"revision": 0}).status_code == 409
    assert client.get(f"{BASE}/sessions/{session_id}").json()["revision"] == state["revision"]
    # 新服务不能从磁盘恢复仍带输出的硬件会话。
    assert MxZFieldCalibrationSessionService(lambda: client.root, client.manager).active() is None
    body = {"revision": state["revision"], "step_ids": ["A08", "A09"]}
    response = client.post(f"{BASE}/sessions/{session_id}/steps", json=body)
    assert response.status_code == 200
    assert client.post(f"{BASE}/sessions/{session_id}/steps", json=body).status_code == 409
    wait(lambda: client.service.get(session_id)["pending_request"] is None)
    state = client.service.get(session_id)
    assert state["error"] is None
    assert state["demod_mode"] == "response"
    response = client.post(f"{BASE}/sessions/{session_id}/finish", json={"revision": state["revision"]})
    assert response.status_code == 200
    wait(lambda: client.service.get(session_id)["status"] == "closed")
    wait(lambda: not client.manager.hardware_lock.locked())
    state = client.service.get(session_id)
    assert state["safety_shutdown"]["completed"]
    assert state["steps"]["A10"]["status"] == "completed"


@pytest.mark.parametrize("method", ["cancel", "eof", "shutdown", "finish"])
def test_cancel_eof_backend_shutdown_safely_close_real_child(client, method):
    session_id = create(client)
    state = steps(client, session_id, [f"A{index:02d}" for index in range(1, 8)])
    (client.root / "pause-shutdown").touch()
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = None
        try:
            if method == "cancel":
                client.manager.get(state["job_id"]).cancellation.cancel()
            elif method == "eof":
                client.service.close_communication()
            elif method == "shutdown":
                pending = executor.submit(client.service.shutdown)
            else:
                assert client.post(f"{BASE}/sessions/{session_id}/finish", json={"revision": state["revision"]}).status_code == 200
            wait(lambda: (client.root / "shutdown-entered").is_file())
            assert client.manager.hardware_lock.locked()
            assert client.manager.get(state["job_id"]).status == "running"
            assert not client.manager.try_acquire_short_hardware()
            assert client.post(BASE + "/sessions", json={"parameters": small_params().to_external()}).status_code == 409
        finally:
            (client.root / "release-shutdown").touch()
        if pending:
            pending.result(timeout=40)
    wait(lambda: client.service.get(session_id)["status"] == "closed")
    wait(lambda: not client.manager.hardware_lock.locked())
    final = client.service.get(session_id)
    assert final["safety_shutdown"]["completed"]
    assert final["outputs"]["Y RF"] == "OFF"
    config = yaml.safe_load((client.root / "data/Mx_Z_Field_Calibration" / state["run_id"] /
        "experiment_config.yaml").read_text(encoding="utf-8"))
    assert config["completion_status"] in ("cancelled", "failed")


def test_child_protocol_rejects_duplicate_and_stale_commands_without_work(client):
    session_id = create(client)
    item = client.service.sessions[session_id]
    job = client.manager.get(item["state"]["job_id"])
    command = {"request_id": "independent-check", "revision": 0, "action": "steps", "step_ids": ["A01"]}
    def results():
        return [event["data"]["command_result"] for event in job.events if "command_result" in event.get("data", {})]
    item["queue"].put(command)
    wait(lambda: len(results()) == 1)
    assert results()[0]["error"] is None
    assert client.service.get(session_id)["revision"] == 1
    item["queue"].put(command | {"revision": 1})
    wait(lambda: len(results()) == 2)
    assert "重复" in results()[-1]["error"]
    item["queue"].put(command | {"request_id": "stale-check"})
    wait(lambda: len(results()) == 3)
    assert "修订" in results()[-1]["error"]
    assert client.service.get(session_id)["revision"] == 1
    assert not (client.root / "data/Mx_Z_Field_Calibration").exists()


def test_concurrent_api_commands_accept_only_one_revision(client):
    session_id = create(client)
    barrier = Barrier(2)
    def submit():
        barrier.wait(timeout=5)
        return client.post(f"{BASE}/sessions/{session_id}/steps", json={"revision": 0, "step_ids": ["A01"]}).status_code
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _: submit(), range(2)))
    assert sorted(responses) == [200, 409]
    wait(lambda: client.service.get(session_id)["pending_request"] is None)
    assert client.service.get(session_id)["revision"] == 1


def test_history_snapshots_missing_results_and_analysis_isolation(client):
    session_id = create(client)
    state = steps(client, session_id, [f"A{index:02d}" for index in range(1, 10)])
    run_id = state["run_id"]
    history = client.get(BASE + "/runs?limit=50&offset=0").json()
    assert history["runs"][0]["run_id"] == run_id
    summary = client.get(f"{BASE}/runs/{run_id}/summary").json()
    assert summary["parameters"]["Z_BIAS_STEP_V"] == 1
    assert summary["analysis_status"] == "not_started"
    assert client.get(BASE + "/runs/not-a-run/summary").status_code == 404
    assert client.get(BASE + "/runs/..%2Fescape/summary").status_code == 404
    analysis_state = client.get(f"{BASE}/runs/{run_id}/analysis")
    assert analysis_state.status_code == 200, analysis_state.text
    revision = analysis_state.json()["revision"]
    job = client.post(f"{BASE}/runs/{run_id}/analysis", json={"revision": revision,
        "step_ids": ["B01", "B02", "B03", "B04"], "analysis_parameters": {"LINEAR_R_SQUARED_MIN": 0.9999}})
    assert job.status_code == 200, job.text
    assert client.post(f"{BASE}/runs/{run_id}/analysis", json={"revision": revision,
        "step_ids": ["B01", "B02", "B03", "B04"]}).status_code == 409
    job_id = job.json()["id"]
    wait(lambda: client.manager.get(job_id).status in ("completed", "failed"))
    assert client.manager.get(job_id).status == "completed", client.manager.get(job_id).error
    refreshed = client.get(f"{BASE}/runs/{run_id}/summary").json()
    assert refreshed["analysis_status"] == "completed"
    assert refreshed["parameters"]["LINEAR_R_SQUARED_MIN"] == 0.99
    assert any(name.endswith("z_frequency_calibration.png") for name in refreshed["artifacts"])
    assert "results/z_frequency_calibration.png" in refreshed["artifacts"]
    assert client.get(f"{BASE}/runs/{run_id}/analysis").json()["options"]["LINEAR_R_SQUARED_MIN"] == 0.9999


def test_legacy_reanalysis_and_stage_api_share_the_same_directory_lock(client, monkeypatch):
    from . import main
    monkeypatch.setattr(main, "ROOT", client.root)
    monkeypatch.setattr(main, "manager", client.manager)
    monkeypatch.setattr(main, "_workflow_session_services", lambda: [client.service])
    route = next(route for route in main.app.routes if getattr(route, "path", None) == "/api/runs/{run_id}/analyze")
    client.app.add_api_route(route.path, route.endpoint, methods=["POST"])
    session_id = create(client)
    state = steps(client, session_id, [f"A{index:02d}" for index in range(1, 10)])
    run_id = state["run_id"]
    entered, release = Event(), Event()
    original = ADAPTER._execute
    def blocked(module, **kwargs):
        if module == ADAPTER.analysis_module:
            entered.set()
            if not release.wait(timeout=30):
                raise TimeoutError("假分析等待释放超时")
        return original(module, **kwargs)
    monkeypatch.setattr(ADAPTER, "_execute", blocked)
    staged = {"revision": 0, "step_ids": ["B01", "B02", "B03", "B04"]}
    try:
        job = client.post(f"{BASE}/runs/{run_id}/analysis", json=staged)
        assert job.status_code == 200, job.text
        assert entered.wait(timeout=5)
        blocked_response = client.post(f"/api/runs/{run_id}/analyze", json={"experiment_id": ADAPTER.id})
        assert blocked_response.status_code == 409 and blocked_response.headers["X-Error-Code"] == "analysis_running"
    finally:
        release.set()
    job_id = job.json()["id"]
    wait(lambda: client.manager.get(job_id).status not in ("queued", "running"))
    assert client.manager.get(job_id).status == "completed", client.manager.get(job_id).error
    entered.clear(); release.clear()
    try:
        job = client.post(f"/api/runs/{run_id}/analyze", json={"experiment_id": ADAPTER.id})
        assert job.status_code == 200, job.text
        assert entered.wait(timeout=5)
        staged["revision"] = client.get(f"{BASE}/runs/{run_id}/analysis").json()["revision"]
        blocked_response = client.post(f"{BASE}/runs/{run_id}/analysis", json=staged)
        assert blocked_response.status_code == 409 and blocked_response.headers["X-Error-Code"] == "analysis_running"
    finally:
        release.set()
    job_id = job.json()["id"]
    wait(lambda: client.manager.get(job_id).status not in ("queued", "running"))
    assert client.manager.get(job_id).status == "completed", client.manager.get(job_id).error
    assert list((client.root / "data/Mx_Z_Field_Calibration" / run_id / "results").glob("before_*/analysis.json"))
