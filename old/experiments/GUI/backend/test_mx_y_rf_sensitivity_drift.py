"""长飘专属真实 API、隔离假仪器子进程和历史只读回归。"""
import json
from dataclasses import replace
from pathlib import Path
from threading import Event

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lab_workflows.experiment_modules.mx_y_rf_sensitivity.test_workflow import copy_config
from lab_workflows.experiment_modules.mx_y_rf_sensitivity_drift.definition import ADAPTER
from lab_workflows.experiment_modules.mx_y_rf_sensitivity_drift.models import MxYRFDriftParams
from lab_workflows.experiment_modules.mx_y_rf_sensitivity_drift import run_format, stages
from lab_workflows.experiment_modules.mx_y_rf_sensitivity_drift.test_analysis import make_run, change_yaml
from lab_workflows.experiment_modules.mx_y_rf_sensitivity_drift.test_session import FAKE_SESSION_MODULE, wait_for
from .jobs import JobManager
from .mx_y_rf_sensitivity_drift import MxYRFDriftSessionService, create_router

BASE = "/api/experiments/" + ADAPTER.id


@pytest.fixture
def client(tmp_path, monkeypatch):
    copy_config(tmp_path)
    diagram = tmp_path / "lab_workflows/experiment_modules/mx_y_rf_sensitivity_drift/workflow.excalidraw"
    diagram.parent.mkdir(parents=True)
    diagram.write_bytes((Path(__file__).resolve().parents[2] / diagram.relative_to(tmp_path)).read_bytes())
    manager = JobManager()
    service = MxYRFDriftSessionService(lambda: tmp_path, manager)
    execute = ADAPTER._execute
    def child(module, **kwargs):
        if module.endswith(".session"):
            module = FAKE_SESSION_MODULE
        else:
            assert module == ADAPTER.analysis_module
            assert kwargs.get("run_dir") and Path(kwargs["run_dir"]).resolve().is_relative_to(tmp_path)
        return execute(module, **kwargs)
    monkeypatch.setattr(ADAPTER, "_execute", child)
    monkeypatch.setenv("MX_Y_RF_DRIFT_TEST_ROOT", str(tmp_path))
    app = FastAPI()
    app.include_router(create_router(service, include_history=True))
    with TestClient(app) as http:
        http.service, http.manager, http.root = service, manager, tmp_path
        yield http
    service.close_communication()
    for job in manager.jobs.values():
        if job.status in ("queued", "running"):
            job.cancellation.cancel()
    wait_for(lambda: all(job.status not in ("queued", "running") for job in manager.jobs.values()))


def create(client, *, duration=1.):
    params = replace(MxYRFDriftParams(), y_rf_amp_points=7, noise_n_avg=2, noise_rate_sa_s=1000,
        noise_duration_s=.256, response_duration_s=.016, system_settle_s=0., temp_switch_on_lag_s=0.,
        drift_duration_s=duration, drift_interval_s=1., y_rf_nt_per_vpp=10.)
    response = client.post(BASE + "/sessions", json={"parameters": params.to_external()})
    assert response.status_code == 200, response.text
    session_id = response.json()["session_id"]
    wait_for(lambda: client.service.get(session_id)["status"] in ("waiting", "closed"))
    state = client.service.get(session_id)
    assert state["status"] == "waiting", client.manager.get(state["job_id"]).events
    return session_id


def steps(client, session_id, selected):
    state = client.service.get(session_id)
    response = client.post(f"{BASE}/sessions/{session_id}/steps", json={"revision": state["revision"], "step_ids": selected})
    assert response.status_code == 200, response.text
    wait_for(lambda: client.service.get(session_id)["pending_request"] is None)
    state = client.service.get(session_id)
    assert not state.get("error"), client.manager.get(state["job_id"]).events
    return state


def through(last):
    ids = [stage.id for stage in stages.ACQUISITION]
    return ids[:ids.index(last) + 1]


