"""后端一键重启门闩、接口和启动入口的无硬件回归。"""

from __future__ import annotations

import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request

from . import __main__ as backend_entry
from . import main
from .jobs import Job, JobManager, RestartInProgressError


def request_for(app) -> Request:
    return Request({
        "type": "http",
        "method": "POST",
        "path": "/api/restart",
        "raw_path": b"/api/restart",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 1234),
        "server": ("127.0.0.1", 8000),
        "scheme": "http",
        "root_path": "",
        "app": app,
    })


class JobManagerRestartTests(unittest.TestCase):
    def test_idle_restart_latches_and_blocks_new_work(self):
        manager = JobManager()

        self.assertTrue(manager.begin_restart())
        with self.assertRaises(RestartInProgressError):
            manager.create("analysis:demo", lambda job: None, hardware_required=False)
        self.assertFalse(manager.try_acquire_short_hardware())
        self.assertFalse(manager.begin_restart())

    def test_active_job_blocks_restart(self):
        manager = JobManager()
        with manager.jobs_lock:
            manager.jobs["active"] = Job(id="active", kind="analysis:demo", status="running")

        self.assertFalse(manager.begin_restart())

    def test_hardware_lock_or_queue_blocks_restart(self):
        manager = JobManager()
        manager.hardware_lock.acquire()
        try:
            self.assertFalse(manager.begin_restart())
        finally:
            manager.hardware_lock.release()

        with manager._hardware_condition:
            manager._hardware_queue.append(Job(id="queued", kind="experiment:demo"))
        try:
            self.assertFalse(manager.begin_restart())
        finally:
            with manager._hardware_condition:
                manager._hardware_queue.clear()


class RestartApiTests(unittest.TestCase):
    def test_idle_restart_requests_server_shutdown(self):
        manager = JobManager()
        server = SimpleNamespace(should_exit=False)
        app = SimpleNamespace(state=SimpleNamespace(uvicorn_server=server))

        with patch.object(main, "manager", manager):
            response = main.restart_backend(request_for(app))

        self.assertEqual(response.status, "restarting")
        self.assertTrue(server.should_exit)
        self.assertTrue(app.state.restart_requested)

    def test_restart_requires_supported_server_entrypoint(self):
        app = SimpleNamespace(state=SimpleNamespace())

        with self.assertRaises(HTTPException) as raised:
            main.restart_backend(request_for(app))

        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(raised.exception.headers["X-Error-Code"], "restart_unavailable")

    def test_restart_rejects_active_jobs(self):
        manager = JobManager()
        with manager.jobs_lock:
            manager.jobs["active"] = Job(id="active", kind="analysis:demo", status="running")
        server = SimpleNamespace(should_exit=False)
        app = SimpleNamespace(state=SimpleNamespace(uvicorn_server=server))

        with patch.object(main, "manager", manager):
            with self.assertRaises(HTTPException) as raised:
                main.restart_backend(request_for(app))

        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.headers["X-Error-Code"], "restart_busy")
        self.assertFalse(server.should_exit)


class BackendEntrypointTests(unittest.TestCase):
    def test_requested_restart_reexecs_same_backend_command(self):
        def run_server():
            backend_entry.app.state.restart_requested = True

        with patch.object(backend_entry, "Server") as server_factory, \
                patch.object(backend_entry.os, "execv", side_effect=SystemExit) as execv:
            server_factory.return_value.run.side_effect = run_server
            with self.assertRaises(SystemExit):
                backend_entry.main()

        execv.assert_called_once_with(sys.executable, [sys.executable, "-m", "backend"])


if __name__ == "__main__":
    unittest.main()
