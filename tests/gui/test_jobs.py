import json
import sys
import time
import unittest
from pathlib import Path
from threading import Event, Thread

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

    def test_hardware_jobs_queue_instead_of_failing(self):
        manager = JobManager()
        release = Event()
        first = manager.create(
            "first",
            lambda _job: release.wait(1),
        )
        for _ in range(100):
            if first.status == "running":
                break
            time.sleep(0.01)
        self.assertEqual(first.status, "running")

        second = manager.create("second", lambda _job: {"value": 2})
        time.sleep(0.05)
        self.assertEqual(second.status, "queued")
        self.assertTrue(any(event["message"] == "等待硬件空闲" for event in second.events))

        release.set()
        self.wait_until_finished(first)
        self.wait_until_finished(second)
        self.assertEqual(second.status, "completed")
        self.assertEqual(second.result, {"value": 2})

    def test_queued_status_stays_queued_until_hardware_is_acquired(self):
        """排队期间详情页、任务列表与 SSE 使用的状态都保持 queued。"""
        manager = JobManager()
        release = Event()
        first = manager.create("first", lambda _job: release.wait(1))
        for _ in range(100):
            if first.status == "running":
                break
            time.sleep(0.01)

        second = manager.create("second", lambda _job: {"value": 2})
        for _ in range(100):
            if manager.queued_hardware_count() == 1:
                break
            time.sleep(0.01)
        self.assertEqual(second.status, "queued")
        self.assertEqual(second.public()["status"], "queued")
        self.assertEqual(second.summary()["status"], "queued")
        self.assertEqual(second.started_at, None)
        stages = [event["stage"] for event in second.events]
        self.assertIn("queued", stages)
        self.assertNotIn("start", stages)
        events, status = manager.stream_state(second, 0)
        self.assertEqual(status, "queued")
        self.assertEqual([event["stage"] for event in events], stages)

        release.set()
        self.wait_until_finished(first)
        self.wait_until_finished(second)
        # 完成序列为 queued -> running -> completed：queued 事件先于 start。
        self.assertEqual(second.status, "completed")
        self.assertEqual(second.events[0]["stage"], "queued")
        self.assertEqual(second.events[1]["stage"], "start")

    def test_short_hardware_lock_refuses_while_jobs_queue_or_run(self):
        manager = JobManager()
        release = Event()
        first = manager.create("first", lambda _job: release.wait(1))
        for _ in range(100):
            if first.status == "running":
                break
            time.sleep(0.01)
        # 长任务运行时锁不可得。
        self.assertFalse(manager.try_acquire_short_hardware())
        second = manager.create("second", lambda _job: {"value": 2})
        for _ in range(100):
            if manager.queued_hardware_count() == 1:
                break
            time.sleep(0.01)
        # 已有排队任务时同样不可得（即使马上轮到它）。
        self.assertFalse(manager.try_acquire_short_hardware(timeout=0.05))
        release.set()
        self.wait_until_finished(first)
        self.wait_until_finished(second)
        self.assertEqual(second.status, "completed")
        # 队列清空且无任务运行后短时操作可以获得锁。
        self.assertTrue(manager.try_acquire_short_hardware())
        manager.hardware_lock.release()

    def test_short_hardware_ops_never_jump_the_queue(self):
        """并发短时接口尝试不能插队到已排队的硬件任务之前。"""
        manager = JobManager()
        release = Event()
        first = manager.create("first", lambda _job: release.wait(1))
        for _ in range(100):
            if first.status == "running":
                break
            time.sleep(0.01)
        second = manager.create("second", lambda _job: {"value": 2})
        for _ in range(100):
            if manager.queued_hardware_count() == 1:
                break
            time.sleep(0.01)

        acquired: list[bool] = []
        threads = [
            Thread(
                target=lambda: acquired.append(
                    manager.try_acquire_short_hardware(timeout=0.05)
                )
            )
            for _ in range(8)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2)
        # 排队期间所有并发短时尝试都失败，不能插队。
        self.assertFalse(any(acquired))
        self.assertEqual(second.status, "queued")

        release.set()
        self.wait_until_finished(first)
        self.wait_until_finished(second)
        # FIFO 语义保持：排队的第二个任务先于短时操作完成。
        self.assertEqual(second.status, "completed")
        self.assertTrue(manager.try_acquire_short_hardware())
        manager.hardware_lock.release()

    def test_queued_hardware_job_can_be_cancelled(self):
        manager = JobManager()
        release = Event()
        first = manager.create("first", lambda _job: release.wait(1))
        for _ in range(100):
            if first.status == "running":
                break
            time.sleep(0.01)

        second = manager.create("second", lambda _job: {"value": 2})
        time.sleep(0.05)
        self.assertEqual(second.status, "queued")

        manager.cancel(second.id)
        self.wait_until_finished(second)
        self.assertEqual(second.status, "cancelled")
        self.assertEqual(second.message, "排队期间已取消")

        release.set()
        self.wait_until_finished(first)
        self.assertEqual(first.status, "completed")

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
