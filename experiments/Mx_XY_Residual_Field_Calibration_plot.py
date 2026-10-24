"""Mx XY 剩磁二维校准离线分析薄入口。"""

import argparse
from pathlib import Path

from lab_workflows.experiment_modules.mx_xy_residual_field_calibration import ADAPTER


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path, help="明确指定运行目录；禁止隐式分析最新历史")
    return ADAPTER.analyze_cli(parser.parse_args().run_dir)


if __name__ == "__main__":
    raise SystemExit(main())
