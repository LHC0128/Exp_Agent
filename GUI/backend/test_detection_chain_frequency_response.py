"""探测链路频率响应标定会话 API：真实子进程配合假仪器，不访问实验硬件。"""

import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
import yaml

from lab_workflows.experiment_modules.detection_chain_frequency_response.definition import ADAPTER
from lab_workflows.experiment_modules.detection_chain_frequency_response.test_workflow import (
    copy_config, scan_params,
)
from .jobs import JobManager
from .detection_chain_frequency_response import (
    DetectionChainFrequencyResponseSessionService, create_router,
)

BASE = "/api/experiments/detection-chain-frequency-response"
FAKE_MODULE = "lab_workflows.experiment_modules.detection_chain_frequency_response.test_workflow"


class DetectionChainSessionAPITest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="dcfr-api-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        copy_config(self.root)
        self.manager = JobManager()
        self.service = DetectionChainFrequencyResponseSessionService(lambda: self.root, self.manager)
        app = FastAPI()
        app.include_router(create_router(self.service))
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.original_execute = ADAPTER._execute
        self.patch = patch.object(ADAPTER, "_execute", side_effect=lambda module, **args:
            self.original_execute(FAKE_MODULE if module.endswith(".session") else module, **args))
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.environment = patch.dict(os.environ, {"DCFR_TEST_ROOT": str(self.root)})
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
        deadline = time.monotonic() + 60
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

    def test_workflow_exposes_stages_and_parameter_impacts_without_jobs(self):
        response = self.client.get(f"{BASE}/workflow")
        self.assertEqual(response.status_code, 200, response.text)
        flow = response.json()
        self.assertEqual([stage["id"] for stage in flow["acquisition"]], [f"A{i:02d}" for i in range(1, 9)])
        self.assertEqual([stage["id"] for stage in flow["analysis"]], ["B01", "B02", "B03"])
        self.assertEqual(flow["diagram_url"], f"{BASE}/workflow/diagram")
        self.assertEqual(flow["parameter_impacts"]["acquisition"]["FIXED_PARAMS.temperature"],
                         ["A04", "A05", "A06", "A07"])
        self.assertEqual(flow["parameter_impacts"]["acquisition"]["HF2_OSC_FREQ_HZ"],
                         ["A05", "A06", "A07"])
        self.assertEqual(flow["parameter_impacts"]["analysis"], {})
        self.assertFalse(self.manager.jobs)

    def test_definition_refresh_conflicts_and_waiting_holds_global_lock(self):
        session_id = self.create()
        self.assertTrue(self.manager.hardware_lock.locked())
        self.assertEqual(self.client.get(f"{BASE}/sessions/active").json()["session_id"], session_id)
        self.assertIsNone(DetectionChainFrequencyResponseSessionService(lambda: self.root, self.manager).active())
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
        scan = self.client.post(f"{BASE}/sessions/{session_id}/steps", json={
            "revision": state["revision"], "step_ids": ["A03", "A04", "A05", "A06", "A07"]})
        self.assertEqual(scan.status_code, 200, scan.text)
        self.wait(lambda: self.service.get(session_id).get("pending_request") is None)
        state = self.client.get(f"{BASE}/sessions/{session_id}").json()
        self.assertEqual(state["steps"]["A07"]["status"], "completed")
        run_dir = self.root / "data/Detection_Chain_Frequency_Response" / state["run_id"]
        self.assertTrue((run_dir / "raw/frequency_response.npz").is_file())
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
            self.assertEqual(state["steps"]["A08"]["status"], "completed")
            self.assertEqual(state["outputs"]["AOM 载波"], "OFF")
            self.assertFalse(self.manager.hardware_lock.locked())
            self.assertEqual(self.manager.get(job_id).status, "cancelled" if cancel else "completed")

    def test_staged_analysis_strict_requests_and_state_reuse(self):
        run = self.root / "data/Detection_Chain_Frequency_Response/0101_1200_fake"
        (run / "raw").mkdir(parents=True)
        (run / "experiment_config.yaml").write_text(yaml.safe_dump(dict(
            experiment_id="detection-chain-frequency-response",
            completion_status="completed",
            parameters=scan_params().to_external())), encoding="utf-8")
        state = self.client.get(f"{BASE}/runs/{run.name}/analysis").json()
        self.assertEqual(state["steps"]["B01"]["status"], "pending")
        self.assertEqual(state["options"], {})
        self.assertEqual(self.client.post(f"{BASE}/runs/{run.name}/analysis",
            json={"revision": 0, "step_ids": ["B02"]}).status_code, 422)
        self.assertEqual(self.client.post(f"{BASE}/runs/{run.name}/analysis",
            json={"revision": "0", "step_ids": ["B01"]}).status_code, 422)
        self.assertEqual(self.client.post(f"{BASE}/runs/{run.name}/analysis",
            json={"revision": 0, "step_ids": ["B01"], "extra": 1}).status_code, 422)
        response = self.client.post(f"{BASE}/runs/{run.name}/analysis",
            json={"revision": 0, "step_ids": ["B01"]})
        self.assertEqual(response.status_code, 200, response.text)
        self.wait(lambda: self.manager.get(response.json()["id"]).status == "completed")
        refreshed = self.client.get(f"{BASE}/runs/{run.name}/analysis").json()
        self.assertEqual(refreshed["steps"]["B01"]["status"], "completed")
        self.assertEqual(refreshed["summary"]["status"], "partial")
        self.assertTrue((run / "results/analysis_summary.json").is_file())

    def test_history_uses_run_snapshot_and_reanalysis_without_hardware(self):
        from . import main

        run = self.root / "data/Detection_Chain_Frequency_Response/0101_1200_history"
        run.mkdir(parents=True)
        config_path = run / "experiment_config.yaml"
        parameters = scan_params().to_external()
        config_path.write_text(yaml.safe_dump(dict(experiment_id="detection-chain-frequency-response",
            run_tag=parameters["RUN_TAG"], completion_status="completed",
            parameters=parameters)), encoding="utf-8")
        client = TestClient(main.app)
        self.addCleanup(client.close)
        with patch.object(main, "ROOT", self.root), patch.object(main, "manager", self.manager):
            listing = client.get(f"{BASE}/runs?limit=50&offset=0").json()
            self.assertEqual(listing["total"], 1)
            self.assertEqual(listing["runs"][0]["run_tag"], parameters["RUN_TAG"])
            summary = client.get(f"{BASE}/runs/{run.name}/summary").json()
            self.assertEqual(summary["parameters"], parameters)
            self.assertEqual(summary["artifacts"], {})
            self.assertEqual(summary["analysis_status"], "not_started")
            self.assertEqual(client.get(f"{BASE}/runs/missing/summary").status_code, 404)
            self.assertFalse(self.manager.jobs)
            (run / "results").mkdir()
            (run / "results/frequency_response.png").write_bytes(b"fake png")
            summary = client.get(f"{BASE}/runs/{run.name}/summary").json()
            self.assertEqual(set(summary["artifacts"]), {"frequency_response.png"})
            with patch.object(ADAPTER, "_execute", return_value=None) as execute:
                response = client.post(f"/api/runs/{run.name}/analyze",
                    json={"experiment_id": "detection-chain-frequency-response"})
                self.assertEqual(response.status_code, 200, response.text)
                self.wait(lambda: self.manager.get(response.json()["id"]).status == "completed")
                execute.assert_called_once()
            self.assertEqual(client.get(f"{BASE}/runs/{run.name}/summary").json()["analysis_status"], "completed")


if __name__ == "__main__":
    unittest.main()
