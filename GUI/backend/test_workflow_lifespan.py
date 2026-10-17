"""流程会话在后台退出时先发送结束命令，再等待安全收尾。"""

import asyncio
from contextlib import ExitStack
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from . import main
from .workflow_sessions import WorkflowSessionService, XYSession, XYStageState


def test_analysis_artifact_signatures_accept_legacy_integers_and_content_hashes():
    files = {"old.npz": [123, 456, 1, 255], "content.json": [321, 654, "a" * 64]}
    trace = dict(input_fingerprint={"raw_files": {"waveform.npz": "b" * 64}},
        parameter_fingerprint="c" * 64, parameters={"LINEAR_R_SQUARED_MIN": .99}, revision=3)
    state = XYStageState(status="completed", files=files, **trace).model_dump()
    assert state["files"] == files
    assert all(state[name] == value for name, value in trace.items())


def test_session_preserves_drift_progress_and_keeps_other_workflows_compatible():
    base = dict(session_id="session", job_id="job", revision=0, status="waiting",
        parameters={}, steps={})
    records = [{"cycle_index": 0, "status": "completed", "run_dir": "child"}]
    assert XYSession.model_validate(base | {"drift_records": records}).drift_records == records
    assert XYSession.model_validate(base).drift_records == []


def test_workflow_quality_metadata_reaches_registered_routes_without_hardware():
    client = TestClient(main.app)
    for service in main._workflow_session_services():
        response = client.get(f"/api/experiments/{service.adapter.id}/workflow")
        assert response.status_code == 200, service.adapter.id
        flow = response.json()
        for stage in [*flow["acquisition"], *flow["analysis"]]:
            assert isinstance(stage["quality_gates"], list)
            assert all(isinstance(gate, str) and gate.strip() for gate in stage["quality_gates"])
    response = client.get("/api/experiments/z-coil-inductance-frequency-response/workflow").json()
    assert "不足四个有效频率点" in response["analysis"][1]["quality_gates"][0]


@pytest.mark.parametrize("body_fails", [False, True])
def test_all_registered_workflow_sessions_close_before_shutdown(body_fails):
    services = main._workflow_session_services()
    registered = tuple(
        value for value in vars(main).values() if isinstance(value, WorkflowSessionService)
    )
    assert set(services) == set(registered)
    assert main.probe_am_sessions in services
    events = []

    async def run():
        async with main.lifespan(main.app):
            if body_fails:
                raise RuntimeError("后台退出")

    with ExitStack() as stack:
        for index, service in enumerate(services):
            stack.enter_context(patch.object(
                service, "close_communication", side_effect=lambda i=index: events.append(("close", i)),
            ))
            stack.enter_context(patch.object(
                service, "shutdown", side_effect=lambda i=index: events.append(("shutdown", i)),
            ))
        if body_fails:
            with pytest.raises(RuntimeError, match="后台退出"):
                asyncio.run(run())
        else:
            asyncio.run(run())

    assert events == [
        *(("close", index) for index in range(len(services))),
        *(("shutdown", index) for index in range(len(services))),
    ]
