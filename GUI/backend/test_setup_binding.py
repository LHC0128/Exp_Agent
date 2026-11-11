"""任务接纳绑定、请求线程预检与空闲装置切换的无硬件回归。"""

from queue import Queue
from threading import Event, get_ident
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
import yaml
from fastapi import HTTPException

from lab_workflows.instrument_config import resolve_mapping
from lab_workflows.setups import (
    SETUP_ENV_VAR, SetupConfigError, bound_setup, bound_setup_id, capture_setup,
    create_setup, effective_setup, load_setup_registry, set_active_setup,
)
from . import __main__ as backend_entry
from . import jobs, main
from .jobs import Job, JobManager
from .workflow_sessions import WorkflowSessionService


WAIT_S = 2


@pytest.fixture(autouse=True)
def isolated_setup_environment(monkeypatch):
    monkeypatch.delenv(SETUP_ENV_VAR, raising=False)


@pytest.fixture
def two_setups(tmp_path):
    params = tmp_path / "params"
    params.mkdir()
    (params / "devices.yaml").write_text("devices: {}\n", encoding="utf-8")

    def mapping(resource):
        return {"mapping": {"Z_magnetic_field": {
            "instrument": "signal_generator", "model": "DG4000", "resource": resource, "channel": 1,
        }}}

    (params / "mapping.yaml").write_text(yaml.safe_dump(mapping("FAKE_A")), encoding="utf-8")
    create_setup("磁力仪 B", copy_from="default", setup_id="mag_b",
        base_revision=load_setup_registry(tmp_path).revision, root=tmp_path)
    (params / "setups" / "mag_b" / "mapping.yaml").write_text(
        yaml.safe_dump(mapping("FAKE_B")), encoding="utf-8")
    return tmp_path


def configuration_bytes(root):
    return {path: path.read_bytes() for path in (root / "params").rglob("*.yaml")}


def wait_for_job(manager, job):
    with manager._hardware_condition:
        assert manager._hardware_condition.wait_for(
            lambda: job.status in jobs.TERMINAL_STATUSES, timeout=WAIT_S,
        ), "假任务未到达终态"


@pytest.mark.parametrize("hardware_required", [True, False])
def test_accepted_job_keeps_captured_setup_after_active_changes(two_setups, hardware_required):
    root = two_setups
    manager = JobManager(setup_resolver=lambda: capture_setup(root).id)

    def run(job):
        return {"setup_id": effective_setup(root).id, "binding": bound_setup_id(),
            "resource": resolve_mapping(root)["Z_magnetic_field"]["resource"]}

    try:
        # 保持任务尚未进入 runner，明确在接纳后、执行前切换 active。
        with manager.jobs_lock:
            job = manager.create("fake", run, hardware_required=hardware_required)
            assert job.setup_id == "default"
            set_active_setup("mag_b", base_revision=load_setup_registry(root).revision, root=root)
        wait_for_job(manager, job)
        assert job.status == "completed", job.error
        assert job.result == {"setup_id": "default", "binding": "default", "resource": "FAKE_A"}
        assert job.public()["setup_id"] == "default"
        assert job.summary()["setup_id"] == "default"
        assert effective_setup(root).id == "mag_b"
        assert bound_setup_id() is None
    finally:
        manager.wait_for_shutdown()


def test_resolver_error_prevents_admission_registration_and_thread_creation():
    failure = SetupConfigError("装置登记损坏")
    manager = JobManager()

    def resolve():
        assert not manager.jobs_lock._is_owned(), "解析必须先于接纳锁"
        raise failure

    manager.setup_resolver = resolve
    runner = Mock()
    with patch.object(jobs, "Thread") as thread, pytest.raises(SetupConfigError) as caught:
        manager.create("fake", runner)
    assert caught.value is failure
    thread.assert_not_called()
    runner.assert_not_called()
    assert manager.jobs == {} and manager._workers == {}
    assert manager.queued_hardware_count() == 0
    assert not manager.hardware_lock.locked()


@pytest.mark.parametrize("options", [{}, {"setup_id": None}])
def test_manager_without_resolver_keeps_unbound_behavior(options):
    manager = JobManager()
    try:
        with patch.object(jobs, "bound_setup", side_effect=AssertionError("空 ID 不应绑定")):
            job = manager.create("fake", lambda job: bound_setup_id(), **options)
            wait_for_job(manager, job)
        assert job.status == "completed", job.error
        assert job.setup_id is None and job.result is None
        assert job.public()["setup_id"] is None
        assert job.summary()["setup_id"] is None
    finally:
        manager.wait_for_shutdown()


def test_explicit_setup_overrides_resolver(two_setups):
    resolver = Mock(side_effect=AssertionError("显式装置不应调用 resolver"))
    manager = JobManager(setup_resolver=resolver)
    try:
        job = manager.create("fake", lambda job: effective_setup(two_setups).id, setup_id="mag_b")
        wait_for_job(manager, job)
        assert job.status == "completed", job.error
        assert job.result == job.setup_id == "mag_b"
        resolver.assert_not_called()
    finally:
        manager.wait_for_shutdown()


