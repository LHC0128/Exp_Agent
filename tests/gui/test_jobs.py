import json
import sys
import time
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "GUI"))

from backend.jobs import JobManager
from lab_workflows.common import ProgressEvent


class JobManagerTests(unittest.TestCase):
    def test_job_records_progress_and_result(self):
        manager = JobManager()

        def runner(job):
            manager.event(job, ProgressEvent("work", "正在测试", 50))
            return {"ok": True}

        job = manager.create("test", runner)
        for _ in range(100):
            if job.status in {"completed", "failed"}:
                break
            time.sleep(0.01)
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
        for _ in range(100):
            if job.status in {"completed", "failed"}:
                break
            time.sleep(0.01)
        public = job.public()
        self.assertEqual(public["result"]["timestamp"], 123456)
        self.assertEqual(public["result"]["samples"], [1.0, 2.0])
        json.dumps(public)


if __name__ == "__main__":
    unittest.main()
