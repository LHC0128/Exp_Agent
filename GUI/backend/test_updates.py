"""检查更新路由的无网络回归：解析、白名单过滤、包名校验与拒绝拉取。"""

import json
import tempfile
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
    (root / "constraints").mkdir()
    (root / "constraints/win-py312.lock.txt").write_text(
        "# 锁文件\nnumpy==2.5.4\n", encoding="utf-8")
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
        self.assertEqual(by_name["numpy"]["source"], "constraints/win-py312.lock.txt")
        self.assertEqual(by_name["requests"]["source"], "venv 其他安装")

    def test_npm_global_filters_whitelist(self):
        fake = lambda args, **kwargs: (0, NPM_OUTDATED)  # noqa: E731
        with patch.object(updates, "_run_command", side_effect=fake), \
             patch.object(updates.shutil, "which", return_value="npm"):
            result = self.service.check_npm_global()
        names = [item["name"] for item in result["items"]]
        self.assertIn("@colbymchenry/codegraph", names)
        self.assertIn("@playwright/cli", names)
        self.assertNotIn("@slidev/cli", names)

    def test_pip_check_is_read_only_and_points_to_lock_workflow(self):
        with patch.object(updates, "_run_command", return_value=(0, PIP_OUTDATED)):
            result = self.service.check_pip()
        self.assertIn("tools/upgrade_lock.ps1", result["note"])
        self.assertNotIn("pip", self.service._checked)

    def test_pip_update_is_rejected_even_with_selection(self):
        with patch.object(updates, "_run_command", side_effect=AssertionError("不得执行 pip install")):
            with self.assertRaises(HTTPException) as raised:
                self.service.start_update("pip", ["numpy"])
        self.assertEqual(raised.exception.status_code, 422)
        self.assertIn("tools/upgrade_lock.ps1", raised.exception.detail)

    def test_update_rejects_unknown_packages(self):
        self.service._checked["npm-global"] = {"@playwright/cli": "0.2.0"}
        with self.assertRaises(HTTPException) as raised:
            self.service.start_update("npm-global", ["totally-unknown-pkg"])
        self.assertEqual(raised.exception.status_code, 422)

    def test_update_rejects_empty_selection(self):
        with self.assertRaises(HTTPException) as raised:
            self.service.start_update("npm-global", [])
        self.assertEqual(raised.exception.status_code, 422)

    def test_update_rejects_without_check_cache(self):
        with self.assertRaises(HTTPException) as raised:
            self.service.start_update("npm-global", ["@playwright/cli"])
        self.assertEqual(raised.exception.status_code, 422)

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

    def test_skills_scan_uses_only_agents_and_preserves_fields(self):
        with tempfile.TemporaryDirectory(prefix="updates-skills-") as temporary:
            root = Path(temporary)
            fixtures = {
                ".agents/skills/a/SKILL.md": "---\nname: shared\ndescription: canonical\n---\n",
                ".agents/skills/fallback/SKILL.md": "没有 frontmatter\n",
                ".claude/skills/a/SKILL.md": "---\nname: shared\ndescription: retired\n---\n",
                ".claude/skills/old-only/SKILL.md": "---\nname: retired-only\n---\n",
                ".agents/disable skill/disabled/SKILL.md": "---\nname: disabled\n---\n",
                ".agents/skill-backups/backup/SKILL.md": "---\nname: backup\n---\n",
                "retired-only/.claude/skills/old/SKILL.md": "---\nname: old\n---\n",
            }
            for relative, content in fixtures.items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            real_iterdir = Path.iterdir

            def iterdir(path):
                self.assertEqual(path, root / ".agents/skills", "scanner 不得枚举退役或停用目录")
                return real_iterdir(path)

            with patch.object(updates, "_run_command", side_effect=AssertionError("scanner 不能执行命令")), \
                    patch.object(Path, "iterdir", iterdir):
                result = UpdatesService(lambda: root, self.manager).check_skills()
            self.assertEqual(set(result), {"items", "note"})
            self.assertEqual(result["items"], [
                {"name": "shared", "description": "canonical", "dirs": [".agents/skills"]},
                {"name": "fallback", "description": "", "dirs": [".agents/skills"]},
            ])
            self.assertEqual(result["note"], "Skills 为本地目录，无版本源，仅列出清单。")
            with patch.object(updates, "_run_command", side_effect=AssertionError("scanner 不能执行命令")):
                empty = UpdatesService(lambda: root / "retired-only", self.manager).check_skills()
            self.assertEqual(empty, {"items": [], "note": result["note"]})

    def test_router_exposes_expected_routes(self):
        router = create_router(self.service)
        routes = {(route.path, method) for route in router.routes for method in route.methods}
        expected = {
            *(("/api/updates/" + name, "GET") for name in (
                "git", "pip", "npm-frontend", "npm-global", "npm-olcli", "skills", "toolchain")),
            *(("/api/updates/" + name, "POST") for name in (
                "git-pull", "npm-frontend", "npm-global", "npm-olcli")),
        }
        self.assertTrue(expected.issubset(routes), routes)
        # Python 依赖只读，不提供就地升级接口。
        self.assertNotIn(("/api/updates/pip", "POST"), routes)


if __name__ == "__main__":
    unittest.main()
