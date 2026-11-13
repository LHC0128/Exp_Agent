"""Mx Z 最优控制 XY 泄露响应离线分析薄入口。"""

# %% Cell 1
import argparse
from pathlib import Path
from lab_workflows.experiment_modules.mx_z_optimal_control_xy_leakage_response import (
    ADAPTER,
)


# %% Cell 2
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path, help="明确的实验运行目录；不隐式选择 latest")
    args = parser.parse_args()
    raise SystemExit(ADAPTER.analyze_cli(args.run_dir))