def test_real_child_refresh_revision_dependency_and_raw_writes(client):
    flow = client.get(BASE + "/workflow").json()
    assert len(flow["acquisition"]) == 11 and len(flow["analysis"]) == 3
    assert flow["analysis"][-1]["parameters"] == []
    assert client.get(BASE + "/workflow/diagram").json()["type"] == "excalidraw"
    session_id = create(client)
    assert client.manager.hardware_lock.locked()
    assert client.get(BASE + "/sessions/active").json()["session_id"] == session_id
    assert client.post(BASE + "/sessions", json={"parameters": {}}).status_code == 409
    missing = client.post(f"{BASE}/sessions/{session_id}/steps", json={"revision": 0, "step_ids": ["A09"]})
    assert missing.status_code == 422 and "前置" in missing.text
    assert client.post(f"{BASE}/sessions/{session_id}/steps", json={"revision": 0, "step_ids": ["A01", "A01"]}).status_code == 422
    state = steps(client, session_id, through("A10"))
    assert state["steps"]["A07"]["status"] == state["steps"]["A08"]["status"] == "stale"
    refreshed = client.get(f"{BASE}/sessions/{session_id}")
    assert refreshed.status_code == 200, refreshed.text
    snapshot = refreshed.json()
    assert len(snapshot["drift_records"]) == 1 and snapshot["drift_records"][0]["status"] == "completed"
    json.dumps(snapshot, allow_nan=False)
    parent = client.root / "data" / run_format.DATA_TYPE / state["run_id"]
    child = Path(snapshot["drift_records"][0]["child_run_dir"])
    assert (child / "raw/amplitude_scan.npz").is_file() and (child / "raw/noise_001.npz").is_file()
    assert yaml.safe_load((parent / "experiment_config.yaml").read_text(encoding="utf-8"))["completion_status"] == "completed"
    assert client.get(f"{BASE}/runs/{state['run_id']}/summary").json()["drift_summary"]["rows"]
    assert client.post(f"{BASE}/sessions/{session_id}/finish", json={"revision": 0}).status_code == 409
    assert MxYRFDriftSessionService(lambda: client.root, client.manager).active() is None
    response = client.post(f"{BASE}/sessions/{session_id}/finish", json={"revision": state["revision"]})
    assert response.status_code == 200
    wait_for(lambda: not client.manager.hardware_lock.locked())
    final = client.get(f"{BASE}/sessions/{session_id}").json()
    assert final["status"] == "closed" and final["steps"]["A11"]["status"] == "completed"
    trace = json.loads((client.root / "test-instrument-events.json").read_text(encoding="utf-8"))
    for index, event in enumerate(trace["events"]):
        if event == "acquire":
            assert trace["events"][index - 2:index] == ["off", "settle"]
            assert trace["events"][index + 1] == "on" and "save" in trace["events"][index + 2:index + 5]


def test_real_slow_writer_keeps_command_pending_and_rejects_concurrent_mutations(client, monkeypatch):
    gate = client.root / "writer-release"
    monkeypatch.setenv("MX_Y_RF_DRIFT_WRITER_GATE", str(gate))
    session_id = create(client)
    state = steps(client, session_id, through("A08"))
    try:
        response = client.post(f"{BASE}/sessions/{session_id}/steps", json={"revision": state["revision"], "step_ids": ["A09"]})
        assert response.status_code == 200
        wait_for(lambda: gate.with_suffix(".started").exists() or client.service.get(session_id)["status"] == "closed")
        assert gate.with_suffix(".started").exists(), client.manager.get(state["job_id"]).events
        running = client.get(f"{BASE}/sessions/{session_id}").json()
        assert running["current_step"] == "A09" and running["pending_request"]
        assert client.manager.hardware_lock.locked()
        revision = running["revision"]
        assert client.post(f"{BASE}/sessions/{session_id}/steps", json={"revision": revision, "step_ids": ["A01"]}).status_code == 409
        assert client.patch(f"{BASE}/sessions/{session_id}/parameters", json={"revision": revision, "parameters": {"RUN_TAG": "busy"}}).status_code == 409
        assert client.post(f"{BASE}/sessions/{session_id}/finish", json={"revision": revision}).status_code == 409
    finally:
        gate.touch()
    wait_for(lambda: client.service.get(session_id)["pending_request"] is None)
    assert client.service.get(session_id)["steps"]["A09"]["status"] == "completed"
    state = steps(client, session_id, ["A10", "A11"])
    assert state["status"] == "closed"


