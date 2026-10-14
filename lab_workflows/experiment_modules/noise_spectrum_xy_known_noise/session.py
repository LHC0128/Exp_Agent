"""使用 XY 串行命令协议持有已知噪声采集会话。"""
import sys
import json
import os
from ..noise_spectrum_xy.session import serve
from ...experiment_runtime import load_runtime_params
from .models import NoiseSpectrumXYKnownNoiseParams
from .workflow import Workflow
from . import stages


def main():
    payload = json.loads(os.environ.get("LAB_TYPED_PARAMETERS", "{}"))
    session_id = payload.pop("session_id")
    os.environ["LAB_TYPED_PARAMETERS"] = json.dumps(payload)
    serve(Workflow(load_runtime_params(NoiseSpectrumXYKnownNoiseParams), session_id=session_id), sys.stdin,
          stages=stages.ACQUISITION, validate=stages.validate_selection, finish_id=stages.FINISH_ID)


if __name__ == "__main__":
    main()
