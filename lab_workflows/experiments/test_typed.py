"""运行目录协议、适配器与真实无硬件子进程的可靠性回归。"""
from __future__ import annotations

from io import StringIO
import json
import os
from pathlib import Path
from queue import Queue
import sys
from threading import Thread
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
import yaml

from ..common import CancellationToken, WorkflowCancelled
from ..experiment_params import ExperimentParams
from ..experiment_runtime import report_runtime_progress, check_cancelled
from ..steps.run_directory import create_run_directory, write_run_config
from . import typed


@pytest.fixture
def adapter(tmp_path, monkeypatch):
    value = typed.TypedWorkflowAdapter("example", "Example", ExperimentParams, __name__, __name__)
    value.root = tmp_path
    monkeypatch.setattr(value, "_resolved", lambda values: SimpleNamespace(to_external=lambda: values))
    monkeypatch.setattr(value, "preflight", lambda values: [])
    original = typed.subprocess.Popen
    # 临时项目不复制虚拟环境，实际协议、stdout、取消与 wait 均走真实子进程。
    monkeypatch.setattr(typed.subprocess, "Popen", lambda command, **kwargs:
        original([sys.executable, *command[1:]], **kwargs))
    return value


def make_run(root, name="current", data_type="Example", experiment_id="example"):
    path = root / "data" / data_type / name
    (path / "raw").mkdir(parents=True)
    (path / "results").mkdir()
    write_run_config(path / "experiment_config.yaml", {
        "experiment_id": experiment_id, "experiment_type": data_type, "parameters": {},
    })
    return path


def wait_for(predicate):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("假子进程未到达预期检查点")


@pytest.mark.parametrize("platform_name, creationflags, start_new_session", [
    ("nt", 0x00000200, False),
    ("posix", 0, True),
])
def test_launch_isolates_child_from_terminal_interrupts(
    adapter, monkeypatch, platform_name, creationflags, start_new_session,
):
    # 只替换 typed 的模块引用，不全局修改 os.name，以免影响 pytest/Path。
    monkeypatch.setattr(typed, "os", SimpleNamespace(
        name=platform_name, environ=os.environ, getpid=os.getpid,
    ))
    monkeypatch.setattr(typed.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200, raising=False)
    process = SimpleNamespace(stdout=StringIO(), poll=lambda: 0, wait=Mock(return_value=0))
    launch = Mock(return_value=process)
    monkeypatch.setattr(typed.subprocess, "Popen", launch)

    assert adapter._execute(adapter.acquisition_module) is None

    options = launch.call_args.kwargs
    assert options["creationflags"] == creationflags
    assert options["start_new_session"] is start_new_session
    assert options["stdout"] == typed.subprocess.PIPE
    assert options["stderr"] == typed.subprocess.STDOUT
    assert options["stdin"] is None
    process.wait.assert_called_once_with()
    assert process.stdout.closed


@pytest.mark.parametrize("failure_source", ["callback", "stdout"])
def test_execute_fault_cancels_commands_and_waits_before_preserving_original_error(
    adapter, monkeypatch, failure_source,
):
    failure = ValueError("父回调原始错误") if failure_source == "callback" else OSError("父通信原始错误")
    events, commands = [], Queue()
    # 这里仅验证主线程 finally；真实取消监控和管道由下方真实子进程测试覆盖。
    monkeypatch.setattr(typed, "threading", SimpleNamespace(
        get_ident=typed.threading.get_ident, Thread=Mock(),
    ))

    class Stdout:
        def __iter__(self):
            if failure_source == "stdout":
                raise failure
            yield "测试子进程日志\n"

        def close(self):
            assert events == ["wait"]
            events.append("stdout_closed")

    def wait():
        cancel_path = Path(launch.call_args.kwargs["env"]["LAB_CANCEL_FILE"])
        assert cancel_path.is_file(), "回调/通信故障必须先发出协作取消"
        assert commands.get_nowait() is None, "等待子进程前必须关闭命令流"
        events.append("wait")
        return 1

    process = SimpleNamespace(stdout=Stdout(), poll=lambda: None, wait=Mock(side_effect=wait),
        terminate=Mock(), kill=Mock())
    launch = Mock(return_value=process)
    monkeypatch.setattr(typed.subprocess, "Popen", launch)

    def broken(event):
        raise failure

    with pytest.raises(type(failure)) as caught:
        adapter._execute(adapter.acquisition_module, progress=broken, commands=commands)
    assert caught.value is failure
    assert events == ["wait", "stdout_closed"]
    process.wait.assert_called_once_with()
    process.terminate.assert_not_called()
    process.kill.assert_not_called()
    assert not Path(launch.call_args.kwargs["env"]["LAB_CANCEL_FILE"]).exists()


