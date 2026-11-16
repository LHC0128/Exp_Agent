# %% [markdown]
# # Z 线圈常数标定：离线分析薄入口
# 必须显式指定运行目录；不连接仪器，不改写 raw。

# %% Cell 1
import argparse
from pathlib import Path

from lab_workflows.experiment_modules.coil_constant_calibration import ADAPTER


def main():
    parser = argparse.ArgumentParser(description="Z 线圈常数标定离线分析")
    parser.add_argument("run_dir", nargs="?", type=Path, help="明确的实验运行目录")
    return ADAPTER.analyze_cli(parser.parse_args().run_dir)


# %% Cell 2
if __name__ == "__main__":
    raise SystemExit(main())
