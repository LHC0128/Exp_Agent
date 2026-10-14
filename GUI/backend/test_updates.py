"""检查更新路由的无网络回归：解析、白名单过滤、包名校验与拒绝拉取。"""

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from . import updates
from .jobs import Job, JobManager
from .updates import UpdatesService, create_router

PIP_OUTDATED = json.dumps([
    {"name": "tqdm", "version": "4.67.0", "latest": "4.80.0"},
    {"name": "numpy", "version": "1.24.0", "latest": "2.1.0"},
    {"name": "requests", "version": "2.0.0", "latest": "2.5.0"},
])

NPM_OUTDATED = json.dumps({
    "@slidev/cli": {"current": "53.0.0", "wanted": "53.1.0", "latest": "54.0.0"},
    "@colbymchenry/codegraph": {"current": "1.6.2", "wanted": "1.7.0", "latest": "1.7.0"},
    "@playwright/cli": {"current": "0.1.22", "wanted": "0.2.0", "latest": "0.2.0"},
})


def _make_root() -> Path:
    root = Path(tempfile.mkdtemp())
    (root / "pyproject.toml").write_text(
        'dependencies = [\n  "tqdm>=4.67,<5",\n]\n', encoding="utf-8")
    (root / "requirements.txt").write_text(
        "# 分析依赖\nnumpy>=1.24\n", encoding="utf-8")
    return root


class UpdatesServiceTests(unittest.TestCase):
    def setUp(self):
        self.manager = JobManager()
        self.service = UpdatesService(lambda: _make_root(), self.manager)

    def test_pip_check_parses_and_annotates_sources(self):
        with patch.object(updates, "_run_command", return_value=(0, PIP_OUTDATED)):
            result = self.service.check_pip()
        by_name = {item["name"]: item for item in result["items"]}
        self.assertEqual(by_name["tqdm"]["source"], "pyproject.toml")
        self.assertEqual(by_name["numpy"]["source"], "requirements.txt")
        self.assertEqual(by_name["requests"]["source"], "venv 其他安装")
        self.assertEqual(self.service._checked["pip"]["tqdm"], "4.80.0")

    def test_npm_global_filters_whitelist(self):
        fake = lambda args, **kwargs: (0, NPM_OUTDATED)  # noqa: E731
        with patch.object(updates, "_run_command", side_effect=fake), \
             patch.object(updates.shutil, "which", return_value="npm"):
            result = self.service.check_npm_global()
        names = [item["name"] for item in result["items"]]
        self.assertIn("@colbymchenry/codegraph", names)
        self.assertIn("@playwright/cli", names)
        self.assertNotIn("@slidev/cli", names)

    def test_update_rejects_unknown_packages(self):
        with self.assertRaises(HTTPException) as raised:
            self.service.start_update("pip", ["totally-unknown-pkg"])
        self.assertEqual(raised.exception.status_code, 422)

    def test_update_rejects_empty_selection(self):
        with self.assertRaises(HTTPException) as raised:
            self.service.start_update("pip", [])
        self.assertEqual(raised.exception.status_code, 422)

    def test_update_rejects_without_check_cache(self):
        with self.assertRaises(HTTPException) as raised:
            self.service.start_update("npm-global", ["@playwright/cli"])
        self.assertEqual(raised.exception.status_code, 422)

    def test_update_pip_job_completes_and_runs_pip_check(self):
        self.service._checked["pip"] = {"numpy": "2.1.0"}
        outputs = [
            (0, "Successfully installed numpy-2.1.0"),
            (0, "No broken requirements found."),
        ]
        with patch.object(updates, "_run_command", side_effect=outputs) as run_mock:
            job_id = self.service.start_update("pip", ["numpy"])["id"]
            deadline = time.monotonic() + 5
            job = self.manager.jobs[job_id]
            while job.status not in {"completed", "failed", "cancelled"} and time.monotonic() < deadline:
                time.sleep(0.05)
            call_args = [call.args[0] for call in run_mock.call_args_list]
        self.assertEqual(job.status, "completed")
        self.assertEqual(job.result["updated"], ["numpy"])
        self.assertTrue(job.result["pip_check_ok"])
        # 升级命令必须包含包名，pip check 必须紧随其后。
        self.assertTrue(any("numpy" in args and "install" in args for args in call_args))
        self.assertTrue(any("check" in args for args in call_args))

    def test_git_pull_refuses_dirty_worktree(self):
        status = {"branch": "main", "upstream": "origin/main", "behind": 3,
                  "ahead": 0, "clean": False, "head": "abc123 demo", "error": None}
        job = Job(id="pull-test", kind="update:git-pull")
        with patch.object(UpdatesService, "check_git", return_value=status):
            with self.assertRaises(RuntimeError) as raised:
                self.service.git_pull(job)
        self.assertIn("未提交修改", str(raised.exception))

    def test_git_pull_refuses_when_local_ahead(self):
        status = {"branch": "main", "upstream": "origin/main", "behind": 3,
                  "ahead": 1, "clean": True, "head": "abc123 demo", "error": None}
        job = Job(id="pull-test", kind="update:git-pull")
        with patch.object(UpdatesService, "check_git", return_value=status):
            with self.assertRaises(RuntimeError) as raised:
                self.service.git_pull(job)
        self.assertIn("领先", str(raised.exception))

    def test_router_exposes_expected_routes(self):
        router = create_router(self.service)
        paths = {route.path for route in router.routes}
        expected = {
            "/api/updates/git", "/api/updates/pip", "/api/updates/npm-frontend",
            "/api/updates/npm-global", "/api/updates/npm-olcli",
            "/api/updates/skills", "/api/updates/toolchain",
            "/api/updates/git-pull", "/api/updates/pip", "/api/updates/npm-frontend",
            "/api/updates/npm-global", "/api/updates/npm-olcli",
        }
        self.assertTrue(expected.issubset(paths), paths)


if __name__ == "__main__":
    unittest.main()
