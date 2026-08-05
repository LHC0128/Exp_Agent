"""Mx Y RF Probe 光功率与 PZT 失谐优化采集薄入口。"""

# %% Cell 1
from lab_workflows.experiment_modules.mx_y_rf_probe_detuning_optimization import (
    ADAPTER,
)


# %% Cell 2
if __name__ == "__main__":
    raise SystemExit(ADAPTER.run_cli())
