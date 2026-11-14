"""真实子进程配假仪器验证本项会话、全局锁、历史和离线产物。"""
from pathlib import Path
from threading import Event
import json
import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from lab_workflows.experiment_modules.mx_z_optimal_control_xy_rf_phase_response.definition import ADAPTER
from lab_workflows.experiment_modules.mx_z_optimal_control_xy_rf_phase_response import models
from lab_workflows.experiment_modules.mx_z_optimal_control_xy_rf_phase_response.stages import ANALYSIS_KEYS
from lab_workflows.experiment_modules.mx_z_optimal_control_xy_rf_phase_response.test_workflow import copy_config, parameters
from lab_workflows.experiment_modules.mx_z_optimal_control_xy_rf_phase_response.test_analysis import synthetic_run
from .jobs import JobManager
from .mx_z_optimal_control_xy_rf_phase_response import MxZOptimalControlXYRFPhaseSessionService, create_router
from .test_mx_z_optimal_control_xy_noise_spectrum import wait

BASE = "/api/experiments/" + ADAPTER.id


@pytest.fixture
def client(tmp_path, monkeypatch):
    copy_config(tmp_path)
    diagram = tmp_path / "lab_workflows/experiment_modules/mx_z_optimal_control_xy_rf_phase_response/workflow.excalidraw"
    diagram.parent.mkdir(parents=True)
    diagram.write_bytes((Path(__file__).resolve().parents[2] / diagram.relative_to(tmp_path)).read_bytes())
    manager = JobManager()
    service = MxZOptimalControlXYRFPhaseSessionService(lambda: tmp_path, manager)
    execute = ADAPTER._execute
    def child(module, **kwargs):
        if module.endswith(".session"):
            module = "lab_workflows.experiment_modules.mx_z_optimal_control_xy_rf_phase_response.test_workflow"
        if module == ADAPTER.analysis_module:
            assert Path(kwargs["run_dir"]).is_relative_to(tmp_path)
        return execute(module, **kwargs)
    monkeypatch.setattr(ADAPTER, "_execute", child)
    monkeypatch.setattr(models, "find_project_root", lambda: tmp_path)
    monkeypatch.setenv("MX_Z_XY_PHASE_TEST_ROOT", str(tmp_path))
    app = FastAPI()
    app.include_router(create_router(service))
    with TestClient(app) as http:
        http.service, http.manager, http.root = service, manager, tmp_path
        yield http
    service.close_communication()
    for job in list(manager.jobs.values()):
        if job.status in ("queued", "running"):
            job.cancellation.cancel()
    wait(lambda: all(job.status not in ("queued", "running") for job in manager.jobs.values()))


def create(client, **overrides):
    response = client.post(BASE + "/sessions", json={"parameters": parameters(client.root).to_external() | overrides})
    assert response.status_code == 200, response.text
    session_id = response.json()["session_id"]
    wait(lambda: client.service.get(session_id)["status"] in ("waiting", "closed"))
    assert client.service.get(session_id)["status"] == "waiting", "\n".join(e.get("message", "") for e in client.manager.get(response.json()["job_id"]).events)
    return session_id


def steps(client, session_id, selected):
    response = client.post(f"{BASE}/sessions/{session_id}/steps", json={
        "revision": client.service.get(session_id)["revision"], "step_ids": selected})
    assert response.status_code == 200, response.text
    wait(lambda: client.service.get(session_id)["pending_request"] is None)
    value = client.service.get(session_id)
    assert not value.get("error"), value
    return value


def test_child_dependencies_global_lock_revisions_refresh_and_finish(client):
    manifest = client.get(BASE + "/workflow").json()
    assert len(manifest["acquisition"]) == 6 and len(manifest["analysis"]) == 3
    assert client.get(BASE + "/workflow/diagram").json()["type"] == "excalidraw"
    session_id = create(client)
    assert client.manager.hardware_lock.locked()
    assert client.get(BASE + "/sessions/active").json()["session_id"] == session_id
    assert client.post(BASE + "/sessions", json={"parameters": parameters(client.root).to_external()}).status_code == 409
    assert client.post(f"{BASE}/sessions/{session_id}/steps", json={"revision": 0, "step_ids": ["A04"]}).status_code == 422
    state = steps(client, session_id, [f"A{i:02}" for i in range(1, 6)])
    assert state["steps"]["A03"]["status"] == "stale"
    assert client.post(f"{BASE}/sessions/{session_id}/finish", json={"revision": 0}).status_code == 409
    assert client.get(f"{BASE}/sessions/{session_id}").json()["revision"] == state["revision"]
    assert MxZOptimalControlXYRFPhaseSessionService(lambda: client.root, client.manager).active() is None
    assert client.post(f"{BASE}/sessions/{session_id}/finish", json={"revision": state["revision"]}).status_code == 200
    wait(lambda: client.service.get(session_id)["status"] == "closed")
    wait(lambda: not client.manager.hardware_lock.locked())
    assert client.service.get(session_id)["steps"]["A12"]["status"] == "completed"
    directory = client.root / "data" / ADAPTER.data_type / state["run_id"]
    assert (directory / "raw/xy_rf_phase_response_scan.npz").is_file()


