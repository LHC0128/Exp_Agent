"""快检入口的失败传播与真实超时清理契约；业务命令通过私有 seam 替换。"""

import json
import subprocess
import sys
import time
from unittest.mock import Mock, call

import pytest

from tools import check


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.delenv("PYTEST_ADDOPTS", raising=False)
    (tmp_path / "src").mkdir()
    (tmp_path / "src/example.py").write_text("VALUE = 1\n", encoding="utf-8")
    monkeypatch.setattr(check, "ROOT", tmp_path)
    calls = []

    def run(command, *, cwd, timeout_s):
        calls.append((command, cwd))
        return 0

    monkeypatch.setattr(check, "_run_command", run)
    monkeypatch.setattr(check, "_verify_results", lambda path: 1)
    return tmp_path, calls


def report_at(root):
    return json.loads((root / "report.json").read_text(encoding="utf-8"))


def test_default_python_uses_fixed_public_nodes_without_git(sandbox, monkeypatch):
    root, calls = sandbox

    def unexpected(*args):
        raise AssertionError("默认快检不能查询 Git")

    monkeypatch.setattr(check, "_changed_files", unexpected)
    assert check.main(["--report", str(root / "report.json")]) == 0
    command, cwd = calls[0]
    assert len(calls) == 1 and cwd == root
    assert command[:4] == [sys.executable, "-m", "pytest", "-q"]
    assert command[5:] == list(check.PYTHON_TESTS)
    stage_nodes = [node for node in command if "test_workflow_stages.py::" in node]
    assert len(stage_nodes) == 5
    assert not any("test_old_stage_imports" in node or "test_experiment_rules" in node
                   or "test_migrated_stage_entrypoints" in node for node in command)
    result = report_at(root)
    assert result["status"] == "passed"
    assert [item["status"] for item in result["checks"]] == ["passed", "passed"]
    assert result["checks"][1]["tests_executed"] == 1
    assert result["scope"]["status"] == result["business_regressions"]["status"] == "not_run"


def test_frontend_profile_is_separate_and_preserves_command_order(sandbox):
    root, calls = sandbox
    assert check.main(["--profile", "frontend", "--report", str(root / "report.json")]) == 0
    assert [command for command, cwd in calls] == check._frontend_commands(root)
    assert all(cwd == root / "GUI/frontend" for command, cwd in calls)
    assert [item["status"] for item in report_at(root)["checks"]] == ["passed"] * 3
    assert not any("pytest" in command or "ci" in command for command, cwd in calls)


@pytest.mark.parametrize("windows,npm", [(True, "npm.cmd"), (False, "npm")])
def test_frontend_executable_and_typescript_path(tmp_path, windows, npm):
    commands = check._frontend_commands(tmp_path, windows=windows)
    assert commands == [
        ["node", str(tmp_path / "GUI/frontend/node_modules/typescript/bin/tsc"), "--noEmit"],
        [npm, "test"], [npm, "run", "build"],
    ]


def test_frontend_failure_keeps_later_steps_not_run(sandbox, monkeypatch):
    root, calls = sandbox

    def fail(command, *, cwd, timeout_s):
        calls.append((command, cwd))
        return 7

    monkeypatch.setattr(check, "_run_command", fail)
    assert check.main(["--profile", "frontend", "--report", str(root / "report.json")]) != 0
    result = report_at(root)
    assert len(calls) == 1 and result["status"] == "failed"
    assert [item["status"] for item in result["checks"]] == ["failed", "not_run", "not_run"]
    assert result["checks"][0]["returncode"] == 7
    assert result["checks"][1]["returncode"] is None


def test_pytest_failure_remains_nonzero_after_successful_report(sandbox, monkeypatch):
    root, _ = sandbox
    monkeypatch.setattr(check, "_run_command", lambda command, cwd, timeout_s: 5)
    assert check.main(["--report", str(root / "report.json")]) == 1
    result = report_at(root)
    assert [item["status"] for item in result["checks"]] == ["passed", "failed"]
    assert result["checks"][1]["returncode"] == 5 and result["status"] == "failed"


