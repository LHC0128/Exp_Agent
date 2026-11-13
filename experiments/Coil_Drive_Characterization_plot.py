# %% [markdown]
# # 线圈驱动链路电学特性：离线分析薄入口
# 必须显式指定运行目录，不选择 latest，不连接仪器。

# %% Cell 1
import argparse
from pathlib import Path
from lab_workflows.experiment_modules.coil_drive_characterization.analysis import analyze


def main():
    parser = argparse.ArgumentParser(description="线圈驱动链路电学特性离线分析")
    parser.add_argument("run_dir", type=Path, help="明确的实验运行目录")
    args = parser.parse_args()
    analyze(args.run_dir)
    return 0


# %% Cell 2
if __name__ == "__main__":
    raise SystemExit(main())