def test_switch_refuses_while_hardware_task_holds_lock(two_setups, monkeypatch):
    manager = JobManager()
    monkeypatch.setattr(main, "manager", manager)
    monkeypatch.setattr(main, "ROOT", two_setups)
    started, release = Event(), Event()

    def run(job):
        started.set()
        assert release.wait(WAIT_S), "测试必须释放假任务屏障"

    before = configuration_bytes(two_setups)
    try:
        job = manager.create("fake-hardware", run)
        assert started.wait(WAIT_S)
        assert manager.hardware_lock.locked()
        with pytest.raises(HTTPException) as caught:
            main._switch_active_setup("mag_b", load_setup_registry(two_setups).revision)
        assert caught.value.status_code == 409
        assert caught.value.headers["X-Error-Code"] == "hardware_busy"
        assert manager.hardware_lock.locked()
        assert configuration_bytes(two_setups) == before
    finally:
        release.set()
        manager.wait_for_shutdown()
    assert job.status == "completed"
    assert not manager.hardware_lock.locked()


def test_switch_refuses_queued_hardware_even_when_lock_is_free(two_setups, monkeypatch):
    manager = JobManager()
    monkeypatch.setattr(main, "manager", manager)
    monkeypatch.setattr(main, "ROOT", two_setups)
    queued = Job(id="queued", kind="fake-hardware")
    with manager._hardware_condition:
        manager._hardware_queue.append(queued)
    before = configuration_bytes(two_setups)
    try:
        assert not manager.hardware_lock.locked()
        with pytest.raises(HTTPException) as caught:
            main._switch_active_setup("mag_b", load_setup_registry(two_setups).revision)
        assert caught.value.status_code == 409
        assert caught.value.headers["X-Error-Code"] == "hardware_busy"
        assert not manager.hardware_lock.locked()
        assert list(manager._hardware_queue) == [queued]
        assert configuration_bytes(two_setups) == before
    finally:
        with manager._hardware_condition:
            manager._hardware_queue.clear()
        manager.wait_for_shutdown()


def test_switch_succeeds_when_idle_without_changing_mappings(two_setups, monkeypatch):
    manager = JobManager()
    monkeypatch.setattr(main, "manager", manager)
    monkeypatch.setattr(main, "ROOT", two_setups)
    before = configuration_bytes(two_setups)
    result = main._switch_active_setup("mag_b", load_setup_registry(two_setups).revision)
    assert result == load_setup_registry(two_setups).public()
    assert result["active"] == "mag_b"
    after = configuration_bytes(two_setups)
    registry_path = two_setups / "params" / "setups.yaml"
    assert after[registry_path] != before[registry_path]
    assert {path: data for path, data in after.items() if path != registry_path} == {
        path: data for path, data in before.items() if path != registry_path}
    assert not manager.hardware_lock.locked()


@pytest.mark.parametrize("setup_id, expected", [("default", "default"), (None, "mag_b")])
@pytest.mark.parametrize("preflight_fails", [False, True])
def test_parameter_preflight_uses_session_job_setup_on_request_thread(
    two_setups, setup_id, expected, preflight_fails,
):
    root = two_setups
    set_active_setup("mag_b", base_revision=load_setup_registry(root).revision, root=root)
    manager = JobManager()
    job = Job(id="session-job", kind="fake-session", status="running", setup_id=setup_id)
    manager.jobs[job.id] = job
    request_thread = get_ident()
    seen = []

    def preflight(parameters, project_root):
        assert get_ident() == request_thread
        assert service.lock._is_owned()
        assert not manager.jobs_lock._is_owned()
        assert parameters == {"value": 2} and project_root == root
        seen.append((bound_setup_id(), effective_setup(root).id,
            resolve_mapping(root)["Z_magnetic_field"]["resource"]))
        if preflight_fails:
            raise ValueError("预检原始错误")

    adapter = SimpleNamespace(params_type=SimpleNamespace(from_external=lambda values: values))
    service = WorkflowSessionService(lambda: root, manager, adapter=adapter, preflight=preflight,
        stages=None, analysis_workflow=None, session_module="fake", diagram_file="fake")
    state = dict(session_id="session", job_id=job.id, revision=0, status="waiting",
        parameters={"value": 1}, steps={}, pending_request=None)
    queue = Queue()
    service.sessions["session"] = {"state": state, "queue": queue}
    original_get = manager.get

    def get(job_id):
        assert service.lock._is_owned(), "会话锁必须先于任务状态锁"
        return original_get(job_id)

    with patch.object(manager, "get", side_effect=get), bound_setup("mag_b"):
        if preflight_fails:
            with pytest.raises(HTTPException) as caught:
                service.command("session", 0, "parameters", parameters={"value": 2})
            assert caught.value.status_code == 422
            assert "预检原始错误" in str(caught.value.detail)
            assert queue.empty() and state["pending_request"] is None
        else:
            result = service.command("session", 0, "parameters", parameters={"value": 2})
            assert result["pending_request"] == state["pending_request"]
            assert queue.get_nowait()["parameters"] == {"value": 2}
            assert queue.empty()
        assert bound_setup_id() == "mag_b"
    assert seen == [(expected, expected, "FAKE_A" if expected == "default" else "FAKE_B")]
    assert bound_setup_id() is None


def test_global_manager_keeps_single_instance_and_resolves_current_root(two_setups, monkeypatch):
    assert main.manager is jobs.manager is backend_entry.manager
    assert all(service.manager is jobs.manager for service in main._workflow_session_services())
    monkeypatch.setattr(main, "ROOT", two_setups)
    assert main.manager.setup_resolver() == "default"
    set_active_setup("mag_b", base_revision=load_setup_registry(two_setups).revision, root=two_setups)
    assert main.manager.setup_resolver() == "mag_b"
