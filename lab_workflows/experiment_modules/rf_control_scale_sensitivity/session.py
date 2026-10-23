"""复用修订检查、协作取消、EOF 与硬件安全收尾协议。"""
import json
import os
import sys
from ...experiment_runtime import load_runtime_params
from ..noise_spectrum_xy.session import serve
from .models import RFControlScaleParams
from .workflow import Workflow
from .stages import ACQUISITION, FINISH_ID, validate_selection


def main():
    payload = json.loads(os.environ.get("LAB_TYPED_PARAMETERS", "{}"))
    session_id = payload.pop("session_id")
    os.environ["LAB_TYPED_PARAMETERS"] = json.dumps(payload)
    ctx = Workflow(load_runtime_params(RFControlScaleParams), session_id=session_id)
    serve(ctx, sys.stdin, stages=ACQUISITION, validate=validate_selection, finish_id=FINISH_ID)


if __name__ == "__main__":
    main()
