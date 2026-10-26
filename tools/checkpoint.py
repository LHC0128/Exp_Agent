"""本地任务断点：保留初始字节基线、代理最后记录及限 profile 的快检证据。

写命令由主代理串行登记；任务被占用时立即拒绝，status 始终只读。
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import subprocess
import sys
import tempfile
from uuid import uuid4


ROOT = Path(__file__).resolve().parent.parent
_CHECKS = {"python": ("python-syntax", "python-tests"),
           "frontend": ("frontend-types", "frontend-tests", "frontend-build")}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _storage(root: Path, relative: str) -> Path:
    path = root / relative
    if path.resolve() != path or not path.is_relative_to(root):
        raise ValueError("checkpoint 存储路径不能经过链接或越界")
    return path


def _task_directory(root: Path, task: str) -> Path:
    if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", task)
            or task.endswith(".") or PureWindowsPath(task).is_reserved()):
        raise ValueError("任务 ID 必须是安全的单段名称，不能包含路径穿越")
    return _storage(root, f".cache/agent-tasks/{task}")


@contextmanager
def _writer(root: Path, directory: Path):
    # 复用普通锁文件，不删除；锁属于 OS 句柄，关闭或进程退出自动释放。
    path = _storage(root, str((directory.parent / f".{directory.name}.write.lock").relative_to(root)))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("任务已有写命令占用；请由主代理串行登记后重试") from exc
        yield


def _approved(root: Path, value: str) -> tuple[str, Path]:
    name = value.replace("\\", "/")
    parts = PurePosixPath(name).parts
    if (not parts or name.endswith(("/", "/.")) or PurePosixPath(name).is_absolute()
            or PureWindowsPath(name).drive or PureWindowsPath(name).is_reserved()
            or any(p in ("..", ".git", ".cache") for p in map(str.lower, parts))
            or any(p.endswith((".", " ")) for p in parts)
            or any(c in name for c in ":*?[]\x00")):
        raise ValueError(f"allow 必须是明确的仓库内文件，不能自指或越界: {value}")
    path = root / PurePosixPath(name)
    resolved = path.resolve()
    if (not resolved.is_relative_to(root)
            or any(p.lower() in (".git", ".cache") for p in resolved.relative_to(root).parts)):
        raise ValueError(f"allow 链接逃逸或指向保留目录: {value}")
    if path.exists() and not path.is_file():
        raise ValueError(f"allow 不能是目录或特殊文件: {value}")
    return PurePosixPath(name).as_posix(), path


def _fingerprints(root: Path, allow: list[dict]) -> dict:
    current = {}
    for item in allow:
        name, path = _approved(root, item["path"])
        current[name] = _sha(path.read_bytes()) if path.exists() else None
    return current


def _save(directory: Path, data: dict) -> None:
    # 同目录写完、同步后替换；旧 checkpoint 不会变成半个 JSON。
    if directory.resolve() != directory:
        raise ValueError("checkpoint 存储路径不能经过链接")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory,
                                         prefix=".checkpoint-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, directory / "checkpoint.json")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _git_head(root: Path) -> str | None:
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                                capture_output=True, text=True, timeout=2, check=False)
        return result.stdout.strip() if result.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def _init(root: Path, directory: Path, args) -> dict:
    files, seen = [], set()
    for value in args.allow:
        name, path = _approved(root, value)
        if Path(name) in seen:
            continue
        seen.add(Path(name))
        files.append((name, path.read_bytes() if path.exists() else None))
    if not args.objective.strip():
        raise ValueError("objective 不能为空")
    # 独占创建整个任务；即使首次初始化中断，也绝不覆盖其中的初始基线。
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "baseline").mkdir()
    allow = []
    for index, (name, original) in enumerate(files):
        baseline = f"baseline/{index:04}.bin"
        if original is not None:
            with (directory / baseline).open("xb") as stream:
                stream.write(original)
        allow.append({"path": name, "baseline": baseline,
                      "sha256": _sha(original) if original is not None else None})
    data = {"schema_version": 1, "task": args.task, "root": str(root),
            "objective": args.objective, "allow": allow, "experiments": args.experiment,
            "git_head": _git_head(root), "created_at": _now(), "agents": {},
            "agent_state_semantics": "last_recorded_not_liveness",
            "remaining": args.remaining, "checks": [],
            "business_regressions": {"status": "not_run"}}
    _save(directory, data)
    return data


def _baseline(root: Path, directory: Path, data: dict) -> dict:
    allow = data["allow"]
    if not isinstance(allow, list) or not allow:
        raise ValueError("checkpoint 缺少批准文件")
    original = {}
    for index, item in enumerate(allow):
        name, _ = _approved(root, item["path"])
        expected = item["sha256"]
        if (name in original or name != item["path"]
                or item["baseline"] != f"baseline/{index:04}.bin"
                or (expected is not None and not re.fullmatch(r"[0-9a-f]{64}", expected))):
            raise ValueError("checkpoint 基线元数据无效")
        path = _storage(root, str((directory / item["baseline"]).relative_to(root)))
        actual = _sha(path.read_bytes()) if path.exists() else None
        if actual != expected:
            raise ValueError(f"baseline 副本 sha256 不可信: {name}")
        original[name] = expected
    return original


def _load(root: Path, directory: Path, task: str) -> dict:
    path = _storage(root, str((directory / "checkpoint.json").relative_to(root)))
    data = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(data, dict) or type(data.get("schema_version")) is not int
            or data["schema_version"] != 1 or data.get("task") != task
            or data.get("root") != str(root) or not isinstance(data.get("checks"), list)):
        raise ValueError("checkpoint 的 schema、任务或仓库不匹配")
    _baseline(root, directory, data)
    return data


def _report(root: Path, directory: Path, record: dict) -> str:
    relative = record["report"]
    if not re.fullmatch(r"reports/[0-9a-f]{32}\.json", relative):
        raise ValueError("验证报告路径无效")
    path = _storage(root, str((directory / relative).relative_to(root)))
    raw = path.read_bytes()
    report = json.loads(raw)
    profile = record["profile"]
    if not isinstance(report, dict):
        raise ValueError("验证报告必须是 JSON 对象")
    checks = report.get("checks")
    if (type(report.get("schema_version")) is not int or report["schema_version"] != 1
            or report.get("root") != str(root) or report.get("profile") != profile
            or report.get("status") != "passed"
            or not isinstance(report.get("business_regressions"), dict)
            or report["business_regressions"].get("status") != "not_run"
            or not isinstance(checks, list) or not all(isinstance(c, dict) for c in checks)
            or [c.get("name") for c in checks] != list(_CHECKS[profile])
            or any(c.get("status") != "passed" or type(c.get("returncode")) is not int
                   or c["returncode"] != 0 for c in checks)):
        raise ValueError("验证报告的 root/profile/schema/预期 checks 无效或未通过")
    return _sha(raw)


def _view(root: Path, directory: Path, data: dict) -> dict:
    original = _baseline(root, directory, data)
    current = _fingerprints(root, data["allow"])
    changed = [name for name in original if current[name] != original[name]]
    verification = {}
    for profile in _CHECKS:
        latest = next((c for c in reversed(data["checks"]) if c["profile"] == profile), None)
        reason = "尚无本 profile 的成功快检"
        valid = False
        if latest is not None and latest["status"] == "passed":
            try:
                valid = (type(latest["returncode"]) is int and latest["returncode"] == 0
                         and latest["before"] == latest["after"] == current
                         and _report(root, directory, latest) == latest["report_sha256"])
                reason = "当前全部批准文件及报告与本次快检一致" if valid else "文件或报告已改变"
            except (OSError, ValueError, KeyError, TypeError) as exc:
                reason = f"验证证据不可用: {exc}"
        verification[profile] = {"status": "verified_for_profile_current" if valid else "not_verified",
                                 "reason": reason}
    verified = [p for p, v in verification.items() if v["status"] == "verified_for_profile_current"]
    return {**data, "baseline_integrity": "passed", "current_fingerprints": current,
            "changed_files": changed, "verification": verification, "verified_profiles": verified,
            "status": "verified_for_profile_current" if verified else (
                "changed_unverified" if changed else "unchanged_unverified"),
            "business_regressions": {"status": "not_run"}}


def inspect_task(root: Path, task: str) -> dict:
    """只读复核当前任务证据；不登记状态、不加写锁、不执行快检。"""
    root = root.resolve()
    directory = _task_directory(root, task)
    return _view(root, directory, _load(root, directory, task))


def _check(root: Path, directory: Path, data: dict, args, runner) -> int:
    reports = _storage(root, str((directory / "reports").relative_to(root)))
    reports.mkdir(exist_ok=True)
    relative = f"reports/{uuid4().hex}.json"
    path = _storage(root, str((directory / relative).relative_to(root)))
    if path.exists():
        raise ValueError("拒绝复用已有验证报告")
    record = {"profile": args.profile, "started_at": _now(), "status": "running",
              "report": relative, "report_sha256": None, "returncode": None,
              "before": _fingerprints(root, data["allow"]), "after": None,
              "business_regressions": {"status": "not_run"}}
    data["checks"].append(record)
    # 先持久化本次尝试；进程被中断时，不再拿更早的成功当作当前证据。
    _save(directory, data)
    code = 1
    try:
        if runner is None:
            from .check import main as runner
        try:
            record["returncode"] = runner(["--profile", args.profile, "--report", str(path),
                                            "--timeout", str(args.timeout)])
        finally:
            record["after"] = _fingerprints(root, data["allow"])
        record["report_sha256"] = _report(root, directory, record)
        if (type(record["returncode"]) is not int or record["returncode"] != 0
                or record["before"] != record["after"]):
            raise ValueError("快检失败或执行期间批准文件改变")
        record["status"], code = "passed", 0
    except (Exception, KeyboardInterrupt) as exc:
        record["status"] = "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
        record["reason"] = f"{type(exc).__name__}: {exc}"
        if isinstance(exc, KeyboardInterrupt):
            code = 130
    finally:
        record["finished_at"] = _now()
        _save(directory, data)
    return code


def _seconds(value: str) -> float:
    seconds = float(value)
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("超时秒数必须是正有限数")
    return seconds


def main(argv: list[str] | None = None, *, _root: Path | None = None, _check_main=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "status", "agent", "check"):
        command = commands.add_parser(name)
        command.add_argument("task", metavar="TASK")
        if name == "init":
            command.add_argument("--objective", required=True)
            command.add_argument("--allow", action="append", required=True)
            command.add_argument("--experiment", action="append", default=[])
            command.add_argument("--remaining", action="append", default=[])
        elif name == "agent":
            command.add_argument("--id", required=True)
            command.add_argument("--state", choices=("pending", "running", "interrupted", "done"), required=True)
            command.add_argument("--note", default="")
            command.add_argument("--remaining", action="append")
        elif name == "check":
            command.add_argument("--profile", choices=tuple(_CHECKS), default="python")
            command.add_argument("--timeout", type=_seconds, default=30.0, metavar="SECONDS")
    args = parser.parse_args(argv)
    try:
        root = (ROOT if _root is None else _root).resolve()
        directory = _task_directory(root, args.task)
        with (nullcontext() if args.command == "status" else _writer(root, directory)):
            data = _init(root, directory, args) if args.command == "init" else _load(root, directory, args.task)
            code = 0
            if args.command == "agent":
                if not args.id.strip():
                    raise ValueError("代理 ID 不能为空")
                if args.remaining is not None:
                    data["remaining"] = args.remaining
                data["agents"][args.id] = {"state": args.state, "note": args.note,
                                           "remaining": list(data["remaining"]), "recorded_at": _now()}
                _save(directory, data)
            elif args.command == "check":
                code = _check(root, directory, data, args, _check_main)
            view = _view(root, directory, data)
            print(json.dumps(view, ensure_ascii=False, indent=2))
            if args.command == "check" and args.profile not in view["verified_profiles"]:
                return code or 1
            return code
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        print(json.dumps({"status": "error", "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False),
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
