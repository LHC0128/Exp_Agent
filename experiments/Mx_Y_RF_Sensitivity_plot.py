"""Mx Y 向 RF 场灵敏度离线分析薄入口。"""

# %% Cell 1
from lab_workflows.experiment_modules.mx_y_rf_sensitivity import ADAPTER


# %% Cell 2
if __name__ == "__main__":
    raise SystemExit(ADAPTER.analyze_cli())
