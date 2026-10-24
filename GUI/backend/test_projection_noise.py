"""投影噪声真实假仪器子进程、会话互斥、历史与离线分析 API 回归。"""
from pathlib import Path
from threading import Event
import time
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from lab_workflows.experiment_modules.projection_noise.definition import ADAPTER
from lab_workflows.experiment_modules.projection_noise.test_workflow import small_params, copy_config
from lab_workflows.experiment_modules.projection_noise.test_analysis import synthetic_run, legacy_run
from .jobs import JobManager
from .projection_noise import ProjectionNoiseSessionService, create_router

ID = "projection-noise"
BASE = "/api/experiments/" + ID


def wait(predicate):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if predicate(): return
        time.sleep(0.025)
    raise AssertionError("假仪器子进程超时")


@pytest.fixture
def client(tmp_path, monkeypatch):
    copy_config(tmp_path)
    relative = "lab_workflows/experiment_modules/projection_noise/workflow.excalidraw"
    diagram = tmp_path / relative
    diagram.parent.mkdir(parents=True)
    diagram.write_bytes((Path(__file__).resolve().parents[2] / relative).read_bytes())
    manager = JobManager()
    service = ProjectionNoiseSessionService(lambda: tmp_path, manager)
    original = ADAPTER._execute
    def execute(module, **kwargs):
        if module.endswith(".session"):
            module = "lab_workflows.experiment_modules.projection_noise.test_workflow"
        return original(module, **kwargs)
    monkeypatch.setattr(ADAPTER, "_execute", execute)
    monkeypatch.setenv("PROJECTION_TEST_ROOT", str(tmp_path))
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
    response = client.post(f"{BASE}/sessions/{session_id}/steps", json=dict(revision=revision, step_ids=ids))
    assert response.status_code == 200, response.text
    wait(lambda: client.service.get(session_id)["pending_request"] is None)
    value = client.service.get(session_id)
    assert value["error"] is None, value
    return value


def test_real_child_dependency_revision_refresh_and_hardware_lock(client):
    flow = client.get(BASE + "/workflow")
    assert flow.status_code == 200
    assert len(flow.json()["acquisition"]) == 9 and len(flow.json()["analysis"]) == 2
    assert client.get(BASE + "/workflow/diagram").json()["type"] == "excalidraw"
    sid = create(client)
    assert client.manager.hardware_lock.locked()
    assert client.get(BASE + "/sessions/active").json()["session_id"] == sid
    assert client.post(BASE + "/sessions", json={"parameters": small_params().to_external()}).status_code == 409
    assert client.post(f"{BASE}/sessions/{sid}/steps", json=dict(revision=0, step_ids=["A07"])).status_code == 422
    value = steps(client, sid, [f"A{i:02d}" for i in range(1, 7)])
    assert client.post(f"{BASE}/sessions/{sid}/finish", json={"revision": 0}).status_code == 409
    assert client.get(f"{BASE}/sessions/{sid}").json()["revision"] == value["revision"]
    assert ProjectionNoiseSessionService(lambda: client.root, client.manager).active() is None
    assert client.get(f"{BASE}/runs/{value['run_id']}/analysis").status_code == 409
    value = steps(client, sid, ["A07", "A08"])
    assert value["safety_shutdown"]["completed"]
    assert value["outputs"]["Pump 载波"] == value["outputs"]["Pump 门控"] == "保持当前状态（本实验不写入）"
    response = client.post(f"{BASE}/sessions/{sid}/finish", json={"revision": value["revision"]})
    assert response.status_code == 200
    wait(lambda: client.service.get(sid)["status"] == "closed")
    wait(lambda: not client.manager.hardware_lock.locked())
    assert client.service.get(sid)["steps"]["A09"]["status"] == "completed"


@pytest.mark.parametrize("method", ["cancel", "eof", "shutdown"])
def test_cancel_eof_shutdown_restore_before_unlock_and_never_touch_pump(client, method):
    sid = create(client)
    value = steps(client, sid, [f"A{i:02d}" for i in range(1, 7)])
    if method == "cancel": client.manager.get(value["job_id"]).cancellation.cancel()
    elif method == "eof": client.service.close_communication()
    else: client.service.shutdown()
    wait(lambda: client.service.get(sid)["status"] == "closed")
    wait(lambda: not client.manager.hardware_lock.locked())
    final = client.service.get(sid)
    assert final["safety_shutdown"]["completed"]
    assert final["outputs"]["GS200"] == "运行前状态" and final["outputs"]["Y"] == "OFF"
    assert final["outputs"]["Pump 载波"] == "保持当前状态（本实验不写入）"


