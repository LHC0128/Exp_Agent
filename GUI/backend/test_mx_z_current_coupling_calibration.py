"""Mx Z 实际电流标定 真实子进程会话、全局互斥、历史与独立分析接口。"""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
import json
import os
import time
import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from lab_workflows.experiment_modules.mx_z_current_coupling_calibration.definition import ADAPTER
from lab_workflows.experiment_modules.mx_z_current_coupling_calibration.test_workflow import copy_config, small_params
from lab_workflows.experiment_modules.mx_z_current_coupling_calibration import stages
from .jobs import JobManager
from .mx_z_current_coupling_calibration import MxZCurrentCouplingCalibrationSessionService, create_router

BASE = "/api/experiments/mx-z-current-coupling-calibration"


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
    diagram = tmp_path / "lab_workflows/experiment_modules/mx_z_current_coupling_calibration/workflow.excalidraw"
    diagram.parent.mkdir(parents=True)
    diagram.write_bytes((Path(__file__).resolve().parents[2] /
        "lab_workflows/experiment_modules/mx_z_current_coupling_calibration/workflow.excalidraw").read_bytes())
    manager = JobManager()
    service = MxZCurrentCouplingCalibrationSessionService(lambda: tmp_path, manager)
    original = ADAPTER._execute

    def execute(module, **kwargs):
        if module.endswith(".session"):
            module = "GUI.backend.test_mx_z_current_coupling_calibration"
        return original(module, **kwargs)

    monkeypatch.setattr(ADAPTER, "_execute", execute)
    monkeypatch.setenv("MX_Z_CURRENT_TEST_ROOT", str(tmp_path))
    app = FastAPI()
    app.include_router(create_router(service, include_history=True))
    with TestClient(app) as http:
        http.service, http.manager, http.root = service, manager, tmp_path
        yield http
    (tmp_path / "release-acquisition").touch()
    (tmp_path / "release-shutdown").touch()
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
    assert value["status"] == "waiting", json.dumps(client.manager.get(value["job_id"]).public(), ensure_ascii=False)
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
    assert len(flow["acquisition"]) == 11 and len(flow["analysis"]) == 4
    expected = {stage.id: list(stage.quality_gates) for stage in (*stages.ACQUISITION, *stages.ANALYSIS)}
    assert {stage["id"]: stage["quality_gates"] for column in ("acquisition", "analysis") for stage in flow[column]} == expected
    diagram = client.get(BASE + "/workflow/diagram").json()
    assert diagram["type"] == "excalidraw"
    notes = {item["customData"]["workflow_stage"]: item for item in diagram["elements"]
        if item.get("customData", {}).get("role") == "quality_gates"}
    assert set(notes) == {key for key, value in expected.items() if value}
    for key, item in notes.items():
        assert item["customData"]["quality_gates"] == expected[key]
        assert "".join(expected[key]).replace(" ", "") in item["text"].replace("\n", "").replace(" ", "")
    session_id = create(client)
    assert client.manager.hardware_lock.locked()
    current = client.get(BASE + "/sessions/active").json()
    assert current["session_id"] == session_id
    assert client.post(BASE + "/sessions", json={"parameters": {}}).status_code == 409
    response = client.post(f"{BASE}/sessions/{session_id}/steps", json={"revision": 0, "step_ids": ["A09"]})
    assert response.status_code == 422
    state = steps(client, session_id, [f"A{index:02d}" for index in range(1, 9)])
    assert client.get(f"{BASE}/runs/{state['run_id']}/analysis").status_code == 409
    assert client.post(f"{BASE}/sessions/{session_id}/finish", json={"revision": 0}).status_code == 409
    assert client.get(f"{BASE}/sessions/{session_id}").json()["revision"] == state["revision"]
    # 新服务不能从磁盘恢复仍带输出的硬件会话。
    assert MxZCurrentCouplingCalibrationSessionService(lambda: client.root, client.manager).active() is None
    body = {"revision": state["revision"], "step_ids": ["A09", "A10"]}
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
    assert state["steps"]["A11"]["status"] == "completed"


