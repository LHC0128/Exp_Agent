"""Mx Z 最优控制 DG4000 偏置 XYZ 平衡场离线分析薄入口。"""

# %% Cell 1
from lab_workflows.experiment_modules.mx_z_optimal_control_dg4000_bias_xyz_balance import (
    ADAPTER,
)


# %% Cell 2
if __name__ == "__main__":
    raise SystemExit(ADAPTER.analyze_cli())