@pytest.mark.parametrize("failure", [OSError("无法启动"), subprocess.TimeoutExpired("node", 30)])
def test_launch_error_and_timeout_do_not_look_like_pass(sandbox, monkeypatch, failure):
    root, _ = sandbox

    def fail(command, *, cwd, timeout_s):
        raise failure

    monkeypatch.setattr(check, "_run_command", fail)
    assert check.main(["--profile", "frontend", "--report", str(root / "report.json")]) == 1
    steps = report_at(root)["checks"]
    assert [item["status"] for item in steps] == ["failed", "not_run", "not_run"]
    assert type(failure).__name__ in steps[0]["reason"]


def test_bad_syntax_prevents_pytest(sandbox):
    root, calls = sandbox
    (root / "src/example.py").write_text("def broken(:\n", encoding="utf-8")
    assert check.main(["--report", str(root / "report.json")]) == 1
    assert not calls
    steps = report_at(root)["checks"]
    assert [item["status"] for item in steps] == ["failed", "not_run"]
    assert "SyntaxError" in steps[0]["reason"]


def test_syntax_is_static_and_limited_to_source_directories(sandbox):
    root, _ = sandbox
    (root / "src/example.py").write_text("raise RuntimeError('不能执行')\n", encoding="utf-8")
    (root / "data").mkdir()
    (root / "data/not_source.py").write_text("def broken(:\n", encoding="utf-8")
    assert check.main(["--report", str(root / "report.json")]) == 0
    assert report_at(root)["checks"][0]["files_checked"] == 1
    assert not (root / "src/__pycache__").exists()


def test_zero_exit_still_requires_actual_pytest_evidence(sandbox, monkeypatch):
    root, _ = sandbox

    def reject(path):
        assert path.name == "pytest.xml"
        raise ValueError("缺失、跳过或未实际执行关联节点")

    monkeypatch.setattr(check, "_verify_results", reject)
    assert check.main(["--report", str(root / "report.json")]) == 1
    item = report_at(root)["checks"][1]
    assert item["status"] == "failed" and item["returncode"] == 0
    assert "未实际执行" in item["reason"]


def test_changed_since_only_records_scope(sandbox, monkeypatch):
    root, calls = sandbox
    requests = []

    def changes(path, since):
        requests.append((path, since))
        return ["lab_workflows/steps/phase.py"]

    monkeypatch.setattr(check, "_changed_files", changes)
    assert check.main(["--changed-since", "HEAD~1", "--report", str(root / "report.json")]) == 0
    assert requests == [(root, "HEAD~1")] and len(calls) == 1
    result = report_at(root)
    assert result["scope"]["status"] == "passed"
    assert result["scope"]["changed_files"] == ["lab_workflows/steps/phase.py"]
    assert result["business_regressions"]["status"] == "not_run"


def test_bad_ref_fails_without_running_checks(sandbox, monkeypatch):
    root, calls = sandbox

    def fail(*args):
        raise ValueError("无法读取 Git 变更")

    monkeypatch.setattr(check, "_changed_files", fail)
    assert check.main(["--changed-since", "bad-ref", "--report", str(root / "report.json")]) == 1
    result = report_at(root)
    assert not calls and result["scope"]["status"] == "failed"
    assert all(item["status"] == "not_run" for item in result["checks"])


def test_report_write_failure_is_nonzero(sandbox):
    root, _ = sandbox
    blocker = root / "not_a_directory"
    blocker.write_text("file", encoding="utf-8")
    assert check.main(["--report", str(blocker / "report.json")]) == 1


@pytest.fixture
def owned_process(monkeypatch):
    process = Mock(pid=4312)
    process.poll.return_value = None
    launch = Mock(return_value=process)
    tree_kill = Mock(return_value=subprocess.CompletedProcess([], 0))
    group_kill = Mock()
    monkeypatch.setattr(check.subprocess, "Popen", launch)
    monkeypatch.setattr(check.subprocess, "run", tree_kill)
    monkeypatch.setattr(check.os, "killpg", group_kill, raising=False)
    monkeypatch.setattr(check.signal, "SIGKILL", 9, raising=False)
    return process, launch, tree_kill, group_kill


