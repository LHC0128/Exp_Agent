"""Bell Bloom Z 电压频率标定 真实子进程会话、全局互斥、历史与独立分析接口。"""
from pathlib import Path
import time
import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from lab_workflows.experiment_modules.bell_bloom_z_field_calibration.definition import ADAPTER
from lab_workflows.experiment_modules.bell_bloom_z_field_calibration.test_workflow import copy_config, small_params
from .jobs import JobManager
from .bell_bloom_z_field_calibration import BellBloomZFieldCalibrationSessionService, create_router

BASE = "/api/experiments/bell-bloom-z-field-calibration"


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
    diagram = tmp_path / "lab_workflows/experiment_modules/bell_bloom_z_field_calibration/workflow.excalidraw"
    diagram.parent.mkdir(parents=True)
    diagram.write_bytes((Path(__file__).resolve().parents[2] /
        "lab_workflows/experiment_modules/bell_bloom_z_field_calibration/workflow.excalidraw").read_bytes())
    manager = JobManager()
    service = BellBloomZFieldCalibrationSessionService(lambda: tmp_path, manager)
    original = ADAPTER._execute

    def execute(module, **kwargs):
        if module.endswith(".session"):
            module = "lab_workflows.experiment_modules.bell_bloom_z_field_calibration.test_workflow"
        return original(module, **kwargs)

    monkeypatch.setattr(ADAPTER, "_execute", execute)
    monkeypatch.setenv("BB_Z_FIELD_TEST_ROOT", str(tmp_path))
    app = FastAPI()
    app.include_router(create_router(service))
    from . import main
    monkeypatch.setattr(main, "ROOT", tmp_path)
    monkeypatch.setattr(main, "manager", manager)
    monkeypatch.setattr(main, "_workflow_session_services", lambda: (service,))
    app.add_api_route(BASE + "/runs", main.bell_bloom_z_runs, methods=["GET"])
    app.add_api_route(BASE + "/runs/{run_id}/summary", main.bell_bloom_z_summary, methods=["GET"])
    app.add_api_route("/api/runs/{run_id}/analyze", main.analyze_run, methods=["POST"])
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
    assert client.get(f"{BASE}/runs/{state['run_id']}/analysis").status_code == 409
    assert client.post(f"{BASE}/sessions/{session_id}/finish", json={"revision": 0}).status_code == 409
    assert client.get(f"{BASE}/sessions/{session_id}").json()["revision"] == state["revision"]
    # 新服务不能从磁盘恢复仍带输出的硬件会话。
    assert BellBloomZFieldCalibrationSessionService(lambda: client.root, client.manager).active() is None
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
    assert final["outputs"]["Pump 脉冲"] == "参考脉冲 ON"
    config = yaml.safe_load((client.root / "data/Bell_Bloom_Z_Field_Calibration" / state["run_id"] /
        "experiment_config.yaml").read_text(encoding="utf-8"))
    assert config["completion_status"] in ("cancelled", "failed")


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
    assert refreshed["parameters"]["LINEAR_R_SQUARED_MIN"] == 0.8
    assert any(name.endswith("z_frequency_calibration.png") for name in refreshed["artifacts"])
    assert "z_frequency_calibration.png" in refreshed["artifacts"]
    assert refreshed["analysis"]["K_Z_Hz_per_V"] == pytest.approx(1000, rel=1e-4)
    assert refreshed["safety_shutdown"]["completed"]
    assert client.get(f"{BASE}/runs/{run_id}/analysis").json()["options"]["LINEAR_R_SQUARED_MIN"] == 0.9999


def test_legacy_reanalysis_uses_same_busy_guard_and_history_contract(client):
    session_id = create(client)
    state = steps(client, session_id, [f"A{index:02d}" for index in range(1, 10)])
    run_id = state["run_id"]
    summary = client.get(f"{BASE}/runs/{run_id}/summary").json()
    assert summary["analysis"] is None and summary["parameters"]["LINEAR_R_SQUARED_MIN"] == 0.8
    response = client.post(f"/api/runs/{run_id}/analyze", json={"experiment_id": "bell-bloom-z-field-calibration"})
    assert response.status_code == 200, response.text
    job_id = response.json()["id"]
    pending = client.post(f"{BASE}/runs/{run_id}/analysis", json={"revision": 0, "step_ids": ["B01"]})
    assert pending.status_code == 409
    wait(lambda: client.manager.get(job_id).status in ("completed", "failed"))
    assert client.manager.get(job_id).status == "completed", client.manager.get(job_id).error
    summary = client.get(f"{BASE}/runs/{run_id}/summary").json()
    assert summary["analysis"]["success"] and "z_frequency_residuals.png" in summary["artifacts"]
    assert summary["parameters"]["LINEAR_R_SQUARED_MIN"] == 0.8
    path = client.root / "data/Bell_Bloom_Z_Field_Calibration" / run_id / "experiment_config.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config["completion_status"] = "acquired"
    path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    assert client.post(f"/api/runs/{run_id}/analyze", json={"experiment_id": "bell-bloom-z-field-calibration"}).status_code == 409


