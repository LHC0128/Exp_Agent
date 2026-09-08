"""Z 线圈电感效应频率响应离线分析入口。"""

import argparse
from pathlib import Path

from lab_workflows.experiment_modules.z_coil_inductance_frequency_response import (
    ADAPTER,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", nargs="?", type=Path)
    return ADAPTER.analyze_cli(parser.parse_args().run_dir)


if __name__ == "__main__":
    raise SystemExit(main())
