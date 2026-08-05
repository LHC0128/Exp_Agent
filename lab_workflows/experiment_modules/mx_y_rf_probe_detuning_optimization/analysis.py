"""Mx Y RF Probe 光功率与 PZT 失谐优化离线分析。"""

from __future__ import annotations

from pathlib import Path

from ...experiment_runtime import runtime_run_dir
from ..mx_y_rf_grid_analysis import (
    GridAnalysisSpec,
    GridAxisSpec,
    analyze_mx_y_rf_grid,
)
from ..mx_y_rf_sensitivity.analysis import _plot_full_analysis
from ..mx_y_rf_sensitivity.point_analysis import evaluate_mx_y_rf_point
from .models import MxYRFProbeDetuningOptimizationParams


GRID_SPEC = GridAnalysisSpec(
    experiment_id="mx-y-rf-probe-detuning-optimization",
    outer=GridAxisSpec(
        manifest_key="pzt_voltage_v",
        index_key="pzt_index",
        coordinate_key="pzt_voltage_v",
        axis_label="PZT scan offset (V)",
        point_label="PZT",
    ),
    inner=GridAxisSpec(
        manifest_key="probe_power_v",
        index_key="probe_index",
        coordinate_key="probe_power_v",
        axis_label="Probe power control voltage (V)",
        point_label="Probe",
    ),
    point_record_fields=(
        "pzt_voltage_readback_v",
        "pzt_actual_v",
        "pzt_setpoint_error_v",
    ),
)


def analyze(run_dir: Path) -> dict:
    return analyze_mx_y_rf_grid(
        run_dir,
        params_type=MxYRFProbeDetuningOptimizationParams,
        spec=GRID_SPEC,
        evaluate_point=evaluate_mx_y_rf_point,
        plot_full_analysis=_plot_full_analysis,
    )


def main() -> int:
    analyze(runtime_run_dir())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
