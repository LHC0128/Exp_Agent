"""从真实会话接口验证 Probe AM；子进程使用假仪器。"""
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
import yaml
from lab_workflows.experiment_modules.noise_spectrum_xy_probe_am.definition import ADAPTER
from lab_workflows.experiment_modules.noise_spectrum_xy_probe_am.test_workflow import copy_config, scan_params
from .jobs import JobManager
from .noise_spectrum_xy_probe_am import ProbeAMSessionService
from .workflow_sessions import create_router

BASE = "/api/experiments/noise-spectrum-xy-uncontrolled-probe-am"


class ProbeAMAPITest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="probe-am-api-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        copy_config(self.root)
        self.manager = JobManager()
        self.service = ProbeAMSessionService(lambda: self.root, self.manager)
        app = FastAPI()
        app.include_router(create_router(self.service))
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        execute = ADAPTER._execute
        self.patch = patch.object(ADAPTER, "_execute", side_effect=lambda module, **args:
            execute("lab_workflows.experiment_modules.noise_spectrum_xy_probe_am.test_workflow" if module.endswith(".session") else module, **args))
        self.patch.start()
        self.addCleanup(self.patch.stop)
        environment = patch.dict(os.environ, {"XY_TEST_ROOT": str(self.root)})
        environment.start()
        self.addCleanup(environment.stop)
        self.addCleanup(self.stop)

    def wait(self, predicate):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(.02)
        self.fail("假会话超时")

    def stop(self):
        self.service.close_communication()
        self.wait(lambda: all(job.status not in ("queued", "running") for job in self.manager.jobs.values()))

    def create(self):
        response = self.client.post(BASE + "/sessions", json={"parameters": scan_params().to_external()})
        self.assertEqual(response.status_code, 200, response.text)
        session_id = response.json()["session_id"]
        self.wait(lambda: self.service.get(session_id)["status"] == "waiting")
        return session_id

    def test_waiting_refresh_dependency_revision_and_eof_shutdown(self):
        session_id = self.create()
        self.assertTrue(self.manager.hardware_lock.locked())
        active = self.client.get(BASE + "/sessions/active").json()
        self.assertEqual(active["session_id"], session_id)
        self.assertEqual(active["outputs"]["Probe AM"], "未知")
        self.assertEqual(self.client.post(BASE + f"/sessions/{session_id}/steps", json={"revision": 0, "step_ids": ["A14"]}).status_code, 422)
        response = self.client.post(BASE + f"/sessions/{session_id}/steps", json={"revision": 0, "step_ids": [f"A{i:02d}" for i in range(1, 15)]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.client.post(BASE + f"/sessions/{session_id}/steps", json={"revision": 0, "step_ids": ["A01"]}).status_code, 409)
        self.wait(lambda: self.service.get(session_id).get("pending_request") is None)
        state = self.client.get(BASE + f"/sessions/{session_id}").json()
        self.assertEqual(state["steps"]["A14"]["status"], "completed")
        self.assertEqual(state["outputs"]["Probe AM"], "载波 ON·噪声 ON")
        self.assertEqual(self.client.patch(BASE + f"/sessions/{session_id}/parameters", json={"revision": 0, "parameters": {"RUN_TAG": "old"}}).status_code, 409)
        self.assertEqual(self.client.patch(BASE + f"/sessions/{session_id}/parameters", json={"revision": state["revision"], "parameters": {"RUN_TAG": "next"}}).status_code, 200)
        self.wait(lambda: self.service.get(session_id).get("pending_request") is None)
        self.service.close_communication()
        self.wait(lambda: self.service.get(session_id)["status"] == "closed")
        self.wait(lambda: self.manager.get(state["job_id"]).status not in ("queued", "running"))
        state = self.service.get(session_id)
        self.assertEqual(state["steps"]["A16"]["status"], "completed", state)
        self.assertEqual(state["outputs"]["Probe AM"], "OFF")
        self.assertFalse(self.manager.hardware_lock.locked())
        self.assertIsNone(ProbeAMSessionService(lambda: self.root, self.manager).active())

    def test_cancel_and_explicit_finish_release_hardware_after_shutdown(self):
        for action in ("cancel", "finish"):
            with self.subTest(action=action):
                session_id = self.create()
                state = self.service.get(session_id)
                if action == "cancel":
                    self.manager.get(state["job_id"]).cancellation.cancel()
                else:
                    self.assertEqual(self.client.post(BASE + f"/sessions/{session_id}/finish", json={"revision": state["revision"]}).status_code, 200)
                self.wait(lambda: self.service.get(session_id)["status"] == "closed")
                self.wait(lambda: self.manager.get(state["job_id"]).status not in ("queued", "running"))
                self.assertEqual(self.service.get(session_id)["steps"]["A16"]["status"], "completed")
                self.assertFalse(self.manager.hardware_lock.locked())

    def test_manifest_diagram_history_snapshot_and_reanalysis(self):
        flow = self.client.get(BASE + "/workflow").json()
        self.assertEqual(flow["acquisition"][-1]["dependencies"], [])
        self.assertEqual(flow["acquisition"][7]["display_parameters"], ["PROBE_AOM_CARRIER_FREQ_HZ", "PROBE_AOM_CARRIER_VPP", "PROBE_AOM_AM_DEPTH_PERCENT"])
        self.assertEqual(flow["parameter_impacts"]["analysis"]["ANALYSIS_FIT_HALF_WIDTH_HZ"], ["B04"])
        diagram = self.root / self.service.diagram_file
        diagram.parent.mkdir(parents=True)
        shutil.copyfile(ADAPTER.root / self.service.diagram_file, diagram)
        self.assertEqual(self.client.get(BASE + "/workflow/diagram").status_code, 200)
        from . import main
        run = self.root / "data/Noise_Spectrum_XY_Ctrl_Uncontrolled_Probe_AM/history"
        run.mkdir(parents=True)
        config_path = run / "experiment_config.yaml"
        config_path.write_text(yaml.safe_dump(dict(experiment_id=ADAPTER.id, completion_status="completed", parameters={"RUN_TAG": "history-snapshot"})), encoding="utf-8")
        with TestClient(main.app) as client, patch.object(main, "ROOT", self.root), patch.object(main, "manager", self.manager), patch.object(main, "probe_am_sessions", self.service):
            self.assertEqual(client.get(BASE + "/runs").json()["total"], 1)
            summary = client.get(BASE + "/runs/history/summary").json()
            self.assertEqual(summary["parameters"]["RUN_TAG"], "history-snapshot")
            self.assertEqual(summary["artifacts"], {})
            self.assertEqual(client.get(BASE + "/runs/missing/summary").status_code, 404)
            (run / "results").mkdir()

            def complete(module, **kwargs):
                (run / "results/analysis_summary.json").write_text('{"status": "completed"}', encoding="utf-8")

            with patch.object(ADAPTER, "_execute", side_effect=complete):
                response = client.post("/api/runs/history/analyze", json={"experiment_id": ADAPTER.id})
                self.assertEqual(response.status_code, 200, response.text)
                job = self.manager.get(response.json()["id"])
                self.wait(lambda: job.status not in ("queued", "running"))
                self.assertEqual(job.status, "completed", job.error)
            self.assertEqual(client.get(BASE + "/runs/history/summary").json()["analysis_status"], "completed")

    def test_analysis_options_numeric_and_rejects_unknown_parameters(self):
        directory = self.root / "data/Noise_Spectrum_XY_Ctrl_Uncontrolled_Probe_AM/options"
        (directory / "raw").mkdir(parents=True)
        (directory / "experiment_config.yaml").write_text(yaml.safe_dump(dict(experiment_id=ADAPTER.id,
            completion_status="completed", parameters={"ANALYSIS_BIN_WIDTH_HZ": 50.0})), encoding="utf-8")
        response = self.client.get(BASE + "/runs/options/analysis")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["options"]["bin_width_hz"], 50.0)
        self.assertEqual(self.client.post(BASE + "/runs/options/analysis", json={"revision": 0, "step_ids": ["B04"],
            "analysis_parameters": {"ANALYSIS_FIT_HALF_WIDTH_HZ": 300.0}}).status_code, 422)
        self.assertFalse(self.manager.jobs)


if __name__ == "__main__":
    unittest.main()
