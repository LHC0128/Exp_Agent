"""Mx 高主场 Z 磁场频率标定离线分析薄入口。"""

# %% Cell 1
from lab_workflows.experiment_modules.mx_z_field_calibration import ADAPTER


# %% Cell 2
if __name__ == "__main__":
    raise SystemExit(ADAPTER.analyze_cli())

