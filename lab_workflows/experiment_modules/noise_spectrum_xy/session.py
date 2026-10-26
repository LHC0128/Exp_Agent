"""stdin 串行命令会话；父进程断开或取消时自动安全结束。"""

import json
import os
import sys
from ...experiment_runtime import load_runtime_params
from ...workflow_session import serve as serve_commands
from .models import NoiseSpectrumXYParams
from .stages import ACQUISITION, validate_selection
from .workflow import Workflow


def serve(ctx, stream, *, stages=ACQUISITION, validate=validate_selection, finish_id="A14"):
    return serve_commands(ctx, stream, stages=stages, validate=validate, finish_id=finish_id)


def main():
    payload = json.loads(os.environ.get("LAB_TYPED_PARAMETERS", "{}"))
    session_id = payload.pop("session_id")
    os.environ["LAB_TYPED_PARAMETERS"] = json.dumps(payload)
    ctx = Workflow(load_runtime_params(NoiseSpectrumXYParams), session_id=session_id)
    serve(ctx, sys.stdin)


if __name__ == "__main__":
    main()
