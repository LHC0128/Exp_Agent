"""在主应用真路由上验证相位灵敏度接线；仅子进程执行用替身。"""

import json
import shutil
from pathlib import Path
from threading import Event

import pytest
import yaml
from fastapi.testclient import TestClient

from lab_workflows.common import DATA_ROOT_ENV, ProgressEvent, data_root
from lab_workflows.experiment_modules.rf_phase_sensitivity import run_format
from lab_workflows.experiment_modules.rf_phase_sensitivity.definition import ADAPTER, DEFINITION
from lab_workflows.experiment_modules.rf_phase_sensitivity.models import RfPhaseSensitivityParams
from lab_workflows.setups import SETUP_ENV_VAR

from . import main
from .jobs import JobManager

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "GUI/frontend/src/pages/RfPhaseSensitivityPage.fixture.json"
ID = "rf-phase-sensitivity"
BASE = f"/api/experiments/{ID}"
RESPONSE_BASE = "/api/experiments/rf-phase-response"


@pytest.fixture
def client(tmp_path, monkeypatch):
    config_dir = tmp_path / "params"
    config_dir.mkdir()
    for filename in ("devices.yaml", "mapping.yaml", "safety_limits.yaml"):
        shutil.copy2(ROOT / "params" / filename, config_dir / filename)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(SETUP_ENV_VAR, raising=False)
    monkeypatch.setenv(DATA_ROOT_ENV, str(tmp_path / "fake_runs"))
    monkeypatch.setattr(ADAPTER, "root", tmp_path)
    monkeypatch.setattr(main, "ROOT", tmp_path)

    manager = JobManager()
    service = main.rf_phase_sensitivity_sessions
    monkeypatch.setattr(main, "manager", manager)
    monkeypatch.setattr(service, "manager", manager)
    monkeypatch.setattr(service, "sessions", {})

    def unexpected_execution(*args, **kwargs):
        raise AssertionError("API 测试不能启动真实采集或分析子进程")

    monkeypatch.setattr(ADAPTER, "_execute", unexpected_execution)
    # 不执行主应用 lifespan：只收尾本用例新建的任务管理器和空会话表。
    http = TestClient(main.app)
    try:
        yield http
    finally:
        service.close_communication()
        manager.wait_for_shutdown()
        http.close()


def test_discovery_schema_workflow_and_real_preflight(client):
    assert main.rf_phase_sensitivity_sessions in main._workflow_session_services()
    assert main.rf_phase_sensitivity_sessions.run_history is DEFINITION.run_history
    assert main.WORKFLOW_RUN_FORMATS[ID] is run_format

    catalog = client.get("/api/experiments")
    assert catalog.status_code == 200, catalog.text
    entries = {item["id"]: item for item in catalog.json()}
    definition = entries[ID]
    assert definition["title"] == "射频相位灵敏度"
    assert definition["execution_mode"] == "typed_workflow" and definition["can_analyze"]
    assert definition["data_type"] != entries["rf-phase-response"]["data_type"]

    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    schema = client.get(f"{BASE}/schema")
    flow = client.get(f"{BASE}/workflow")
    assert schema.status_code == flow.status_code == 200
    assert schema.json()["fields"] == fixture["fields"]
    manifest = flow.json()
    assert manifest == fixture["flow"]
    assert [stage["id"] for stage in manifest["acquisition"]] == ["A01", "A02", "A03", "A04", "A05", "A06", "A07"]
    assert [stage["id"] for stage in manifest["analysis"]] == ["B01"]
    assert (manifest["run_step_id"], manifest["finish_step_id"]) == ("A06", "A07")
    assert manifest["diagram_url"] == f"{BASE}/workflow/diagram"
    assert client.get(f"{RESPONSE_BASE}/workflow").json()["diagram_url"] == f"{RESPONSE_BASE}/workflow/diagram"

    parameters = {"PM_FREQUENCIES_HZ": [10.0, 20.0, 40.0], "Y_RF_VPP": 0.2, "Y_BIAS_V": 0.5}
    response = client.post(f"{BASE}/preflight", json={"parameters": parameters})
    assert response.status_code == 200 and response.json()["ok"], response.text
    for bad in ({"Y_RF_FREQ_HZ": 6000.0}, {"Y_RF_VPP": 21.0}):
        invalid = parameters | bad
        response = client.post(f"{BASE}/preflight", json={"parameters": invalid})
        assert response.status_code == 200 and not response.json()["ok"], response.text
        assert response.json()["errors"]
        assert client.post(f"{BASE}/runs", json={"parameters": invalid}).status_code == 422
    assert client.get(f"{BASE}/sessions/active").json() is None
    assert client.get("/api/jobs").json() == []
    assert not data_root(main.ROOT).exists()


def _history_run(name, *, patch=None):
    directory = data_root(main.ROOT) / run_format.DATA_TYPE / name
    directory.mkdir(parents=True)
    params = RfPhaseSensitivityParams(run_tag=name)
    config = run_format.initial_metadata(params) | {
        "timestamp": "2026-11-23T10:00:00", "run_tag": name, "completion_status": "completed",
        "schema_version": params.schema_version, "parameters": params.to_external(),
    } | (patch or {})
    path = directory / "experiment_config.yaml"
    path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    return path


