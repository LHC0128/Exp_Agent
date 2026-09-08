"""历史删除只操作选定目录，并与任务注册互斥。"""

import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
import unittest
from unittest.mock import patch

from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "GUI"))
from backend import main
from backend.jobs import Job, JobManager


class RunDeleteTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.base = self.root / "data" / "Scope_Capture"
        for name in ("selected", "other"):
            (self.base / name / "raw").mkdir(parents=True)
            (self.base / name / "raw" / "waveform.npz").write_bytes(b"fixture")
        self.manager = JobManager()
        self.patches = [patch.object(main, "ROOT", self.root), patch.object(main, "manager", self.manager)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def test_delete_selected_directory_only(self):
        self.assertTrue(main.delete_run("scope-capture", "selected")["ok"])
        self.assertFalse((self.base / "selected").exists())
        self.assertTrue((self.base / "other" / "raw" / "waveform.npz").exists())

    def test_reject_path_traversal_and_nested_path(self):
        for name in ("..", ".", "../other", "selected/raw", "selected\\raw", "C:other"):
            with self.subTest(name=name), self.assertRaises(HTTPException) as error:
                main.delete_run("scope-capture", name)
            self.assertEqual(error.exception.status_code, 400)

    def test_reject_active_capture_and_analysis(self):
        for kind in ("experiment:scope-capture", "analysis:scope-capture:selected"):
            self.manager.jobs = {"active": Job(id="active", kind=kind)}
            with self.assertRaises(HTTPException) as error:
                main.delete_run("scope-capture", "selected")
            self.assertEqual(error.exception.status_code, 409)
        self.assertTrue((self.base / "selected").exists())

    def test_completed_jobs_do_not_block(self):
        self.manager.jobs["done"] = Job(id="done", kind="experiment:scope-capture", status="completed")
        self.assertTrue(main.delete_run("scope-capture", "selected")["ok"])

    def test_analysis_cannot_register_during_delete(self):
        entered, release, attempted, finished = Event(), Event(), Event(), Event()
        import shutil
        original = shutil.rmtree
        errors = []

        def remove(path):
            entered.set()
            self.assertTrue(release.wait(3))
            original(path)

        def analysis():
            attempted.set()
            try:
                main.analyze_run("selected", main.AnalysisBody(experiment_id="scope-capture"))
            except HTTPException as exc:
                errors.append(exc.status_code)
            finally:
                finished.set()

        with patch.object(main.shutil, "rmtree", side_effect=remove):
            deletion = Thread(target=main.delete_run, args=("scope-capture", "selected"))
            deletion.start()
            self.assertTrue(entered.wait(3))
            worker = Thread(target=analysis)
            worker.start()
            self.assertTrue(attempted.wait(3))
            self.assertFalse(finished.wait(0.05))
            release.set()
            deletion.join(3)
            worker.join(3)
        self.assertEqual(errors, [404])
        self.assertEqual(self.manager.jobs, {})

    def test_link_to_outside_is_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        link = self.base / "linked"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("当前 Windows 用户未启用符号链接权限")
        try:
            with self.assertRaises(HTTPException):
                main.delete_run("scope-capture", "linked")
            self.assertTrue(outside.exists())
        finally:
            link.unlink()


if __name__ == "__main__":
    unittest.main()
