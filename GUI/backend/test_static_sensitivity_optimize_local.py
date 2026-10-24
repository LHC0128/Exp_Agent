"""真实会话子进程、硬件锁、只读历史和同目录分析互斥。"""
from pathlib import Path
from threading import Event, Thread
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lab_workflows.common import find_project_root
from lab_workflows.experiment_modules.static_sensitivity_optimize_local.definition import ADAPTER
from lab_workflows.experiment_modules.static_sensitivity_optimize_local.test_analysis import synthetic_batch, legacy_batch
from lab_workflows.experiment_modules.static_sensitivity_optimize_local.test_workflow import small_params
from .jobs import JobManager
from .static_sensitivity_optimize_local import StaticLocalSessionService, create_router

ID = "static-sensitivity-optimize-local"
BASE = "/api/experiments/" + ID


def wait(predicate):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if predicate(): return
        time.sleep(.025)
    raise AssertionError("假仪器子进程未完成")


@pytest.fixture
def client(tmp_path, monkeypatch):
    source = find_project_root()
    (tmp_path / "params").mkdir()
    for name in ("devices.yaml", "mapping.yaml", "safety_limits.yaml"):
        (tmp_path / "params" / name).write_bytes((source / "params" / name).read_bytes())
    diagram = "lab_workflows/experiment_modules/static_sensitivity_optimize_local/workflow.excalidraw"
    target = tmp_path / diagram
    target.parent.mkdir(parents=True)
    target.write_bytes((source / diagram).read_bytes())
    manager = JobManager()
    service = StaticLocalSessionService(lambda: tmp_path, manager)
    execute = ADAPTER._execute
    def run(module, **kwargs):
        if module.endswith(".session"):
            module = "lab_workflows.experiment_modules.static_sensitivity_optimize_local.test_workflow"
        return execute(module, **kwargs)
    monkeypatch.setattr(ADAPTER, "_execute", run)
    monkeypatch.setenv("STATIC_LOCAL_TEST_ROOT", str(tmp_path))
    monkeypatch.setenv("STATIC_LOCAL_TEST_SESSION", "1")
    app = FastAPI()
    app.include_router(create_router(service))
    with TestClient(app) as http:
        http.service, http.manager, http.root = service, manager, tmp_path
        yield http
    (tmp_path / "release_shutdown").touch()
    service.shutdown()
    wait(lambda: all(job.status not in ("queued", "running") for job in manager.jobs.values()))


def create(client):
    response = client.post(BASE + "/sessions", json={"parameters": small_params().to_external()})
    assert response.status_code == 200, response.text
    sid = response.json()["session_id"]
    wait(lambda: client.service.get(sid)["status"] in ("waiting", "closed"))
    assert client.service.get(sid)["status"] == "waiting", client.service.get(sid)
    return sid


def steps(client, sid, selected):
    response = client.post(BASE + f"/sessions/{sid}/steps", json=dict(revision=client.service.get(sid)["revision"], step_ids=selected))
    assert response.status_code == 200, response.text
    wait(lambda: client.service.get(sid)["pending_request"] is None)
    value = client.service.get(sid)
    assert value["error"] is None, value
    return value


def test_real_nested_child_keeps_parent_identity_revision_and_hardware_lock(client):
    flow = client.get(BASE + "/workflow").json()
    assert len(flow["acquisition"]) == 6 and len(flow["analysis"]) == 3
    assert client.get(BASE + "/workflow/diagram").json()["type"] == "excalidraw"
    sid = create(client)
    assert client.manager.hardware_lock.locked()
    assert client.post(BASE + "/sessions", json={"parameters": small_params().to_external()}).status_code == 409
    assert client.post(BASE + f"/sessions/{sid}/steps", json=dict(revision=0, step_ids=["A04"])).status_code == 422
    state = steps(client, sid, ["A01", "A02", "A03"])
    assert state["session_id"] == sid and set(state["steps"]) == {f"A{i:02d}" for i in range(1, 7)}
    assert client.get(BASE + "/sessions/active").json()["session_id"] == sid
    assert client.get(BASE + f"/runs/{state['run_id']}/analysis").status_code == 409
    assert client.post(BASE + f"/sessions/{sid}/finish", json=dict(revision=0)).status_code == 409
    assert StaticLocalSessionService(lambda: client.root, client.manager).active() is None
    state = steps(client, sid, ["A04", "A05"])
    assert state["safety_shutdown"]["completed"]
    assert state["outputs"]["Z"] == "OFF" and state["outputs"]["温控"] == "ON"
    assert client.post(BASE + f"/sessions/{sid}/finish", json=dict(revision=state["revision"])).status_code == 200
    wait(lambda: client.service.get(sid)["status"] == "closed")
    wait(lambda: not client.manager.hardware_lock.locked())
    assert client.service.get(sid)["steps"]["A06"]["status"] == "completed"


@pytest.mark.parametrize("method", ["cancel", "eof", "shutdown"])
def test_cancel_eof_shutdown_wait_for_actual_cleanup_before_unlock(client, monkeypatch, method):
    monkeypatch.setenv("STATIC_LOCAL_TEST_BLOCK_SHUTDOWN", "1")
    sid = create(client)
    state = steps(client, sid, ["A01", "A02", "A03"])
    thread = None
    if method == "cancel": client.manager.get(state["job_id"]).cancellation.cancel()
    elif method == "eof": client.service.close_communication()
    else:
        thread = Thread(target=client.service.shutdown)
        thread.start()
    try:
        wait(lambda: (client.root / "shutdown_waiting").exists())
        assert client.manager.hardware_lock.locked()
    finally:
        (client.root / "release_shutdown").touch()
        if thread: thread.join(30)
    wait(lambda: client.service.get(sid)["status"] == "closed")
    wait(lambda: not client.manager.hardware_lock.locked())
    assert client.service.get(sid)["safety_shutdown"]["completed"]


