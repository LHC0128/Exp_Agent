"""π 脉冲宽度原子域定标的离线分析薄入口。"""

import argparse
from pathlib import Path

from lab_workflows.experiment_modules.pi_width_scan import ADAPTER


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", nargs="?", type=Path)
    return ADAPTER.analyze_cli(parser.parse_args().run_dir)


if __name__ == "__main__":
    raise SystemExit(main())