@pytest.mark.parametrize("return_code", [0, 1, -15])
def test_cancelled_child_exit_does_not_claim_safety_or_hide_nonzero_exit(adapter, monkeypatch, caplog, return_code):
    token = CancellationToken()
    token.cancel()
    process = SimpleNamespace(
        stdout=StringIO(), poll=lambda: return_code, wait=Mock(return_value=return_code),
        terminate=Mock(), kill=Mock(),
    )
    monkeypatch.setattr(typed.subprocess, "Popen", Mock(return_value=process))

    with pytest.raises(WorkflowCancelled) as caught:
        adapter._execute(adapter.acquisition_module, cancellation=token)

    assert "子进程已退出" in str(caught.value)
    assert "安全收尾结果请核对日志" in str(caught.value)
    assert "已在安全检查点停止" not in str(caught.value)
    warnings = [record for record in caplog.records if record.levelname == "WARNING"]
    assert len(warnings) == int(bool(return_code))
    if return_code:
        assert f"退出码 {return_code}" in warnings[0].message
        assert "未确认仪器安全" in warnings[0].message
        assert "正常取消也可能如此" in warnings[0].message
        assert adapter.acquisition_module in warnings[0].message
    process.wait.assert_called_once_with()
    process.terminate.assert_not_called()
    process.kill.assert_not_called()


def test_real_child_result_ignores_newer_unreported_and_other_experiment_runs(adapter):
    old = make_run(adapter.root, "old")
    original = (old / "experiment_config.yaml").read_bytes()
    result = adapter.run({"scenario": "normal"}, None, None)
    path = Path(result["run_dir"])
    assert path.name.endswith("_current")
    assert json.loads((path / "results/received.json").read_text()) == str(path)
    assert (adapter.root / "data/Example/unreported").is_dir()
    assert (old / "experiment_config.yaml").read_bytes() == original
    assert yaml.safe_load((path / "experiment_config.yaml").read_text(encoding="utf-8"))["analysis_status"] == "completed"


@pytest.mark.parametrize("scenario, message", [
    ("missing", "未上报"), ("other_type", "未上报"), ("outside", "不属于本实验"),
    ("relative", "绝对路径"), ("missing_config", "缺少"), ("identity", "身份不匹配"),
    ("malformed", "无效的运行目录"), ("failure", "异常结束"), ("ambiguous", "唯一"),
])
def test_real_child_invalid_result_never_falls_back_to_history(adapter, scenario, message):
    old = make_run(adapter.root, "old")
    original = {path: path.read_bytes() for path in old.rglob("*") if path.is_file()}
    with pytest.raises(RuntimeError, match=message):
        adapter.run({"scenario": scenario}, None, None)
    assert {path: path.read_bytes() for path in old.rglob("*") if path.is_file()} == original
    assert not list((adapter.root / "data").rglob("received.json"))


def test_same_data_type_child_does_not_replace_parent_result(adapter):
    result = adapter.run({"scenario": "same_type_child"}, None, None)
    path = Path(result["run_dir"])
    assert path.name.endswith("_current")
    assert json.loads((path / "results/received.json").read_text()) == str(path)
    assert not list((adapter.root / "data/Example").glob("*_child/results/received.json"))


