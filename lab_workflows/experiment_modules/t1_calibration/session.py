"""T1 分步会话复用串行命令、修订、取消和 EOF 安全协议。"""
import json
import os
import sys
from ...experiment_runtime import load_runtime_params
from lab_workflows.workflow_session import serve as serve_commands
from .models import T1CalibrationParams
from .stages import ACQUISITION, FINISH_ID, validate_selection
from .workflow import Workflow


def serve(ctx, stream):
    return serve_commands(ctx, stream, stages=ACQUISITION, validate=validate_selection, finish_id=FINISH_ID)


def main():
    payload = json.loads(os.environ["LAB_TYPED_PARAMETERS"])
    session_id = payload.pop("session_id")
    os.environ["LAB_TYPED_PARAMETERS"] = json.dumps(payload)
    serve(Workflow(load_runtime_params(T1CalibrationParams), session_id=session_id), sys.stdin)


if __name__ == "__main__":
    main()
