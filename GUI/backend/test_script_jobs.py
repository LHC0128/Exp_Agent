"""GUI 代跑脚本：路径校验、硬件队列互斥、取消收尾与开发目录拒绝；临时 git 仓库加假脚本，不连接硬件。"""

from __future__ import annotations

import subprocess
import sys
import textwrap
import time
from pathlib import Path
from threading import Event
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lab_workflows import script_subprocess

from . import main
from .jobs import JobManager, require_hardware_mode
from .script_jobs import ScriptRejected, create_router, resolve_script, validate_argv

WAIT_S = 20
TERMINAL = {"completed", "failed", "cancelled"}


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), "-c", "user.name=test", "-c", "user.email=test@example.invalid",
                    *args], check=True, capture_output=True)


def _write(root: Path, relative: str, source: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(source), encoding="utf-8")
    return path


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "run"
    root.mkdir()
    _git(root, "init", "-q")
    _write(root, "scripts/demo/echo.py", """
        import sys
        print("argv", *sys.argv[1:], flush=True)
    """)
    _write(root, "scripts/demo/fail.py", "raise SystemExit(3)\n")
    _write(root, "scripts/demo/cleanup.py", """
        import sys, time
        from pathlib import Path
        marker = Path(sys.argv[1])
        started = Path(sys.argv[2])
        try:
            for index in range(600):
                print("acquire", index, flush=True)
                time.sleep(0.05)
        finally:
            started.write_text("1", encoding="utf-8")
            steps = []
            for step in range(3):
                steps.append(f"step{step}")
                time.sleep(0.2)
            marker.write_text("\\n".join(steps + ["done"]), encoding="utf-8")
    """)
    _write(root, "scripts/demo/client.py", "import subprocess\nsubprocess.run(['python', '-m', 'tools.lab_client', 'status'])\n")
    _write(root, "scripts/demo/modified.py", "print('v1')\n")
    _write(root, "experiments/outside.py", "print('outside')\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    _write(root, "scripts/demo/modified.py", "print('v2')\n")
    _write(root, "scripts/demo/untracked.py", "print('new')\n")
    real_popen = subprocess.Popen

    def popen(command, *args, **kwargs):
        # 临时仓库没有 agent_exp_env：只把 script_runner 子进程改用当前解释器，git 等调用原样。
        if isinstance(command, list) and "lab_workflows.script_runner" in command:
            command = [sys.executable, *command[1:]]
        return real_popen(command, *args, **kwargs)

    monkeypatch.setattr(script_subprocess.subprocess, "Popen", popen)
    return root


@pytest.fixture
def manager():
    value = JobManager()
    yield value
    value.wait_for_shutdown()


@pytest.fixture
def client(repo: Path, manager: JobManager) -> TestClient:
    app = FastAPI()
    app.include_router(create_router(manager, lambda: repo))
    return TestClient(app)


def finished(manager: JobManager, job_id: str) -> str:
    deadline = time.monotonic() + WAIT_S
    while manager.get(job_id).status not in TERMINAL:
        assert time.monotonic() < deadline, "脚本任务未结束"
        time.sleep(0.02)
    return manager.get(job_id).status


def wait_until(condition, message: str) -> None:
    deadline = time.monotonic() + WAIT_S
    while not condition():
        assert time.monotonic() < deadline, message
        time.sleep(0.02)


@pytest.mark.parametrize("path, reason", [
    ("", "相对仓库根"),
    ("/scripts/demo/echo.py", "相对仓库根"),
    ("scripts\\demo\\echo.py", "相对仓库根"),
    ("C:/scripts/demo/echo.py", "相对仓库根"),
    ("scripts/../experiments/outside.py", ". 或 .."),
    ("scripts/./demo/echo.py", ". 或 .."),
    ("scripts//demo/echo.py", ". 或 .."),
    ("experiments/outside.py", "scripts/ 下"),
    ("scripts/demo/echo.txt", "scripts/ 下"),
    ("scripts/demo/missing.py", "不存在"),
    ("scripts/demo/untracked.py", "未在当前版本"),
    ("scripts/demo/modified.py", "不一致"),
    ("scripts/demo/client.py", "客户端脚本"),
])
def test_resolve_script_rejects_unreleased_or_outside_paths(repo: Path, path: str, reason: str) -> None:
    with pytest.raises(ScriptRejected, match=reason):
        resolve_script(repo, path)


def test_resolve_script_accepts_tracked_unmodified_script(repo: Path) -> None:
    script = resolve_script(repo, "scripts/demo/echo.py")
    assert script.path == (repo / "scripts/demo/echo.py").resolve()
    assert script.commit


def test_validate_argv_limits() -> None:
    assert validate_argv(["--vpp", "0.2"]) == ["--vpp", "0.2"]
    with pytest.raises(ScriptRejected, match="超过"):
        validate_argv(["x"] * 257)
    with pytest.raises(ScriptRejected, match="空字符"):
        validate_argv(["a\x00b"])


def test_preflight_reports_resolution_without_queueing(client: TestClient, manager: JobManager) -> None:
    response = client.post("/api/scripts/preflight", json={"path": "scripts/demo/echo.py", "argv": ["a"]})
    assert response.status_code == 200
    body = response.json()
    assert body["path"] == "scripts/demo/echo.py" and body["argv"] == ["a"] and body["commit"]
    assert manager.jobs == {}
    rejected = client.post("/api/scripts/preflight", json={"path": "scripts/demo/untracked.py"})
    assert rejected.status_code == 400 and "未在当前版本" in rejected.json()["detail"]


def test_run_completes_with_stdout_events_result_and_log(client: TestClient, manager: JobManager) -> None:
    response = client.post("/api/scripts/runs", json={"path": "scripts/demo/echo.py", "argv": ["--vpp", "0.2"]})
    assert response.status_code == 200
    job = response.json()
    assert job["kind"] == "script:scripts/demo/echo.py"
    assert finished(manager, job["id"]) == "completed"
    detail = manager.get(job["id"]).public()
    messages = [event["message"] for event in detail["events"]]
    assert "argv --vpp 0.2" in messages
    result = detail["result"]
    assert result["exit_code"] == 0 and result["argv"] == ["--vpp", "0.2"]
    assert "argv --vpp 0.2" in Path(result["log_path"]).read_text(encoding="utf-8")
    assert not manager.hardware_lock.locked()


def test_nonzero_exit_fails_with_exit_code(client: TestClient, manager: JobManager) -> None:
    job = client.post("/api/scripts/runs", json={"path": "scripts/demo/fail.py"}).json()
    assert finished(manager, job["id"]) == "failed"
    assert "退出码 3" in manager.get(job["id"]).error


def test_cancel_holds_hardware_lock_until_script_cleanup_finishes(
    client: TestClient, manager: JobManager, repo: Path,
) -> None:
    marker, started = repo / "marker.txt", repo / "started.txt"
    job = client.post("/api/scripts/runs", json={
        "path": "scripts/demo/cleanup.py", "argv": [str(marker), str(started)],
    }).json()
    wait_until(lambda: any(e["message"].startswith("acquire") for e in manager.get(job["id"]).events),
               "脚本未开始采集")
    manager.cancel(job["id"])
    wait_until(started.exists, "取消后脚本未进入 finally")
    # 收尾期间任务仍在运行、硬件锁仍被持有。
    assert manager.get(job["id"]).status == "running"
    assert manager.hardware_lock.locked()
    assert finished(manager, job["id"]) == "cancelled"
    assert marker.read_text(encoding="utf-8").splitlines() == ["step0", "step1", "step2", "done"]
    assert not manager.hardware_lock.locked()


def test_script_waits_in_fifo_queue_behind_running_hardware_job(
    client: TestClient, manager: JobManager,
) -> None:
    holding, release = Event(), Event()

    def experiment(job):
        holding.set()
        assert release.wait(WAIT_S)
        return "ok"

    first = manager.create("experiment:fake", experiment)
    assert holding.wait(WAIT_S)
    job = client.post("/api/scripts/runs", json={"path": "scripts/demo/echo.py"}).json()
    wait_until(lambda: manager.queued_hardware_count() == 1, "脚本未进入硬件队列")
    assert manager.get(job["id"]).status == "queued"
    release.set()
    assert finished(manager, first.id) == "completed"
    assert finished(manager, job["id"]) == "completed"


def test_dev_backend_rejects_script_requests_before_queueing() -> None:
    previous = main.manager.hardware_policy
    main.manager.hardware_policy = require_hardware_mode
    try:
        with patch("hardware_gate.denial_reason", return_value="测试：开发目录"):
            api = TestClient(main.app)
            before = set(main.manager.jobs)
            for endpoint in ("/api/scripts/preflight", "/api/scripts/runs"):
                response = api.post(endpoint, json={"path": "scripts/demo/echo.py"})
                assert response.status_code == 409
                assert response.headers["X-Error-Code"] == "dev_hardware_disabled"
            assert set(main.manager.jobs) == before
    finally:
        main.manager.hardware_policy = previous
