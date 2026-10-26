"""检出仓库的无硬件快检：固定公共契约，或独立的前端检查。"""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
from tempfile import TemporaryDirectory
import time


ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIRECTORIES = ("src", "lab_workflows", "experiments", "GUI/backend", "tools")
PYTHON_TESTS = (
    "lab_workflows/test_experiment_architecture.py",
    "lab_workflows/test_workflow_state.py",
    "lab_workflows/test_analysis_execution.py",
    "lab_workflows/test_workflow_stages.py::test_stage_fields_defaults_frozen_and_json_contract",
    "lab_workflows/test_workflow_stages.py::test_initial_states_order_isolation_and_file_round_trip",
    "lab_workflows/test_workflow_stages.py::test_dependency_rules_keep_explicit_finish_and_only_invalidate_dependants",
    "lab_workflows/test_workflow_stages.py::test_analysis_rules_have_no_implicit_finish_node",
    "lab_workflows/test_workflow_stages.py::test_dependency_selection_preserves_preflight_exception_and_rerun_invalidation",
    "tools/test_check.py",
    "tools/test_checkpoint.py",
    "tools/test_delivery_check.py",
)
COMMAND_TIMEOUT_S = 30
_CLEANUP_TIMEOUT_S = 10
_WINDOWS = os.name == "nt"


def _syntax_files(root: Path) -> list[Path]:
    return sorted({path for directory in SOURCE_DIRECTORIES
                   for path in (root / directory).rglob("*.py") if path.is_file()})


def _positive_seconds(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("超时秒数必须是正有限数") from exc
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("超时秒数必须是正有限数")
    return seconds


def _run_command(command: list[str], *, cwd: Path, timeout_s: float) -> int:
    """继承输出；超时仅清理本次启动的进程树，不按进程名清理。"""
    process = subprocess.Popen(command, cwd=cwd, start_new_session=not _WINDOWS)
    try:
        return process.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired as original:
        notes = []
        try:
            if _WINDOWS:
                if process.poll() is None:
                    # wait 超时不会先杀父进程，taskkill 此时仍能追踪本次子孙。
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                   timeout=_CLEANUP_TIMEOUT_S, check=True)
                else:
                    notes.append("本次父进程已退出，不能确认子孙进程已停止")
            else:
                os.killpg(process.pid, signal.SIGKILL)
        except Exception as exc:
            notes.append(f"本次进程树清理失败，不能确认子孙进程已停止: {type(exc).__name__}: {exc}")
        try:
            process.wait(timeout=_CLEANUP_TIMEOUT_S)
        except Exception as exc:
            notes.append(f"本次父进程回收失败: {type(exc).__name__}: {exc}")
        if notes:
            # Python 3.10 没有 add_note；显式保留属性，交给报告如实呈现。
            original.__notes__ = [*getattr(original, "__notes__", ()), *notes]
        raise


def _validate_pytest_addopts() -> None:
    """拒绝内置选择/配置缩减入口；不隔离任意插件，不删除合法并行设置。"""
    forbidden = {"--deselect", "--ignore", "--ignore-glob", "--collect-only", "--co",
                 "--lf", "--last-failed", "--stepwise", "--sw", "--stepwise-skip", "--sw-skip",
                 "--config-file", "--rootdir"}
    collection_keys = {"addopts", "python_files", "python_classes", "python_functions",
                       "testpaths", "norecursedirs"}
    try:
        options = shlex.split(os.environ.get("PYTEST_ADDOPTS", ""))
    except ValueError as exc:
        raise ValueError(f"PYTEST_ADDOPTS 无法解析: {exc}") from exc
    for index, option in enumerate(options):
        name, separator, value = option.partition("=")
        if option.startswith("@") or name in forbidden:
            raise ValueError(f"PYTEST_ADDOPTS 不能缩小固定测试集合: {option}")
        following = options[index + 1] if index + 1 < len(options) else ""
        override = value if name == "--override-ini" and separator else (
            following if name == "--override-ini" else None)
        if option.startswith("-") and not option.startswith("--"):
            for offset, flag in enumerate(option[1:], start=1):
                if flag in "kmc":
                    raise ValueError(f"PYTEST_ADDOPTS 不能缩小固定测试集合: {option}")
                if flag == "o":
                    override = option[offset + 1:] or following
                    break
                if flag in "rpn":
                    # 取值短旗的剩余字符是值，不是后续选项（例如 -rsk）。
                    break
        if override is not None and override.removeprefix("=").partition("=")[0].strip() in collection_keys:
            raise ValueError(f"PYTEST_ADDOPTS 不能缩小固定测试集合: {option}")


def _changed_files(root: Path, since: str) -> list[str]:
    # 只显式请求范围时查询 Git；不加载注册表或具体实验。
    from lab_workflows.experiment_validation import changed_files
    return changed_files(root, since)


