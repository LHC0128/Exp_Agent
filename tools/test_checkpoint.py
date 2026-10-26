"""通过公共 main 验证断点与证据失效；快检仅使用写入真实 schema 的替身。"""
import io
import json
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock

import pytest

from tools import check, checkpoint


RAW = b"\x00\xfforiginal\r\nbytes\x80\n"


@pytest.fixture
def task(tmp_path, monkeypatch):
    (tmp_path / "source.bin").write_bytes(RAW)
    monkeypatch.setattr(checkpoint.subprocess, "run", Mock(side_effect=FileNotFoundError("无 Git")))
    monkeypatch.setattr(check, "main", Mock(side_effect=AssertionError("禁止执行真实快检")))
    calls = []

    def cli(*args, runner=None):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = checkpoint.main(list(args), _root=tmp_path, _check_main=runner)
        return code, json.loads(out.getvalue() or err.getvalue())

    def fake(argv):
        options = dict(zip(argv[::2], argv[1::2]))
        assert set(options) == {"--profile", "--report", "--timeout"}
        path, profile = Path(options["--report"]), options["--profile"]
        assert not path.exists(), "每次必须使用全新报告，不能复用测试缓存"
        names = (("python-syntax", "python-tests") if profile == "python" else
                 ("frontend-types", "frontend-tests", "frontend-build"))
        report = {"schema_version": 1, "profile": profile, "root": str(tmp_path),
                  "python": "3.10", "timeout_s": float(options["--timeout"]),
                  "scope": {"status": "not_run", "changed_since": None, "changed_files": [],
                            "reason": "未请求范围"},
                  "checks": [{"name": name, "status": "passed", "returncode": 0,
                              "command": [], "duration_s": 0.001, "reason": "替身已执行"}
                             for name in names],
                  "business_regressions": {"status": "not_run", "reason": "不执行业务回归"},
                  "status": "passed", "duration_s": 0.002}
        path.write_text(json.dumps(report), encoding="utf-8")
        calls.append((options, path))
        return 0

    return tmp_path, cli, fake, calls


def init(cli, name="task"):
    code, result = cli("init", name, "--objective", "接手本地任务", "--allow", "source.bin",
                       "--allow", "new/missing.py", "--experiment", "experiment-a",
                       "--experiment", "experiment-b", "--remaining", "完成精确验证")
    assert code == 0
    return result


def test_init_bytes_interrupted_agent_and_reopened_status_preserve_baseline(task):
    root, cli, _, _ = task
    initial = init(cli)
    directory = root / ".cache/agent-tasks/task"
    backup = directory / initial["allow"][0]["baseline"]
    assert backup.read_bytes() == RAW
    assert initial["allow"][1]["sha256"] is None
    assert not (directory / initial["allow"][1]["baseline"]).exists()
    assert initial["git_head"] is None
    assert initial["experiments"] == ["experiment-a", "experiment-b"]
    (root / "source.bin").write_bytes(b"changed")
    assert cli("agent", "task", "--id", "worker", "--state", "interrupted", "--note", "停止后待接手",
               "--remaining", "检查差异", "--remaining", "重新验证")[0] == 0
    code, resumed = cli("status", "task")
    assert code == 0 and resumed["status"] == "changed_unverified"
    assert resumed["agents"]["worker"]["state"] == "interrupted"
    assert resumed["agents"]["worker"]["note"] == "停止后待接手"
    assert resumed["agent_state_semantics"] == "last_recorded_not_liveness"
    assert resumed["remaining"] == ["检查差异", "重新验证"]
    assert cli("init", "task", "--objective", "覆盖", "--allow", "source.bin")[0] != 0
    assert backup.read_bytes() == RAW
    assert cli("status", "task")[1]["objective"] == "接手本地任务"
    partial = root / ".cache/agent-tasks/partial/baseline"
    partial.mkdir(parents=True)
    sentinel = partial / "0000.bin"
    sentinel.write_bytes(RAW)
    assert cli("init", "partial", "--objective", "覆盖中断基线", "--allow", "source.bin")[0] != 0
    assert sentinel.read_bytes() == RAW