@pytest.mark.parametrize("method", ["cancel", "eof", "shutdown", "finish"])
def test_cancel_eof_backend_shutdown_safely_close_real_child(client, method):
    session_id = create(client)
    state = steps(client, session_id, [f"A{index:02d}" for index in range(1, 9)])
    (client.root / "pause-shutdown").touch()
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = None
        try:
            if method == "cancel":
                client.manager.get(state["job_id"]).cancellation.cancel()
            elif method == "eof":
                client.service.close_communication()
            elif method == "shutdown":
                pending = executor.submit(client.service.shutdown)
            else:
                assert client.post(f"{BASE}/sessions/{session_id}/finish", json={"revision": state["revision"]}).status_code == 200
            wait(lambda: (client.root / "shutdown-entered").is_file())
            assert client.manager.hardware_lock.locked()
            assert client.manager.get(state["job_id"]).status == "running"
            assert not client.manager.try_acquire_short_hardware()
            assert client.post(BASE + "/sessions", json={"parameters": small_params().to_external()}).status_code == 409
        finally:
            (client.root / "release-shutdown").touch()
        if pending:
            pending.result(timeout=40)
    wait(lambda: client.service.get(session_id)["status"] == "closed")
    wait(lambda: not client.manager.hardware_lock.locked())
    final = client.service.get(session_id)
    assert final["safety_shutdown"]["completed"]
    assert final["outputs"]["Y RF"] == "OFF"
    config = yaml.safe_load((client.root / "data/Mx_Z_Current_Coupling_Calibration" / state["run_id"] /
        "experiment_config.yaml").read_text(encoding="utf-8"))
    assert config["completion_status"] in ("cancelled", "failed")


def test_child_protocol_rejects_duplicate_and_stale_commands_without_work(client):
    session_id = create(client)
    item = client.service.sessions[session_id]
    job = client.manager.get(item["state"]["job_id"])
    command = {"request_id": "independent-check", "revision": 0, "action": "steps", "step_ids": ["A01"]}
    def results():
        return [event["data"]["command_result"] for event in job.events if "command_result" in event.get("data", {})]
    item["queue"].put(command)
    wait(lambda: len(results()) == 1)
    assert results()[0]["error"] is None
    assert client.service.get(session_id)["revision"] == 1
    item["queue"].put(command | {"revision": 1})
    wait(lambda: len(results()) == 2)
    assert "重复" in results()[-1]["error"]
    item["queue"].put(command | {"request_id": "stale-check"})
    wait(lambda: len(results()) == 3)
    assert "修订" in results()[-1]["error"]
    assert client.service.get(session_id)["revision"] == 1
    assert not (client.root / "data/Mx_Z_Current_Coupling_Calibration").exists()


def test_concurrent_api_commands_accept_only_one_revision(client):
    session_id = create(client)
    barrier = Barrier(2)
    def submit():
        barrier.wait(timeout=5)
        return client.post(f"{BASE}/sessions/{session_id}/steps", json={"revision": 0, "step_ids": ["A01"]}).status_code
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _: submit(), range(2)))
    assert sorted(responses) == [200, 409]
    wait(lambda: client.service.get(session_id)["pending_request"] is None)
    assert client.service.get(session_id)["revision"] == 1


def test_api_parameter_apply_preserves_draft_failure_and_only_invalidates_real_impacts(client):
    session_id = create(client)
    before = steps(client, session_id, [f"A{index:02d}" for index in range(1, 9)])
    endpoint = f"{BASE}/sessions/{session_id}/parameters"
    invalid = client.patch(endpoint, json={"revision": before["revision"],
        "parameters": {"ZERO_BIAS_CENTER_FREQUENCY_HZ": 0}})
    assert invalid.status_code == 422
    assert client.service.get(session_id) == before
    path = client.root / "data" / ADAPTER.data_type / before["run_id"] / "experiment_config.yaml"
    frozen = path.read_bytes()
    changed = {"ZERO_BIAS_CENTER_FREQUENCY_HZ": 90500, "SYSTEM_SETTLE_S": .002}
    response = client.patch(endpoint, json={"revision": before["revision"], "parameters": changed})
    assert response.status_code == 200, response.text
    wait(lambda: client.service.get(session_id)["pending_request"] is None)
    after = client.service.get(session_id)
    assert after["revision"] == before["revision"] + 1
    assert all(after["parameters"][key] == value for key, value in changed.items())
    expected = set().union(*(stages.definition()["parameter_impacts"]["acquisition"][key] for key in changed))
    completed = {key for key, value in before["steps"].items() if value["status"] == "completed"}
    assert {key for key, value in after["steps"].items() if value["status"] == "stale"} == completed & expected
    assert after["run_id"] == before["run_id"] and path.read_bytes() == frozen
    assert client.post(f"{BASE}/sessions/{session_id}/steps", json={
        "revision": after["revision"], "step_ids": ["A09"]}).status_code == 422


