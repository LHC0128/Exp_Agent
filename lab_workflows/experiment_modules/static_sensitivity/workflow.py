"""静磁场灵敏度采集入口。

参数、注册和取消由 typed adapter 管理；硬件步骤继续复用已经现场验证的
主实验脚本，避免复制一套 GS200、HF2 和温控时序。
"""

from __future__ import annotations

import json
import os
import runpy
from pathlib import Path

from ...experiment_runtime import load_runtime_params
from .models import StaticSensitivityParams


def run(params: StaticSensitivityParams) -> None:
    root = Path(__file__).resolve().parents[3]
    environment = os.environ.copy()
    environment["LAB_STATIC_CONFIG"] = json.dumps(params.to_legacy(), ensure_ascii=False)
    old_config = os.environ.get("LAB_STATIC_CONFIG")
    os.environ.update(environment)
    try:
        runpy.run_path(str(root / "experiments" / "Static_Magnetic_Field_Sensitivity.py"), run_name="__main__")
    finally:
        if old_config is None:
            os.environ.pop("LAB_STATIC_CONFIG", None)
        else:
            os.environ["LAB_STATIC_CONFIG"] = old_config


def main() -> int:
    run(load_runtime_params(StaticSensitivityParams))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
