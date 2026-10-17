"""真实子进程假仪器：互斥、修订、恢复、EOF、取消和独立分析。"""
from dataclasses import replace
from pathlib import Path
from threading import Event
import json
import yaml
import time
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from lab_workflows.experiment_modules.mx_z_optimal_control_xy_rf_sensitivity import models
from lab_workflows.experiment_modules.mx_z_optimal_control_xy_rf_sensitivity.definition import ADAPTER
from lab_workflows.experiment_modules.mx_z_optimal_control_xy_rf_sensitivity.test_workflow import copy_config
from .jobs import JobManager
from .mx_z_optimal_control_xy_rf_sensitivity import MxZOptimalControlXYRFSessionService, create_router

BASE = "/api/experiments/" + ADAPTER.id


def wait(predicate):
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.025)
    raise AssertionError("假仪器子进程超时")


@pytest.fixture
def client(tmp_path, monkeypatch):
    copy_config(tmp_path)
    diagram = tmp_path / "lab_workflows/experiment_modules/mx_z_optimal_control_xy_rf_sensitivity/workflow.excalidraw"
    diagram.parent.mkdir(parents=True)
    diagram.write_bytes((Path(__file__).resolve().parents[2] / diagram.relative_to(tmp_path)).read_bytes())
    manager = JobManager()
    service = MxZOptimalControlXYRFSessionService(lambda: tmp_path, manager)
    execute = ADAPTER._execute
    def child(module, **kwargs):
        if module.endswith(".session"):
            module = "lab_workflows.experiment_modules.mx_z_optimal_control_xy_rf_sensitivity.test_workflow"
        return execute(module, **kwargs)
    monkeypatch.setattr(ADAPTER, "_execute", child)
    from lab_workflows.experiment_modules import mx_z_control_params
    monkeypatch.setattr(mx_z_control_params, "find_project_root", lambda: tmp_path)
    monkeypatch.setenv("MX_Z_XY_RF_TEST_ROOT", str(tmp_path))
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
    from lab_workflows.experiment_modules.mx_z_optimal_control_xy_rf_sensitivity.test_workflow import parameters
    params = parameters()
    response = client.post(BASE + "/sessions", json={"parameters": params.to_external()})
    assert response.status_code == 200, response.text
    session_id = response.json()["session_id"]
    wait(lambda: client.service.get(session_id)["status"] in ("waiting", "closed"))
    assert client.service.get(session_id)["status"] == "waiting", "\n".join(event.get("message", "") for event in client.manager.get(response.json()["job_id"]).events)
    return session_id


def steps(client, session_id, selected):
    state = client.service.get(session_id)
    response = client.post(f"{BASE}/sessions/{session_id}/steps", json={"revision": state["revision"], "step_ids": selected})
    assert response.status_code == 200, response.text
    wait(lambda: client.service.get(session_id)["pending_request"] is None)
    state = client.service.get(session_id)
    assert not state.get("error"), state
    return state


def test_child_lock_dependency_revision_refresh_and_finish(client):
    flow = client.get(BASE + "/workflow").json()
    assert len(flow["acquisition"]) == 7 and len(flow["analysis"]) == 3
    assert client.get(BASE + "/workflow/diagram").json()["type"] == "excalidraw"
    session_id = create(client)
    assert client.manager.hardware_lock.locked()
    assert client.get(BASE + "/sessions/active").json()["session_id"] == session_id
    assert client.post(BASE + "/sessions", json={"parameters": client.service.get(session_id)["parameters"]}).status_code == 409
    assert client.post(f"{BASE}/sessions/{session_id}/steps", json={"revision": 0, "step_ids": ["A04"]}).status_code == 422
    state = steps(client, session_id, ["A01", "A02", "A03"])
    assert client.post(f"{BASE}/sessions/{session_id}/finish", json={"revision": 0}).status_code == 409
    assert client.get(f"{BASE}/sessions/{session_id}").json()["revision"] == state["revision"]
    assert MxZOptimalControlXYRFSessionService(lambda: client.root, client.manager).active() is None
    state = steps(client, session_id, ["A04", "A05", "A06"])
    assert state["steps"]["A03"]["status"] == "stale"
    response = client.post(f"{BASE}/sessions/{session_id}/finish", json={"revision": state["revision"]})
    assert response.status_code == 200
    wait(lambda: client.service.get(session_id)["status"] == "closed")
    wait(lambda: not client.manager.hardware_lock.locked())
    assert client.service.get(session_id)["steps"]["A12"]["status"] == "completed"


