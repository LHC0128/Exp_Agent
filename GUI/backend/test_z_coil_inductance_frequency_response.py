"""专属真实路由：假硬件子进程、硬件互斥、历史与独立分析。"""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import tempfile
from threading import Barrier, Event
import time
import unittest
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
import yaml

from lab_workflows.experiment_modules.z_coil_inductance_frequency_response.definition import ADAPTER
from lab_workflows.experiment_modules.z_coil_inductance_frequency_response.test_workflow import (
    ROOT, copy_config, fake_hardware, scan_params,
)
from lab_workflows.experiment_modules.z_coil_inductance_frequency_response.test_analysis import synthetic_run
from .jobs import JobManager
from .z_coil_inductance_frequency_response import ZCoilInductanceFrequencyResponseSessionService, create_router

BASE = "/api/experiments/z-coil-inductance-frequency-response"
FAKE_MODULE = "lab_workflows.experiment_modules.z_coil_inductance_frequency_response.test_workflow"


class ScopeSessionAPITests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="zaw-scope-api-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        copy_config(self.root)
        self.manager = JobManager()
        self.service = ZCoilInductanceFrequencyResponseSessionService(lambda: self.root, self.manager)
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
        environment = patch.dict(os.environ, {"Z_INDUCTANCE_TEST_ROOT": str(self.root)})
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
        self.assertEqual([item["id"] for item in flow.json()["acquisition"]], [f"A{i:02d}" for i in range(1, 8)])
        self.assertEqual(flow.json()["parameter_impacts"]["acquisition"]["DRIVE_AMPLITUDE_VPP"], ["A04", "A05", "A06"])
        self.assertEqual({stage["id"]: stage["quality_gates"]
            for stage in flow.json()["acquisition"] + flow.json()["analysis"] if stage["quality_gates"]}, {
            "A01": ["参数、安全限值、接线映射及独立通道兼容检查全部通过"],
            "A03": ["参考时钟写入后严格回读一致，失败先收尾再断开"],
            "A06": ["Scope 完整帧与自动量程检查通过；读取或写盘失败保留已取得的帧并停止后续采集"],
            "B01": ["实验身份、原始格式、必需波形、正频率与下降沿均有效"],
            "B02": ["存在有效幅值供参考归一化；不足四个有效频率点时模型不可用，保留测量诊断供导出"],
        })
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
        self.assertIsNone(ZCoilInductanceFrequencyResponseSessionService(lambda: self.root, self.manager).active())
        missing = self.client.post(f"{BASE}/sessions/{identity}/steps", json=dict(revision=0, step_ids=["A06"]))
        self.assertEqual(missing.status_code, 422)
        self.assertIn("Scope", missing.text)
        stale = self.client.post(f"{BASE}/sessions/{identity}/steps", json=dict(revision=1, step_ids=["A01"]))
        self.assertEqual(stale.status_code, 409)
        response = self.client.post(f"{BASE}/sessions/{identity}/steps", json=dict(revision=0,
            step_ids=["A01", "A02", "A03", "A04", "A05", "A06"]))
        self.assertEqual(response.status_code, 200, response.text)
        duplicate = self.client.post(f"{BASE}/sessions/{identity}/steps", json=dict(revision=0, step_ids=["A01"]))
        self.assertEqual(duplicate.status_code, 409)
        self.wait(lambda: self.service.get(identity).get("pending_request") is None)
        state = self.client.get(f"{BASE}/sessions/{identity}").json()
        self.assertEqual(state["steps"]["A06"]["status"], "completed", state.get("error"))
        self.assertEqual(state["daq_settings"]["actual_points"], 1281)
        run = self.root / "data" / ADAPTER.data_type / state["run_id"]
        self.assertTrue((run / "raw/scope_f003_r01.npz").is_file())
        with fake_hardware(self.root):
            changed = self.client.patch(f"{BASE}/sessions/{identity}/parameters", json=dict(
                revision=state["revision"], parameters={"SCOPE_CYCLES": 4}))
        self.assertEqual(changed.status_code, 200, changed.text)
        self.wait(lambda: self.service.get(identity).get("pending_request") is None)
        updated = self.service.get(identity)
        self.assertEqual({key for key, value in updated["steps"].items() if value["status"] == "stale"}, {"A05", "A06"})
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
                original_event, cleanup_locks = self.manager.event, []

                def record(job, event):
                    snapshot = event.data.get("workflow_session", {})
                    if snapshot.get("steps", {}).get("A07", {}).get("status") == "completed":
                        cleanup_locks.append(self.manager.hardware_lock.locked())
                    return original_event(job, event)

                with patch.object(self.manager, "event", side_effect=record):
                    if ending == "cancel":
                        self.manager.cancel(state["job_id"])
                    elif ending == "eof":
                        self.service.close_communication()
                    else:
                        self.service.shutdown()
                    self.wait(lambda: self.manager.get(state["job_id"]).status not in ("queued", "running"))
                self.assertTrue(cleanup_locks)
                self.assertTrue(all(cleanup_locks))
                closed = self.service.get(identity)
                self.assertEqual(closed["steps"]["A07"]["status"], "completed")
                self.assertEqual(closed["outputs"]["Z 正弦"], "DC 0 V / OFF")
                self.assertFalse(self.manager.hardware_lock.locked())

    def test_concurrent_commands_and_child_duplicate_ids_execute_only_once(self):
        identity = self.create()
        barrier = Barrier(2)

        def submit():
            barrier.wait(timeout=5)
            return self.client.post(f"{BASE}/sessions/{identity}/steps",
                json=dict(revision=0, step_ids=["A01"]))

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _index: submit(), range(2)))
        self.assertEqual(sorted(response.status_code for response in responses), [200, 409])
        accepted = next(response.json() for response in responses if response.status_code == 200)
        self.wait(lambda: self.service.get(identity).get("pending_request") is None)
        state = self.service.get(identity)
        self.assertEqual(state["revision"], 1)
        self.assertEqual(state["steps"]["A01"]["status"], "completed")
        self.assertEqual(state["steps"]["A02"]["status"], "pending")
        job = self.manager.get(state["job_id"])
        duplicate = dict(request_id=accepted["pending_request"], revision=state["revision"],
                         action="steps", step_ids=["A01"])
        self.service.sessions[identity]["queue"].put(duplicate)
        self.wait(lambda: any(event.get("data", {}).get("command_result", {}).get("error") ==
            "重复或无效的请求编号" for event in job.events))
        self.assertEqual(self.service.get(identity)["revision"], 1)
        acquired = Event()
        contender = self.manager.create("inductance-test-contender", lambda _job: acquired.set())
        self.wait(lambda: self.manager.queued_hardware_count() == 1)
        self.assertFalse(acquired.wait(.05))
        response = self.client.post(f"{BASE}/sessions/{identity}/finish", json=dict(revision=1))
        self.assertEqual(response.status_code, 200, response.text)
        self.wait(lambda: contender.status == "completed")
        self.assertTrue(acquired.is_set())
        self.assertEqual(self.service.get(identity)["steps"]["A07"]["status"], "completed")

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
            json=dict(revision=0, step_ids=["B01"], analysis_parameters={"DRIVE_AMPLITUDE_VPP": 2})).status_code, 422)
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
        self.assertIn("results/frequency_response.png", summary["artifacts"])
        image = self.client.get(summary["artifacts"]["results/frequency_response.png"])
        self.assertEqual(image.status_code, 200)
        self.assertTrue(image.content.startswith(b"\x89PNG"))
        for name in ("frequency_response.json", "frequency_response.png", "waveform_overview.png"):
            self.assertIn("v", parse_qs(urlsplit(summary["artifacts"][f"results/{name}"]).query))
        rerun = self.client.post(f"{BASE}/runs/{run.name}/analysis",
            json=dict(revision=refreshed.json()["revision"], step_ids=["B03"]))
        self.assertEqual(rerun.status_code, 200, rerun.text)
        self.wait(lambda: self.manager.get(rerun.json()["id"]).status not in ("queued", "running"))
        self.assertEqual(self.manager.get(rerun.json()["id"]).status, "completed")
        regenerated = self.client.get(f"{BASE}/runs/{run.name}/summary").json()
        for name in ("frequency_response.json", "frequency_response.png", "waveform_overview.png"):
            key = f"results/{name}"
            self.assertNotEqual(summary["artifacts"][key], regenerated["artifacts"][key])
            self.assertEqual(self.client.get(regenerated["artifacts"][key]).status_code, 200)
        self.assertEqual(summary["artifacts"]["results/frequency_evaluation.json"],
                         regenerated["artifacts"]["results/frequency_evaluation.json"])
        config = yaml.safe_load((run / "experiment_config.yaml").read_text(encoding="utf-8"))
        config["completion_status"] = "running"
        (run / "experiment_config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
        self.assertEqual(self.client.get(f"{BASE}/runs/{run.name}/analysis").status_code, 409)
        self.assertFalse(self.manager.hardware_lock.locked())

    def test_unavailable_model_report_and_history_state_remain_valid_json(self):
        run = synthetic_run(self.root)
        for path in (run / "raw").glob("scope_f003_r*.npz"):
            path.unlink()
        response = self.client.post(f"{BASE}/runs/{run.name}/analysis",
            json=dict(revision=0, step_ids=["B01", "B02", "B03"]))
        self.assertEqual(response.status_code, 200, response.text)
        job = self.manager.get(response.json()["id"])
        self.wait(lambda: job.status not in ("queued", "running"))
        self.assertEqual(job.status, "completed", job.error)
        state = self.client.get(f"{BASE}/runs/{run.name}/analysis")
        self.assertEqual(state.status_code, 200, state.text)
        self.assertEqual(state.json()["steps"]["B03"]["status"], "completed")
        self.assertEqual(state.json()["options"], {})
        summary = self.client.get(f"{BASE}/runs/{run.name}/summary").json()
        report_response = self.client.get(summary["artifacts"]["results/frequency_response.json"])
        report = json.loads(report_response.text,
            parse_constant=lambda value: self.fail(f"报告包含非标准 JSON 常量 {value}"))
        self.assertFalse(report["theory_fit"]["available"])
        self.assertEqual(report["theory_fit"]["reason"], "至少需要 4 个正频率幅值点")
        self.assertEqual(report["frequency_count"], 3)
        self.assertEqual(summary["analysis_status"], "completed")


if __name__ == "__main__":
    unittest.main()
