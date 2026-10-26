"""全局退出的封口、队列与真实线程等待；全部使用假任务和确定性屏障。"""

from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock, Thread, current_thread, get_ident
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import pytest
from fastapi.testclient import TestClient

from . import jobs, main
from .jobs import Job, JobManager, ShutdownInProgressError


WAIT_S = 2


def observed(event):
    assert event.wait(WAIT_S), "假任务未到达约定的屏障"


def queued(manager, count):
    with manager._hardware_condition:
        assert manager._hardware_condition.wait_for(
            lambda: manager.queued_hardware_count() == count, timeout=WAIT_S,
        ), "假任务没有按约定进入硬件队列"


@pytest.fixture
def manager():
    value = JobManager()
    yield value
    value.wait_for_shutdown()


def test_shutdown_cancels_queue_and_holds_hardware_until_running_cleanup(manager):
    running, cleaning, release, joining = (Event() for _ in range(4))
    queued_runner = Mock()
    saved = []

    def run(job):
        running.set()
        assert job.cancellation.wait(WAIT_S)
        cleaning.set()
        observed(release)
        saved.append("已保存部分数据")
        job.cancellation.raise_if_cancelled()

    first = manager.create("experiment:demo", run)
    observed(running)
    second = manager.create("session:other", queued_runner)
    queued(manager, 1)
    worker = next(worker for worker, job in manager._workers.items() if job is first)
    original_join = worker.join

    def join(*args, **kwargs):
        joining.set()
        return original_join(*args, **kwargs)

    with ThreadPoolExecutor(max_workers=1) as pool, patch.object(worker, "join", side_effect=join):
        try:
            manager.begin_shutdown()
            manager.begin_shutdown()
            drain = pool.submit(manager.wait_for_shutdown)
            observed(cleaning)
            observed(joining)
            with manager._hardware_condition:
                assert manager._hardware_condition.wait_for(
                    lambda: second.status == "cancelled", timeout=WAIT_S,
                )
            assert not drain.done()
            assert first.status == "running"
            assert manager.hardware_lock.locked()
            queued_runner.assert_not_called()
        finally:
            release.set()
        drain.result(timeout=WAIT_S)
    assert first.status == "cancelled"
    assert saved == ["已保存部分数据"]
    assert not manager.hardware_lock.locked()
    manager.wait_for_shutdown()


@pytest.mark.parametrize("hardware_required", [True, False])
def test_shutdown_cancels_accepted_work_before_it_reaches_runner(manager, hardware_required):
    accepted, release = Event(), Event()
    run = Mock()
    original_run = manager._run

    def before_run(*args):
        accepted.set()
        observed(release)
        original_run(*args)

    with patch.object(manager, "_run", side_effect=before_run):
        try:
            job = manager.create("analysis:demo", run, hardware_required=hardware_required)
            observed(accepted)
            assert job.status == "queued"
            assert manager.queued_hardware_count() == 0
            manager.begin_shutdown()
        finally:
            release.set()
        manager.wait_for_shutdown()
    run.assert_not_called()
    assert job.status == "cancelled"
    assert not manager.hardware_lock.locked()


def test_shutdown_between_hardware_handoff_and_running_never_invokes_runner(manager):
    acquired, release = Event(), Event()
    run = Mock()
    original_wait = manager._wait_for_hardware

    def handoff(job):
        result = original_wait(job)
        acquired.set()
        observed(release)
        return result

    with patch.object(manager, "_wait_for_hardware", side_effect=handoff):
        try:
            job = manager.create("experiment:demo", run)
            observed(acquired)
            assert manager.hardware_lock.locked()
            manager.begin_shutdown()
        finally:
            release.set()
        manager.wait_for_shutdown()
    run.assert_not_called()
    assert job.status == "cancelled"
    assert not manager.hardware_lock.locked()