@pytest.mark.parametrize("method", ["cancel", "eof", "shutdown"])
def test_child_cancel_eof_and_backend_shutdown(client, method):
    session_id = create(client)
    state = steps(client, session_id, ["A01", "A02", "A03"])
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
    config = yaml.safe_load((client.root / "data/Mx_Z_Optimal_Control_XY_RF_Sensitivity" / state["run_id"] / "experiment_config.yaml").read_text(encoding="utf-8"))
    assert config["completion_status"] in ("failed", "cancelled")
    assert config["main_field_restore"]["success"]


def test_history_snapshot_analysis_isolation_and_readonly_result(client, monkeypatch):
    session_id = create(client)
    state = steps(client, session_id, [f"A{i:02}" for i in range(1, 7)])
    run_id = state["run_id"]
    assert client.get(BASE + "/runs?limit=50&offset=0").json()["runs"][0]["run_id"] == run_id
    summary = client.get(f"{BASE}/runs/{run_id}/summary").json()
    assert summary["parameters"]["Y_RF_AMP_POINTS"] == 21
    assert summary["analysis_status"] == "not_started"
    assert client.get(BASE + "/runs/missing/summary").status_code == 404
    assert client.get(BASE + "/runs/..%2Fescape/summary").status_code == 404
    assert client.get(f"{BASE}/runs/{run_id}/result").json() == {"status": "not_started"}
    snapshot = client.get(f"{BASE}/runs/{run_id}/analysis").json()
    assert snapshot["options"] == {}
    config = client.root / "data/Mx_Z_Optimal_Control_XY_RF_Sensitivity" / run_id / "experiment_config.yaml"
    parameters = yaml.safe_load(config.read_text(encoding="utf-8"))["parameters"]
    assert client.post(f"{BASE}/runs/{run_id}/analysis", json={"revision": snapshot["revision"],
        "step_ids": ["B01"], "analysis_parameters": {"LOW_FREQ_SKIP_HZ": 8}}).status_code == 422
    entered, release = Event(), Event()
    execute = ADAPTER._execute
    def blocked(module, **kwargs):
        if module == ADAPTER.analysis_module:
            entered.set()
            assert release.wait(30)
        return execute(module, **kwargs)
    monkeypatch.setattr(ADAPTER, "_execute", blocked)
    body = dict(revision=snapshot["revision"], step_ids=["B01", "B02", "B03"], analysis_parameters={})
    try:
        response = client.post(f"{BASE}/runs/{run_id}/analysis", json=body)
        assert response.status_code == 200, response.text
        job_id = response.json()["id"]
        assert entered.wait(5)
        assert client.manager.hardware_lock.locked()
        assert client.post(f"{BASE}/runs/{run_id}/analysis", json=body).status_code == 409
    finally:
        release.set()
    wait(lambda: client.manager.get(job_id).status in ("completed", "failed"))
    assert client.manager.get(job_id).status == "completed", client.manager.get(job_id).error
    links = client.get(f"{BASE}/runs/{run_id}/summary").json()["artifacts"]
    point_report = "results/points/x_000_y_000/analysis.yaml"
    assert point_report in links
    assert client.get(links[point_report]).status_code == 200
    assert yaml.safe_load(config.read_text(encoding="utf-8"))["parameters"] == parameters
    result = client.get(f"{BASE}/runs/{run_id}/result")
    assert result.status_code == 200 and result.json()["success"] is True
    assert client.post(f"{BASE}/runs/{run_id}/analysis", json=body).status_code == 409
    old = config.parent / "results/analysis.yaml"
    before = old.read_bytes()
    for _ in range(2):
        assert client.get(f"{BASE}/runs/{run_id}/summary").status_code == 200
        assert client.get(f"{BASE}/runs/{run_id}/result").status_code == 200
    assert old.read_bytes() == before


