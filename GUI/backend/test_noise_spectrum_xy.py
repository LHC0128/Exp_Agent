"""会话 API：真实子进程配合假仪器，不访问实验硬件。"""

import os
import json
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

    def test_workflow_exposes_display_bindings_and_parameter_impacts_without_jobs(self):
        response = self.client.get(f"{BASE}/workflow")
        self.assertEqual(response.status_code, 200, response.text)
        flow = response.json()
        self.assertEqual([stage["id"] for stage in flow["acquisition"]
                          if "TEMP_SWITCH_ON_SETTLE_S" in stage["display_parameters"]], ["A08", "A10", "A12"])
        self.assertEqual(flow["parameter_impacts"]["acquisition"]["TEMP_SWITCH_ON_SETTLE_S"],
                         [f"A{i:02d}" for i in range(8, 14)])
        self.assertEqual(flow["parameter_impacts"]["analysis"]["ANALYSIS_BIN_WIDTH_HZ"], ["B02", "B03", "B04", "B05"])
        self.assertFalse(self.manager.jobs)

    def test_gui_fixture_matches_actual_schema_and_workflow(self):
        from lab_workflows.experiment_modules.noise_spectrum_xy import stages
        fixture_path = Path(__file__).parents[1] / "frontend/src/pages/noiseSpectrumXY.fixture.json"
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        # 默认值是测试快照；用户保存默认值后不改变字段和流程契约。
        field_contract = lambda schema: [{key: value for key, value in field.items() if key != "default"}
                                         for field in schema["fields"]]
        self.assertEqual(field_contract(fixture["schema"]), field_contract(ADAPTER.schema()))
        self.assertEqual(len(fixture["schema"]["fields"]), 46)
        self.assertEqual(fixture["flow"], json.loads(json.dumps(stages.definition())))
        module = Path(__file__).parents[2] / "lab_workflows/experiment_modules/noise_spectrum_xy"
        diagram = json.loads(next(module.glob("*.excalidraw")).read_text(encoding="utf-8"))
        notes = {item["customData"]["workflow_stage"]: item["customData"]["quality_gates"]
                 for item in diagram["elements"] if item.get("customData", {}).get("role") == "quality_gates"}
        self.assertEqual(notes, {stage.id: list(stage.quality_gates)
                                for stage in (*stages.ACQUISITION, *stages.ANALYSIS) if stage.quality_gates})

    def test_backend_shutdown_finishes_real_session_before_releasing_lock(self):
        session_id = self.create()
        response = self.client.post(f"{BASE}/sessions/{session_id}/steps",
            json={"revision": 0, "step_ids": ["A01", "A02"]})
        self.assertEqual(response.status_code, 200, response.text)
        self.wait(lambda: self.service.get(session_id).get("pending_request") is None)
        self.assertTrue(self.manager.hardware_lock.locked())
        from threading import Event
        competing_ran = Event()
        queued = self.manager.create("test:competing-hardware", lambda job: competing_ran.set())
        self.wait(lambda: self.manager.queued_hardware_count() == 1)
        self.assertEqual(queued.status, "queued")
        self.assertFalse(competing_ran.is_set())
        self.service.shutdown()
        state = self.service.get(session_id)
        self.assertEqual(state["status"], "closed")
        self.assertEqual(state["steps"]["A14"]["status"], "completed")
        self.assertEqual(state["outputs"]["X"], "OFF")
        self.assertEqual(self.manager.get(state["job_id"]).status, "completed")
        self.wait(lambda: queued.status == "completed")
        self.assertTrue(competing_ran.is_set())
        self.assertFalse(self.manager.hardware_lock.locked())

    def test_history_uses_run_snapshot_missing_results_and_reanalysis_without_hardware(self):
        from . import main

        run = self.root / "data/Noise_Spectrum_XY_Ctrl/0101_1200_history"
        run.mkdir(parents=True)
        config_path = run / "experiment_config.yaml"
        parameters = {"RUN_TAG": "saved-history", "ANALYSIS_BIN_WIDTH_HZ": 75.0}
        config_path.write_text(yaml.safe_dump(self.qualification_config() | {"parameters": parameters}), encoding="utf-8")
        client = TestClient(main.app)
        self.addCleanup(client.close)
        with patch.object(main, "ROOT", self.root), patch.object(main, "manager", self.manager), \
                patch.object(main, "xy_sessions", self.service):
            listing = client.get(f"{BASE}/runs?limit=50&offset=0").json()
            self.assertEqual(listing["total"], 1)
            self.assertEqual(listing["runs"][0]["run_tag"], "saved-history")
            summary = client.get(f"{BASE}/runs/{run.name}/summary").json()
            self.assertEqual(summary["parameters"], parameters)
            self.assertEqual(summary["artifacts"], {})
            self.assertEqual(summary["analysis_status"], "not_started")
            self.assertEqual(client.get(f"{BASE}/runs/missing/summary").status_code, 404)
            self.assertFalse(self.manager.jobs)
            (run / "results").mkdir()
            (run / "results/noise_spectrum_2d.png").write_bytes(b"fake png")
            summary = client.get(f"{BASE}/runs/{run.name}/summary").json()
            self.assertEqual(set(summary["artifacts"]), {"noise_spectrum_2d.png"})
            with patch.object(ADAPTER, "_execute", return_value=None) as execute:
                response = client.post(f"/api/runs/{run.name}/analyze", json={"experiment_id": "noise-spectrum-xy"})
                self.assertEqual(response.status_code, 200, response.text)
                self.wait(lambda: self.manager.get(response.json()["id"]).status == "completed")
                execute.assert_called_once()
            self.assertEqual(yaml.safe_load(config_path.read_text(encoding="utf-8"))["parameters"], parameters)
            self.assertEqual(client.get(f"{BASE}/runs/{run.name}/summary").json()["analysis_status"], "completed")
            config_path.write_text(yaml.safe_dump(dict(experiment_id="noise-spectrum-xy")), encoding="utf-8")
            self.assertEqual(client.get(f"{BASE}/runs/{run.name}/summary").status_code, 409)

    def qualification_config(self):
        return dict(experiment_id="noise-spectrum-xy", experiment_type="Noise_Spectrum_XY_Ctrl",
            schema_version=4, raw_format_version=1, completion_status="completed", parameters={},
            scan_params={"xy_envelope_voltage_V": [0.0, 1.0]},
            hf2_daq={"actual_rate_Sa_s": 8192.0, "DAQ_duration_s": 1.0, "nperseg": 256})

    def test_generic_can_analyze_matches_sine_format_and_input_qualification(self):
        """通用历史的按钮资格必须和正式分析 B01 使用同一输入边界。"""
        from copy import deepcopy
        from . import main
        from lab_workflows.experiment_modules.noise_spectrum_xy.run_format import (
            require_supported_format, validate_analysis_inputs,
        )

        configs = {}
        for schema in (1, 2, 3, 4, 999):
            config = self.qualification_config()
            config["schema_version"] = schema
            if schema != 999:
                config.pop("raw_format_version")
            configs[f"schema-{schema}"] = config
        early = self.qualification_config()
        for key in ("experiment_id", "schema_version", "raw_format_version"):
            early.pop(key)
        early["scan_params"] = dict(XY_AMP_START_V=0.0, XY_AMP_STOP_V=1.0, XY_AMP_POINTS=2)
        configs["early-amp"] = early
        for name, updates in (("unknown-raw", {"raw_format_version": 99}),
                ("bool-raw", {"raw_format_version": True}), ("running", {"completion_status": "running"}),
                ("foreign-type", {"experiment_type": "Noise_Spectrum_XY_Ctrl_Known_Noise"})):
            configs[name] = self.qualification_config() | updates
        configs["missing-rate"] = deepcopy(self.qualification_config())
        configs["missing-rate"]["hf2_daq"].pop("actual_rate_Sa_s")
        configs["unknown-unmarked-schema"] = self.qualification_config()
        configs["unknown-unmarked-schema"].pop("raw_format_version")
        configs["unknown-unmarked-schema"]["schema_version"] = 999
        for name, config in configs.items():
            run = self.root / "data/Noise_Spectrum_XY_Ctrl" / name
            run.mkdir(parents=True)
            (run / "experiment_config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
        with TestClient(main.app) as client, patch.object(main, "ROOT", self.root):
            response = client.get("/api/runs?experiment_id=noise-spectrum-xy&limit=200")
            self.assertEqual(response.status_code, 200, response.text)
            actual = {item["id"]: item["can_analyze"] for item in response.json()["runs"]}
            for name, config in configs.items():
                with self.subTest(name=name):
                    try:
                        require_supported_format(config)
                        validate_analysis_inputs(config)
                        expected = config.get("completion_status") != "running"
                    except ValueError:
                        expected = False
                    self.assertEqual(actual[name], expected)
        self.assertFalse(self.manager.jobs)

    def test_legacy_reanalysis_rejects_ineligible_sine_before_creating_job(self):
        """旧入口不得接受新分步入口明确拒绝的原始格式。"""
        from types import SimpleNamespace
        from . import main

        run = self.root / "data/Noise_Spectrum_XY_Ctrl/ineligible"
        run.mkdir(parents=True)
        for name in ("unknown-raw", "missing-rate", "foreign-type"):
            with self.subTest(name=name):
                config = self.qualification_config()
                if name == "unknown-raw":
                    config["raw_format_version"] = 99
                elif name == "missing-rate":
                    config["hf2_daq"].pop("actual_rate_Sa_s")
                else:
                    config["experiment_type"] = "Noise_Spectrum_XY_Ctrl_Known_Noise"
                path = run / "experiment_config.yaml"
                path.write_text(yaml.safe_dump(config), encoding="utf-8")
                before = path.read_bytes()
                with TestClient(main.app) as client, patch.object(main, "ROOT", self.root), \
                        patch.object(main, "manager", self.manager), patch.object(main, "xy_sessions", self.service), \
                        patch.object(self.manager, "create", return_value=SimpleNamespace(
                            public=lambda: {"id": "must-not-exist", "status": "queued"})) as create:
                    response = client.post(f"/api/runs/{run.name}/analyze", json={"experiment_id": "noise-spectrum-xy"})
                    self.assertIn(response.status_code, (400, 409, 422), response.text)
                    create.assert_not_called()
                    self.assertEqual(path.read_bytes(), before)
                    self.assertFalse(self.manager.jobs)

    def test_legacy_reanalysis_accepts_verified_sine_formats_and_rejects_running(self):
        """资格收紧后，已核实旧格式及新 raw 的未来参数版本仍能进入任务队列。"""
        from types import SimpleNamespace
        from . import main

        run = self.root / "data/Noise_Spectrum_XY_Ctrl/compatible"
        run.mkdir(parents=True)
        configs = []
        for schema in (1, 2, 3, 4, 999):
            config = self.qualification_config() | {"schema_version": schema}
            if schema != 999:
                config.pop("raw_format_version")
            configs.append((f"schema-{schema}", config))
        early = self.qualification_config()
        for key in ("experiment_id", "schema_version", "raw_format_version"):
            early.pop(key)
        early["scan_params"] = dict(XY_AMP_START_V=0.0, XY_AMP_STOP_V=1.0, XY_AMP_POINTS=2)
        configs.append(("early-amp", early))
        configs.append(("running", self.qualification_config() | {"completion_status": "running"}))
        with TestClient(main.app) as client, patch.object(main, "ROOT", self.root), \
                patch.object(main, "manager", self.manager), patch.object(main, "xy_sessions", self.service):
            for name, config in configs:
                with self.subTest(name=name), patch.object(self.manager, "create", return_value=SimpleNamespace(
                        public=lambda: {"id": "compatible-job", "status": "queued"})) as create:
                    path = run / "experiment_config.yaml"
                    path.write_text(yaml.safe_dump(config), encoding="utf-8")
                    before = path.read_bytes()
                    response = client.post(f"/api/runs/{run.name}/analyze", json={"experiment_id": "noise-spectrum-xy"})
                    if name == "running":
                        self.assertEqual(response.status_code, 409, response.text)
                        self.assertEqual(response.headers["X-Error-Code"], "run_busy")
                        create.assert_not_called()
                    else:
                        self.assertEqual(response.status_code, 200, response.text)
                        create.assert_called_once()
                        self.assertFalse(create.call_args.kwargs["hardware_required"])
                    self.assertEqual(path.read_bytes(), before)

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
            experiment_id="noise-spectrum-xy", schema_version=4, raw_format_version=1,
            scan_params={"xy_envelope_voltage_V": [0.0, 0.5]},
            hf2_daq={"actual_rate_Sa_s": 1000.0, "DAQ_duration_s": 1.0},
            completion_status="completed", parameters={})), encoding="utf-8")
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

        release.clear()
        with patch.object(ADAPTER, "_execute", side_effect=lambda *a, **kw: release.wait(20)), \
                patch.object(main, "ROOT", self.root), patch.object(main, "manager", self.manager), \
                patch.object(main, "xy_sessions", self.service):
            try:
                legacy = main.analyze_run(run.name, main.AnalysisBody(experiment_id="noise-spectrum-xy"))
                self.wait(lambda: self.manager.get(legacy["id"]).status == "running")
                staged = self.client.post(f"{BASE}/runs/{run.name}/analysis",
                    json={"revision": 0, "step_ids": ["B01"]})
                self.assertEqual(staged.status_code, 409, staged.text)
                self.assertEqual(staged.headers["X-Error-Code"], "analysis_running")
            finally:
                release.set()
            self.wait(lambda: self.manager.get(legacy["id"]).status == "completed")


if __name__ == "__main__":
    unittest.main()
