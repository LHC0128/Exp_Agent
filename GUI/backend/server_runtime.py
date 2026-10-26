"""在 Uvicorn 等待长连接之前封口，并等待真实任务安全收尾。"""

from __future__ import annotations

import asyncio
import socket
from types import FrameType
from typing import Protocol

import uvicorn


async def await_shutdown(waiter: asyncio.Future[None]) -> None:
    """真实收尾完成后才传播取消；重复取消不使调用协程提前返回。"""
    cancelled = False
    while not waiter.done():
        try:
            await asyncio.shield(waiter)
        except asyncio.CancelledError:
            # shield 只保护底层等待；外层也必须留在这里，不能提前返回。
            cancelled = True
    waiter.result()
    if cancelled:
        raise asyncio.CancelledError


class _ShutdownManager(Protocol):
    def begin_shutdown(self) -> None: ...

    def wait_for_shutdown(self) -> None: ...


class Server(uvicorn.Server):
    """注入任务管理器；重复退出信号和异步取消都不绕过真实收尾。

    已按 Uvicorn 0.51.0 核对：serve 在 capture_signals 内调用 _serve，
    shutdown 先等待连接/请求，最后才关闭 lifespan。升级时须保留对应回归。
    """

    def __init__(self, config: uvicorn.Config, manager: _ShutdownManager) -> None:
        super().__init__(config)
        self._manager = manager
        self._shutdown_waiter: asyncio.Future[None] | None = None

    def handle_exit(self, sig: int, frame: FrameType | None) -> None:
        # 信号可能重入持锁中的主线程：只赋值，不调管理器、日志或 join。
        # 不调父方法，既不设置 force_exit，也不记录供 capture_signals 重放的信号；
        # 否则恢复 asyncio.Runner 的旧处理器后仍可能触发取消或 KeyboardInterrupt。
        self.should_exit = True

    async def _serve(self, sockets: list[socket.socket] | None = None) -> None:
        try:
            await super()._serve(sockets)
        finally:
            # 启动失败、主循环异常/取消同样收尾；此时信号处理器尚未恢复。
            await self._wait_for_shutdown()

    async def shutdown(self, sockets: list[socket.socket] | None = None) -> None:
        self._start_shutdown()
        try:
            await super().shutdown(sockets=sockets)
        finally:
            await self._wait_for_shutdown()

    def _start_shutdown(self) -> asyncio.Future[None]:
        if self._shutdown_waiter is None:
            self._manager.begin_shutdown()
            # 独立于 Uvicorn 的连接任务集合，连接超时不能取消真实 worker 的等待。
            self._shutdown_waiter = asyncio.get_running_loop().run_in_executor(
                None, self._manager.wait_for_shutdown,
            )
        return self._shutdown_waiter

    async def _wait_for_shutdown(self) -> None:
        await await_shutdown(self._start_shutdown())
