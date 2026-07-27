"""Mx Y RF 光功率灵敏度优化离线分析薄入口。"""

# %% Cell 1
from lab_workflows.experiment_modules.mx_y_rf_power_optimization import ADAPTER


# %% Cell 2
if __name__ == "__main__":
    raise SystemExit(ADAPTER.analyze_cli())
