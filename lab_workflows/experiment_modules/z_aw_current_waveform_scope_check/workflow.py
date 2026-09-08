"""Z 任意波实际电流波形验证采集工作流。"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np

from ...common import find_project_root
from ...current_feedback import (
    load_corrected_control_waveform,
    load_current_coupling_calibration,
    target_current_from_omega,
    validate_current_power,
)
from ...experiment_runtime import load_runtime_params
from ..mx_z_optimal_control_rf_sensitivity.sources import load_theory_control
from ..mx_z_optimal_control_rf_sensitivity.workflow import (
    _corrected_control_contract,
    _save_corrected_source_snapshot,
)
from ..z_aw_waveform_scope_check.workflow import run_scope_check
from .models import ZAWCurrentWaveformScopeCheckParams


EXPERIMENT_ID = "z-aw-current-waveform-scope-check"
DATA_TYPE = "Z_AW_Current_Waveform_Scope_Check"
EXECUTION_MODE = "typed_workflow"


def run(params: ZAWCurrentWaveformScopeCheckParams) -> Path:
    """采集采样电阻波形并将其作为实际电流验证目标。"""
    theory_override = None
    applied_override = None
    snapshot_writer = None
    target_control_scale = None
    corrected = None
    if params.control_waveform_source == "corrected_run":
        corrected = load_corrected_control_waveform(
            find_project_root(), params.corrected_control_source_run
        )
        theory_override, applied_override = _corrected_control_contract(corrected)
        snapshot_writer = lambda raw_dir: _save_corrected_source_snapshot(
            raw_dir,
            corrected,
            theory_override,
            applied_override,
        )
        target_control_scale = 1.0
    calibration = load_current_coupling_calibration(
        find_project_root(), params.current_coupling_calibration_source_run
    )
    if abs(calibration.sense_resistor_ohm - params.sense_resistor_ohm) > max(
        1e-12, abs(params.sense_resistor_ohm) * 1e-6
    ):
        raise ValueError("实验参数中的采样电阻与电流耦合标定来源不一致")
    if corrected is not None:
        if corrected.coupling_calibration_run != calibration.run_name:
            raise ValueError("冻结波形与选择的电流耦合标定运行不一致")
        if corrected.coupling_calibration_sha256 != calibration.analysis_sha256:
            raise ValueError("冻结波形与当前电流耦合标定文件哈希不一致")
        target_current = np.asarray(corrected.target_current_a, dtype=float)
    else:
        theory = load_theory_control(
            Path(params.control_results_root),
            params.control_version,
        )
        target_current = target_current_from_omega(
            params.control_scale * theory.omega_ctrl_hz,
            calibration,
        )
    validate_current_power(
        target_current,
        params.sense_resistor_ohm,
        params.sense_resistor_power_rating_w,
        derating_fraction=0.5,
        maximum_current_a=params.maximum_current_a,
    )
    return run_scope_check(
        replace(
        params,
            z_calibration_source_run=params.command_z_calibration_source_run,
        ),
        experiment_id=EXPERIMENT_ID,
        data_type=DATA_TYPE,
        measurement_mode="current",
        current_calibration=calibration,
        theory_override=theory_override,
        applied_override=applied_override,
        source_snapshot_writer=snapshot_writer,
        target_control_scale=target_control_scale,
    )


def main() -> int:
    run(load_runtime_params(ZAWCurrentWaveformScopeCheckParams))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
