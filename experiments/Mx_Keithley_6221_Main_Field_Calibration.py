"""Mx Keithley 6221 主磁场频率标定采集薄入口。"""

# %% Cell 1
from lab_workflows.experiment_modules.mx_keithley_6221_main_field_calibration import (
    ADAPTER,
)


# %% Cell 2
if __name__ == "__main__":
    raise SystemExit(ADAPTER.run_cli())