def test_exported_gui_fixture_and_editable_diagram_match_actual_contract():
    root = Path(__file__).resolve().parents[2]
    fixture = json.loads((root / "GUI/frontend/src/pages/MxYRFSensitivityDriftPage.fixture.json").read_text(encoding="utf-8"))
    assert fixture == json.loads(json.dumps(dict(schema=ADAPTER.schema(), flow=stages.definition())))
    diagram = json.loads((root / "lab_workflows/experiment_modules/mx_y_rf_sensitivity_drift/workflow.excalidraw").read_text(encoding="utf-8"))
    assert diagram["customData"]["workflow"] == json.loads(json.dumps(stages.definition()))
    nodes = {item["customData"]["stage_id"]: item for item in diagram["elements"] if "stage_id" in item.get("customData", {})}
    assert len(nodes) == 14
    for stage in (*stages.ACQUISITION, *stages.ANALYSIS):
        meta = nodes[stage.id]["customData"]
        assert meta["dependencies"] == list(stage.dependencies) and meta["group"] == stage.group
        assert meta["quality_gates"] == list(stage.quality_gates)
        text = next(item["text"] for item in diagram["elements"] if item["id"] == f"text-{stage.id}")
        assert all("".join(gate.split()) in "".join(text.split()) for gate in stage.quality_gates)
    assert not nodes["A11"]["customData"]["dependencies"]


@pytest.mark.parametrize("method", ["cancel", "eof", "shutdown"])
def test_cancel_eof_shutdown_restore_before_releasing_hardware(client, method, monkeypatch):
    gate = client.root / "shutdown-release"
    monkeypatch.setenv("MX_Y_RF_DRIFT_SHUTDOWN_GATE", str(gate))
    session_id = create(client)
    state = steps(client, session_id, through("A08"))
    original = (client.root / "data" / ADAPTER.data_type / state["run_id"] / "experiment_config.yaml").read_bytes()
    response = client.patch(f"{BASE}/sessions/{session_id}/parameters", json={"revision": state["revision"],
        "parameters": {"PUMP_CARRIER_FREQUENCY_HZ": 99000000., "DRIFT_DURATION_S": 2.}})
    assert response.status_code == 200
    wait_for(lambda: client.service.get(session_id)["pending_request"] is None)
    state = client.service.get(session_id)
    stopped = Event()
    closing = None
    try:
        if method == "cancel":
            client.manager.get(state["job_id"]).cancellation.cancel()
        elif method == "eof":
            client.service.close_communication()
        else:
            from threading import Thread
            closing = Thread(target=client.service.shutdown)
            closing.start()
        wait_for(lambda: gate.with_suffix(".started").is_file() or client.service.get(session_id)["status"] == "closed")
        assert gate.with_suffix(".started").is_file(), client.manager.get(state["job_id"]).events
        assert client.manager.hardware_lock.locked()
        probe = client.manager.create("hardware-probe", lambda job: stopped.set())
        assert probe.status == "queued" and not stopped.is_set()
        busy = client.post(f"{BASE}/sessions/{session_id}/steps", json={"revision": state["revision"], "step_ids": ["A01"]})
        assert busy.status_code == 409
        assert not gate.with_suffix(".finished").exists()
    finally:
        gate.touch()
        if closing:
            closing.join(45)
    wait_for(lambda: probe.status == "completed")
    final = client.get(f"{BASE}/sessions/{session_id}").json()
    assert final["status"] == "closed" and final["safety_shutdown"]["completed"]
    trace = json.loads(gate.with_suffix(".finished").read_text(encoding="utf-8"))
    config = yaml.safe_load(original)
    assert trace["safe"] and trace["pump_frequency_hz"] == config["parameters"]["PUMP_CARRIER_FREQUENCY_HZ"]
    saved = yaml.safe_load((client.root / "data" / ADAPTER.data_type / state["run_id"] / "experiment_config.yaml").read_text(encoding="utf-8"))
    assert saved["parameters"] == config["parameters"]