def test_service_rejects_unsupported_raw_format_and_wrong_identity(client):
    directory = client.root / "data/Bell_Bloom_Z_Field_Calibration/bad"
    directory.mkdir(parents=True)
    path = directory / "experiment_config.yaml"
    config = dict(experiment_id="bell-bloom-z-field-calibration", parameters={}, completion_status="completed", raw_format_version=2)
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    assert client.get(BASE + "/runs/bad/analysis").status_code == 422
    config.update(experiment_id="mx-z-field-calibration", raw_format_version=1)
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    assert client.get(BASE + "/runs/bad/analysis").status_code == 422


@pytest.mark.parametrize("method", ["cancel", "eof", "shutdown"])
def test_active_scan_cancellation_preserves_real_partial_files_and_recovers(client, monkeypatch, method):
    monkeypatch.setenv("BB_Z_FIELD_TEST_SLOW", "1")
    session_id = create(client)
    state = steps(client, session_id, [f"A{index:02d}" for index in range(1, 8)])
    run_id = state["run_id"]
    response = client.post(f"{BASE}/sessions/{session_id}/steps", json={"revision": state["revision"], "step_ids": ["A08"]})
    assert response.status_code == 200, response.text
    directory = client.root / "data/Bell_Bloom_Z_Field_Calibration" / run_id
    wait(lambda: list((directory / "raw").glob("z_*/frequency_*_attempt_*.npz")))
    if method == "cancel":
        client.manager.get(state["job_id"]).cancellation.cancel()
    elif method == "eof":
        client.service.close_communication()
    else:
        client.service.shutdown()
    wait(lambda: client.service.get(session_id)["status"] == "closed")
    wait(lambda: not client.manager.hardware_lock.locked())
    assert client.service.get(session_id)["safety_shutdown"]["completed"]
    files = list((directory / "raw").glob("z_*/frequency_*_attempt_*.npz"))
    assert 1 <= len(files) < 27
    config = yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf-8"))
    assert config["completion_status"] == "cancelled"


def test_main_app_exposes_this_service_without_overriding_legacy_summary():
    from . import main
    routes = {route.path: route for route in main.app.routes if hasattr(route, "path")}
    with TestClient(main.app) as http:
        assert http.get(BASE + "/workflow").status_code == 200
        assert http.get(BASE + "/sessions/active").status_code == 200
    assert routes[BASE + "/runs/{run_id}/summary"].endpoint is main.bell_bloom_z_summary
    assert any(service.adapter.id == "bell-bloom-z-field-calibration" for service in main._workflow_session_services())


@pytest.mark.parametrize("method", ["cancel", "eof", "shutdown"])
def test_real_child_keeps_hardware_lock_until_safety_shutdown_returns(client, monkeypatch, method):
    from threading import Thread
    monkeypatch.setenv("BB_Z_FIELD_TEST_SHUTDOWN_BARRIER", "1")
    session_id = create(client)
    state = steps(client, session_id, [f"A{index:02d}" for index in range(1, 8)])
    thread = None
    try:
        if method == "cancel":
            client.manager.get(state["job_id"]).cancellation.cancel()
        elif method == "eof":
            client.service.close_communication()
        else:
            thread = Thread(target=client.service.shutdown)
            thread.start()
        wait(lambda: (client.root / "shutdown_started").is_file())
        assert client.manager.hardware_lock.locked()
        assert client.manager.get(state["job_id"]).status == "running"
        assert client.service.get(session_id)["status"] != "closed"
    finally:
        (client.root / "shutdown_release").touch()
        if thread:
            thread.join(timeout=20)
            assert not thread.is_alive()
    wait(lambda: client.service.get(session_id)["status"] == "closed")
    wait(lambda: not client.manager.hardware_lock.locked())
    assert client.service.get(session_id)["safety_shutdown"]["completed"]


def test_real_child_parameter_patch_matches_impacts_without_executing_stale_modules(client):
    session_id = create(client)
    state = steps(client, session_id, [f"A{index:02d}" for index in range(1, 8)])
    directory = client.root / "data/Bell_Bloom_Z_Field_Calibration" / state["run_id"]
    saved_parameters = yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf-8"))["parameters"]
    values = {"PUMP_MOD_DUTY": 10, "SYSTEM_SETTLE_S": 0.002}
    body = {"revision": state["revision"], "parameters": values}
    response = client.patch(f"{BASE}/sessions/{session_id}/parameters", json=body)
    assert response.status_code == 200, response.text
    assert client.patch(f"{BASE}/sessions/{session_id}/parameters", json=body).status_code == 409
    wait(lambda: client.service.get(session_id)["pending_request"] is None)
    applied = client.service.get(session_id)
    assert applied["error"] is None and applied["revision"] == state["revision"] + 1
    impacts = client.get(BASE + "/workflow").json()["parameter_impacts"]["acquisition"]
    affected = {item for key in values for item in impacts[key]}
    completed = {key for key, value in state["steps"].items() if value["status"] == "completed"}
    assert {key for key, value in applied["steps"].items() if value["status"] == "stale"} == affected & completed
    assert applied["steps"]["A05"]["status"] == "completed"
    assert not list((directory / "raw").glob("z_*"))
    assert yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf-8"))["parameters"] == saved_parameters
    assert client.post(f"{BASE}/sessions/{session_id}/steps", json={"revision": applied["revision"], "step_ids": ["A08"]}).status_code == 422
    assert client.post(f"{BASE}/sessions/{session_id}/finish", json={"revision": applied["revision"]}).status_code == 200
    wait(lambda: client.service.get(session_id)["status"] == "closed")
    wait(lambda: not client.manager.hardware_lock.locked())
