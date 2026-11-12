"""开发目录硬件策略：需要硬件的请求在入队/取锁前拒绝；全部用假任务，不连接硬件。"""

import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from . import main
from .jobs import DevHardwareDisabled, JobManager, require_hardware_mode
from .workflow_sessions import WorkflowSessionService


def finished(manager, job):
    deadline = time.monotonic() + 5
    while manager.get(job.id).status not in {"completed", "failed", "cancelled"}:
        assert time.monotonic() < deadline, "假任务未结束"
        time.sleep(0.01)
    return manager.get(job.id).status


DENIED = patch("hardware_gate.denial_reason", return_value="测试：开发目录")
ALLOWED = patch("hardware_gate.denial_reason", return_value=None)


def test_require_hardware_mode_follows_gate():
    with DENIED, pytest.raises(DevHardwareDisabled, match="开发目录不能采集"):
        require_hardware_mode()
    with ALLOWED:
        require_hardware_mode()


def test_policy_rejects_hardware_job_before_registration():
    manager = JobManager()
    manager.hardware_policy = require_hardware_mode
    runner = MagicMock()
    with DENIED, pytest.raises(DevHardwareDisabled):
        manager.create("experiment:x", runner)
    runner.assert_not_called()
    assert manager.jobs == {}
    with DENIED:
        job = manager.create("analysis:x", lambda job: "ok", hardware_required=False)
    assert finished(manager, job) == "completed"


def test_manager_without_policy_keeps_previous_behavior():
    manager = JobManager()
    with DENIED:
        job = manager.create("experiment:x", lambda job: "ok")
    assert finished(manager, job) == "completed"


@pytest.fixture
def client():
    previous = main.manager.hardware_policy
    main.manager.hardware_policy = require_hardware_mode
    try:
        yield TestClient(main.app)
    finally:
        main.manager.hardware_policy = previous


def test_api_rejects_hardware_job_and_short_operation_in_dev(client):
    with DENIED, patch.object(main, "synchronize_clocks") as sync, \
            patch.object(main, "discover_visa_devices") as discover:
        response = client.post("/api/clocks/sync")
        assert response.status_code == 409
        assert response.headers["X-Error-Code"] == "dev_hardware_disabled"
        response = client.post("/api/device-library/discover-visa")
        assert response.status_code == 409
        assert response.headers["X-Error-Code"] == "dev_hardware_disabled"
    sync.assert_not_called()
    discover.assert_not_called()
    assert not main.manager.hardware_lock.locked()


def test_health_reports_runtime_mode(client):
    with DENIED:
        assert client.get("/api/health").json()["runtime_mode"] == "dev"
    with ALLOWED:
        assert client.get("/api/health").json()["runtime_mode"] == "lab"


def test_rejected_workflow_session_is_not_left_registered():
    manager = JobManager()
    manager.hardware_policy = require_hardware_mode
    adapter = MagicMock()
    adapter.defaults.return_value.to_external.return_value = {}
    params = MagicMock()
    params.to_external.return_value = {}
    service = WorkflowSessionService(
        lambda: None, manager, adapter=adapter, preflight=lambda *args: (params,),
        stages=MagicMock(), analysis_workflow=None, session_module="m", diagram_file=None,
    )
    with DENIED, pytest.raises(DevHardwareDisabled):
        service.create({})
    assert service.sessions == {}
    assert service.active() is None


@pytest.mark.parametrize(("allowed", "environ", "expected"), [
    (True, {}, 8000),
    (True, {"LAB_BACKEND_PORT": "8000"}, 8000),
    (False, {}, 8100),
    (False, {"LAB_BACKEND_PORT": "8200"}, 8200),
])
def test_backend_port_by_runtime_mode(allowed, environ, expected):
    from .__main__ import resolve_port

    with patch("hardware_gate.denial_reason", return_value=None if allowed else "开发"):
        assert resolve_port(environ) == expected


@pytest.mark.parametrize(("allowed", "environ", "message"), [
    (True, {"LAB_BACKEND_PORT": "8100"}, "运行目录后端必须使用 8000"),
    (False, {"LAB_BACKEND_PORT": "8000"}, "不能使用运行目录专用"),
    (False, {"LAB_BACKEND_PORT": "80"}, "不是有效端口"),
])
def test_backend_port_rejects_wrong_mode(allowed, environ, message):
    from .__main__ import resolve_port

    with patch("hardware_gate.denial_reason", return_value=None if allowed else "开发"), \
            pytest.raises(SystemExit, match=message):
        resolve_port(environ)
