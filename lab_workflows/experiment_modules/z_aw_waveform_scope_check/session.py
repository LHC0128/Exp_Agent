"""复用串行命令、修订和取消协议，EOF 自动安全结束。"""
import json
import os
import sys

from ...experiment_runtime import load_runtime_params
from ..detection_chain_frequency_response.session import serve as serve_commands
from .models import ZAWWaveformScopeCheckParams
from .stages import ACQUISITION, validate_selection
from .workflow import Workflow


def serve(ctx, stream):
    return serve_commands(ctx, stream, stages=ctx.acquisition_stages,
                          validate=validate_selection, finish_id=ctx.finish_id)


def main():
    payload = json.loads(os.environ.get("LAB_TYPED_PARAMETERS", "{}"))
    session_id = payload.pop("session_id")
    os.environ["LAB_TYPED_PARAMETERS"] = json.dumps(payload)
    serve(Workflow(load_runtime_params(ZAWWaveformScopeCheckParams), session_id=session_id), sys.stdin)


if __name__ == "__main__":
    main()
