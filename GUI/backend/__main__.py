"""Bell-Bloom FastAPI 后端启动入口。"""

from __future__ import annotations

import argparse
import ctypes
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Sequence

import uvicorn

from .jobs import manager
from .main import app
from .server_runtime import Server


HOST = "127.0.0.1"
PORT = 8000
_WATCH_INTERVAL_S = 0.5
_SYNCHRONIZE = 0x00100000
_WAIT_TIMEOUT = 0x00000102


def _parse_options(argv: Sequence[str]) -> argparse.Namespace:
    # 不使用 add_help，避免测试运行器的参数被入口误当成帮助请求。
    parser = argparse.ArgumentParser(prog="python -m backend", add_help=False)
    parser.add_argument("--log-file", type=Path)
    parser.add_argument("--stop-file", type=Path)
    parser.add_argument("--parent-pid", type=int)
    options, _ = parser.parse_known_args(argv)
    return options


def _redirect_output(log_file: Path) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    stream = log_file.open("a", encoding="utf-8", buffering=1)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    stream.write(f"\n===== {stamp} backend start pid={os.getpid()} =====\n")
    stream.flush()
    sys.stdout = stream
    sys.stderr = stream


def _process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
        kernel32.WaitForSingleObject.restype = ctypes.c_uint32
        kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
        handle = kernel32.OpenProcess(_SYNCHRONIZE, False, pid)
        if not handle:
            return False
        try:
            return kernel32.WaitForSingleObject(handle, 0) == _WAIT_TIMEOUT
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _watch_exit(
    application,
    stop_file: Path | None,
    parent_pid: int | None,
    stop_requested: threading.Event,
) -> None:
    """停止文件出现或启动器退出时，请求服务走正常收尾。"""
    while True:
        if stop_file is not None and stop_file.exists():
            break
        if parent_pid is not None and not _process_alive(parent_pid):
            break
        time.sleep(_WATCH_INTERVAL_S)
    stop_requested.set()
    while True:
        server = getattr(application.state, "uvicorn_server", None)
        if server is not None:
            server.should_exit = True
            return
        time.sleep(0.05)


def _restart_arguments(options: argparse.Namespace) -> list[str]:
    arguments: list[str] = []
    if options.log_file is not None:
        arguments += ["--log-file", str(options.log_file)]
    if options.stop_file is not None:
        arguments += ["--stop-file", str(options.stop_file)]
    if options.parent_pid is not None:
        arguments += ["--parent-pid", str(options.parent_pid)]
    return arguments


def main(argv: Sequence[str] | None = None) -> None:
    """启动后端，并支持从运行中的服务优雅自重启。"""
    options = _parse_options(sys.argv[1:] if argv is None else argv)
    if options.log_file is not None:
        _redirect_output(options.log_file)
    stop_requested = threading.Event()
    if options.stop_file is not None or options.parent_pid is not None:
        threading.Thread(
            target=_watch_exit,
            args=(app, options.stop_file, options.parent_pid, stop_requested),
            name="backend-exit-watch",
            daemon=True,
        ).start()
    while True:
        app.state.restart_requested = False
        server = Server(
            uvicorn.Config(app, host=HOST, port=PORT, reload=False),
            manager,
        )
        app.state.uvicorn_server = server
        if stop_requested.is_set():
            server.should_exit = True
        server.run()
        if stop_requested.is_set() or not app.state.restart_requested:
            return
        os.execv(
            sys.executable,
            [sys.executable, "-m", "backend", *_restart_arguments(options)],
        )


if __name__ == "__main__":
    main()
