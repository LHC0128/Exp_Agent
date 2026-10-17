"""探测链路频率响应标定会话 API：真实子进程配合假仪器，不访问实验硬件。"""

import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
import numpy as np
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
            experiment_type="Detection_Chain_Frequency_Response",
            completion_status="completed",
            parameters=scan_params().to_external())), encoding="utf-8")
        np.savez(run / "raw/frequency_response.npz",
                 am_frequencies_hz=[85000.0, 90000.0, 95000.0],
                 peak_amplitudes=[1e-6, np.nan, 1e-6],
                 peak_frequencies_hz=[5000.0, np.nan, 5000.0], actual_rate=12000.0)
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
        self.wait(lambda: self.manager.get(response.json()["id"]).status in ("completed", "cancelled", "failed"))
        self.assertEqual(self.manager.get(response.json()["id"]).status, "completed",
                         self.manager.get(response.json()["id"]).error)
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
            self.assertEqual(client.get(summary["artifacts"]["frequency_response.png"]).content, b"fake png")
            self.assertEqual(client.get(f"{BASE}/runs/invalid%3Arun/summary").status_code, 404)
            foreign = run.with_name("0101_1201_other")
            foreign.mkdir()
            (foreign / "experiment_config.yaml").write_text(yaml.safe_dump(dict(
                experiment_id="static-sensitivity", parameters={"RUN_TAG": "foreign"})), encoding="utf-8")
            self.assertEqual(client.get(f"{BASE}/runs/{foreign.name}/summary").status_code, 404)
            self.assertEqual(client.get(f"{BASE}/runs?limit=50&offset=0").json()["total"], 1)
            with patch.object(ADAPTER, "_execute", return_value=None) as execute:
                response = client.post(f"/api/runs/{run.name}/analyze",
                    json={"experiment_id": "detection-chain-frequency-response"})
                self.assertEqual(response.status_code, 200, response.text)
                self.wait(lambda: self.manager.get(response.json()["id"]).status == "completed")
                execute.assert_called_once()
            self.assertEqual(client.get(f"{BASE}/runs/{run.name}/summary").json()["analysis_status"], "completed")

    def test_parameter_revision_requires_explicit_rerun_and_new_directory(self):
        session_id = self.create()
        self.assertEqual(self.client.post(f"{BASE}/sessions", json={"parameters": {}}).status_code, 409)
        response = self.client.post(f"{BASE}/sessions/{session_id}/steps", json={
            "revision": 0, "step_ids": [f"A{i:02d}" for i in range(1, 8)]})
        self.assertEqual(response.status_code, 200, response.text)
        self.wait(lambda: self.service.get(session_id).get("pending_request") is None)
        original = self.service.get(session_id)
        base = self.root / "data/Detection_Chain_Frequency_Response"
        original_config = (base / original["run_id"] / "experiment_config.yaml").read_bytes()
        response = self.client.patch(f"{BASE}/sessions/{session_id}/parameters", json={
            "revision": original["revision"], "parameters": {"HF2_OSC_FREQ_HZ": 90500.0, "RUN_TAG": "rerun"}})
        self.assertEqual(response.status_code, 200, response.text)
        self.wait(lambda: self.service.get(session_id).get("pending_request") is None)
        changed = self.service.get(session_id)
        self.assertEqual(changed["run_id"], original["run_id"])
        self.assertEqual((base / original["run_id"] / "experiment_config.yaml").read_bytes(), original_config)
        self.assertEqual([changed["steps"][f"A{i:02d}"]["status"] for i in range(5, 8)], ["stale"] * 3)
        self.assertEqual(changed["steps"]["A04"]["status"], "completed")
        refused = self.client.post(f"{BASE}/sessions/{session_id}/steps", json={
            "revision": changed["revision"], "step_ids": ["A07"]})
        self.assertEqual(refused.status_code, 422)
        response = self.client.post(f"{BASE}/sessions/{session_id}/steps", json={
            "revision": changed["revision"], "step_ids": ["A05", "A06", "A07"]})
        self.assertEqual(response.status_code, 200, response.text)
        self.wait(lambda: self.service.get(session_id).get("pending_request") is None)
        rerun = self.service.get(session_id)
        self.assertNotEqual(rerun["run_id"], original["run_id"])
        self.assertEqual((base / original["run_id"] / "experiment_config.yaml").read_bytes(), original_config)
        config = yaml.safe_load((base / rerun["run_id"] / "experiment_config.yaml").read_text(encoding="utf-8"))
        self.assertEqual(config["parameters"]["HF2_OSC_FREQ_HZ"], 90500.0)

    def test_eof_cancel_shutdown_hold_lock_until_real_child_safety_snapshot(self):
        for mode in ("eof", "cancel", "shutdown"):
            with self.subTest(mode=mode):
                session_id = self.create()
                response = self.client.post(f"{BASE}/sessions/{session_id}/steps", json={
                    "revision": 0, "step_ids": [f"A{i:02d}" for i in range(1, 7)]})
                self.assertEqual(response.status_code, 200, response.text)
                self.wait(lambda: self.service.get(session_id).get("pending_request") is None)
                released = []
                original_release = self.manager._release_hardware

                def release():
                    state = self.service.get(session_id)
                    released.append((self.manager.hardware_lock.locked(), state))
                    original_release()

                job_id = self.service.get(session_id)["job_id"]
                with patch.object(self.manager, "_release_hardware", side_effect=release):
                    if mode == "cancel":
                        self.manager.cancel(job_id)
                    elif mode == "shutdown":
                        self.service.shutdown()
                    else:
                        self.service.close_communication()
                    self.wait(lambda: bool(released) and self.manager.get(job_id).status in
                              ("completed", "cancelled", "failed"))
                self.assertEqual(len(released), 1)
                held, snapshot = released[0]
                self.assertTrue(held)
                self.assertEqual(snapshot["steps"]["A08"]["status"], "completed")
                self.assertTrue(snapshot["safety_shutdown"]["completed"])
                self.assertEqual(snapshot["outputs"]["温控"], "ON")
                self.assertEqual(snapshot["outputs"]["AOM 载波"], "OFF")
                self.assertEqual(snapshot["outputs"]["AOM AM"], "OFF")
                self.assertFalse(self.manager.hardware_lock.locked())

    def test_finish_has_no_prerequisite_and_diagram_is_editable(self):
        session_id = self.create()
        response = self.client.post(f"{BASE}/sessions/{session_id}/finish", json={"revision": 0})
        self.assertEqual(response.status_code, 200, response.text)
        self.wait(lambda: self.service.get(session_id)["status"] == "closed")
        self.assertEqual(self.service.get(session_id)["steps"]["A08"]["status"], "completed")
        # 测试工程只复制设备配置；原图下载明确使用真实本实验包文件。
        with patch.object(self.service, "root", lambda: Path(__file__).resolve().parents[2]):
            diagram = self.client.get(f"{BASE}/workflow/diagram")
        self.assertEqual(diagram.status_code, 200, diagram.text)
        self.assertEqual(diagram.json()["type"], "excalidraw")


if __name__ == "__main__":
    unittest.main()