@pytest.mark.parametrize("method", ["cancel", "eof", "shutdown"])
def test_child_cancel_eof_shutdown_zeros_xy_and_preserves_trigger(client, method):
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
    assert final["outputs"]["X"] == final["outputs"]["Y RF"] == "OFF"
    assert final["outputs"]["共同触发"] == "保持当前状态"
    assert final["outputs"]["Z"] == "保持当前最优控制"
    config = yaml.safe_load((client.root / "data" / ADAPTER.data_type / state["run_id"] / "experiment_config.yaml").read_text(encoding="utf-8"))
    assert config["completion_status"] in ("failed", "cancelled") and config["main_field_restore"]["success"]
    assert config["final_output_policy"]["trigger"] == "preserve current state on every exit path"


def test_child_quality_failure_blocks_following_stage(client):
    session_id = create(client, R_BAD_POINT_STD_THRESHOLD_V=0.)
    state = steps(client, session_id, ["A01", "A02", "A03", "A04", "A05"])
    assert state["steps"]["A04"]["status"] == "quality_failed"
    assert state["steps"]["A05"]["status"] == "pending"
    response = client.post(f"{BASE}/sessions/{session_id}/steps", json={"revision": state["revision"], "step_ids": ["A05"]})
    assert response.status_code == 422
    directory = client.root / "data" / ADAPTER.data_type / state["run_id"]
    assert len(list((directory / "raw").glob("*attempt*.npz"))) == 3


def test_history_analysis_options_results_download_and_snapshot_isolation(client):
    directory, _ = synthetic_run(client.root / "data" / ADAPTER.data_type)
    run_id = directory.name
    assert client.get(BASE + "/runs?limit=50&offset=0").json()["runs"][0]["run_id"] == run_id
    summary = client.get(f"{BASE}/runs/{run_id}/summary").json()
    assert summary["parameters"]["RUN_TAG"] == "historical" and summary["analysis_status"] == "not_started"
    assert client.get(f"{BASE}/runs/{run_id}/result").json() == {"status": "not_started"}
    snapshot = client.get(f"{BASE}/runs/{run_id}/analysis").json()
    assert set(snapshot["options"]) == set(ANALYSIS_KEYS)
    assert client.post(f"{BASE}/runs/{run_id}/analysis", json={"revision": snapshot["revision"],
        "step_ids": ["B01"], "analysis_parameters": {"RUN_TAG": "wrong"}}).status_code == 422
    before = yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf-8"))["parameters"]
    body = dict(revision=snapshot["revision"], step_ids=["B01", "B02", "B03"], analysis_parameters={ANALYSIS_KEYS[0]: .2})
    response = client.post(f"{BASE}/runs/{run_id}/analysis", json=body)
    assert response.status_code == 200, response.text
    job = client.manager.get(response.json()["id"])
    wait(lambda: job.status in ("completed", "failed"))
    assert job.status == "completed", "\n".join(str(e.get("message")) for e in job.events)
    assert client.post(f"{BASE}/runs/{run_id}/analysis", json=body).status_code == 409
    result = client.get(f"{BASE}/runs/{run_id}/result").json()
    assert result["nonlinear_fit_settings"]["r_squared_min"] == .2
    assert result["nonlinear_fit_accepted_count"] == 1 and "best_xy" not in result
    links = client.get(f"{BASE}/runs/{run_id}/summary").json()["artifacts"]
    assert "results/xy_rf_phase_response_nonlinear_maps.png" in links
    assert client.get(links["results/analysis.json"]).status_code == 200
    assert yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf-8"))["parameters"] == before
    state = (directory / "analysis_state.json").read_bytes()
    config_path = directory / "experiment_config.yaml"
    content, stat = config_path.read_bytes(), config_path.stat()
    for _ in range(2):
        assert client.get(f"{BASE}/runs/{run_id}/summary").status_code == 200
        assert client.get(f"{BASE}/runs/{run_id}/result").status_code == 200
        assert client.get(f"{BASE}/runs/{run_id}/analysis").status_code == 200
    assert (directory / "analysis_state.json").read_bytes() == state
    assert config_path.read_bytes() == content and config_path.stat().st_mtime_ns == stat.st_mtime_ns


