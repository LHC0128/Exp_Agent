"""MxStandardRfPhaseSensitivity 离线分析薄入口。"""

import argparse
from pathlib import Path

from lab_workflows.experiment_modules.mx_standard_rf_phase_sensitivity import ADAPTER


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", nargs="?", type=Path)
    return ADAPTER.analyze_cli(parser.parse_args().run_dir)


if __name__ == "__main__":
    raise SystemExit(main())
