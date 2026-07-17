"""XY DirectAW DC 标定离线分析薄入口。"""

import argparse
from pathlib import Path

from lab_workflows.experiment_modules.xy_direct_aw_dc_calibration import ADAPTER


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", nargs="?", type=Path)
    args = parser.parse_args()
    return ADAPTER.analyze_cli(args.run_dir)


if __name__ == "__main__":
    raise SystemExit(main())
