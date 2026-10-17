"""从真实会话接口验证已知噪声；子进程使用假仪器。"""
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from threading import Thread
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
import yaml
from lab_workflows.experiment_modules.noise_spectrum_xy_known_noise.definition import ADAPTER
from lab_workflows.experiment_modules.noise_spectrum_xy_known_noise.test_workflow import copy_known_config, scan_params
from lab_workflows.experiment_modules.noise_spectrum_xy_known_noise.test_analysis import static_source
from .jobs import JobManager
from .noise_spectrum_xy_known_noise import KnownNoiseSessionService
from .workflow_sessions import create_router

BASE = "/api/experiments/noise-spectrum-xy-known-noise"


class KnownNoiseAPITest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="known-api-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        copy_known_config(self.root)
        self.manager = JobManager()
        self.service = KnownNoiseSessionService(lambda: self.root, self.manager)
        app = FastAPI()
        app.include_router(create_router(self.service))
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        execute = ADAPTER._execute
        self.patch = patch.object(ADAPTER, "_execute", side_effect=lambda module, **args:
            execute("lab_workflows.experiment_modules.noise_spectrum_xy_known_noise.test_workflow" if module.endswith(".session") else module, **args))
        self.patch.start()
        self.addCleanup(self.patch.stop)
        environment = patch.dict(os.environ, {"XY_TEST_ROOT": str(self.root)})
        environment.start()
        self.addCleanup(environment.stop)
        self.addCleanup(self.stop)

    def wait(self, predicate):
        deadline = time.monotonic()+30
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(.02)
        self.fail("假会话超时")

    def stop(self):
        self.service.close_communication()
        self.wait(lambda: all(job.status not in ("queued", "running") for job in self.manager.jobs.values()))

    def create(self):
        response = self.client.post(BASE+"/sessions", json={"parameters": scan_params().to_external()})
        self.assertEqual(response.status_code, 200, response.text)
        session_id = response.json()["session_id"]
        self.wait(lambda: self.service.get(session_id)["status"] == "waiting")
        return session_id

    def test_waiting_refresh_dependency_revision_and_eof_shutdown(self):
        session_id = self.create()
        self.assertTrue(self.manager.hardware_lock.locked())
        active = self.client.get(BASE+"/sessions/active").json()
        self.assertEqual(active["session_id"], session_id)
        self.assertEqual(self.client.post(BASE+f"/sessions/{session_id}/steps", json={"revision": 0, "step_ids": ["A13"]}).status_code, 422)
        response = self.client.post(BASE+f"/sessions/{session_id}/steps", json={"revision": 0, "step_ids": [f"A{i:02d}" for i in range(1, 14)]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.client.post(BASE+f"/sessions/{session_id}/steps", json={"revision": 0, "step_ids": ["A01"]}).status_code, 409)
        self.wait(lambda: self.service.get(session_id).get("pending_request") is None)
        state = self.client.get(BASE+f"/sessions/{session_id}").json()
        self.assertEqual(state["steps"]["A13"]["status"], "completed")
        self.assertEqual(self.client.patch(BASE+f"/sessions/{session_id}/parameters", json={"revision": 0, "parameters": {"RUN_TAG": "old"}}).status_code, 409)
        self.assertEqual(self.client.patch(BASE+f"/sessions/{session_id}/parameters", json={"revision": state["revision"], "parameters": {"RUN_TAG": "next"}}).status_code, 200)
        self.wait(lambda: self.service.get(session_id).get("pending_request") is None)
        self.service.close_communication()
        self.wait(lambda: self.service.get(session_id)["status"] == "closed")
        self.wait(lambda: self.manager.get(state["job_id"]).status not in ("queued", "running"))
        state = self.service.get(session_id)
        self.assertEqual(state["steps"]["A15"]["status"], "completed", state)
        self.assertEqual(state["outputs"]["共用触发"], "OFF")
        self.assertFalse(self.manager.hardware_lock.locked())
        self.assertIsNone(KnownNoiseSessionService(lambda: self.root, self.manager).active())

    def test_cancel_and_explicit_finish_release_hardware_after_shutdown(self):
        for action in ("cancel", "finish"):
            with self.subTest(action=action):
                session_id = self.create()
                state = self.service.get(session_id)
                if action == "cancel":
                    self.manager.get(state["job_id"]).cancellation.cancel()
                else:
                    self.assertEqual(self.client.post(BASE+f"/sessions/{session_id}/finish", json={"revision": state["revision"]}).status_code, 200)
                self.wait(lambda: self.service.get(session_id)["status"] == "closed")
                self.wait(lambda: self.manager.get(state["job_id"]).status not in ("queued", "running"))
                self.assertEqual(self.service.get(session_id)["steps"]["A15"]["status"], "completed")
                self.assertFalse(self.manager.hardware_lock.locked())

    def test_manifest_diagram_history_snapshot_and_reanalysis(self):
        flow = self.client.get(BASE+"/workflow").json()
        self.assertEqual(flow["acquisition"][-1]["dependencies"], [])
        self.assertEqual(flow["parameter_impacts"]["analysis"]["ANALYSIS_DISPERSION_SOURCE_RUN"], ["B05", "B06"])
        diagram = self.root / self.service.diagram_file
        diagram.parent.mkdir(parents=True)
        shutil.copyfile(ADAPTER.root / self.service.diagram_file, diagram)
        self.assertEqual(self.client.get(BASE+"/workflow/diagram").status_code, 200)
        from . import main
        run = self.root / "data/Noise_Spectrum_XY_Ctrl_Known_Noise/history"
        run.mkdir(parents=True)
        config_path = run / "experiment_config.yaml"
        config_path.write_text(yaml.safe_dump(dict(experiment_id=ADAPTER.id, completion_status="completed",
            hf2_daq=dict(actual_rate_Sa_s=1000., DAQ_duration_s=1.),
            scan_params=dict(xy_envelope_voltage_V=[0., 1.]),
            parameters={"RUN_TAG": "history-snapshot"})), encoding="utf-8")
        with TestClient(main.app) as client, patch.object(main, "ROOT", self.root), patch.object(main, "manager", self.manager), patch.object(main, "known_noise_sessions", self.service):
            self.assertEqual(client.get(BASE+"/runs").json()["total"], 1)
            summary = client.get(BASE+"/runs/history/summary").json()
            self.assertEqual(summary["parameters"]["RUN_TAG"], "history-snapshot")
            self.assertEqual(summary["artifacts"], {})
            self.assertEqual(client.get(BASE+"/runs/missing/summary").status_code, 404)
            (run / "results").mkdir()
            def complete(module, **kwargs):
                (run / "results/analysis_summary.json").write_text('{"status": "waiting_calibration"}', encoding="utf-8")
            with patch.object(ADAPTER, "_execute", side_effect=complete):
                response = client.post("/api/runs/history/analyze", json={"experiment_id": ADAPTER.id})
                self.assertEqual(response.status_code, 200, response.text)
                job = self.manager.get(response.json()["id"])
                self.wait(lambda: job.status not in ("queued", "running"))
                self.assertEqual(job.status, "completed", job.error)
            self.assertEqual(client.get(BASE+"/runs/history/summary").json()["analysis_status"], "waiting_calibration")

    def test_analysis_options_are_strings_and_only_analysis_validates_source(self):
        static_source(self.root)
        directory = self.root / "data/Noise_Spectrum_XY_Ctrl_Known_Noise/options"
        (directory / "raw").mkdir(parents=True)
        (directory / "experiment_config.yaml").write_text(yaml.safe_dump(dict(experiment_id=ADAPTER.id,
            completion_status="completed", parameters={"ANALYSIS_DISPERSION_SOURCE_RUN": ""})), encoding="utf-8")
        response = self.client.get(BASE+"/runs/options/analysis")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["options"]["dispersion_source_run"], "")
        self.assertEqual(self.client.post(BASE+"/runs/options/analysis", json={"revision": 0, "step_ids": ["B06"],
            "analysis_parameters": {"ANALYSIS_DISPERSION_SOURCE_RUN": "source"}}).status_code, 422)
        self.assertFalse(self.manager.jobs)

    def test_invalid_manual_kz_rejects_session_before_scheduling_hardware(self):
        for value in (0, -1, True, "5373.638"):
            with self.subTest(value=value):
                response = self.client.post(BASE+"/sessions", json={"parameters": scan_params().to_external()
                    | {"Z_CALIBRATION_K_HZ_PER_V": value}})
                self.assertEqual(response.status_code, 422, response.text)
        self.assertFalse(self.manager.jobs)
        self.assertFalse(self.manager.hardware_lock.locked())

    def test_gui_fixtures_match_current_fields_and_stage_contract(self):
        from lab_workflows.experiment_modules.noise_spectrum_xy_known_noise import stages
        from lab_workflows.experiment_modules.noise_spectrum_xy_known_noise.models import NoiseSpectrumXYKnownNoiseParams
        directory = ADAPTER.root / "GUI/frontend/src/pages"
        fixture = json.loads((directory / "knownNoiseSchema.fixture.json").read_text(encoding="utf-8"))
        params = NoiseSpectrumXYKnownNoiseParams()
        current = params.schema(self.root)
        self.assertEqual(fixture["schema_version"], params.schema_version)
        self.assertEqual(len(fixture["fields"]), 56)
        for saved, actual in zip(fixture["fields"], current["fields"], strict=True):
            self.assertEqual({key: value for key, value in saved.items() if key != "options"},
                             {key: value for key, value in actual.items() if key != "options"})
        self.assertEqual(json.loads((directory / "knownNoiseWorkflow.fixture.json").read_text(encoding="utf-8")),
                         json.loads(json.dumps(stages.definition(), ensure_ascii=False)))

    def test_real_child_rejects_replayed_request_and_stale_revision(self):
        session_id = self.create()
        response = self.client.post(BASE+f"/sessions/{session_id}/steps", json={"revision": 0,
            "step_ids": [f"A{i:02d}" for i in range(1, 13)]})
        self.assertEqual(response.status_code, 200, response.text)
        self.wait(lambda: self.service.get(session_id).get("pending_request") is None)
        state = self.service.get(session_id)
        job = self.manager.get(state["job_id"])
        queued = self.manager.create("protocol-lock-probe", lambda current: "done")
        path = self.root / "data" / ADAPTER.data_type / state["run_id"] / "experiment_config.yaml"
        before = path.read_bytes()
        events, _ = self.manager.stream_state(job, 0)
        request = [event["data"]["command_result"]["request_id"] for event in events
                   if event.get("data", {}).get("command_result")][-1]
        queue = self.service.sessions[session_id]["queue"]
        for command, message in ((dict(request_id=request, revision=state["revision"], action="steps", step_ids=["A13"]), "重复"),
                (dict(request_id="stale-known", revision=0, action="parameters", parameters={"RUN_TAG": "stale"}), "修订")):
            start = len(job.events)
            queue.put(command)
            def rejected():
                fresh, _ = self.manager.stream_state(job, start)
                return any(message in (event.get("data", {}).get("command_result", {}).get("error") or "") for event in fresh)
            self.wait(rejected)
            self.assertEqual(self.service.get(session_id)["revision"], state["revision"])
            self.assertEqual(self.service.get(session_id)["steps"]["A13"]["status"], "pending")
            self.assertEqual(queued.status, "queued")
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.client.post(BASE+f"/sessions/{session_id}/finish", json={"revision": state["revision"]}).status_code, 200)
        self.wait(lambda: self.service.get(session_id)["status"] == "closed" and queued.status == "completed")

    def test_stop_during_real_child_daq_keeps_lock_until_safety_shutdown(self):
        for action in ("cancel", "eof", "shutdown"):
            with self.subTest(action=action), patch.dict(os.environ, {"KNOWN_TEST_BLOCK_DAQ": "1", "KNOWN_TEST_BLOCK_SHUTDOWN": "1"}):
                for name in ("daq_waiting", "shutdown_waiting", "release_shutdown"):
                    (self.root / name).unlink(missing_ok=True)
                session_id = self.create()
                response = self.client.post(BASE+f"/sessions/{session_id}/steps", json={"revision": 0,
                    "step_ids": [f"A{i:02d}" for i in range(1, 13)]})
                self.assertEqual(response.status_code, 200, response.text)
                self.wait(lambda: self.service.get(session_id).get("pending_request") is None)
                state = self.service.get(session_id)
                queued = self.manager.create("shutdown-lock-probe", lambda current: "done")
                response = self.client.post(BASE+f"/sessions/{session_id}/steps", json={"revision": state["revision"], "step_ids": ["A13"]})
                self.assertEqual(response.status_code, 200, response.text)
                self.wait(lambda: (self.root / "daq_waiting").is_file())
                revision = self.service.get(session_id)["revision"]
                self.assertEqual(self.client.post(BASE+f"/sessions/{session_id}/steps", json={"revision": revision, "step_ids": ["A13"]}).status_code, 409)
                self.assertEqual(self.client.patch(BASE+f"/sessions/{session_id}/parameters", json={"revision": revision, "parameters": {"RUN_TAG": "running"}}).status_code, 409)
                closer = None
                try:
                    if action == "cancel":
                        self.manager.cancel(state["job_id"])
                    elif action == "eof":
                        self.service.close_communication()
                    else:
                        closer = Thread(target=self.service.shutdown)
                        closer.start()
                    self.wait(lambda: (self.root / "shutdown_waiting").is_file())
                    self.assertTrue(self.manager.hardware_lock.locked())
                    self.assertEqual(queued.status, "queued")
                    self.assertEqual(self.manager.get(state["job_id"]).status, "running")
                finally:
                    (self.root / "release_shutdown").touch()
                    if closer:
                        closer.join(timeout=20)
                        self.assertFalse(closer.is_alive())
                self.wait(lambda: self.service.get(session_id)["status"] == "closed" and queued.status == "completed")
                closed = self.service.get(session_id)
                self.assertIsNotNone(closed["safety_shutdown"], closed["steps"]["A15"])
                self.assertTrue(closed["safety_shutdown"]["completed"])
                self.assertEqual(closed["steps"]["A15"]["status"], "completed")
                self.assertEqual(closed["outputs"]["共用触发"], "OFF")
                config = yaml.safe_load((self.root / "data" / ADAPTER.data_type / closed["run_id"] / "experiment_config.yaml").read_text(encoding="utf-8"))
                self.assertEqual(config["completion_status"], "cancelled")
