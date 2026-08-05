"""Mx Y RF 光功率灵敏度优化离线分析。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...experiment_runtime import runtime_run_dir
from ..mx_y_rf_grid_analysis import (
    GridAnalysisSpec,
    GridAxisSpec,
    analyze_mx_y_rf_grid,
)
from ..mx_y_rf_sensitivity.analysis import _plot_full_analysis
from ..mx_y_rf_sensitivity.point_analysis import evaluate_mx_y_rf_point
from .models import MxYRFPowerOptimizationParams


GRID_SPEC = GridAnalysisSpec(
    experiment_id="mx-y-rf-power-optimization",
    outer=GridAxisSpec(
        manifest_key="pump_power_v",
        index_key="pump_index",
        coordinate_key="pump_power_v",
        axis_label="Pump power control voltage (V)",
        point_label="Pump",
    ),
    inner=GridAxisSpec(
        manifest_key="probe_power_v",
        index_key="probe_index",
        coordinate_key="probe_power_v",
        axis_label="Probe power control voltage (V)",
        point_label="Probe",
    ),
)


def analyze(run_dir: Path) -> dict[str, Any]:
    """通过轴参数化公共实现分析 Pump/Probe 二维网格。"""
    return analyze_mx_y_rf_grid(
        run_dir,
        params_type=MxYRFPowerOptimizationParams,
        spec=GRID_SPEC,
        evaluate_point=evaluate_mx_y_rf_point,
        plot_full_analysis=_plot_full_analysis,
    )


def main() -> int:
    analyze(runtime_run_dir())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
