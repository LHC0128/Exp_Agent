"""后端一键重启门闩、接口和启动入口的无硬件回归。"""

from __future__ import annotations

import sys
import tempfile
import threading
import unittest
from pathlib import Path
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

    def test_restart_arguments_keep_launcher_flags(self):
        options = backend_entry._parse_options([
            "--log-file", "backend.log",
            "--stop-file", "backend.stop",
            "--parent-pid", "42",
        ])

        self.assertEqual(
            backend_entry._restart_arguments(options),
            [
                "--log-file", "backend.log",
                "--stop-file", "backend.stop",
                "--parent-pid", "42",
            ],
        )


class ExitWatchTests(unittest.TestCase):
    def test_stop_file_requests_server_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            stop_file = Path(tmp) / "backend.stop"
            server = SimpleNamespace(should_exit=False)
            application = SimpleNamespace(state=SimpleNamespace(uvicorn_server=server))
            stop_requested = threading.Event()
            thread = threading.Thread(
                target=backend_entry._watch_exit,
                args=(application, stop_file, None, stop_requested),
            )

            thread.start()
            stop_file.touch()
            thread.join(3)

        self.assertFalse(thread.is_alive())
        self.assertTrue(stop_requested.is_set())
        self.assertTrue(server.should_exit)

    def test_dead_launcher_requests_server_exit(self):
        server = SimpleNamespace(should_exit=False)
        application = SimpleNamespace(state=SimpleNamespace(uvicorn_server=server))
        stop_requested = threading.Event()
        thread = threading.Thread(
            target=backend_entry._watch_exit,
            args=(application, None, 0, stop_requested),
        )

        thread.start()
        thread.join(3)

        self.assertFalse(thread.is_alive())
        self.assertTrue(stop_requested.is_set())
        self.assertTrue(server.should_exit)

    def test_log_file_redirects_stdout_and_stderr(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_file = Path(tmp) / "logs" / "backend.log"
            original_out, original_err = sys.stdout, sys.stderr
            stream = None
            try:
                backend_entry._redirect_output(log_file)
                stream = sys.stdout
                print("stdout line")
                print("stderr line", file=sys.stderr)
            finally:
                sys.stdout, sys.stderr = original_out, original_err
                if stream is not None:
                    stream.close()

            text = log_file.read_text(encoding="utf-8")

        self.assertIn("backend start pid=", text)
        self.assertIn("stdout line", text)
        self.assertIn("stderr line", text)


if __name__ == "__main__":
    unittest.main()