@pytest.mark.parametrize("windows", [True, False])
@pytest.mark.parametrize("returncode", [0, 9])
@pytest.mark.parametrize("timeout_s", [30.0, 180.5])
def test_subprocess_adapter_preserves_exit_code_and_timeout(
    tmp_path, monkeypatch, owned_process, windows, returncode, timeout_s,
):
    process, launch, tree_kill, group_kill = owned_process
    monkeypatch.setattr(check, "_WINDOWS", windows)
    process.wait.return_value = returncode
    assert check._run_command(["npm.cmd", "test"], cwd=tmp_path, timeout_s=timeout_s) == returncode
    launch.assert_called_once_with(["npm.cmd", "test"], cwd=tmp_path, start_new_session=not windows)
    process.wait.assert_called_once_with(timeout=timeout_s)
    tree_kill.assert_not_called()
    group_kill.assert_not_called()


@pytest.mark.parametrize("windows", [True, False])
def test_timeout_only_kills_owned_tree_then_reaps_and_raises_original(
    tmp_path, monkeypatch, owned_process, windows,
):
    process, launch, tree_kill, group_kill = owned_process
    monkeypatch.setattr(check, "_WINDOWS", windows)
    failure = subprocess.TimeoutExpired("npm.cmd", 30)
    process.wait.side_effect = [failure, -9]

    def kill_tree(*args, **kwargs):
        assert process.wait.call_count == 1 and process.poll() is None
        return subprocess.CompletedProcess(args[0], 0)

    tree_kill.side_effect = kill_tree
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        check._run_command(["npm.cmd", "test"], cwd=tmp_path, timeout_s=30)
    assert caught.value is failure
    assert process.wait.call_args_list == [call(timeout=30), call(timeout=10)]
    if windows:
        tree_kill.assert_called_once_with(["taskkill", "/PID", "4312", "/T", "/F"], timeout=10, check=True)
        group_kill.assert_not_called()
    else:
        group_kill.assert_called_once_with(4312, 9)
        tree_kill.assert_not_called()
    assert not getattr(failure, "__notes__", [])


@pytest.mark.parametrize("windows", [True, False])
def test_tree_cleanup_failure_is_not_hidden_and_still_attempts_reap(
    tmp_path, monkeypatch, owned_process, windows,
):
    process, launch, tree_kill, group_kill = owned_process
    monkeypatch.setattr(check, "_WINDOWS", windows)
    failure = subprocess.TimeoutExpired("npm.cmd", 30)
    process.wait.side_effect = [failure, -9]
    tree_kill.side_effect = subprocess.CalledProcessError(1, "taskkill")
    group_kill.side_effect = PermissionError("模拟无权限")
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        check._run_command(["npm.cmd", "test"], cwd=tmp_path, timeout_s=30)
    assert caught.value is failure
    assert "不能确认子孙进程已停止" in failure.__notes__[0]
    assert process.wait.call_args_list == [call(timeout=30), call(timeout=10)]


def test_exited_windows_parent_is_not_targeted_again(tmp_path, monkeypatch, owned_process):
    process, launch, tree_kill, group_kill = owned_process
    monkeypatch.setattr(check, "_WINDOWS", True)
    process.poll.return_value = 0
    failure = subprocess.TimeoutExpired("npm.cmd", 30)
    process.wait.side_effect = [failure, 0]
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        check._run_command(["npm.cmd", "test"], cwd=tmp_path, timeout_s=30)
    assert caught.value is failure
    tree_kill.assert_not_called()
    group_kill.assert_not_called()
    assert "父进程已退出" in failure.__notes__[0]


