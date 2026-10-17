"""三维平衡网格的串行命令和通信 EOF 安全收尾入口。"""

import json
import os
import sys

from ...experiment_runtime import load_runtime_params
from ..noise_spectrum_xy.session import serve
from .models import MxZOptimalControlDG4000BiasXYZBalanceParams
from .stages import ACQUISITION, FINISH_ID, validate_selection
from .workflow import Workflow


def main():
    payload = json.loads(os.environ.get("LAB_TYPED_PARAMETERS", "{}"))
    session_id = payload.pop("session_id")
    os.environ["LAB_TYPED_PARAMETERS"] = json.dumps(payload)
    ctx = Workflow(load_runtime_params(MxZOptimalControlDG4000BiasXYZBalanceParams), session_id=session_id)
    serve(ctx, sys.stdin, stages=ACQUISITION, validate=validate_selection, finish_id=FINISH_ID)


if __name__ == "__main__":
    main()
