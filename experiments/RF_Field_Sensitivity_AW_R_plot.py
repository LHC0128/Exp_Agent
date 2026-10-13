# %% [markdown]
# # RF 灵敏度（DirectAW R）离线分析入口
# 仅加载明确指定的运行目录，不连接硬件。

# %% 导入
import argparse
from pathlib import Path
from lab_workflows.experiment_modules.rf_sensitivity_direct_aw_r.analysis import analyze


# %% 选择历史运行
def main():
    parser = argparse.ArgumentParser(description="DirectAW R 标定 / X 噪声灵敏度分析")
    parser.add_argument("run_dir", type=Path)
    analyze(parser.parse_args().run_dir)


if __name__ == "__main__":
    main()
