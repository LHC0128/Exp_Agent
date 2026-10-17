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
from lab_workflows.experiment_modules.mx_y_optimal_control_rf_frequency_response import models
from lab_workflows.experiment_modules.mx_y_optimal_control_rf_frequency_response.definition import ADAPTER
from lab_workflows.experiment_modules.mx_y_optimal_control_rf_frequency_response.test_workflow import copy_config
from .jobs import JobManager
from .mx_y_optimal_control_rf_frequency_response import MxYOptimalControlFrequencySessionService, create_router

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
    diagram = tmp_path / "lab_workflows/experiment_modules/mx_y_optimal_control_rf_frequency_response/workflow.excalidraw"
    diagram.parent.mkdir(parents=True)
    diagram.write_bytes((Path(__file__).resolve().parents[2] / diagram.relative_to(tmp_path)).read_bytes())
    manager = JobManager()
    service = MxYOptimalControlFrequencySessionService(lambda: tmp_path, manager)
    execute = ADAPTER._execute
    def child(module, **kwargs):
        if module.endswith(".session"):
            module = "lab_workflows.experiment_modules.mx_y_optimal_control_rf_frequency_response.test_workflow"
        return execute(module, **kwargs)
    monkeypatch.setattr(ADAPTER, "_execute", child)
    monkeypatch.setattr(models, "find_project_root", lambda: tmp_path)
    monkeypatch.setenv("MX_Y_OPTIMAL_FREQUENCY_TEST_ROOT", str(tmp_path))
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
    params = replace(models.MxYOptimalControlRFFrequencyResponseParams(), corrected_control_source_run="frozen",
        frequency_points=3, frequency_start_hz=100., frequency_stop_hz=300., phase_scan_stop_deg=180., phase_scan_step_deg=90.,
        frequency_duration_s=.016, system_settle_s=0., temp_switch_on_lag_s=0., comparison_enabled=True,
        constant_control_calibration_source_run="coupling", constant_control_larmor_frequency_hz=1000.)
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
    queue = client.service.sessions[session_id]["queue"]
    queue.put({"request_id": "duplicate-probe", "revision": 0, "action": "steps", "step_ids": ["A01"]})
    wait(lambda: client.service.get(session_id)["revision"] == 1)
    queue.put({"request_id": "duplicate-probe", "revision": 1, "action": "steps", "step_ids": ["A01"]})
    wait(lambda: any("重复或无效" in event.get("message", "") for event in client.manager.get(client.service.get(session_id)["job_id"]).events))
    assert client.service.get(session_id)["revision"] == 1
    state = steps(client, session_id, ["A01", "A02", "A03"])
    assert client.post(f"{BASE}/sessions/{session_id}/finish", json={"revision": 0}).status_code == 409
    assert client.get(f"{BASE}/sessions/{session_id}").json()["revision"] == state["revision"]
    assert MxYOptimalControlFrequencySessionService(lambda: client.root, client.manager).active() is None
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
    assert client.service.get(session_id)["safety_shutdown"]["completed"]


def test_independent_analysis_real_child_mutual_exclusion_and_history_snapshot(client, monkeypatch):
    session_id = create(client)
    state = steps(client, session_id, ["A01", "A02", "A03", "A04", "A05", "A06"])
    run_id = state["run_id"]
    assert client.get(BASE + "/runs").json()["runs"][0]["run_id"] == run_id
    summary = client.get(f"{BASE}/runs/{run_id}/summary").json()
    assert summary["parameters"]["FREQUENCY_POINTS"] == 3
    assert client.get(BASE + "/runs/missing/summary").status_code == 404
    original = (client.root / "data" / ADAPTER.data_type / run_id / "raw/optimal_control_phase_frequency_scan.npz").read_bytes()
    revision = client.get(f"{BASE}/runs/{run_id}/analysis").json()["revision"]
    entered, release = Event(), Event()
    execute = ADAPTER._execute
    def blocked(module, **kwargs):
        if module == ADAPTER.analysis_module:
            entered.set()
            assert release.wait(30)
        return execute(module, **kwargs)
    monkeypatch.setattr(ADAPTER, "_execute", blocked)
    body = {"revision": revision, "step_ids": ["B01", "B02", "B03"], "analysis_parameters": {}}
    try:
        response = client.post(f"{BASE}/runs/{run_id}/analysis", json=body)
        assert response.status_code == 200, response.text
        job = client.manager.get(response.json()["id"])
        assert entered.wait(5)
        assert client.manager.hardware_lock.locked()
        duplicate = client.post(f"{BASE}/runs/{run_id}/analysis", json=body)
        assert duplicate.status_code == 409 and duplicate.headers["X-Error-Code"] == "analysis_running"
    finally:
        release.set()
    wait(lambda: job.status in ("completed", "failed"))
    assert job.status == "completed", job.error
    assert client.get(f"{BASE}/runs/{run_id}/analysis").json()["options"] == {}
    assert client.post(f"{BASE}/runs/{run_id}/analysis", json=body).status_code == 409
    assert (client.root / "data" / ADAPTER.data_type / run_id / "raw/optimal_control_phase_frequency_scan.npz").read_bytes() == original
    finish = client.service.get(session_id)
    client.post(f"{BASE}/sessions/{session_id}/finish", json={"revision": finish["revision"]})
    wait(lambda: client.service.get(session_id)["status"] == "closed")


