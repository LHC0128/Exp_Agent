"""专属真实路由：假硬件子进程、硬件互斥、历史与独立分析。"""
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

from lab_workflows.experiment_modules.z_aw_current_waveform_scope_check.definition import ADAPTER
from lab_workflows.experiment_modules.z_aw_current_waveform_scope_check.test_workflow import (
    ROOT, copy_config, fake_hardware, scan_params,
)
from lab_workflows.experiment_modules.z_aw_current_waveform_scope_check.test_analysis import synthetic_run
from .jobs import JobManager
from .z_aw_current_waveform_scope_check import ZAWCurrentWaveformScopeCheckSessionService, create_router

BASE = "/api/experiments/z-aw-current-waveform-scope-check"
FAKE_MODULE = "lab_workflows.experiment_modules.z_aw_current_waveform_scope_check.test_workflow"


class CurrentSessionAPITests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="zaw-scope-api-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        copy_config(self.root)
        self.manager = JobManager()
        self.service = ZAWCurrentWaveformScopeCheckSessionService(lambda: self.root, self.manager)
        diagram = self.root / self.service.diagram_file
        diagram.parent.mkdir(parents=True)
        shutil.copyfile(ROOT / self.service.diagram_file, diagram)
        app = FastAPI()
        app.include_router(create_router(self.service, include_history=True))
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        original = ADAPTER._execute
        redirect = patch.object(ADAPTER, "_execute", side_effect=lambda module, **kwargs:
            original(FAKE_MODULE if module.endswith(".session") else module, **kwargs))
        redirect.start(); self.addCleanup(redirect.stop)
        environment = patch.dict(os.environ, {"Z_CURRENT_TEST_ROOT": str(self.root)})
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
            response = self.client.post(f"{BASE}/sessions", json={"parameters": scan_params(self.root).to_external()})
        self.assertEqual(response.status_code, 200, response.text)
        value = response.json()
        self.wait(lambda: self.service.get(value["session_id"])["status"] == "waiting")
        return value["session_id"]

    def command(self, identity, step_ids):
        revision = self.service.get(identity)["revision"]
        response = self.client.post(f"{BASE}/sessions/{identity}/steps", json=dict(revision=revision, step_ids=step_ids))
        self.assertEqual(response.status_code, 200, response.text)
        self.wait(lambda: self.service.get(identity).get("pending_request") is None)
        return self.service.get(identity)

    def test_manifest_diagram_and_history_are_read_only_and_snapshot_based(self):
        flow = self.client.get(f"{BASE}/workflow")
        self.assertEqual(flow.status_code, 200, flow.text)
        self.assertEqual([item["id"] for item in flow.json()["acquisition"]], [f"A{i:02d}" for i in range(1, 7)])
        self.assertEqual(flow.json()["parameter_impacts"]["acquisition"]["CONTROL_SCALE"], ["A03", "A04", "A05"])
        diagram = self.client.get(flow.json()["diagram_url"])
        self.assertEqual(diagram.status_code, 200)
        self.assertEqual(diagram.json()["type"], "excalidraw")
        run = synthetic_run(self.root)
        listing = self.client.get(f"{BASE}/runs?limit=50&offset=0").json()
        self.assertEqual(listing["total"], 1)
        summary = self.client.get(f"{BASE}/runs/{run.name}/summary").json()
        self.assertEqual(summary["parameters"], scan_params(self.root).to_external())
        self.assertEqual(summary["artifacts"], {})
        self.assertEqual(summary["analysis_status"], "not_started")
        self.assertEqual(self.client.get(f"{BASE}/runs/missing/summary").status_code, 404)
        self.assertEqual(self.client.get(f"{BASE}/runs/%2e%2e/summary").status_code, 404)
        self.assertEqual(self.client.get(f"{BASE}/runs/{run.name}/files/../../params/devices.yaml").status_code, 404)
        self.assertFalse(self.manager.jobs)

    def test_waiting_session_holds_lock_refresh_conflicts_and_parameters_invalidate_actual_steps(self):
        identity = self.create()
        self.assertTrue(self.manager.hardware_lock.locked())
        self.assertEqual(self.client.get(f"{BASE}/sessions/active").json()["session_id"], identity)
        self.assertIsNone(ZAWCurrentWaveformScopeCheckSessionService(lambda: self.root, self.manager).active())
        missing = self.client.post(f"{BASE}/sessions/{identity}/steps", json=dict(revision=0, step_ids=["A05"]))
        self.assertEqual(missing.status_code, 422)
        self.assertIn("Scope", missing.text)
        stale = self.client.post(f"{BASE}/sessions/{identity}/steps", json=dict(revision=1, step_ids=["A01"]))
        self.assertEqual(stale.status_code, 409)
        response = self.client.post(f"{BASE}/sessions/{identity}/steps", json=dict(revision=0,
            step_ids=["A01", "A02", "A03", "A04", "A05"]))
        self.assertEqual(response.status_code, 200, response.text)
        duplicate = self.client.post(f"{BASE}/sessions/{identity}/steps", json=dict(revision=0, step_ids=["A01"]))
        self.assertEqual(duplicate.status_code, 409)
        self.wait(lambda: self.service.get(identity).get("pending_request") is None)
        state = self.client.get(f"{BASE}/sessions/{identity}").json()
        self.assertEqual(state["steps"]["A05"]["status"], "completed", state.get("error"))
        self.assertEqual(state["daq_settings"]["actual_points"], 193)
        run = self.root / "data" / ADAPTER.data_type / state["run_id"]
        self.assertTrue((run / "raw/scope_capture_001.npz").is_file())
        with fake_hardware(self.root):
            changed = self.client.patch(f"{BASE}/sessions/{identity}/parameters", json=dict(
                revision=state["revision"], parameters={"SCOPE_CYCLES": 4}))
        self.assertEqual(changed.status_code, 200, changed.text)
        self.wait(lambda: self.service.get(identity).get("pending_request") is None)
        updated = self.service.get(identity)
        self.assertEqual({key for key, value in updated["steps"].items() if value["status"] == "stale"}, {"A04", "A05"})
        self.assertTrue(self.manager.hardware_lock.locked())
        finish = self.client.post(f"{BASE}/sessions/{identity}/finish", json=dict(revision=updated["revision"]))
        self.assertEqual(finish.status_code, 200, finish.text)
        self.wait(lambda: self.manager.get(state["job_id"]).status == "completed")
        self.assertFalse(self.manager.hardware_lock.locked())
        self.assertIsNone(self.service.active())

    def test_cancel_eof_and_shutdown_finish_before_hardware_unlock(self):
        for ending in ("cancel", "eof", "shutdown"):
            with self.subTest(ending=ending):
                identity = self.create()
                state = self.command(identity, ["A01", "A02", "A03"])
                if ending == "cancel":
                    self.manager.cancel(state["job_id"])
                elif ending == "eof":
                    self.service.close_communication()
                else:
                    self.service.shutdown()
                self.wait(lambda: self.manager.get(state["job_id"]).status not in ("queued", "running"))
                closed = self.service.get(identity)
                self.assertEqual(closed["steps"]["A06"]["status"], "completed")
                self.assertEqual(closed["outputs"]["Z 任意波"], "DC 0 V / OFF")
                self.assertFalse(self.manager.hardware_lock.locked())

    def test_analysis_api_uses_real_subprocess_strict_requests_and_directory_mutex(self):
        from . import main
        run = synthetic_run(self.root)
        state = self.client.get(f"{BASE}/runs/{run.name}/analysis").json()
        self.assertEqual(state["options"], {})
        self.assertEqual(self.client.post(f"{BASE}/runs/{run.name}/analysis",
            json=dict(revision=0, step_ids=["B02"])).status_code, 422)
        self.assertEqual(self.client.post(f"{BASE}/runs/{run.name}/analysis",
            json=dict(revision="0", step_ids=["B01"])).status_code, 422)
        self.assertEqual(self.client.post(f"{BASE}/runs/{run.name}/analysis",
            json=dict(revision=0, step_ids=["B01"], analysis_parameters={"CONTROL_SCALE": 2})).status_code, 422)
        first = self.client.post(f"{BASE}/runs/{run.name}/analysis", json=dict(revision=0, step_ids=["B01", "B02", "B03"]))
        self.assertEqual(first.status_code, 200, first.text)
        again = self.client.post(f"{BASE}/runs/{run.name}/analysis", json=dict(revision=0, step_ids=["B01"]))
        self.assertEqual(again.status_code, 409)
        with patch.object(main, "ROOT", self.root), patch.object(main, "manager", self.manager):
            legacy = TestClient(main.app)
            self.addCleanup(legacy.close)
            self.assertEqual(legacy.post(f"/api/runs/{run.name}/analyze",
                json={"experiment_id": ADAPTER.id}).status_code, 409)
        self.wait(lambda: self.manager.get(first.json()["id"]).status not in ("queued", "running"))
        job = self.manager.get(first.json()["id"])
        self.assertEqual(job.status, "completed", job.error)
        refreshed = self.client.get(f"{BASE}/runs/{run.name}/analysis")
        self.assertEqual(refreshed.status_code, 200, refreshed.text)
        self.assertEqual(refreshed.json()["steps"]["B03"]["status"], "completed")
        summary = self.client.get(f"{BASE}/runs/{run.name}/summary").json()
        self.assertEqual(summary["analysis_status"], "completed")
        self.assertIn("results/current_waveform_similarity.png", summary["artifacts"])
        image = self.client.get(summary["artifacts"]["results/current_waveform_similarity.png"])
        self.assertEqual(image.status_code, 200)
        self.assertTrue(image.content.startswith(b"\x89PNG"))
        config = yaml.safe_load((run / "experiment_config.yaml").read_text(encoding="utf-8"))
        config["completion_status"] = "running"
        (run / "experiment_config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
        self.assertEqual(self.client.get(f"{BASE}/runs/{run.name}/analysis").status_code, 409)
        self.assertFalse(self.manager.hardware_lock.locked())


    def test_history_identity_and_format_reject_unknown_without_writing(self):
        run = synthetic_run(self.root)
        config_path = run / "experiment_config.yaml"
        original = config_path.read_bytes()
        for change in (dict(experiment_id=None), dict(raw_format_version=True), dict(measurement_mode="coil_voltage", raw_format_version=None)):
            config = yaml.safe_load(original.decode("utf-8"))
            config.update(change)
            config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
            before = config_path.read_bytes()
            self.assertEqual(self.client.get(f"{BASE}/runs").json()["total"], 0)
            self.assertEqual(self.client.get(f"{BASE}/runs/{run.name}/summary").status_code, 422)
            self.assertEqual(config_path.read_bytes(), before)
        config_path.write_bytes(original)

    def test_main_router_discovery_and_lifespan_include_target(self):
        from . import main
        self.assertIn(main.z_aw_current_scope_sessions, main._workflow_session_services())
        with patch.object(main, "ROOT", self.root):
            client = TestClient(main.app)
            self.addCleanup(client.close)
            response = client.get(f"{BASE}/workflow")
            self.assertEqual(response.status_code, 200, response.text)
            public = client.get(BASE)
            self.assertEqual(public.status_code, 200, public.text)
            self.assertIn("电流", public.json()["description"])
            self.assertIn(f"{BASE}/runs", main.app.openapi()["paths"])


if __name__ == "__main__":
    unittest.main()
