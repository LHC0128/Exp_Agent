# %% [markdown]
# # RF 灵敏度（DirectAW）离线分析入口
# 必须指定新格式运行目录，不连接仪器。

# %% 导入
import argparse
from pathlib import Path
from lab_workflows.experiment_modules.rf_sensitivity_direct_aw.analysis import analyze

# %% 选择运行目录并分析
def main() -> None:
    parser = argparse.ArgumentParser(description="RF 灵敏度 DirectAW 离线分析")
    parser.add_argument("run_dir", type=Path, help="通过格式资格检查的 DirectAW 新模式运行目录（参数版本 2 或 3）")
    analyze(parser.parse_args().run_dir)


if __name__ == "__main__":
    main()