def test_child_holds_lock_until_shutdown_uses_executed_configuration(client, monkeypatch):
    gate = client.root / "shutdown-release"
    monkeypatch.setenv("MX_Y_OPTIMAL_FREQUENCY_SHUTDOWN_GATE", str(gate))
    session_id = create(client)
    state = steps(client, session_id, ["A01", "A02", "A03", "A04"])
    config_path = client.root / "data" / ADAPTER.data_type / state["run_id"] / "experiment_config.yaml"
    original = yaml.safe_load(config_path.read_text(encoding="utf-8"))["parameters"]
    response = client.patch(f"{BASE}/sessions/{session_id}/parameters", json={"revision": state["revision"],
        "parameters": {"PUMP_CARRIER_FREQUENCY_HZ": 99000000., "FREQUENCY_POINTS": 4}})
    assert response.status_code == 200
    wait(lambda: client.service.get(session_id)["pending_request"] is None)
    state = client.service.get(session_id)
    assert state["steps"]["A03"]["status"] == "stale"
    assert yaml.safe_load(config_path.read_text(encoding="utf-8"))["parameters"] == original
    probe_started = Event()
    try:
        response = client.post(f"{BASE}/sessions/{session_id}/finish", json={"revision": state["revision"]})
        assert response.status_code == 200
        wait(lambda: gate.with_suffix(".started").is_file())
        assert client.manager.hardware_lock.locked()
        probe = client.manager.create("hardware-probe", lambda job: probe_started.set())
        assert probe.status == "queued" and not probe_started.is_set()
        assert client.post(f"{BASE}/sessions/{session_id}/finish", json={"revision": state["revision"]}).status_code == 409
    finally:
        gate.touch()
    wait(lambda: client.service.get(session_id)["status"] == "closed")
    wait(lambda: probe.status == "completed")
    assert json.loads(gate.with_suffix(".finished").read_text(encoding="utf-8")) == {
        "pump_frequency_hz": original["PUMP_CARRIER_FREQUENCY_HZ"], "completed": True}
    assert yaml.safe_load(config_path.read_text(encoding="utf-8"))["parameters"] == original


@pytest.mark.parametrize("damage", ["wrong_identity", "unknown_raw", "legacy_schema1", "bad_yaml"])
def test_real_main_history_format_qualification_and_unanalyzed_visibility(client, monkeypatch, damage):
    from . import main
    from lab_workflows.experiment_modules.mx_y_optimal_control_rf_frequency_response.test_analysis import build_run
    monkeypatch.setattr(main, "ROOT", client.root)
    monkeypatch.setattr(main, "manager", client.manager)
    monkeypatch.setattr(main, "_workflow_session_services", lambda: (client.service,))
    directory = build_run(client.root)
    path = directory / "experiment_config.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    if damage == "bad_yaml":
        path.write_text("parameters: [", encoding="utf-8")
    else:
        if damage == "wrong_identity":
            config["experiment_id"] = "other"
        elif damage == "unknown_raw":
            config["raw_format_version"] = True
        else:
            config.pop("raw_format_version")
            config["schema_version"] = 1
        path.write_text(yaml.safe_dump(config), encoding="utf-8")
    with TestClient(main.app) as http:
        assert http.get(BASE + "/runs").json()["runs"] == []
        assert http.get(f"{BASE}/runs/{directory.name}/summary").status_code == 422
        assert http.get(f"{BASE}/runs/{directory.name}/analysis").status_code == 422
        assert http.post(f"/api/runs/{directory.name}/analyze", json={"experiment_id": ADAPTER.id}).status_code == 422
    assert not client.manager.jobs


def test_real_main_lists_unanalyzed_and_verified_history_but_blocks_running_analysis(client, monkeypatch):
    from . import main
    from lab_workflows.experiment_modules.mx_y_optimal_control_rf_frequency_response.test_analysis import build_run
    monkeypatch.setattr(main, "ROOT", client.root)
    monkeypatch.setattr(main, "manager", client.manager)
    monkeypatch.setattr(main, "_workflow_session_services", lambda: (client.service,))
    directory = build_run(client.root, schema=2, raw_version=None)
    path = directory / "experiment_config.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    with TestClient(main.app) as http:
        assert http.get(BASE + "/runs").json()["runs"][0]["run_id"] == directory.name
        summary = http.get(f"{BASE}/runs/{directory.name}/summary")
        assert summary.status_code == 200, summary.text
        assert summary.json()["parameters"]["RUN_TAG"] == config["parameters"]["RUN_TAG"]
        assert summary.json()["artifacts"] == {}
        config["completion_status"] = "running"
        path.write_text(yaml.safe_dump(config), encoding="utf-8")
        assert http.get(f"{BASE}/runs/{directory.name}/analysis").status_code == 409
        assert http.post(f"/api/runs/{directory.name}/analyze", json={"experiment_id": ADAPTER.id}).status_code == 409
    assert not client.manager.jobs