def test_reap_failure_remains_visible(tmp_path, monkeypatch, owned_process):
    process, launch, tree_kill, group_kill = owned_process
    monkeypatch.setattr(check, "_WINDOWS", True)
    failure = subprocess.TimeoutExpired("npm.cmd", 30)
    process.wait.side_effect = [failure, subprocess.TimeoutExpired("npm.cmd", 10)]
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        check._run_command(["npm.cmd", "test"], cwd=tmp_path, timeout_s=30)
    assert caught.value is failure and "父进程回收失败" in failure.__notes__[0]


@pytest.mark.parametrize("profile", ["python", "frontend"])
@pytest.mark.parametrize("options,expected", [([], 30.0), (["--timeout", "180.5"], 180.5)])
def test_configured_timeout_reaches_every_command_and_report(sandbox, monkeypatch, profile, options, expected):
    root, _ = sandbox
    budgets = []

    def run(command, *, cwd, timeout_s):
        budgets.append(timeout_s)
        return 0

    monkeypatch.setattr(check, "_run_command", run)
    assert check.main(["--profile", profile, *options, "--report", str(root / "report.json")]) == 0
    assert budgets == [expected] * (1 if profile == "python" else 3)
    assert report_at(root)["timeout_s"] == expected
    assert check.COMMAND_TIMEOUT_S == 30


@pytest.mark.parametrize("value", ["0", "-1", "nan", "NaN", "inf", "-inf", "1e999", "not-a-number"])
def test_invalid_timeout_is_rejected_before_any_check(sandbox, value):
    root, calls = sandbox
    with pytest.raises(SystemExit) as caught:
        check.main([f"--timeout={value}", "--report", str(root / "report.json")])
    assert caught.value.code == 2
    assert not calls and not (root / "report.json").exists()


@pytest.mark.parametrize("options", [
    "-k smoke", "-ksmoke", "-m fast", "-mfast",
    "--deselect item", "--deselect=item", "--ignore path", "--ignore=path",
    "--ignore-glob '*.py'", "--ignore-glob=*.py", "--collect-only", "--collect-only=true",
    "--co", "--lf", "--lf=true", "--last-failed", "--last-failed=true",
    "--stepwise", "--stepwise=true", "--sw", "--sw=true", "--stepwise-skip", "--sw-skip",
    "-qk smoke", "-vk smoke", "-qm fast", "-qqm fast", "-vqk smoke",
    "@args.txt", "-c pytest.ini", "-cpytest.ini", "-qcpytest.ini",
    "--config-file pytest.ini", "--config-file=pytest.ini", "--rootdir path", "--rootdir=path",
])
def test_pytest_selection_addopts_cannot_shrink_fixed_suite(sandbox, monkeypatch, options):
    root, calls = sandbox
    monkeypatch.setenv("PYTEST_ADDOPTS", options)
    assert check.main(["--report", str(root / "report.json")]) == 1
    result = report_at(root)
    assert not calls
    assert [item["status"] for item in result["checks"]] == ["passed", "failed"]
    assert "PYTEST_ADDOPTS 不能缩小固定测试集合" in result["checks"][1]["reason"]
    assert check.os.environ["PYTEST_ADDOPTS"] == options


@pytest.mark.parametrize("options", [
    "-n auto --dist=loadscope", "--maxfail=1 --tb=short", "-o 'log_cli=true'",
    "-rsk", "-pmyplugin", "-n auto", "-n8", "-qra", "-o log_cli=true",
    "-qolog_cli=true", "-qo log_cli=true", "--override-ini=log_cli=true", "--override-ini log_cli=true",
])
def test_nonselection_addopts_are_preserved(sandbox, monkeypatch, options):
    root, calls = sandbox
    monkeypatch.setenv("PYTEST_ADDOPTS", options)
    assert check.main(["--report", str(root / "report.json")]) == 0
    assert len(calls) == 1 and check.os.environ["PYTEST_ADDOPTS"] == options


