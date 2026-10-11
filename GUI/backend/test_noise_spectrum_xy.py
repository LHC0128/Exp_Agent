"""会话 API：真实子进程配合假仪器，不访问实验硬件。"""

import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
import yaml

from lab_workflows.experiment_modules.noise_spectrum_xy.definition import ADAPTER
from lab_workflows.experiment_modules.noise_spectrum_xy.test_sine_mode import copy_config, scan_params
from .jobs import JobManager
from .noise_spectrum_xy import XYSessionService, create_router

BASE = "/api/experiments/noise-spectrum-xy"
FAKE_MODULE = "lab_workflows.experiment_modules.noise_spectrum_xy.test_stages"


class XYSessionAPITest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="xy-api-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        copy_config(self.root)
        self.manager = JobManager()
        self.service = XYSessionService(lambda: self.root, self.manager)
        app = FastAPI()
        app.include_router(create_router(self.service))
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.original_execute = ADAPTER._execute
        self.patch = patch.object(ADAPTER, "_execute", side_effect=lambda module, **args:
            self.original_execute(FAKE_MODULE if module.endswith(".session") else module, **args))
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.environment = patch.dict(os.environ, {"XY_TEST_ROOT": str(self.root)})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.addCleanup(self.stop_sessions)

    def stop_sessions(self):
        self.service.close_communication()
        for job in list(self.manager.jobs.values()):
            if job.status in ("queued", "running"):
                job.cancellation.cancel()
        self.wait(lambda: all(job.status not in ("queued", "running") for job in self.manager.jobs.values()))

    def wait(self, predicate):
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.02)
        self.fail("假仪器子进程超时")

    def create(self):
        response = self.client.post(f"{BASE}/sessions", json={"parameters": scan_params().to_external()})
        self.assertEqual(response.status_code, 200, response.text)
        value = response.json()
        self.wait(lambda: self.service.get(value["session_id"])["status"] == "waiting")
        return value["session_id"]

    def test_definition_refresh_conflicts_and_waiting_holds_global_lock(self):
        flow = self.client.get(f"{BASE}/workflow").json()
        self.assertEqual([stage["id"] for stage in flow["acquisition"]], [f"A{i:02d}" for i in range(1, 15)])
        session_id = self.create()
        self.assertTrue(self.manager.hardware_lock.locked())
        self.assertEqual(self.client.get(f"{BASE}/sessions/active").json()["session_id"], session_id)
        self.assertIsNone(XYSessionService(lambda: self.root, self.manager).active())
        missing = self.client.post(f"{BASE}/sessions/{session_id}/steps", json={"revision": 0, "step_ids": ["A03"]})
        self.assertEqual(missing.status_code, 422)
        self.assertIn("设备连接", missing.text)
        stale = self.client.post(f"{BASE}/sessions/{session_id}/steps", json={"revision": 1, "step_ids": ["A01"]})
        self.assertEqual(stale.headers["X-Error-Code"], "revision_conflict")
        first = self.client.post(f"{BASE}/sessions/{session_id}/steps", json={"revision": 0, "step_ids": ["A02", "A01"]})
        self.assertEqual(first.status_code, 200, first.text)
        duplicate = self.client.post(f"{BASE}/sessions/{session_id}/steps", json={"revision": 0, "step_ids": ["A01"]})
        self.assertEqual(duplicate.status_code, 409)
        self.wait(lambda: self.service.get(session_id).get("pending_request") is None)
        state = self.client.get(f"{BASE}/sessions/{session_id}").json()
        self.assertEqual(state["steps"]["A02"]["status"], "completed")
        self.assertTrue(self.manager.hardware_lock.locked())
        invalid = self.client.patch(f"{BASE}/sessions/{session_id}/parameters",
            json={"revision": state["revision"], "parameters": {"RUN_TAG": "../x"}})
        self.assertEqual(invalid.status_code, 422)
        finish = self.client.post(f"{BASE}/sessions/{session_id}/finish", json={"revision": state["revision"]})
        self.assertEqual(finish.status_code, 200, finish.text)
        self.wait(lambda: self.manager.get(state["job_id"]).status == "completed")
        self.assertFalse(self.manager.hardware_lock.locked())
        self.assertIsNone(self.service.active())

    def test_communication_eof_and_cancel_finish_before_unlock(self):
        for cancel in (False, True):
            session_id = self.create()
            response = self.client.post(f"{BASE}/sessions/{session_id}/steps",
                json={"revision": 0, "step_ids": ["A01", "A02"]})
            self.assertEqual(response.status_code, 200)
            self.wait(lambda: self.service.get(session_id).get("pending_request") is None)
            job_id = self.service.get(session_id)["job_id"]
            if cancel:
                self.manager.cancel(job_id)
            else:
                self.service.close_communication()
            self.wait(lambda: self.manager.get(job_id).status in ("completed", "cancelled", "failed"))
            state = self.service.get(session_id)
            self.assertEqual(state["steps"]["A14"]["status"], "completed")
            self.assertEqual(state["outputs"]["X"], "OFF")
            self.assertFalse(self.manager.hardware_lock.locked())
            self.assertEqual(self.manager.get(job_id).status, "cancelled" if cancel else "completed")

    def test_analysis_preflight_shared_dedup_and_strict_requests(self):
        run = self.root / "data/Noise_Spectrum_XY_Ctrl/0101_1200_fake"
        (run / "raw").mkdir(parents=True)
        (run / "experiment_config.yaml").write_text(yaml.safe_dump(dict(
            experiment_id="noise-spectrum-xy", completion_status="completed", parameters={})), encoding="utf-8")
        state = self.client.get(f"{BASE}/runs/{run.name}/analysis").json()
        self.assertEqual(state["steps"]["B01"]["status"], "pending")
        self.assertEqual(self.client.post(f"{BASE}/runs/{run.name}/analysis", json={"revision": 0, "step_ids": ["B02"]}).status_code, 422)
        self.assertEqual(self.client.post(f"{BASE}/runs/{run.name}/analysis", json={"revision": "0", "step_ids": ["B01"]}).status_code, 422)
        self.assertEqual(self.client.post(f"{BASE}/runs/{run.name}/analysis", json={"revision": 0, "step_ids": ["B01"], "extra": 1}).status_code, 422)
        from threading import Event
        release = Event()
        with patch.object(ADAPTER, "_execute", side_effect=lambda *a, **kw: release.wait(20)):
            response = self.client.post(f"{BASE}/runs/{run.name}/analysis", json={"revision": 0, "step_ids": ["B01"]})
            self.assertEqual(response.status_code, 200, response.text)
            duplicate = self.client.post(f"{BASE}/runs/{run.name}/analysis", json={"revision": 0, "step_ids": ["B01"]})
            self.assertEqual(duplicate.headers["X-Error-Code"], "analysis_running")
            from . import main
            from fastapi import HTTPException
            with patch.object(main, "ROOT", self.root), patch.object(main, "manager", self.manager), patch.object(main, "xy_sessions", self.service):
                with self.assertRaises(HTTPException) as caught:
                    main.analyze_run(run.name, main.AnalysisBody(experiment_id="noise-spectrum-xy"))
                self.assertEqual(caught.exception.headers["X-Error-Code"], "analysis_running")
            release.set()
            self.wait(lambda: self.manager.get(response.json()["id"]).status == "completed")


if __name__ == "__main__":
    unittest.main()
