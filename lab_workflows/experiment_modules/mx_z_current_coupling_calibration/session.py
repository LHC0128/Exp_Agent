"""Mx Z 实际电流标定的串行分步子进程与 EOF 安全收尾入口。"""
import json
import os
import sys
from ...experiment_runtime import load_runtime_params
from ..noise_spectrum_xy.session import serve
from .models import MxZCurrentCouplingCalibrationParams
from .stages import ACQUISITION, FINISH_ID, validate_selection
from .workflow import Workflow


def main():
    payload = json.loads(os.environ.get("LAB_TYPED_PARAMETERS", "{}"))
    session_id = payload.pop("session_id")
    os.environ["LAB_TYPED_PARAMETERS"] = json.dumps(payload)
    ctx = Workflow(load_runtime_params(MxZCurrentCouplingCalibrationParams), session_id=session_id)
    serve(ctx, sys.stdin, stages=ACQUISITION, validate=validate_selection, finish_id=FINISH_ID)


if __name__ == "__main__":
    main()
