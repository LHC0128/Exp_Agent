"""在真实隔离子进程中复用修订、串行命令、取消和 EOF 收尾。"""
import json
import os
import sys
from ...experiment_runtime import load_runtime_params
from ..z_coil_inductance_frequency_response.session import serve
from .models import ZCoilCurrentFrequencyResponseParams
from .workflow import Workflow


def main():
    payload = json.loads(os.environ.get("LAB_TYPED_PARAMETERS", "{}"))
    session_id = payload.pop("session_id")
    os.environ["LAB_TYPED_PARAMETERS"] = json.dumps(payload)
    serve(Workflow(load_runtime_params(ZCoilCurrentFrequencyResponseParams), session_id=session_id), sys.stdin)


if __name__ == "__main__":
    main()
