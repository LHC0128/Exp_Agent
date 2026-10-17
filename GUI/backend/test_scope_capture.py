"""示波器采集专属真实路由：假硬件子进程、硬件互斥、历史与独立分析。"""
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from threading import Event
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
import yaml

from lab_workflows.experiment_modules.scope_capture.definition import ADAPTER
from lab_workflows.experiment_modules.scope_capture.test_analysis import make_run
from lab_workflows.experiment_modules.scope_capture.test_workflow import (
    ROOT, copy_config, fake_hardware, scan_params,
)
from .jobs import JobManager
from .scope_capture import ScopeCaptureSessionService, create_router

BASE = "/api/experiments/scope-capture"
FAKE_MODULE = "lab_workflows.experiment_modules.scope_capture.test_workflow"


class ScopeCaptureSessionAPITests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="scope-capture-api-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        copy_config(self.root)
        self.manager = JobManager()
        self.service = ScopeCaptureSessionService(lambda: self.root, self.manager)
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
        environment = patch.dict(os.environ, {"SCOPE_CAPTURE_TEST_ROOT": str(self.root)})
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
        value = response.json()
        self.wait(lambda: self.service.get(value["session_id"])["status"] == "waiting")
        return value["session_id"]

    def command(self, identity, step_ids):
        revision = self.service.get(identity)["revision"]
        response = self.client.post(f"{BASE}/sessions/{identity}/steps", json=dict(revision=revision, step_ids=step_ids))
        self.assertEqual(response.status_code, 200, response.text)
        self.wait(lambda: self.service.get(identity).get("pending_request") is None)
        return self.service.get(identity)

    def test_manifest_diagram_and_history_are_read_only(self):
        flow = self.client.get(f"{BASE}/workflow")
        self.assertEqual(flow.status_code, 200, flow.text)
        manifest = flow.json()
        self.assertEqual([item["id"] for item in manifest["acquisition"]], [f"A{i:02d}" for i in range(1, 6)])
        self.assertEqual([item["id"] for item in manifest["analysis"]], [f"B{i:02d}" for i in range(1, 4)])
        # 每个校验节点都声明真实质量门槛文本，不用文案替代业务阻断。
        for item in manifest["acquisition"] + manifest["analysis"]:
            self.assertTrue(item["quality_gates"], item["id"])
        self.assertEqual(manifest["parameter_impacts"]["analysis"], {})
        self.assertEqual(manifest["parameter_impacts"]["acquisition"]["duration_s"], ["A03", "A04"])
        self.assertEqual(manifest["parameter_impacts"]["acquisition"]["disable_temperature_control"],
                         ["A02", "A03", "A04"])
        diagram = self.client.get(manifest["diagram_url"])
        self.assertEqual(diagram.status_code, 200)
        self.assertEqual(diagram.json()["type"], "excalidraw")
        listing = self.client.get(f"{BASE}/runs?limit=50&offset=0").json()
        self.assertEqual(listing, {"runs": [], "total": 0})
        self.assertEqual(self.client.get(f"{BASE}/runs/missing/summary").status_code, 404)
        self.assertEqual(self.client.get(f"{BASE}/runs/%2e%2e/summary").status_code, 404)
        self.assertEqual(self.client.get(f"{BASE}/runs/xxx/files/../../params/devices.yaml").status_code, 404)
        self.assertFalse(self.manager.jobs)

    def test_waiting_session_holds_lock_conflicts_and_parameters_invalidate_steps(self):
        identity = self.create()
        self.assertTrue(self.manager.hardware_lock.locked())
        self.assertEqual(self.client.get(f"{BASE}/sessions/active").json()["session_id"], identity)
        self.assertIsNone(ScopeCaptureSessionService(lambda: self.root, self.manager).active())
        missing = self.client.post(f"{BASE}/sessions/{identity}/steps", json=dict(revision=0, step_ids=["A04"]))
        self.assertEqual(missing.status_code, 422)
        self.assertIn("A03", missing.text)
        stale = self.client.post(f"{BASE}/sessions/{identity}/steps", json=dict(revision=1, step_ids=["A01"]))
        self.assertEqual(stale.status_code, 409)
        response = self.client.post(f"{BASE}/sessions/{identity}/steps", json=dict(revision=0,
            step_ids=["A01", "A02", "A03", "A04"]))
        self.assertEqual(response.status_code, 200, response.text)
        duplicate = self.client.post(f"{BASE}/sessions/{identity}/steps", json=dict(revision=0, step_ids=["A01"]))
        self.assertEqual(duplicate.status_code, 409)
        self.wait(lambda: self.service.get(identity).get("pending_request") is None)
        state = self.client.get(f"{BASE}/sessions/{identity}").json()
        self.assertEqual(state["steps"]["A04"]["status"], "completed", state.get("error"))
        self.assertEqual(state["daq_settings"]["readback_points"], 500)
        self.assertEqual(state["outputs"]["温控开关"], "5 V ON（已恢复）")
        run = self.root / "data" / ADAPTER.data_type / state["run_id"]
        self.assertTrue((run / "raw/waveform.npz").is_file())
        with fake_hardware(self.root):
            changed = self.client.patch(f"{BASE}/sessions/{identity}/parameters", json=dict(
                revision=state["revision"], parameters={"duration_s": 0.02}))
        self.assertEqual(changed.status_code, 200, changed.text)
        self.wait(lambda: self.service.get(identity).get("pending_request") is None)
        updated = self.service.get(identity)
        self.assertEqual({key for key, value in updated["steps"].items() if value["status"] == "stale"},
                         {"A03", "A04"})
        # 应用参数只使步骤失效，不触发采集。
        self.assertTrue(self.manager.hardware_lock.locked())
        finish = self.client.post(f"{BASE}/sessions/{identity}/finish", json=dict(revision=updated["revision"]))
        self.assertEqual(finish.status_code, 200, finish.text)
        self.wait(lambda: self.manager.get(state["job_id"]).status == "completed")
        closed = self.service.get(identity)
        # 结束节点完成后会话关闭，不再处于等待。
        self.assertEqual(closed["steps"]["A05"]["status"], "completed")
        self.assertEqual(closed["status"], "closed")
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
                self.assertEqual(closed["steps"]["A05"]["status"], "completed")
                self.assertEqual(closed["outputs"]["SDS"], "STOP；已断开")
                self.assertEqual(closed["outputs"]["温控开关"], "5 V ON；已断开")
                self.assertFalse(self.manager.hardware_lock.locked())

    def test_queue_and_concurrent_commands_do_not_overlap_hardware(self):
        identity = self.create()
        entered = Event()
        queued = self.manager.create("scope-test-other-hardware", lambda job: entered.set(), hardware_required=True)
        self.assertFalse(entered.wait(.05))
        self.assertEqual(queued.status, "queued")
        response = self.client.post(f"{BASE}/sessions", json={"parameters": scan_params().to_external()})
        self.assertEqual(response.status_code, 409)
        state = self.command(identity, ["A01", "A02", "A03"])
        self.finish_session(identity, state)
        self.wait(entered.is_set)
        self.wait(lambda: queued.status == "completed")

    def test_completed_capture_stays_completed_after_cancel_eof_and_shutdown(self):
        for ending in ("cancel", "eof", "shutdown"):
            with self.subTest(ending=ending):
                identity = self.create()
                state = self.command(identity, ["A01", "A02", "A03", "A04"])
                run = self.root / "data/Scope_Capture" / state["run_id"]
                raw = (run / "raw/waveform.npz").read_bytes()
                previous = yaml.safe_load((run / "experiment_config.yaml").read_text(encoding="utf-8"))
                if ending == "cancel":
                    self.manager.cancel(state["job_id"])
                elif ending == "eof":
                    self.service.close_communication()
                else:
                    self.service.shutdown()
                self.wait(lambda: self.manager.get(state["job_id"]).status not in ("queued", "running"))
                current = yaml.safe_load((run / "experiment_config.yaml").read_text(encoding="utf-8"))
                self.assertEqual(current["completion_status"], "completed")
                self.assertIsNone(current["failure_reason"])
                self.assertEqual(current["parameters"], previous["parameters"])
                self.assertEqual((run / "raw/waveform.npz").read_bytes(), raw)
                self.assertTrue(current["safety_shutdown"]["completed"])
                self.assertEqual(self.service.get(identity)["status"], "closed")
                self.assertFalse(self.manager.hardware_lock.locked())

    def test_bad_raw_identity_yaml_and_running_history_qualification(self):
        base = self.root / "data/Scope_Capture"
        valid = make_run(base, "valid", legacy=True)
        for name, change in (("unknown", {"raw_format_version": 7}),
                             ("foreign", {"experiment_id": "other"}),
                             ("running", {"completion_status": "running"})):
            run = make_run(base, name)
            path = run / "experiment_config.yaml"
            config = yaml.safe_load(path.read_text(encoding="utf-8")) | change
            path.write_text(yaml.safe_dump(config), encoding="utf-8")
        broken = base / "broken"
        broken.mkdir()
        (broken / "experiment_config.yaml").write_text("x: [", encoding="utf-8")
        listing = self.client.get(f"{BASE}/runs")
        self.assertEqual(listing.status_code, 200, listing.text)
        self.assertEqual({item["run_id"] for item in listing.json()["runs"]}, {"valid", "running"})
        self.assertEqual(self.client.get(f"{BASE}/runs/valid/summary").json()["parameters"],
                         yaml.safe_load((valid / "experiment_config.yaml").read_text(encoding="utf-8"))["parameters"])
        for name in ("unknown", "foreign", "broken"):
            with self.subTest(name=name):
                self.assertEqual(self.client.get(f"{BASE}/runs/{name}/summary").status_code, 422)
                self.assertEqual(self.client.get(f"{BASE}/runs/{name}/files/raw/waveform.npz").status_code, 422)
                self.assertEqual(self.client.get(f"{BASE}/runs/{name}/analysis").status_code, 422)
                self.assertEqual(self.client.post(f"{BASE}/runs/{name}/analysis",
                    json=dict(revision=0, step_ids=["B01"])).status_code, 422)
        self.assertEqual(self.client.get(f"{BASE}/runs/running/summary").status_code, 200)
        self.assertEqual(self.client.get(f"{BASE}/runs/running/analysis").status_code, 409)
        self.assertFalse(self.manager.jobs)

    def test_actual_main_history_routes_and_legacy_analysis_share_qualification_and_mutex(self):
        from . import main
        run = make_run(self.root / "data/Scope_Capture", "valid")
        with patch.multiple(main, ROOT=self.root, manager=self.manager, scope_capture_sessions=self.service), \
                TestClient(main.app) as client:
            listing = client.get("/api/runs?experiment_id=scope-capture")
            self.assertEqual(listing.status_code, 200, listing.text)
            self.assertEqual(listing.json()["runs"][0]["id"], "valid")
            state = self.client.get(f"{BASE}/runs/valid/analysis").json()
            first = self.client.post(f"{BASE}/runs/valid/analysis", json=dict(revision=state["revision"],
                step_ids=["B01", "B02", "B03"]))
            self.assertEqual(first.status_code, 200, first.text)
            self.assertEqual(client.post("/api/runs/valid/analyze", json={"experiment_id": ADAPTER.id}).status_code, 409)
            self.wait(lambda: self.manager.get(first.json()["id"]).status not in ("queued", "running"))
            self.assertEqual(self.manager.get(first.json()["id"]).status, "completed")
            image = client.get("/api/runs/scope-capture/valid/artifacts/waveform_psd.png")
            self.assertEqual(image.status_code, 200, image.text[:100] if image.status_code != 200 else "")
            legacy = client.post("/api/runs/valid/analyze", json={"experiment_id": ADAPTER.id})
            self.assertEqual(legacy.status_code, 200, legacy.text)
            self.assertEqual(self.client.post(f"{BASE}/runs/valid/analysis",
                json=dict(revision=0, step_ids=["B01"])).status_code, 409)
            self.wait(lambda: self.manager.get(legacy.json()["id"]).status not in ("queued", "running"))
            self.assertEqual(self.manager.get(legacy.json()["id"]).status, "completed")
            path = run / "experiment_config.yaml"
            config = yaml.safe_load(path.read_text(encoding="utf-8"))
            for change, expected in (({"raw_format_version": 8}, 422),
                                     ({"experiment_id": "other"}, 422),
                                     ({"completion_status": "running"}, 409)):
                path.write_text(yaml.safe_dump(config | change), encoding="utf-8")
                count = len(self.manager.jobs)
                refused = client.post("/api/runs/valid/analyze", json={"experiment_id": ADAPTER.id})
                self.assertEqual(refused.status_code, expected, refused.text)
                self.assertEqual(len(self.manager.jobs), count)
                listing = client.get("/api/runs?experiment_id=scope-capture").json()["runs"]
                if expected == 422:
                    self.assertEqual(listing, [])
                    self.assertEqual(client.get("/api/runs/scope-capture/valid/artifacts/waveform_psd.png").status_code, 422)
                else:
                    self.assertEqual(len(listing), 1)
                    self.assertFalse(listing[0]["can_analyze"])

    def test_analysis_subprocess_real_chain_and_directory_mutex(self):
        identity = self.create()
        state = self.command(identity, ["A01", "A02", "A03", "A04"])
        run_id = state["run_id"]
        analysis = self.client.get(f"{BASE}/runs/{run_id}/analysis")
        self.assertEqual(analysis.status_code, 200, analysis.text)
        self.assertEqual(analysis.json()["options"], {})
        self.assertEqual(analysis.json()["steps"]["B03"]["status"], "pending")
        self.assertEqual(self.client.post(f"{BASE}/runs/{run_id}/analysis",
            json=dict(step_ids=["B02"])).status_code, 422)
        self.assertEqual(self.client.post(f"{BASE}/runs/{run_id}/analysis",
            json=dict(revision="0", step_ids=["B01"])).status_code, 422)
        self.assertEqual(self.client.post(f"{BASE}/runs/{run_id}/analysis",
            json=dict(revision=0, step_ids=["B01"], analysis_parameters={"psd_window": "boxcar"})).status_code, 422)
        first = self.client.post(f"{BASE}/runs/{run_id}/analysis", json=dict(revision=0, step_ids=["B01"]))
        self.assertEqual(first.status_code, 200, first.text)
        concurrent = self.client.post(f"{BASE}/runs/{run_id}/analysis", json=dict(revision=0, step_ids=["B02"]))
        self.assertEqual(concurrent.status_code, 409)
        self.wait(lambda: self.manager.get(first.json()["id"]).status not in ("queued", "running"))
        job = self.manager.get(first.json()["id"])
        self.assertEqual(job.status, "completed", job.error)
        refreshed = self.client.get(f"{BASE}/runs/{run_id}/analysis")
        self.assertEqual(refreshed.status_code, 200, refreshed.text)
        self.assertEqual(refreshed.json()["steps"]["B01"]["status"], "completed")
        summary = self.client.get(f"{BASE}/runs/{run_id}/summary").json()
        self.assertEqual(summary["analysis_status"], "partial")
        # 剩余阶段从 psd.npz 前置继续，完成 display.json 与波形/PSD 结果图。
        revision = refreshed.json()["revision"]
        final = self.client.post(f"{BASE}/runs/{run_id}/analysis", json=dict(revision=revision, step_ids=["B02", "B03"]))
        self.assertEqual(final.status_code, 200, final.text)
        self.wait(lambda: self.manager.get(final.json()["id"]).status not in ("queued", "running"))
        summary = self.client.get(f"{BASE}/runs/{run_id}/summary").json()
        self.assertEqual(summary["analysis_status"], "completed")
        self.assertIn("results/display.json", summary["artifacts"])
        self.assertIn("results/psd.npz", summary["artifacts"])
        image = self.client.get(summary["artifacts"]["results/waveform_psd.png"])
        self.assertEqual(image.status_code, 200)
        self.assertTrue(image.content.startswith(b"\x89PNG"))
        run = self.root / "data" / ADAPTER.data_type / run_id
        config = yaml.safe_load((run / "experiment_config.yaml").read_text(encoding="utf-8"))
        config["completion_status"] = "running"
        (run / "experiment_config.yaml").write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
        self.assertEqual(self.client.get(f"{BASE}/runs/{run_id}/analysis").status_code, 409)
        self.finish_session(identity, state)
        # 分析不占硬件锁；会话等待期间持锁，结束后释放。
        self.assertFalse(self.manager.hardware_lock.locked())

    def finish_session(self, identity, state):
        revision = self.service.get(identity)["revision"]
        finish = self.client.post(f"{BASE}/sessions/{identity}/finish", json=dict(revision=revision))
        self.assertEqual(finish.status_code, 200, finish.text)
        self.wait(lambda: self.manager.get(state["job_id"]).status not in ("queued", "running"))


if __name__ == "__main__":
    unittest.main()
