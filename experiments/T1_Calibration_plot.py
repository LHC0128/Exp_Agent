# %% [markdown] Cell 0
# # T1 离线分析：明确指定目录，不连接仪器、不修改 raw。
# 例：python experiments/T1_Calibration_plot.py data/T1_Calibration/0529_1405_test

# %% Cell 1
import os
from pathlib import Path
import argparse
from lab_workflows.experiment_modules.t1_calibration.analysis import analyze

# %% Cell 2
def main():
    parser = argparse.ArgumentParser(description="T1 标定离线分析")
    parser.add_argument("run_dir", nargs="?", default=os.environ.get("LAB_RUN_DIR"))
    args = parser.parse_args()
    if not args.run_dir:
        parser.error("请明确指定运行目录，或设置 LAB_RUN_DIR")
    analyze(Path(args.run_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