def test_missing_result_is_rejected_without_analyzer(adapter):
    adapter.analysis_module = None
    with pytest.raises(RuntimeError, match="未上报"):
        adapter.run({"scenario": "missing"}, None, None)


def test_real_child_cancellation_waits_for_safety_cleanup(adapter):
    token, errors = CancellationToken(), []
    def run():
        try:
            adapter.run({"scenario": "cancel"}, None, token)
        except BaseException as exc:
            errors.append(exc)
    thread = Thread(target=run)
    thread.start()
    try:
        wait_for(lambda: (adapter.root / "ready").exists())
        token.cancel()
        wait_for(lambda: (adapter.root / "cleanup_entered").exists())
        assert thread.is_alive(), "子进程收尾完成之前不能向调用方返回"
        assert not (adapter.root / "cleanup_finished").exists()
    finally:
        (adapter.root / "allow_cleanup").touch()
        thread.join(20)
    assert not thread.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], WorkflowCancelled)
    assert (adapter.root / "cleanup_finished").exists()
    assert not list((adapter.root / "data/.gui_jobs").glob("*.cancel"))
    assert not list((adapter.root / "data").rglob("received.json"))


def test_progress_callback_failure_preserves_error_and_waits_for_cleanup(adapter):
    (adapter.root / "allow_cleanup").touch()
    def broken(event):
        raise ValueError("父进程回调原始错误")
    with pytest.raises(ValueError, match="父进程回调原始错误"):
        adapter._execute(adapter.acquisition_module, parameters={"scenario": "cancel"}, progress=broken)
    assert (adapter.root / "cleanup_finished").exists()
    assert not list((adapter.root / "data/.gui_jobs").glob("*.cancel"))


def test_analysis_failure_preserves_raw_and_records_status_atomically(adapter, monkeypatch):
    monkeypatch.setenv("TYPED_TEST_ANALYSIS_FAILURE", "1")
    with pytest.raises(RuntimeError, match="自动分析失败"):
        adapter.run({"scenario": "normal"}, None, None)
    path = next((adapter.root / "data/Example").glob("*_current"))
    assert (path / "raw/record.txt").read_text() == "raw"
    assert yaml.safe_load((path / "experiment_config.yaml").read_text(encoding="utf-8"))["analysis_status"] == "failed"


def test_launch_does_not_inherit_previous_run_context(adapter, monkeypatch):
    old = make_run(adapter.root, "old")
    before = (old / "experiment_config.yaml").read_bytes()
    monkeypatch.setenv("LAB_RUN_DIR", str(old))
    monkeypatch.setenv("LAB_TYPED_PARAMETERS", "invalid inherited parameters")
    result = adapter.run({"scenario": "normal"}, None, None)
    assert result["run_dir"] != str(old)
    assert not (old / "results/received.json").exists()
    assert (old / "experiment_config.yaml").read_bytes() == before


def test_analysis_status_write_failure_preserves_config_and_original_error(tmp_path, monkeypatch):
    path = make_run(tmp_path)
    config_path = path / "experiment_config.yaml"
    before = config_path.read_bytes()
    def fail(*args):
        raise OSError("状态写入失败")
    monkeypatch.setattr(typed, "write_run_config", fail)
    typed.TypedWorkflowAdapter._write_analysis_status(path, status="failed", error="原始分析错误")
    assert config_path.read_bytes() == before


def registered_definitions():
    if __name__ == "__main__":
        return []
    from .registry import list_experiments
    return list_experiments()