@pytest.mark.parametrize("status", ["running", "acquired", "completed"])
def test_legacy_nan_summary_is_strict_json_read_only_and_stage_state_is_separate(client, status):
    parent = make_run(client.root, status, statuses=("pending", "pending"), marked=False)
    change_yaml(parent / "experiment_config.yaml", lambda config: config.update(completion_status=status))
    results = parent / "results"
    results.mkdir()
    payload = dict(experiment_id=ADAPTER.id, run_dir=str(parent), rows=[dict(cycle_index=0, valid=False,
        sensitivity_flat_median_ft_per_sqrt_hz=float("nan"), error="旧轮次诊断")])
    (results / "analysis.json").write_text(json.dumps(payload), encoding="utf-8")
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in parent.rglob("*") if path.is_file()}
    response = client.get(f"{BASE}/runs/{parent.name}/summary")
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["drift_summary"]["rows"][0]["sensitivity_flat_median_ft_per_sqrt_hz"] is None
    assert value["analysis_status"] == "not_started" and "results/analysis.json" in value["artifacts"]
    json.dumps(value, allow_nan=False)
    state = client.get(f"{BASE}/runs/{parent.name}/analysis")
    if status == "completed":
        assert state.status_code == 200 and state.json()["options"] == {}
        assert all(item["status"] == "pending" for item in state.json()["steps"].values())
    else:
        assert state.status_code == 409 and state.headers["X-Error-Code"] == "run_busy"
    assert {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in parent.rglob("*") if path.is_file()} == before
    assert not (parent / "analysis_state.json").exists()


def test_invalid_cycle_and_pending_records_survive_get_and_real_sse(client, monkeypatch):
    from . import main
    monkeypatch.setenv("MX_Y_RF_DRIFT_INVALID_FIRST_CYCLE", "1")
    session_id = create(client, duration=2.)
    state = steps(client, session_id, through("A08"))
    pending = client.get(f"{BASE}/sessions/{session_id}").json()
    assert [record["status"] for record in pending["drift_records"]] == ["pending", "pending"]
    state = steps(client, session_id, ["A09", "A10"])
    snapshot = client.get(f"{BASE}/sessions/{session_id}")
    assert snapshot.status_code == 200, snapshot.text
    records = snapshot.json()["drift_records"]
    assert [record["status"] for record in records] == ["invalid_quality", "completed"]
    json.dumps(snapshot.json(), allow_nan=False)
    client.post(f"{BASE}/sessions/{session_id}/finish", json={"revision": state["revision"]})
    wait_for(lambda: not client.manager.hardware_lock.locked())
    job = client.manager.get(state["job_id"])
    assert job.status == "completed", job.error
    json.dumps(job.public(), allow_nan=False)
    monkeypatch.setattr(main, "manager", client.manager)
    monkeypatch.setattr(main, "_workflow_session_services", lambda: (client.service,))
    with TestClient(main.app) as http:
        response = http.get(f"/api/jobs/{job.id}/events")
        assert response.status_code == 200, response.text
        snapshots = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
        assert any(item.get("data", {}).get("workflow_session", {}).get("drift_records", [{}])[0].get("status")
            == "invalid_quality" for item in snapshots if item.get("data", {}).get("workflow_session", {}).get("drift_records"))
        assert all("NaN" not in json.dumps(item, allow_nan=False) for item in snapshots)


def test_history_empty_results_identity_path_and_parameters(client):
    parent = make_run(client.root)
    other = make_run(client.root, "other")
    change_yaml(other / "experiment_config.yaml", lambda value: value.update(experiment_id="wrong"))
    assert client.get(BASE + "/runs").json()["total"] == 1
    value = client.get(f"{BASE}/runs/{parent.name}/summary").json()
    assert value["parameters"] == {"UNCHANGED_PARAMETER": 17}
    assert value["artifacts"] == value["drift_summary"] == {}
    assert client.get(BASE + "/runs/missing/summary").status_code == 404
    assert client.get(BASE + "/runs/other/summary").status_code == 422
    assert client.service.run_directory(parent.name).resolve() == parent.resolve()
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        client.service.run_directory("../parent")
    assert client.get(f"{BASE}/runs/{parent.name}/files/experiment_config.yaml").status_code == 200