def test_shutdown_waits_for_analysis_and_uninterruptible_tool_without_forcing(manager):
    analysis_started, tool_started, release_analysis, release_tool = (Event() for _ in range(4))
    joined = Event()

    def analysis(job):
        analysis_started.set()
        observed(release_analysis)
        job.cancellation.raise_if_cancelled()

    def tool(job):
        tool_started.set()
        observed(release_tool)
        return {"readback": "finished"}

    analysis_job = manager.create("analysis:demo", analysis, hardware_required=False)
    tool_job = manager.create("clock-sync", tool)
    observed(analysis_started)
    observed(tool_started)
    worker = next(worker for worker, job in manager._workers.items() if job is analysis_job)
    original_join = worker.join

    def join(*args, **kwargs):
        joined.set()
        return original_join(*args, **kwargs)

    with ThreadPoolExecutor(max_workers=1) as pool, patch.object(worker, "join", side_effect=join):
        try:
            drain = pool.submit(manager.wait_for_shutdown)
            observed(joined)
            assert analysis_job.cancellation.cancelled
            assert tool_job.cancellation.cancelled
            assert not drain.done()
            release_analysis.set()
            with manager._hardware_condition:
                assert manager._hardware_condition.wait_for(
                    lambda: analysis_job.status == "cancelled", timeout=WAIT_S,
                )
            assert not drain.done()
            assert manager.hardware_lock.locked()
        finally:
            release_analysis.set()
            release_tool.set()
        drain.result(timeout=WAIT_S)
    assert tool_job.status == "completed"
    assert tool_job.result == {"readback": "finished"}


def test_shutdown_waits_for_terminal_pruned_job_thread_to_really_return():
    manager = JobManager(max_terminal_jobs=0)
    finished, release, joining = Event(), Event(), Event()
    original_run = manager._run

    def after_run(*args):
        original_run(*args)
        finished.set()
        observed(release)

    with patch.object(manager, "_run", side_effect=after_run):
        job = manager.create("analysis:demo", lambda job: None, hardware_required=False)
        observed(finished)
        assert job.status == "completed"
        assert manager.jobs == {}
        worker = next(iter(manager._workers))
        original_join = worker.join

        def join(*args, **kwargs):
            joining.set()
            return original_join(*args, **kwargs)

        with ThreadPoolExecutor(max_workers=1) as pool, patch.object(worker, "join", side_effect=join):
            try:
                drain = pool.submit(manager.wait_for_shutdown)
                observed(joining)
                assert not drain.done()
            finally:
                release.set()
            drain.result(timeout=WAIT_S)
    assert not worker.is_alive()


def test_shutdown_waits_for_already_started_short_hardware_operation(manager):
    waiting = Event()
    original_wait = manager._hardware_condition.wait
    assert manager.try_acquire_short_hardware()

    def wait(*args, **kwargs):
        waiting.set()
        return original_wait(*args, **kwargs)

    with ThreadPoolExecutor(max_workers=1) as pool, patch.object(manager._hardware_condition, "wait", side_effect=wait):
        try:
            drain = pool.submit(manager.wait_for_shutdown)
            observed(waiting)
            assert not drain.done()
            with pytest.raises(ShutdownInProgressError):
                manager.try_acquire_short_hardware()
        finally:
            manager.hardware_lock.release()
        drain.result(timeout=WAIT_S)


def test_shutdown_wakes_short_operation_waiter_without_lock_order_deadlock(manager):
    running, release, short_waiting = Event(), Event(), Event()

    def run(job):
        running.set()
        observed(release)

    manager.create("experiment:running", run)
    observed(running)
    manager.create("experiment:queued", Mock())
    queued(manager, 1)
    original_wait = manager._hardware_condition.wait
    short_thread = []

    def wait(*args, **kwargs):
        if short_thread and current_thread() is short_thread[0]:
            short_waiting.set()
        return original_wait(*args, **kwargs)

    def short():
        short_thread.append(current_thread())
        return manager.try_acquire_short_hardware(timeout=10)

    with ThreadPoolExecutor(max_workers=1) as pool, patch.object(manager._hardware_condition, "wait", side_effect=wait):
        try:
            future = pool.submit(short)
            observed(short_waiting)
            manager.begin_shutdown()
            with pytest.raises(ShutdownInProgressError):
                future.result(timeout=WAIT_S)
        finally:
            release.set()
    manager.wait_for_shutdown()


def test_analysis_admission_under_jobs_lock_remains_reentrant(manager):
    run = Mock()
    with manager.jobs_lock:
        job = manager.create("analysis:demo", run, hardware_required=False)
        manager.begin_shutdown()
    manager.wait_for_shutdown()
    assert job.status == "cancelled"
    run.assert_not_called()


def test_failed_thread_start_does_not_leave_an_undrainable_job(manager):
    class FailedThread(Thread):
        def start(self):
            raise RuntimeError("线程启动失败")

    with patch.object(jobs, "Thread", FailedThread), pytest.raises(RuntimeError, match="线程启动失败"):
        manager.create("analysis:demo", Mock(), hardware_required=False)
    assert manager.jobs == {}
    assert manager._workers == {}
    manager.wait_for_shutdown()


