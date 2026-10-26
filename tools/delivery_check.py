"""只读汇总交付元数据与当前任务证据；不运行验证，也不决定上传或整仓就绪。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import subprocess
import tempfile

from .checkpoint import inspect_task


ROOT = Path(__file__).resolve().parent.parent
_COMMANDS = {
    "root": ("rev-parse", "--show-toplevel"),
    "head": ("rev-parse", "--verify", "--quiet", "HEAD"),
    "head_path": ("rev-parse", "--git-path", "HEAD"),
    "index_path": ("rev-parse", "--git-path", "index"),
    "status": ("-c", "core.fsmonitor=false", "status", "--porcelain=v1", "-z", "--untracked-files=normal"),
    "ignored": ("check-ignore", "-z", "--stdin"),
}
_LOCAL_DIRS = {".agents", ".claude", ".codex", ".vscode", ".codegraph", ".zcode", ".pi"}


def _git(root: Path, key: str, runner, stdin: bytes = b"") -> bytes | None:
    # 固定命令，不接受用户提供的 Git 子命令；临时禁用 fsmonitor，不写配置。
    result = runner(["git", *_COMMANDS[key]], cwd=root,
                    env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"}, input=stdin,
                    capture_output=True, timeout=10, check=False)
    accepted = (0, 1) if key in ("head", "ignored") else (0,)
    if result.returncode not in accepted:
        raise ValueError(f"Git {key} 查询失败: {os.fsdecode(result.stderr).rstrip()}")
    return None if key == "head" and result.returncode == 1 else result.stdout


def _file_signature(path: Path) -> dict:
    if not path.exists():
        return {"path": str(path), "sha256": None, "mtime_ns": None}
    raw = path.read_bytes()
    return {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest(),
            "mtime_ns": path.stat().st_mtime_ns}


def _snapshot(root: Path, runner) -> dict:
    raw = _git(root, "head", runner)
    result = {"head_commit": os.fsdecode(raw).rstrip("\r\n") if raw is not None else None}
    # HEAD 文件内容捕获同一提交上的分支切换，提交 ID 则捕获引用推进。
    for key in ("head_path", "index_path"):
        path = Path(os.fsdecode(_git(root, key, runner)).rstrip("\r\n"))
        result[key.removesuffix("_path")] = _file_signature((root / path).resolve())
    return result


def _paths(raw: bytes) -> list[str]:
    if raw and not raw.endswith(b"\0"):
        raise ValueError("Git NUL 输出不完整")
    return [os.fsdecode(value) for value in raw[:-1].split(b"\0")] if raw else []


def _status(raw: bytes) -> tuple[list[dict], list[dict], list[dict]]:
    fields, changed, staged, untracked = _paths(raw), [], [], []
    index = 0
    while index < len(fields):
        field = fields[index]
        index += 1
        if len(field) < 4 or field[2] != " ":
            raise ValueError("Git porcelain v1 记录无效")
        xy, path = field[:2], field[3:]
        item = {"path": path, "xy": xy}
        if "R" in xy or "C" in xy:
            if index >= len(fields) or not fields[index]:
                raise ValueError("Git rename/copy 缺少原路径")
            item["original_path"] = fields[index]
            index += 1
        if xy == "??":
            untracked.append(item)
        elif xy != "!!":
            changed.append(item)
            if xy[0] != " ":
                staged.append(item)
    return changed, staged, untracked


def _relative(value: str) -> str:
    name = value.replace("\\", "/")
    parts = PurePosixPath(name).parts
    if (not parts or PurePosixPath(name).is_absolute() or PureWindowsPath(name).drive
            or ".." in parts or any(c in name for c in ":*?[]\x00")
            or any(p.endswith((".", " ")) or PureWindowsPath(p).is_reserved() for p in parts)):
        raise ValueError(f"必须是明确的根目录相对路径: {value}")
    return PurePosixPath(name).as_posix()


def _local_only(name: str) -> bool:
    parts = [p.casefold() for p in PurePosixPath(name).parts]
    return (any(p in _LOCAL_DIRS for p in parts) or parts[-1] in ("agents.md", "claude.md")
            or name.casefold() in ("skills-lock.json", ".mcp.json", ".zcodeignore"))


def _report_path(root: Path, value: str) -> Path:
    name = _relative(value)
    parts = PurePosixPath(name).parts
    path = root / name
    if (len(parts) != 3 or parts[:2] != (".cache", "delivery") or path.suffix != ".json"
            or path.resolve() != path):
        raise ValueError("报告仅可写入根 .cache/delivery/<name>.json，不能经过符号链接")
    if path.exists():
        old = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(old, dict) or old.get("tool") != "tools.delivery_check"
                or old.get("schema_version") != 1 or old.get("root") != str(root)):
            raise ValueError("拒绝覆盖非本工具拥有的 JSON 报告")
    return path


def _write_report(root: Path, value: str, report: dict) -> None:
    path = _report_path(root, value)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".delivery-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None, *, _root: Path | None = None, _run_git=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", metavar="ID")
    parser.add_argument("--candidate", action="append", nargs="+", default=[], metavar="ROOT_REL_PATH")
    parser.add_argument("--report", metavar=".cache/delivery/NAME.json")
    args = parser.parse_args(argv)
    root, runner = (ROOT if _root is None else _root).resolve(), _run_git or subprocess.run
    result = {"schema_version": 1, "tool": "tools.delivery_check", "root": str(root),
              "status": "collected", "changed": [], "staged": [], "untracked": [],
              "candidates": [], "ignored": [], "local_only": [], "errors": [],
              "task": {"task": args.task, "status": "not_verified", "verified_profiles": [],
                       "verification": {p: {"status": "not_verified", "reason": "未取得任务 profile 证据"}
                                        for p in ("python", "frontend")}},
              "business_regressions": {"status": "not_run"}, "repository_readiness": "not_assessed",
              "scope": "只分类，不决定上传；changed 为全部已跟踪变动，staged 为其子集。",
              "git_safety_scope": "仅固定只读 Git 请求及 HEAD/index 一致性校验；不是任意配置/插件的安全沙箱。",
              "evidence_scope": "profile 证据仅限任务批准文件；不是业务回归或整仓就绪证明。"}
    before = after = None
    writable = False
    try:
        if args.report is not None:
            _report_path(root, args.report)
            writable = True
        candidates = list(dict.fromkeys(_relative(p) for group in args.candidate for p in group))
        result["candidates"] = candidates
        top = Path(os.fsdecode(_git(root, "root", runner)).rstrip("\r\n")).resolve()
        if top != root:
            raise ValueError("Git 顶层目录不等于当前根目录")
        before = _snapshot(root, runner)
        try:
            result["changed"], result["staged"], result["untracked"] = _status(_git(root, "status", runner))
            if args.task is not None:
                result["task"] = inspect_task(root, args.task)
            approved = [item["path"] for item in result["task"].get("allow", [])]
            explicit = list(dict.fromkeys([*candidates, *approved]))
            result["ignored_query_paths"] = explicit
            if explicit:
                raw = _git(root, "ignored", runner, b"".join(os.fsencode(p) + b"\0" for p in explicit))
                result["ignored"] = _paths(raw)
                if not set(result["ignored"]).issubset(explicit):
                    raise ValueError("Git ignored 输出包含未明确查询的路径")
            seen = set(explicit)
            for item in result["changed"] + result["untracked"]:
                seen.add(item["path"])
                if "original_path" in item:
                    seen.add(item["original_path"])
            result["local_only"] = sorted(name for name in seen if _local_only(name))
        finally:
            after = _snapshot(root, runner)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as exc:
        result["errors"].append(f"{type(exc).__name__}: {exc}")
    if before is not None:
        result["git"] = {"before": before, "after": after, "stable": before == after}
        if after is not None and before != after:
            result["errors"].append("查询期间 Git HEAD/index 指纹改变；结果不稳定，不能归因于本工具。")
    result["status"] = "failed" if result["errors"] else "collected"
    if writable:
        try:
            _write_report(root, args.report, result)
        except (OSError, ValueError, TypeError, RuntimeError) as exc:
            result["errors"].append(f"报告写入失败: {exc}")
            result["status"] = "failed"
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
