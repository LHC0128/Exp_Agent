"""Mx Y RF 真实子进程会话、全局互斥、历史与独立分析接口。"""
from dataclasses import replace
from pathlib import Path
from threading import Event
import json
import time
import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from lab_workflows.experiment_modules.mx_y_rf_sensitivity.definition import ADAPTER
from lab_workflows.experiment_modules.mx_y_rf_sensitivity.models import MxYRFParams
from lab_workflows.experiment_modules.mx_y_rf_sensitivity.test_workflow import copy_config
from .jobs import JobManager
from .mx_y_rf_sensitivity import MxYRFSessionService, create_router

BASE = "/api/experiments/mx-y-rf-sensitivity"


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
    diagram = tmp_path / "lab_workflows/experiment_modules/mx_y_rf_sensitivity/workflow.excalidraw"
    diagram.parent.mkdir(parents=True)
    diagram.write_bytes((Path(__file__).resolve().parents[2] /
        "lab_workflows/experiment_modules/mx_y_rf_sensitivity/workflow.excalidraw").read_bytes())
    manager = JobManager()
    service = MxYRFSessionService(lambda: tmp_path, manager)
    original = ADAPTER._execute

    def execute(module, **kwargs):
        if module.endswith(".session"):
            module = "lab_workflows.experiment_modules.mx_y_rf_sensitivity.test_workflow"
        return original(module, **kwargs)

    monkeypatch.setattr(ADAPTER, "_execute", execute)
    monkeypatch.setenv("MX_Y_RF_TEST_ROOT", str(tmp_path))
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
    params = replace(MxYRFParams(), y_rf_amp_points=21, noise_n_avg=2, noise_rate_sa_s=1000,
        noise_duration_s=0.256, response_duration_s=0.016, system_settle_s=0, temp_switch_on_lag_s=0,
        y_rf_nt_per_vpp=10)
    response = client.post(BASE + "/sessions", json={"parameters": params.to_external()})
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
    value = client.service.get(session_id)
    assert value["error"] is None, value
    return value


def test_real_child_dependency_revision_lock_refresh_and_finish(client):
    flow = client.get(BASE + "/workflow").json()
    assert len(flow["acquisition"]) == 12 and len(flow["analysis"]) == 3
    from lab_workflows.experiment_modules.mx_y_rf_sensitivity import stages
    declared = stages.definition()
    for column in ("acquisition", "analysis"):
        assert [item["quality_gates"] for item in flow[column]] == [list(item["quality_gates"]) for item in declared[column]]
    assert {item["id"] for item in flow["acquisition"] + flow["analysis"] if item["quality_gates"]} == {
        "A01", "A03", "A08", "A09", "B01"}
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
    assert MxYRFSessionService(lambda: client.root, client.manager).active() is None
    state = steps(client, session_id, ["A08", "A09", "A10", "A11"])
    assert state["demod_mode"] == "response"
    response = client.post(f"{BASE}/sessions/{session_id}/finish", json={"revision": state["revision"]})
    assert response.status_code == 200
    wait(lambda: client.service.get(session_id)["status"] == "closed")
    wait(lambda: not client.manager.hardware_lock.locked())
    state = client.service.get(session_id)
    assert state["safety_shutdown"]["completed"]
    assert state["steps"]["A12"]["status"] == "completed"


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
    config = yaml.safe_load((client.root / "data/Mx_Y_RF_Sensitivity" / state["run_id"] /
        "experiment_config.yaml").read_text(encoding="utf-8"))
    assert config["completion_status"] in ("cancelled", "failed")