def test_old_history_readable_but_unsupported_analysis_and_new_failure_wins(client):
    root = client.root / "data/Mx_Z_Optimal_Control_XY_RF_Sensitivity/old"
    (root / "results").mkdir(parents=True)
    config = dict(experiment_id=ADAPTER.id, data_type=ADAPTER.data_type, schema_version=4,
        completion_status="completed", parameters={"RUN_TAG": "old"})
    (root / "experiment_config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    report = root / "results/analysis.yaml"
    report.write_text("response_fit:\n  success: true\n  uncertainty: .nan\n", encoding="utf-8")
    before = report.read_bytes()
    assert client.get(BASE + "/runs/old/summary").status_code == 200
    response = client.get(BASE + "/runs/old/result")
    assert response.status_code == 200 and response.json()["response_fit"]["uncertainty"] is None
    assert client.get(BASE + "/runs/old/analysis").status_code == 409
    (root / "analysis_state.json").write_text(json.dumps({"summary": {"status": "quality_failed", "error": "新质量失败"}}), encoding="utf-8")
    assert client.get(BASE + "/runs/old/result").json()["status"] == "quality_failed"
    assert report.read_bytes() == before


def test_missing_results_wrong_ids_and_nested_links_are_read_only(client):
    root = client.root / "data/Mx_Z_Optimal_Control_XY_RF_Sensitivity/empty"
    root.mkdir(parents=True)
    config = dict(experiment_id=ADAPTER.id, data_type=ADAPTER.data_type, schema_version=2,
        measurement_mode="xy_bias_grid_rf_sensitivity", completion_status="completed", parameters={"RUN_TAG": "snapshot"})
    path = root / "experiment_config.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    before = path.read_bytes()
    response = client.get(BASE + "/runs/empty/summary")
    assert response.status_code == 200 and response.json()["artifacts"] == {}
    assert response.json()["parameters"] == {"RUN_TAG": "snapshot"}
    assert client.get(BASE + "/runs/empty/result").json() == {"status": "not_started"}
    assert client.get(BASE + "/runs/missing/summary").status_code == 404
    assert client.get(BASE + "/runs/empty/files/../experiment_config.yaml").status_code == 404
    assert path.read_bytes() == before
    config["experiment_id"] = "other"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    assert client.get(BASE + "/runs/empty/summary").status_code == 422


def test_finish_uses_executed_parameters_and_holds_lock_during_cleanup(client, monkeypatch):
    gate = client.root / "shutdown-release"
    monkeypatch.setenv("MX_Z_XY_RF_TEST_SHUTDOWN_GATE", str(gate))
    session_id = create(client)
    state = steps(client, session_id, [f"A{i:02}" for i in range(1, 6)])
    path = client.root / "data/Mx_Z_Optimal_Control_XY_RF_Sensitivity" / state["run_id"] / "experiment_config.yaml"
    original = yaml.safe_load(path.read_text(encoding="utf-8"))["parameters"]
    response = client.patch(f"{BASE}/sessions/{session_id}/parameters", json={"revision": state["revision"],
        "parameters": {"PUMP_CARRIER_FREQUENCY_HZ": 99e6, "RESPONSE_RATE_SA_S": 2000.}})
    assert response.status_code == 200, response.text
    wait(lambda: client.service.get(session_id)["pending_request"] is None)
    state = client.service.get(session_id)
    try:
        response = client.post(f"{BASE}/sessions/{session_id}/finish", json={"revision": state["revision"]})
        assert response.status_code == 200, response.text
        wait(lambda: gate.with_suffix(".started").is_file())
        assert client.manager.hardware_lock.locked()
        assert client.service.get(session_id)["status"] != "closed"
        assert client.post(f"{BASE}/sessions/{session_id}/finish", json={"revision": state["revision"]}).status_code == 409
    finally:
        gate.touch()
    wait(lambda: client.service.get(session_id)["status"] == "closed")
    wait(lambda: not client.manager.hardware_lock.locked())
    report = json.loads(gate.with_suffix(".finished").read_text(encoding="utf-8"))
    assert report["pump_frequency_hz"] == original["PUMP_CARRIER_FREQUENCY_HZ"]
    assert report["response_rate_sa_s"] == original["RESPONSE_RATE_SA_S"]
    assert report["errors"] == []
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["parameters"] == original