def test_rejects_unsafe_task_allow_directory_and_resolved_link_escape(task, monkeypatch):
    root, cli, _, _ = task
    (root / "folder").mkdir()
    for name in ("../escape", "..", ".", "x/y", "x\\y", "C:task", "CON", "tail."):
        assert cli("init", name, "--objective", "拒绝", "--allow", "source.bin")[0] != 0
    for value in ("../outside", str(root / "source.bin"), "C:/outside", "C:outside",
                  ".git/config", ".GIT/config", ".cache/task", "folder", "new/", "new/.", "*.py"):
        assert cli("init", "invalid", "--objective", "拒绝", "--allow", value)[0] != 0
    assert not (root / ".cache/agent-tasks/invalid").exists()
    # 模拟链接解析到仓库外，不要求 Windows 的建链权限，也不跳过安全负例。
    resolve = Path.resolve
    alias = root / "alias.bin"
    monkeypatch.setattr(Path, "resolve", lambda path, *a, **kw:
                        root.parent / "outside.bin" if path == alias else resolve(path, *a, **kw))
    assert cli("init", "escape", "--objective", "拒绝链接逃逸", "--allow", "alias.bin")[0] != 0


@pytest.mark.parametrize("damage", ["tampered", "missing"])
def test_status_refuses_untrusted_baseline(task, damage):
    root, cli, _, _ = task
    initial = init(cli)
    backup = root / ".cache/agent-tasks/task" / initial["allow"][0]["baseline"]
    if damage == "missing":
        backup.unlink()
    else:
        backup.write_bytes(b"corrupt")
    code, result = cli("status", "task")
    assert code != 0 and result["status"] == "error"
    assert "baseline" in result["error"]


def test_check_calls_existing_main_always_runs_and_invalidates_all_approved_files(task, monkeypatch):
    root, cli, fake, calls = task
    init(cli)
    monkeypatch.setattr(check, "main", fake)
    code, checked = cli("check", "task", "--profile", "python", "--timeout", "2.5")
    assert code == 0 and checked["verified_profiles"] == ["python"]
    assert checked["verification"]["python"]["status"] == "verified_for_profile_current"
    assert checked["verification"]["frontend"]["status"] == "not_verified"
    assert checked["business_regressions"]["status"] == "not_run"
    assert calls[0][0]["--timeout"] == "2.5"
    assert set(checked["checks"][-1]["before"]) == {"source.bin", "new/missing.py"}
    assert cli("status", "task")[1]["verified_profiles"] == ["python"]
    assert cli("check", "task", "--profile", "python")[0] == 0
    assert len(calls) == 2 and calls[0][1] != calls[1][1]
    (root / "new").mkdir()
    (root / "new/missing.py").write_bytes(b"VALUE = 1\n")
    changed = cli("status", "task")[1]
    assert changed["status"] == "changed_unverified" and not changed["verified_profiles"]
    assert cli("check", "task", "--profile", "frontend", runner=fake)[1]["verified_profiles"] == ["frontend"]
    (root / "source.bin").write_bytes(b"again")
    assert cli("status", "task")[1]["verified_profiles"] == []


@pytest.mark.parametrize("case", ["returncode", "missing", "root", "profile", "schema", "status",
                                  "checks_missing", "check_status", "check_returncode", "business"])
def test_failed_or_invalid_report_never_reuses_old_success(task, case):
    root, cli, fake, calls = task
    init(cli)
    assert cli("check", "task", runner=fake)[0] == 0

    def bad(argv):
        if case == "missing":
            return 0
        fake(argv)
        path = calls[-1][1]
        report = json.loads(path.read_text(encoding="utf-8"))
        if case in ("root", "profile", "schema", "status"):
            key = "schema_version" if case == "schema" else case
            report[key] = {"root": str(root.parent), "profile": "frontend", "schema": 2,
                           "status": "failed"}[case]
        elif case == "checks_missing":
            report["checks"] = []
        elif case in ("check_status", "check_returncode"):
            report["checks"][0]["status" if case == "check_status" else "returncode"] = (
                "not_run" if case == "check_status" else 4)
        elif case == "business":
            report["business_regressions"]["status"] = "passed"
        path.write_text(json.dumps(report), encoding="utf-8")
        return 7 if case == "returncode" else 0

    code, result = cli("check", "task", runner=bad)
    assert code != 0 and result["checks"][-1]["status"] == "failed"
    assert result["checks"][0]["status"] == "passed"
    assert result["checks"][-1]["after"] is not None
    assert cli("status", "task")[1]["verified_profiles"] == []


