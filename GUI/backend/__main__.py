"""Bell-Bloom FastAPI 后端启动入口。"""

from __future__ import annotations

import os
import sys

import uvicorn

from .jobs import manager
from .main import app
from .server_runtime import Server


HOST = "127.0.0.1"
PORT = 8000


def main() -> None:
    """启动后端，并支持从运行中的服务优雅自重启。"""
    while True:
        app.state.restart_requested = False
        server = Server(
            uvicorn.Config(app, host=HOST, port=PORT, reload=False),
            manager,
        )
        app.state.uvicorn_server = server
        server.run()
        if not app.state.restart_requested:
            return
        os.execv(sys.executable, [sys.executable, "-m", "backend"])


if __name__ == "__main__":
    main()
