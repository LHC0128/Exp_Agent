"""Mx Y RF Probe 光功率与 PZT 失谐优化离线分析薄入口。"""

# %% Cell 1
from pathlib import Path
import sys
from lab_workflows.experiment_modules.mx_y_rf_probe_detuning_optimization import (
    ADAPTER,
)


# %% Cell 2
if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("用法: Mx_Y_RF_Probe_Detuning_Optimization_plot.py <run_dir>；必须显式指定目录")
    raise SystemExit(ADAPTER.analyze_cli(Path(sys.argv[1])))