def test_history_snapshots_missing_results_and_analysis_isolation(client, monkeypatch):
    session_id = create(client)
    state = steps(client, session_id, [f"A{index:02d}" for index in range(1, 12)])
    run_id = state["run_id"]
    history = client.get(BASE + "/runs?limit=50&offset=0").json()
    assert history["runs"][0]["run_id"] == run_id
    summary = client.get(f"{BASE}/runs/{run_id}/summary").json()
    assert summary["parameters"]["Y_RF_AMP_POINTS"] == 21
    assert summary["analysis_status"] == "not_started"
    assert client.get(BASE + "/runs/not-a-run/summary").status_code == 404
    assert client.get(BASE + "/runs/..%2Fescape/summary").status_code == 404
    analysis_state = client.get(f"{BASE}/runs/{run_id}/analysis")
    assert analysis_state.status_code == 200, analysis_state.text
    revision = analysis_state.json()["revision"]
    entered, release = Event(), Event()
    execute = ADAPTER._execute
    def blocked_analysis(module, **kwargs):
        if module == ADAPTER.analysis_module:
            entered.set()
            assert release.wait(30), "测试未释放真实分析子进程"
        return execute(module, **kwargs)
    monkeypatch.setattr(ADAPTER, "_execute", blocked_analysis)
    body = {"revision": revision, "step_ids": ["B01", "B02", "B03"], "analysis_parameters": {"LOW_FREQ_SKIP_HZ": 7}}
    config_path = client.root / "data/Mx_Y_RF_Sensitivity" / run_id / "experiment_config.yaml"
    snapshot = yaml.safe_load(config_path.read_text(encoding="utf-8"))["parameters"]
    raw_path = config_path.parent / "raw/amplitude_scan.npz"
    original_raw = raw_path.read_bytes()
    try:
        job = client.post(f"{BASE}/runs/{run_id}/analysis", json=body)
        assert job.status_code == 200, job.text
        job_id = job.json()["id"]
        assert entered.wait(5)
        assert client.manager.hardware_lock.locked(), "独立分析可以在硬件会话等待时运行"
        duplicate = client.post(f"{BASE}/runs/{run_id}/analysis", json=body)
        assert duplicate.status_code == 409 and duplicate.headers["X-Error-Code"] == "analysis_running"
    finally:
        release.set()
    wait(lambda: client.manager.get(job_id).status in ("completed", "failed"))
    assert client.manager.get(job_id).status == "completed", client.manager.get(job_id).error
    refreshed = client.get(f"{BASE}/runs/{run_id}/summary").json()
    assert refreshed["analysis_status"] == "completed"
    assert refreshed["parameters"]["LOW_FREQ_SKIP_HZ"] == 3
    assert any(name.endswith("amplitude_response.png") for name in refreshed["artifacts"])
    assert client.get(f"{BASE}/runs/{run_id}/analysis").json()["options"]["LOW_FREQ_SKIP_HZ"] == 7
    assert yaml.safe_load(config_path.read_text(encoding="utf-8"))["parameters"] == snapshot
    assert raw_path.read_bytes() == original_raw
    stale = client.post(f"{BASE}/runs/{run_id}/analysis", json=body)
    assert stale.status_code == 409 and stale.headers["X-Error-Code"] == "revision_conflict"
    comparison_release = Event()
    comparison = client.manager.create("comparison:mx-y-rf-sensitivity", lambda job: comparison_release.wait(30), hardware_required=False)
    try:
        response = client.post(f"{BASE}/runs/{run_id}/analysis", json=body)
        assert response.status_code == 409 and response.headers["X-Error-Code"] == "comparison_running"
    finally:
        comparison_release.set()
    wait(lambda: comparison.status == "completed")


def test_real_child_finish_uses_executed_parameters_and_holds_lock_through_cleanup(client, monkeypatch):
    gate = client.root / "shutdown-release"
    monkeypatch.setenv("MX_Y_RF_TEST_SHUTDOWN_GATE", str(gate))
    session_id = create(client)
    state = steps(client, session_id, [f"A{index:02d}" for index in range(1, 11)])
    config_path = client.root / "data/Mx_Y_RF_Sensitivity" / state["run_id"] / "experiment_config.yaml"
    parameters = yaml.safe_load(config_path.read_text(encoding="utf-8"))["parameters"]
    response = client.patch(f"{BASE}/sessions/{session_id}/parameters", json={"revision": state["revision"],
        "parameters": {"PUMP_CARRIER_FREQUENCY_HZ": 99e6, "PUMP_CARRIER_AMPLITUDE_VPP": 0.15,
            "Y_RF_FREQUENCY_HZ": 11000, "RESPONSE_RATE_SA_S": 2000}})
    assert response.status_code == 200, response.text
    wait(lambda: client.service.get(session_id)["pending_request"] is None)
    state = client.service.get(session_id)
    assert state["parameters"]["Y_RF_FREQUENCY_HZ"] == 11000
    assert yaml.safe_load(config_path.read_text(encoding="utf-8"))["parameters"] == parameters
    probe_started = Event()
    try:
        response = client.post(f"{BASE}/sessions/{session_id}/finish", json={"revision": state["revision"]})
        assert response.status_code == 200, response.text
        wait(lambda: gate.with_suffix(".started").is_file())
        assert client.manager.hardware_lock.locked()
        assert client.service.get(session_id)["status"] != "closed"
        probe = client.manager.create("hardware-probe", lambda job: probe_started.set())
        assert not probe_started.is_set() and probe.status == "queued"
        duplicate = client.post(f"{BASE}/sessions/{session_id}/finish", json={"revision": state["revision"]})
        assert duplicate.status_code == 409
    finally:
        gate.touch()
    wait(lambda: client.service.get(session_id)["status"] == "closed")
    wait(lambda: probe.status == "completed")
    assert probe_started.is_set()
    saved = json.loads(gate.with_suffix(".finished").read_text(encoding="utf-8"))
    assert saved == {"pump_frequency_hz": parameters["PUMP_CARRIER_FREQUENCY_HZ"],
        "response_rate_sa_s": parameters["RESPONSE_RATE_SA_S"], "completed": True}
    assert yaml.safe_load(config_path.read_text(encoding="utf-8"))["parameters"] == parameters
