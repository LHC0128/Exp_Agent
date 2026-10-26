"""流程会话在后台退出时先发送结束命令，再等待安全收尾。"""

import asyncio
from contextlib import ExitStack
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

from . import main
from .jobs import JobManager, ShutdownInProgressError
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
    runtime = SimpleNamespace(
        begin_shutdown=lambda: events.append(("begin", -1)),
        wait_for_shutdown=lambda: events.append(("wait", -1)),
    )

    async def run():
        async with main.lifespan(main.app):
            if body_fails:
                raise RuntimeError("后台退出")

    with ExitStack() as stack:
        stack.enter_context(patch.object(main, "manager", runtime))
        old_shutdowns = []
        for index, service in enumerate(services):
            stack.enter_context(patch.object(
                service, "close_communication", side_effect=lambda i=index: events.append(("close", i)),
            ))
            old_shutdowns.append(stack.enter_context(patch.object(service, "shutdown")))
        if body_fails:
            with pytest.raises(RuntimeError, match="后台退出"):
                asyncio.run(run())
        else:
            asyncio.run(run())
        for shutdown in old_shutdowns:
            shutdown.assert_not_called()

    assert events == [
        ("begin", -1),
        *(("close", index) for index in range(len(services))),
        ("wait", -1),
    ]


def test_lifespan_cancellation_does_not_skip_global_cleanup_or_start_queued_work():
    manager = JobManager()
    running, cleaning, release = Event(), Event(), Event()
    other_runner = Mock()
    events = []

    def run(job):
        running.set()
        assert job.cancellation.wait(2)
        cleaning.set()
        assert release.wait(2), "测试必须释放收尾屏障"
        job.cancellation.raise_if_cancelled()

    first = manager.create("experiment:demo", run)
    assert running.wait(2)
    second = manager.create("clock-sync", other_runner)
    with manager._hardware_condition:
        assert manager._hardware_condition.wait_for(
            lambda: manager.queued_hardware_count() == 1, timeout=2,
        )

    def close():
        with pytest.raises(ShutdownInProgressError):
            manager.create("analysis:late", Mock(), hardware_required=False)
        events.append("close")

    async def cycle():
        async def run_lifespan():
            async with main.lifespan(main.app):
                pass

        task = asyncio.create_task(run_lifespan())
        try:
            assert await asyncio.to_thread(cleaning.wait, 2)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done(), "异步取消不能跳过真实收尾"
            assert manager.hardware_lock.locked()
            other_runner.assert_not_called()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)

    try:
        with patch.object(main, "manager", manager), patch.object(
            main, "_workflow_session_services", return_value=(SimpleNamespace(close_communication=close),),
        ):
            asyncio.run(cycle())
    finally:
        release.set()
        manager.wait_for_shutdown()
    assert first.status == "cancelled"
    assert second.status == "cancelled"
    assert events == ["close"]
    assert not manager.hardware_lock.locked()


def test_lifespan_still_drains_global_tasks_when_closing_communication_fails():
    manager = JobManager()
    failure = OSError("关闭通信原始错误")
    service = SimpleNamespace(close_communication=Mock(side_effect=failure))

    async def cycle():
        async with main.lifespan(main.app):
            pass

    with patch.object(main, "manager", manager), patch.object(
        main, "_workflow_session_services", return_value=(service,),
    ), patch.object(manager, "wait_for_shutdown", wraps=manager.wait_for_shutdown) as wait:
        with pytest.raises(OSError) as caught:
            asyncio.run(cycle())
        assert caught.value is failure
        wait.assert_called_once_with()
        with pytest.raises(ShutdownInProgressError):
            manager.create("analysis:late", Mock(), hardware_required=False)