def test_invalid_ids_missing_results_and_unsupported_legacy_are_readonly(client):
    directory, _ = synthetic_run(client.root / "data" / ADAPTER.data_type)
    run_id = directory.name
    assert client.get(BASE + "/runs/missing/summary").status_code == 404
    assert client.get(BASE + "/runs/..%2Fescape/summary").status_code == 404
    assert client.get(f"{BASE}/runs/{run_id}/summary").json()["artifacts"] == {}
    path = directory / "experiment_config.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config.pop("raw_format_version")
    config["schema_version"] = 99
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    (directory / "results").mkdir()
    report = directory / "results/analysis.yaml"
    report.write_text("old: true\nuncertainty: .nan\n", encoding="utf-8")
    before = report.read_bytes()
    assert client.get(f"{BASE}/runs/{run_id}/summary").status_code == 200
    assert client.get(f"{BASE}/runs/{run_id}/result").json()["uncertainty"] is None
    assert client.get(f"{BASE}/runs/{run_id}/analysis").status_code == 409
    (directory / "analysis_state.json").write_text(json.dumps({"summary": {"status": "failed", "error": "新分析失败"}}), encoding="utf-8")
    assert client.get(f"{BASE}/runs/{run_id}/result").json()["status"] == "failed"
    assert report.read_bytes() == before
    config["experiment_id"] = "other"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    assert client.get(f"{BASE}/runs/{run_id}/summary").status_code == 422


def test_unexecuted_draft_and_cleanup_hold_global_lock(client, monkeypatch):
    gate = client.root / "shutdown-release"
    monkeypatch.setenv("MX_Z_XY_PHASE_TEST_SHUTDOWN_GATE", str(gate))
    session_id = create(client)
    state = steps(client, session_id, ["A01", "A02", "A03"])
    path = client.root / "data" / ADAPTER.data_type / state["run_id"] / "experiment_config.yaml"
    original = yaml.safe_load(path.read_text(encoding="utf-8"))["parameters"]
    response = client.patch(f"{BASE}/sessions/{session_id}/parameters", json={"revision": state["revision"],
        "parameters": {"PUMP_CARRIER_FREQUENCY_HZ": 99e6, "RESPONSE_RATE_SA_S": 2000.}})
    assert response.status_code == 200, response.text
    wait(lambda: client.service.get(session_id)["pending_request"] is None)
    state = client.service.get(session_id)
    try:
        assert client.post(f"{BASE}/sessions/{session_id}/finish", json={"revision": state["revision"]}).status_code == 200
        wait(lambda: gate.with_suffix(".started").is_file())
        assert client.manager.hardware_lock.locked()
        assert client.post(f"{BASE}/sessions/{session_id}/finish", json={"revision": state["revision"]}).status_code == 409
    finally:
        gate.touch()
    wait(lambda: client.service.get(session_id)["status"] == "closed")
    wait(lambda: not client.manager.hardware_lock.locked())
    report = json.loads(gate.with_suffix(".finished").read_text(encoding="utf-8"))
    assert report["pump_frequency_hz"] == original["PUMP_CARRIER_FREQUENCY_HZ"] and report["errors"] == []
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["parameters"] == original


def test_main_discovery_lifespan_and_legacy_directory_lock(client, monkeypatch):
    from . import main
    assert main.mx_z_optimal_xy_phase_sessions in main._workflow_session_services()
    assert ADAPTER.id in main.WORKFLOW_RUN_FORMATS
    directory, _ = synthetic_run(client.root / "data" / ADAPTER.data_type)
    monkeypatch.setattr(main, "ROOT", client.root)
    monkeypatch.setattr(main, "manager", client.manager)
    monkeypatch.setattr(main, "_workflow_session_services", lambda: (client.service,))
    entered, release = Event(), Event()
    execute = ADAPTER._execute
    def blocked(module, **kwargs):
        if module == ADAPTER.analysis_module:
            assert Path(kwargs["run_dir"]).resolve() == directory.resolve()
            entered.set()
            assert release.wait(30)
        return execute(module, **kwargs)
    monkeypatch.setattr(ADAPTER, "_execute", blocked)
    http = TestClient(main.app)
    try:
        response = http.post(f"/api/runs/{directory.name}/analyze", json={"experiment_id": ADAPTER.id})
        assert response.status_code == 200, response.text
        job = client.manager.get(response.json()["id"])
        assert entered.wait(5)
        assert http.post(f"/api/runs/{directory.name}/analyze", json={"experiment_id": ADAPTER.id}).status_code == 409
        assert client.post(f"{BASE}/runs/{directory.name}/analysis", json={"revision": 0,
            "step_ids": ["B01"], "analysis_parameters": {}}).status_code == 409
    finally:
        release.set()
    wait(lambda: job.status in ("completed", "failed"))
    assert job.status == "completed", "\n".join(str(e.get("message")) for e in job.events)
    assert (directory / "results/xy_rf_phase_response_nonlinear_maps.png").is_file()
