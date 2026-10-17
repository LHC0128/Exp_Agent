"""本实验真实子进程会话：全局队列、修订、刷新和安全退出。"""
import json
import os
from pathlib import Path
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
import yaml

from GUI.backend.jobs import JobManager
from GUI.backend.rf_sensitivity_aw_known_noise import RFKnownNoiseSessionService
from GUI.backend.workflow_sessions import create_router
from .definition import ADAPTER
from .models import RFSensitivityKnownNoiseParams
from . import workflow
from .test_workflow import known_rig, rig, control_source, assert_shutdown


BASE = "/api/experiments/rf-sensitivity-aw-known-noise"


def wait_for(predicate):
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.02)
    raise AssertionError("已知噪声假仪器子进程超时")


@pytest.fixture
def session_client(known_rig, monkeypatch):
    manager = JobManager()
    service = RFKnownNoiseSessionService(lambda: known_rig.tmp_path, manager)
    app = FastAPI()
    app.include_router(create_router(service))
    execute = ADAPTER._execute
    monkeypatch.setattr(ADAPTER, "_execute", lambda module, **kwargs: execute(
        "lab_workflows.experiment_modules.rf_sensitivity_aw_known_noise.test_session_acceptance"
        if module.endswith(".session") else module, **kwargs))
    monkeypatch.setenv("RF_KNOWN_ACCEPTANCE_ROOT", str(known_rig.tmp_path))
    client = TestClient(app)
    yield client, service, manager
    service.close_communication()
    for job in list(manager.jobs.values()):
        if job.status in ("queued", "running"):
            job.cancellation.cancel()
    wait_for(lambda: all(job.status not in ("queued", "running") for job in manager.jobs.values()))
    client.close()


def create_session(session_client, known_rig):
    client, service, _ = session_client
    response = client.post(BASE + "/sessions", json={"parameters": known_rig.params.to_external()})
    assert response.status_code == 200, response.text
    session_id = response.json()["session_id"]
    wait_for(lambda: service.get(session_id)["status"] in ("waiting", "closed"))
    assert service.get(session_id)["status"] == "waiting", service.get(session_id)
    return session_id


def test_session_real_child_queue_revision_refresh_apply_and_finish(session_client, known_rig):
    client, service, manager = session_client
    session_id = create_session(session_client, known_rig)
    path = BASE + "/sessions/" + session_id
    assert manager.hardware_lock.locked()
    probe = manager.create("acceptance-hardware-probe", lambda _: {"done": True})
    assert probe.status == "queued"
    assert client.post(path + "/steps", json={"revision": 0, "step_ids": ["A13"]}).status_code == 422
    assert client.post(path + "/steps", json={"revision": "0", "step_ids": ["A01"]}).status_code == 422
    assert client.post(path + "/steps", json={"revision": 0, "step_ids": ["A01"], "extra": 1}).status_code == 422
    response = client.post(path + "/steps", json={"revision": 0, "step_ids": [f"A{i:02d}" for i in range(1, 11)]})
    assert response.status_code == 200, response.text
    assert client.post(path + "/steps", json={"revision": 0, "step_ids": ["A01"]}).status_code == 409
    wait_for(lambda: service.get(session_id).get("pending_request") is None)
    state = service.get(session_id)
    assert state["status"] == "waiting" and state["steps"]["A10"]["status"] == "completed", state
    assert client.get(BASE + "/sessions/active").json()["session_id"] == session_id
    assert probe.status == "queued"
    assert client.post(path + "/steps", json={"revision": 0, "step_ids": ["A01"]}).headers["X-Error-Code"] == "revision_conflict"
    run = known_rig.tmp_path / "data/RF_Field_Sensitivity_AW_Known_Noise" / state["run_id"]
    before = (run / "experiment_config.yaml").read_bytes()
    assert client.get(BASE + f"/runs/{run.name}/analysis").status_code == 409
    applied = client.patch(path + "/parameters", json={"revision": state["revision"], "parameters": {"NOISE_N_AVG": 3}})
    assert applied.status_code == 200, applied.text
    wait_for(lambda: service.get(session_id).get("pending_request") is None)
    state = service.get(session_id)
    assert state["parameters"]["NOISE_N_AVG"] == 3
    assert yaml.safe_load((run / "experiment_config.yaml").read_bytes())["parameters"] == yaml.safe_load(before)["parameters"]
    assert client.post(path + "/finish", json={"revision": state["revision"]}).status_code == 200
    wait_for(lambda: service.get(session_id)["status"] == "closed" and probe.status == "completed")
    assert service.get(session_id)["safety_shutdown"]["completed"]
    assert not manager.hardware_lock.locked()
    assert service.active() is None
    assert RFKnownNoiseSessionService(lambda: known_rig.tmp_path, manager).active() is None


@pytest.mark.parametrize("ending", ["eof", "cancel", "shutdown"])
def test_real_child_eof_cancel_shutdown_before_hardware_unlock(session_client, known_rig, ending):
    client, service, manager = session_client
    session_id = create_session(session_client, known_rig)
    path = BASE + "/sessions/" + session_id
    response = client.post(path + "/steps", json={"revision": 0, "step_ids": [f"A{i:02d}" for i in range(1, 11)]})
    assert response.status_code == 200, response.text
    wait_for(lambda: service.get(session_id).get("pending_request") is None)
    state = service.get(session_id)
    assert state["steps"]["A10"]["status"] == "completed", state
    job = manager.get(state["job_id"])
    probe = manager.create("acceptance-queued-after-known-noise", lambda _: {"closed": service.get(session_id)["status"],
        "shutdown": service.get(session_id).get("safety_shutdown")})
    if ending == "eof":
        service.close_communication()
    elif ending == "cancel":
        manager.cancel(job.id)
    else:
        service.shutdown()
    wait_for(lambda: job.status in ("completed", "cancelled", "failed") and probe.status == "completed")
    assert probe.result["closed"] == "closed"
    assert probe.result["shutdown"]["completed"]
    state = service.get(session_id)
    assert state["steps"]["A16"]["status"] == "completed"
    assert state["outputs"]["X"] == state["outputs"]["Y"] == state["outputs"]["Z"] == "OFF"
    assert state["outputs"]["温控"] == "ON"
    assert job.status == ("cancelled" if ending == "cancel" else "completed"), job.error


def fake_session_main():
    from .session import main
    from ..rf_sensitivity_direct_aw import workflow as shared
    root = Path(os.environ["RF_KNOWN_ACCEPTANCE_ROOT"])
    report = shared.report_runtime_progress
    with pytest.MonkeyPatch.context() as patch:
        source = control_source.__wrapped__(root, patch)
        devices = known_rig.__wrapped__(rig.__wrapped__(root, patch, source), patch)
        values = json.loads(os.environ["LAB_TYPED_PARAMETERS"])
        values.pop("session_id")
        devices.params = RFSensitivityKnownNoiseParams.from_external(values)
        def progress(stage, *args, **kwargs):
            if stage == "shutdown":
                devices.in_shutdown = True
            report(stage, *args, **kwargs)
        patch.setattr(shared, "report_runtime_progress", progress)
        main()
        if devices.events:
            assert_shutdown(devices)


if __name__ == "__main__":
    fake_session_main()