@pytest.mark.parametrize("damage", ["tampered", "missing"])
def test_status_rechecks_report_bytes_and_never_falls_back_to_previous_success(task, damage):
    _, cli, fake, calls = task
    init(cli)
    assert cli("check", "task", runner=fake)[0] == 0
    assert cli("check", "task", runner=fake)[0] == 0
    latest = calls[-1][1]
    if damage == "missing":
        latest.unlink()
    else:
        # JSON 仍有效且宣称成功，但原始字节 sha 已改变。
        latest.write_text(latest.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    code, result = cli("status", "task")
    assert code == 0 and result["verified_profiles"] == []
    assert result["verification"]["python"]["status"] == "not_verified"


@pytest.mark.parametrize("outcome", ["change", "exception", "interrupt"])
def test_check_rejects_inflight_mutation_or_interruption_and_keeps_audit(task, outcome):
    root, cli, fake, _ = task
    init(cli)

    def during(argv):
        fake(argv)
        (root / "source.bin").write_bytes(b"edited during check")
        if outcome == "exception":
            raise RuntimeError("替身失败")
        if outcome == "interrupt":
            raise KeyboardInterrupt()
        return 0

    code, result = cli("check", "task", runner=during)
    assert code == (130 if outcome == "interrupt" else 1)
    record = result["checks"][-1]
    assert record["status"] == ("interrupted" if outcome == "interrupt" else "failed")
    assert record["before"] != record["after"] and record["after"] is not None
    reopened = cli("status", "task")[1]
    assert reopened["status"] == "changed_unverified" and reopened["verified_profiles"] == []
    assert reopened["checks"][-1]["status"] == record["status"]


@pytest.mark.parametrize("outcome", ["passed", "interrupted"])
def test_task_writer_rejects_nested_agent_but_status_reads_and_release_allows_write(task, outcome):
    _, cli, fake, _ = task
    init(cli)
    assert cli("agent", "task", "--id", "lead", "--state", "running", "--note", "保留主代理记录")[0] == 0

    def during(argv):
        for agent in ("racer-a", "racer-b"):
            code, rejected = cli("agent", "task", "--id", agent, "--state", "running",
                                 "--remaining", "不应写入")
            assert code != 0 and "主代理串行登记" in rejected["error"]
        code, reading = cli("status", "task")
        assert code == 0 and reading["checks"][-1]["status"] == "running"
        assert set(reading["agents"]) == {"lead"}
        assert reading["remaining"] == ["完成精确验证"]
        fake(argv)
        if outcome == "interrupted":
            raise KeyboardInterrupt()
        return 0

    code, checked = cli("check", "task", runner=during)
    assert code == (0 if outcome == "passed" else 130)
    assert set(checked["agents"]) == {"lead"}
    assert checked["agents"]["lead"]["note"] == "保留主代理记录"
    code, updated = cli("agent", "task", "--id", "racer-a", "--state", "done", "--note", "锁已释放")
    assert code == 0 and set(updated["agents"]) == {"lead", "racer-a"}
    assert updated["agents"]["racer-a"]["note"] == "锁已释放"
    assert len(updated["checks"]) == 1 and updated["checks"][0]["status"] == outcome


def test_inspect_task_is_public_read_only_and_reuses_current_evidence_validation(task):
    root, cli, fake, _ = task
    initial = init(cli)
    assert cli("check", "task", runner=fake)[0] == 0
    directory = root / ".cache/agent-tasks/task"
    metadata = root / ".git"
    metadata.mkdir()
    (metadata / "index").write_bytes(b"unchanged index")
    watched = [directory / "checkpoint.json", directory / initial["allow"][0]["baseline"], metadata / "index"]
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in watched}
    calls = checkpoint.subprocess.run.call_count
    inspected = checkpoint.inspect_task(root, "task")
    assert inspected["verified_profiles"] == ["python"]
    assert inspected["business_regressions"]["status"] == "not_run"
    (root / "source.bin").write_bytes(b"changed")
    assert checkpoint.inspect_task(root, "task")["status"] == "changed_unverified"
    assert {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in watched} == before
    assert checkpoint.subprocess.run.call_count == calls
    with pytest.raises(FileNotFoundError):
        checkpoint.inspect_task(root, "missing")
    watched[1].write_bytes(b"corrupt baseline")
    with pytest.raises(ValueError, match="baseline"):
        checkpoint.inspect_task(root, "task")