def test_warning_threshold_only_reports_and_never_ends_wait(manager, caplog):
    clock = [0.0]
    alive = [True]
    worker = Mock(spec=Thread)
    worker.is_alive.side_effect = lambda: alive[0]
    job = Job(id="waiting", kind="experiment:cleanup", status="running")
    manager.jobs[job.id] = job
    manager._workers[worker] = job

    def join(timeout):
        assert job.cancellation.cancelled
        clock[0] += jobs.SHUTDOWN_WARNING_INTERVAL_S + 0.1
        if worker.join.call_count == 3:
            alive[0] = False
            job.status = "cancelled"

    worker.join.side_effect = join
    with patch.object(jobs, "time", SimpleNamespace(monotonic=lambda: clock[0])):
        manager.wait_for_shutdown()
    assert worker.join.call_args_list == [call(timeout=jobs.SHUTDOWN_POLL_INTERVAL_S)] * 3
    assert len(caplog.records) == 2
    assert all("不会强行退出" in record.message for record in caplog.records)
    assert all("experiment:cleanup" in record.message for record in caplog.records)


def test_fifo_and_short_hardware_fairness_remain_unchanged(manager):
    running, release = Event(), Event()
    order = []

    def first(job):
        order.append("first")
        running.set()
        observed(release)

    one = manager.create("first", first)
    observed(running)
    two = manager.create("second", lambda job: order.append("second"))
    queued(manager, 1)
    three = manager.create("third", lambda job: order.append("third"))
    queued(manager, 2)
    try:
        assert not manager.try_acquire_short_hardware()
    finally:
        release.set()
    with manager._hardware_condition:
        assert manager._hardware_condition.wait_for(
            lambda: all(job.status == "completed" for job in (one, two, three)), timeout=WAIT_S,
        )
    assert order == ["first", "second", "third"]


def test_multiple_shutdown_waiters_drain_the_same_real_thread(manager):
    running, release, both_joining = Event(), Event(), Event()
    joined_by, lock = set(), Lock()

    def run(job):
        running.set()
        observed(release)

    job = manager.create("experiment:demo", run)
    observed(running)
    worker = next(worker for worker, item in manager._workers.items() if item is job)
    original_join = worker.join

    def join(*args, **kwargs):
        with lock:
            joined_by.add(get_ident())
            if len(joined_by) == 2:
                both_joining.set()
        return original_join(*args, **kwargs)

    with ThreadPoolExecutor(max_workers=2) as pool, patch.object(worker, "join", side_effect=join):
        try:
            waits = [pool.submit(manager.wait_for_shutdown) for _ in range(2)]
            observed(both_joining)
            assert all(not wait.done() for wait in waits)
            assert manager.hardware_lock.locked()
        finally:
            release.set()
        for wait in waits:
            wait.result(timeout=WAIT_S)
    assert not worker.is_alive()
    assert not manager.hardware_lock.locked()


def test_shutdown_reports_original_failure_without_claiming_instrument_safety(manager, caplog):
    running, release = Event(), Event()
    failure = ValueError("设备收尾原始错误")

    def run(job):
        running.set()
        assert job.cancellation.wait(WAIT_S)
        observed(release)
        raise failure

    job = manager.create("experiment:demo", run)
    observed(running)
    try:
        manager.begin_shutdown()
        assert manager.hardware_lock.locked()
    finally:
        release.set()
    manager.wait_for_shutdown()
    assert job.status == "failed"
    assert job.error == str(failure)
    assert job.message == str(failure)
    records = [record for record in caplog.records if record.levelname == "ERROR"]
    assert len(records) == 1
    assert records[0].exc_info[1] is failure
    assert "未确认仪器安全" in records[0].message
    assert job.id in records[0].message
    assert not manager.hardware_lock.locked()


def test_shutdown_api_rejects_new_work_but_keeps_read_only_job_queries(manager):
    manager.begin_shutdown()
    with patch.object(main, "manager", manager), TestClient(main.app) as client, \
            patch.object(main, "read_device") as read, patch.object(main, "save_device_library") as save:
        for path, payload in (
            ("/api/clocks/sync", None),
            ("/api/devices/demo/refresh", None),
            ("/api/device-library", {"base_revision": "draft", "devices": {}}),
        ):
            response = client.put(path, json=payload) if payload is not None else client.post(path)
            assert response.status_code == 503, response.text
            assert response.headers["X-Error-Code"] == "shutting_down"
            assert isinstance(response.json()["detail"], str)
        assert client.get("/api/jobs").status_code == 200
        read.assert_not_called()
        save.assert_not_called()
    for hardware_required in (True, False):
        with pytest.raises(ShutdownInProgressError):
            manager.create("rejected", Mock(), hardware_required=hardware_required)
    assert not manager.begin_restart()
    assert manager.jobs == {}
