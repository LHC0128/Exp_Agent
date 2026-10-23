"""Mx Y RF 光功率灵敏度优化离线分析薄入口。"""

# %% Cell 1
import argparse
from pathlib import Path
from lab_workflows.experiment_modules.mx_y_rf_power_optimization import ADAPTER


# %% Cell 2
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="离线分析指定光功率优化运行，不隐式选择最新历史")
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()
    raise SystemExit(ADAPTER.analyze_cli(args.run_dir))
