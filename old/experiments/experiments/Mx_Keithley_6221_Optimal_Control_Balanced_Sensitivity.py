"""Mx Keithley 6221 最优控制 平衡-灵敏度一体化采集薄入口。"""

# %% Cell 1
from lab_workflows.experiment_modules.mx_keithley_6221_optimal_control_balanced_sensitivity import (
    ADAPTER,
)


# %% Cell 2
if __name__ == "__main__":
    raise SystemExit(ADAPTER.run_cli())
