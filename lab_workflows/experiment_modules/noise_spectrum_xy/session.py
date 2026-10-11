"""stdin 串行命令会话；父进程断开或取消时自动安全结束。"""

import json
import os
import sys
from queue import Empty, Queue
from threading import Thread
from pathlib import Path
from threading import Event

from ...common import WorkflowCancelled
from ...experiment_runtime import check_cancelled, load_runtime_params, report_runtime_progress
from .models import NoiseSpectrumXYParams
from .stages import ACQUISITION, validate_selection
from .workflow import Workflow


def serve(ctx, stream):
    commands = Queue()
    disconnected = Event()

    def read_commands():
        try:
            for line in stream:
                commands.put(line)
        finally:
            disconnected.set()
            if cancel_path := os.environ.get("LAB_CANCEL_FILE"):
                Path(cancel_path).touch(exist_ok=True)
            commands.put(None)

    Thread(target=read_commands, daemon=True).start()
    seen = set()
    failure = None
    try:
        ctx.publish()
        while not ctx.closed:
            check_cancelled()
            try:
                line = commands.get(timeout=0.1)
            except Empty:
                continue
            if line is None:
                break
            request_id = None
            try:
                command = json.loads(line)
                request_id = command["request_id"]
                if not isinstance(request_id, str) or request_id in seen:
                    raise ValueError("重复或无效的请求编号")
                seen.add(request_id)
                if command["revision"] != ctx.revision:
                    raise ValueError("会话修订已改变，请刷新后重试")
                action = command["action"]
                if action == "steps":
                    validate_selection(ACQUISITION, ctx.states, command["step_ids"])
                    if command["step_ids"] != ["A14"]:
                        ctx.recheck()
                elif action == "parameters":
                    ctx.update_parameters(command["parameters"])
                elif action != "finish":
                    raise ValueError("未知会话命令")
            except (KeyError, TypeError, ValueError) as exc:
                report_runtime_progress("waiting", str(exc), data={"command_result": {
                    "request_id": request_id, "error": str(exc)}, "workflow_session": ctx.snapshot()})
                continue
            if action == "steps":
                ctx.execute(command["step_ids"])
            elif action == "finish":
                ctx.finish()
            report_runtime_progress("waiting", "命令已完成", data={"command_result": {
                "request_id": request_id, "error": None}, "workflow_session": ctx.snapshot()})
    except WorkflowCancelled as exc:
        failure = exc
        if not disconnected.is_set():
            raise
    except BaseException as exc:
        failure = exc
        raise
    finally:
        ctx.finish(failure)


def main():
    payload = json.loads(os.environ.get("LAB_TYPED_PARAMETERS", "{}"))
    session_id = payload.pop("session_id")
    os.environ["LAB_TYPED_PARAMETERS"] = json.dumps(payload)
    ctx = Workflow(load_runtime_params(NoiseSpectrumXYParams), session_id=session_id)
    serve(ctx, sys.stdin)


if __name__ == "__main__":
    main()
