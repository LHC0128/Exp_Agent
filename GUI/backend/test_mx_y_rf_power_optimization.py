"""光功率网格真实假仪器子进程、全局队列、只读历史与离线分析 API。"""
from pathlib import Path
from threading import Event
import json
import time
import sys
import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from lab_workflows.experiment_modules.mx_y_rf_power_optimization.definition import ADAPTER
from lab_workflows.experiment_modules.mx_y_rf_power_optimization.test_workflow import copy_config, parameters
from .jobs import JobManager
from .mx_y_rf_power_optimization import MxYRFPowerOptimizationSessionService, create_router

BASE = "/api/experiments/mx-y-rf-power-optimization"


def wait(predicate):
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.025)
    raise AssertionError("光功率假仪器子进程超时")


@pytest.fixture
def client(tmp_path, monkeypatch):
    copy_config(tmp_path)
    diagram = tmp_path / "lab_workflows/experiment_modules/mx_y_rf_power_optimization/workflow.excalidraw"
    diagram.parent.mkdir(parents=True)
    diagram.write_bytes((Path(__file__).resolve().parents[2] /
        "lab_workflows/experiment_modules/mx_y_rf_power_optimization/workflow.excalidraw").read_bytes())
    monkeypatch.setattr(ADAPTER, "root", tmp_path)
    defaults = tmp_path / "params/experiments/mx-y-rf-power-optimization.yaml"
    defaults.parent.mkdir()
    parameters().save_yaml(defaults)
    monkeypatch.setattr(ADAPTER, "defaults_path", defaults)
    from lab_workflows.experiments import typed
    popen = typed.subprocess.Popen
    def launch(command, **kwargs):
        # 取消文件和数据仍在临时根目录；解释器与模块来自当前检出。
        return popen([sys.executable, *command[1:]], **(kwargs | {"cwd": Path(__file__).resolve().parents[2]}))
    monkeypatch.setattr(typed.subprocess, "Popen", launch)
    manager = JobManager()
    service = MxYRFPowerOptimizationSessionService(lambda: tmp_path, manager)
    original = ADAPTER._execute
    def execute(module, **kwargs):
        if module in (ADAPTER.acquisition_module, service.session_module):
            module = "lab_workflows.experiment_modules.mx_y_rf_power_optimization.test_workflow"
        return original(module, **kwargs)
    monkeypatch.setattr(ADAPTER, "_execute", execute)
    monkeypatch.setenv("MX_Y_RF_POWER_TEST_ROOT", str(tmp_path))
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
    response = client.post(BASE + "/sessions", json={"parameters": parameters().to_external()})
    assert response.status_code == 200, response.text
    session_id = response.json()["session_id"]
    wait(lambda: client.service.get(session_id)["status"] in ("waiting", "closed"))
    assert client.service.get(session_id)["status"] == "waiting", "\\n".join(
        event["message"] for event in client.manager.get(response.json()["job_id"]).events)
    return session_id


def steps(client, session_id, ids):
    revision = client.service.get(session_id)["revision"]
    response = client.post(f"{BASE}/sessions/{session_id}/steps", json={"revision": revision, "step_ids": ids})
    assert response.status_code == 200, response.text
    wait(lambda: client.service.get(session_id)["pending_request"] is None)
    state = client.service.get(session_id)
    assert state["error"] is None, state
    return state


