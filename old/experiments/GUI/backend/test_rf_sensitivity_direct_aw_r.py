"""真实 HTTP 路由、隔离历史、真实假仪器会话及离线导出验收。"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import time
import json
from threading import Event, Thread
import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from . import main
from .jobs import JobManager
from .rf_sensitivity_direct_aw_r import RFRSessionService
from .workflow_sessions import create_router
from lab_workflows.common import find_project_root
from lab_workflows.experiment_modules.rf_sensitivity_direct_aw_r.definition import ADAPTER
from lab_workflows.experiment_modules.rf_sensitivity_direct_aw_r.test_workflow import rig, control_source, rrig, through
from lab_workflows.experiment_modules.rf_sensitivity_direct_aw_r.test_analysis import synthetic_run
from lab_workflows.experiment_modules.rf_sensitivity_direct_aw_r.run_format import EXPERIMENT_ID, DATA_TYPE

BASE = f"/api/experiments/{EXPERIMENT_ID}"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "ROOT", tmp_path)
    monkeypatch.setattr(main, "manager", JobManager())
    monkeypatch.setattr(main.rf_r_sessions, "manager", main.manager)
    with TestClient(main.app) as client:
        yield client


def make_run(root, name="first", **changes):
    directory = root / "data" / DATA_TYPE / name
    config, _ = synthetic_run(directory)
    config.update(started_at=name, run_tag="历史参数", **changes)
    path = directory / "experiment_config.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return directory


def wait_for(predicate):
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError("假仪器任务未在限时内完成")


def test_history_isolation_snapshot_missing_results_and_reanalysis(client, tmp_path, monkeypatch):
    directory = make_run(tmp_path)
    make_run(tmp_path, "wrong", experiment_id="rf-sensitivity-aw")
    make_run(tmp_path, "failed", completion_status="failed")
    before = (directory / "experiment_config.yaml").read_bytes()
    runs = client.get(f"{BASE}/runs?limit=1&offset=0").json()
    assert runs["total"] == 2 and runs["runs"][0]["run_id"] == "first"
    assert client.get("/api/experiments/rf-sensitivity-aw/runs").json()["total"] == 0
    assert client.get(f"/api/runs?experiment_id={EXPERIMENT_ID}").json()["total"] == 2
    summary = client.get(f"{BASE}/runs/first/summary").json()
    assert summary["response_component"] == "R" and summary["noise_component"] == "X"
    assert summary["parameters"]["Z_RF_FREQ"] == 3020 and summary["parameters"]["Z_RF_NOISE_FREQ"] == 3000
    assert summary["stage_frequencies"]["response"]["rf_frequency_Hz"] == 3020
    assert summary["analysis"] is None and summary["artifacts"] == {}
    assert summary["can_analyze"] and not summary["can_compare"]
    assert (directory / "experiment_config.yaml").read_bytes() == before
    assert client.get(f"{BASE}/runs/missing/summary").status_code == 404
    assert client.get(f"{BASE}/runs/..%5Coutside/summary").status_code == 400
    create = Mock(return_value=SimpleNamespace(public=lambda: {"id": "analysis-job"}))
    monkeypatch.setattr(main.manager, "create", create)
    assert client.post("/api/runs/first/analyze", json={"experiment_id": EXPERIMENT_ID}).status_code == 200
    assert create.call_args.args[0] == f"analysis:{EXPERIMENT_ID}:first"
    assert create.call_args.kwargs["hardware_required"] is False
    create.reset_mock()
    assert client.post("/api/runs/failed/analyze", json={"experiment_id": EXPERIMENT_ID}).status_code == 400
    assert not create.called


def test_real_comparison_export_guards_and_artifact_isolation(client, tmp_path, monkeypatch):
    from lab_workflows.experiment_modules.rf_sensitivity_direct_aw_r.analysis import analyze
    entries = []
    for name in ("first", "second"):
        directory = make_run(tmp_path, name)
        analyze(directory)
        entries.append({"run_id": name, "legend": f"Run {name}"})
    before = {path: path.read_bytes() for path in (tmp_path / "data").rglob("*") if path.is_file()}
    assert client.post(f"{BASE}/compare", json={"runs": [{"run_id": "first"}, {"run_id": "second", "legend": "中文"}]}).status_code == 400
    response = client.post(f"{BASE}/compare", json={"runs": entries})
    assert response.status_code == 200, response.text
    identifier = response.json()["id"]
    assert main.manager.get(identifier).kind == f"comparison:{EXPERIMENT_ID}"
    wait_for(lambda: main.manager.get(identifier).status in ("completed", "failed"))
    job = main.manager.get(identifier)
    assert job.status == "completed", job.error
    assert job.result["response_component"] == "R"
    for extension, prefix in (("png", b"\x89PNG"), ("pdf", b"%PDF")):
        url = job.result["artifacts"][f"full_analysis_comparison.{extension}"]
        assert BASE in url and client.get(url).content.startswith(prefix)
        assert client.get(url.replace(EXPERIMENT_ID, "rf-sensitivity-aw")).status_code == 404
    assert all(path.read_bytes() == value for path, value in before.items())
    monkeypatch.setattr(main.manager, "has_active_kind", lambda kind: kind == f"comparison:{EXPERIMENT_ID}")
    assert client.post("/api/runs/first/analyze", json={"experiment_id": EXPERIMENT_ID}).status_code == 409
    assert client.post(f"{BASE}/compare", json={"runs": entries}).status_code == 409
    monkeypatch.setattr(main.manager, "jobs", {"compare": SimpleNamespace(kind=f"comparison:{EXPERIMENT_ID}", status="running")})
    assert client.delete(f"/api/runs/{EXPERIMENT_ID}/first").status_code == 409


@pytest.fixture
def workflow_client(rrig, monkeypatch):
    manager = JobManager()
    service = RFRSessionService(lambda: rrig.tmp_path, manager)
    app = FastAPI()
    app.include_router(create_router(service))
    original = ADAPTER._execute
    monkeypatch.setattr(ADAPTER, "_execute", lambda module, **kwargs: original(
        "lab_workflows.experiment_modules.rf_sensitivity_direct_aw_r.test_workflow"
        if module.endswith(".session") else module, **kwargs))
    monkeypatch.setenv("RF_TEST_ROOT", str(rrig.tmp_path))
    with TestClient(app) as client:
        yield client, service, manager
    service.close_communication()
    for job in manager.jobs.values():
        job.cancellation.cancel()
    service.shutdown()


def test_real_session_restore_staged_order_same_directory_and_close(workflow_client, rrig):
    client, service, manager = workflow_client
    flow = client.get(f"{BASE}/workflow").json()
    assert [item["id"] for item in flow["acquisition"]] == through("A16")
    assert "A11" not in json.dumps(flow["acquisition"])
    assert flow["diagram_url"] == f"{BASE}/workflow/diagram"
    source = find_project_root() / service.diagram_file
    target = rrig.tmp_path / service.diagram_file
    target.parent.mkdir(parents=True)
    target.write_bytes(source.read_bytes())
    assert client.get(flow["diagram_url"]).json()["type"] == "excalidraw"
    response = client.post(f"{BASE}/sessions", json={"parameters": rrig.params.to_external()})
    assert response.status_code == 200, response.text
    sid = response.json()["session_id"]
    wait_for(lambda: service.get(sid)["status"] == "waiting")
    assert client.get(f"{BASE}/sessions/active").json()["session_id"] == sid
    assert not manager.hardware_lock.acquire(blocking=False)
    step_url = f"{BASE}/sessions/{sid}/steps"
    assert client.post(step_url, json={"revision": 0, "step_ids": ["A12"]}).status_code == 422
    assert client.post(step_url, json={"revision": 99, "step_ids": ["A01"]}).status_code == 409
    def execute(ids):
        state = service.get(sid)
        result = client.post(step_url, json={"revision": state["revision"], "step_ids": ids})
        assert result.status_code == 200, result.text
        wait_for(lambda: service.get(sid).get("pending_request") is None)
        expected = "closed" if ids == ["A16"] else "waiting"
        wait_for(lambda: service.get(sid)["status"] == expected)
        assert service.get(sid)["status"] == expected, manager.get(response.json()["job_id"]).error
    execute(through("A13")[::-1])
    run_id = service.get(sid)["run_id"]
    execute(["A12"])
    execute(["A14"])
    assert service.get(sid)["run_id"] == run_id
    execute(["A15"])
    directory = rrig.tmp_path / "data" / DATA_TYPE / run_id
    config = yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf-8"))
    assert config["completion_status"] == "completed" and config["stage_frequencies"]["response"]["rf_frequency_Hz"] == 3020
    execute(["A16"])


def test_communication_close_and_backend_restart_do_not_restore_hardware(workflow_client, rrig):
    client, service, manager = workflow_client
    response = client.post(f"{BASE}/sessions", json={"parameters": rrig.params.to_external()})
    sid = response.json()["session_id"]
    wait_for(lambda: service.get(sid)["status"] == "waiting")
    state = service.get(sid)
    assert client.post(f"{BASE}/sessions/{sid}/steps", json={"revision": state["revision"], "step_ids": through("A13")}).status_code == 200
    wait_for(lambda: service.get(sid).get("pending_request") is None)
    run_id = service.get(sid)["run_id"]
    service.close_communication()
    wait_for(lambda: service.get(sid)["status"] == "closed")
    config = yaml.safe_load((rrig.tmp_path / "data" / DATA_TYPE / run_id / "experiment_config.yaml").read_text(encoding="utf-8"))
    assert config["safety_shutdown"]["completed"] and config["completion_status"] == "cancelled"
    assert not manager.get(response.json()["job_id"]).cancellation.cancelled
    replacement = RFRSessionService(lambda: rrig.tmp_path, JobManager())
    assert replacement.active() is None


def start_session(client, service, rrig):
    response = client.post(f"{BASE}/sessions", json={"parameters": rrig.params.to_external()})
    assert response.status_code == 200, response.text
    value = response.json()
    wait_for(lambda: service.get(value["session_id"])["status"] == "waiting")
    return value["session_id"], value["job_id"]


def test_real_process_rejects_revision_duplicate_and_parallel_commands(workflow_client, rrig, monkeypatch):
    client, service, manager = workflow_client
    monkeypatch.setenv("RF_R_CAPTURE_BARRIER", "1")
    sid, job_id = start_session(client, service, rrig)
    base = f"{BASE}/sessions/{sid}"
    response = client.post(base + "/steps", json={"revision": 0, "step_ids": ["A01"]})
    request_id = response.json()["pending_request"]
    wait_for(lambda: service.get(sid).get("pending_request") is None)
    state = service.get(sid)
    assert client.get(base).json()["revision"] == state["revision"]
    assert client.get(f"{BASE}/sessions/active").json()["session_id"] == sid
    assert client.post(base + "/steps", json={"revision": 0, "step_ids": ["A01"]}).headers["X-Error-Code"] == "revision_conflict"
    item = service.sessions[sid]
    with service.lock:
        item["state"]["pending_request"] = request_id
        item["queue"].put({"request_id": request_id, "revision": state["revision"], "action": "steps", "step_ids": ["A01"]})
    wait_for(lambda: service.get(sid).get("pending_request") is None)
    assert "重复" in service.get(sid)["error"]
    assert service.get(sid)["revision"] == state["revision"]
    response = client.post(base + "/steps", json={"revision": state["revision"], "step_ids": through("A13")})
    assert response.status_code == 200, response.text
    try:
        wait_for(lambda: (rrig.tmp_path / "capture-entered").exists())
        current = service.get(sid)["revision"]
        assert client.post(base + "/steps", json={"revision": current, "step_ids": ["A01"]}).status_code == 409
        assert client.patch(base + "/parameters", json={"revision": current, "parameters": {"Z_RF_NOISE_FREQ": 2999}}).status_code == 409
        assert not manager.hardware_lock.acquire(blocking=False)
    finally:
        (rrig.tmp_path / "capture-release").touch()
    wait_for(lambda: service.get(sid).get("pending_request") is None)
    run_id = service.get(sid)["run_id"]
    assert (rrig.tmp_path / "data" / DATA_TYPE / run_id / "raw/response_data.npz").is_file()
    assert client.post(base + "/finish", json={"revision": service.get(sid)["revision"]}).status_code == 200
    wait_for(lambda: manager.get(job_id).status == "completed")
    assert manager.hardware_lock.acquire(blocking=False)
    manager.hardware_lock.release()


@pytest.mark.parametrize("ending", ["cancel", "eof", "shutdown"])
def test_real_process_holds_lock_until_cancel_eof_or_backend_shutdown_finishes(workflow_client, rrig, monkeypatch, ending):
    client, service, manager = workflow_client
    monkeypatch.setenv("RF_R_SHUTDOWN_BARRIER", "1")
    monkeypatch.setenv("RF_R_CAPTURE_BARRIER", "1")
    sid, job_id = start_session(client, service, rrig)
    response = client.post(f"{BASE}/sessions/{sid}/steps", json={"revision": 0, "step_ids": through("A13")})
    assert response.status_code == 200, response.text
    wait_for(lambda: (rrig.tmp_path / "capture-entered").exists())
    run_id = service.get(sid)["run_id"]
    shutdown_thread = None
    try:
        if ending == "cancel":
            manager.get(job_id).cancellation.cancel()
        elif ending == "eof":
            service.close_communication()
        else:
            shutdown_thread = Thread(target=service.shutdown)
            shutdown_thread.start()
        wait_for(lambda: (rrig.tmp_path / "shutdown-entered").exists())
        assert manager.get(job_id).status == "running"
        assert not manager.hardware_lock.acquire(blocking=False), "恢复期间硬件锁必须继续持有"
        if shutdown_thread:
            assert shutdown_thread.is_alive()
    finally:
        (rrig.tmp_path / "capture-release").touch()
        (rrig.tmp_path / "shutdown-release").touch()
        if shutdown_thread:
            shutdown_thread.join(timeout=20)
            assert not shutdown_thread.is_alive()
    wait_for(lambda: manager.get(job_id).status in ("cancelled", "completed"))
    assert service.get(sid)["status"] == "closed" and service.active() is None
    config = yaml.safe_load((rrig.tmp_path / "data" / DATA_TYPE / run_id / "experiment_config.yaml").read_text(encoding="utf-8"))
    assert config["completion_status"] == "cancelled" and config["safety_shutdown"]["completed"]
    assert manager.hardware_lock.acquire(blocking=False)
    manager.hardware_lock.release()


def test_staged_and_legacy_analysis_share_directory_lock_and_keep_history_parameters(client, tmp_path, monkeypatch):
    directory = make_run(tmp_path)
    before = yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf-8"))["parameters"]
    raw = {path.name: path.read_bytes() for path in (directory / "raw").iterdir()}
    entered, release = Event(), Event()
    execute = ADAPTER._execute
    def blocked(module, **kwargs):
        if module == ADAPTER.analysis_module:
            entered.set()
            assert release.wait(20), "分析屏障未释放"
        return execute(module, **kwargs)
    monkeypatch.setattr(ADAPTER, "_execute", blocked)
    state = client.get(f"{BASE}/runs/first/analysis")
    assert state.status_code == 200, state.text
    revision = state.json()["revision"]
    body = {"revision": revision, "step_ids": ["B01", "B02"], "analysis_parameters": {"NOISE_NPERSEG": 256}}
    response = client.post(f"{BASE}/runs/first/analysis", json=body)
    assert response.status_code == 200, response.text
    try:
        assert entered.wait(5)
        assert client.post("/api/runs/first/analyze", json={"experiment_id": EXPERIMENT_ID}).status_code == 409
        assert client.post(f"{BASE}/runs/first/analysis", json=body).status_code == 409
        assert main.manager.hardware_lock.acquire(blocking=False)
        main.manager.hardware_lock.release()
    finally:
        release.set()
    job = main.manager.get(response.json()["id"])
    wait_for(lambda: job.status in ("completed", "failed"))
    assert job.status == "completed", job.error
    assert client.post(f"{BASE}/runs/first/analysis", json=body).headers["X-Error-Code"] == "revision_conflict"
    entered.clear()
    release.clear()
    response = client.post("/api/runs/first/analyze", json={"experiment_id": EXPERIMENT_ID})
    assert response.status_code == 200, response.text
    try:
        assert entered.wait(5)
        assert client.post(f"{BASE}/runs/first/analysis", json={**body, "revision": revision + 1}).status_code == 409
    finally:
        release.set()
    job = main.manager.get(response.json()["id"])
    wait_for(lambda: job.status in ("completed", "failed"))
    assert job.status == "completed", job.error
    assert yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf-8"))["parameters"] == before
    assert raw == {path.name: path.read_bytes() for path in (directory / "raw").iterdir()}
