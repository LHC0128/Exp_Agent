import json
import sys
import time
import unittest
from pathlib import Path
from threading import Event

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "GUI"))

from backend.jobs import JobManager
from lab_workflows.common import ProgressEvent


class JobManagerTests(unittest.TestCase):
    @staticmethod
    def wait_until_finished(job):
        for _ in range(100):
            if job.status in {"completed", "failed", "cancelled"}:
                return
            time.sleep(0.01)
        raise AssertionError("任务未在预期时间内结束")

    def test_job_records_progress_and_result(self):
        manager = JobManager()

        def runner(job):
            manager.event(job, ProgressEvent("work", "正在测试", 50))
            return {"ok": True}

        job = manager.create("test", runner)
        self.wait_until_finished(job)
        self.assertEqual(job.status, "completed")
        self.assertEqual(job.result, {"ok": True})
        self.assertTrue(any(event["message"] == "正在测试" for event in job.events))

    def test_job_public_converts_numpy_values_to_json_types(self):
        manager = JobManager()

        def runner(job):
            return {
                "timestamp": np.uint64(123456),
                "samples": np.array([1.0, 2.0]),
            }

        job = manager.create("numpy-result", runner)
        self.wait_until_finished(job)
        public = job.public()
        self.assertEqual(public["result"]["timestamp"], 123456)
        self.assertEqual(public["result"]["samples"], [1.0, 2.0])
        json.dumps(public)

    def test_prunes_oldest_terminal_jobs(self):
        manager = JobManager(max_terminal_jobs=2)
        jobs = []
        for index in range(3):
            job = manager.create(
                f"job-{index}",
                lambda _job, value=index: {"value": value},
                hardware_required=False,
            )
            self.wait_until_finished(job)
            jobs.append(job)

        with self.assertRaisesRegex(KeyError, "未知任务"):
            manager.get(jobs[0].id)
        self.assertEqual(
            [item["id"] for item in manager.list_public()],
            [jobs[2].id, jobs[1].id],
        )

    def test_never_prunes_active_jobs(self):
        manager = JobManager(max_terminal_jobs=0)
        release = Event()
        active = manager.create(
            "active",
            lambda _job: release.wait(1),
            hardware_required=False,
        )
        for _ in range(100):
            if active.status == "running":
                break
            time.sleep(0.01)

        finished = manager.create(
            "finished",
            lambda _job: None,
            hardware_required=False,
        )
        self.wait_until_finished(finished)

        self.assertIs(manager.get(active.id), active)
        with self.assertRaisesRegex(KeyError, "未知任务"):
            manager.get(finished.id)
        release.set()
        self.wait_until_finished(active)


if __name__ == "__main__":
    unittest.main()
