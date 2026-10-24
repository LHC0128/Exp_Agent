"""主场示波器噪声谱 API 的假仪器真实子进程、互斥、历史及独立分析。"""
from pathlib import Path
from threading import Event
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lab_workflows.experiment_modules.mx_main_field_scope_noise_spectrum.definition import ADAPTER
from lab_workflows.experiment_modules.mx_main_field_scope_noise_spectrum.test_workflow import small_params, copy_config
from lab_workflows.experiment_modules.mx_main_field_scope_noise_spectrum.test_analysis import synthetic_run
from .jobs import JobManager
from .mx_main_field_scope_noise_spectrum import MxMainFieldScopeNoiseSessionService, create_router

ID = "mx-main-field-scope-noise-spectrum"
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
    relative = "lab_workflows/experiment_modules/mx_main_field_scope_noise_spectrum/workflow.excalidraw"
    diagram = tmp_path / relative
    diagram.parent.mkdir(parents=True)
    diagram.write_bytes((Path(__file__).resolve().parents[2] / relative).read_bytes())
    manager = JobManager()
    service = MxMainFieldScopeNoiseSessionService(lambda: tmp_path, manager)
    original = ADAPTER._execute

    def execute(module, **kwargs):
        if module.endswith(".session"):
            module = "lab_workflows.experiment_modules.mx_main_field_scope_noise_spectrum.test_workflow"
        return original(module, **kwargs)

    monkeypatch.setattr(ADAPTER, "_execute", execute)
    monkeypatch.setenv("MX_SCOPE_TEST_ROOT", str(tmp_path))
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


def test_real_child_dependency_revision_refresh_and_global_lock(client):
    flow = client.get(BASE + "/workflow")
    assert flow.status_code == 200, flow.text
    assert len(flow.json()["acquisition"]) == 10 and len(flow.json()["analysis"]) == 2
    assert client.get(BASE + "/workflow/diagram").json()["type"] == "excalidraw"
    session_id = create(client)
    assert client.manager.hardware_lock.locked()
    assert client.get(BASE + "/sessions/active").json()["session_id"] == session_id
    assert client.post(BASE + "/sessions", json={"parameters": {}}).status_code == 409
    assert client.post(f"{BASE}/sessions/{session_id}/steps", json={"revision": 0, "step_ids": ["A07"]}).status_code == 422
    state = steps(client, session_id, [f"A{i:02d}" for i in range(1, 8)])
    assert client.post(f"{BASE}/sessions/{session_id}/finish", json={"revision": 0}).status_code == 409
    assert client.get(f"{BASE}/sessions/{session_id}").json()["revision"] == state["revision"]
    assert MxMainFieldScopeNoiseSessionService(lambda: client.root, client.manager).active() is None
    assert client.get(f"{BASE}/runs/{state['run_id']}/analysis").status_code == 409
    state = steps(client, session_id, ["A08", "A09"])
    assert state["safety_shutdown"]["completed"]
    response = client.post(f"{BASE}/sessions/{session_id}/finish", json={"revision": state["revision"]})
    assert response.status_code == 200
    wait(lambda: client.service.get(session_id)["status"] == "closed")
    wait(lambda: not client.manager.hardware_lock.locked())
    assert client.service.get(session_id)["steps"]["A10"]["status"] == "completed"


@pytest.mark.parametrize("method", ["cancel", "eof", "shutdown"])
def test_cancel_eof_and_shutdown_restore_before_unlock(client, method):
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
    assert final["outputs"]["GS200"] == "运行前状态"
    assert final["outputs"]["Y"] == "OFF"


