"""Mx Y RF 灵敏度长飘采集入口。"""

# %% Cell 1
from lab_workflows.experiment_modules.mx_y_rf_sensitivity_drift import ADAPTER


# %% Cell 2
if __name__ == "__main__":
    raise SystemExit(ADAPTER.run_cli())