def test_history_snapshots_readonly_and_actual_analysis_isolated_per_directory(client, monkeypatch):
    base = client.root / "data" / ADAPTER.data_type
    run, other = synthetic_run(base / "1024_test"), synthetic_run(base / "1024_other")
    original = {p: p.read_bytes() for p in run.rglob("*") if p.is_file()}
    listing = client.get(BASE + "/runs?limit=50&offset=0")
    assert listing.status_code == 200 and listing.json()["total"] == 2
    summary = client.get(BASE + f"/runs/{run.name}/summary").json()
    assert summary["parameters"]["WELCH_NPERSEG"] == 256
    assert summary["analysis_status"] == "not_started" and not summary["artifacts"]
    assert client.get(BASE + "/runs/missing/summary").status_code == 404
    assert client.get(BASE + "/runs/..%2Fescape/summary").status_code == 404
    state = client.get(BASE + f"/runs/{run.name}/analysis").json()
    assert {p: p.read_bytes() for p in original} == original
    body = dict(revision=state["revision"], step_ids=["B01", "B02"], analysis_parameters={"WELCH_NPERSEG": 128})
    execute, entered, release = ADAPTER._execute, Event(), Event()
    def blocked(module, **kwargs):
        if module == ADAPTER.analysis_module:
            entered.set(); assert release.wait(30)
        return execute(module, **kwargs)
    monkeypatch.setattr(ADAPTER, "_execute", blocked)
    try:
        response = client.post(BASE + f"/runs/{run.name}/analysis", json=body)
        assert response.status_code == 200, response.text
        jid = response.json()["id"]
        assert entered.wait(5)
        assert client.post(BASE + f"/runs/{run.name}/analysis", json=body).status_code == 409
    finally:
        release.set()
    wait(lambda: client.manager.get(jid).status in ("completed", "failed"))
    assert client.manager.get(jid).status == "completed", client.manager.get(jid).error
    assert client.get(BASE + f"/runs/{run.name}/analysis").json()["options"]["WELCH_NPERSEG"] == 128
    assert client.get(BASE + f"/runs/{other.name}/analysis").json()["options"]["WELCH_NPERSEG"] == 256
    assert client.post(BASE + f"/runs/{run.name}/analysis", json=body).status_code == 409
    refreshed = client.get(BASE + f"/runs/{run.name}/summary").json()
    assert refreshed["analysis_status"] in ("completed", "quality_failed")
    assert refreshed["parameters"]["WELCH_NPERSEG"] == 256
    assert any(name.endswith("scope_psd_overview.png") for name in refreshed["artifacts"])
    assert client.get(next(iter(refreshed["artifacts"].values()))).status_code == 200
    assert {p: p.read_bytes() for p in original} == original


def test_legacy_hf2_history_and_current_model_migration_without_defaults(client):
    base = client.root / "data" / ADAPTER.data_type
    old = legacy_run(base / "legacy_hf2")
    value = client.get(BASE + f"/runs/{old.name}/summary")
    assert value.status_code == 200 and value.json()["parameters"] == {}
    assert client.get(BASE + f"/runs/{old.name}/analysis").json()["options"] == {}
    run = synthetic_run(base / "old_sds", schema=4, marked=False)
    config = run / "experiment_config.yaml"
    before = config.read_bytes()
    value = client.get(BASE + f"/runs/{run.name}/summary").json()["parameters"]
    assert value["SYSTEM_SETTLE_S"] == 0.001 and value["FIXED_PARAMS.Probe_laser_power"] == 0.3
    assert config.read_bytes() == before


def test_main_registration_generic_reanalysis_and_shared_directory_lock(client, monkeypatch):
    from . import main
    paths = main.app.openapi()["paths"]
    assert BASE + "/workflow" in paths and BASE + "/runs/{run_id}/analysis" in paths
    assert main.projection_noise_sessions in main._workflow_session_services()
    from lab_workflows.experiment_modules.projection_noise.definition import DEFINITION
    assert main._experiment_public(DEFINITION)["id"] == ID
    run = synthetic_run(client.root / "data" / ADAPTER.data_type / "generic")
    monkeypatch.setattr(main, "ROOT", client.root)
    monkeypatch.setattr(main, "manager", client.manager)
    monkeypatch.setattr(main, "_workflow_session_services", lambda: [client.service])
    # 两个入口使用相同的目录级任务 kind，不能并发改写同一结果。
    execute, entered, release = ADAPTER._execute, Event(), Event()
    def blocked(module, **kwargs):
        if module == ADAPTER.analysis_module:
            entered.set(); assert release.wait(30)
        return execute(module, **kwargs)
    monkeypatch.setattr(ADAPTER, "_execute", blocked)
    state = client.get(BASE + f"/runs/{run.name}/analysis").json()
    try:
        job = main._create_analysis_job(run.name, main.AnalysisBody(experiment_id=ID))
        assert entered.wait(5)
        response = client.post(BASE + f"/runs/{run.name}/analysis", json=dict(
            revision=state["revision"], step_ids=["B01", "B02"], analysis_parameters={}))
        assert response.status_code == 409
    finally:
        release.set()
    wait(lambda: client.manager.get(job["id"]).status in ("completed", "failed"))
    assert client.manager.get(job["id"]).status == "completed", client.manager.get(job["id"]).error
    assert (run / "results/analysis.json").is_file()
