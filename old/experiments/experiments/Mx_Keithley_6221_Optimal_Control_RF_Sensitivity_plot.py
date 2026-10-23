"""Mx Keithley 6221 主场最优控制 RF 灵敏度离线分析薄入口。"""

# %% Cell 1
from lab_workflows.experiment_modules.mx_keithley_6221_optimal_control_rf_sensitivity import ADAPTER


# %% Cell 2
if __name__ == "__main__":
    raise SystemExit(ADAPTER.analyze_cli())