def _verify_results(path: Path) -> int:
    from lab_workflows.experiment_validation import verify_pytest_results
    return verify_pytest_results(path, list(PYTHON_TESTS))


def _frontend_commands(root: Path, *, windows: bool = os.name == "nt") -> list[list[str]]:
    npm = "npm.cmd" if windows else "npm"
    return [
        ["node", str(root / "GUI/frontend/node_modules/typescript/bin/tsc"), "--noEmit"],
        [npm, "test"],
        [npm, "run", "build"],
    ]


def _pending(name: str) -> dict:
    return {"name": name, "status": "not_run", "command": [], "returncode": None,
            "duration_s": 0.0, "reason": "尚未执行；前置失败时不继续"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=("python", "frontend"), default="python")
    parser.add_argument("--changed-since", metavar="REF", help="仅记录 Git 变更范围，不扩大测试集合")
    parser.add_argument("--report", type=Path, help="写入本轮 JSON 报告（不是测试缓存）")
    parser.add_argument("--timeout", type=_positive_seconds, default=float(COMMAND_TIMEOUT_S),
                        metavar="SECONDS", help="单个检查子进程的超时秒数，默认 30")
    args = parser.parse_args(argv)
    started = time.monotonic()
    names = ("python-syntax", "python-tests") if args.profile == "python" else (
        "frontend-types", "frontend-tests", "frontend-build")
    checks = [_pending(name) for name in names]
    scope = {"status": "not_run", "changed_since": args.changed_since, "changed_files": [],
             "reason": "未请求变更范围；默认快检不依赖 Git"}
    report = {
        "schema_version": 1, "profile": args.profile, "python": sys.version.split()[0],
        "root": str(ROOT), "scope": scope, "checks": checks, "timeout_s": args.timeout,
        "business_regressions": {"status": "not_run", "reason": "本入口不执行关联实验业务回归"},
    }
    failed = False
    if args.changed_since is not None:
        try:
            scope.update(status="passed", changed_files=_changed_files(ROOT, args.changed_since),
                         reason="仅记录范围；不是完整影响计划或业务验证证据")
        except Exception as exc:
            scope.update(status="failed", reason=f"{type(exc).__name__}: {exc}")
            failed = True

    if not failed:
        try:
            with TemporaryDirectory(prefix="exp-agent-check-") as temporary:
                junit = Path(temporary) / "pytest.xml"
                if args.profile == "python":
                    checks[1]["command"] = [sys.executable, "-m", "pytest", "-q",
                                             f"--junitxml={junit}", *PYTHON_TESTS]
                    cwd = ROOT
                else:
                    cwd = ROOT / "GUI/frontend"
                    for item, command in zip(checks, _frontend_commands(ROOT)):
                        item["command"] = command
                for item in checks:
                    tick = time.monotonic()
                    try:
                        print(f"[check] {item['name']}", flush=True)
                        if item["name"] == "python-syntax":
                            paths = _syntax_files(ROOT)
                            if not paths:
                                raise ValueError("固定源码目录未发现 Python 文件，拒绝空检查通过")
                            for path in paths:
                                # 编译字节尊重源文件编码，不执行模块，也不写入 __pycache__。
                                compile(path.read_bytes(), str(path), "exec", dont_inherit=True)
                            item["files_checked"] = len(paths)
                            item["returncode"] = 0
                        else:
                            if item["name"] == "python-tests":
                                _validate_pytest_addopts()
                            item["returncode"] = _run_command(item["command"], cwd=cwd, timeout_s=args.timeout)
                            if item["returncode"] != 0:
                                raise RuntimeError(f"子进程退出码 {item['returncode']}")
                            if item["name"] == "python-tests":
                                item["tests_executed"] = _verify_results(junit)
                        item.update(status="passed", reason="已执行并通过")
                    except Exception as exc:
                        item.update(status="failed", reason=f"{type(exc).__name__}: {exc}")
                        if notes := getattr(exc, "__notes__", None):
                            item["notes"] = list(notes)
                            item["reason"] += "; " + "; ".join(notes)
                        failed = True
                    item["duration_s"] = round(time.monotonic() - tick, 3)
                    if failed:
                        break
        except Exception as exc:
            # 包括临时目录创建/清理失败；不能把基础设施异常记为通过。
            failed = True
            report["error"] = f"{type(exc).__name__}: {exc}"

    report["status"] = "failed" if failed else "passed"
    report["duration_s"] = round(time.monotonic() - started, 3)
    if args.report is not None:
        try:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except OSError as exc:
            failed = True
            print(f"[check] 报告写入失败: {exc}", file=sys.stderr)
    if scope["status"] == "failed":
        print(f"[check] scope: failed — {scope['reason']}", file=sys.stderr)
    for item in checks:
        print(f"[check] {item['name']}: {item['status']} — {item['reason']}", flush=True)
    print("[check] 实验业务回归: not_run（固定快检不能替代专项回归）", flush=True)
    if "error" in report:
        print(f"[check] {report['error']}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