def test_real_analysis_child_empty_options_concurrency_revisions_and_immutable_raw(client, monkeypatch):
    parent = make_run(client.root, statuses=("pending", "pending"))
    before = (parent / run_format.MANIFEST).read_bytes()
    url = f"{BASE}/runs/{parent.name}/analysis"
    state = client.get(url).json()
    assert state["options"] == {}
    body = dict(revision=state["revision"], step_ids=["B01", "B02", "B03"], analysis_parameters={})
    assert client.post(url, json=body | {"step_ids": ["B03"]}).status_code == 422
    assert client.post(url, json=body | {"analysis_parameters": {"FIT_R_SQUARED_MIN": .9}}).status_code == 422
    execute = ADAPTER._execute
    entered, release = Event(), Event()
    def blocked(module, **kwargs):
        entered.set()
        assert release.wait(30)
        return execute(module, **kwargs)
    monkeypatch.setattr(ADAPTER, "_execute", blocked)
    try:
        response = client.post(url, json=body)
        assert response.status_code == 200, response.text
        job = client.manager.get(response.json()["id"])
        assert entered.wait(5)
        assert not client.manager.hardware_lock.locked()
        duplicate = client.post(url, json=body)
        assert duplicate.status_code == 409 and duplicate.headers["X-Error-Code"] == "analysis_running"
    finally:
        release.set()
    wait_for(lambda: job.status in ("completed", "failed"))
    assert job.status == "completed", job.error
    result = client.get(url)
    assert result.status_code == 200, result.text
    assert all(item["status"] == "completed" for item in result.json()["steps"].values())
    assert result.json()["summary"]["valid_cycle_count"] == 0
    assert client.post(url, json=body).status_code == 409
    assert (parent / run_format.MANIFEST).read_bytes() == before
    summary = client.get(f"{BASE}/runs/{parent.name}/summary").json()
    assert summary["drift_summary"]["valid_cycle_count"] == 0
    assert all("results/" + name in summary["artifacts"] for name in ("drift_summary.csv", "drift_trend.png", "drift_trend_clock_time.png"))


def test_real_main_routes_old_reanalysis_is_explicit_and_running_is_blocked(client, monkeypatch):
    from . import main
    monkeypatch.setattr(main, "ROOT", client.root)
    monkeypatch.setattr(main, "manager", client.manager)
    monkeypatch.setattr(main, "_workflow_session_services", lambda: (client.service,))
    monkeypatch.setattr(main.mx_y_rf_drift_sessions, "manager", client.manager)
    parent = make_run(client.root, statuses=("pending",))
    with TestClient(main.app) as http:
        dedicated = http.get(BASE + "/runs")
        assert dedicated.status_code == 200 and dedicated.json()["total"] == 1
        summary = http.get(f"{BASE}/runs/{parent.name}/summary")
        assert summary.status_code == 200 and summary.json()["drift_summary"] == {}
        assert http.get(BASE + "/workflow").json()["analysis"] == json.loads(json.dumps(stages.definition()["analysis"]))
        listed = http.get("/api/runs", params={"experiment_id": ADAPTER.id})
        assert listed.status_code == 200 and listed.json()["runs"][0]["id"] == parent.name
        change_yaml(parent / "experiment_config.yaml", lambda config: config.update(completion_status="running"))
        assert http.post(f"/api/runs/{parent.name}/analyze", json={"experiment_id": ADAPTER.id}).status_code == 409
        change_yaml(parent / "experiment_config.yaml", lambda config: config.update(completion_status="completed"))
        response = http.post(f"/api/runs/{parent.name}/analyze", json={"experiment_id": ADAPTER.id})
        assert response.status_code == 200, response.text
        job = client.manager.get(response.json()["id"])
        wait_for(lambda: job.status in ("completed", "failed"))
        assert job.status == "completed", job.error
        assert (parent / "results/drift_summary.csv").is_file()
