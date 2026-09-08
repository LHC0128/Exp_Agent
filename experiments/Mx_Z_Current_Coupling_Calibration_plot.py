"""Mx Z 实际电流-耦合强度标定离线分析入口。"""

from __future__ import annotations

import argparse
from pathlib import Path

from lab_workflows.experiment_modules.mx_z_current_coupling_calibration import ADAPTER


def main() -> int:
    parser = argparse.ArgumentParser(description="分析 Mx Z 实际电流-耦合强度标定运行目录")
    parser.add_argument("run_dir", nargs="?", type=Path, help="实验运行目录")
    return ADAPTER.analyze_cli(parser.parse_args().run_dir)


if __name__ == "__main__":
    raise SystemExit(main())