@pytest.mark.parametrize("key", ["addopts", "python_files", "python_classes", "python_functions", "testpaths", "norecursedirs"])
@pytest.mark.parametrize("template", [
    "-o '{key}=-kfoo'", "-o{key}=-kfoo", "-o={key}=-kfoo", "-qo{key}=-kfoo",
    "-qo '{key}=-kfoo'", "--override-ini '{key}=-kfoo'", "--override-ini={key}=-kfoo",
])
def test_collection_ini_overrides_are_rejected(sandbox, monkeypatch, key, template):
    root, calls = sandbox
    monkeypatch.setenv("PYTEST_ADDOPTS", template.format(key=key))
    assert check.main(["--report", str(root / "report.json")]) == 1
    assert not calls
    assert "PYTEST_ADDOPTS 不能缩小固定测试集合" in report_at(root)["checks"][1]["reason"]


def test_frontend_does_not_reject_pytest_selection_settings(sandbox, monkeypatch):
    root, calls = sandbox
    monkeypatch.setenv("PYTEST_ADDOPTS", "-ksmoke")
    assert check.main(["--profile", "frontend", "--report", str(root / "report.json")]) == 0
    assert len(calls) == 3


def test_malformed_pytest_addopts_fail_before_launch(sandbox, monkeypatch):
    root, calls = sandbox
    monkeypatch.setenv("PYTEST_ADDOPTS", "'unfinished")
    assert check.main(["--report", str(root / "report.json")]) == 1
    assert not calls and "PYTEST_ADDOPTS 无法解析" in report_at(root)["checks"][1]["reason"]


def test_cleanup_notes_reach_failure_report(sandbox, monkeypatch):
    root, _ = sandbox
    failure = subprocess.TimeoutExpired("npm.cmd", 30)
    failure.__notes__ = ["本次进程树清理失败，不能确认子孙进程已停止"]

    def fail(command, *, cwd, timeout_s):
        raise failure

    monkeypatch.setattr(check, "_run_command", fail)
    assert check.main(["--profile", "frontend", "--report", str(root / "report.json")]) == 1
    steps = report_at(root)["checks"]
    assert [item["status"] for item in steps] == ["failed", "not_run", "not_run"]
    assert steps[0]["notes"] == failure.__notes__
    assert "不能确认子孙进程已停止" in steps[0]["reason"]


def test_timeout_reaps_real_parent_and_child_before_next_command(tmp_path):
    """只启动本轮自有假进程；超时返回后，父子都不能继续响应下一轮文件门控。"""
    helper = tmp_path / "owned_tree.py"
    helper.write_text('''\
import os
from pathlib import Path
import subprocess
import sys
import time

role, directory = sys.argv[1:]
root = Path(directory)
deadline = time.monotonic() + 3.0
if role == "parent":
    subprocess.Popen([sys.executable, "-B", __file__, "child", directory])
ready = root / f"{role}.ready"
temporary = ready.with_suffix(".tmp")
temporary.write_text(str(os.getpid()), encoding="utf-8")
temporary.replace(ready)
while time.monotonic() < deadline:
    if (root / "release").exists():
        (root / f"{role}.survived").write_text("alive", encoding="utf-8")
        break
    time.sleep(0.005)
''', encoding="utf-8")
    command = [sys.executable, "-B", str(helper), "parent", str(tmp_path)]
    release = tmp_path / "release"
    survived = [tmp_path / f"{role}.survived" for role in ("parent", "child")]
    try:
        with pytest.raises(subprocess.TimeoutExpired) as caught:
            check._run_command(command, cwd=tmp_path, timeout_s=1.0)
        assert caught.value.cmd == command and caught.value.timeout == 1.0
        assert not getattr(caught.value, "__notes__", [])
        assert all((tmp_path / f"{role}.ready").is_file() for role in ("parent", "child")), (
            "真实父子尚未启动，不能将未启动误判为清理成功"
        )
        release.touch()
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline and not any(path.exists() for path in survived):
            time.sleep(0.005)
        assert not any(path.exists() for path in survived), "超时返回后本轮父子仍在执行"
    finally:
        # 即使断言失败也释放本轮门控；所有假进程另有 3 秒自然退出上限。
        release.touch()
