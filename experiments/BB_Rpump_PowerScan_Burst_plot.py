# %% [markdown]
# # BB Burst Pump 功率扫描：离线分析薄入口
# 必须显式提供 run_dir；不读取 latest、不连接仪器、不改写原始数据。

# %% Cell 1
import argparse
from pathlib import Path
from lab_workflows.experiment_modules.bb_power_scan_burst.analysis import analyze


def main():
    parser = argparse.ArgumentParser(description="BB Burst Pump 功率扫描离线分析")
    parser.add_argument("run_dir", type=Path, help="明确的实验运行目录")
    args = parser.parse_args()
    analyze(args.run_dir)
    return 0


# %% Cell 2
if __name__ == "__main__":
    raise SystemExit(main())