def test_route_fixture_matches_real_visible_model_fields_and_stage_metadata():
    root = Path(__file__).resolve().parents[2]
    fixture = json.loads((root / "GUI/frontend/src/pages/MxZCurrentCouplingCalibrationPage.fixture.json").read_text(encoding="utf-8"))
    assert fixture["flow"] == json.loads(json.dumps(stages.definition()))
    assert fixture["schema"] == small_params().schema(root)


def legacy_result_run(client, content, **config_values):
    directory = client.root / "data" / ADAPTER.data_type / "0914_legacy_result"
    (directory / "results").mkdir(parents=True)
    config = dict(experiment_id=ADAPTER.id, data_type=ADAPTER.data_type,
        timestamp="0914", run_tag="legacy", completion_status="failed", analysis_status="completed",
        parameters=small_params().to_external())
    config.update(config_values)
    (directory / "experiment_config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    (directory / "results/analysis.json").write_bytes(content)
    return directory


@pytest.mark.parametrize("completion_status, include_data_type", [
    ("completed", True), ("failed", True), ("cancelled", False), ("running", True), ("acquired", False),
])
def test_read_only_legacy_result_normalizes_nonfinite_json_without_reanalysis(client, completion_status, include_data_type):
    original = '''{"success":false,"K_Z_Hz_per_A":NaN,"K_Z_Hz_per_Vsense":Infinity,
        "f_0_at_zero_current_hz":-Infinity,"accepted_curve_count":3,"total_curve_count":5,
        "rejection_reasons":["旧频扫质量未通过"],"linearity":{"r_squared":NaN},
        "hysteresis":{"maximum_hz":Infinity},"repeatability":{"std":-Infinity},
        "operating_point":{"overflow":1e999},"curves":[{"fit":{"parameters":[NaN,Infinity,-Infinity,1e999]}}],
        "note":"NaN / Infinity 文字保持原样"}'''.encode("utf-8")
    directory = legacy_result_run(client, original, completion_status=completion_status,
        raw_format_version=99)
    config_path = directory / "experiment_config.yaml"
    if not include_data_type:
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        config.pop("data_type")
        config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    (directory / "analysis_state.json").write_text(
        json.dumps({"summary": {"status": "quality_failed", "error": ""}}), encoding="utf-8")
    plots = {"current_coupling_calibration.png": b"original current plot",
             "frequency_response_fits.png": b"original frequency plot"}
    for name, content in plots.items():
        (directory / "results" / name).write_bytes(content)
    before = {path: (path.stat().st_mtime_ns, path.read_bytes()) for path in directory.rglob("*") if path.is_file()}
    response = client.get(f"{BASE}/runs/{directory.name}/result")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/json"
    payload = json.loads(response.content, parse_constant=lambda value: pytest.fail(f"响应包含非标准 JSON：{value}"))
    assert payload["success"] is False
    for key in ("K_Z_Hz_per_A", "K_Z_Hz_per_Vsense", "f_0_at_zero_current_hz"):
        assert payload[key] is None
    assert payload["accepted_curve_count"] == 3 and payload["total_curve_count"] == 5
    assert payload["rejection_reasons"] == ["旧频扫质量未通过"]
    assert payload["linearity"] == {"r_squared": None}
    assert payload["hysteresis"] == {"maximum_hz": None}
    assert payload["repeatability"] == {"std": None}
    assert payload["operating_point"] == {"overflow": None}
    assert payload["curves"][0]["fit"]["parameters"] == [None] * 4
    assert payload["note"] == "NaN / Infinity 文字保持原样"
    summary = client.get(f"{BASE}/runs/{directory.name}/summary").json()
    assert summary["completion_status"] == completion_status
    assert summary["analysis_status"] == "quality_failed"
    assert client.get(summary["artifacts"]["results/analysis.json"]).content == original
    for name, content in plots.items():
        assert client.get(summary["artifacts"][f"results/{name}"]).content == content
    assert {path: (path.stat().st_mtime_ns, path.read_bytes()) for path in directory.rglob("*") if path.is_file()} == before
    assert not client.manager.jobs


@pytest.mark.parametrize("content", [b"[]", b"null", b'{"success":', b"\xff", b"true"])
def test_legacy_result_rejects_unreadable_or_nonmapping_json(client, content):
    directory = legacy_result_run(client, content)
    response = client.get(f"{BASE}/runs/{directory.name}/result")
    assert response.status_code == 422, response.text
    assert (directory / "results/analysis.json").read_bytes() == content
    assert not client.manager.jobs


@pytest.mark.parametrize("config_values", [{"experiment_id": None}, {"experiment_id": "another-experiment"}, {"data_type": "another-type"}])
def test_legacy_result_rejects_wrong_identity(client, config_values):
    directory = legacy_result_run(client, b'{"success":false}', **config_values)
    assert client.get(f"{BASE}/runs/{directory.name}/result").status_code == 422


def test_legacy_result_keeps_missing_fields_and_validates_paths(client):
    directory = legacy_result_run(client, b'{"success":false}')
    assert client.get(f"{BASE}/runs/{directory.name}/result").json() == {"success": False}
    assert client.get(BASE + "/runs/not-a-run/result").status_code == 404
    assert client.get(BASE + "/runs/..%2Fescape/result").status_code == 404
    (directory / "results/analysis.json").unlink()
    assert client.get(f"{BASE}/runs/{directory.name}/result").status_code == 404


def test_history_snapshots_missing_results_and_analysis_isolation(client):
    session_id = create(client)
    state = steps(client, session_id, [f"A{index:02d}" for index in range(1, 11)])
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
    assert refreshed["parameters"]["LINEAR_R_SQUARED_MIN"] == 0.99
    assert any(name.endswith("current_coupling_calibration.png") for name in refreshed["artifacts"])
    assert "results/current_coupling_calibration.png" in refreshed["artifacts"]
    assert client.get(f"{BASE}/runs/{run_id}/analysis").json()["options"]["LINEAR_R_SQUARED_MIN"] == 0.9999
    state = client.get(f"{BASE}/runs/{run_id}/analysis").json()
    record = state["steps"]["B04"]
    assert record["input_fingerprint"] == state["fingerprint"]
    assert len(record["parameter_fingerprint"]) == 64
    assert record["parameters"] == {"LINEAR_R_SQUARED_MIN": 0.9999}
    assert record["revision"] == state["revision"]
    signature = record["files"]["analysis.json"]
    assert len(signature) == 3 and isinstance(signature[2], str) and len(signature[2]) == 64
    assert client.get(refreshed["artifacts"]["results/current_coupling_calibration.png"]).status_code == 200
    payload = client.get(refreshed["artifacts"]["results/analysis.json"]).json()
    assert payload["success"] and payload["K_Z_Hz_per_A"] == pytest.approx(100000)
    assert payload["K_Z_Hz_per_Vsense"] == pytest.approx(10000)
    assert payload["f_0_at_zero_current_hz"] == pytest.approx(89900)
    assert payload["hysteresis"]["available"] and "current_std_a_mean" in payload["repeatability"]
    assert all("rejection_reasons" in curve["fit"] for curve in payload["curves"])


def test_legacy_reanalysis_and_stage_api_share_the_same_directory_lock(client, monkeypatch):
    from . import main
    monkeypatch.setattr(main, "ROOT", client.root)
    monkeypatch.setattr(main, "manager", client.manager)
    monkeypatch.setattr(main, "_workflow_session_services", lambda: [client.service])
    route = next(route for route in main.app.routes if getattr(route, "path", None) == "/api/runs/{run_id}/analyze")
    client.app.add_api_route(route.path, route.endpoint, methods=["POST"])
    session_id = create(client)
    state = steps(client, session_id, [f"A{index:02d}" for index in range(1, 11)])
    run_id = state["run_id"]
    entered, release = Event(), Event()
    original = ADAPTER._execute
    def blocked(module, **kwargs):
        if module == ADAPTER.analysis_module:
            entered.set()
            if not release.wait(timeout=30):
                raise TimeoutError("假分析等待释放超时")
        return original(module, **kwargs)
    monkeypatch.setattr(ADAPTER, "_execute", blocked)
    staged = {"revision": 0, "step_ids": ["B01", "B02", "B03", "B04"]}
    try:
        job = client.post(f"{BASE}/runs/{run_id}/analysis", json=staged)
        assert job.status_code == 200, job.text
        assert entered.wait(timeout=5)
        blocked_response = client.post(f"/api/runs/{run_id}/analyze", json={"experiment_id": ADAPTER.id})
        assert blocked_response.status_code == 409 and blocked_response.headers["X-Error-Code"] == "analysis_running"
    finally:
        release.set()
    job_id = job.json()["id"]
    wait(lambda: client.manager.get(job_id).status not in ("queued", "running"))
    assert client.manager.get(job_id).status == "completed", client.manager.get(job_id).error
    entered.clear(); release.clear()
    try:
        job = client.post(f"/api/runs/{run_id}/analyze", json={"experiment_id": ADAPTER.id})
        assert job.status_code == 200, job.text
        assert entered.wait(timeout=5)
        staged["revision"] = client.get(f"{BASE}/runs/{run_id}/analysis").json()["revision"]
        blocked_response = client.post(f"{BASE}/runs/{run_id}/analysis", json=staged)
        assert blocked_response.status_code == 409 and blocked_response.headers["X-Error-Code"] == "analysis_running"
    finally:
        release.set()
    job_id = job.json()["id"]
    wait(lambda: client.manager.get(job_id).status not in ("queued", "running"))
    assert client.manager.get(job_id).status == "completed", client.manager.get(job_id).error
    assert list((client.root / "data/Mx_Z_Current_Coupling_Calibration" / run_id / "results").glob("before_*/analysis.json"))


@pytest.mark.parametrize("method", ["cancel", "eof", "shutdown"])
def test_close_during_real_scan_keeps_lock_through_safety_shutdown(client, method):
    session_id = create(client)
    state = steps(client, session_id, [f"A{index:02d}" for index in range(1, 9)])
    (client.root / "pause-acquisition").touch()
    (client.root / "pause-shutdown").touch()
    response = client.post(f"{BASE}/sessions/{session_id}/steps", json={
        "revision": state["revision"], "step_ids": ["A09"]})
    assert response.status_code == 200, response.text
    wait(lambda: (client.root / "acquisition-entered").is_file())
    assert client.service.get(session_id)["status"] == "running"
    assert client.manager.hardware_lock.locked()
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = None
        try:
            if method == "cancel":
                client.manager.get(state["job_id"]).cancellation.cancel()
            elif method == "eof":
                client.service.close_communication()
            else:
                pending = executor.submit(client.service.shutdown)
            if method != "cancel":
                assert client.manager.hardware_lock.locked()
                (client.root / "release-acquisition").touch()
            wait(lambda: (client.root / "shutdown-entered").is_file())
            assert client.manager.hardware_lock.locked()
            assert not client.manager.try_acquire_short_hardware()
            assert client.manager.get(state["job_id"]).status == "running"
        finally:
            (client.root / "release-acquisition").touch()
            (client.root / "release-shutdown").touch()
        if pending:
            pending.result(timeout=40)
    wait(lambda: not client.manager.hardware_lock.locked())
    final = client.service.get(session_id)
    assert final["status"] == "closed" and final["safety_shutdown"]["completed"]
    assert final["outputs"]["Y RF"] == "OFF" and final["outputs"]["温控"] == "ON"
    directory = client.root / "data" / ADAPTER.data_type / state["run_id"]
    assert (directory / "raw/z_000/sense_current.npz").is_file()
    assert (directory / "raw/z_000/frequency_0000_attempt_00.npz").is_file()
    config = yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf-8"))
    assert config["completion_status"] in ("failed", "cancelled")


@pytest.mark.parametrize("change, code", [
    ({"experiment_id": "mx-z-field-calibration"}, 422),
    ({"experiment_id": None}, 422),
    ({"raw_format_version": 99}, 422),
    ({"completion_status": "running"}, 409),
    ({"completion_status": "acquired"}, 409),
])
def test_both_analysis_routes_reject_ineligible_directory_before_job_creation(client, monkeypatch, change, code):
    from . import main
    monkeypatch.setattr(main, "ROOT", client.root)
    monkeypatch.setattr(main, "manager", client.manager)
    monkeypatch.setattr(main, "_workflow_session_services", lambda: [client.service])
    route = next(route for route in main.app.routes if getattr(route, "path", None) == "/api/runs/{run_id}/analyze")
    client.app.add_api_route(route.path, route.endpoint, methods=["POST"])
    session_id = create(client)
    state = steps(client, session_id, [f"A{index:02d}" for index in range(1, 11)])
    directory = client.root / "data" / ADAPTER.data_type / state["run_id"]
    config_path = directory / "experiment_config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config.update(change)
    config_path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    before = set(client.manager.jobs)
    endpoint = f"{BASE}/runs/{state['run_id']}/analysis"
    assert client.get(endpoint).status_code == code
    assert client.post(endpoint, json={"revision": 0, "step_ids": ["B01"]}).status_code == code
    assert client.post(f"/api/runs/{state['run_id']}/analyze", json={"experiment_id": ADAPTER.id}).status_code == code
    assert set(client.manager.jobs) == before


def test_real_analysis_quality_failure_is_not_success_even_when_process_completed(client, monkeypatch):
    from . import main
    monkeypatch.setattr(main, "ROOT", client.root)
    monkeypatch.setattr(main, "manager", client.manager)
    monkeypatch.setattr(main, "_workflow_session_services", lambda: [client.service])
    route = next(route for route in main.app.routes if getattr(route, "path", None) == "/api/runs/{run_id}/analyze")
    client.app.add_api_route(route.path, route.endpoint, methods=["POST"])
    session_id = create(client)
    state = steps(client, session_id, [f"A{index:02d}" for index in range(1, 11)])
    directory = client.root / "data" / ADAPTER.data_type / state["run_id"]
    import numpy as np
    path = directory / "raw/z_000/frequency_scan.npz"
    with np.load(path) as loaded:
        data = {key: loaded[key] for key in loaded.files}
    data["sense_display_edge_fraction"] = np.asarray(.99)
    np.savez(path, **data)
    endpoint = f"{BASE}/runs/{state['run_id']}/analysis"
    response = client.post(endpoint, json={"revision": 0, "step_ids": [stage.id for stage in stages.ANALYSIS]})
    assert response.status_code == 200, response.text
    job_id = response.json()["id"]
    wait(lambda: client.manager.get(job_id).status not in ("queued", "running"))
    assert client.manager.get(job_id).status == "completed", client.manager.get(job_id).error
    result = client.get(endpoint)
    assert result.status_code == 200, result.text
    analysis = result.json()
    assert analysis["summary"]["status"] == "quality_failed" and not analysis["summary"]["success"]
    assert analysis["steps"]["B04"]["status"] == "quality_failed"
    summary = client.get(f"{BASE}/runs/{state['run_id']}/summary").json()
    assert summary["analysis_status"] == "quality_failed"
    payload = client.get(summary["artifacts"]["results/analysis.json"]).json()
    assert not payload["success"] and payload["rejection_reasons"]
    assert "results/current_coupling_calibration.png" in summary["artifacts"]
    response = client.post(f"/api/runs/{state['run_id']}/analyze", json={"experiment_id": ADAPTER.id})
    assert response.status_code == 200, response.text
    job_id = response.json()["id"]
    wait(lambda: client.manager.get(job_id).status not in ("queued", "running"))
    assert client.manager.get(job_id).status == "completed", client.manager.get(job_id).error
    config = yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf-8"))
    assert config["analysis_status"] == "completed"
    assert client.get(f"{BASE}/runs/{state['run_id']}/summary").json()["analysis_status"] == "quality_failed"


def test_main_app_real_routes_reach_this_service_and_history(client, monkeypatch):
    from . import main
    service = main.mx_z_current_coupling_sessions
    monkeypatch.setattr(main, "ROOT", client.root)
    monkeypatch.setattr(main, "manager", client.manager)
    monkeypatch.setattr(service, "manager", client.manager)
    monkeypatch.setattr(service, "sessions", client.service.sessions)
    assert service in main._workflow_session_services()
    with TestClient(main.app) as http:
        definition = http.get(BASE)
        assert definition.status_code == 200 and definition.json()["id"] == ADAPTER.id
        assert http.get(BASE + "/schema").status_code == 200
        assert http.get(BASE + "/workflow").json() == json.loads(json.dumps(stages.definition()))
        assert http.get(BASE + "/workflow/diagram").json()["type"] == "excalidraw"
        response = http.post(BASE + "/sessions", json={"parameters": small_params().to_external()})
        assert response.status_code == 200, response.text
        session_id = response.json()["session_id"]
        wait(lambda: service.get(session_id)["status"] == "waiting")
        state = steps(client, session_id, [f"A{index:02d}" for index in range(1, 11)])
        run_id = state["run_id"]
        assert http.get(BASE + "/runs?limit=50&offset=0").json()["runs"][0]["run_id"] == run_id
        assert http.get(f"{BASE}/runs/{run_id}/summary").json()["parameters"]["SENSE_RESISTOR_OHM"] == 10
        assert http.get(f"{BASE}/runs/{run_id}/files/raw/z_000/sense_current.npz").status_code == 200
        response = http.post(f"/api/runs/{run_id}/analyze", json={"experiment_id": ADAPTER.id})
        assert response.status_code == 200, response.text
        job_id = response.json()["id"]
        wait(lambda: client.manager.get(job_id).status not in ("queued", "running"))
        assert client.manager.get(job_id).status == "completed", client.manager.get(job_id).error
        assert http.get(f"{BASE}/runs/{run_id}/analysis").json()["steps"]["B04"]["status"] == "completed"
        assert http.get(f"{BASE}/runs/{run_id}/result").json()["success"] is True
    assert not client.manager.hardware_lock.locked()


if __name__ == "__main__":
    from lab_workflows.experiment_modules.mx_z_current_coupling_calibration import session, acquisition, preparation
    from lab_workflows.experiment_modules.mx_z_current_coupling_calibration.test_workflow import install_rig
    from lab_workflows.experiment_modules.mx_y_rf_sensitivity import acquisition as r_acquisition
    root = Path(os.environ["MX_Z_CURRENT_TEST_ROOT"])
    payload = json.loads(os.environ["LAB_TYPED_PARAMETERS"])
    payload.pop("session_id")
    patcher = pytest.MonkeyPatch()
    rig = install_rig(root, patcher, ADAPTER.params_type.from_external(payload), progress=True)
    shutdown = preparation.safe_shutdown

    def tracked_shutdown(*args, **kwargs):
        rig.shutting_down = True
        try:
            return shutdown(*args, **kwargs)
        finally:
            rig.shutting_down = False

    acquire = r_acquisition.daq.acquire_data

    def paused_acquire(*args, **kwargs):
        if rig.captures and (root / "pause-acquisition").is_file():
            (root / "acquisition-entered").touch()
            deadline = time.monotonic() + 40
            while not (root / "release-acquisition").is_file():
                kwargs["cancellation_check"]()
                if time.monotonic() > deadline:
                    raise TimeoutError("假仪器等待采集释放超时")
                time.sleep(.01)
        return acquire(*args, **kwargs)

    patcher.setattr(preparation, "safe_shutdown", tracked_shutdown)
    patcher.setattr(r_acquisition.daq, "acquire_data", paused_acquire)
    session.main()