def test_history_lists_only_own_format_and_serves_existing_readonly_artifacts(client):
    archived = _history_run("history")
    wrong_version = _history_run("wrong_version", patch={"raw_format_version": run_format.RAW_FORMAT_VERSION + 1})
    _history_run("other_experiment", patch={"experiment_id": "rf-phase-response", "data_type": "rf_phase_response"})
    _history_run("running", patch={"completion_status": "running"})
    _history_run("unknown", patch={"completion_status": "unknown"})
    summary_name = f"results/{run_format.SUMMARY}"
    artifact = archived.parent / summary_name
    artifact.parent.mkdir()
    artifact.write_bytes(b'{"diagnostic": "synthetic"}')
    wrong_artifact = wrong_version.parent / summary_name
    wrong_artifact.parent.mkdir()
    shutil.copy2(artifact, wrong_artifact)
    before = archived.read_bytes(), artifact.read_bytes()

    history = client.get(f"{BASE}/runs?limit=20&offset=0")
    assert history.status_code == 200, history.text
    assert {item["run_id"] for item in history.json()["runs"]} == {"history", "running", "unknown"}
    assert history.json()["total"] == 3
    generic = client.get("/api/runs", params={"experiment_id": ID})
    assert generic.status_code == 200, generic.text
    assert {item["id"]: item["can_analyze"] for item in generic.json()["runs"]} == {
        "history": True, "running": False, "unknown": False,
    }
    response = client.get(f"{BASE}/runs/history/summary")
    assert response.status_code == 200, response.text
    summary = response.json()
    assert summary["parameters"] == RfPhaseSensitivityParams(run_tag="history").to_external()
    assert set(summary["artifacts"]) == {summary_name}
    response = client.get(summary["artifacts"][summary_name])
    assert response.status_code == 200 and response.content == before[1], response.text
    assert client.get(f"{BASE}/runs/wrong_version/summary").status_code == 422
    assert client.get(f"{BASE}/runs/wrong_version/files/{summary_name}").status_code == 422
    assert client.get(f"{BASE}/runs/other_experiment/summary").status_code == 422
    blocked = client.get(f"{BASE}/runs/running/analysis")
    assert blocked.status_code == 409 and blocked.headers["X-Error-Code"] == "run_busy"
    assert client.get(f"{BASE}/runs/unknown/analysis").status_code == 422
    assert client.get(f"{BASE}/runs/missing/summary").status_code == 404
    assert (archived.read_bytes(), artifact.read_bytes()) == before


def test_session_dispatch_dependencies_and_cancel_wait_for_child(client, monkeypatch):
    waiting, completed, cleaning, release = Event(), Event(), Event(), Event()
    stage_ids = ["A01", "A02", "A03", "A04", "A05"]

    def execute(module, *, parameters, commands, cancellation, progress):
        assert module.endswith(".rf_phase_sensitivity.session")
        assert parameters["RUN_TAG"] == "stub_session"
        progress(ProgressEvent("waiting", "替身会话等待", data={"workflow_session": {"status": "waiting"}}))
        waiting.set()
        command = commands.get(timeout=10)
        assert command["action"] == "steps" and command["step_ids"] == stage_ids
        progress(ProgressEvent("waiting", "替身命令已完成", data={
            "workflow_session": {"revision": 1, "steps": {
                stage: {"status": "completed", "error": ""} for stage in stage_ids
            } | {"A06": {"status": "pending", "error": ""}, "A07": {"status": "pending", "error": ""}}},
            "command_result": {"request_id": command["request_id"], "error": None},
        }))
        completed.set()
        assert cancellation.wait(10), "没有收到取消标记"
        cleaning.set()
        assert release.wait(10), "测试未释放会话收尾屏障"
        progress(ProgressEvent("finish", "替身会话已收尾", data={
            "workflow_session": {"status": "closed", "safety_shutdown": {"completed": True, "errors": []}},
        }))
        cancellation.raise_if_cancelled()

    monkeypatch.setattr(ADAPTER, "_execute", execute)
    try:
        response = client.post(f"{BASE}/sessions", json={"parameters": {"RUN_TAG": "stub_session"}})
        assert response.status_code == 200, response.text
        session_id, job_id = (response.json()[key] for key in ("session_id", "job_id"))
        session_url = f"{BASE}/sessions/{session_id}"
        job_url = f"/api/jobs/{job_id}"
        assert waiting.wait(10), client.get(job_url).json()
        assert client.get(f"{BASE}/sessions/active").json()["session_id"] == session_id
        assert client.get(f"{RESPONSE_BASE}/sessions/active").json() is None
        response = client.post(f"{session_url}/steps", json={"revision": 0, "step_ids": ["A03"]})
        assert response.status_code == 422 and "A02" in response.json()["detail"], response.text
        response = client.post(f"{session_url}/steps", json={"revision": 0, "step_ids": stage_ids})
        assert response.status_code == 200 and response.json()["pending_request"], response.text
        assert completed.wait(10), client.get(job_url).json()
        current = client.get(session_url).json()
        assert current["steps"]["A03"]["status"] == "completed" and current["pending_request"] is None
        assert client.post(f"{session_url}/finish", json={"revision": 0}).status_code == 409
        response = client.post(f"{job_url}/cancel")
        assert response.status_code == 200, response.text
        assert cleaning.wait(10), client.get(job_url).json()
        assert main.manager.hardware_lock.locked()
    finally:
        release.set()

    main.manager.wait_for_shutdown()
    assert client.get(job_url).json()["status"] == "cancelled"
    final = client.get(session_url).json()
    assert final["status"] == "closed" and final["safety_shutdown"]["completed"]
    assert not main.manager.hardware_lock.locked()