def test_history_missing_results_legacy_readonly_identity_and_analysis_lock(client, monkeypatch):
    base = client.root / "data" / ADAPTER.data_type
    parent = synthetic_batch(base / "1024_local")
    legacy = legacy_batch(base / "0709_0951_opt_local")
    original = {path: path.read_bytes() for path in base.rglob("*") if path.is_file()}
    listing = client.get(BASE + "/runs?limit=50&offset=0")
    assert listing.status_code == 200 and listing.json()["total"] == 2
    summary = client.get(BASE + f"/runs/{parent.name}/summary").json()
    assert summary["parameters"]["RAMP_LOW"] == -.1 and summary["analysis_status"] == "not_started"
    assert summary["artifacts"] == {}
    old = client.get(BASE + f"/runs/{legacy.name}/summary").json()
    assert old["optimization_summary"]["source"] == "legacy_existing_summary"
    assert "HF2_DEMOD_RATE" not in old["parameters"]
    assert isinstance(old["parameters"]["OPTIMIZE_SEQUENCE"], str)
    child = next(path for path in base.iterdir() if path.name.endswith("_point"))
    assert client.get(BASE + f"/runs/{child.name}/summary").status_code == 422
    assert client.get(BASE + "/runs/missing/summary").status_code == 404
    assert client.get(BASE + "/runs/..%2Fescape/summary").status_code == 404
    state = client.get(BASE + f"/runs/{parent.name}/analysis").json()
    assert all(path.read_bytes() == content for path, content in original.items())
    execute, entered, release = ADAPTER._execute, Event(), Event()
    def blocked(module, **kwargs):
        if module == ADAPTER.analysis_module:
            entered.set(); assert release.wait(30)
        return execute(module, **kwargs)
    monkeypatch.setattr(ADAPTER, "_execute", blocked)
    body = dict(revision=state["revision"], step_ids=["B01", "B02", "B03"], analysis_parameters={})
    try:
        response = client.post(BASE + f"/runs/{parent.name}/analysis", json=body)
        assert response.status_code == 200, response.text
        jid = response.json()["id"]
        assert entered.wait(5)
        assert client.post(BASE + f"/runs/{parent.name}/analysis", json=body).status_code == 409
    finally:
        release.set()
    wait(lambda: client.manager.get(jid).status in ("completed", "failed"))
    assert client.manager.get(jid).status == "completed", client.manager.get(jid).error
    assert client.get(BASE + f"/runs/{parent.name}/analysis").json()["options"] == {}
    result = client.get(BASE + f"/runs/{parent.name}/summary").json()
    assert result["optimization_summary"]["best_run"]["iteration"] == 1
    assert result["artifacts"]
    assert all(path.read_bytes() == value for path, value in original.items())


def test_actual_main_route_definition_and_old_reanalysis_contract(tmp_path, monkeypatch):
    from . import main
    parent = synthetic_batch(tmp_path / "data" / ADAPTER.data_type / "1024_local")
    monkeypatch.setattr(main, "ROOT", tmp_path)
    monkeypatch.setattr(main.static_local_sessions, "root", lambda: tmp_path)
    with TestClient(main.app) as client:
        info = client.get(BASE).json()
        assert info["execution_mode"] == "typed_workflow" and "直流逐点" in info["description"]
        assert client.get(BASE + "/workflow").status_code == 200
        assert client.get(BASE + f"/runs/{parent.name}/summary").status_code == 200
        with patch_analyze(main):
            response = client.post(f"/api/runs/{parent.name}/analyze", json={"experiment_id": ID})
            assert response.status_code == 200, response.text
            wait(lambda: main.manager.get(response.json()["id"]).status in ("completed", "failed"))


def test_complete_adapter_returns_parent_not_dc_child(client, monkeypatch):
    original = ADAPTER._execute
    def execute(module, **kwargs):
        if module == ADAPTER.acquisition_module:
            module = "lab_workflows.experiment_modules.static_sensitivity_optimize_local.test_workflow"
        return original(module, **kwargs)
    monkeypatch.setattr(ADAPTER, "_execute", execute)
    monkeypatch.setattr(ADAPTER, "root", client.root)
    monkeypatch.setattr(ADAPTER, "defaults_path", client.root / "params/experiments/static-sensitivity-optimize-local.yaml")
    import subprocess
    import sys
    popen = subprocess.Popen
    def launch(command, **kwargs):
        # 与生产适配器一样从源码根启动；仅运行数据和假仪器配置位于临时根。
        kwargs["cwd"] = Path(__file__).resolve().parents[2]
        return popen([sys.executable, *command[1:]], **kwargs)
    monkeypatch.setattr(subprocess, "Popen", launch)
    result = ADAPTER.run(small_params().to_external(), None, None)
    parent = Path(result["run_dir"])
    import yaml
    assert yaml.safe_load((parent / "experiment_config.yaml").read_text(encoding="utf-8"))["experiment_id"] == ID
    assert (parent / "raw/optimization_manifest.json").is_file()
    assert (parent / "results/optimization_local_summary.png").is_file()


def patch_analyze(main):
    from unittest.mock import patch
    from lab_workflows.experiment_modules.static_sensitivity_optimize_local.definition import DEFINITION
    return patch.object(DEFINITION, "analysis_runner", return_value={"artifacts": []})