def test_history_readonly_snapshots_and_real_analysis_directory_isolation(client, monkeypatch):
    base = client.root / "data" / ADAPTER.data_type
    run = synthetic_run(base / "1018_test")
    other = synthetic_run(base / "1018_other")
    raw = {path: path.read_bytes() for path in run.rglob("*") if path.is_file()}
    listing = client.get(BASE + "/runs?limit=50&offset=0")
    assert listing.status_code == 200 and listing.json()["total"] == 2
    summary = client.get(BASE + f"/runs/{run.name}/summary").json()
    assert summary["parameters"]["CONTROL_FREQUENCY_POINTS"] == 21
    assert summary["analysis_status"] == "not_started" and not summary["artifacts"]
    assert client.get(BASE + "/runs/missing/summary").status_code == 404
    assert client.get(BASE + "/runs/..%2Fescape/summary").status_code == 404
    state = client.get(BASE + f"/runs/{run.name}/analysis").json()
    assert {path: path.read_bytes() for path in raw} == raw
    body = dict(revision=state["revision"], step_ids=["B01", "B02"], analysis_parameters={"FIT_EXAMPLE_PLOT_COUNT": 2})
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
    assert client.get(BASE + f"/runs/{run.name}/analysis").json()["options"]["FIT_EXAMPLE_PLOT_COUNT"] == 2
    assert client.get(BASE + f"/runs/{other.name}/analysis").json()["options"]["FIT_EXAMPLE_PLOT_COUNT"] == 1
    assert client.post(BASE + f"/runs/{run.name}/analysis", json=body).status_code == 409
    refreshed = client.get(BASE + f"/runs/{run.name}/summary").json()
    assert refreshed["analysis_status"] in ("completed", "quality_failed")
    assert refreshed["parameters"]["FIT_EXAMPLE_PLOT_COUNT"] == 1
    assert any(name.endswith("noise_spectrum_2d.png") for name in refreshed["artifacts"])
    first_file = next(iter(refreshed["artifacts"].values()))
    assert client.get(first_file).status_code == 200
    assert {path: path.read_bytes() for path in raw} == raw


def test_legacy_history_projection_uses_model_without_current_defaults(client):
    import yaml
    run = synthetic_run(client.root / "data" / ADAPTER.data_type / "legacy", schema=6)
    path = run / "experiment_config.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf8"))
    values = config["parameters"]
    for key in ("FIXED_PARAMS.X_magnetic_field", "FIXED_PARAMS.Y_magnetic_field",
                "GLOBAL_CORE_FIT_FREQUENCY_MIN_HZ", "SYSTEM_SETTLE_S"):
        values.pop(key)
    values.update(GLOBAL_FIT_FREQUENCY_MIN_HZ=5000, GLOBAL_FIT_FREQUENCY_MAX_HZ=45000,
                  TEMP_SWITCH_OFF_SETTLE_S=0.8)
    # 版本 3 的旧 XY 关闭规则与版本 6 的低频分析迁移都由模型执行。
    config["schema_version"] = 3
    path.write_text(yaml.safe_dump(config), encoding="utf8")
    original = path.read_bytes()
    response = client.get(BASE + f"/runs/{run.name}/summary")
    assert response.status_code == 200, response.text
    restored = response.json()["parameters"]
    assert restored["FIXED_PARAMS.X_magnetic_field"] == restored["FIXED_PARAMS.Y_magnetic_field"] == 0
    assert restored["SYSTEM_SETTLE_S"] == 0.8
    assert restored["GLOBAL_FIT_FREQUENCY_MIN_HZ"] == 500
    assert restored["GLOBAL_CORE_FIT_FREQUENCY_MIN_HZ"] == 2000
    assert path.read_bytes() == original


def test_main_routes_discovery_and_lifespan_registration():
    from . import main
    paths = main.app.openapi()["paths"]
    assert BASE + "/workflow" in paths and BASE + "/runs/{run_id}/analysis" in paths
    assert main.mx_main_field_scope_sessions in main._workflow_session_services()
    from lab_workflows.experiment_modules.mx_main_field_scope_noise_spectrum.definition import DEFINITION
    assert main._experiment_public(DEFINITION)["id"] == ID
