"""XY DirectAW Demod3 R 噪声谱离线分析薄入口。"""

import argparse
from pathlib import Path

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path, help="显式指定运行目录，不自动分析 latest")
    args = parser.parse_args()
    from lab_workflows.experiment_modules.noise_spectrum_xy_demod3_r import ADAPTER
    return ADAPTER.analyze_cli(args.run_dir)


if __name__ == "__main__":
    raise SystemExit(main())
