# %% [markdown]
# # ConstXY 频率响应离线分析
# 必须显式指定 run_dir；绝不隐式选择并改写最新历史运行。
# 说明：docs/rf_sensitivity_constxy_frequency.md
# %% 离线入口
import argparse
from pathlib import Path
from lab_workflows.experiment_modules.rf_sensitivity_constxy_frequency.analysis import analyze


def main():
    parser = argparse.ArgumentParser(description="ConstXY 频率响应离线分析")
    parser.add_argument("run_dir", type=Path, help="明确的运行目录")
    analyze(parser.parse_args().run_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
