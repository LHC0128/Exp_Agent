"""DirectAW 串行命令入口，复用取消、修订检查和 EOF 安全收尾协议。"""
import json
import os
import sys
from ...experiment_runtime import load_runtime_params
from lab_workflows.workflow_session import serve
from .models import RFSensitivityDirectAWParams
from .stages import MULTI_ACQUISITION as ACQUISITION, FINISH_ID, validate_selection
from .workflow import Workflow


def main():
    payload = json.loads(os.environ.get("LAB_TYPED_PARAMETERS", "{}"))
    session_id = payload.pop("session_id")
    os.environ["LAB_TYPED_PARAMETERS"] = json.dumps(payload)
    ctx = Workflow(load_runtime_params(RFSensitivityDirectAWParams), session_id=session_id)
    serve(ctx, sys.stdin, stages=ACQUISITION, validate=validate_selection, finish_id=FINISH_ID)


if __name__ == "__main__":
    main()
