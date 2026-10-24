"""复用已有串行命令、修订与 EOF/取消收尾协议。"""
import json
import os
import sys
from ...experiment_runtime import load_runtime_params
from ..detection_chain_frequency_response.session import serve as serve_commands
from .models import PhotonShotNoiseParams
from .stages import ACQUISITION, FINISH_ID, validate_selection
from .workflow import Workflow


def serve(ctx, stream):
    return serve_commands(ctx, stream, stages=ACQUISITION, validate=validate_selection, finish_id=FINISH_ID)


def main():
    payload = json.loads(os.environ["LAB_TYPED_PARAMETERS"])
    session_id = payload.pop("session_id")
    os.environ["LAB_TYPED_PARAMETERS"] = json.dumps(payload)
    serve(Workflow(load_runtime_params(PhotonShotNoiseParams), session_id=session_id), sys.stdin)


if __name__ == "__main__":
    main()