@pytest.mark.parametrize("definition", registered_definitions(), ids=lambda value: value.id)
def test_all_registered_adapters_use_explicit_result_and_keep_analysis_contract(definition, tmp_path, monkeypatch):
    """只验证真实适配器契约，不冒充各实验的科学或采集业务验收。"""
    value = definition.experiment_runner.__self__
    assert isinstance(value, typed.TypedWorkflowAdapter)
    monkeypatch.setattr(value, "root", tmp_path)
    monkeypatch.setattr(value, "_resolved", lambda values: SimpleNamespace(to_external=lambda: values))
    monkeypatch.setattr(value, "preflight", lambda values: [])
    path = make_run(tmp_path, data_type=value.data_type, experiment_id=value.id)
    calls = []
    def execute(module, **kwargs):
        calls.append((module, kwargs.get("run_dir")))
        if module == value.acquisition_module:
            return path
        (path / "results/analysis_summary.json").write_text(json.dumps({"status": "completed", "error": None}))
    monkeypatch.setattr(value, "_execute", execute)
    assert value.run({}, None, None)["run_dir"] == str(path)
    if value.analysis_module:
        assert calls[-1] == (value.analysis_module, path)
    calls.clear()
    monkeypatch.setattr(value, "_execute", lambda *args, **kwargs: calls.append(args) or None)
    with pytest.raises(RuntimeError, match="未上报"):
        value.run({}, None, None)
    assert len(calls) == 1, "缺少本次目录时不能启动任何分析器"


def child_main():
    """专用于上述真实子进程测试，不解析或连接任何真实仪器。"""
    root = Path.cwd()
    if os.environ.get("LAB_RUN_DIR"):
        if os.environ.get("TYPED_TEST_ANALYSIS_FAILURE"):
            raise RuntimeError("原始分析错误")
        path = Path(os.environ["LAB_RUN_DIR"])
        (path / "results/received.json").write_text(json.dumps(str(path)))
        return
    scenario = json.loads(os.environ["LAB_TYPED_PARAMETERS"])["scenario"]
    if scenario == "missing":
        return
    if scenario in ("outside", "relative", "malformed"):
        path = make_run(root / "foreign")
        value = str(path) if scenario == "outside" else "relative" if scenario == "relative" else 42
        report_runtime_progress("run_directory", "测试目录", data={"run_directory": {"data_type": "Example", "path": value}})
        return
    from ..steps import run_directory
    snapshot = dict(resolved_mapping={}, device_library_revision="library", physical_mapping_revision="mapping",
        device_library={}, physical_mappings={})
    with patch.object(run_directory, "instrument_config_snapshot", return_value=snapshot), \
            patch.object(run_directory, "load_safety_limits", return_value={}):
        path = create_run_directory("Other" if scenario == "other_type" else "Example", "current", {}, project_root=root).root
    (path / "raw/record.txt").write_text("raw")
    if scenario == "identity":
        write_run_config(path / "experiment_config.yaml", {"experiment_id": "wrong", "experiment_type": "Example"})
    elif scenario == "missing_config":
        (path / "experiment_config.yaml").unlink()
    elif scenario == "failure":
        raise RuntimeError("采集原始错误")
    elif scenario == "cancel":
        (root / "ready").touch()
        try:
            while True:
                check_cancelled()
                time.sleep(0.01)
        finally:
            (root / "cleanup_entered").touch()
            wait_for(lambda: (root / "allow_cleanup").exists())
            (root / "cleanup_finished").touch()
    elif scenario in ("same_type_child", "ambiguous"):
        write_run_config(path / "experiment_config.yaml", {"experiment_id": "example", "experiment_type": "Example"})
        with patch.object(run_directory, "instrument_config_snapshot", return_value=snapshot), \
                patch.object(run_directory, "load_safety_limits", return_value={}):
            child = create_run_directory("Example", "child", {}, project_root=root).root
        write_run_config(child / "experiment_config.yaml", {
            "experiment_id": "child" if scenario == "same_type_child" else "example", "experiment_type": "Example",
        })
    elif scenario == "normal":
        decoy = make_run(root, "unreported")
        os.utime(decoy, (time.time() + 10000, time.time() + 10000))
        with patch.object(run_directory, "instrument_config_snapshot", return_value=snapshot), \
                patch.object(run_directory, "load_safety_limits", return_value={}):
            create_run_directory("Other", "other", {}, project_root=root)


if __name__ == "__main__":
    child_main()