def test_dependency_revision_global_lock_refresh_and_finish(client):
    flow = client.get(BASE + "/workflow").json()
    assert len(flow["acquisition"]) == 10 and len(flow["analysis"]) == 3
    assert flow["acquisition"][-1]["dependencies"] == []
    assert client.get(BASE + "/workflow/diagram").json()["type"] == "excalidraw"
    session_id = create(client)
    assert client.manager.hardware_lock.locked()
    assert client.get(BASE + "/sessions/active").json()["session_id"] == session_id
    assert client.post(BASE + "/sessions", json={"parameters": {}}).status_code == 409
    assert client.post(f"{BASE}/sessions/{session_id}/steps",
        json={"revision": 0, "step_ids": ["A08"]}).status_code == 422
    state = steps(client, session_id, [f"A{i:02d}" for i in range(1, 8)])
    assert client.post(f"{BASE}/sessions/{session_id}/finish", json={"revision": 0}).status_code == 409
    assert client.get(f"{BASE}/sessions/{session_id}").json()["revision"] == state["revision"]
    assert MxYRFPowerOptimizationSessionService(lambda: client.root, client.manager).active() is None
    release, entered = Event(), Event()
    probe = client.manager.create("hardware-probe", lambda job: (entered.set(), release.wait(20)))
    assert probe.status == "queued" and not entered.is_set()
    response = client.post(f"{BASE}/sessions/{session_id}/finish", json={"revision": state["revision"]})
    assert response.status_code == 200
    try:
        wait(lambda: client.service.get(session_id)["status"] == "closed")
        wait(entered.is_set)
        assert client.service.get(session_id)["safety_shutdown"]["completed"]
    finally:
        release.set()
    wait(lambda: probe.status == "completed")


@pytest.mark.parametrize("method", ["cancel", "eof", "shutdown"])
def test_cancel_eof_and_backend_shutdown_restore_before_unlock(client, method):
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
    state = client.service.get(session_id)
    assert state["safety_shutdown"]["completed"] and state["outputs"]["Y RF"] == "OFF"
    config = yaml.safe_load((client.root / "data/Mx_Y_RF_Power_Optimization" / state["run_id"] /
        "experiment_config.yaml").read_text(encoding="utf-8"))
    assert config["completion_status"] in ("cancelled", "failed")
    assert config["laser_power_restore"]["success"]


def test_real_history_analysis_read_only_options_and_mutual_exclusion(client, monkeypatch):
    session_id = create(client)
    state = steps(client, session_id, [f"A{i:02d}" for i in range(1, 9)])
    run_id = state["run_id"]
    assert client.get(f"{BASE}/runs/{run_id}/analysis").status_code == 409
    steps(client, session_id, ["A09"])
    history = client.get(BASE + "/runs?limit=50&offset=0").json()
    assert history["runs"][0]["run_id"] == run_id
    config = client.root / "data/Mx_Y_RF_Power_Optimization" / run_id / "experiment_config.yaml"
    original = config.read_bytes()
    state = client.get(f"{BASE}/runs/{run_id}/analysis").json()
    assert config.read_bytes() == original and not (config.parent / "analysis_state.json").exists()
    summary = client.get(f"{BASE}/runs/{run_id}/summary").json()
    assert summary["parameters"]["PUMP_POWER_POINTS"] == 2 and summary["artifacts"] == {}
    for path in ("not-a-run/summary", "..%2Fescape/summary", f"{run_id}/files/..%2Fexperiment_config.yaml"):
        assert client.get(f"{BASE}/runs/{path}").status_code == 404
    entered, release = Event(), Event()
    execute = ADAPTER._execute
    def block(module, **kwargs):
        if module == ADAPTER.analysis_module:
            entered.set()
            assert release.wait(30)
        return execute(module, **kwargs)
    monkeypatch.setattr(ADAPTER, "_execute", block)
    body = {"revision": state["revision"], "step_ids": ["B01", "B02", "B03"], "analysis_parameters": {"LOW_FREQ_SKIP_HZ": 7}}
    try:
        response = client.post(f"{BASE}/runs/{run_id}/analysis", json=body)
        assert response.status_code == 200, response.text
        job_id = response.json()["id"]
        assert entered.wait(5)
        assert client.manager.hardware_lock.locked(), "独立分析不占用或释放采集会话的硬件锁"
        assert client.post(f"{BASE}/runs/{run_id}/analysis", json=body).status_code == 409
    finally:
        release.set()
    wait(lambda: client.manager.get(job_id).status in ("completed", "failed"))
    assert client.manager.get(job_id).status == "completed", client.manager.get(job_id).error
    result = client.get(f"{BASE}/runs/{run_id}/summary").json()
    assert result["analysis_status"] == "completed" and config.read_bytes() == original
    assert any(name.endswith("zero_point_sensitivity_heatmap.png") for name in result["artifacts"])
    assert client.get(f"{BASE}/runs/{run_id}/analysis").json()["options"]["LOW_FREQ_SKIP_HZ"] == 7
    assert client.post(f"{BASE}/runs/{run_id}/analysis", json=body).status_code == 409


