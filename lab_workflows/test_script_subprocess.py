"""公共子进程执行器与 script_runner 的端到端测试：真实子进程跑临时脚本，不连接仪器。"""

from __future__ import annotations

import io
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from lab_workflows import script_subprocess
from lab_workflows.common import CancellationToken
from lab_workflows.script_subprocess import run_script_runner


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """临时根目录；子进程改用当前解释器（临时根下没有 agent_exp_env）。"""
    real_popen = subprocess.Popen

    def popen(command, *args, **kwargs):
        if isinstance(command, list) and "lab_workflows.script_runner" in command:
            command = [sys.executable, *command[1:]]
        return real_popen(command, *args, **kwargs)

    monkeypatch.setattr(script_subprocess.subprocess, "Popen", popen)
    return tmp_path


def _script(root: Path, relative: str, source: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(source), encoding="utf-8")
    return path


def test_script_mode_sets_argv_and_script_dir_import(root: Path) -> None:
    _script(root, "scripts/demo/helper.py", "GREETING = 'hello'\n")
    script = _script(root, "scripts/demo/main.py", """
        import sys
        from helper import GREETING

        def main():
            print(GREETING, *sys.argv[1:])
            return 0

        if __name__ == "__main__":
            raise SystemExit(main())
    """)
    lines: list[str] = []
    log = io.StringIO()
    code = run_script_runner(root, script, token="t1", argv=["--vpp", "0.2"],
                             on_line=lines.append, log=log)
    assert code == 0
    assert lines == ["hello --vpp 0.2"]
    assert "hello --vpp 0.2" in log.getvalue()
    assert list((root / "tmp" / "gui_jobs").iterdir()) == []


def test_script_mode_propagates_nonzero_exit(root: Path) -> None:
    script = _script(root, "scripts/fail.py", "raise SystemExit(4)\n")
    assert run_script_runner(root, script, token="t2", argv=[], on_line=lambda _: None) == 4


def test_overrides_mode_injects_initial_constant(root: Path) -> None:
    script = _script(root, "experiments/legacy_demo.py", "VALUE = 1\nprint('VALUE', VALUE)\n")
    lines: list[str] = []
    code = run_script_runner(root, script, token="t3", overrides={"VALUE": 5}, on_line=lines.append)
    assert code == 0
    assert lines == ["VALUE 5"]
    assert list((root / "tmp" / "gui_jobs").iterdir()) == []


def _cleanup_script(root: Path, marker: Path) -> Path:
    """采集循环被取消后，finally 里的多步收尾循环必须全部执行。"""
    return _script(root, "scripts/cleanup.py", f"""
        import time
        marker = {str(marker)!r}
        try:
            for index in range(400):
                print("acquire", index, flush=True)
                time.sleep(0.05)
        finally:
            with open(marker, "w", encoding="utf-8") as handle:
                for step in range(3):
                    handle.write(f"step{{step}}\\n")
                    time.sleep(0.05)
                handle.write("done\\n")
    """)


def test_cancel_runs_full_finally_cleanup_before_return(root: Path) -> None:
    marker = root / "cleanup.txt"
    script = _cleanup_script(root, marker)
    token = CancellationToken()

    def on_line(message: str) -> None:
        if message.startswith("acquire"):
            token.cancel()

    code = run_script_runner(root, script, token="t4", argv=[], on_line=on_line, cancellation=token)
    assert code != 0
    assert marker.read_text(encoding="utf-8").splitlines() == ["step0", "step1", "step2", "done"]
    assert list((root / "tmp" / "gui_jobs").iterdir()) == []


def test_library_check_cancelled_sees_cancel_file(root: Path) -> None:
    """库函数里的循环不被插入检查点，靠 LAB_CANCEL_FILE 让 check_cancelled() 生效。"""
    marker = root / "cleanup.txt"
    _script(root, "scripts/lib/slow_library.py", """
        import time
        from lab_workflows.experiment_runtime import check_cancelled

        def wait(seconds):
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                check_cancelled()
                time.sleep(0.05)
    """)
    script = _script(root, "scripts/lib/main.py", f"""
        from slow_library import wait
        print("start", flush=True)
        try:
            wait(30)
        finally:
            open({str(marker)!r}, "w", encoding="utf-8").write("done")
    """)
    token = CancellationToken()
    started = time.monotonic()
    code = run_script_runner(root, script, token="t6", argv=[], cancellation=token,
                             on_line=lambda message: token.cancel())
    assert code != 0
    assert time.monotonic() - started < 15
    assert marker.read_text(encoding="utf-8") == "done"


def test_forwarding_error_requests_stop_and_waits_for_cleanup(root: Path) -> None:
    marker = root / "cleanup.txt"
    script = _cleanup_script(root, marker)

    def on_line(message: str) -> None:
        raise RuntimeError("进度转发失败")

    with pytest.raises(RuntimeError, match="进度转发失败"):
        run_script_runner(root, script, token="t5", argv=[], on_line=on_line)
    # 返回时子进程已走完收尾，不会在硬件锁释放后继续占用仪器。
    assert marker.read_text(encoding="utf-8").splitlines()[-1] == "done"
    assert list((root / "tmp" / "gui_jobs").iterdir()) == []
