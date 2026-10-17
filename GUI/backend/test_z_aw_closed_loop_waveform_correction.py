"""闭环真实 API：假硬件子进程、硬件锁、修订及临时目录分析。"""
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
from lab_workflows.experiment_modules.z_aw_closed_loop_waveform_correction.definition import ADAPTER
from lab_workflows.experiment_modules.z_aw_closed_loop_waveform_correction.test_workflow import (
    ROOT, copy_config, fake_hardware, scan_params,
)
from lab_workflows.experiment_modules.z_aw_closed_loop_waveform_correction.test_analysis import synthetic_run
from .jobs import JobManager
from .z_aw_closed_loop_waveform_correction import ZAWClosedLoopSessionService, create_router

BASE = "/api/experiments/z-aw-closed-loop-waveform-correction"
FAKE_MODULE = "lab_workflows.experiment_modules.z_aw_closed_loop_waveform_correction.test_workflow"


class ClosedLoopAPITests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="z-closed-loop-api-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        copy_config(self.root)
        self.manager = JobManager()
        self.service = ZAWClosedLoopSessionService(lambda: self.root, self.manager)
        diagram = self.root / self.service.diagram_file
        diagram.parent.mkdir(parents=True)
        shutil.copyfile(ROOT / self.service.diagram_file, diagram)
        app = FastAPI()
        app.include_router(create_router(self.service))
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        original = ADAPTER._execute
        redirect = patch.object(ADAPTER, "_execute", side_effect=lambda module, **kwargs:
            original(FAKE_MODULE if module.endswith(".session") else module, **kwargs))
        redirect.start(); self.addCleanup(redirect.stop)
        environment = patch.dict(os.environ, {"Z_CLOSED_TEST_ROOT": str(self.root)})
        environment.start(); self.addCleanup(environment.stop)
        self.addCleanup(self.stop_sessions)

    def wait(self, predicate):
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(.02)
        self.fail("假仪器子进程未按时完成")

    def stop_sessions(self):
        self.service.close_communication()
        for job in list(self.manager.jobs.values()):
            if job.status in ("queued", "running"):
                job.cancellation.cancel()
        self.wait(lambda: all(job.status not in ("queued", "running") for job in self.manager.jobs.values()))

    def create(self):
        with fake_hardware(self.root):
            response = self.client.post(f"{BASE}/sessions", json={"parameters": scan_params().to_external()})
        self.assertEqual(response.status_code, 200, response.text)
        identity = response.json()["session_id"]
        self.wait(lambda: self.service.get(identity)["status"] == "waiting")
        return identity

    def command(self, identity, ids):
        response = self.client.post(f"{BASE}/sessions/{identity}/steps", json=dict(
            revision=self.service.get(identity)["revision"], step_ids=ids))
        self.assertEqual(response.status_code, 200, response.text)
        self.wait(lambda: self.service.get(identity).get("pending_request") is None)
        return self.service.get(identity)

    def test_workflow_diagram_history_and_summary_preserve_old_fields_without_writing(self):
        flow = self.client.get(f"{BASE}/workflow").json()
        self.assertEqual(len(flow["acquisition"]), 6)
        self.assertEqual(len(flow["analysis"]), 2)
        self.assertEqual(self.client.get(flow["diagram_url"]).json()["type"], "excalidraw")
        run = synthetic_run(self.root)
        before = {p: p.read_bytes() for p in run.rglob("*") if p.is_file()}
        self.assertEqual(self.client.get(f"{BASE}/runs?limit=1&offset=0").json()["total"], 1)
        summary = self.client.get(f"{BASE}/runs/{run.name}/summary")
        self.assertEqual(summary.status_code, 200, summary.text)
        value = summary.json()
        self.assertEqual(value["parameters"], scan_params().to_external())
        self.assertEqual(value["iteration_count"], 1)
        self.assertEqual(value["best_iteration"], 0)
        self.assertAlmostEqual(value["best_relative_rms_error"], .02)
        self.assertIn("corrected_control_waveform.npz", value["artifacts"])
        self.assertEqual(value["analysis_status"], "not_started")
        self.assertEqual({p: p.read_bytes() for p in run.rglob("*") if p.is_file()}, before)
        self.assertFalse(self.manager.jobs)
        self.assertEqual(self.client.get(f"{BASE}/runs/missing/summary").status_code, 404)
        self.assertEqual(self.client.get(f"{BASE}/runs/%2e%2e/summary").status_code, 404)
        self.assertEqual(self.client.get(f"{BASE}/runs/{run.name}/files/../../params/devices.yaml").status_code, 404)
        for p in (run / "results").iterdir():
            p.unlink()
        self.assertEqual(self.client.get(f"{BASE}/runs/{run.name}/summary").json()["artifacts"], {})

    def test_real_subprocess_holds_lock_rejects_conflicts_and_preserves_applied_run(self):
        identity = self.create()
        self.assertTrue(self.manager.hardware_lock.locked())
        self.assertEqual(self.client.get(f"{BASE}/sessions/active").json()["session_id"], identity)
        self.assertIsNone(ZAWClosedLoopSessionService(lambda: self.root, self.manager).active())
        missing = self.client.post(f"{BASE}/sessions/{identity}/steps", json=dict(revision=0, step_ids=["A05"]))
        self.assertEqual(missing.status_code, 422)
        self.assertEqual(self.client.post(f"{BASE}/sessions/{identity}/steps", json=dict(revision=1, step_ids=["A01"])).status_code, 409)
        state = self.command(identity, ["A01", "A02", "A03", "A04", "A05"])
        self.assertEqual(state["steps"]["A05"]["status"], "completed", state)
        run = self.root / "data" / ADAPTER.data_type / state["run_id"]
        config = yaml.safe_load((run / "experiment_config.yaml").read_text(encoding="utf-8"))
        self.assertTrue((run / "raw/iteration_000/scope_capture_000.npz").is_file())
        with fake_hardware(self.root):
            response = self.client.patch(f"{BASE}/sessions/{identity}/parameters", json=dict(
                revision=state["revision"], parameters={"CONTROL_SCALE": .9}))
        self.assertEqual(response.status_code, 200, response.text)
        self.wait(lambda: self.service.get(identity).get("pending_request") is None)
        updated = self.service.get(identity)
        self.assertEqual(updated["steps"]["A03"]["status"], "stale")
        self.assertEqual(updated["steps"]["A02"]["status"], "completed")
        response = self.client.post(f"{BASE}/sessions/{identity}/finish", json=dict(revision=updated["revision"]))
        self.assertEqual(response.status_code, 200, response.text)
        self.wait(lambda: self.manager.get(state["job_id"]).status == "completed")
        self.assertFalse(self.manager.hardware_lock.locked())
        self.assertEqual(yaml.safe_load((run / "experiment_config.yaml").read_text(encoding="utf-8"))["parameters"], config["parameters"])

    def test_cancel_eof_and_backend_shutdown_release_lock_after_cleanup(self):
        for ending in ("cancel", "eof", "shutdown"):
            with self.subTest(ending=ending):
                identity = self.create()
                state = self.command(identity, ["A01", "A02", "A03", "A04"])
                if ending == "cancel":
                    self.manager.cancel(state["job_id"])
                elif ending == "eof":
                    self.service.close_communication()
                else:
                    self.service.shutdown()
                self.wait(lambda: self.manager.get(state["job_id"]).status not in ("queued", "running"))
                final = self.service.get(identity)
                self.assertEqual(final["status"], "closed", final)
                self.assertTrue(final["safety_shutdown"]["completed"], final)
                self.assertFalse(self.manager.hardware_lock.locked())

    def test_analysis_dependencies_revisions_busy_run_and_real_subprocess(self):
        run = synthetic_run(self.root)
        url = f"{BASE}/runs/{run.name}/analysis"
        initial = self.client.get(url).json()
        self.assertEqual(initial["options"], {})
        self.assertEqual(self.client.post(url, json=dict(revision=0, step_ids=["B02"])).status_code, 422)
        self.assertEqual(self.client.post(url, json=dict(revision=1, step_ids=["B01"])).status_code, 409)
        self.assertEqual(self.client.post(url, json=dict(revision=0, step_ids=["B01"], analysis_parameters={"TARGET_RELATIVE_RMS":.5})).status_code, 422)
        response = self.client.post(url, json=dict(revision=0, step_ids=["B01", "B02"]))
        self.assertEqual(response.status_code, 200, response.text)
        job = self.manager.get(response.json()["id"])
        self.assertEqual(self.client.post(url, json=dict(revision=0, step_ids=["B01"])).status_code, 409)
        self.wait(lambda: job.status not in ("queued", "running"))
        self.assertEqual(job.status, "completed", job.public())
        self.assertTrue((run / "results/final_waveform_report.yaml").is_file())
        self.assertEqual(self.client.get(url).json()["steps"]["B02"]["status"], "completed")
        config_path = run / "experiment_config.yaml"
        config = yaml.safe_load(config_path.read_text(encoding="utf-8")); config["completion_status"] = "running"
        config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
        self.assertEqual(self.client.get(url).status_code, 409)

    def test_main_routes_and_legacy_reanalysis_share_run_guard(self):
        from . import main
        run = synthetic_run(self.root)
        client = TestClient(main.app)
        self.addCleanup(client.close)
        operation = main.app.openapi()["paths"][f"{BASE}/runs/{{run_id}}/summary"]["get"]["operationId"]
        self.assertEqual(operation, "experiment_run_summary_api_experiments_z_aw_closed_loop_waveform_correction_runs__run_id__summary_get")
        with patch.object(main, "ROOT", self.root):
            self.assertEqual(client.get(f"{BASE}/workflow").status_code, 200)
            self.assertEqual(client.get(f"{BASE}/runs/{run.name}/summary").status_code, 200)
            config_path = run / "experiment_config.yaml"
            config = yaml.safe_load(config_path.read_text(encoding="utf-8")); config["completion_status"] = "running"
            config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
            response = client.post(f"/api/runs/{run.name}/analyze", json={"experiment_id": ADAPTER.id})
            self.assertEqual(response.status_code, 409, response.text)


if __name__ == "__main__":
    unittest.main()
