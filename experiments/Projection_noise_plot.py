"""原子自旋投影噪声 SDS 离线分析薄入口。"""

import argparse
from pathlib import Path

from lab_workflows.experiment_modules.projection_noise import ADAPTER


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path, help="明确指定运行目录；不隐式分析 latest")
    return ADAPTER.analyze_cli(parser.parse_args().run_dir)


if __name__ == "__main__":
    raise SystemExit(main())
