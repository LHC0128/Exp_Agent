import asyncio
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "GUI"))

from backend.main import job_events
from backend.jobs import manager
from lab_workflows.common import ProgressEvent


class FakeRequest:
    headers = {"last-event-id": "1"}

    @staticmethod
    async def is_disconnected():
        return False


async def collect_stream(job_id: str) -> str:
    response = await job_events(job_id, FakeRequest())
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk.decode() if isinstance(chunk, bytes) else chunk)
    return "".join(chunks)


class JobEventStreamTests(unittest.TestCase):
    def test_stream_resumes_after_last_event_id_and_finishes(self):
        def runner(job):
            manager.event(job, ProgressEvent("work", "第二条事件", 50))
            return {"ok": True}

        job = manager.create("sse-test", runner, hardware_required=False)
        for _ in range(100):
            if job.status == "completed":
                break
            time.sleep(0.01)

        body = asyncio.run(collect_stream(job.id))
        self.assertIn("id: 2", body)
        self.assertIn("第二条事件", body)
        self.assertNotIn("任务开始", body)
        self.assertIn('"done": true', body)


if __name__ == "__main__":
    unittest.main()
