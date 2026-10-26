"""Uvicorn 0.51.0 退出时序回归；只用假监听器、ASGI 和信号注册表。"""

from __future__ import annotations

import asyncio
import signal
import unittest
from contextlib import contextmanager
from threading import Event, Lock
from unittest.mock import AsyncMock, Mock, call, patch

import uvicorn
from uvicorn.server import HANDLED_SIGNALS

from .server_runtime import Server


class FakeManager:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.lifecycle_lock = Lock()
        self.begun = Event()
        self.wait_entered = Event()
        self.release = Event()
        self.finished = Event()
        self.on_begin = lambda: None

    def begin_shutdown(self) -> None:
        # 持锁时模拟信号，若回调误入管理器则立即失败，而不是挂住测试。
        if not self.lifecycle_lock.acquire(blocking=False):
            raise AssertionError("信号回调不应获取任务管理器锁")
        try:
            self.events.append("manager.begin")
            self.begun.set()
            self.on_begin()
        finally:
            self.lifecycle_lock.release()

    def wait_for_shutdown(self) -> None:
        self.wait_entered.set()
        if not self.release.wait(2):
            raise AssertionError("测试没有释放假 worker 的收尾门闩")
        self.events.append("manager.finished")
        self.finished.set()


class FakeASGI:
    def __init__(self, events: list[str], *, fail_startup: bool = False) -> None:
        self.events = events
        self.fail_startup = fail_startup
        self.stream_started = asyncio.Event()
        self.close_stream = asyncio.Event()
        self.stream_cancelled = asyncio.Event()
        self.lifespan_stopped = asyncio.Event()

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "lifespan":
            assert (await receive())["type"] == "lifespan.startup"
            if self.fail_startup:
                await send({"type": "lifespan.startup.failed", "message": "假启动失败"})
                return
            await send({"type": "lifespan.startup.complete"})
            assert (await receive())["type"] == "lifespan.shutdown"
            self.events.append("lifespan.shutdown")
            self.lifespan_stopped.set()
            await send({"type": "lifespan.shutdown.complete"})
            return
        await send({"type": "http.response.start", "status": 200, "headers": []})
        self.stream_started.set()
        try:
            await self.close_stream.wait()
        except asyncio.CancelledError:
            self.stream_cancelled.set()
            raise
        await send({"type": "http.response.body", "body": b"data: stopped\n\n"})
        self.events.append("stream.finished")


class FakeListener:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def close(self) -> None:
        self.events.append("listener.close")

    async def wait_closed(self) -> None:
        self.events.append("listener.closed")


class FakeSocket:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def close(self) -> None:
        self.events.append("socket.close")


class FakeConnection:
    def __init__(self, manager: FakeManager, events: list[str]) -> None:
        self.manager = manager
        self.events = events

    def shutdown(self) -> None:
        assert self.manager.begun.is_set(), "应先取消任务，再通知长连接退出"
        self.events.append("connection.shutdown")


@contextmanager
def fake_signals():
    """执行真实 capture_signals，但不改真实处理器，也不向 PID 发信号。"""
    originals = {sig: Mock(name=f"original_{sig}") for sig in HANDLED_SIGNALS}
    handlers = dict(originals)

    def register(sig, handler):
        previous = handlers[sig]
        handlers[sig] = handler
        return previous

    with patch("uvicorn.server.signal.signal", side_effect=register), \
            patch("uvicorn.server.signal.raise_signal") as replay:
        yield handlers, originals, replay


class ServerRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def make_runtime(self, *, timeout=None, fail_startup=False, server_type=Server):
        events: list[str] = []
        manager = FakeManager(events)
        self.addCleanup(manager.release.set)
        app = FakeASGI(events, fail_startup=fail_startup)
        config = uvicorn.Config(
            app, lifespan="on", http="h11", ws="none", log_config=None,
            timeout_graceful_shutdown=timeout,
        )
        server = server_type(config, manager)
        listener = FakeListener(events)
        sock = FakeSocket(events)
        return server, manager, app, listener, sock, events

    async def wait_event(self, event: Event) -> None:
        self.assertTrue(await asyncio.to_thread(event.wait, 1))

    async def attach_stream(self, server: Server, manager: FakeManager, app: FakeASGI, events):
        connection = FakeConnection(manager, events)
        server.server_state.connections.add(connection)

        async def run_stream():
            try:
                await app({"type": "http"}, AsyncMock(), AsyncMock())
            finally:
                server.server_state.connections.discard(connection)

        task = asyncio.create_task(run_stream())
        server.server_state.tasks.add(task)
        task.add_done_callback(server.server_state.tasks.discard)
        await asyncio.wait_for(app.stream_started.wait(), 1)
        return task

    async def test_should_exit_begins_cleanup_before_long_connection_wait(self):
        server, manager, app, listener, sock, events = self.make_runtime()
        stream = await self.attach_stream(server, manager, app, events)
        manager.on_begin = app.close_stream.set
        server.should_exit = True
        with fake_signals() as (handlers, originals, replay), \
                patch.object(asyncio.get_running_loop(), "create_server", AsyncMock(return_value=listener)):
            serving = asyncio.create_task(server.serve(sockets=[sock]))
            await asyncio.wait_for(app.lifespan_stopped.wait(), 1)
            await self.wait_event(manager.wait_entered)
            self.assertFalse(serving.done(), "真实 worker 未结束时不应返回")
            self.assertFalse(manager.finished.is_set())
            manager.release.set()
            await asyncio.wait_for(serving, 1)
            self.assertEqual(handlers, originals)
            replay.assert_not_called()
        await stream
        self.assertLess(events.index("manager.begin"), events.index("listener.close"))
        self.assertLess(events.index("manager.begin"), events.index("connection.shutdown"))
        self.assertLess(events.index("stream.finished"), events.index("lifespan.shutdown"))
        self.assertIn("listener.closed", events)
        self.assertIn("socket.close", events)
        self.assertTrue(manager.finished.is_set())
        self.assertEqual(events.count("manager.begin"), 1)

    async def test_reentrant_and_repeated_signals_do_not_force_or_replay(self):
        class ReentrantServer(Server):
            def __setattr__(self, name, value):
                if name == "should_exit" and getattr(self, "reenter", False):
                    self.reenter = False
                    self.handle_exit(signal.SIGINT, None)
                super().__setattr__(name, value)

        server, manager, app, listener, sock, events = self.make_runtime(server_type=ReentrantServer)
        stream = await self.attach_stream(server, manager, app, events)
        manager.on_begin = app.close_stream.set
        with fake_signals() as (handlers, originals, replay), \
                patch.object(asyncio.get_running_loop(), "create_server", AsyncMock(return_value=listener)):
            async def request_exit():
                with manager.lifecycle_lock:
                    server.reenter = True
                    handlers[signal.SIGINT](signal.SIGINT, None)
                    handlers[signal.SIGINT](signal.SIGINT, None)
                self.assertFalse(manager.begun.is_set(), "signal handler 只能记录退出意图")

            server.config.callback_notify = request_exit
            serving = asyncio.create_task(server.serve(sockets=[sock]))
            await asyncio.wait_for(app.lifespan_stopped.wait(), 1)
            await self.wait_event(manager.wait_entered)
            for sig in HANDLED_SIGNALS:
                handlers[sig](sig, None)
            self.assertFalse(server.force_exit)
            self.assertFalse(serving.done())
            manager.release.set()
            await asyncio.wait_for(serving, 1)
            self.assertEqual(handlers, originals)
            replay.assert_not_called()
        await stream
        self.assertEqual(server._captured_signals, [])
        self.assertTrue(manager.finished.is_set())

    async def test_connection_timeout_cannot_skip_real_cleanup(self):
        server, manager, app, listener, sock, events = self.make_runtime(timeout=0.001)
        stream = await self.attach_stream(server, manager, app, events)
        server.should_exit = True
        with fake_signals(), \
                patch.object(asyncio.get_running_loop(), "create_server", AsyncMock(return_value=listener)):
            serving = asyncio.create_task(server.serve(sockets=[sock]))
            await asyncio.wait_for(app.lifespan_stopped.wait(), 1)
            await asyncio.wait_for(app.stream_cancelled.wait(), 1)
            self.assertFalse(serving.done())
            self.assertFalse(manager.finished.is_set())
            self.assertFalse(server._shutdown_waiter.cancelled())
            manager.release.set()
            await asyncio.wait_for(serving, 1)
        with self.assertRaises(asyncio.CancelledError):
            await stream
        self.assertTrue(manager.finished.is_set())
        self.assertLess(events.index("manager.begin"), events.index("lifespan.shutdown"))

    async def test_cancellation_before_shutdown_waits_inside_signal_capture(self):
        server, manager, app, listener, sock, events = self.make_runtime()
        ticking = asyncio.Event()

        async def block_main_loop():
            ticking.set()
            await asyncio.Event().wait()

        server.config.callback_notify = block_main_loop
        with fake_signals() as (handlers, originals, replay), \
                patch.object(asyncio.get_running_loop(), "create_server", AsyncMock(return_value=listener)):
            serving = asyncio.create_task(server.serve(sockets=[sock]))
            await asyncio.wait_for(ticking.wait(), 1)
            self.assertFalse(manager.begun.is_set())
            serving.cancel()
            await self.wait_event(manager.wait_entered)
            serving.cancel()
            await asyncio.sleep(0)
            serving.cancel()
            await asyncio.sleep(0)
            self.assertFalse(serving.done())
            self.assertIs(handlers[signal.SIGINT].__self__, server)
            self.assertFalse(server._shutdown_waiter.cancelled())
            manager.release.set()
            with self.assertRaises(asyncio.CancelledError):
                await serving
            self.assertEqual(handlers, originals)
            replay.assert_not_called()
        await server.lifespan.shutdown()
        self.assertTrue(manager.finished.is_set())
        self.assertEqual(events.count("manager.begin"), 1)

    async def test_outer_wait_for_timeout_is_delayed_until_real_cleanup(self):
        server, manager, app, listener, sock, events = self.make_runtime()
        server.should_exit = True
        with fake_signals(), \
                patch.object(asyncio.get_running_loop(), "create_server", AsyncMock(return_value=listener)):
            serving = asyncio.create_task(server.serve(sockets=[sock]))
            await self.wait_event(manager.wait_entered)
            timed = asyncio.create_task(asyncio.wait_for(serving, timeout=0))
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            self.assertFalse(timed.done(), "wait_for 也必须等真实收尾后才能报告超时")
            self.assertFalse(serving.done())
            manager.release.set()
            with self.assertRaises(asyncio.TimeoutError):
                await timed
        await server.lifespan.shutdown()
        self.assertTrue(manager.finished.is_set())

    async def test_startup_failure_still_runs_real_cleanup(self):
        server, manager, app, listener, sock, events = self.make_runtime(fail_startup=True)
        manager.release.set()
        with fake_signals() as (handlers, originals, replay), \
                patch.object(asyncio.get_running_loop(), "create_server", AsyncMock()) as create_server:
            with self.assertRaises(SystemExit):
                await server.serve(sockets=[sock])
            create_server.assert_not_called()
            self.assertEqual(handlers, originals)
            replay.assert_not_called()
        self.assertTrue(manager.begun.is_set())
        self.assertTrue(manager.finished.is_set())
        self.assertFalse(server.started)

    async def test_installed_uvicorn_capture_replays_and_second_sigint_forces(self):
        # 对照当前框架行为，防止仅测 adapter.handle_exit 字段却漏掉上下文退出重放。
        server = uvicorn.Server(uvicorn.Config(FakeASGI([]), log_config=None))
        with fake_signals() as (handlers, originals, replay):
            with server.capture_signals():
                handlers[signal.SIGINT](signal.SIGINT, None)
                handlers[signal.SIGINT](signal.SIGINT, None)
            self.assertEqual(handlers, originals)
            self.assertTrue(server.force_exit)
            self.assertEqual(replay.call_args_list, [call(signal.SIGINT), call(signal.SIGINT)])


if __name__ == "__main__":
    unittest.main()
