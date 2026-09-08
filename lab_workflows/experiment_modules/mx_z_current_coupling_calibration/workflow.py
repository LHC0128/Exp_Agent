"""Mx Z 实际电流-耦合强度标定采集工作流。"""

from __future__ import annotations

from pathlib import Path

from ...experiment_runtime import load_runtime_params
from ..mx_z_field_calibration.workflow import run_calibration
from .models import MxZCurrentCouplingCalibrationParams


EXPERIMENT_ID = "mx-z-current-coupling-calibration"
DATA_TYPE = "Mx_Z_Current_Coupling_Calibration"
EXECUTION_MODE = "typed_workflow"


def run(params: MxZCurrentCouplingCalibrationParams) -> Path:
    """同步采集采样电阻电压，并复用 Mx Z 共振扫描。"""
    return run_calibration(
        params,
        experiment_id=EXPERIMENT_ID,
        data_type=DATA_TYPE,
        completion_label="Mx Z 实际电流-耦合强度标定",
        include_scope=True,
    )


def main() -> int:
    run(load_runtime_params(MxZCurrentCouplingCalibrationParams))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
