"""检查更新与执行软件更新的路由。

覆盖：Git 仓库、Python 依赖（全 venv）、前端 npm、全局 CLI 白名单、
olcli 隔离 npm 前缀、AI Skills 清单与工具链版本。
更新任务以后台 Job 运行（hardware_required=False），不占用硬件互斥队列。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from lab_workflows.common import ProgressEvent, WorkflowCancelled

from .jobs import Job, JobManager

CHECK_TIMEOUT = 180
UPDATE_TIMEOUT = 900
NPM_TIMEOUT = 300
GIT_TIMEOUT = 60

# 全局 CLI 分区只展示这些包；slidev / marp / opencode 等不列出不检查。
GLOBAL_CLI_WHITELIST = (
    "@colbymchenry/codegraph",
    "@playwright/cli",
    "@agegr/pi-web",
    "mineru-open-api",
)
OLCLI_PREFIX = Path.home() / ".local" / "olcli-ustc"

_REQUIREMENT_NAME = re.compile(r"^\s*\"?'?([A-Za-z0-9][A-Za-z0-9._-]*)")


def _run_command(
    args: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: float = CHECK_TIMEOUT,
    cancellation: Any = None,
) -> tuple[int, str]:
    """以列表参数执行子进程（不经 shell），返回 (返回码, 合并输出)。

    协作式取消与超时都会 terminate 子进程；读输出放在独立线程，
    避免阻塞的主循环错过取消检查点。
    """
    process = subprocess.Popen(
        args,
        cwd=None if cwd is None else str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    lines: list[str] = []

    def _read() -> None:
        if process.stdout is not None:
            for line in process.stdout:
                lines.append(line.rstrip())

    reader = threading.Thread(target=_read, daemon=True)
    reader.start()
    deadline = time.monotonic() + timeout
    cancelled = False
    timed_out = False
    try:
        while process.poll() is None:
            if cancellation is not None and cancellation.cancelled:
                cancelled = True
                break
            if time.monotonic() > deadline:
                timed_out = True
                break
            time.sleep(0.2)
    finally:
        if cancelled or timed_out:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
    if cancelled:
        raise WorkflowCancelled("更新任务已取消")
    if timed_out:
        raise TimeoutError(f"命令超时（{timeout:.0f}s）：{args[0]}")
    reader.join(timeout=10)
    return process.returncode, "\n".join(lines)


def _try_run(args: list[str], *, timeout: float = 30, **kwargs: Any) -> tuple[int, str]:
    """吞掉执行异常的 _run_command，供可选的版本探测使用。"""
    try:
        return _run_command(args, timeout=timeout, **kwargs)
    except (TimeoutError, OSError, ValueError) as exc:
        return 1, str(exc)


class UpdatesService:
    """各分区的检查逻辑与更新任务执行。"""

    def __init__(self, root_getter: Callable[[], Path], manager: JobManager) -> None:
        self._root_getter = root_getter
        self.manager = manager
        # scope -> {包名: 最新版本}，更新接口只接受最近一次检查结果中的包名。
        self._checked: dict[str, dict[str, str]] = {}

    @property
    def root(self) -> Path:
        return self._root_getter()

    # ---------------------------------------------------------------- git

    def check_git(self) -> dict[str, Any]:
        root = self.root
        branch, upstream, head, clean = None, None, None, None
        behind = ahead = None
        error: str | None = None
        if shutil.which("git") is None:
            error = "未找到 git 命令"
        else:
            _, branch = _try_run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=root, timeout=GIT_TIMEOUT)
            rc, fetch_out = _try_run(["git", "fetch", "--quiet"], cwd=root, timeout=GIT_TIMEOUT)
            if rc != 0:
                error = f"git fetch 失败：{fetch_out.strip()[:300]}"
            _, upstream = _try_run(
                ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
                cwd=root, timeout=GIT_TIMEOUT,
            )
            if upstream:
                _, behind_out = _try_run(["git", "rev-list", "--count", "HEAD..@{u}"], cwd=root, timeout=GIT_TIMEOUT)
                _, ahead_out = _try_run(["git", "rev-list", "--count", "@{u}..HEAD"], cwd=root, timeout=GIT_TIMEOUT)
                behind = int(behind_out.strip()) if behind_out.strip().isdigit() else None
                ahead = int(ahead_out.strip()) if ahead_out.strip().isdigit() else None
            _, head = _try_run(["git", "log", "-1", "--pretty=%h %s"], cwd=root, timeout=GIT_TIMEOUT)
            _, status_out = _try_run(["git", "status", "--porcelain"], cwd=root, timeout=GIT_TIMEOUT)
            clean = not status_out.strip()
        return {
            "branch": branch and branch.strip() or None,
            "upstream": upstream and upstream.strip() or None,
            "behind": behind,
            "ahead": ahead,
            "clean": clean,
            "head": head and head.strip() or None,
            "error": error,
        }

    def git_pull(self, job: Job) -> dict[str, Any]:
        status = self.check_git()
        if not status["upstream"]:
            raise RuntimeError("当前分支没有配置上游，无法拉取，请手动处理。")
        if status["error"] and status["behind"] is None:
            raise RuntimeError(f"无法确认远端状态：{status['error']}")
        if not status["clean"]:
            raise RuntimeError("工作区有未提交修改，拒绝拉取；请先提交或暂存（stash）后重试。")
        if not status["behind"]:
            return {"updated": False, "message": "已与远端同步，无需拉取。"}
        if status["ahead"]:
            raise RuntimeError("本地有领先提交，直接拉取会产生合并，请手动 git pull 处理。")
        self.manager.event(job, ProgressEvent("update", "正在执行 git pull --ff-only", 10))
        _, output = _run_command(
            ["git", "pull", "--ff-only"], cwd=self.root,
            timeout=UPDATE_TIMEOUT, cancellation=job.cancellation,
        )
        after = self.check_git()
        return {"updated": True, "message": "拉取完成。", "output_tail": output[-2000:], "head": after["head"]}

    # ---------------------------------------------------------------- pip

    def _declared_sources(self) -> dict[str, str]:
        """收集依赖声明文件中的包名 -> 来源标签。"""
        sources: dict[str, str] = {}
        files = [
            ("pyproject.toml", "pyproject.toml"),
            ("GUI/requirements.txt", "GUI/requirements.txt"),
            ("requirements.txt", "requirements.txt"),
        ]
        for label, rel in files:
            path = self.root / rel
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            for raw in text.splitlines():
                line = raw.split("#", 1)[0].strip().strip(",").strip('"').strip("'")
                if not line or line.startswith(("-", "[", "}")):
                    continue
                match = _REQUIREMENT_NAME.match(line)
                if match:
                    sources.setdefault(match.group(1).lower(), label)
        return sources

    def check_pip(self) -> dict[str, Any]:
        _, output = _run_command(
            [sys.executable, "-m", "pip", "list", "--outdated", "--format", "json",
             "--disable-pip-version-check"],
            timeout=CHECK_TIMEOUT,
        )
        try:
            rows = json.loads(output) if output.strip() else []
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"pip 输出无法解析：{output[-500:]}") from exc
        sources = self._declared_sources()
        items = []
        cache: dict[str, str] = {}
        for row in rows:
            name = str(row.get("name") or "").strip()
            current = str(row.get("version") or "")
            latest = str(row.get("latest") or row.get("latest_version") or "")
            if not name or not latest:
                continue
            cache[name] = latest
            items.append({
                "name": name,
                "current": current,
                "latest": latest,
                "source": sources.get(name.lower(), "venv 其他安装"),
            })
        self._checked["pip"] = cache
        return {"items": items}

    def update_pip(self, job: Job, packages: list[str]) -> dict[str, Any]:
        self.manager.event(job, ProgressEvent("update", f"pip install -U {len(packages)} 个包", 10))
        _, install_out = _run_command(
            [sys.executable, "-m", "pip", "install", "--upgrade", "--disable-pip-version-check", *packages],
            timeout=UPDATE_TIMEOUT, cancellation=job.cancellation,
        )
        self.manager.event(job, ProgressEvent("check", "运行 pip check 验证依赖一致性", 80))
        check_rc, check_out = _try_run(
            [sys.executable, "-m", "pip", "check", "--disable-pip-version-check"], timeout=120,
        )
        ok = check_rc == 0
        return {
            "updated": packages,
            "pip_check_ok": ok,
            "pip_check": check_out.strip()[-2000:],
            "output_tail": install_out[-2000:],
            "note": "若更新了 fastapi / uvicorn / pydantic，需重启 GUI 后端生效。",
        }

    # ---------------------------------------------------------------- npm

    def _npm_outdated(self, cwd: Path | None, global_: bool, prefix: Path | None = None) -> dict[str, Any]:
        npm = shutil.which("npm")
        if npm is None:
            raise RuntimeError("未找到 npm 命令")
        env = None
        if prefix is not None:
            # 仅命令级环境变量，绝不写入用户级 .npmrc（见 olcli skill 的 npm 卫生规则）。
            env = {**os.environ, "npm_config_prefix": str(prefix)}
        args = [npm, "outdated", "--json"]
        if global_:
            args.append("-g")
        _, output = _run_command(args, cwd=cwd, env=env, timeout=NPM_TIMEOUT)
        try:
            return json.loads(output) if output.strip() else {}
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"npm 输出无法解析：{output[-500:]}") from exc

    def _npm_section(self, scope: str, cwd: Path | None, global_: bool,
                     prefix: Path | None, whitelist: tuple[str, ...] | None) -> dict[str, Any]:
        data = self._npm_outdated(cwd, global_, prefix)
        items = []
        cache: dict[str, str] = {}
        for name, info in sorted(data.items()):
            if not isinstance(info, dict):
                continue
            if whitelist is not None and name not in whitelist:
                continue
            latest = str(info.get("latest") or "")
            current = str(info.get("current") or "")
            if not latest or not current:
                continue
            cache[name] = latest
            items.append({
                "name": name,
                "current": current,
                "wanted": str(info.get("wanted") or ""),
                "latest": latest,
            })
        self._checked[scope] = cache
        return {"items": items}

    def check_npm_frontend(self) -> dict[str, Any]:
        return self._npm_section("npm-frontend", self.root / "GUI" / "frontend", False, None, None)

    def check_npm_global(self) -> dict[str, Any]:
        return self._npm_section("npm-global", None, True, None, GLOBAL_CLI_WHITELIST)

    def check_npm_olcli(self) -> dict[str, Any]:
        return self._npm_section("npm-olcli", None, True, OLCLI_PREFIX, None)

    def update_npm(self, job: Job, scope: str, packages: list[str]) -> dict[str, Any]:
        npm = shutil.which("npm")
        if npm is None:
            raise RuntimeError("未找到 npm 命令")
        env = None
        cwd = None
        args = [npm, "install"]
        if scope == "npm-frontend":
            cwd = self.root / "GUI" / "frontend"
        else:
            args.append("-g")
        if scope == "npm-olcli":
            env = {**os.environ, "npm_config_prefix": str(OLCLI_PREFIX)}
        args.extend(f"{name}@latest" for name in packages)
        self.manager.event(job, ProgressEvent("update", f"npm install {len(packages)} 个包", 10))
        _, output = _run_command(
            args, cwd=cwd, env=env, timeout=UPDATE_TIMEOUT, cancellation=job.cancellation,
        )
        note = "更新后请重新检查本分区；开发模式需重启 Vite（或刷新页面），dist 部署需重新 npm run build。"
        if scope == "npm-global" and "@agegr/pi-web" in packages:
            note = "pi 本体已更新，需重启 pi 后生效。" + note
        if scope == "npm-olcli":
            note = "已安装到 ~/.local/olcli-ustc 隔离前缀（仅命令级 npm_config_prefix）。"
        return {"updated": packages, "output_tail": output[-2000:], "note": note}

    # ---------------------------------------------------------------- skills

    def check_skills(self) -> dict[str, Any]:
        skills: dict[str, dict[str, Any]] = {}
        for rel in (".agents/skills",):
            base = self.root / rel
            try:
                entries = sorted(p for p in base.iterdir() if (p / "SKILL.md").is_file())
            except OSError:
                continue
            for entry in entries:
                try:
                    text = (entry / "SKILL.md").read_text(encoding="utf-8")
                except OSError:
                    continue
                meta = self._frontmatter(text)
                name = str(meta.get("name") or entry.name)
                description = str(meta.get("description") or "")
                if name in skills:
                    skills[name]["dirs"].append(rel)
                else:
                    skills[name] = {"name": name, "description": description, "dirs": [rel]}
        return {"items": list(skills.values()), "note": "Skills 为本地目录，无版本源，仅列出清单。"}

    @staticmethod
    def _frontmatter(text: str) -> dict[str, Any]:
        if not text.startswith("---"):
            return {}
        parts = text.split("---", 2)
        if len(parts) < 3:
            return {}
        try:
            data = yaml.safe_load(parts[1]) or {}
        except yaml.YAMLError:
            return {}
        return data if isinstance(data, dict) else {}

    # ---------------------------------------------------------------- toolchain

    def check_toolchain(self) -> dict[str, Any]:
        def version(label: str, args: list[str]) -> dict[str, Any]:
            # Windows 上 npm 等是 .cmd，必须先经 which 解析完整路径。
            resolved = shutil.which(args[0])
            if resolved is None:
                return {"name": label, "version": None, "error": f"未找到 {args[0]} 命令"}
            _, out = _try_run([resolved, *args[1:]], timeout=30)
            value = out.strip().splitlines()[0] if out.strip() else ""
            return {"name": label, "version": value or None,
                    "error": None if value else f"无法获取 {label} 版本"}

        return {
            "items": [
                {"name": "python", "version": sys.version.split()[0], "error": None},
                version("pip", [sys.executable, "-m", "pip", "--version"]),
                version("node", ["node", "--version"]),
                version("npm", ["npm", "--version"]),
                version("git", ["git", "--version"]),
            ]
        }

    # ---------------------------------------------------------------- jobs

    def start_update(self, scope: str, packages: list[str] | None) -> dict[str, Any]:
        if scope == "git-pull":
            job = self.manager.create(
                "update:git-pull", self.git_pull, hardware_required=False,
            )
            return job.public()
        allowed = self._checked.get(scope)
        if not packages:
            raise HTTPException(422, "未选择任何包。")
        if not allowed:
            raise HTTPException(422, "没有可用的检查结果，请先重新检查该分区。")
        unknown = [name for name in packages if name not in allowed]
        if unknown:
            raise HTTPException(422, f"包不在最近检查结果中，请先重新检查：{', '.join(unknown)}")
        selected = list(packages)

        def runner(job: Job) -> dict[str, Any]:
            if scope == "pip":
                return self.update_pip(job, selected)
            return self.update_npm(job, scope, selected)

        job = self.manager.create(f"update:{scope}", runner, hardware_required=False)
        return job.public()


class UpdateBody(BaseModel):
    packages: list[str] = []


def create_router(service: UpdatesService) -> APIRouter:
    router = APIRouter(prefix="/api/updates")

    @router.get("/git")
    def git_status():
        try:
            return service.check_git()
        except (TimeoutError, OSError) as exc:
            raise HTTPException(502, f"Git 检查失败：{exc}") from exc

    @router.get("/pip")
    def pip_status():
        try:
            return service.check_pip()
        except (TimeoutError, OSError, RuntimeError) as exc:
            raise HTTPException(502, f"pip 检查失败：{exc}") from exc

    @router.get("/npm-frontend")
    def npm_frontend_status():
        try:
            return service.check_npm_frontend()
        except (TimeoutError, OSError, RuntimeError) as exc:
            raise HTTPException(502, f"npm 检查失败：{exc}") from exc

    @router.get("/npm-global")
    def npm_global_status():
        try:
            return service.check_npm_global()
        except (TimeoutError, OSError, RuntimeError) as exc:
            raise HTTPException(502, f"npm 检查失败：{exc}") from exc

    @router.get("/npm-olcli")
    def npm_olcli_status():
        try:
            return service.check_npm_olcli()
        except (TimeoutError, OSError, RuntimeError) as exc:
            raise HTTPException(502, f"npm 检查失败：{exc}") from exc

    @router.get("/skills")
    def skills_status():
        return service.check_skills()

    @router.get("/toolchain")
    def toolchain_status():
        return service.check_toolchain()

    @router.post("/git-pull")
    def git_pull():
        return service.start_update("git-pull", None)

    @router.post("/pip")
    def pip_update(body: UpdateBody):
        return service.start_update("pip", body.packages)

    @router.post("/npm-frontend")
    def npm_frontend_update(body: UpdateBody):
        return service.start_update("npm-frontend", body.packages)

    @router.post("/npm-global")
    def npm_global_update(body: UpdateBody):
        return service.start_update("npm-global", body.packages)

    @router.post("/npm-olcli")
    def npm_olcli_update(body: UpdateBody):
        return service.start_update("npm-olcli", body.packages)

    return router
