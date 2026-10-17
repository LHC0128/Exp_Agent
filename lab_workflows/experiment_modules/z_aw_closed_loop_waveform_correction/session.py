"""闭环会话复用串行命令、修订、取消和 EOF 安全结束。"""
import json
import os
import sys
from ...experiment_runtime import load_runtime_params
from ..z_aw_waveform_scope_check.session import serve
from .models import ZAWClosedLoopWaveformCorrectionParams
from .workflow import Workflow


def main():
    payload = json.loads(os.environ.get("LAB_TYPED_PARAMETERS", "{}"))
    session_id = payload.pop("session_id")
    os.environ["LAB_TYPED_PARAMETERS"] = json.dumps(payload)
    serve(Workflow(load_runtime_params(ZAWClosedLoopWaveformCorrectionParams), session_id=session_id), sys.stdin)


if __name__ == "__main__":
    main()