def test_cleanup_holds_hardware_lock_and_uses_executed_not_applied_draft(client, monkeypatch):
    gate = client.root / "cleanup-release"
    monkeypatch.setenv("MX_Y_RF_POWER_SHUTDOWN_GATE", str(gate))
    session_id = create(client)
    state = steps(client, session_id, [f"A{i:02d}" for i in range(1, 9)])
    config = client.root / "data/Mx_Y_RF_Power_Optimization" / state["run_id"] / "experiment_config.yaml"
    frozen = config.read_bytes()
    response = client.patch(f"{BASE}/sessions/{session_id}/parameters", json={"revision": state["revision"],
        "parameters": {"PUMP_CARRIER_FREQUENCY_HZ": 99e6, "FIXED_PARAMS.Pump_laser_power": 0.4}})
    assert response.status_code == 200
    wait(lambda: client.service.get(session_id)["pending_request"] is None)
    state = client.service.get(session_id)
    assert config.read_bytes() == frozen
    response = client.post(f"{BASE}/sessions/{session_id}/finish", json={"revision": state["revision"]})
    assert response.status_code == 200
    probe = None
    try:
        wait(lambda: gate.with_suffix(".started").exists())
        assert client.manager.hardware_lock.locked()
        probe = client.manager.create("cleanup-probe", lambda job: "next")
        assert probe.status == "queued"
        assert client.post(f"{BASE}/sessions/{session_id}/finish", json={"revision": state["revision"]}).status_code == 409
    finally:
        gate.touch()
    wait(lambda: client.service.get(session_id)["status"] == "closed")
    wait(lambda: probe.status == "completed")
    report = json.loads(gate.with_suffix(".finished").read_text(encoding="utf-8"))
    assert report["laser_power_restore"]["pump_power_v"] == parameters().pump_laser_power_v
    assert report["safety_shutdown"]["completed"]


def test_history_ignores_unknown_formats_and_preserves_valid_legacy_snapshots(client):
    base = client.root / "data/Mx_Y_RF_Power_Optimization"
    for name, extra in (("legacy", {}), ("wrong", {"experiment_id": "other"}), ("future", {"raw_format_version": 99})):
        path = base / name
        path.mkdir(parents=True)
        config = {"experiment_id": ADAPTER.id, "data_type": ADAPTER.data_type,
            "parameters": parameters().to_external(), "completion_status": "completed"} | extra
        (path / "experiment_config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    assert [r["run_id"] for r in client.get(BASE + "/runs").json()["runs"]] == ["legacy"]
    assert client.get(BASE + "/runs/future/summary").status_code == 422
    assert client.get(BASE + "/runs/legacy/summary").json()["artifacts"] == {}


def test_main_routes_complete_run_auto_analysis_and_reanalysis_use_same_temp_directory(client, monkeypatch):
    from . import main
    monkeypatch.setattr(main, "ROOT", client.root)
    monkeypatch.setattr(main, "manager", client.manager)
    monkeypatch.setattr(main, "mx_y_rf_power_sessions", client.service)
    http = TestClient(main.app)
    response = http.post(BASE + "/runs", json={"parameters": parameters().to_external()})
    assert response.status_code == 200, response.text
    job_id = response.json()["id"]
    wait(lambda: client.manager.get(job_id).status in ("completed", "failed"))
    job = client.manager.get(job_id)
    assert job.status == "completed", job.error
    runs = http.get(BASE + "/runs").json()["runs"]
    run_id = runs[0]["run_id"]
    assert http.get(f"{BASE}/runs/{run_id}/summary").json()["analysis_status"] == "completed"
    result = client.root / "data/Mx_Y_RF_Power_Optimization" / run_id / "results/optimization.npz"
    assert result.is_file()
    response = http.post(f"/api/runs/{run_id}/analyze", json={"experiment_id": ADAPTER.id})
    assert response.status_code == 200, response.text
    job_id = response.json()["id"]
    wait(lambda: client.manager.get(job_id).status in ("completed", "failed"))
    assert client.manager.get(job_id).status == "completed", client.manager.get(job_id).error
    assert client.service in main._workflow_session_services()
